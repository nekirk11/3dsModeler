"""Pure menu state machine: tap/hold, search, cancellation, commit, repeat.

The controller is the only component allowed to call ``HostAdapter.launch``.
Renderers send normalized :class:`MenuEvent` values and render returned
:class:`MenuView` values; they never execute catalog actions themselves.
"""

from __future__ import annotations

import math
import uuid
from typing import Any

from .catalog import ACTIONS, ACTIONS_BY_ID, REPEAT_ACTION_ID, build_rows
from .contracts import (
    Availability,
    ContextLevel,
    ContextSnapshot,
    ControllerSummary,
    HostAdapter,
    LaunchResult,
    MenuEvent,
    MenuView,
    ParameterSpec,
)
from .parameters import ParameterError, parameter_schema, validate_parameters
from .profile import MenuProfile

LOG_LIMIT = 50
CLOSED = "closed"
HELD = "held"
LATCHED = "latched"
SEARCH = "search"

_OPEN_PHASES = (HELD, LATCHED, SEARCH)
PARAMETER_DIALOG_UNAVAILABLE = (
    "Native parameter dialog is unavailable in this renderer."
)


class MenuController:
    """Shared controller used by the browser sandbox, Qt popup, and Max host.

    ``open`` snapshots the host and computes capabilities without mutation.
    ``handle`` owns threshold/neutral-zone transitions, search, navigation,
    session/sequence rejection, repeat memory, and logging.  A too-short tap
    latches the menu without arming a row.  A commit always revalidates and
    then closes before returning; repeated release/click events cannot double
    dispatch because the menu is closed.

    Renderers that must hide a widget and restore focus before native dispatch
    may register a synchronous ``set_pre_dispatch`` callback.  It runs only
    after revalidation succeeds and immediately before ``HostAdapter.launch``.

    Parameterized actions use a synchronous ``set_parameter_prompt`` collector.
    The menu closes and an internal busy token is set before that modal callback;
    a fresh snapshot and capability check run again after collection.  Panel
    buttons call ``invoke`` (the same dispatch path, no profile visibility
    filter), and read-only panel state comes from ``summary``.
    """

    def __init__(self, host: HostAdapter, profile: MenuProfile) -> None:
        if not isinstance(profile, MenuProfile):
            raise TypeError("profile must be a MenuProfile")
        self._host = host
        self._profile = profile.clone()
        self._phase = CLOSED
        self._session_id = ""
        self._sequence = 0
        self._last_event_sequence = -1
        self._origin: tuple[float, float] = (0.0, 0.0)
        self._open_time = 0.0
        self._invocation = ""
        self._context: ContextLevel | None = None
        self._snapshot: ContextSnapshot | None = None
        self._availability: dict[str, Availability] = {}
        self._rows = []
        self._query = ""
        self._highlighted: str | None = None
        self._armed: str | None = None
        self._status = "Ready."
        self._last_result: LaunchResult | None = None
        self._last_repeat: str | None = None
        self._log: list[dict[str, Any]] = []
        self._pre_dispatch: Any = None
        self._parameter_prompt: Any = None
        self._busy = False
        self._invocation_token: int | None = None
        self._token_counter = 0

    # ------------------------------------------------------------------ views

    def view(self) -> MenuView:
        return MenuView(
            session_id=self._session_id,
            phase=self._phase,
            context=self._context,
            query=self._query,
            rows=list(self._rows),
            highlighted_id=self._highlighted,
            armed_id=self._armed,
            status=self._status,
            last_result=self._last_result,
            log=[dict(entry) for entry in self._log],
            sequence=self._sequence,
        )

    def _bump(self) -> None:
        self._sequence += 1

    def summary(self) -> ControllerSummary:
        """Read-only state for a native tools panel.

        This is the frozen panel seam: use it instead of private controller or
        host attributes.  It never performs a host query or mutation.
        """

        snapshot = self._snapshot
        availability = (
            self._effective_availability(self._availability, self._context)
            if self._context is not None
            else dict(self._availability)
        )
        ordered: list[tuple[str, Availability]] = []
        for spec in ACTIONS:
            if spec.id in availability:
                ordered.append((spec.id, availability[spec.id]))
        for action_id in sorted(set(availability) - {item[0] for item in ordered}):
            ordered.append((action_id, availability[action_id]))
        return ControllerSummary(
            phase=self._phase,
            context=self._context,
            target_kind=snapshot.target_kind if snapshot is not None else "none",
            selection_count=snapshot.selection_count if snapshot is not None else 0,
            panel_ready=snapshot.panel_ready if snapshot is not None else False,
            conflicting_mode=(
                snapshot.conflicting_mode if snapshot is not None else False
            ),
            busy=self._busy,
            status=self._status,
            last_result=self._last_result,
            last_repeat_id=self._last_repeat,
            availability=tuple(ordered),
        )

    def _effective_availability(
        self,
        availability: dict[str, Availability],
        context: ContextLevel | None,
    ) -> dict[str, Availability]:
        result = {
            key: value
            for key, value in availability.items()
            if isinstance(value, Availability)
        }
        if context is None:
            return result
        for spec in ACTIONS:
            if spec.id == REPEAT_ACTION_ID or context not in spec.allowed_levels:
                continue
            try:
                schema = parameter_schema(spec.id, context)
            except ParameterError:
                continue
            if schema and self._parameter_prompt is None:
                result[spec.id] = Availability(False, PARAMETER_DIALOG_UNAVAILABLE)
        return result

    # ------------------------------------------------------- parameter prompt

    def set_parameter_prompt(self, callback: Any) -> None:
        """Register the optional synchronous parameter collector.

        The callback receives ``(ActionSpec, ContextSnapshot,
        tuple[ParameterSpec, ...])`` and returns a plain ``dict`` or ``None``
        for cancel.  It may only collect values; it must never launch a host
        action.  Register it at native panel/bootstrap lifetime, not per
        transient popup.
        """

        if callback is not None and not callable(callback):
            raise TypeError("parameter prompt callback must be callable or None")
        self._parameter_prompt = callback

    def clear_parameter_prompt(self, callback: Any = None) -> None:
        """Clear the hook, or only if it still matches ``callback``."""

        if callback is None or self._parameter_prompt == callback:
            self._parameter_prompt = None

    def _new_invocation(self) -> int:
        self._token_counter += 1
        return self._token_counter

    def _invalidate_invocation(self) -> None:
        self._invocation_token = None

    def _token_valid(self, token: int) -> bool:
        return self._busy and self._invocation_token == token

    def dispose(self) -> MenuView:
        """Invalidate pending input and close the controller view."""

        self._invalidate_invocation()
        self._busy = False
        self._close("Disposed; pending input cancelled.", result="cancelled")
        self._record(None, "cancelled", "dispose")
        self._bump()
        return self.view()

    def release_owned_native_mode(self) -> LaunchResult | None:
        """Ask the host to exit only its exact-owned native mode, if supported.

        This is an optional additive seam used by bootstrap teardown.  It never
        launches an action or guesses mode ownership: adapters without a public
        ``release_owned_mode`` hook are left untouched.
        """

        release = getattr(self._host, "release_owned_mode", None)
        if not callable(release):
            return None
        try:
            result = release()
        except Exception as exc:
            result = LaunchResult(
                "error",
                "model.exit_tool",
                "Owned-mode release failed: %s" % exc,
            )
        if not isinstance(result, LaunchResult):
            result = LaunchResult(
                "error",
                "model.exit_tool",
                "Host returned an invalid owned-mode release result.",
            )
        self._record("model.exit_tool", result.status, result.message)
        return result

    def _record(self, action: str | None, result: str, detail: str = "") -> None:
        self._log.append(
            {
                "session": self._session_id,
                "context": self._context.value if self._context else None,
                "action": action,
                "result": result,
                "detail": detail,
            }
        )
        if len(self._log) > LOG_LIMIT:
            del self._log[: len(self._log) - LOG_LIMIT]

    # ----------------------------------------------------------- pre-dispatch

    def set_pre_dispatch(self, callback: Any) -> None:
        """Register a renderer hook that runs immediately before native launch.

        The callback receives the resolved action ID.  It must synchronously
        hide any popup and restore focus before a native command mode is
        entered.  It is not called for disabled rows, stale contexts, or
        changed availability, and an exception becomes an error result with no
        dispatch.
        """

        if callback is not None and not callable(callback):
            raise TypeError("pre_dispatch callback must be callable or None")
        self._pre_dispatch = callback

    def clear_pre_dispatch(self, callback: Any = None) -> None:
        """Clear the hook, or only if it still matches ``callback``."""

        if callback is None or self._pre_dispatch == callback:
            self._pre_dispatch = None

    # ------------------------------------------------------------------- open

    def open(
        self,
        origin: tuple[float, float] | None = None,
        time_ms: float = 0.0,
        invocation: str = "held",
    ) -> MenuView:
        """Open a fresh session or cancel an already-open menu.

        ``invocation`` is ``held`` or ``latched``.  A held menu waits for the
        original trigger release; a latched menu (manual host entry) never
        synthesizes a key release.
        """

        if invocation not in (HELD, LATCHED):
            raise ValueError("invocation must be 'held' or 'latched'")

        if self._busy:
            self._invalidate_invocation()
            self._status = (
                "Another invocation is awaiting parameters; this open request "
                "was rejected."
            )
            self._record("open", "cancelled", "busy")
            self._bump()
            return self.view()

        if self._phase != CLOSED:
            self._close(
                "Another invocation cancelled the open menu; nothing launched.",
                result="cancelled",
            )
            self._record("open", "cancelled", "second invocation")
            self._bump()
            return self.view()

        self._origin = (float(origin[0]), float(origin[1])) if origin else (0.0, 0.0)
        self._open_time = float(time_ms)
        self._invocation = invocation
        self._query = ""
        self._highlighted = None
        self._armed = None
        self._last_result = None
        self._phase = invocation
        self._session_id = uuid.uuid4().hex
        self._last_event_sequence = -1

        try:
            snapshot = self._host.snapshot()
            if not isinstance(snapshot, ContextSnapshot):
                raise TypeError("host snapshot() did not return ContextSnapshot")
            availability = self._host.availability(snapshot)
            if not isinstance(availability, dict):
                raise TypeError("host availability() did not return a dict")
        except Exception as exc:  # Host failure must not take down the menu.
            self._phase = CLOSED
            self._context = None
            self._snapshot = None
            self._availability = {}
            self._rows = []
            self._status = f"Host unavailable: {exc}"
            self._record("open", "error", str(exc))
            self._bump()
            return self.view()

        self._snapshot = snapshot
        self._context = snapshot.level
        self._availability = self._effective_availability(
            availability, snapshot.level
        )
        self._rows = build_rows(
            self._profile,
            snapshot.level,
            self._availability,
            self._last_repeat,
            "",
        )
        self._status = (
            "Hold menu open. Move outside the neutral zone and release after the "
            "hold threshold to launch."
            if invocation == HELD
            else "Menu open (latched). Choose a row explicitly."
        )
        self._record("open", "opened", invocation)
        self._bump()
        return self.view()

    # ----------------------------------------------------------------- events

    def handle(self, event: MenuEvent) -> MenuView:
        """Apply one normalized event.  Stale sessions/unsigned events are ignored."""

        if not isinstance(event, MenuEvent):
            raise TypeError("event must be a MenuEvent")
        if self._busy:
            self._invalidate_invocation()
            self._status = "Event ignored while awaiting parameters."
            self._bump()
            return self.view()
        if self._phase == CLOSED:
            return self.view()
        if event.session_id != self._session_id:
            return self.view()
        if event.sequence <= self._last_event_sequence:
            return self.view()
        self._last_event_sequence = event.sequence

        kind = event.kind
        if kind == "trigger_down":
            if event.auto_repeat:
                self._status = "Ignored auto-repeated trigger press."
            else:
                self._close(
                    "Second invocation cancelled the open menu; nothing launched.",
                    result="cancelled",
                )
                self._record("open", "cancelled", "second invocation")
        elif kind == "trigger_up":
            self._handle_trigger_up(event)
        elif kind == "pointer_move":
            self._handle_pointer_move(event)
        elif kind == "click_row":
            self._handle_click(event)
        elif kind == "key_down":
            self._handle_key(event)
        elif kind == "query_changed":
            self._set_query(str(event.text or ""))
        elif kind in ("outside_click", "focus_lost", "cancel"):
            labels = {
                "outside_click": "outside click",
                "focus_lost": "focus loss",
                "cancel": "cancel request",
            }
            self._close(
                f"Cancelled by {labels[kind]}; nothing launched.",
                result="cancelled",
            )
            self._record(None, "cancelled", labels[kind])
        else:
            self._status = f"Unsupported event ignored: {kind!r}."

        self._bump()
        return self.view()

    # ------------------------------------------------------------------ close

    def _close(self, status: str, result: str = "cancelled") -> None:
        self._phase = CLOSED
        self._rows = []
        self._query = ""
        self._highlighted = None
        self._armed = None
        self._status = status
        self._invocation = ""

    # -------------------------------------------------------------- gestures

    def _inside_neutral(self, x: float, y: float) -> bool:
        radius = self._profile.neutral_px
        return math.hypot(float(x), float(y)) <= radius

    def _handle_trigger_up(self, event: MenuEvent) -> None:
        if event.auto_repeat:
            self._status = "Ignored auto-repeated trigger release."
            return
        if self._invocation != HELD or self._phase != HELD:
            # Latched/search/typed menus never execute from a trigger release.
            return

        duration = float(event.time_ms) - self._open_time
        if duration < self._profile.tap_ms:
            # A quick tap latches in place.  Nothing is armed or executed, so
            # an explicit click or Enter is still required.
            self._phase = LATCHED
            self._armed = None
            self._highlighted = None
            self._status = (
                f"Tap released after {duration:.0f} ms, below the "
                f"{self._profile.tap_ms} ms hold threshold; menu latched "
                "with no armed action. Click a row or press Enter."
            )
            self._record(None, "latched", "tap release")
            return
        if self._inside_neutral(event.x, event.y):
            self._close(
                "Released inside the neutral zone; nothing launched.",
                result="cancelled",
            )
            self._record(None, "cancelled", "neutral release")
            return
        if not self._armed:
            self._close(
                "Released without a deliberately hovered eligible row; nothing launched.",
                result="cancelled",
            )
            self._record(None, "cancelled", "no armed row")
            return

        armed = self._armed
        row = self._row(armed)
        if row is None or not row.enabled:
            self._close(
                (row.reason if row else "The armed row is gone")
                + "; nothing launched.",
                result="cancelled",
            )
            self._record(armed, "cancelled", "armed row unavailable")
            return
        self._commit(armed, float(event.time_ms))

    def _handle_pointer_move(self, event: MenuEvent) -> None:
        if self._inside_neutral(event.x, event.y):
            self._armed = None
            self._highlighted = None
            self._status = (
                "Neutral zone: releasing the trigger cancels."
                if self._invocation == HELD
                else "No row under pointer."
            )
            return

        row = self._row(event.action_id) if event.action_id else None
        if row is None:
            self._highlighted = None
            if self._phase == HELD:
                self._armed = None
            self._status = "No row under pointer."
            return

        self._highlighted = row.action_id
        if self._phase == HELD:
            self._armed = row.action_id if row.enabled else None
        else:
            self._armed = None
        self._status = row.reason if not row.enabled else ""

    def _handle_click(self, event: MenuEvent) -> None:
        row = self._row(event.action_id)
        if row is None:
            self._status = "That row is no longer available."
            return
        self._highlighted = row.action_id
        self._commit(row.action_id, float(event.time_ms))

    def _handle_key(self, event: MenuEvent) -> None:
        key = (event.key or "").strip()
        lowered = key.lower()

        if lowered in ("escape", "esc"):
            self._close("Cancelled with Escape; nothing launched.", result="cancelled")
            self._record(None, "cancelled", "escape")
            return
        if lowered in ("enter", "return"):
            if event.auto_repeat:
                self._status = "Ignored auto-repeated Enter."
                return
            self._commit_highlighted()
            return
        if lowered in ("arrowdown", "down"):
            self._move_highlight(1)
            return
        if lowered in ("arrowup", "up"):
            self._move_highlight(-1)
            return
        if lowered in ("home",):
            self._highlight_index(0)
            return
        if lowered in ("end",):
            self._highlight_index(len(self._rows) - 1)
            return
        if lowered in ("backspace", "delete"):
            if self._query:
                self._set_query(self._query[:-1])
            return

        text = event.text
        if not text and len(key) == 1 and key.isprintable():
            text = key
        if text and all(character.isprintable() for character in text):
            self._set_query(self._query + text)

    def _move_highlight(self, delta: int) -> None:
        if not self._rows:
            self._status = (
                "No matching rows. Clear the search to see available rows."
                if self._query
                else "This context has no rows."
            )
            return
        current = None
        for index, row in enumerate(self._rows):
            if row.action_id == self._highlighted:
                current = index
                break
        if current is None:
            index = 0 if delta > 0 else len(self._rows) - 1
        else:
            index = min(max(current + delta, 0), len(self._rows) - 1)
        self._highlight_index(index)

    def _highlight_index(self, index: int) -> None:
        if not self._rows:
            return
        index = min(max(index, 0), len(self._rows) - 1)
        row = self._rows[index]
        self._highlighted = row.action_id
        self._armed = None
        self._status = row.reason if not row.enabled else ""

    def _commit_highlighted(self) -> None:
        if not self._rows:
            self._status = (
                f"No actions match {self._query!r}. Clear the search to see "
                "available rows."
                if self._query
                else "This context has no rows."
            )
            return
        if not self._highlighted:
            self._status = "Highlight a row before pressing Enter."
            return
        self._commit(self._highlighted, 0.0)

    def _set_query(self, query: str) -> None:
        self._query = query
        self._highlighted = None
        self._armed = None
        self._phase = SEARCH if query else LATCHED
        self._rows = build_rows(
            self._profile,
            self._context,
            self._availability,
            self._last_repeat,
            query,
        )
        if self._rows:
            self._status = (
                f"Search: {query}" if query else "Search cleared; profile order restored."
            )
        else:
            self._status = (
                f"No actions match {query!r}. Clear the search to see available rows."
            )

    # ----------------------------------------------------------------- commit

    def _row(self, action_id: str | None):
        if not action_id:
            return None
        for row in self._rows:
            if row.action_id == action_id:
                return row
        return None

    def _commit(self, action_id: str, time_ms: float) -> None:
        """Revalidate and dispatch at most once, then close."""

        row = self._row(action_id)
        if row is None:
            self._status = "That row is no longer available."
            return
        if not row.enabled:
            self._status = row.reason or "That action is unavailable."
            self._record(action_id, "disabled", self._status)
            return

        target_id = self._last_repeat if action_id == REPEAT_ACTION_ID else action_id
        if not target_id:
            self._status = "No repeatable action has launched yet."
            self._record(action_id, "disabled", self._status)
            return
        spec = ACTIONS_BY_ID.get(target_id)
        if (
            spec is None
            or self._context is None
            or self._context not in spec.allowed_levels
        ):
            self._status = "The last action is not available in this context."
            self._record(target_id, "disabled", self._status)
            return
        if self._snapshot is None:
            self._status = "No host context is available."
            self._record(target_id, "disabled", self._status)
            return

        self._dispatch(
            spec,
            target_id,
            self._snapshot,
            clicked_id=action_id,
            from_menu=True,
        )

    def invoke(self, action_id: str) -> MenuView:
        """Invoke one explicit panel/catalog action through the commit path.

        The panel uses this instead of ``host.launch``.  Profile visibility is
        intentionally ignored: a level button exposed by the panel remains
        usable even if a custom profile hides its menu row.  Busy/reentrant
        calls are rejected without dispatch.
        """

        if self._busy:
            self._status = (
                "Another invocation is awaiting parameters; this request was "
                "rejected."
            )
            self._record(action_id, "cancelled", "busy invoke")
            self._bump()
            return self.view()

        if not isinstance(action_id, str) or action_id not in ACTIONS_BY_ID:
            message = "Unknown action ID."
            self._last_result = LaunchResult("blocked", str(action_id), message)
            self._record(str(action_id), "blocked", message)
            self._status = f"Blocked: {message}"
            self._bump()
            return self.view()

        target_id = self._last_repeat if action_id == REPEAT_ACTION_ID else action_id
        if not target_id:
            message = "No repeatable action has launched yet."
            self._last_result = LaunchResult("blocked", action_id, message)
            self._record(action_id, "blocked", message)
            self._status = f"Blocked: {message}"
            self._bump()
            return self.view()
        spec = ACTIONS_BY_ID.get(target_id)
        if spec is None:
            message = "The repeat target is not in the catalog."
            self._last_result = LaunchResult("blocked", action_id, message)
            self._record(action_id, "blocked", message)
            self._status = f"Blocked: {message}"
            self._bump()
            return self.view()

        if self._phase != CLOSED:
            self._close(
                "Panel invocation replaced the open menu; nothing launched from it.",
                result="cancelled",
            )
            self._record(action_id, "cancelled", "panel invocation")

        try:
            fresh = self._host.snapshot()
            if not isinstance(fresh, ContextSnapshot):
                raise TypeError("host snapshot() did not return ContextSnapshot")
            fresh_availability = self._host.availability(fresh)
            if not isinstance(fresh_availability, dict):
                raise TypeError("host availability() did not return a dict")
        except Exception as exc:
            message = f"Host revalidation failed: {exc}"
            self._last_result = LaunchResult("error", target_id, message)
            self._record(target_id, "error", message)
            self._status = f"Error: {message}"
            self._bump()
            return self.view()

        if fresh.level not in spec.allowed_levels:
            message = f"{spec.label} is not available in the {fresh.level.value} context."
            self._last_result = LaunchResult("blocked", target_id, message)
            self._record(target_id, "blocked", message)
            self._status = f"Blocked: {message}"
            self._bump()
            return self.view()

        effective = self._effective_availability(fresh_availability, fresh.level)
        available = effective.get(target_id)
        if not isinstance(available, Availability) or not available.enabled:
            reason = (
                available.reason
                if isinstance(available, Availability)
                else "Host binding not verified."
            )
            self._last_result = LaunchResult("blocked", target_id, reason)
            self._record(target_id, "blocked", reason)
            self._status = f"Blocked {spec.label}: {reason}"
            self._context = fresh.level
            self._snapshot = fresh
            self._availability = effective
            self._bump()
            return self.view()

        self._context = fresh.level
        self._snapshot = fresh
        self._availability = effective
        self._dispatch(
            spec,
            target_id,
            fresh,
            clicked_id=action_id,
            from_menu=False,
        )
        self._bump()
        return self.view()

    def _dispatch(
        self,
        spec,
        target_id: str,
        base_snapshot: ContextSnapshot,
        *,
        clicked_id: str,
        from_menu: bool,
    ) -> None:
        try:
            schema = parameter_schema(target_id, base_snapshot.level)
        except ParameterError as exc:
            self._last_result = LaunchResult(
                "error", target_id, f"Parameter schema unavailable: {exc}"
            )
            self._record(target_id, "error", self._last_result.message)
            status = f"Error preparing {spec.label}: {exc}"
            if from_menu:
                self._close(status, result="error")
            else:
                self._status = status
            return

        if schema and self._parameter_prompt is None:
            message = PARAMETER_DIALOG_UNAVAILABLE
            self._last_result = LaunchResult("blocked", target_id, message)
            self._record(target_id, "blocked", message)
            status = f"Blocked {spec.label}: {message}"
            if from_menu:
                self._close(status, result="blocked")
            else:
                self._status = status
            return

        if schema:
            self._dispatch_parameterized(
                spec,
                target_id,
                base_snapshot,
                schema,
                clicked_id=clicked_id,
                from_menu=from_menu,
            )
        else:
            self._dispatch_parameterless(
                spec,
                target_id,
                base_snapshot,
                clicked_id=clicked_id,
                from_menu=from_menu,
            )

    def _dispatch_parameterless(
        self,
        spec,
        target_id: str,
        base_snapshot: ContextSnapshot,
        *,
        clicked_id: str,
        from_menu: bool,
    ) -> None:
        try:
            fresh = self._host.snapshot()
            fresh_availability = self._host.availability(fresh)
        except Exception as exc:
            self._last_result = LaunchResult(
                "error", target_id, f"Host revalidation failed: {exc}"
            )
            self._record(target_id, "error", self._last_result.message)
            status = f"Error: could not revalidate before dispatch: {exc}"
            if from_menu:
                self._close(status, result="error")
            else:
                self._status = status
            return

        if (
            not isinstance(fresh, ContextSnapshot)
            or fresh.identity() != base_snapshot.identity()
        ):
            message = "Context or selection changed before dispatch; relaunch the menu."
            self._last_result = LaunchResult("blocked", target_id, message)
            self._record(target_id, "blocked", message)
            status = f"Blocked: {message}"
            if from_menu:
                self._close(status, result="blocked")
            else:
                self._status = status
            return

        if not isinstance(fresh_availability, dict):
            message = "Host availability() did not return a dict."
            self._last_result = LaunchResult("error", target_id, message)
            self._record(target_id, "error", message)
            status = f"Error: {message}"
            if from_menu:
                self._close(status, result="error")
            else:
                self._status = status
            return

        effective = self._effective_availability(fresh_availability, fresh.level)
        available = effective.get(target_id)
        if not isinstance(available, Availability) or not available.enabled:
            reason = (
                available.reason
                if isinstance(available, Availability)
                else "Host binding not verified."
            )
            if from_menu:
                # Only capability changed; keep the open menu with honest reasons.
                self._availability = effective
                self._rows = build_rows(
                    self._profile,
                    fresh.level,
                    self._availability,
                    self._last_repeat,
                    self._query,
                )
                self._highlighted = None
                self._armed = None
                self._status = reason or "That action is no longer available."
                self._record(target_id, "blocked", self._status)
            else:
                self._last_result = LaunchResult("blocked", target_id, reason)
                self._record(target_id, "blocked", reason)
                self._status = f"Blocked {spec.label}: {reason}"
            return

        if self._pre_dispatch is not None:
            try:
                self._pre_dispatch(target_id)
            except Exception as exc:
                self._last_result = LaunchResult(
                    "error", target_id, f"Pre-dispatch close failed: {exc}"
                )
                self._record(target_id, "error", self._last_result.message)
                status = f"Error: popup could not close before dispatch: {exc}"
                if from_menu:
                    self._close(status, result="error")
                else:
                    self._status = status
                return

        try:
            result = self._host.launch(target_id, fresh)
        except Exception as exc:
            result = LaunchResult("error", target_id, f"Host dispatch failed: {exc}")
        self._finish(spec, target_id, clicked_id, result)

    def _dispatch_parameterized(
        self,
        spec,
        target_id: str,
        base_snapshot: ContextSnapshot,
        schema: tuple[ParameterSpec, ...],
        *,
        clicked_id: str,
        from_menu: bool,
    ) -> None:
        token = self._new_invocation()
        self._busy = True
        self._invocation_token = token
        if from_menu:
            self._close(
                "Waiting for native parameters; menu closed before the dialog.",
                result="pending",
            )
        else:
            self._status = "Waiting for native parameters."

        try:
            if self._pre_dispatch is not None:
                try:
                    self._pre_dispatch(target_id)
                except Exception as exc:
                    self._last_result = LaunchResult(
                        "error", target_id, f"Pre-dispatch close failed: {exc}"
                    )
                    self._record(target_id, "error", self._last_result.message)
                    self._close(
                        f"Error: popup could not close before dispatch: {exc}",
                        result="error",
                    )
                    return

            try:
                values = self._parameter_prompt(spec, base_snapshot, schema)
            except Exception as exc:
                self._last_result = LaunchResult(
                    "error", target_id, f"Parameter dialog failed: {exc}"
                )
                self._record(target_id, "error", self._last_result.message)
                self._close(
                    f"Error collecting parameters for {spec.label}: {exc}",
                    result="error",
                )
                return

            if not self._token_valid(token):
                message = "Parameter request was superseded; nothing applied."
                self._last_result = LaunchResult("cancelled", target_id, message)
                self._record(target_id, "cancelled", message)
                self._close(f"Cancelled {spec.label}: invocation invalidated.", result="cancelled")
                return

            if values is None:
                message = "Parameter dialog cancelled."
                self._last_result = LaunchResult("cancelled", target_id, message)
                self._record(target_id, "cancelled", message)
                self._close(
                    f"Cancelled {spec.label}; nothing applied.", result="cancelled"
                )
                return

            try:
                parameters = validate_parameters(
                    target_id, base_snapshot.level, values
                )
            except ParameterError as exc:
                self._last_result = LaunchResult(
                    "error", target_id, f"Invalid parameters: {exc}"
                )
                self._record(target_id, "error", self._last_result.message)
                self._close(
                    f"Error validating {spec.label} parameters: {exc}",
                    result="error",
                )
                return

            try:
                fresh = self._host.snapshot()
                fresh_availability = self._host.availability(fresh)
            except Exception as exc:
                self._last_result = LaunchResult(
                    "error", target_id, f"Host revalidation failed: {exc}"
                )
                self._record(target_id, "error", self._last_result.message)
                self._close(
                    f"Error: could not revalidate after parameters: {exc}",
                    result="error",
                )
                return

            if (
                not isinstance(fresh, ContextSnapshot)
                or fresh.identity() != base_snapshot.identity()
            ):
                message = (
                    "Context or selection changed while the parameter dialog was "
                    "open; no edit was applied."
                )
                self._last_result = LaunchResult("blocked", target_id, message)
                self._record(target_id, "blocked", message)
                self._close(f"Blocked: {message}", result="blocked")
                return

            if not isinstance(fresh_availability, dict):
                message = "Host availability() did not return a dict."
                self._last_result = LaunchResult("error", target_id, message)
                self._record(target_id, "error", message)
                self._close(f"Error: {message}", result="error")
                return

            available = fresh_availability.get(target_id)
            if not isinstance(available, Availability) or not available.enabled:
                reason = (
                    available.reason
                    if isinstance(available, Availability)
                    else "Host binding not verified."
                )
                self._last_result = LaunchResult("blocked", target_id, reason)
                self._record(target_id, "blocked", reason)
                self._close(
                    f"Blocked {spec.label}: {reason}", result="blocked"
                )
                return

            if not self._token_valid(token):
                message = "Parameter request was superseded; nothing applied."
                self._last_result = LaunchResult("cancelled", target_id, message)
                self._record(target_id, "cancelled", message)
                self._close(
                    f"Cancelled {spec.label}: invocation invalidated.",
                    result="cancelled",
                )
                return

            try:
                result = self._host.launch(target_id, fresh, parameters)
            except TypeError as exc:
                result = LaunchResult(
                    "error",
                    target_id,
                    "Host adapter does not accept validated parameters: %s" % exc,
                )
            except Exception as exc:
                result = LaunchResult(
                    "error", target_id, f"Host dispatch failed: {exc}"
                )
            self._finish(spec, target_id, clicked_id, result)
        finally:
            if self._invocation_token == token:
                self._invocation_token = None
            self._busy = False

    def _finish(self, spec, target_id: str, clicked_id: str, result: Any) -> None:
        if not isinstance(result, LaunchResult):
            result = LaunchResult(
                "error", target_id, "Host returned an invalid launch result."
            )

        self._last_result = result
        if (
            result.status in ("launched", "simulated", "applied")
            and spec.repeatable
            and clicked_id != REPEAT_ACTION_ID
        ):
            self._last_repeat = target_id

        if result.status == "launched":
            status = f"Launched {spec.label}."
            if result.message:
                status += f" {result.message}"
        elif result.status == "applied":
            status = f"Applied once: {spec.label}."
            if result.message:
                status += f" {result.message}"
        elif result.status == "simulated":
            status = f"SIMULATED {spec.label}: {result.message or 'no host changes'}"
        elif result.status == "noop":
            status = f"No changes: {spec.label}."
            if result.message:
                status += f" {result.message}"
        elif result.status == "cancelled":
            status = f"Cancelled {spec.label}; nothing applied."
            if result.message:
                status += f" {result.message}"
        elif result.status == "blocked":
            status = f"Blocked {spec.label}: {result.message or 'host rejected dispatch'}"
        else:
            status = f"Error launching {spec.label}: {result.message or 'unknown host error'}"

        self._record(target_id, result.status, result.message)
        self._close(status, result=result.status)

    # ---------------------------------------------------------------- profile

    def set_profile(self, profile: MenuProfile) -> MenuView:
        """Replace the profile.  Replacing while open cancels the menu."""

        if not isinstance(profile, MenuProfile):
            raise TypeError("profile must be a MenuProfile")
        if self._busy:
            self._invalidate_invocation()
        was_open = self._phase != CLOSED
        self._profile = profile.clone()
        if was_open:
            self._close("Profile replaced; menu closed without launching.", result="cancelled")
            self._record("profile", "cancelled", "replaced while open")
        else:
            self._status = "Profile loaded."
        self._bump()
        return self.view()

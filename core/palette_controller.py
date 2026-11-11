"""Host-free palette controller for V2 search, pivots, confirmation and dispatch.

The controller is the only object allowed to call ``ManeuverHost``.  Renderers
receive immutable :class:`PaletteView` values and never construct executable
descriptors, touch private fields, or call the host directly.
"""

from __future__ import annotations

import inspect
import uuid
from types import MappingProxyType
from typing import Any, Callable, Mapping

from .contracts import Availability, LaunchResult
from .maneuver_contracts import (
    ConfirmationPrompt,
    ManeuverAtlas,
    ManeuverContext,
    ManeuverHost,
    ManeuverRequest,
    ManeuverRow,
    ManeuverSnapshot,
    PaletteView,
    SystemCommand,
)
from .search import search_maneuvers

_QUERY_LIMIT = 512
_STATUS_LIMIT = 512
_SUCCESS_STATUSES = frozenset({"launched", "applied", "simulated", "noop"})
_CLOSING_STATUSES = _SUCCESS_STATUSES | {"cancelled"}
_COLLAPSE_ID = "collapse_to_poly"


def _safe_text(value: Any, limit: int = _STATUS_LIMIT) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\x00", " ").replace("\r", " ").replace("\n", " ")
    return text[:limit]


class PaletteController:
    """Search-first controller shared by the Qt palette and the simulator."""

    def __init__(
        self,
        atlas: ManeuverAtlas,
        host: ManeuverHost,
        system_callbacks: Mapping[SystemCommand | str, Callable[..., Any]] | None = None,
        *,
        warnings: tuple[str, ...] = (),
    ) -> None:
        if not isinstance(atlas, ManeuverAtlas):
            raise TypeError("atlas must be a ManeuverAtlas")
        if not callable(getattr(host, "snapshot", None)):
            raise TypeError("host must implement the ManeuverHost seam")
        self._atlas = atlas
        self._host = host
        callbacks: dict[SystemCommand, Callable[..., Any]] = {}
        for key, callback in (system_callbacks or {}).items():
            try:
                command = key if isinstance(key, SystemCommand) else SystemCommand(key)
            except ValueError as exc:
                raise TypeError("unknown system command: %r" % (key,)) from exc
            if not callable(callback):
                raise TypeError("system callback for %s must be callable" % command.value)
            callbacks[command] = callback
        self._system_callbacks = MappingProxyType(callbacks)
        self._warnings = tuple(_safe_text(value) for value in warnings if value)

        self._disposed = False
        self._open = False
        self._busy = False
        self._session_id = ""
        self._generation = 0
        self._context: ManeuverContext | None = None
        self._snapshot: ManeuverSnapshot | None = None
        self._query = ""
        self._rows: tuple[ManeuverRow, ...] = ()
        self._highlighted: str | None = None
        self._status = "Ready."
        self._last_result: LaunchResult | None = None
        self._pending: ConfirmationPrompt | None = None
        self._pending_identity: tuple[Any, ...] | None = None
        self._pre_dispatch: Callable[..., Any] | None = None

    # ------------------------------------------------------------------ views

    def view(self) -> PaletteView:
        return PaletteView(
            session_id=self._session_id,
            generation=self._generation,
            context=self._context,
            selection_count=(
                self._snapshot.selection_count if self._snapshot is not None else 0
            ),
            query=self._query,
            rows=self._rows,
            highlighted_row_id=self._highlighted,
            status=self._status,
            busy=self._busy,
            last_result=self._last_result,
            pending_confirmation=self._pending,
        )

    def _bump(self) -> None:
        self._generation += 1

    def _row(self, row_id: str | None) -> ManeuverRow | None:
        if not row_id:
            return None
        for row in self._rows:
            if row.row_id == row_id:
                return row
        return None

    # --------------------------------------------------------------- provider

    def _availability(
        self, maneuver_id: str, pivot_context: ManeuverContext | None
    ) -> Availability:
        if self._snapshot is None:
            return Availability(False, "No host context is available.")
        try:
            value = self._host.availability(
                maneuver_id, self._snapshot, pivot_context=pivot_context
            )
        except Exception:
            return Availability(False, "Availability is unavailable.")
        if not isinstance(value, Availability):
            return Availability(False, "Availability is unavailable.")
        return value

    def _rebuild_rows(self, *, preserve_highlight: bool) -> None:
        if self._context is None or self._snapshot is None:
            self._rows = ()
        else:
            self._rows = search_maneuvers(
                self._atlas, self._query, self._context, self._availability
            )
        if preserve_highlight and self._row(self._highlighted) is not None:
            pass
        elif self._rows:
            first_enabled = next(
                (row for row in self._rows if row.enabled), self._rows[0]
            )
            self._highlighted = first_enabled.row_id
        else:
            self._highlighted = None
        self._bump()

    def _refresh(self, *, preserve_highlight: bool) -> bool:
        try:
            fresh = self._host.snapshot()
        except Exception:
            return False
        if not isinstance(fresh, ManeuverSnapshot):
            return False
        self._snapshot = fresh
        self._context = fresh.context
        self._rebuild_rows(preserve_highlight=preserve_highlight)
        return True

    # ------------------------------------------------------------------- open

    def open(self) -> PaletteView:
        """Open or refresh one session.  This never mutates the host."""

        if self._disposed:
            return self.view()
        if self._busy:
            self._status = "Another dispatch is in progress; wait for it to finish."
            self._bump()
            return self.view()

        was_open = self._open
        self._session_id = uuid.uuid4().hex
        self._open = True
        self._busy = False
        self._pending = None
        self._pending_identity = None
        self._last_result = None
        self._query = self._query if was_open else ""
        try:
            snapshot = self._host.snapshot()
        except Exception:
            self._snapshot = None
            self._context = None
            self._rows = ()
            self._highlighted = None
            self._status = "Host unavailable; no maneuvers can be dispatched."
            self._bump()
            return self.view()
        if not isinstance(snapshot, ManeuverSnapshot):
            self._snapshot = None
            self._context = None
            self._rows = ()
            self._highlighted = None
            self._status = "Host returned an invalid context snapshot."
            self._bump()
            return self.view()

        self._snapshot = snapshot
        self._context = snapshot.context
        self._rebuild_rows(preserve_highlight=False)
        status = "Search 3dsModeler."
        if self._warnings:
            status += " " + "; ".join(self._warnings)
        self._status = status
        return self.view()

    # ---------------------------------------------------------------- search

    def set_query(self, text: str) -> PaletteView:
        if self._disposed:
            return self.view()
        if self._busy:
            self._status = "Search is unavailable while an action is dispatching."
            self._bump()
            return self.view()
        self._query = _safe_text(text, _QUERY_LIMIT)
        self._pending = None
        self._pending_identity = None
        self._rebuild_rows(preserve_highlight=False)
        if not self._rows:
            self._status = "No matching maneuvers. Clear the search to see available rows."
        elif self._query.strip() == ">":
            self._status = "System commands."
        elif self._query:
            self._status = "Search: %s" % self._query
        else:
            self._status = "Search cleared; atlas order restored."
        return self.view()

    def navigate(self, delta: int) -> PaletteView:
        if self._disposed:
            return self.view()
        if self._busy:
            self._status = "Navigation is unavailable while an action is dispatching."
            return self.view()
        if not self._rows:
            self._status = (
                "No matching rows. Clear the search to see available rows."
                if self._query
                else "This context has no rows."
            )
            return self.view()
        try:
            step = int(delta)
        except (TypeError, ValueError):
            step = 0
        current: int | None = None
        for index, row in enumerate(self._rows):
            if row.row_id == self._highlighted:
                current = index
                break
        if current is None:
            index = 0 if step >= 0 else len(self._rows) - 1
        else:
            index = min(max(current + step, 0), len(self._rows) - 1)
        row = self._rows[index]
        self._highlighted = row.row_id
        self._status = row.reason if not row.enabled else ""
        return self.view()

    # -------------------------------------------------------------- activate

    def activate(self, row_id: str, *, session_id: str, generation: int) -> PaletteView:
        if self._disposed:
            return self.view()
        if self._busy:
            self._status = "Another action is already dispatching; wait for the result."
            self._bump()
            return self.view()
        if (
            not self._open
            or session_id != self._session_id
            or generation != self._generation
        ):
            self._status = "That selection is stale; reopen the palette and try again."
            self._bump()
            return self.view()
        if self._pending is not None:
            self._status = "Confirm or cancel the pending destructive action first."
            self._bump()
            return self.view()
        row = self._row(row_id)
        if row is None:
            self._status = "That row is no longer available."
            self._bump()
            return self.view()
        if row.system_command is not None:
            return self._dispatch_system(row)
        if not row.enabled:
            reason = row.reason or "This maneuver is unavailable in the current context."
            self._last_result = LaunchResult("blocked", row.maneuver_id or row_id, reason)
            self._status = "Blocked: %s" % reason
            self._bump()
            return self.view()
        if row.maneuver_id is None:
            self._status = "That row has no maneuver."
            self._bump()
            return self.view()
        if row.maneuver_id == _COLLAPSE_ID:
            return self._request_confirmation(row)
        return self._dispatch_row(row, confirmed=False)

    def _request_confirmation(self, row: ManeuverRow) -> PaletteView:
        if self._snapshot is None:
            self._status = "No host context is available."
            self._bump()
            return self.view()
        token = uuid.uuid4().hex
        prompt = ConfirmationPrompt(
            token=token,
            row_id=row.row_id,
            maneuver_id=row.maneuver_id or _COLLAPSE_ID,
            message=(
                "Collapse the selected node's modifier stack into an Editable Poly "
                "base? Modifiers may be lost and instancing can affect other nodes."
            ),
            destructive_scope=(
                "Only the selected node is targeted; the action is one owned undo "
                "chunk and cannot be undone automatically on failure."
            ),
        )
        self._pending = prompt
        self._pending_identity = (
            token,
            row.row_id,
            self._session_id,
            self._generation,
            self._snapshot.identity(),
        )
        self._status = "Confirm collapse: review the destructive scope, then choose Confirm or Cancel."
        return self.view()

    def confirm(self, token: str, accepted: bool) -> PaletteView:
        if self._disposed:
            return self.view()
        if self._pending is None or token != self._pending.token:
            self._status = "That confirmation is no longer valid."
            return self.view()
        prompt = self._pending
        identity = self._pending_identity
        self._pending = None
        self._pending_identity = None
        if accepted is not True:
            self._last_result = LaunchResult(
                "cancelled", prompt.maneuver_id, "Confirmation declined."
            )
            self._status = "Cancelled collapse; nothing was changed."
            self._bump()
            return self.view()
        current_identity = (
            prompt.token,
            prompt.row_id,
            self._session_id,
            self._generation,
            self._snapshot.identity() if self._snapshot is not None else None,
        )
        if identity is None or current_identity != identity:
            self._last_result = LaunchResult(
                "blocked", prompt.maneuver_id, "Scene or session changed before confirmation."
            )
            self._status = "Blocked: the scene or palette changed before confirmation."
            self._bump()
            return self.view()
        row = self._row(prompt.row_id)
        if row is None or row.maneuver_id != prompt.maneuver_id or not row.enabled:
            self._last_result = LaunchResult(
                "blocked", prompt.maneuver_id, "The confirmed row is no longer available."
            )
            self._status = "Blocked: the confirmed row is no longer available."
            self._bump()
            return self.view()
        return self._dispatch_row(row, confirmed=True)

    # --------------------------------------------------------------- dispatch

    def _call_pre_dispatch(self, maneuver_id: str) -> None:
        callback = self._pre_dispatch
        if callback is None:
            return
        try:
            signature = inspect.signature(callback)
        except (TypeError, ValueError):
            callback(maneuver_id)
            return
        parameters = list(signature.parameters.values())
        positional = [
            parameter
            for parameter in parameters
            if parameter.kind
            in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        ]
        has_varargs = any(
            parameter.kind is inspect.Parameter.VAR_POSITIONAL
            for parameter in parameters
        )
        if not positional and not has_varargs:
            callback()
        else:
            callback(maneuver_id)

    def _dispatch_row(self, row: ManeuverRow, *, confirmed: bool) -> PaletteView:
        if self._disposed:
            return self.view()
        if row.maneuver_id is None:
            self._status = "That row has no maneuver."
            self._bump()
            return self.view()
        try:
            fresh = self._host.snapshot()
        except Exception:
            result = LaunchResult(
                "error", row.maneuver_id, "Host revalidation failed before dispatch."
            )
            return self._finish_dispatch(row, result)
        if not isinstance(fresh, ManeuverSnapshot):
            result = LaunchResult(
                "error", row.maneuver_id, "Host returned an invalid context snapshot."
            )
            return self._finish_dispatch(row, result)
        if self._snapshot is None or fresh.identity() != self._snapshot.identity():
            self._snapshot = fresh
            self._context = fresh.context
            self._rebuild_rows(preserve_highlight=True)
            result = LaunchResult(
                "blocked",
                row.maneuver_id,
                "Context, selection, or safety state changed before dispatch.",
            )
            return self._finish_dispatch(row, result)

        spec = self._atlas.by_id(row.maneuver_id)
        if spec is None:
            result = LaunchResult("error", row.maneuver_id, "Unknown maneuver ID.")
            return self._finish_dispatch(row, result)
        context = row.pivot_context or fresh.context
        route = spec.route_for(context, fresh.selection_count > 0)
        convert_selection = bool(route is not None and route.kind == "context_switch")
        request = ManeuverRequest(
            maneuver_id=row.maneuver_id,
            expected_snapshot=fresh,
            pivot_context=row.pivot_context,
            convert_selection=convert_selection,
            confirmed=bool(confirmed and row.maneuver_id == _COLLAPSE_ID),
        )

        self._busy = True
        try:
            try:
                self._call_pre_dispatch(row.maneuver_id)
            except Exception:
                result = LaunchResult(
                    "error",
                    row.maneuver_id,
                    "Pre-dispatch focus/close hook failed; nothing was dispatched.",
                )
                return self._finish_dispatch(row, result)
            try:
                result = self._host.execute(request)
            except Exception:
                result = LaunchResult(
                    "error", row.maneuver_id, "Host dispatch failed."
                )
            if not isinstance(result, LaunchResult):
                result = LaunchResult(
                    "error", row.maneuver_id, "Host returned an invalid launch result."
                )
            return self._finish_dispatch(row, result)
        finally:
            self._busy = False

    def _finish_dispatch(self, row: ManeuverRow, result: LaunchResult) -> PaletteView:
        self._last_result = result
        label = row.label
        status = {
            "launched": "Launched %s." % label,
            "applied": "Applied once: %s." % label,
            "simulated": "SIMULATED %s." % label,
            "noop": "No changes: %s." % label,
            "cancelled": "Cancelled %s; nothing applied." % label,
            "blocked": "Blocked %s: %s" % (label, result.message or "host rejected dispatch"),
            "error": "Error launching %s: %s" % (label, result.message or "unknown host error"),
        }.get(result.status, "Error launching %s." % label)
        if result.message and result.status in _SUCCESS_STATUSES:
            status += " " + result.message
        self._status = _safe_text(status)
        if result.status in _CLOSING_STATUSES:
            self._open = False
            self._rows = ()
            self._highlighted = None
            self._pending = None
            self._pending_identity = None
            self._query = ""
            self._bump()
        else:
            self._refresh(preserve_highlight=True)
        return self.view()

    def _finish_system(self, command: SystemCommand, result: LaunchResult) -> PaletteView:
        self._last_result = result
        label = command.label
        if result.status in _SUCCESS_STATUSES:
            self._status = "Completed %s." % label
            if result.message:
                self._status += " " + result.message
            self._status = _safe_text(self._status)
            if command in (SystemCommand.PULL_UPDATES, SystemCommand.UNLOAD_UI):
                self._open = False
                self._rows = ()
                self._highlighted = None
                self._pending = None
                self._pending_identity = None
                self._query = ""
                self._bump()
        elif result.status == "blocked":
            self._status = "Blocked %s: %s" % (label, result.message or "unavailable")
            self._bump()
        elif result.status == "cancelled":
            self._status = "Cancelled %s; nothing changed." % label
            self._bump()
        else:
            self._status = "Error running %s: %s" % (
                label,
                result.message or "unknown error",
            )
            self._bump()
        return self.view()

    def _dispatch_system(self, row: ManeuverRow) -> PaletteView:
        command = row.system_command
        if command is None:
            self._status = "That row has no system command."
            self._bump()
            return self.view()
        callback = self._system_callbacks.get(command)
        if callback is None:
            result = LaunchResult(
                "blocked", command.value, "System callback is not installed."
            )
            return self._finish_system(command, result)
        self._busy = True
        try:
            try:
                value = callback()
            except Exception:
                value = LaunchResult(
                    "error", command.value, "System callback failed."
                )
            if isinstance(value, LaunchResult):
                result = value
            elif value is None:
                result = LaunchResult("applied", command.value)
            elif isinstance(value, str):
                result = LaunchResult("applied", command.value, _safe_text(value))
            else:
                result = LaunchResult(
                    "error", command.value, "System callback returned an invalid result."
                )
            if self._disposed:
                return self.view()
            return self._finish_system(command, result)
        finally:
            self._busy = False

    # ---------------------------------------------------------- context switch

    def switch_context(
        self,
        context: ManeuverContext | str,
        *,
        convert_selection: bool = False,
    ) -> PaletteView:
        if self._disposed:
            return self.view()
        if self._busy:
            self._status = "Another action is dispatching; wait before switching context."
            self._bump()
            return self.view()
        try:
            target = (
                context
                if isinstance(context, ManeuverContext)
                else ManeuverContext(context)
            )
        except (TypeError, ValueError):
            self._status = "Unknown context."
            self._bump()
            return self.view()
        try:
            expected = self._host.snapshot()
        except Exception:
            self._last_result = LaunchResult(
                "error", "context:%s" % target.value, "Host snapshot failed."
            )
            self._status = "Error: could not read the current context."
            self._bump()
            return self.view()
        if not isinstance(expected, ManeuverSnapshot):
            self._last_result = LaunchResult(
                "error", "context:%s" % target.value, "Invalid host snapshot."
            )
            self._status = "Error: host returned an invalid context snapshot."
            self._bump()
            return self.view()
        if expected.context is target and not convert_selection:
            self._snapshot = expected
            self._context = target
            self._status = "Already in the %s context." % target.value
            self._bump()
            return self.view()
        try:
            result = self._host.switch_context(
                target,
                expected_snapshot=expected,
                convert_selection=bool(convert_selection),
            )
        except Exception:
            result = LaunchResult(
                "error", "context:%s" % target.value, "Host context switch failed."
            )
        if not isinstance(result, LaunchResult):
            result = LaunchResult(
                "error", "context:%s" % target.value, "Invalid host switch result."
            )
        self._last_result = result
        if result.status in _SUCCESS_STATUSES:
            if self._open:
                if self._refresh(preserve_highlight=False):
                    self._status = "Context: %s." % self._context.value
                else:
                    self._context = target
                    self._rows = ()
                    self._highlighted = None
                    self._bump()
                    self._status = "Context changed; the palette could not refresh."
            else:
                try:
                    fresh = self._host.snapshot()
                except Exception:
                    fresh = None
                if isinstance(fresh, ManeuverSnapshot):
                    self._snapshot = fresh
                    self._context = fresh.context
                else:
                    self._context = target
                self._status = "Context: %s." % self._context.value
            if result.message:
                self._status += " " + result.message
        elif result.status == "blocked":
            self._status = "Blocked context switch: %s" % (
                result.message or "host rejected the switch"
            )
            self._bump()
        else:
            self._status = "Error switching context: %s" % (
                result.message or "unknown host error"
            )
            self._bump()
        self._status = _safe_text(self._status)
        return self.view()

    # -------------------------------------------------------------- lifecycle

    def cancel(self, reason: str = "") -> PaletteView:
        if self._disposed:
            return self.view()
        had_confirmation = self._pending is not None
        prompt = self._pending
        self._pending = None
        self._pending_identity = None
        self._open = False
        self._rows = ()
        self._highlighted = None
        self._query = ""
        if had_confirmation and prompt is not None:
            self._last_result = LaunchResult(
                "cancelled", prompt.maneuver_id, "Destructive action cancelled."
            )
            self._status = "Cancelled collapse; nothing was changed."
        else:
            self._status = "Cancelled."
        if reason:
            self._status += " " + _safe_text(reason)
        self._status = _safe_text(self._status)
        self._bump()
        return self.view()

    def dispose(self) -> PaletteView:
        if self._disposed:
            return self.view()
        self._disposed = True
        self._busy = False
        self._open = False
        self._rows = ()
        self._highlighted = None
        self._pending = None
        self._pending_identity = None
        self._pre_dispatch = None
        self._system_callbacks = MappingProxyType({})
        self._status = "Disposed."
        self._bump()
        return self.view()

    # ------------------------------------------------------------ pre-dispatch

    def set_pre_dispatch(self, callback: Callable[..., Any] | None) -> None:
        if callback is not None and not callable(callback):
            raise TypeError("pre_dispatch callback must be callable or None")
        if self._disposed:
            return
        self._pre_dispatch = callback

    def clear_pre_dispatch(self, callback: Callable[..., Any] | None = None) -> None:
        if callback is None or self._pre_dispatch == callback:
            self._pre_dispatch = None


__all__ = ["PaletteController"]

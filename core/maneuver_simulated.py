"""Deterministic V2 host simulator with no mesh or scene changes.

Every execute/switch result is labelled ``simulated``.  The simulator obeys
the same snapshot/revalidation rules as a real host so the controller, search,
and palette tests exercise the frozen seam rather than a shortcut.
"""

from __future__ import annotations

import hashlib
from typing import Any

from .contracts import Availability, LaunchResult
from .maneuver_contracts import (
    PREP_TIERS,
    ManeuverAtlas,
    ManeuverContext,
    ManeuverRequest,
    ManeuverSnapshot,
)

_CONTEXT_COMPONENT = {
    ManeuverContext.OBJECT: "object",
    ManeuverContext.VERTEX: "vertices",
    ManeuverContext.EDGE: "edges",
    ManeuverContext.BORDER: "border edges",
    ManeuverContext.FACE: "faces",
    ManeuverContext.ELEMENT: "elements",
}

_SELECT_FIRST = {
    ManeuverContext.OBJECT: "Select an object first.",
    ManeuverContext.VERTEX: "Select vertices first.",
    ManeuverContext.EDGE: "Select edges first.",
    ManeuverContext.BORDER: "Select border edges first.",
    ManeuverContext.FACE: "Select faces first.",
    ManeuverContext.ELEMENT: "Select elements first.",
}


def _digest(*parts: object) -> str:
    text = "|".join(str(part) for part in parts)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


class SimulatedManeuverHost:
    """In-memory implementation of the four-method V2 host seam."""

    def __init__(
        self,
        atlas: ManeuverAtlas,
        *,
        context: ManeuverContext | str = ManeuverContext.VERTEX,
        selection_count: int = 1,
        target_kind: str = "editable_poly",
        prep_tier: str = "ready",
    ) -> None:
        if not isinstance(atlas, ManeuverAtlas):
            raise TypeError("atlas must be a ManeuverAtlas")
        self._atlas = atlas
        self._context = (
            context if isinstance(context, ManeuverContext) else ManeuverContext(context)
        )
        self._target_kind = str(target_kind)
        self._target_token = "sim:poly:base"
        self._selection_count = int(selection_count)
        self._selection_token_override: str | None = None
        self._revision = 1
        self._prep_tier = prep_tier if prep_tier in PREP_TIERS else "ready"
        self._blocked_reason = ""
        self._hold_active = False
        self._restore_or_redoing = False
        self._mode_token = "idle"
        self._caddy_active = False
        self._safety_token_override: str | None = None
        self._launch_log: list[dict[str, Any]] = []

    # --------------------------------------------------------------- snapshot

    def _selection_token(self) -> str:
        if self._selection_token_override is not None:
            return self._selection_token_override
        return _digest(
            "selection", self._context.value, self._selection_count, self._revision
        )

    def snapshot(self) -> ManeuverSnapshot:
        selection_token = self._selection_token()
        safety = self._safety_token_override or _digest(
            "safety",
            self._context.value,
            self._target_kind,
            self._target_token,
            self._selection_count,
            selection_token,
            self._revision,
            self._prep_tier,
            self._blocked_reason,
            self._hold_active,
            self._restore_or_redoing,
            self._mode_token,
            self._caddy_active,
        )
        return ManeuverSnapshot(
            context=self._context,
            target_kind=self._target_kind,
            target_token=self._target_token,
            selection_count=self._selection_count,
            selection_token=selection_token,
            revision=self._revision,
            prep_tier=self._prep_tier,
            blocked_reason=self._blocked_reason,
            hold_active=self._hold_active,
            restore_or_redoing=self._restore_or_redoing,
            mode_token=self._mode_token,
            caddy_active=self._caddy_active,
            safety_token=safety,
        )

    # ------------------------------------------------------------- test wiring

    def set_context(
        self,
        context: ManeuverContext | str,
        *,
        selection_count: int | None = None,
        target_kind: str | None = None,
        target_token: str | None = None,
        selection_token: str | None = None,
        prep_tier: str | None = None,
        blocked_reason: str | None = None,
        hold_active: bool | None = None,
        restore_or_redoing: bool | None = None,
        mode_token: str | None = None,
        caddy_active: bool | None = None,
        revision: int | None = None,
    ) -> ManeuverSnapshot:
        self._context = (
            context if isinstance(context, ManeuverContext) else ManeuverContext(context)
        )
        if selection_count is not None:
            self._selection_count = int(selection_count)
            self._selection_token_override = None
        if selection_token is not None:
            self._selection_token_override = str(selection_token)
        if target_kind is not None:
            self._target_kind = str(target_kind)
        if target_token is not None:
            self._target_token = str(target_token)
        if prep_tier is not None:
            if prep_tier not in PREP_TIERS:
                raise ValueError("unknown prep tier: %r" % (prep_tier,))
            self._prep_tier = prep_tier
        if blocked_reason is not None:
            self._blocked_reason = str(blocked_reason)
        if hold_active is not None:
            self._hold_active = bool(hold_active)
        if restore_or_redoing is not None:
            self._restore_or_redoing = bool(restore_or_redoing)
        if mode_token is not None:
            self._mode_token = str(mode_token)
        if caddy_active is not None:
            self._caddy_active = bool(caddy_active)
        self._revision = int(revision) if revision is not None else self._revision + 1
        return self.snapshot()

    def set_selection_count(self, count: int) -> ManeuverSnapshot:
        return self.set_context(self._context, selection_count=count)

    def set_blocked_reason(self, reason: str) -> ManeuverSnapshot:
        self._blocked_reason = str(reason)
        self._revision += 1
        return self.snapshot()

    def set_hold(self, active: bool) -> ManeuverSnapshot:
        self._hold_active = bool(active)
        self._revision += 1
        return self.snapshot()

    def set_caddy_active(self, active: bool) -> ManeuverSnapshot:
        self._caddy_active = bool(active)
        self._revision += 1
        return self.snapshot()

    def advance_revision(self) -> ManeuverSnapshot:
        self._revision += 1
        return self.snapshot()

    # ------------------------------------------------------------- availability

    def availability(
        self,
        maneuver_id: str,
        snapshot: ManeuverSnapshot,
        *,
        pivot_context: ManeuverContext | None = None,
    ) -> Availability:
        if not isinstance(snapshot, ManeuverSnapshot):
            return Availability(False, "Invalid host snapshot.")
        if not isinstance(maneuver_id, str):
            return Availability(False, "Unknown maneuver.")
        spec = self._atlas.by_id(maneuver_id)
        if spec is None:
            return Availability(False, "Unknown maneuver.")
        try:
            destination = (
                pivot_context
                if pivot_context is not None
                else snapshot.context
            )
            if not isinstance(destination, ManeuverContext):
                destination = ManeuverContext(destination)
        except (TypeError, ValueError):
            return Availability(False, "Unknown context.")
        if destination not in spec.contexts:
            return Availability(False, "Not available in the %s context." % destination.value)
        if snapshot.blocked_reason:
            return Availability(False, snapshot.blocked_reason)
        if snapshot.prep_tier == "blocked":
            return Availability(False, "The active target cannot be prepared safely.")
        if snapshot.hold_active:
            return Availability(False, "The undo system is holding; finish the current operation first.")
        if snapshot.restore_or_redoing:
            return Availability(False, "The scene is restoring or redoing; try again.")
        if snapshot.caddy_active:
            return Availability(False, "A native caddy is active; finish or cancel it first.")
        if snapshot.target_kind == "none":
            return Availability(False, "Select a supported object.")
        if snapshot.target_kind == "multiple":
            return Availability(False, "Multiple objects are selected; select one target.")
        if snapshot.target_kind == "edit_poly":
            return Availability(
                False,
                "Edit Poly modifiers are outside V2; use explicit Collapse to Poly.",
            )
        route = spec.route_for(destination, snapshot.selection_count > 0)
        if route is None:
            if snapshot.selection_count > 0:
                return Availability(
                    False,
                    "Not available for selected %s." % _CONTEXT_COMPONENT[destination],
                )
            return Availability(False, _SELECT_FIRST[destination])
        return Availability(True)

    # --------------------------------------------------------------- dispatch

    def execute(self, request: ManeuverRequest) -> LaunchResult:
        if not isinstance(request, ManeuverRequest):
            return LaunchResult("error", "unknown", "Invalid maneuver request.")
        current = self.snapshot()
        if current.identity() != request.expected_snapshot.identity():
            self._log(
                request.maneuver_id,
                current.context,
                "blocked",
                "Context or selection changed before dispatch.",
            )
            return LaunchResult(
                "blocked",
                request.maneuver_id,
                "Context or selection changed before dispatch; relaunch the palette.",
            )
        availability = self.availability(
            request.maneuver_id,
            current,
            pivot_context=request.pivot_context,
        )
        if not availability.enabled:
            self._log(
                request.maneuver_id,
                current.context,
                "blocked",
                availability.reason,
            )
            return LaunchResult("blocked", request.maneuver_id, availability.reason)

        spec = self._atlas.by_id(request.maneuver_id)
        if spec is None:  # pragma: no cover - availability already checked
            return LaunchResult("error", request.maneuver_id, "Unknown maneuver.")
        if request.maneuver_id == "collapse_to_poly" and not request.confirmed:
            self._log(
                request.maneuver_id,
                current.context,
                "blocked",
                "Destructive collapse requires explicit confirmation.",
            )
            return LaunchResult(
                "blocked",
                request.maneuver_id,
                "Destructive collapse requires explicit confirmation.",
            )

        destination = request.pivot_context or current.context
        route = spec.route_for(destination, current.selection_count > 0)
        if route is None:  # pragma: no cover - availability already checked
            return LaunchResult(
                "blocked", request.maneuver_id, "No route is available for this selection."
            )

        changed_context = destination
        if route.kind == "context_switch":
            changed_context = ManeuverContext(route.target)
        self._context = changed_context
        if request.maneuver_id == "convert_to_poly":
            self._target_kind = "editable_poly"
            self._target_token = "sim:poly:base"
        self._prep_tier = "ready"
        self._revision += 1
        self._selection_token_override = None
        self._log(
            request.maneuver_id,
            changed_context,
            "simulated",
            "SIMULATED %s via %s %s; no scene changes."
            % (spec.label, route.kind, route.target),
            kind=route.kind,
            target=route.target,
        )
        return LaunchResult(
            "simulated",
            request.maneuver_id,
            "SIMULATED %s via %s %s; no scene changes."
            % (spec.label, route.kind, route.target),
        )

    # ---------------------------------------------------------- context switch

    def switch_context(
        self,
        context: ManeuverContext,
        *,
        expected_snapshot: ManeuverSnapshot,
        convert_selection: bool = False,
    ) -> LaunchResult:
        if not isinstance(expected_snapshot, ManeuverSnapshot):
            return LaunchResult("error", "context", "Invalid expected snapshot.")
        try:
            target = (
                context
                if isinstance(context, ManeuverContext)
                else ManeuverContext(context)
            )
        except (TypeError, ValueError):
            return LaunchResult("error", "context", "Unknown context.")
        current = self.snapshot()
        if current.identity() != expected_snapshot.identity():
            self._log("context", current.context, "blocked", "Context changed before switch.")
            return LaunchResult(
                "blocked",
                "context",
                "Context or selection changed before the switch; try again.",
            )
        if self._blocked_reason:
            self._log("context", current.context, "blocked", self._blocked_reason)
            return LaunchResult("blocked", "context", self._blocked_reason)
        if self._hold_active or self._restore_or_redoing:
            reason = (
                "The undo system is holding; finish the current operation first."
                if self._hold_active
                else "The scene is restoring or redoing; try again."
            )
            self._log("context", current.context, "blocked", reason)
            return LaunchResult("blocked", "context", reason)
        if target is current.context and not convert_selection:
            self._log("context", current.context, "simulated", "SIMULATED same-context switch.")
            return LaunchResult(
                "simulated", "context", "SIMULATED %s context; no scene changes." % target.value
            )
        self._context = target
        self._revision += 1
        if convert_selection:
            self._selection_token_override = _digest(
                "converted", target.value, self._selection_count, self._revision
            )
        else:
            self._selection_token_override = None
        self._log("context", target, "simulated", "SIMULATED context switch.")
        return LaunchResult(
            "simulated", "context", "SIMULATED %s context; no scene changes." % target.value
        )

    # ----------------------------------------------------------------- helpers

    @property
    def launch_log(self) -> list[dict[str, Any]]:
        return [dict(entry) for entry in self._launch_log]

    def clear_log(self) -> None:
        self._launch_log = []

    def _log(
        self,
        maneuver_id: str,
        context: ManeuverContext,
        status: str,
        detail: str,
        *,
        kind: str | None = None,
        target: str | None = None,
    ) -> None:
        entry: dict[str, Any] = {
            "maneuver_id": maneuver_id,
            "status": status,
            "context": context.value,
            "detail": detail,
        }
        if kind is not None:
            entry["kind"] = kind
        if target is not None:
            entry["target"] = target
        self._launch_log.append(entry)


__all__ = ["SimulatedManeuverHost"]

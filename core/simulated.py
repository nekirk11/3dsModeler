"""Deterministic simulated host with no mesh edits.

Every result is labelled simulated by the view/status; it is never evidence
that Max supports a binding.  Scenario mutations let callers exercise
revalidation while a menu is open.
"""

from __future__ import annotations

from .catalog import ACTIONS, ACTIONS_BY_ID, REPEAT_ACTION_ID
from .contracts import (
    Availability,
    ContextLevel,
    ContextSnapshot,
    LaunchResult,
)
from .parameters import requires_parameters

_PARAMETER_UNAVAILABLE = "Native parameter dialog is unavailable in this renderer."

_LEVELS = tuple(level.value for level in ContextLevel)

SCENARIO_NAMES: tuple[str, ...] = (
    "no_selection",
    "primitive",
    "editable_poly_object",
    "editable_poly_vertex",
    "editable_poly_edge",
    "editable_poly_border",
    "editable_poly_polygon",
    "editable_poly_element",
    "editable_poly_vertex_empty",
    "editable_poly_polygon_empty",
    "multiple_objects",
    "active_edit_poly",
    "conflicting_mode",
    "changed_target",
    "deleted_target",
)

_COMPONENT_REASONS = {
    ContextLevel.VERTEX: "Select vertices.",
    ContextLevel.EDGE: "Select edges.",
    ContextLevel.BORDER: "Select border edges.",
    ContextLevel.POLYGON: "Select polygons.",
    ContextLevel.ELEMENT: "Select elements.",
}


def _scenario_snapshot(name: str, revision: int) -> ContextSnapshot:
    if name == "no_selection":
        return ContextSnapshot(
            ContextLevel.OBJECT, "none", "", 0, "", True, False, revision
        )
    if name == "primitive":
        return ContextSnapshot(
            ContextLevel.OBJECT,
            "other",
            "primitive:box",
            1,
            "sel:primitive:box",
            True,
            False,
            revision,
        )
    if name.startswith("editable_poly_"):
        suffix = name[len("editable_poly_") :]
        empty = suffix.endswith("_empty")
        base = suffix[: -len("_empty")] if empty else suffix
        if base not in _LEVELS:
            raise ValueError(f"Unknown simulated scenario: {name!r}")
        level = ContextLevel(base)
        count = 0 if empty else 1
        return ContextSnapshot(
            level,
            "editable_poly",
            "poly:base",
            count,
            f"sel:{base}:{count}",
            True,
            False,
            revision,
        )
    if name == "multiple_objects":
        return ContextSnapshot(
            ContextLevel.OBJECT,
            "multiple",
            "targets:3",
            3,
            "sel:multiple:3",
            True,
            False,
            revision,
        )
    if name == "active_edit_poly":
        return ContextSnapshot(
            ContextLevel.OBJECT,
            "edit_poly",
            "mod:edit_poly",
            1,
            "sel:edit_poly:1",
            True,
            False,
            revision,
        )
    if name == "conflicting_mode":
        return ContextSnapshot(
            ContextLevel.POLYGON,
            "editable_poly",
            "poly:base",
            1,
            "sel:polygon:1",
            True,
            True,
            revision,
        )
    if name == "changed_target":
        return ContextSnapshot(
            ContextLevel.VERTEX,
            "editable_poly",
            "poly:base:changed",
            1,
            "sel:vertex:changed",
            True,
            False,
            revision,
        )
    if name == "deleted_target":
        return ContextSnapshot(
            ContextLevel.VERTEX, "none", "", 0, "", True, False, revision
        )
    raise ValueError(f"Unknown simulated scenario: {name!r}")


class SimulatedHost:
    """In-memory host adapter used by the sandbox and optional desktop demo."""

    def __init__(self, scenario: str = "editable_poly_vertex") -> None:
        if scenario not in SCENARIO_NAMES:
            raise ValueError(f"Unknown simulated scenario: {scenario!r}")
        self._snapshot = _scenario_snapshot(scenario, 1)
        self._launch_log: list[dict[str, object]] = []

    # --------------------------------------------------------------- mutations
    def set_scenario(self, name: str) -> ContextSnapshot:
        if name not in SCENARIO_NAMES:
            raise ValueError(f"Unknown simulated scenario: {name!r}")
        self._snapshot = _scenario_snapshot(name, self._snapshot.revision + 1)
        return self._snapshot

    def set_context(
        self,
        *,
        level: ContextLevel | str | None = None,
        target_kind: str | None = None,
        target_token: str | None = None,
        selection_count: int | None = None,
        selection_token: str | None = None,
        panel_ready: bool | None = None,
        conflicting_mode: bool | None = None,
        revision: int | None = None,
    ) -> ContextSnapshot:
        current = self._snapshot
        self._snapshot = ContextSnapshot(
            ContextLevel(level) if level is not None else current.level,
            target_kind if target_kind is not None else current.target_kind,
            target_token if target_token is not None else current.target_token,
            selection_count
            if selection_count is not None
            else current.selection_count,
            selection_token
            if selection_token is not None
            else current.selection_token,
            panel_ready if panel_ready is not None else current.panel_ready,
            conflicting_mode
            if conflicting_mode is not None
            else current.conflicting_mode,
            revision if revision is not None else current.revision + 1,
        )
        return self._snapshot

    # ---------------------------------------------------------------- adapter
    def snapshot(self) -> ContextSnapshot:
        return self._snapshot

    def availability(self, context: ContextSnapshot) -> dict[str, Availability]:
        return {
            spec.id: self._availability_for(spec, context)
            for spec in ACTIONS
            if spec.id != REPEAT_ACTION_ID
        }

    def launch(
        self,
        action_id: str,
        expected_context: ContextSnapshot,
        parameters: dict[str, object] | None = None,
    ) -> LaunchResult:
        if not isinstance(expected_context, ContextSnapshot):
            return LaunchResult("error", action_id, "Invalid expected context.")
        if action_id == REPEAT_ACTION_ID:
            return LaunchResult(
                "blocked", action_id, "Repeat is resolved by the controller."
            )
        spec = ACTIONS_BY_ID.get(action_id)
        if spec is None:
            return LaunchResult("error", action_id, "Unknown action ID.")

        current = self._snapshot
        if current.identity() != expected_context.identity():
            self._log(
                action_id,
                current,
                "blocked",
                "Context changed before dispatch.",
            )
            return LaunchResult(
                "blocked",
                action_id,
                "Context or selection changed before dispatch; relaunch the menu.",
            )

        availability = self._availability_for(spec, current)
        if not availability.enabled:
            self._log(action_id, current, "blocked", availability.reason)
            return LaunchResult("blocked", action_id, availability.reason)

        message = f"SIMULATED {spec.label}; no scene or mesh changes."
        self._log(action_id, current, "simulated", message)
        return LaunchResult("simulated", action_id, message)

    # ----------------------------------------------------------------- helpers
    @property
    def launch_log(self) -> list[dict[str, object]]:
        return [dict(entry) for entry in self._launch_log]

    def clear_log(self) -> None:
        self._launch_log = []

    def _log(
        self,
        action_id: str,
        context: ContextSnapshot,
        status: str,
        detail: str,
    ) -> None:
        self._launch_log.append(
            {
                "action_id": action_id,
                "status": status,
                "context": context.level.value,
                "target_kind": context.target_kind,
                "target_token": context.target_token,
                "selection_count": context.selection_count,
                "selection_token": context.selection_token,
                "detail": detail,
            }
        )

    def _availability_for(self, spec, context: ContextSnapshot) -> Availability:
        if context.level not in spec.allowed_levels:
            return Availability(False, "Not available in this context.")
        if requires_parameters(spec.id, context.level):
            return Availability(False, _PARAMETER_UNAVAILABLE)
        if spec.id == "view.frame_selection":
            if context.selection_count > 0 and context.target_kind != "none":
                return Availability(True)
            return Availability(False, "Select one supported object.")

        if spec.id.startswith("transform."):
            if context.selection_count > 0 and context.target_kind != "none":
                return Availability(True)
            return Availability(False, "Select one supported object.")

        # Remaining actions are sub-object selection/modeling operations.
        if context.target_kind == "none":
            return Availability(False, "Select one supported object.")
        if context.target_kind == "multiple":
            return Availability(
                False,
                "Multiple objects selected; sub-object editing is not supported "
                "in this experiment.",
            )
        if context.target_kind in ("edit_poly", "other"):
            return Availability(
                False,
                "Active Edit Poly target not supported in this experiment.",
            )
        if context.target_kind != "editable_poly":
            return Availability(
                False,
                "Active Edit Poly target not supported in this experiment.",
            )
        if not context.panel_ready:
            return Availability(False, "Modify panel must be active.")
        if context.conflicting_mode:
            return Availability(False, "A conflicting command mode is active.")
        if context.selection_count == 0:
            return Availability(
                False,
                _COMPONENT_REASONS.get(context.level, "Select components."),
            )
        return Availability(True)

"""Frozen public contracts shared by every prototype stream.

Keep this module Python 3.11 compatible and stdlib-only.  Renderers and host
adapters consume these types; they must not add renderer state to them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol


class ContextLevel(str, Enum):
    """The six deliberately distinct menu contexts."""

    OBJECT = "object"
    VERTEX = "vertex"
    EDGE = "edge"
    BORDER = "border"
    POLYGON = "polygon"
    ELEMENT = "element"


#: Display/iteration order used for scenario pickers and tests.  This is not
#: an availability or sorting policy.
LEVEL_ORDER: tuple[ContextLevel, ...] = (
    ContextLevel.OBJECT,
    ContextLevel.VERTEX,
    ContextLevel.EDGE,
    ContextLevel.BORDER,
    ContextLevel.POLYGON,
    ContextLevel.ELEMENT,
)

#: Max sub-object level index mapping (0-5) for the adapter.  Mapping is only
#: valid after the adapter has identified a supported active editing target.
LEVEL_INDEX: dict[ContextLevel, int] = {
    level: index for index, level in enumerate(LEVEL_ORDER)
}


@dataclass(frozen=True)
class ContextSnapshot:
    """Opaque, immutable host context read immediately before opening/launch.

    ``target_kind`` is one of ``none``, ``editable_poly``, ``edit_poly``,
    ``other`` or ``multiple``.  ``target_token``/``selection_token`` are
    opaque identity fingerprints; core never dereferences them.  ``revision``
    changes when the active target/stack identity changes.
    """

    level: ContextLevel
    target_kind: str
    target_token: str
    selection_count: int
    selection_token: str
    panel_ready: bool
    conflicting_mode: bool
    revision: int

    TARGET_KINDS = ("none", "editable_poly", "edit_poly", "other", "multiple")

    def __post_init__(self) -> None:
        if isinstance(self.level, str) and not isinstance(self.level, ContextLevel):
            object.__setattr__(self, "level", ContextLevel(self.level))
        if not isinstance(self.level, ContextLevel):
            raise ValueError(f"Unsupported context level: {self.level!r}")
        if self.target_kind not in self.TARGET_KINDS:
            raise ValueError(f"Unsupported target kind: {self.target_kind!r}")
        if self.selection_count < 0:
            raise ValueError("selection_count must be non-negative")
        if self.revision < 0:
            raise ValueError("revision must be non-negative")

    def identity(self) -> tuple[Any, ...]:
        """Fields that must still match immediately before dispatch."""

        return (
            self.level.value,
            self.target_kind,
            self.target_token,
            self.selection_count,
            self.selection_token,
            self.panel_ready,
            self.conflicting_mode,
            self.revision,
        )


@dataclass(frozen=True)
class Availability:
    """Whether an action is currently usable, with a readable reason if not."""

    enabled: bool
    reason: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ValueError("Availability.enabled must be a bool")
        if not self.enabled and not str(self.reason).strip():
            raise ValueError("Disabled availability requires a nonempty reason")


@dataclass(frozen=True)
class ActionSpec:
    """One stable catalog action.  Profile order decides row order."""

    id: str
    label: str
    aliases: tuple[str, ...]
    group: str
    allowed_levels: tuple[ContextLevel, ...]
    repeatable: bool
    execution_kind: str

    EXECUTION_KINDS = ("interactive", "immediate", "selection", "view")

    def __post_init__(self) -> None:
        if self.execution_kind not in self.EXECUTION_KINDS:
            raise ValueError(
                f"Unsupported execution_kind for {self.id}: {self.execution_kind!r}"
            )
        if not self.allowed_levels:
            raise ValueError(f"Action {self.id} must allow at least one level")
        if not self.label.strip():
            raise ValueError(f"Action {self.id} requires a label")


@dataclass(frozen=True)
class LaunchResult:
    """Result of a launch request.

    ``launched`` means a native command mode was entered, not that geometry
    changed.  ``applied`` means the adapter completed one undoable edit and
    checked its operation-specific postcondition; it is still not evidence
    that Max itself was validated in this environment.  ``noop`` means the
    requested values changed nothing.  ``cancelled`` means the user or a
    reentrancy guard stopped the request before dispatch.
    """

    status: str
    action_id: str
    message: str = ""

    STATUSES = (
        "launched",
        "simulated",
        "blocked",
        "error",
        "applied",
        "cancelled",
        "noop",
    )

    def __post_init__(self) -> None:
        if self.status not in self.STATUSES:
            raise ValueError(f"Unsupported launch status: {self.status!r}")


@dataclass(frozen=True)
class ParameterSpec:
    """One frozen, renderer-agnostic parameter contract.

    ``kind`` is ``float``, ``int``, ``bool`` or ``enum``.  It never carries a
    host wrapper, callable, property name, or script fragment.
    """

    name: str
    label: str
    kind: str
    default: Any
    minimum: float | int | None = None
    maximum: float | int | None = None
    choices: tuple[str, ...] = ()
    unit: str = ""

    KINDS = ("float", "int", "bool", "enum")

    def __post_init__(self) -> None:
        if not self.name or not self.name.isidentifier():
            raise ValueError(f"Invalid parameter name: {self.name!r}")
        if not str(self.label).strip():
            raise ValueError(f"Parameter {self.name} requires a label")
        if self.kind not in self.KINDS:
            raise ValueError(f"Unsupported parameter kind: {self.kind!r}")
        if not isinstance(self.unit, str):
            raise ValueError("Parameter unit must be a string")
        if not isinstance(self.choices, tuple) or not all(
            isinstance(choice, str) for choice in self.choices
        ):
            raise ValueError("Parameter choices must be a tuple of strings")
        if self.kind == "enum":
            if not self.choices:
                raise ValueError(f"Enum parameter {self.name} requires choices")
            if self.default not in self.choices:
                raise ValueError(
                    f"Enum parameter {self.name} default is not a choice"
                )
        elif self.choices:
            raise ValueError("Only enum parameters may declare choices")
        if self.kind == "bool":
            if not isinstance(self.default, bool):
                raise ValueError(f"Bool parameter {self.name} default must be bool")
        elif self.kind == "int":
            if not isinstance(self.default, int) or isinstance(self.default, bool):
                raise ValueError(f"Int parameter {self.name} default must be int")
        elif self.kind == "float":
            if isinstance(self.default, bool) or not isinstance(
                self.default, (int, float)
            ):
                raise ValueError(f"Float parameter {self.name} default must be numeric")
        for bound_name, bound in (("minimum", self.minimum), ("maximum", self.maximum)):
            if bound is not None and (
                isinstance(bound, bool) or not isinstance(bound, (int, float))
            ):
                raise ValueError(f"Parameter {bound_name} must be numeric or None")
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ValueError("Parameter minimum must not exceed maximum")


class HostAdapter(Protocol):
    """The only interface through which the core may inspect or act on a host.

    ``parameters`` is optional for backward compatibility with parameter-free
    adapters.  Parameterized catalog actions must not be dispatched without a
    validated mapping.
    """

    def snapshot(self) -> ContextSnapshot:
        ...

    def availability(self, context: ContextSnapshot) -> dict[str, Availability]:
        ...

    def launch(
        self,
        action_id: str,
        expected_context: ContextSnapshot,
        parameters: dict[str, Any] | None = None,
    ) -> LaunchResult:
        ...


@dataclass(frozen=True)
class MenuEvent:
    """Normalized event from a renderer.

    Coordinates are logical-pixel offsets from the invocation origin.  An
    ``action_id`` on pointer/click events is a renderer hit-test result, never
    implicit permission to execute.  ``sequence`` is the producer sequence;
    the controller rejects non-increasing values per session.
    """

    session_id: str = ""
    sequence: int = 0
    kind: str = ""
    time_ms: float = 0.0
    x: float = 0.0
    y: float = 0.0
    action_id: str | None = None
    text: str = ""
    key: str = ""
    auto_repeat: bool = False


@dataclass(frozen=True)
class MenuRow:
    """One renderable, ordered row.  Disabled rows stay in place."""

    action_id: str
    label: str
    group: str
    enabled: bool
    reason: str
    execution_kind: str


@dataclass(frozen=True)
class MenuView:
    """The sole rendering input; JSON-compatible through :meth:`to_dict`."""

    session_id: str = ""
    phase: str = "closed"
    context: ContextLevel | None = None
    query: str = ""
    rows: list[MenuRow] = field(default_factory=list)
    highlighted_id: str | None = None
    armed_id: str | None = None
    status: str = ""
    last_result: LaunchResult | None = None
    log: list[dict[str, Any]] = field(default_factory=list)
    sequence: int = 0

    PHASES = ("closed", "held", "latched", "search")

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "phase": self.phase,
            "context": self.context.value if self.context else None,
            "query": self.query,
            "rows": [
                {
                    "action_id": row.action_id,
                    "label": row.label,
                    "group": row.group,
                    "enabled": row.enabled,
                    "reason": row.reason,
                    "execution_kind": row.execution_kind,
                }
                for row in self.rows
            ],
            "highlighted_id": self.highlighted_id,
            "armed_id": self.armed_id,
            "status": self.status,
            "last_result": (
                {
                    "status": self.last_result.status,
                    "action_id": self.last_result.action_id,
                    "message": self.last_result.message,
                }
                if self.last_result
                else None
            ),
            "log": [dict(entry) for entry in self.log],
            "sequence": self.sequence,
        }


@dataclass(frozen=True)
class ControllerSummary:
    """Read-only controller state for a native tools panel.

    The panel must call :meth:`MenuController.summary` instead of reaching
    through private host/controller attributes.  The object contains no live
    host values and no callables.
    """

    phase: str
    context: ContextLevel | None
    target_kind: str
    selection_count: int
    panel_ready: bool
    conflicting_mode: bool
    busy: bool
    status: str
    last_result: LaunchResult | None
    last_repeat_id: str | None
    availability: tuple[tuple[str, Availability], ...] = ()

    def availability_for(self, action_id: str) -> Availability | None:
        for known_id, value in self.availability:
            if known_id == action_id:
                return value
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "context": self.context.value if self.context else None,
            "target_kind": self.target_kind,
            "selection_count": self.selection_count,
            "panel_ready": self.panel_ready,
            "conflicting_mode": self.conflicting_mode,
            "busy": self.busy,
            "status": self.status,
            "last_result": (
                {
                    "status": self.last_result.status,
                    "action_id": self.last_result.action_id,
                    "message": self.last_result.message,
                }
                if self.last_result
                else None
            ),
            "last_repeat_id": self.last_repeat_id,
            "availability": [
                {
                    "action_id": action_id,
                    "enabled": value.enabled,
                    "reason": value.reason,
                }
                for action_id, value in self.availability
            ],
        }

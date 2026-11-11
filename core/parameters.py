"""Frozen parameter schemas and validation for catalog actions.

This module is the single allowlist for field names, types, ranges, enum
members, and destructive confirmations.  It imports no host module and never
stores callables, scripts, property names, or live pymxs values.

Empty schema means the action takes no parameter prompt.  A nonempty schema
means the controller must collect and validate a concrete mapping before
dispatch; defaults are shown in a dialog and are never applied invisibly.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from .catalog import ACTIONS_BY_ID
from .contracts import ContextLevel, ParameterSpec

MAX_FLOAT_MAGNITUDE = 1_000_000.0
UNIT_SYSTEM = "system units"

_CONFIRM = ParameterSpec(
    name="confirm",
    label="Confirm this destructive edit",
    kind="bool",
    default=False,
)
_OPEN = ParameterSpec(
    name="open",
    label="Open chamfer",
    kind="bool",
    default=False,
)
_SEGMENTS = ParameterSpec(
    name="segments",
    label="Segments",
    kind="int",
    default=1,
    minimum=1,
    maximum=100,
)
_HEIGHT = ParameterSpec(
    name="height",
    label="Height",
    kind="float",
    default=1.0,
    minimum=-MAX_FLOAT_MAGNITUDE,
    maximum=MAX_FLOAT_MAGNITUDE,
    unit=UNIT_SYSTEM,
)
_OUTLINE = ParameterSpec(
    name="outline",
    label="Outline",
    kind="float",
    default=-0.1,
    minimum=-MAX_FLOAT_MAGNITUDE,
    maximum=MAX_FLOAT_MAGNITUDE,
    unit=UNIT_SYSTEM,
)
_AMOUNT = ParameterSpec(
    name="amount",
    label="Amount",
    kind="float",
    default=0.1,
    minimum=0.0,
    maximum=MAX_FLOAT_MAGNITUDE,
    unit=UNIT_SYSTEM,
)
_THRESHOLD = ParameterSpec(
    name="threshold",
    label="Weld threshold",
    kind="float",
    default=0.1,
    minimum=0.0,
    maximum=MAX_FLOAT_MAGNITUDE,
    unit=UNIT_SYSTEM,
)

_EXTRUDE_TYPE = ParameterSpec(
    name="extrusion_type",
    label="Extrusion type",
    kind="enum",
    default="group",
    choices=("group", "local_normal", "by_polygon"),
)
_INSET_TYPE = ParameterSpec(
    name="inset_type",
    label="Inset type",
    kind="enum",
    default="group",
    choices=("group", "by_polygon"),
)
_PINCH = ParameterSpec(
    name="pinch",
    label="Pinch",
    kind="int",
    default=0,
    minimum=-100,
    maximum=100,
)
_SLIDE = ParameterSpec(
    name="slide",
    label="Slide",
    kind="int",
    default=0,
    minimum=-100,
    maximum=100,
)
_TAPER = ParameterSpec(
    name="taper",
    label="Taper",
    kind="float",
    default=0.0,
    minimum=-100.0,
    maximum=100.0,
)
_BIAS = ParameterSpec(
    name="bias",
    label="Bias",
    kind="float",
    default=0.0,
    minimum=-100.0,
    maximum=100.0,
)
#: Autodesk documents bridgeTwist1/bridgeTwist2 as Integer properties, so the
#: frozen schema and native write are exact integers (no fractions/booleans).
_TWIST = ParameterSpec(
    name="twist",
    label="Twist",
    kind="int",
    default=0,
    minimum=-100,
    maximum=100,
)
_CLEANUP = ParameterSpec(
    name="cleanup",
    label="Delete isolated vertices",
    kind="bool",
    default=True,
)


class ParameterError(ValueError):
    """Raised when an action/level/value is not in the frozen allowlist."""


def _coerce_level(level: ContextLevel | str) -> ContextLevel:
    try:
        return level if isinstance(level, ContextLevel) else ContextLevel(level)
    except ValueError as exc:
        raise ParameterError(f"Unsupported context level: {level!r}") from exc


def parameter_schema(
    action_id: str, level: ContextLevel | str
) -> tuple[ParameterSpec, ...]:
    """Return the frozen schema for ``action_id`` at ``level``.

    Raises :class:`ParameterError` for unknown action IDs or levels the action
    cannot operate in.  An empty tuple means no parameter prompt.
    """

    spec = ACTIONS_BY_ID.get(action_id)
    if spec is None:
        raise ParameterError(f"Unknown action ID: {action_id!r}")
    context = _coerce_level(level)
    if context not in spec.allowed_levels:
        raise ParameterError(
            f"{action_id} is not available in the {context.value} context"
        )

    if action_id == "model.extrude":
        return (_HEIGHT, _EXTRUDE_TYPE)
    if action_id == "model.bevel":
        return (_HEIGHT, _OUTLINE, _EXTRUDE_TYPE)
    if action_id == "model.inset":
        return (_AMOUNT, _INSET_TYPE)
    if action_id == "model.chamfer":
        return (_AMOUNT, _OPEN)
    if action_id == "model.connect":
        if context == ContextLevel.VERTEX:
            return ()
        return (_SEGMENTS, _PINCH, _SLIDE)
    if action_id == "model.bridge":
        return (_SEGMENTS, _TAPER, _BIAS, _TWIST)
    if action_id == "model.weld":
        return (_THRESHOLD,)
    if action_id == "model.collapse":
        return (_CONFIRM,)
    if action_id == "model.delete":
        if context == ContextLevel.VERTEX:
            return (_CONFIRM, _CLEANUP)
        return (_CONFIRM,)
    if action_id == "model.remove":
        return (_CONFIRM,)
    return ()


def requires_parameters(action_id: str, level: ContextLevel | str) -> bool:
    """Whether the action/level needs a parameter prompt."""

    return bool(parameter_schema(action_id, level))


def _within_range(spec: ParameterSpec, value: float | int) -> bool:
    if spec.minimum is not None and value < spec.minimum:
        return False
    if spec.maximum is not None and value > spec.maximum:
        return False
    return True


def _validate_value(spec: ParameterSpec, value: Any) -> Any:
    if spec.kind == "bool":
        if not isinstance(value, bool):
            raise ParameterError(f"{spec.name} must be a boolean")
        return value

    if spec.kind == "int":
        if not isinstance(value, int) or isinstance(value, bool):
            raise ParameterError(f"{spec.name} must be an integer")
        if not _within_range(spec, value):
            raise ParameterError(
                f"{spec.name} must be between {spec.minimum} and {spec.maximum}"
            )
        return value

    if spec.kind == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ParameterError(f"{spec.name} must be a finite number")
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ParameterError(f"{spec.name} must be a finite number") from exc
        if not math.isfinite(number):
            raise ParameterError(f"{spec.name} must be finite")
        if abs(number) > MAX_FLOAT_MAGNITUDE:
            raise ParameterError(
                f"{spec.name} exceeds the {MAX_FLOAT_MAGNITUDE:g} unit limit"
            )
        if not _within_range(spec, number):
            raise ParameterError(
                f"{spec.name} must be between {spec.minimum} and {spec.maximum}"
            )
        return number

    if spec.kind == "enum":
        if not isinstance(value, str) or value not in spec.choices:
            raise ParameterError(
                f"{spec.name} must be one of: {', '.join(spec.choices)}"
            )
        return value

    raise ParameterError(f"Unsupported parameter kind for {spec.name}")


def _validate_operation_rules(action_id: str, values: dict[str, Any]) -> None:
    if action_id == "model.bevel":
        if values["height"] == 0 and values["outline"] == 0:
            raise ParameterError("Bevel height and outline cannot both be zero")
    if action_id in ("model.inset", "model.chamfer"):
        if values["amount"] <= 0:
            raise ParameterError("Amount must be greater than zero")
    if action_id == "model.weld":
        if values["threshold"] <= 0:
            raise ParameterError("Weld threshold must be greater than zero")
    if action_id in ("model.collapse", "model.delete", "model.remove"):
        if values.get("confirm") is not True:
            raise ParameterError("Destructive edits require explicit confirmation")


def validate_parameters(
    action_id: str,
    level: ContextLevel | str,
    values: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Validate and normalize one parameter mapping.

    Returns a fresh plain ``dict``.  Missing, extra, mistyped, non-finite, or
    out-of-range fields raise :class:`ParameterError`; no defaults are filled
    when ``values`` is missing for a parameterized action.
    """

    schema = parameter_schema(action_id, level)
    if not schema:
        if values is None:
            return {}
        if not isinstance(values, Mapping):
            raise ParameterError("Parameters must be a mapping")
        if values:
            raise ParameterError(
                "This action takes no parameters; remove "
                + ", ".join(sorted(str(key) for key in values))
            )
        return {}

    if not isinstance(values, Mapping):
        raise ParameterError("Validated parameters are required for this action")
    names = {spec.name for spec in schema}
    extra = sorted(str(key) for key in set(values) - names)
    if extra:
        raise ParameterError("Unknown parameter(s): " + ", ".join(extra))
    missing = sorted(names - set(values))
    if missing:
        raise ParameterError("Missing parameter(s): " + ", ".join(missing))

    validated = {spec.name: _validate_value(spec, values[spec.name]) for spec in schema}
    _validate_operation_rules(action_id, validated)
    return validated

"""Pure, Qt-independent helpers for the native parameter dialog and panel.

This module imports only frozen core contracts.  It never imports PySide6,
``pymxs``, or ``qtmax`` and never touches a host.  Keeping the typed-value
handling here lets the optional Qt widgets stay thin and lets this logic be
tested on a machine without Qt.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from ..core.contracts import ContextLevel, ParameterSpec
from ..core.parameters import (
    MAX_FLOAT_MAGNITUDE,
    ParameterError,
    validate_parameters,
)

INT_FALLBACK_LIMIT = int(MAX_FLOAT_MAGNITUDE)


def initial_values(schema: Sequence[ParameterSpec]) -> dict[str, Any]:
    """Return a fresh mapping of schema defaults in declaration order."""

    return {spec.name: spec.default for spec in schema}


def numeric_bounds(spec: ParameterSpec) -> tuple[float | int, float | int]:
    """Return finite spin-box bounds, falling back to the prototype limit."""

    if spec.kind == "int":
        low = spec.minimum if spec.minimum is not None else -INT_FALLBACK_LIMIT
        high = spec.maximum if spec.maximum is not None else INT_FALLBACK_LIMIT
        return int(low), int(high)
    if spec.kind != "float":
        raise ValueError(f"{spec.name} is not numeric")
    low = spec.minimum if spec.minimum is not None else -MAX_FLOAT_MAGNITUDE
    high = spec.maximum if spec.maximum is not None else MAX_FLOAT_MAGNITUDE
    return float(low), float(high)


def display_choice(choice: str) -> str:
    """Human-readable enum text; the wire value never contains this spelling."""

    return str(choice).replace("_", " ")


def unit_suffix(spec: ParameterSpec) -> str:
    """Return a parenthesized unit suffix, or an empty string."""

    return f" ({spec.unit})" if spec.unit else ""


def has_confirmation(schema: Sequence[ParameterSpec]) -> bool:
    """Whether the schema contains the frozen destructive-edit confirmation."""

    return any(spec.name == "confirm" and spec.kind == "bool" for spec in schema)


def action_summary(spec: Any) -> str:
    """Describe catalog semantics without claiming a host result."""

    kind = str(getattr(spec, "execution_kind", "action"))
    if kind == "immediate":
        detail = "immediate edit"
    elif kind == "interactive":
        detail = "viewport interaction"
    elif kind == "selection":
        detail = "native context/selection change"
    elif kind == "view":
        detail = "viewport view change"
    else:
        detail = kind
    repeatable = bool(getattr(spec, "repeatable", False))
    repeat = (
        "Repeat reopens this dialog against the current context with defaults."
        if repeatable
        else "This action is not repeatable."
    )
    return f"{detail}; {repeat}"


def dialog_notes(
    spec: Any, context: Any, schema: Sequence[ParameterSpec]
) -> tuple[str, ...]:
    """Frozen informational lines shown above the typed controls."""

    level = getattr(getattr(context, "level", None), "value", "unknown")
    target = getattr(context, "target_kind", "unknown")
    count = getattr(context, "selection_count", 0)
    lines = [
        f"{getattr(spec, 'label', 'Action')} · {level} context",
        f"Target: {target}; {count} selected component(s).",
        action_summary(spec),
        "No live preview. Applying once performs a single native edit on the "
        "current Max selection.",
    ]
    if any(spec.unit for spec in schema):
        lines.append("Numeric lengths use 3ds Max system units.")
    if has_confirmation(schema):
        lines.append(
            "This is a destructive edit. Tick the confirmation checkbox to "
            "enable Apply once."
        )
    return tuple(lines)


def validate_dialog_values(
    action_id: str,
    level: ContextLevel | str,
    schema: Sequence[ParameterSpec],
    values: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate one dialog mapping through the frozen core allowlist.

    The dialog checks the schema it was given against the mapping before
    delegating to :func:`core.parameters.validate_parameters`, so a mismatched
    renderer schema cannot smuggle an extra field.
    """

    names = {spec.name for spec in schema}
    provided = set(values)
    extra = sorted(str(key) for key in provided - names)
    missing = sorted(names - provided)
    if extra:
        raise ParameterError("Unexpected dialog field(s): " + ", ".join(extra))
    if missing:
        raise ParameterError("Missing dialog field(s): " + ", ".join(missing))
    return validate_parameters(action_id, level, values)


def describe_target(summary: Any) -> str:
    """Return one read-only target line for a ``ControllerSummary``."""

    context = getattr(summary, "context", None)
    if context is None:
        return "Context not read yet. Press Refresh context or choose a level."
    level = getattr(context, "value", context)
    target = str(getattr(summary, "target_kind", "none"))
    count = int(getattr(summary, "selection_count", 0) or 0)
    panel_ready = bool(getattr(summary, "panel_ready", False))
    parts = [
        f"{level} context",
        f"target {target}",
        f"{count} selected",
        "Modify panel ready" if panel_ready else "Modify panel not detected",
    ]
    if bool(getattr(summary, "conflicting_mode", False)):
        parts.append("conflicting host mode")
    if bool(getattr(summary, "busy", False)):
        parts.append("awaiting parameters")
    return " · ".join(parts)


def describe_launch_result(result: Any) -> str:
    """Render a result with honest applied/launched/blocked/error labels."""

    if result is None:
        return ""
    status = str(getattr(result, "status", "") or "unknown")
    message = str(getattr(result, "message", "") or "")
    labels = {
        "applied": "APPLIED ONCE",
        "launched": "LAUNCHED (mode entry only; no geometry result claimed)",
        "simulated": "SIMULATED (no mesh changes)",
        "blocked": "BLOCKED",
        "error": "ERROR",
        "cancelled": "CANCELLED",
        "noop": "NO CHANGES",
    }
    label = labels.get(status, status.upper())
    return f"{label} — {message}" if message else label


def enabled_action_ids(summary: Any) -> tuple[str, ...]:
    """Return enabled action IDs from a summary without host access."""

    ordered: list[str] = []
    for item in getattr(summary, "availability", ()) or ():
        try:
            action_id, availability = item
        except (TypeError, ValueError):
            continue
        if bool(getattr(availability, "enabled", False)):
            ordered.append(str(action_id))
    return tuple(ordered)


def coalesce_lines(lines: Iterable[str]) -> str:
    """Join nonempty informational lines for a Qt label."""

    return "\n".join(line for line in lines if line)

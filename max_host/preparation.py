"""V2 target inspection and three-tier preparation for 3ds Max 2025.

The V2 host seam accepts a broader set of targets than the frozen V1 adapter
without weakening any safety guard.  This module owns only host-internal live
values: :class:`TargetAssessment` and :class:`PreparedTarget` never cross the
core boundary.  Opaque identity tokens are derived here for snapshots.

Preparation tiers (plan section 6.1):

``ready``
    An unmodified, non-instanced Editable Poly base object.
``convert``
    Exactly one valid non-instanced selected node with no modifiers that Max
    reports as convertible to Editable Poly.  Conversion is one owned undo chunk.
``base_with_modifiers``
    Editable Poly base object with modifiers above it but no Edit_Poly anywhere.
    Only the base interface is edited, ``showEndResult`` is retained/forced on,
    and the stack is never baked.
``blocked``
    Everything else.  Refusals name the explicit remedy (``collapse_to_poly`` or
    Max Make Unique) and occur before panel/base/showEndResult/geometry writes.

No native behavior here was exercised against a real 3ds Max 2025 install; the
live checklist remains NOT RUN.  Importing this module never imports ``pymxs``,
Qt, or ``qtmax``.
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..core.contracts import ContextLevel
from .operations import (
    GeometryInfo,
    GeometryReadError,
    OperationError,
    bitarray_indices,
    read_geometry,
    read_selection_indices,
    read_settings,
)
from .transactions import (
    hold_flags,
    run_undoable,
)

# ---------------------------------------------------------------------------
# V2 contract bridge
#
# Stream A owns ``core/maneuver_contracts.py``.  Until it lands (or when this
# module is imported in a checkout that predates it) the exact plan section 5
# shapes are mirrored here so B can be built and tested independently.  The
# re-export below is the single import point for the max_host layer.
# ---------------------------------------------------------------------------

CONTRACT_SOURCE = "local-fallback"
try:  # pragma: no cover - exercised by the real contract checkout
    from ..core.maneuver_contracts import (  # type: ignore[attr-defined]
        ExecutionDescriptor,
        ManeuverAtlas,
        ManeuverContext,
        ManeuverRequest,
        ManeuverSnapshot,
        ManeuverSpec,
    )

    CONTRACT_SOURCE = "core.maneuver_contracts"
except Exception:  # ImportError or a partially written contract module

    class ManeuverContext(str, Enum):
        """Public V2 context vocabulary (``face`` is the V2 polygon synonym)."""

        OBJECT = "object"
        VERTEX = "vertex"
        EDGE = "edge"
        BORDER = "border"
        FACE = "face"
        ELEMENT = "element"

    @dataclass(frozen=True)
    class ExecutionDescriptor:
        kind: str
        target: str
        api: str
        doc_url: str

    @dataclass(frozen=True)
    class ManeuverSpec:
        id: str
        label: str
        group: str
        aliases: Any
        contexts: Any

    @dataclass(frozen=True)
    class ManeuverAtlas:
        maneuvers: Any

    @dataclass(frozen=True)
    class ManeuverSnapshot:
        context: Any
        target_kind: str = "none"
        target_token: str = ""
        selection_count: int = 0
        selection_token: str = ""
        revision: int = 0
        prep_tier: str = "blocked"
        blocked_reason: str = ""
        hold_active: bool = False
        restore_or_redoing: bool = False
        mode_token: str = "none"
        caddy_active: bool = False
        safety_token: str = ""

    @dataclass(frozen=True)
    class ManeuverRequest:
        maneuver_id: str
        expected_snapshot: Any
        pivot_context: Any = None
        convert_selection: bool = False
        confirmed: bool = False


# ---------------------------------------------------------------------------
# Context mapping
# ---------------------------------------------------------------------------

V2_CONTEXTS: Tuple[str, ...] = (
    "object",
    "vertex",
    "edge",
    "border",
    "face",
    "element",
)

V2_LEVEL_INDEX: Dict[str, int] = {
    "object": 0,
    "vertex": 1,
    "edge": 2,
    "border": 3,
    "face": 4,
    "element": 5,
}

LEVEL_V2_CONTEXT: Dict[int, str] = {index: name for name, index in V2_LEVEL_INDEX.items()}

V2_LEGACY_LEVEL: Dict[str, ContextLevel] = {
    "object": ContextLevel.OBJECT,
    "vertex": ContextLevel.VERTEX,
    "edge": ContextLevel.EDGE,
    "border": ContextLevel.BORDER,
    "face": ContextLevel.POLYGON,
    "element": ContextLevel.ELEMENT,
}

#: ``EditablePoly.ConvertSelection`` name values.
V2_CONVERT_ENUM: Dict[str, str] = {
    "object": "Object",
    "vertex": "Vertex",
    "edge": "Edge",
    "border": "Border",
    "face": "Face",
    "element": "Element",
}

PREP_TIERS = ("ready", "convert", "base_with_modifiers", "blocked")

EDIT_POLY_REASON = (
    "Edit Poly modifiers are outside V2; use explicit Collapse to Poly if you "
    "intend to bake the stack."
)
INSTANCE_REASON = (
    "Instanced or shared base geometry cannot be edited safely; use Max Make "
    "Unique first. Explicit Collapse to Poly will not de-instance."
)
COLLAPSE_REMEDY = (
    "Use the explicit Collapse to Poly action if you intend to bake the modifier "
    "stack. No stack is baked automatically."
)
SHOW_END_RESULT_REASON = (
    "showEndResult could not be read or set on the selected target; editing "
    "beneath the stack cannot be presented safely."
)
CONVERT_REASON = (
    "Max reports that this selected node cannot be converted to Editable Poly; "
    "no conversion was attempted."
)
HOLD_REASON = "An active theHold holding/restoring/redoing state owns the undo system."


class PreparationError(RuntimeError):
    """A preparation or context-switch precondition failed."""


def context_value(value: Any) -> str:
    """Return the canonical V2 context string, rejecting the V1 synonym."""

    if isinstance(value, ManeuverContext):
        value = value.value
    elif hasattr(value, "value") and not isinstance(value, str):
        value = value.value
    if not isinstance(value, str):
        raise ValueError("Unsupported V2 context: %r" % (value,))
    text = value.strip().lower()
    if text == "polygon":
        raise ValueError(
            "The V1 'polygon' context is not a V2 context; use 'face'."
        )
    if text not in V2_CONTEXTS:
        raise ValueError("Unsupported V2 context: %r" % (value,))
    return text


def make_context(value: Any) -> Any:
    """Return a ``ManeuverContext`` instance for a validated context value."""

    text = context_value(value)
    if isinstance(value, ManeuverContext):
        return value
    try:
        return ManeuverContext(text)
    except Exception as exc:  # pragma: no cover - contract mismatch
        raise ValueError("ManeuverContext could not be constructed: %s" % exc) from exc


def to_legacy_context(value: Any) -> ContextLevel:
    """Map a V2 context to the frozen V1 level; only ``face`` is translated."""

    return V2_LEGACY_LEVEL[context_value(value)]


def from_legacy_context(level: Any) -> str:
    """Map a V1 level back to V2; rejects non-frozen legacy values."""

    if isinstance(level, str):
        level = ContextLevel(level)
    if not isinstance(level, ContextLevel):
        raise ValueError("Unsupported legacy context: %r" % (level,))
    return "face" if level == ContextLevel.POLYGON else level.value


def _digest(*parts: Any) -> str:
    hasher = hashlib.sha256()
    for part in parts:
        hasher.update(str(part).encode("utf-8", "backslashreplace"))
        hasher.update(b"\x00")
    return hasher.hexdigest()


def _is_undefined(value: Any) -> bool:
    if value is None:
        return True
    try:
        return str(value).strip().lower() in {"undefined", "none"}
    except Exception:
        return False


def _documented_flag(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    return None


def _stable_handle(runtime: Any, value: Any) -> Optional[str]:
    try:
        handle = runtime.getHandleByAnim(value)
    except Exception:
        return None
    if handle is None or _is_undefined(handle):
        return None
    try:
        text = str(handle).strip()
    except Exception:
        return None
    if not text or text.lower() == "undefined":
        return None
    return "h:%s" % text


def _class_name(runtime: Any, value: Any) -> str:
    try:
        cls = runtime.classOf(value)
    except Exception:
        return "unknown"
    name = getattr(cls, "__name__", None)
    if isinstance(name, str) and name:
        return name
    return str(cls)


def _class_is(runtime: Any, value: Any, expected: str) -> bool:
    if value is None or _is_undefined(value):
        return False
    expected_cls = getattr(runtime, expected, None)
    if expected_cls is not None:
        try:
            if runtime.isKindOf(value, expected_cls):
                return True
        except Exception:
            pass
        try:
            if runtime.classOf(value) == expected_cls:
                return True
        except Exception:
            pass
    name = _class_name(runtime, value).lower()
    for token in ("<", ">", "'", "#", "."):
        name = name.replace(token, " ")
    return expected.lower() in name.split()


def _pymxs_equal(left: Any, right: Any) -> bool:
    try:
        return bool(left == right)
    except Exception:
        return False


def _selected_nodes(runtime: Any) -> List[Any]:
    try:
        selection = runtime.selection
    except Exception:
        return []
    if selection is None or _is_undefined(selection):
        return []
    try:
        return list(selection)
    except TypeError:
        return [selection]
    except Exception:
        return []


def _current_object(runtime: Any) -> Optional[Any]:
    try:
        current = runtime.modPanel.getCurrentObject()
    except Exception:
        return None
    if current is None or _is_undefined(current):
        return None
    return current


def _base_object(node: Any) -> Optional[Any]:
    try:
        base = node.baseObject
    except Exception:
        return None
    if base is None or _is_undefined(base):
        return None
    return base


def _modifiers(node: Any) -> Optional[List[Any]]:
    """Return the modifier list, or ``None`` when it cannot be read."""

    try:
        modifiers = node.modifiers
    except Exception:
        return None
    if modifiers is None or _is_undefined(modifiers):
        return []
    try:
        return list(modifiers)
    except TypeError:
        return None
    except Exception:
        return None


def _editable_poly_interface(node: Any) -> Any:
    try:
        value = getattr(node, "EditablePoly", None)
    except Exception:
        return None
    if value is None or _is_undefined(value):
        return None
    return value


PREVIEW_STATES: Tuple[str, ...] = ("on", "off", "absent", "unknown")


def _preview_flag(ep: Any, names: Sequence[str]) -> Tuple[bool, Optional[bool]]:
    """Return ``(present, value)`` for the first documented preview flag.

    ``present`` is False only when every candidate is genuinely absent
    (``AttributeError``/``None`` on a minimal fake).  A present but
    non-callable, raising, or non-Boolean/non-integer member yields
    ``(True, None)`` so the caller can fail closed.
    """

    for name in names:
        try:
            reader = getattr(ep, name)
        except AttributeError:
            continue
        except Exception:
            return True, None
        if reader is None:
            continue
        if not callable(reader):
            return True, None
        try:
            value = _documented_flag(reader())
        except Exception:
            return True, None
        if value is None:
            return True, None
        return True, value
    return False, None


def read_ep_preview(ep: Any) -> Tuple[str, Optional[bool]]:
    """Read ``EditablePoly.PreviewOn()``/``PreviewGetDragging()`` read-only.

    Returns ``(state, dragging)`` where ``state`` is ``on``, ``off``,
    ``absent`` (no ``PreviewOn`` member), or ``unknown`` (a present member
    raised or returned a non-Boolean/non-integer value).  The live preview
    belongs to the native caddy, so an unreadable present member fails closed.
    ``PreviewGetDragging`` is the documented method; ``PreviewDragging`` is
    accepted only as a backward-compatible secondary spelling.
    """

    if ep is None or _is_undefined(ep):
        return "absent", None
    on_present, on_value = _preview_flag(ep, ("PreviewOn",))
    drag_present, drag_value = _preview_flag(
        ep, ("PreviewGetDragging", "PreviewDragging")
    )
    dragging = drag_value if drag_present else None
    if not on_present:
        if drag_present and drag_value is True:
            return "on", True
        if drag_present and drag_value is None:
            return "unknown", None
        return "absent", dragging
    if on_value is None:
        return "unknown", dragging
    if drag_present and drag_value is None:
        return "unknown", None
    return ("on" if (on_value or dragging) else "off"), dragging


def _dependent_nodes(runtime: Any, base: Any) -> Optional[List[Any]]:
    refs = getattr(runtime, "refs", None)
    if refs is None or _is_undefined(refs):
        return None
    function = getattr(refs, "dependentNodes", None)
    if not callable(function):
        return None
    try:
        result = function(base, firstOnly=False, baseObjectOnly=True)
    except Exception:
        return None
    if result is None or _is_undefined(result):
        return None
    try:
        return list(result)
    except TypeError:
        return [result]
    except Exception:
        return None


def _normalize_mode(value: Any) -> str:
    if value is None or _is_undefined(value):
        return "none"
    try:
        text = str(value).strip().lower()
    except Exception:
        return "unknown"
    text = text.lstrip("#").strip("\"'")
    if text in ("-1", "none"):
        return "none"
    return text or "none"


def _read_task_mode(runtime: Any) -> Tuple[str, bool]:
    function = getattr(runtime, "getCommandPanelTaskMode", None)
    if not callable(function):
        return "unknown", False
    try:
        value = function()
    except Exception:
        return "unknown", False
    if value is None or _is_undefined(value):
        return "unknown", False
    return _normalize_mode(value), True


def _read_raw_level(runtime: Any) -> Optional[int]:
    try:
        value = getattr(runtime, "subObjectLevel")
    except Exception:
        return None
    if value is None or _is_undefined(value) or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _current_hold_reason(runtime: Any) -> Optional[str]:
    """Recheck hold state immediately before an owned undo chunk."""

    holding, restoring, readable = hold_flags(runtime)
    if not readable:
        return (
            "Undo/redo state could not be verified; no owned undo chunk was "
            "started."
        )
    if holding or restoring:
        return HOLD_REASON
    return None


def _native_name(runtime: Any, text: str) -> Any:
    factory = getattr(runtime, "Name", None)
    if not callable(factory):
        raise PreparationError("pymxs.runtime.Name is unavailable.")
    return factory(text)


def _array(runtime: Any, values: Sequence[int]) -> Any:
    factory = getattr(runtime, "Array", None)
    if not callable(factory):
        raise PreparationError("pymxs.runtime.Array is unavailable.")
    return factory(*tuple(int(value) for value in values))


def stack_identity(runtime: Any, node: Any) -> Tuple[Tuple[str, str, bool], ...]:
    """Return a read-only modifier-stack identity snapshot."""

    modifiers = _modifiers(node)
    if modifiers is None:
        return (("stack", "unreadable", False),)
    identity: List[Tuple[str, str, bool]] = []
    for modifier in modifiers:
        try:
            handle = modifier.handle
            handle_text = "h:%s" % handle if not _is_undefined(handle) else "h:?"
        except Exception:
            handle_text = "h:?"
        try:
            enabled = bool(modifier.enabled)
        except Exception:
            enabled = False
        identity.append((_class_name(runtime, modifier), handle_text, enabled))
    return tuple(identity)


def find_edit_poly(runtime: Any, modifiers: Sequence[Any]) -> bool:
    """Q10: any Edit_Poly modifier, active or inactive, is excluded."""

    for modifier in modifiers:
        if _class_is(runtime, modifier, "Edit_Poly"):
            return True
    return False


def _show_end_result(
    runtime: Any, node: Any, base: Any
) -> Tuple[Optional[bool], Any, str]:
    """Read ``showEndResult`` from documented presentation owners."""

    for owner, label in ((node, "node"), (base, "baseObject")):
        try:
            value = getattr(owner, "showEndResult")
        except Exception:
            continue
        if value is None or _is_undefined(value):
            continue
        flag = _documented_flag(value)
        if flag is not None:
            return flag, owner, label
    max_ops = getattr(runtime, "maxOps", None)
    if max_ops is not None and not _is_undefined(max_ops):
        try:
            value = getattr(max_ops, "showEndResult")
        except Exception:
            value = None
        if value is not None and not _is_undefined(value):
            flag = _documented_flag(value)
            if flag is not None:
                return flag, max_ops, "maxOps"
    return None, None, "unreadable"


def _write_show_end_result(owner: Any, value: bool) -> None:
    try:
        setattr(owner, "showEndResult", bool(value))
    except Exception as exc:
        raise PreparationError(SHOW_END_RESULT_REASON) from exc


# ---------------------------------------------------------------------------
# Panel-switch and rollback guards
# ---------------------------------------------------------------------------


def _time_token(runtime: Any) -> str:
    try:
        return str(getattr(runtime, "currentTime", "unreadable"))
    except Exception:
        return "unreadable"


def _transform_token(node: Any) -> str:
    try:
        value = getattr(node, "transform", None)
    except Exception:
        return "unreadable"
    if value is None or _is_undefined(value):
        return "unreadable"
    try:
        return str(value)
    except Exception:
        return "unreadable"


@dataclass(frozen=True)
class _SceneGuard:
    """Read-only scene identity captured before a panel or stack mutation."""

    selected: Tuple[Optional[str], ...]
    node: Optional[str]
    base: Optional[str]
    base_class: str
    stack: Tuple[Tuple[str, str, bool], ...]
    transform: str
    current_time: str
    hold: Tuple[bool, bool, bool]


def _selection_handles(runtime: Any) -> Tuple[Optional[str], ...]:
    return tuple(
        sorted(_stable_handle(runtime, selected) or "?" for selected in _selected_nodes(runtime))
    )


def _capture_scene_guard(runtime: Any, node: Any) -> _SceneGuard:
    current_base = _base_object(node)
    return _SceneGuard(
        selected=_selection_handles(runtime),
        node=_stable_handle(runtime, node),
        base=_stable_handle(runtime, current_base),
        base_class=_class_name(runtime, current_base) if current_base is not None else "none",
        stack=stack_identity(runtime, node),
        transform=_transform_token(node),
        current_time=_time_token(runtime),
        hold=hold_flags(runtime),
    )


def _verify_scene_guard(
    runtime: Any,
    guard: Any,
    node: Any,
    *,
    allow_owned_hold: bool = False,
) -> str:
    """Return a drift reason, or ``""`` when the captured scene is unchanged.

    The base object is always re-read from the node; a caller-supplied cached
    base could otherwise compare the captured base against itself.  Inside an
    owned ``pymxs.undo`` chunk ``theHold.Holding()`` is legitimately True, so
    only ``allow_owned_hold=True`` skips that single check (still rejecting any
    ``RestoreOrRedoing()``).
    """

    if not isinstance(guard, _SceneGuard):
        return "The preparation guard state is unavailable."
    if _selection_handles(runtime) != guard.selected:
        return "The object selection changed while the Modify panel was opening."
    if _stable_handle(runtime, node) != guard.node:
        return "The selected node changed while the Modify panel was opening."
    fresh_base = _base_object(node)
    if _stable_handle(runtime, fresh_base) != guard.base:
        return "The target base object changed while the Modify panel was opening."
    if (
        _class_name(runtime, fresh_base) if fresh_base is not None else "none"
    ) != guard.base_class:
        return "The target base object class changed while the Modify panel was opening."
    if stack_identity(runtime, node) != guard.stack:
        return "The modifier stack changed while the Modify panel was opening."
    if _transform_token(node) != guard.transform:
        return "The target transform changed while the Modify panel was opening."
    if _time_token(runtime) != guard.current_time:
        return "The scene time changed while the Modify panel was opening."
    holding, restoring, readable = hold_flags(runtime)
    if not readable:
        return "Undo/redo state could not be re-verified after the panel switch."
    if restoring:
        return HOLD_REASON
    if holding and not allow_owned_hold:
        return HOLD_REASON
    if not allow_owned_hold and (holding, restoring, readable) != guard.hold:
        return "Undo/redo state changed while the Modify panel was opening."
    return ""


@dataclass(frozen=True)
class _PrimitiveSnapshot:
    """Verifiable pre-mutation state for a non-Editable-Poly target."""

    base_class: str
    base_handle: Optional[str]
    stack: Tuple[Tuple[str, str, bool], ...]
    transform: str
    settings_token: str
    settings_readable: bool
    polygon_count: Optional[str]


@dataclass(frozen=True)
class _PreparationState:
    """Pre-mutation state used to judge rollback after a failed undo chunk."""

    guard: _SceneGuard
    geometry: Optional[GeometryInfo]
    primitive: Optional[_PrimitiveSnapshot] = None
    capture_problem: str = ""


def _polygon_count_token(runtime: Any, node: Any) -> Optional[str]:
    getter = getattr(runtime, "getPolygonCount", None)
    if not callable(getter):
        return None
    try:
        value = getter(node)
    except Exception:
        return None
    if value is None or _is_undefined(value):
        return None
    try:
        items = list(value)
    except TypeError:
        try:
            return str(value)
        except Exception:
            return None
    except Exception:
        return None
    if not items:
        return None
    parts = []
    for item in items:
        try:
            parts.append(str(int(item)))
        except Exception:
            return None
    return ",".join(parts)


def _capture_primitive_snapshot(
    runtime: Any, node: Any
) -> Tuple[Optional[_PrimitiveSnapshot], str]:
    """Capture primitive state, or return why preparation must refuse."""

    base = _base_object(node)
    if base is None:
        return None, "The primitive base object could not be read."
    base_class = _class_name(runtime, base)
    if base_class in ("unknown", "none", ""):
        return None, "The primitive base object class could not be verified."
    base_handle = _stable_handle(runtime, base)
    if base_handle is None:
        return None, "The primitive base object identity could not be verified."
    transform = _transform_token(node)
    if transform == "unreadable":
        return None, "The target transform could not be read before conversion."
    settings_token, settings_readable = read_settings(node)
    polygon_count = _polygon_count_token(runtime, node)
    if not settings_readable and polygon_count is None:
        return None, (
            "The primitive state could not be captured before conversion; "
            "rollback could not be verified."
        )
    return (
        _PrimitiveSnapshot(
            base_class=base_class,
            base_handle=base_handle,
            stack=stack_identity(runtime, node),
            transform=transform,
            settings_token=settings_token,
            settings_readable=settings_readable,
            polygon_count=polygon_count,
        ),
        "",
    )


def _capture_preparation_state(runtime: Any, node: Any) -> _PreparationState:
    guard = _capture_scene_guard(runtime, node)
    try:
        geometry: Optional[GeometryInfo] = read_geometry(runtime, node)
    except Exception:
        geometry = None
    primitive = None
    problem = ""
    base = _base_object(node)
    is_poly = base is not None and _class_is(runtime, base, "Editable_Poly")
    if not is_poly:
        primitive, problem = _capture_primitive_snapshot(runtime, node)
    return _PreparationState(
        guard=guard,
        geometry=geometry,
        primitive=primitive,
        capture_problem=problem,
    )


def _restoration_problem(
    runtime: Any, state: Any, node: Any
) -> str:
    """Return why rollback is unverified, or ``""`` when state matches."""

    if not isinstance(state, _PreparationState):
        return "the pre-preparation state was not captured"
    if state.capture_problem:
        return state.capture_problem
    problem = _verify_scene_guard(runtime, state.guard, node)
    if problem:
        return problem
    if state.primitive is not None:
        fresh, capture_problem = _capture_primitive_snapshot(runtime, node)
        if fresh is None:
            return capture_problem or "the primitive state could not be re-read"
        if fresh != state.primitive:
            return "primitive base/transform/settings/counts were not restored"
    if state.geometry is None:
        if state.primitive is None:
            return "no verifiable geometry or primitive state was captured"
        return ""
    try:
        after = read_geometry(runtime, node)
    except Exception as exc:
        return "geometry could not be re-read (%s)" % type(exc).__name__
    if after.counts != state.geometry.counts or after.digest != state.geometry.digest:
        return "mesh geometry was not restored"
    return ""


def _unverified_rollback(
    runtime: Any, state: Any, node: Any, tier: str, message: str
) -> PreparedTarget:
    problem = _restoration_problem(runtime, state, node)
    if problem:
        return PreparedTarget(
            ok=False,
            status="error",
            tier=tier,
            reason=(
                message
                + " Rollback could not be verified (%s); native V2 topology "
                "mutations must stay latched disabled." % problem
            ),
            rollback_unverified=True,
        )
    return PreparedTarget(
        ok=False,
        status="error",
        tier=tier,
        reason=message + " The owned undo chunk restored the pre-preparation state.",
    )


# ---------------------------------------------------------------------------
# Assessment and prepared target
# ---------------------------------------------------------------------------


@dataclass
class TargetAssessment:
    """Read-only V2 assessment of the active target.

    Host-internal: ``node``/``base``/``ep`` are live pymxs wrappers and never
    cross the core seam.
    """

    tier: str = "blocked"
    reason: str = "Select one object"
    category: str = "target"
    target_kind: str = "none"
    node: Any = None
    base: Any = None
    ep: Any = None
    node_token: Optional[str] = None
    base_token: Optional[str] = None
    modifiers: Tuple[Any, ...] = ()
    modifier_classes: Tuple[str, ...] = ()
    stack: Tuple[Tuple[str, str, bool], ...] = ()
    has_modifiers: bool = False
    has_edit_poly: bool = False
    instanced: Optional[bool] = None
    can_convert: Optional[bool] = None
    task_mode: str = "unknown"
    task_mode_known: bool = False
    panel_ready: bool = False
    raw_level: Optional[int] = None
    context: str = "object"
    indices: Tuple[int, ...] = ()
    selection_count: int = 0
    selection_token: str = ""
    geometry: Optional[GeometryInfo] = None
    geometry_reason: str = ""
    settings_token: str = ""
    settings_readable: bool = False
    show_end_result: Optional[bool] = None
    show_end_result_owner: Any = None
    show_end_result_location: str = "unreadable"
    hold_ok: bool = False
    hold_reason: str = ""
    holding: bool = False
    restore_or_redoing: bool = False
    preview_state: str = "absent"
    preview_dragging: Optional[bool] = None

    def __post_init__(self) -> None:
        if self.tier not in PREP_TIERS:
            raise ValueError("Unsupported preparation tier: %r" % (self.tier,))
        if self.preview_state not in PREVIEW_STATES:
            raise ValueError("Unsupported preview state: %r" % (self.preview_state,))

    @property
    def target_token(self) -> str:
        return _digest("target", self.tier, self.target_kind, self.node_token, self.base_token)


@dataclass
class PreparedTarget:
    """One prepared base-interface target (host-internal)."""

    ok: bool
    status: str  # prepared | noop | blocked | error
    tier: str = "blocked"
    reason: str = ""
    node: Any = None
    base: Any = None
    ep: Any = None
    node_token: Optional[str] = None
    base_token: Optional[str] = None
    context: str = "object"
    raw_level: int = 0
    indices: Tuple[int, ...] = ()
    selection_count: int = 0
    geometry: Optional[GeometryInfo] = None
    settings_token: str = ""
    stack_after: Tuple[Tuple[str, str, bool], ...] = ()
    show_end_result: Optional[bool] = None
    task_mode: str = "unknown"
    converted: bool = False
    collapsed: bool = False
    rollback_unverified: bool = False

    def __post_init__(self) -> None:
        if self.status not in ("prepared", "noop", "blocked", "error"):
            raise ValueError("Unsupported prepared status: %r" % (self.status,))
        if self.tier not in PREP_TIERS:
            raise ValueError("Unsupported preparation tier: %r" % (self.tier,))


@dataclass
class SwitchOutcome:
    """Outcome of a prepared context switch or selection pivot."""

    ok: bool
    status: str  # switched | converted | noop | blocked | error
    reason: str = ""
    context: str = "object"
    raw_level: int = 0
    indices: Tuple[int, ...] = ()
    selection_count: int = 0
    converted: bool = False


@dataclass(frozen=True)
class _PrepOutcome:
    status: str
    message: str


# ---------------------------------------------------------------------------
# Inspection
# ---------------------------------------------------------------------------


def _assert_main_thread(runtime: Any = None) -> None:
    """pymxs must be touched on the Max main thread."""

    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError(
            "Prepared V2 target inspection must run on the 3ds Max main thread."
        )


def _blocked(
    reason: str, category: str, target_kind: str = "other", **kwargs: Any
) -> TargetAssessment:
    assessment = TargetAssessment(
        tier="blocked",
        reason=reason,
        category=category,
        target_kind=target_kind,
        **kwargs,
    )
    return assessment


def inspect_target(runtime: Any) -> TargetAssessment:
    """Assess the active target without mutating panel, stack, or selection."""

    _assert_main_thread(runtime)
    nodes = _selected_nodes(runtime)
    raw_level = _read_raw_level(runtime)
    task_mode, task_mode_known = _read_task_mode(runtime)
    current = _current_object(runtime)

    if not nodes:
        return _blocked(
            "Select one object",
            "target",
            target_kind="none",
            raw_level=raw_level,
            task_mode=task_mode,
            task_mode_known=task_mode_known,
        )
    if len(nodes) > 1:
        return _blocked(
            "Select a single object; V2 topology editing is single-target only.",
            "target",
            target_kind="multiple",
            raw_level=raw_level,
            task_mode=task_mode,
            task_mode_known=task_mode_known,
            selection_count=len(nodes),
        )

    node = nodes[0]
    node_token = _stable_handle(runtime, node)
    base = _base_object(node)
    modifiers = _modifiers(node)

    if node_token is None:
        return _blocked(
            "The selected target has no stable identity; no preparation is allowed.",
            "unreadable state",
            target_kind="other",
            node=node,
            base=base,
            raw_level=raw_level,
            task_mode=task_mode,
            task_mode_known=task_mode_known,
        )
    if base is None:
        return _blocked(
            "The selected target base object could not be read; no preparation is allowed.",
            "unreadable state",
            target_kind="other",
            node=node,
            node_token=node_token,
            raw_level=raw_level,
            task_mode=task_mode,
            task_mode_known=task_mode_known,
        )
    if modifiers is None:
        return _blocked(
            "The modifier stack could not be read; preparation fails closed rather "
            "than assuming zero modifiers.",
            "unreadable state",
            target_kind="other",
            node=node,
            base=base,
            node_token=node_token,
            base_token=_stable_handle(runtime, base),
            raw_level=raw_level,
            task_mode=task_mode,
            task_mode_known=task_mode_known,
        )

    modifier_classes = tuple(_class_name(runtime, modifier) for modifier in modifiers)
    stack = stack_identity(runtime, node)
    base_token = _stable_handle(runtime, base)
    base_is_poly = _class_is(runtime, base, "Editable_Poly")
    has_edit_poly = find_edit_poly(runtime, modifiers)

    panel_current = current is not None and _pymxs_equal(current, base)
    if not panel_current and current is not None:
        panel_current = any(
            _pymxs_equal(current, modifier) for modifier in modifiers
        )
    initial_context = "object"
    if panel_current and raw_level in LEVEL_V2_CONTEXT:
        initial_context = LEVEL_V2_CONTEXT[raw_level]

    def build(**kwargs: Any) -> TargetAssessment:
        defaults: Dict[str, Any] = dict(
            node=node,
            base=base,
            ep=_editable_poly_interface(node),
            node_token=node_token,
            base_token=base_token,
            modifiers=tuple(modifiers),
            modifier_classes=modifier_classes,
            stack=stack,
            has_modifiers=bool(modifiers),
            has_edit_poly=has_edit_poly,
            task_mode=task_mode,
            task_mode_known=task_mode_known,
            raw_level=raw_level,
            panel_ready=panel_current,
            context=initial_context,
        )
        defaults.update(kwargs)
        return TargetAssessment(**defaults)

    # Q10: Edit_Poly anywhere (active or inactive) blocks V2 topology editing.
    if has_edit_poly:
        return build(
            tier="blocked",
            reason=EDIT_POLY_REASON,
            category="edit poly exclusion",
            target_kind="edit_poly",
            panel_ready=current is not None,
        )

    # Sharing/instancing must be proven, never assumed.
    dependents = _dependent_nodes(runtime, base)
    if dependents is None:
        return build(
            tier="blocked",
            reason=(
                "Shared/instanced geometry could not be verified "
                "(refs.dependentNodes unavailable); no preparation is allowed."
            ),
            category="unreadable state",
            target_kind="other",
        )
    instanced = len(dependents) != 1 or not _pymxs_equal(dependents[0], node)
    if instanced:
        return build(
            tier="blocked",
            reason=INSTANCE_REASON,
            category="instance",
            target_kind="other",
            instanced=True,
        )

    holding, restoring, hold_readable = hold_flags(runtime)
    if not hold_readable:
        return build(
            tier="blocked",
            reason=(
                "Undo/redo state could not be verified (theHold unavailable or "
                "unreadable); no preparation is allowed."
            ),
            category="hold",
            target_kind="other",
            instanced=False,
        )
    if holding or restoring:
        return build(
            tier="blocked",
            reason=HOLD_REASON,
            category="hold",
            target_kind="other",
            instanced=False,
            hold_ok=False,
            hold_reason=HOLD_REASON,
            holding=holding,
            restore_or_redoing=restoring,
        )

    if base_is_poly:
        tier = "base_with_modifiers" if modifiers else "ready"
        target_kind = "editable_poly"
        can_convert: Optional[bool] = None
    else:
        if modifiers:
            return build(
                tier="blocked",
                reason=(
                    "A non-Editable Poly base with modifiers cannot be prepared "
                    "without baking the stack. " + COLLAPSE_REMEDY
                ),
                category="target",
                target_kind="other",
                instanced=False,
                can_convert=False,
            )
        convert_target = getattr(runtime, "Editable_Poly", None)
        converter = getattr(runtime, "canConvertTo", None)
        if convert_target is None or not callable(converter):
            return build(
                tier="blocked",
                reason="Conversion capability could not be verified; no conversion was attempted.",
                category="unreadable state",
                target_kind="other",
                instanced=False,
                can_convert=None,
            )
        try:
            can_convert = bool(converter(node, convert_target))
        except Exception:
            return build(
                tier="blocked",
                reason="Conversion capability could not be read; no conversion was attempted.",
                category="unreadable state",
                target_kind="other",
                instanced=False,
                can_convert=None,
            )
        if not can_convert:
            return build(
                tier="blocked",
                reason=CONVERT_REASON,
                category="target",
                target_kind="other",
                instanced=False,
                can_convert=False,
            )
        tier = "convert"
        target_kind = "other"

    # Presentation/context state is informational for inspection.
    panel_ready = False
    if current is not None:
        if _pymxs_equal(current, base):
            panel_ready = True
        else:
            for modifier in modifiers:
                if _pymxs_equal(current, modifier):
                    panel_ready = True
                    break
    context = "object"
    if panel_ready and raw_level in LEVEL_V2_CONTEXT:
        context = LEVEL_V2_CONTEXT[raw_level]

    assessment = build(
        tier=tier,
        reason="",
        category="",
        target_kind=target_kind,
        instanced=False,
        can_convert=can_convert,
        panel_ready=panel_ready,
        context=context,
    )

    legacy_level = V2_LEGACY_LEVEL[context]
    if context == "object":
        assessment.indices = ()
        assessment.selection_count = len(nodes)
        assessment.selection_token = _digest(
            "objects", *sorted(_stable_handle(runtime, selected) or "?" for selected in nodes)
        )
    else:
        try:
            indices = read_selection_indices(runtime, legacy_level, node)
        except OperationError as exc:
            return build(
                tier="blocked",
                reason="Component selection could not be read: %s" % exc,
                category="context/selection",
                target_kind=target_kind,
                instanced=False,
                can_convert=can_convert,
                panel_ready=panel_ready,
                context=context,
            )
        assessment.indices = tuple(indices or ())
        assessment.selection_count = len(assessment.indices)
        assessment.selection_token = (
            _digest("components", context, *assessment.indices)
            if assessment.indices
            else _digest("empty", context)
        )

    if base_is_poly:
        try:
            assessment.geometry = read_geometry(runtime, node)
        except GeometryReadError as exc:
            assessment.geometry_reason = exc.reason
            return build(
                tier="blocked",
                reason=exc.reason,
                category="unreadable state",
                target_kind=target_kind,
                instanced=False,
                can_convert=can_convert,
                panel_ready=panel_ready,
                context=context,
            )
        except Exception as exc:
            assessment.geometry_reason = (
                "Mesh geometry could not be read (%s); topology is disabled."
                % type(exc).__name__
            )
            return build(
                tier="blocked",
                reason=assessment.geometry_reason,
                category="unreadable state",
                target_kind=target_kind,
                instanced=False,
                can_convert=can_convert,
                panel_ready=panel_ready,
                context=context,
            )
        assessment.settings_token, assessment.settings_readable = read_settings(node)
        if not assessment.settings_readable:
            return build(
                tier="blocked",
                reason=(
                    "Soft-selection/constraint/operation settings could not be read; "
                    "topology is disabled."
                ),
                category="unreadable state",
                target_kind=target_kind,
                instanced=False,
                can_convert=can_convert,
                panel_ready=panel_ready,
                context=context,
            )
        (
            assessment.show_end_result,
            assessment.show_end_result_owner,
            assessment.show_end_result_location,
        ) = _show_end_result(runtime, node, base)
    else:
        assessment.geometry = None
        assessment.geometry_reason = ""
        assessment.settings_token, assessment.settings_readable = read_settings(node)

    assessment.hold_ok = True
    assessment.hold_reason = ""
    assessment.holding = holding
    assessment.restore_or_redoing = restoring
    assessment.preview_state, assessment.preview_dragging = read_ep_preview(
        assessment.ep
    )
    return assessment


# ---------------------------------------------------------------------------
# Preparation
# ---------------------------------------------------------------------------


def _ensure_modify_panel(runtime: Any) -> None:
    setter = getattr(runtime, "setCommandPanelTaskMode", None)
    if not callable(setter):
        raise PreparationError(
            "setCommandPanelTaskMode is unavailable; the Modify panel cannot be opened."
        )
    try:
        setter(_native_name(runtime, "modify"))
    except Exception as exc:
        raise PreparationError(
            "The Modify panel could not be opened (%s)." % type(exc).__name__
        ) from exc
    mode, known = _read_task_mode(runtime)
    if not known or mode != "modify":
        raise PreparationError("The Modify panel did not become the active task mode.")


def _set_current_base(runtime: Any, node: Any, base: Any) -> None:
    panel = getattr(runtime, "modPanel", None)
    setter = getattr(panel, "setCurrentObject", None)
    if not callable(setter):
        raise PreparationError(
            "modPanel.setCurrentObject is unavailable; the base object cannot be "
            "shown in the Modify panel."
        )
    current = _current_object(runtime)
    if current is not None and _pymxs_equal(current, base):
        return
    try:
        setter(base)
    except Exception as exc:
        raise PreparationError(
            "The base object could not be shown in the Modify panel (%s)."
            % type(exc).__name__
        ) from exc
    current = _current_object(runtime)
    if current is None or not _pymxs_equal(current, base):
        raise PreparationError(
            "The Modify panel current object did not become the target base object."
        )


def _prepare_ready(
    runtime: Any, assessment: TargetAssessment, undo_factory: Any = None
) -> PreparedTarget:
    guard = _capture_scene_guard(runtime, assessment.node)
    try:
        _ensure_modify_panel(runtime)
        drift = _verify_scene_guard(runtime, guard, assessment.node)
        if drift:
            raise PreparationError(drift)
        _set_current_base(runtime, assessment.node, assessment.base)
    except PreparationError as exc:
        return PreparedTarget(
            ok=False,
            status="error",
            tier=assessment.tier,
            reason=str(exc),
            node=assessment.node,
            base=assessment.base,
        )
    return _prepared_from_assessment(runtime, assessment, converted=False)


def _prepare_base_with_modifiers(
    runtime: Any, assessment: TargetAssessment
) -> PreparedTarget:
    previous = assessment.show_end_result
    owner = assessment.show_end_result_owner
    if previous is None or owner is None:
        return PreparedTarget(
            ok=False,
            status="blocked",
            tier=assessment.tier,
            reason=SHOW_END_RESULT_REASON,
        )
    changed = not previous
    guard = _capture_scene_guard(runtime, assessment.node)
    try:
        _ensure_modify_panel(runtime)
        drift = _verify_scene_guard(runtime, guard, assessment.node)
        if drift:
            raise PreparationError(drift)
        _set_current_base(runtime, assessment.node, assessment.base)
        if changed:
            _write_show_end_result(owner, True)
            observed, observed_owner, _ = _show_end_result(
                runtime, assessment.node, assessment.base
            )
            if observed is not True:
                if previous is False:
                    try:
                        _write_show_end_result(owner, False)
                    except PreparationError:
                        pass
                raise PreparationError(SHOW_END_RESULT_REASON)
    except PreparationError as exc:
        if changed:
            try:
                _write_show_end_result(owner, previous)
            except PreparationError:
                pass
        return PreparedTarget(
            ok=False,
            status="error",
            tier=assessment.tier,
            reason=str(exc),
        )
    return _prepared_from_assessment(runtime, assessment, converted=False)


def _prepare_convert(
    runtime: Any,
    assessment: TargetAssessment,
    *,
    undo_factory: Any = None,
    allow_collapse: bool = False,
) -> PreparedTarget:
    node = assessment.node
    node_token = assessment.node_token
    convert_target = getattr(runtime, "Editable_Poly", None)
    converter = getattr(runtime, "convertTo", None)
    if convert_target is None or not callable(converter):
        return PreparedTarget(
            ok=False,
            status="blocked",
            tier="convert",
            reason="Editable Poly conversion is unavailable in this host.",
        )
    pre_state = _capture_preparation_state(runtime, node)
    if pre_state.capture_problem:
        return PreparedTarget(
            ok=False,
            status="blocked",
            tier="convert",
            reason=pre_state.capture_problem,
        )
    try:
        _ensure_modify_panel(runtime)
    except PreparationError as exc:
        return PreparedTarget(
            ok=False, status="error", tier="convert", reason=str(exc)
        )
    drift = _verify_scene_guard(runtime, pre_state.guard, node)
    if drift:
        return PreparedTarget(
            ok=False, status="error", tier="convert", reason=drift
        )
    hold_problem = _current_hold_reason(runtime)
    if hold_problem:
        return PreparedTarget(
            ok=False, status="error", tier="convert", reason=hold_problem
        )

    def edit() -> _PrepOutcome:
        drift = _verify_scene_guard(
            runtime, pre_state.guard, node, allow_owned_hold=True
        )
        if drift:
            raise PreparationError(drift)
        converter(node, convert_target)
        new_base = _base_object(node)
        if new_base is None or not _class_is(runtime, new_base, "Editable_Poly"):
            raise PreparationError(
                "Conversion did not produce an Editable Poly base object."
            )
        remaining = _modifiers(node)
        if remaining is None or remaining:
            raise PreparationError("Conversion left a modifier stack on the target.")
        if _stable_handle(runtime, node) != node_token:
            raise PreparationError("The selected node identity changed during conversion.")
        if _editable_poly_interface(node) is None:
            raise PreparationError("The converted target exposes no EditablePoly interface.")
        return _PrepOutcome("applied", "Converted the selected object to Editable Poly.")

    outcome = run_undoable(
        "3dsModeler V2: convert to Editable Poly",
        edit,
        undo_factory,
        hold_guard=lambda: _current_hold_reason(runtime),
    )
    if outcome.status == "error" or not outcome.completed:
        return _unverified_rollback(
            runtime, pre_state, node, "convert", outcome.message
        )

    base = _base_object(node)
    if base is None:
        return PreparedTarget(
            ok=False,
            status="error",
            tier="convert",
            reason="The converted Editable Poly base object could not be reacquired.",
        )
    try:
        _set_current_base(runtime, node, base)
    except PreparationError as exc:
        return PreparedTarget(
            ok=False, status="error", tier="convert", reason=str(exc)
        )
    prepared = _prepared_from_assessment(runtime, assessment, converted=True)
    prepared.base = base
    prepared.base_token = _stable_handle(runtime, base)
    prepared.ep = _editable_poly_interface(node)
    prepared.stack_after = stack_identity(runtime, node)
    return prepared


def _prepared_from_assessment(
    runtime: Any, assessment: TargetAssessment, *, converted: bool
) -> PreparedTarget:
    node = assessment.node
    base = _base_object(node) or assessment.base
    ep = _editable_poly_interface(node)
    raw_level = _read_raw_level(runtime)
    context = "object"
    if raw_level in LEVEL_V2_CONTEXT and raw_level != 0:
        context = LEVEL_V2_CONTEXT[raw_level]
    elif raw_level == 0:
        context = "object"
    indices: Tuple[int, ...] = ()
    selection_count = 0
    if context == "object":
        selection_count = len(_selected_nodes(runtime))
    else:
        try:
            indices = tuple(
                read_selection_indices(runtime, V2_LEGACY_LEVEL[context], node) or ()
            )
        except OperationError:
            indices = ()
            selection_count = 0
        else:
            selection_count = len(indices)
    geometry = None
    try:
        geometry = read_geometry(runtime, node)
    except Exception:
        geometry = assessment.geometry
    settings_token, _ = read_settings(node)
    show_end_result, _owner, _location = _show_end_result(runtime, node, base)
    return PreparedTarget(
        ok=True,
        status="prepared",
        tier=assessment.tier,
        reason="",
        node=node,
        base=base,
        ep=ep,
        node_token=assessment.node_token,
        base_token=_stable_handle(runtime, base),
        context=context,
        raw_level=raw_level if raw_level is not None else 0,
        indices=indices,
        selection_count=selection_count,
        geometry=geometry,
        settings_token=settings_token,
        stack_after=stack_identity(runtime, node),
        show_end_result=show_end_result,
        task_mode="modify",
        converted=converted,
    )


def prepare_target(
    runtime: Any,
    assessment: TargetAssessment,
    *,
    allow_collapse: bool = False,
    confirmed: bool = False,
    undo_factory: Any = None,
) -> PreparedTarget:
    """Apply the approved tier immediately before one native dispatch.

    Re-inspection happens first so a stale assessment cannot prepare a newly
    selected target.  ``allow_collapse``/``confirmed`` are only honored by
    :func:`collapse_to_poly`; ordinary preparation never bakes a stack.
    """

    _assert_main_thread(runtime)
    if not isinstance(assessment, TargetAssessment):
        return PreparedTarget(
            ok=False,
            status="blocked",
            reason="A valid target assessment is required.",
        )
    if allow_collapse or confirmed:
        return PreparedTarget(
            ok=False,
            status="blocked",
            tier=assessment.tier,
            reason="Stack collapse requires the explicit collapse_to_poly action.",
        )

    fresh = inspect_target(runtime)
    if (
        fresh.node_token != assessment.node_token
        or fresh.tier != assessment.tier
        or fresh.base_token != assessment.base_token
        or fresh.raw_level != assessment.raw_level
        or fresh.selection_token != assessment.selection_token
    ):
        return PreparedTarget(
            ok=False,
            status="blocked",
            tier=fresh.tier,
            reason="The target changed before preparation; reopen the context.",
        )

    if fresh.tier == "blocked":
        return PreparedTarget(
            ok=False,
            status="blocked",
            tier="blocked",
            reason=fresh.reason,
        )
    if fresh.tier == "ready":
        return _prepare_ready(runtime, fresh, undo_factory)
    if fresh.tier == "base_with_modifiers":
        return _prepare_base_with_modifiers(runtime, fresh)
    if fresh.tier == "convert":
        return _prepare_convert(runtime, fresh, undo_factory=undo_factory)
    return PreparedTarget(
        ok=False,
        status="blocked",
        tier=fresh.tier,
        reason="Unsupported preparation tier.",
    )


def collapse_to_poly(
    runtime: Any,
    assessment: TargetAssessment,
    *,
    confirmed: bool,
    undo_factory: Any = None,
) -> PreparedTarget:
    """Explicit, confirmed stack bake in one owned undo chunk.

    This is the only V2 path allowed to remove a modifier stack.  Instanced or
    shared targets are refused; no Make Unique or de-instancing is attempted.
    """

    _assert_main_thread(runtime)
    if not isinstance(assessment, TargetAssessment):
        return PreparedTarget(
            ok=False, status="blocked", reason="A valid target assessment is required."
        )
    if not confirmed:
        return PreparedTarget(
            ok=False,
            status="blocked",
            tier=assessment.tier,
            reason=(
                "Collapsing the stack is destructive and requires explicit "
                "confirmation describing modifier loss and possible instancing "
                "impact."
            ),
        )
    if assessment.tier == "blocked" and assessment.instanced:
        return PreparedTarget(
            ok=False, status="blocked", tier="blocked", reason=INSTANCE_REASON
        )

    fresh = inspect_target(runtime)
    if fresh.node is None or fresh.node_token is None:
        return PreparedTarget(
            ok=False,
            status="blocked",
            tier="blocked",
            reason=fresh.reason or "Select one object to collapse.",
        )
    if fresh.instanced:
        return PreparedTarget(
            ok=False, status="blocked", tier="blocked", reason=INSTANCE_REASON
        )
    if not fresh.hold_ok:
        holding, restoring, readable = hold_flags(runtime)
        if not readable or holding or restoring:
            return PreparedTarget(
                ok=False,
                status="blocked",
                tier="blocked",
                reason=(
                    "Undo/redo state could not be verified; explicit collapse is "
                    "blocked."
                ),
            )
    if fresh.base is not None:
        dependents = _dependent_nodes(runtime, fresh.base)
        if (
            dependents is None
            or len(dependents) != 1
            or not _pymxs_equal(dependents[0], fresh.node)
        ):
            return PreparedTarget(
                ok=False, status="blocked", tier="blocked", reason=INSTANCE_REASON
            )
    if fresh.tier == "blocked" and not fresh.has_edit_poly and fresh.category not in (
        "target",
        "edit poly exclusion",
    ):
        return PreparedTarget(
            ok=False, status="blocked", tier="blocked", reason=fresh.reason
        )
    if fresh.node is None or fresh.node_token is None:
        return PreparedTarget(
            ok=False, status="blocked", tier="blocked", reason=fresh.reason
        )

    node = fresh.node
    node_token = fresh.node_token
    base_is_poly = fresh.base is not None and _class_is(
        runtime, fresh.base, "Editable_Poly"
    )
    has_modifiers = bool(fresh.modifiers)
    if base_is_poly and not has_modifiers:
        return PreparedTarget(
            ok=True,
            status="noop",
            tier="ready",
            reason="The selected object is already an unmodified Editable Poly; no stack was baked.",
        )

    if base_is_poly:
        command = getattr(runtime, "collapseStack", None)
        command_name = "collapseStack"
        if not callable(command):
            return PreparedTarget(
                ok=False,
                status="blocked",
                tier="blocked",
                reason=(
                    "collapseStack is unavailable; the modifier stack cannot be "
                    "baked explicitly."
                ),
            )
    else:
        command = getattr(runtime, "convertTo", None)
        command_name = "convertTo"
        convert_target = getattr(runtime, "Editable_Poly", None)
        if not callable(command) or convert_target is None:
            return PreparedTarget(
                ok=False,
                status="blocked",
                tier="blocked",
                reason="Editable Poly conversion is unavailable in this host.",
            )

    stack_before = stack_identity(runtime, node)
    pre_state = _capture_preparation_state(runtime, node)
    if pre_state.capture_problem:
        return PreparedTarget(
            ok=False,
            status="blocked",
            tier="blocked",
            reason=pre_state.capture_problem,
        )

    def edit() -> _PrepOutcome:
        drift = _verify_scene_guard(
            runtime, pre_state.guard, node, allow_owned_hold=True
        )
        if drift:
            raise PreparationError(drift)
        if command_name == "convertTo":
            command(node, convert_target)
        else:
            command(node)
        new_base = _base_object(node)
        if new_base is None or not _class_is(runtime, new_base, "Editable_Poly"):
            raise PreparationError("Stack collapse did not produce an Editable Poly base.")
        later = _modifiers(node)
        if later is None or later:
            raise PreparationError("Stack collapse left modifiers on the target.")
        if _stable_handle(runtime, node) != node_token:
            raise PreparationError("The selected node identity changed during collapse.")
        if _editable_poly_interface(node) is None:
            raise PreparationError("The collapsed target exposes no EditablePoly interface.")
        return _PrepOutcome(
            "applied",
            "Baked the modifier stack into Editable Poly (%d stack item(s) removed)."
            % len(stack_before),
        )

    try:
        _ensure_modify_panel(runtime)
    except PreparationError as exc:
        return PreparedTarget(
            ok=False, status="error", tier="blocked", reason=str(exc)
        )
    drift = _verify_scene_guard(runtime, pre_state.guard, node)
    if drift:
        return PreparedTarget(
            ok=False, status="error", tier="blocked", reason=drift
        )
    hold_problem = _current_hold_reason(runtime)
    if hold_problem:
        return PreparedTarget(
            ok=False, status="error", tier="blocked", reason=hold_problem
        )
    outcome = run_undoable(
        "3dsModeler V2: collapse to Editable Poly",
        edit,
        undo_factory,
        hold_guard=lambda: _current_hold_reason(runtime),
    )
    if outcome.status == "error" or not outcome.completed:
        return _unverified_rollback(
            runtime, pre_state, node, "blocked", outcome.message
        )

    base = _base_object(node)
    try:
        _set_current_base(runtime, node, base)
    except PreparationError as exc:
        return PreparedTarget(
            ok=False, status="error", tier="blocked", reason=str(exc)
        )
    fresh_after = inspect_target(runtime)
    prepared = _prepared_from_assessment(runtime, fresh_after, converted=not base_is_poly)
    prepared.collapsed = True
    prepared.status = "prepared"
    prepared.tier = "ready"
    prepared.reason = outcome.message
    return prepared


# ---------------------------------------------------------------------------
# Context switching and the open-edge fill pivot
# ---------------------------------------------------------------------------


def _selection_count(runtime: Any, prepared: PreparedTarget, context: str) -> int:
    if context == "object":
        return len(_selected_nodes(runtime))
    try:
        indices = read_selection_indices(
            runtime, V2_LEGACY_LEVEL[context], prepared.node
        )
    except OperationError:
        return 0
    return len(indices or ())


def _set_level(runtime: Any, index: int) -> Tuple[bool, str]:
    try:
        runtime.subObjectLevel = index
        observed = int(runtime.subObjectLevel)
    except Exception as exc:
        return False, "subObjectLevel could not be set (%s)." % type(exc).__name__
    if observed != index:
        return False, "subObjectLevel readback was %s, expected %d." % (observed, index)
    return True, ""


def switch_prepared_context(
    runtime: Any,
    prepared: PreparedTarget,
    context: Any,
    *,
    convert_selection: bool = False,
    hold_guard: Any = None,
) -> SwitchOutcome:
    """Switch the raw sub-object level on one prepared target.

    Ordinary switches set only ``subObjectLevel`` and preserve the existing
    destination selection.  ``convert_selection=True`` invokes the documented
    ``EditablePoly.ConvertSelection`` between fixed name enums first.
    """

    _assert_main_thread(runtime)
    try:
        destination = context_value(context)
    except ValueError as exc:
        return SwitchOutcome(False, "blocked", str(exc))
    if not isinstance(prepared, PreparedTarget) or not prepared.ok:
        return SwitchOutcome(
            False,
            "blocked",
            prepared.reason if isinstance(prepared, PreparedTarget) else "The target is not prepared.",
        )
    if prepared.ep is None or _is_undefined(prepared.ep):
        return SwitchOutcome(
            False, "blocked", "The target EditablePoly interface is unavailable."
        )
    if hold_guard is not None:
        try:
            reason = hold_guard()
        except Exception as exc:
            return SwitchOutcome(
                False,
                "blocked",
                "Undo/redo state could not be verified (%s)." % type(exc).__name__,
            )
        if reason:
            return SwitchOutcome(False, "blocked", reason)

    index = V2_LEVEL_INDEX[destination]
    if destination == prepared.context:
        return SwitchOutcome(
            True,
            "noop",
            "Already in the %s context." % destination,
            context=destination,
            raw_level=prepared.raw_level,
            indices=prepared.indices,
            selection_count=_selection_count(runtime, prepared, destination),
        )

    previous_level = prepared.raw_level
    guard = _capture_scene_guard(runtime, prepared.node)
    drift = _verify_scene_guard(runtime, guard, prepared.node)
    if drift:
        return SwitchOutcome(
            False,
            "blocked",
            drift,
            context=prepared.context,
            raw_level=previous_level,
        )
    converted = False
    if convert_selection:
        try:
            source_name = _native_name(runtime, V2_CONVERT_ENUM[prepared.context])
            destination_name = _native_name(runtime, V2_CONVERT_ENUM[destination])
            prepared.ep.ConvertSelection(
                source_name, destination_name, requireAll=False
            )
        except Exception as exc:
            return SwitchOutcome(
                False,
                "error",
                "Selection conversion failed (%s)." % type(exc).__name__,
                context=prepared.context,
                raw_level=previous_level,
            )
        converted = True
        drift = _verify_scene_guard(runtime, guard, prepared.node)
        if drift:
            return SwitchOutcome(
                False,
                "error",
                "The scene changed during selection conversion: %s" % drift,
                context=prepared.context,
                raw_level=previous_level,
            )

    ok, reason = _set_level(runtime, index)
    if not ok:
        if converted:
            try:
                runtime.subObjectLevel = previous_level
            except Exception:
                pass
        return SwitchOutcome(
            False,
            "error",
            reason,
            context=prepared.context,
            raw_level=previous_level,
        )

    try:
        indices = (
            ()
            if destination == "object"
            else tuple(
                read_selection_indices(
                    runtime, V2_LEGACY_LEVEL[destination], prepared.node
                )
                or ()
            )
        )
    except OperationError:
        indices = ()
    count = _selection_count(runtime, prepared, destination)
    return SwitchOutcome(
        True,
        "converted" if converted else "switched",
        (
            "Converted the component selection to the %s context; no geometry changed."
            % destination
            if converted
            else "Entered the native %s sub-object context; the existing selection was preserved."
            % destination
        ),
        context=destination,
        raw_level=index,
        indices=indices,
        selection_count=count,
        converted=converted,
    )


def _open_edges(runtime: Any, node: Any) -> Optional[Tuple[int, ...]]:
    polyop = getattr(runtime, "polyop", None)
    if polyop is None or _is_undefined(polyop):
        return None
    try:
        bitarray = polyop.getOpenEdges(node)
    except Exception:
        return None
    if bitarray is None or _is_undefined(bitarray):
        return ()
    indices = bitarray_indices(bitarray, runtime)
    if indices is None:
        return None
    return tuple(sorted(indices))


def _set_edge_selection(runtime: Any, node: Any, indices: Sequence[int]) -> bool:
    polyop = getattr(runtime, "polyop", None)
    setter = getattr(polyop, "setEdgeSelection", None)
    if not callable(setter):
        return False
    try:
        setter(node, _array(runtime, indices))
    except Exception:
        return False
    return True


def pivot_open_edges_to_border(
    runtime: Any, prepared: PreparedTarget
) -> SwitchOutcome:
    """The ``fill`` intent exception: Edge -> open-border selection -> Cap.

    Only selected edges that are documented open edges are converted.  Closed
    edges are never capped, and an empty intersection is blocked before any
    selection or level write.
    """

    _assert_main_thread(runtime)
    if not isinstance(prepared, PreparedTarget) or not prepared.ok:
        return SwitchOutcome(False, "blocked", "The target is not prepared.")
    if prepared.context not in ("edge", "border"):
        return SwitchOutcome(
            False,
            "blocked",
            "Fill pivots from the edge context only.",
            context=prepared.context,
        )
    if prepared.context == "border":
        if not prepared.indices:
            return SwitchOutcome(
                False,
                "blocked",
                "Select open border edges first.",
                context="border",
            )
        return SwitchOutcome(
            True,
            "noop",
            "Already in the border context.",
            context="border",
            raw_level=prepared.raw_level,
            indices=prepared.indices,
            selection_count=len(prepared.indices),
        )

    open_edges = _open_edges(runtime, prepared.node)
    if open_edges is None:
        return SwitchOutcome(
            False,
            "blocked",
            "Open edges could not be read; fill will not cap arbitrary borders.",
            context="edge",
        )
    selected = tuple(prepared.indices)
    if not selected:
        return SwitchOutcome(
            False, "blocked", "Select open edges to fill.", context="edge"
        )
    open_selected = tuple(edge for edge in selected if edge in set(open_edges))
    if not open_selected:
        return SwitchOutcome(
            False,
            "blocked",
            "The selected edges are closed; fill only caps open border edges.",
            context="edge",
        )
    filtered = len(open_selected) != len(selected)
    if not _set_edge_selection(runtime, prepared.node, open_selected):
        return SwitchOutcome(
            False, "error", "The open-edge selection could not be narrowed.", context="edge"
        )

    narrowed = PreparedTarget(
        ok=True,
        status="prepared",
        tier=prepared.tier,
        reason="",
        node=prepared.node,
        base=prepared.base,
        ep=prepared.ep,
        node_token=prepared.node_token,
        base_token=prepared.base_token,
        context="edge",
        raw_level=prepared.raw_level,
        indices=open_selected,
        selection_count=len(open_selected),
        geometry=prepared.geometry,
        settings_token=prepared.settings_token,
        stack_after=prepared.stack_after,
        show_end_result=prepared.show_end_result,
        task_mode=prepared.task_mode,
    )
    outcome = switch_prepared_context(
        runtime, narrowed, "border", convert_selection=True
    )
    if not outcome.ok:
        return outcome
    if not outcome.indices:
        return SwitchOutcome(
            False,
            "blocked",
            "Selection conversion produced no open border; fill was not launched.",
            context="border",
        )
    message = (
        "Converted selected open edges to a Border selection; the closed edges "
        "were filtered out. No geometry changed yet."
        if filtered
        else "Converted selected open edges to a Border selection; no geometry changed yet."
    )
    return SwitchOutcome(
        True,
        "converted",
        message,
        context="border",
        raw_level=outcome.raw_level,
        indices=outcome.indices,
        selection_count=outcome.selection_count,
        converted=True,
    )


__all__ = [
    "COLLAPSE_REMEDY",
    "CONTRACT_SOURCE",
    "CONVERT_REASON",
    "EDIT_POLY_REASON",
    "ExecutionDescriptor",
    "HOLD_REASON",
    "INSTANCE_REASON",
    "LEVEL_V2_CONTEXT",
    "ManeuverAtlas",
    "ManeuverContext",
    "ManeuverRequest",
    "ManeuverSnapshot",
    "ManeuverSpec",
    "PREP_TIERS",
    "PreparedTarget",
    "PreparationError",
    "SHOW_END_RESULT_REASON",
    "SwitchOutcome",
    "TargetAssessment",
    "V2_CONTEXTS",
    "V2_CONVERT_ENUM",
    "V2_LEGACY_LEVEL",
    "V2_LEVEL_INDEX",
    "collapse_to_poly",
    "context_value",
    "find_edit_poly",
    "from_legacy_context",
    "inspect_target",
    "make_context",
    "pivot_open_edges_to_border",
    "prepare_target",
    "stack_identity",
    "switch_prepared_context",
    "to_legacy_context",
]

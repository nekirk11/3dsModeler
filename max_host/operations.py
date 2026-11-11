"""Trusted native Editable Poly mappings for the Max 2025 prototype.

This module is deliberately narrow.  It is called by :mod:`max_host.adapter`
only after the adapter has revalidated the exact target, level, component
selection, geometry fingerprint, and command-mode state.  It contains no Qt,
menu lifecycle, controller, or profile code, and it never dispatches an
untrusted action name or script string.

Every operation below maps to a documented 2025 MAXScript/pymxs member:

* ``polyop.extrudeFaces`` / ``bevelFaces`` (Editable_Poly Extrude/Bevel docs)
* ``EditablePoly.buttonOp #Inset`` / ``#GrowSelection`` / ``#ShrinkSelection``
* ``EditablePoly.chamferVertices`` / ``chamferEdges``
* ``EditablePoly.ConnectVertices`` / ``ConnectEdges`` and their edge settings
* ``EditablePoly.ReadyToBridgeFlagged`` + bridge button enums
* ``polyop.capHolesByEdge`` on selected open edges
* ``polyop.weldVertsByThreshold`` with the documented border-vertex restriction
* ``polyop.collapseVerts`` / ``collapseEdges`` / ``collapseFaces``
* ``polyop.deleteVerts`` / ``deleteEdges`` / ``deleteFaces`` / ``deleteIsoVerts``
* ``EditablePoly.Remove``, ``splitEdges``, ``breakVerts``, ``MakePlanarIn``,
  ``flipNormals``, ``SelectBorder``, ``SelectElement``

Temporary operation properties (extrusion/inset/connect/bridge/weld settings)
are written and restored inside the caller's single undo chunk.  A boolean
``false`` return or an unchanged result is never converted into ``applied``.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple

from ..core.contracts import ContextLevel
from ..core.parameters import ParameterError, validate_parameters
from .transactions import TransactionOutcome, hold_reason, run_undoable

MAX_MUTABLE_VERTS = 20_000
MAX_MUTABLE_FACES = 20_000
MAX_MUTABLE_EDGES = 60_000

_EXTRUSION_TYPES = {"group": 0, "local_normal": 1, "by_polygon": 2}
_INSET_TYPES = {"group": 0, "by_polygon": 1}

#: Read-only properties included in the stale-state fingerprint.  All are
#: documented Editable_Poly properties (soft selection and constraints).
_FINGERPRINT_PROPERTIES = (
    "useSoftSel",
    "ssUseEdgeDist",
    "ssEdgeDist",
    "affectBackfacing",
    "falloff",
    "pinch",
    "bubble",
    "lockSoftSel",
    "constrainType",
    "preserveUVs",
)

#: Documented Editable Poly object properties temporarily written by native
#: operations.  They belong to the node/base object, not to the EditablePoly
#: method interface.
_OPERATION_PROPERTIES = (
    "extrusionType",
    "insetAmount",
    "insetType",
    "connectEdgeSegments",
    "connectEdgePinch",
    "connectEdgeSlide",
    "bridgeSelected",
    "bridgeSegments",
    "bridgeTaper",
    "bridgeBias",
    "bridgeTwist1",
    "bridgeTwist2",
    "weldThreshold",
)

_COERCION_HELPER = "(fn (b) (b as array))"


class OperationError(RuntimeError):
    """A documented precondition, native call, or postcondition failed."""


class GeometryReadError(OperationError):
    """Mesh geometry could not be read or exceeds the supported limit."""

    def __init__(self, reason: str, over_limit: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.over_limit = over_limit


@dataclass(frozen=True)
class GeometryInfo:
    verts: int
    edges: int
    faces: int
    digest: str
    elapsed: float = 0.0

    @property
    def counts(self) -> Tuple[int, int, int]:
        return (self.verts, self.edges, self.faces)


@dataclass(frozen=True)
class OperationOutcome:
    """Internal outcome; the adapter converts it to a ``LaunchResult``."""

    status: str
    message: str

    def __post_init__(self) -> None:
        if self.status not in ("applied", "noop"):
            raise ValueError(f"Unsupported operation status: {self.status!r}")


@dataclass(frozen=True)
class MutableTarget:
    """Exact target information established by the adapter for one dispatch."""

    node: Any
    ep: Any
    level: ContextLevel
    raw_level: int
    indices: Tuple[int, ...]
    geometry: GeometryInfo
    settings_token: str
    target_token: str = ""
    node_token: str = ""
    base_token: str = ""


def _is_undefined(value: Any) -> bool:
    if value is None:
        return True
    try:
        return str(value).strip().lower() in {"undefined", "none"}
    except Exception:
        return False


def _polyop(runtime: Any) -> Any:
    polyop = getattr(runtime, "polyop", None)
    if polyop is None or _is_undefined(polyop):
        raise OperationError("polyop is unavailable in this host.")
    return polyop


def _editable_poly(node: Any) -> Any:
    ep = getattr(node, "EditablePoly", None)
    if ep is None or _is_undefined(ep):
        raise OperationError("The target does not expose the EditablePoly interface.")
    return ep


def _property_owner(node: Any) -> Any:
    """Return the node/base object that owns documented Editable Poly properties."""

    try:
        base = getattr(node, "baseObject", None)
    except Exception:
        base = None
    if base is not None and not _is_undefined(base):
        return base
    return node


def _native_array(runtime: Any, values: Sequence[int]) -> Any:
    """Build the documented pymxs/MAXScript Array for native component args."""

    factory = getattr(runtime, "Array", None)
    if not callable(factory):
        raise OperationError(
            "pymxs.runtime.Array is unavailable; component indices cannot be "
            "passed to the native method safely."
        )
    return factory(*tuple(int(value) for value in values))


def _name(runtime: Any, text: str) -> Any:
    factory = getattr(runtime, "Name", None)
    if not callable(factory):
        raise OperationError("pymxs.runtime.Name is unavailable; enum arguments "
                             "cannot be constructed safely.")
    return factory(text)


def _as_indices(value: Any) -> Optional[Tuple[int, ...]]:
    if value is None or _is_undefined(value):
        return ()
    try:
        indices = []
        for item in value:
            if isinstance(item, bool):
                return None
            indices.append(int(item))
    except Exception:
        return None
    if any(index < 1 for index in indices):
        return None
    return tuple(sorted(set(indices)))


def _as_ordered_indices(value: Any) -> Optional[Tuple[int, ...]]:
    """Preserve native order for geometry fingerprints (winding matters)."""

    if value is None or _is_undefined(value):
        return ()
    try:
        indices = []
        for item in value:
            if isinstance(item, bool):
                return None
            indices.append(int(item))
    except Exception:
        return None
    if any(index < 1 for index in indices):
        return None
    return tuple(indices)


def _coercion_helper(runtime: Any) -> Optional[Any]:
    try:
        helper = runtime.execute(_COERCION_HELPER)
    except Exception:
        return None
    return helper if callable(helper) else None


def bitarray_indices(bitarray: Any, runtime: Any = None) -> Optional[Tuple[int, ...]]:
    """Return sorted 1-based set indices, or ``None`` when unreadable.

    Iteration is the documented BitArray sequence behavior.  If a wrapper does
    not iterate, the fixed MAXScript coercion helper ``(b as array)`` is used
    through a local function value; no selection data is interpolated into a
    script string.
    """

    if bitarray is None or _is_undefined(bitarray):
        return ()
    try:
        if bool(getattr(bitarray, "isEmpty")):
            return ()
    except Exception:
        pass
    indices = _as_indices(bitarray)
    if indices is not None:
        return indices
    if runtime is not None:
        helper = _coercion_helper(runtime)
        if helper is not None:
            try:
                return _as_indices(helper(bitarray))
            except Exception:
                return None
    return None


def bitarray_count(bitarray: Any) -> Optional[int]:
    """Return the documented ``numberSet`` count, never the bit capacity."""

    if bitarray is None or _is_undefined(bitarray):
        return 0
    for attribute in ("numberSet", "numberset"):
        try:
            value = getattr(bitarray, attribute)
        except Exception:
            continue
        if value is None or _is_undefined(value) or isinstance(value, bool):
            continue
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            continue
    try:
        return max(0, len(bitarray))
    except Exception:
        return None


def read_selection_indices(
    runtime: Any, level: ContextLevel, node: Any
) -> Optional[Tuple[int, ...]]:
    """Read the exact 1-based component selection for ``level``."""

    polyop = _polyop(runtime)
    try:
        if level == ContextLevel.VERTEX:
            bitarray = polyop.getVertSelection(node)
        elif level in (ContextLevel.EDGE, ContextLevel.BORDER):
            bitarray = polyop.getEdgeSelection(node)
        elif level in (ContextLevel.POLYGON, ContextLevel.ELEMENT):
            bitarray = polyop.getFaceSelection(node)
        else:
            return ()
    except Exception as exc:
        raise OperationError(
            "Component selection could not be read (%s): %s"
            % (type(exc).__name__, exc)
        ) from exc
    indices = bitarray_indices(bitarray, runtime)
    if indices is None:
        raise OperationError(
            "Component selection indices could not be coerced; topology is "
            "disabled for this selection."
        )
    return indices


def _update(hasher: Any, *parts: Any) -> None:
    for part in parts:
        hasher.update(str(part).encode("utf-8", "backslashreplace"))
        hasher.update(b"\x00")


def read_geometry(runtime: Any, node: Any) -> GeometryInfo:
    """Read a bounded, ordered geometry fingerprint without mutating the mesh."""

    started = time.perf_counter()
    polyop = _polyop(runtime)
    try:
        verts = int(polyop.getNumVerts(node))
        edges = int(polyop.getNumEdges(node))
        faces = int(polyop.getNumFaces(node))
    except Exception as exc:
        raise GeometryReadError(
            "Mesh topology counts could not be read (%s): %s"
            % (type(exc).__name__, exc)
        ) from exc
    if min(verts, edges, faces) < 0:
        raise GeometryReadError("Mesh topology counts were invalid.")
    if (
        verts > MAX_MUTABLE_VERTS
        or faces > MAX_MUTABLE_FACES
        or edges > MAX_MUTABLE_EDGES
    ):
        raise GeometryReadError(
            "Mesh exceeds the supported mutable limit "
            "(%d vertices / %d faces / %d edges); topology is disabled."
            % (MAX_MUTABLE_VERTS, MAX_MUTABLE_FACES, MAX_MUTABLE_EDGES),
            over_limit=True,
        )

    hasher = hashlib.sha256()
    _update(hasher, "counts", verts, edges, faces)
    try:
        for index in range(1, verts + 1):
            point = polyop.getVert(node, index)
            if _is_undefined(point):
                raise GeometryReadError(
                    "Vertex %d position could not be read." % index
                )
            _update(hasher, "v", index, point)
        for index in range(1, edges + 1):
            endpoints = _as_ordered_indices(polyop.getEdgeVerts(node, index))
            if endpoints is None or len(endpoints) != 2:
                raise GeometryReadError(
                    "Edge %d endpoints could not be read." % index
                )
            _update(hasher, "e", index, endpoints)
        for index in range(1, faces + 1):
            loop = _as_ordered_indices(polyop.getFaceVerts(node, index))
            if loop is None or len(loop) < 3:
                raise GeometryReadError(
                    "Face %d vertex loop could not be read." % index
                )
            _update(hasher, "f", index, loop)
    except GeometryReadError:
        raise
    except Exception as exc:
        raise GeometryReadError(
            "Mesh geometry could not be read (%s): %s"
            % (type(exc).__name__, exc)
        ) from exc

    return GeometryInfo(
        verts=verts,
        edges=edges,
        faces=faces,
        digest=hasher.hexdigest(),
        elapsed=time.perf_counter() - started,
    )


def read_settings(node: Any) -> Tuple[str, bool]:
    """Fingerprint documented soft-selection, constraint, and operation settings.

    Properties are read from the node/base object; the EditablePoly interface is
    reserved for methods.  An unreadable property returns ``False`` so the
    adapter can fail closed instead of claiming a verified rollback.
    """

    owner = _property_owner(node)
    if owner is None or _is_undefined(owner):
        return ("unreadable:property-owner", False)
    parts = []
    for name in _FINGERPRINT_PROPERTIES + _OPERATION_PROPERTIES:
        try:
            value = getattr(owner, name)
        except Exception:
            return ("unreadable:%s" % name, False)
        if _is_undefined(value):
            return ("unreadable:%s" % name, False)
        parts.append("%s=%s" % (name, value))
    return ("|".join(parts), True)


def _required_anim_handle(runtime: Any, value: Any, label: str) -> str:
    """Return the documented stable anim handle or fail closed.

    Same identity query as the adapter (``runtime.getHandleByAnim``).  The
    ``.handle`` node property is not used as a fallback, especially for base
    objects that do not expose it; an absent, throwing, or invalid query is an
    unreadable fingerprint component.
    """

    if value is None or _is_undefined(value):
        raise OperationError("Pre/post %s is unavailable for handle identity." % label)
    getter = getattr(runtime, "getHandleByAnim", None)
    if not callable(getter):
        raise OperationError(
            "Pre/post %s anim handle is unreadable: runtime.getHandleByAnim is "
            "unavailable." % label
        )
    try:
        handle = getter(value)
    except Exception as exc:
        raise OperationError(
            "Pre/post %s anim handle is unreadable (%s): %s"
            % (label, type(exc).__name__, exc)
        ) from exc
    if isinstance(handle, bool) or handle is None or _is_undefined(handle):
        raise OperationError("Pre/post %s anim handle is unavailable." % label)
    try:
        text = str(handle).strip()
    except Exception as exc:
        raise OperationError(
            "Pre/post %s anim handle is unreadable (%s): %s"
            % (label, type(exc).__name__, exc)
        ) from exc
    try:
        int(text)
    except (TypeError, ValueError) as exc:
        raise OperationError(
            "Pre/post %s anim handle is not a valid integer handle." % label
        ) from exc
    if not text or text.lower() == "undefined":
        raise OperationError("Pre/post %s anim handle is unavailable." % label)
    return "h:%s" % text


def target_fingerprint(runtime: Any, target: MutableTarget) -> str:
    """Full pre/post state used to judge rollback after a failed undo chunk.

    Every component must be readable and the settings fingerprint must come
    from the node/base object that owns the documented properties.  An
    unreadable component raises :class:`OperationError` instead of contributing
    an ``unreadable:*`` token that could match on both sides and falsely verify
    a rollback.
    """

    geometry = read_geometry(runtime, target.node)
    indices = read_selection_indices(runtime, target.level, target.node)
    settings, readable = read_settings(target.node)
    if not readable:
        raise OperationError(
            "Pre/post operation settings are unreadable (%s); rollback cannot "
            "be verified." % settings
        )
    try:
        transform_value = target.node.transform
    except Exception as exc:
        raise OperationError(
            "Pre/post transform is unreadable (%s): %s"
            % (type(exc).__name__, exc)
        ) from exc
    if transform_value is None or _is_undefined(transform_value):
        raise OperationError("Pre/post transform is unreadable.")
    try:
        base_object = target.node.baseObject
    except Exception as exc:
        raise OperationError(
            "Pre/post base object is unreadable (%s): %s"
            % (type(exc).__name__, exc)
        ) from exc
    if base_object is None or _is_undefined(base_object):
        raise OperationError("Pre/post base object is unreadable.")
    node_handle = _required_anim_handle(runtime, target.node, "node")
    base_handle = _required_anim_handle(runtime, base_object, "base object")
    try:
        raw_time = getattr(runtime, "currentTime")
    except Exception as exc:
        raise OperationError(
            "Pre/post current time is unreadable (%s): %s"
            % (type(exc).__name__, exc)
        ) from exc
    if isinstance(raw_time, bool) or _is_undefined(raw_time):
        raise OperationError("Pre/post current time is unreadable.")
    try:
        current_time = str(raw_time).strip()
    except Exception as exc:
        raise OperationError(
            "Pre/post current time is unreadable (%s): %s"
            % (type(exc).__name__, exc)
        ) from exc
    if not current_time or current_time.lower() == "time:unreadable":
        raise OperationError("Pre/post current time is unreadable.")
    hasher = hashlib.sha256()
    _update(
        hasher,
        target.target_token,
        target.node_token,
        target.base_token,
        node_handle,
        base_handle,
        target.level.value,
        indices,
        geometry.digest,
        geometry.counts,
        settings,
        readable,
        transform_value,
        current_time,
    )
    return hasher.hexdigest()


def _require_indices(indices: Sequence[int], minimum: int = 1, noun: str = "components") -> Tuple[int, ...]:
    if isinstance(indices, bool) or not isinstance(indices, (tuple, list)):
        raise OperationError("Validated %s are required." % noun)
    cleaned = []
    for index in indices:
        if isinstance(index, bool) or not isinstance(index, int) or index < 1:
            raise OperationError("Component indices must be positive integers.")
        cleaned.append(index)
    cleaned = tuple(sorted(set(cleaned)))
    if len(cleaned) < minimum:
        raise OperationError("Select at least %d %s first." % (minimum, noun))
    return cleaned


def _selection_now(runtime: Any, level: ContextLevel, node: Any) -> Tuple[int, ...]:
    indices = read_selection_indices(runtime, level, node)
    return indices or ()


def _finish_selection(
    runtime: Any,
    level: ContextLevel,
    node: Any,
    before: Tuple[int, ...],
    label: str,
) -> OperationOutcome:
    after = _selection_now(runtime, level, node)
    if after == before:
        return OperationOutcome(
            "noop", "%s did not change the component selection." % label
        )
    return OperationOutcome(
        "applied",
        "%s applied (%d -> %d selected components)."
        % (label, len(before), len(after)),
    )


def _finish_geometry(runtime: Any, node: Any, before: GeometryInfo, label: str) -> OperationOutcome:
    after = read_geometry(runtime, node)
    if after.counts == before.counts and after.digest == before.digest:
        return OperationOutcome("noop", "%s did not change the mesh." % label)
    return OperationOutcome(
        "applied",
        "%s applied (%d/%d/%d -> %d/%d/%d vertices/edges/faces)."
        % (
            label,
            before.verts,
            before.edges,
            before.faces,
            after.verts,
            after.edges,
            after.faces,
        ),
    )


def _apply_settings(node: Any, settings: Sequence[Tuple[str, Any]]) -> list:
    owner = _property_owner(node)
    previous = []
    try:
        for name, value in settings:
            old = getattr(owner, name)
            if _is_undefined(old):
                raise OperationError("Editable Poly property %s is unavailable." % name)
            previous.append((name, old))
            setattr(owner, name, value)
    except BaseException:
        _restore_settings(node, previous, suppress=True)
        raise
    return previous


def _restore_settings(node: Any, previous: Sequence[Tuple[str, Any]], suppress: bool = False) -> None:
    owner = _property_owner(node)
    errors = []
    for name, value in reversed(list(previous)):
        try:
            setattr(owner, name, value)
        except Exception as exc:
            errors.append("%s (%s)" % (name, exc))
    if errors and not suppress:
        raise OperationError(
            "Could not restore Editable Poly setting(s): " + "; ".join(errors)
        )


def _require_true(value: Any, label: str) -> None:
    if not value:
        raise OperationError("%s reported failure." % label)


def _open_edges(runtime: Any, node: Any) -> Tuple[int, ...]:
    polyop = _polyop(runtime)
    try:
        bitarray = polyop.getOpenEdges(node)
    except Exception as exc:
        raise OperationError(
            "Open edges could not be read (%s): %s" % (type(exc).__name__, exc)
        ) from exc
    edges = bitarray_indices(bitarray, runtime)
    if edges is None:
        raise OperationError("Open-edge indices could not be coerced.")
    return edges


def _verts_using_edges(runtime: Any, node: Any, edges: Sequence[int]) -> Tuple[int, ...]:
    polyop = _polyop(runtime)
    try:
        bitarray = polyop.getVertsUsingEdge(node, _native_array(runtime, edges))
    except Exception as exc:
        raise OperationError(
            "Border vertices could not be read (%s): %s"
            % (type(exc).__name__, exc)
        ) from exc
    verts = bitarray_indices(bitarray, runtime)
    if verts is None:
        raise OperationError("Border-vertex indices could not be coerced.")
    return verts


def _op_grow(runtime, node, ep, level, indices, params):
    before = _selection_now(runtime, level, node)
    ep.buttonOp(_name(runtime, "GrowSelection"))
    return _finish_selection(runtime, level, node, before, "Grow selection")


def _op_shrink(runtime, node, ep, level, indices, params):
    before = _selection_now(runtime, level, node)
    ep.buttonOp(_name(runtime, "ShrinkSelection"))
    return _finish_selection(runtime, level, node, before, "Shrink selection")


def _op_loop(runtime, node, ep, level, indices, params):
    before = _selection_now(runtime, level, node)
    ep.SelectEdgeLoop()
    return _finish_selection(runtime, level, node, before, "Select edge loop")


def _op_ring(runtime, node, ep, level, indices, params):
    before = _selection_now(runtime, level, node)
    ep.SelectEdgeRing()
    return _finish_selection(runtime, level, node, before, "Select edge ring")


def _op_clear(runtime, node, ep, level, indices, params):
    polyop = _polyop(runtime)
    if not indices:
        return OperationOutcome("noop", "Selection is already empty.")
    none = _name(runtime, "none")
    if level == ContextLevel.VERTEX:
        polyop.setVertSelection(node, none)
    elif level in (ContextLevel.EDGE, ContextLevel.BORDER):
        polyop.setEdgeSelection(node, none)
    elif level in (ContextLevel.POLYGON, ContextLevel.ELEMENT):
        polyop.setFaceSelection(node, none)
    else:
        raise OperationError("Clearing selection is not supported at object level.")
    return _finish_selection(runtime, level, node, tuple(indices), "Clear selection")


def _op_extrude(runtime, node, ep, level, indices, params):
    amount = float(params["height"])
    if amount == 0:
        return OperationOutcome(
            "noop", "Extrude height is zero; no geometry change was requested."
        )
    polyop = _polyop(runtime)
    before = read_geometry(runtime, node)
    previous = _apply_settings(
        node, (("extrusionType", _EXTRUSION_TYPES[params["extrusion_type"]]),)
    )
    try:
        polyop.extrudeFaces(node, _native_array(runtime, indices), amount)
    finally:
        _restore_settings(node, previous)
    return _finish_geometry(runtime, node, before, "Extrude")


def _op_bevel(runtime, node, ep, level, indices, params):
    height = float(params["height"])
    outline = float(params["outline"])
    if height == 0 and outline == 0:
        return OperationOutcome(
            "noop", "Bevel height and outline are zero; no geometry change was requested."
        )
    polyop = _polyop(runtime)
    before = read_geometry(runtime, node)
    previous = _apply_settings(
        node, (("extrusionType", _EXTRUSION_TYPES[params["extrusion_type"]]),)
    )
    try:
        polyop.bevelFaces(node, _native_array(runtime, indices), height, outline)
    finally:
        _restore_settings(node, previous)
    return _finish_geometry(runtime, node, before, "Bevel")


def _op_inset(runtime, node, ep, level, indices, params):
    before = read_geometry(runtime, node)
    previous = _apply_settings(
        node,
        (
            ("insetAmount", float(params["amount"])),
            ("insetType", _INSET_TYPES[params["inset_type"]]),
        ),
    )
    try:
        ep.buttonOp(_name(runtime, "Inset"))
    finally:
        _restore_settings(node, previous)
    return _finish_geometry(runtime, node, before, "Inset")


def _op_chamfer(runtime, node, ep, level, indices, params):
    amount = float(params["amount"])
    before = read_geometry(runtime, node)
    if level == ContextLevel.VERTEX:
        ep.chamferVertices(amount, open=bool(params["open"]))
        label = "Vertex chamfer"
    elif level == ContextLevel.EDGE:
        ep.chamferEdges(amount, open=bool(params["open"]))
        label = "Edge chamfer"
    else:
        raise OperationError("Chamfer requires a vertex or edge selection.")
    return _finish_geometry(runtime, node, before, label)


def _op_cap(runtime, node, ep, level, indices, params):
    open_edges = set(_open_edges(runtime, node))
    selected = set(indices)
    if not selected:
        raise OperationError("Select border edges to cap.")
    if not selected.issubset(open_edges):
        raise OperationError(
            "Cap requires selected open border edges only; unrelated holes are "
            "not capped."
        )
    polyop = _polyop(runtime)
    before = read_geometry(runtime, node)
    _require_true(
        polyop.capHolesByEdge(node, _native_array(runtime, indices)),
        "Cap selected border",
    )
    return _finish_geometry(runtime, node, before, "Cap selected border")


def _op_connect(runtime, node, ep, level, indices, params):
    before = read_geometry(runtime, node)
    if level == ContextLevel.VERTEX:
        _require_true(ep.ConnectVertices(vertexFlag=1), "Connect vertices")
        label = "Connect vertices"
    elif level == ContextLevel.EDGE:
        previous = _apply_settings(
            node,
            (
                ("connectEdgeSegments", int(params["segments"])),
                ("connectEdgePinch", int(params["pinch"])),
                ("connectEdgeSlide", int(params["slide"])),
            ),
        )
        try:
            _require_true(ep.ConnectEdges(edgeFlag=1), "Connect edges")
        finally:
            _restore_settings(node, previous)
        label = "Connect edges"
    else:
        raise OperationError("Connect requires a vertex or edge selection.")
    return _finish_geometry(runtime, node, before, label)


def _validate_bridge_border(runtime: Any, node: Any, indices: Sequence[int]) -> None:
    polyop = _polyop(runtime)
    selected = set(indices)
    open_edges = set(_open_edges(runtime, node))
    if not selected or not selected.issubset(open_edges):
        raise OperationError(
            "Border bridge requires selected open border edges only."
        )
    loops = []
    for edge in sorted(selected):
        try:
            bitarray = polyop.getBorderFromEdge(node, edge)
        except Exception as exc:
            raise OperationError(
                "Border loop could not be read (%s): %s"
                % (type(exc).__name__, exc)
            ) from exc
        loop = bitarray_indices(bitarray, runtime)
        if not loop:
            raise OperationError("A selected edge is not part of an open border loop.")
        loop_set = frozenset(loop)
        if loop_set not in loops:
            loops.append(loop_set)
    if len(loops) != 2:
        raise OperationError(
            "Border bridge requires exactly two complete, disjoint open loops."
        )
    if loops[0] & loops[1]:
        raise OperationError("Border bridge loops must be disjoint.")
    if loops[0] | loops[1] != selected:
        raise OperationError(
            "Border bridge requires the two complete open loops to be selected."
        )


def _op_bridge(runtime, node, ep, level, indices, params):
    if level == ContextLevel.BORDER:
        _validate_bridge_border(runtime, node, indices)
        sel_level = "Border"
        button = "BridgeBorder"
    elif level == ContextLevel.POLYGON:
        if len(indices) != 2:
            raise OperationError("Polygon bridge requires exactly two selected faces.")
        sel_level = "Face"
        button = "BridgePolygon"
    elif level == ContextLevel.EDGE:
        if len(indices) < 2:
            raise OperationError("Edge bridge requires at least two selected edges.")
        sel_level = "Edge"
        button = "BridgeEdge"
    else:
        raise OperationError("Bridge requires an edge, border, or polygon selection.")

    _require_true(
        ep.ReadyToBridgeFlagged(selLevel=_name(runtime, sel_level), flag=1),
        "Bridge readiness check",
    )
    before = read_geometry(runtime, node)
    # Autodesk documents bridgeTwist1/bridgeTwist2 as Integer properties.
    twist = int(params["twist"])
    previous = _apply_settings(
        node,
        (
            ("bridgeSelected", 1),
            ("bridgeSegments", int(params["segments"])),
            ("bridgeTaper", float(params["taper"])),
            ("bridgeBias", float(params["bias"])),
            ("bridgeTwist1", twist),
            ("bridgeTwist2", twist),
        ),
    )
    try:
        ep.buttonOp(_name(runtime, button))
    finally:
        _restore_settings(node, previous)
    return _finish_geometry(runtime, node, before, "Bridge")


def _op_weld(runtime, node, ep, level, indices, params):
    if level != ContextLevel.VERTEX:
        raise OperationError("Threshold weld requires a vertex selection.")
    selected = _require_indices(indices, 2, "vertices")
    open_edges = _open_edges(runtime, node)
    border_verts = set(_verts_using_edges(runtime, node, open_edges))
    if not set(selected).issubset(border_verts):
        raise OperationError(
            "Threshold weld is documented for border vertices only."
        )
    polyop = _polyop(runtime)
    before = read_geometry(runtime, node)
    previous = _apply_settings(
        node, (("weldThreshold", float(params["threshold"])),)
    )
    try:
        polyop.weldVertsByThreshold(node, _native_array(runtime, selected))
    finally:
        _restore_settings(node, previous)
    return _finish_geometry(runtime, node, before, "Threshold weld")


def _op_collapse(runtime, node, ep, level, indices, params):
    polyop = _polyop(runtime)
    before = read_geometry(runtime, node)
    if level == ContextLevel.VERTEX:
        polyop.collapseVerts(node, _native_array(runtime, indices))
        label = "Collapse vertices"
    elif level == ContextLevel.EDGE:
        polyop.collapseEdges(node, _native_array(runtime, indices))
        label = "Collapse edges"
    elif level == ContextLevel.POLYGON:
        polyop.collapseFaces(node, _native_array(runtime, indices))
        label = "Collapse faces"
    else:
        raise OperationError("Component collapse requires vertex, edge, or polygon.")
    return _finish_geometry(runtime, node, before, label)


def _op_delete(runtime, node, ep, level, indices, params):
    polyop = _polyop(runtime)
    before = read_geometry(runtime, node)
    if level == ContextLevel.VERTEX:
        polyop.deleteVerts(node, _native_array(runtime, indices))
        if bool(params.get("cleanup", True)):
            polyop.deleteIsoVerts(node)
        label = "Delete vertices"
    elif level in (ContextLevel.EDGE, ContextLevel.BORDER):
        polyop.deleteEdges(node, _native_array(runtime, indices), delIsoVerts=True)
        label = "Delete edges"
    elif level in (ContextLevel.POLYGON, ContextLevel.ELEMENT):
        polyop.deleteFaces(node, _native_array(runtime, indices), delIsoVerts=True)
        label = "Delete faces"
    else:
        raise OperationError("Delete requires a sub-object selection.")
    return _finish_geometry(runtime, node, before, label)


def _op_remove(runtime, node, ep, level, indices, params):
    before = read_geometry(runtime, node)
    if level == ContextLevel.VERTEX:
        _require_true(
            ep.Remove(selLevel=_name(runtime, "Vertex"), flag=1), "Remove vertices"
        )
        label = "Remove vertices"
    elif level == ContextLevel.EDGE:
        _require_true(
            ep.Remove(selLevel=_name(runtime, "Edge"), flag=1), "Remove edges"
        )
        label = "Remove edges"
    else:
        raise OperationError("Remove requires a vertex or edge selection.")
    return _finish_geometry(runtime, node, before, label)


def _op_split(runtime, node, ep, level, indices, params):
    before = read_geometry(runtime, node)
    _require_true(ep.splitEdges(edgeFlags=1), "Split edges")
    return _finish_geometry(runtime, node, before, "Split edges")


def _op_break(runtime, node, ep, level, indices, params):
    before = read_geometry(runtime, node)
    _require_true(ep.breakVerts(1), "Break vertices")
    return _finish_geometry(runtime, node, before, "Break vertices")


def _op_planar(axis: str):
    def handler(runtime, node, ep, level, indices, params):
        before = read_geometry(runtime, node)
        _require_true(
            ep.MakePlanarIn(
                _name(runtime, axis),
                selLevel=_name(runtime, "CurrentLevel"),
                flag=1,
            ),
            "Make planar %s" % axis,
        )
        return _finish_geometry(runtime, node, before, "Make planar %s" % axis)

    return handler


def _op_flip(runtime, node, ep, level, indices, params):
    before = read_geometry(runtime, node)
    _require_true(ep.flipNormals(1), "Flip normals")
    return _finish_geometry(runtime, node, before, "Flip normals")


def _op_to_border(runtime, node, ep, level, indices, params):
    before = _selection_now(runtime, level, node)
    ep.SelectBorder()
    return _finish_selection(runtime, level, node, before, "Select border")


def _op_to_element(runtime, node, ep, level, indices, params):
    before = _selection_now(runtime, level, node)
    ep.SelectElement()
    return _finish_selection(runtime, level, node, before, "Select element")


_HANDLERS = {
    "selection.grow": _op_grow,
    "selection.shrink": _op_shrink,
    "selection.loop": _op_loop,
    "selection.ring": _op_ring,
    "selection.clear": _op_clear,
    "selection.to_border": _op_to_border,
    "selection.to_element": _op_to_element,
    "model.extrude": _op_extrude,
    "model.bevel": _op_bevel,
    "model.inset": _op_inset,
    "model.chamfer": _op_chamfer,
    "model.cap": _op_cap,
    "model.connect": _op_connect,
    "model.bridge": _op_bridge,
    "model.weld": _op_weld,
    "model.collapse": _op_collapse,
    "model.delete": _op_delete,
    "model.remove": _op_remove,
    "model.split_edges": _op_split,
    "model.break_vertices": _op_break,
    "model.planar_x": _op_planar("X"),
    "model.planar_y": _op_planar("Y"),
    "model.planar_z": _op_planar("Z"),
    "model.flip_normals": _op_flip,
}

#: Actions whose edits are wrapped in a single owned undo chunk.
MUTATION_ACTIONS = frozenset(_HANDLERS)

_MINIMUM_INDICES = {
    "selection.grow": 1,
    "selection.shrink": 1,
    "selection.loop": 1,
    "selection.ring": 1,
    "selection.clear": 0,
    "selection.to_border": 1,
    "selection.to_element": 1,
    "model.extrude": 1,
    "model.bevel": 1,
    "model.inset": 1,
    "model.chamfer": 1,
    "model.cap": 1,
    "model.connect": 2,
    "model.bridge": 2,
    "model.weld": 2,
    "model.collapse": 1,
    "model.delete": 1,
    "model.remove": 1,
    "model.split_edges": 1,
    "model.break_vertices": 1,
    "model.planar_x": 1,
    "model.planar_y": 1,
    "model.planar_z": 1,
    "model.flip_normals": 1,
}


def _point(value: Any) -> Tuple[float, float, float]:
    """Coerce a documented Point3/BigPoint value to three floats."""

    if value is None or _is_undefined(value):
        raise OperationError("A vertex position could not be read.")
    try:
        return (float(value.x), float(value.y), float(value.z))
    except Exception:
        pass
    try:
        items = tuple(value)
    except Exception:
        items = ()
    if len(items) >= 3:
        try:
            return (float(items[0]), float(items[1]), float(items[2]))
        except (TypeError, ValueError):
            pass
    try:
        text = str(value).strip().strip("()")
        parts = [part for part in text.replace(",", " ").split() if part]
        if len(parts) >= 3:
            return (float(parts[0]), float(parts[1]), float(parts[2]))
    except Exception:
        pass
    raise OperationError("A vertex position could not be coerced to three floats.")


def _vertex_positions(runtime: Any, node: Any) -> Tuple[Tuple[float, float, float], ...]:
    polyop = _polyop(runtime)
    try:
        count = int(polyop.getNumVerts(node))
    except Exception as exc:
        raise OperationError(
            "Vertex count could not be read (%s)." % type(exc).__name__
        ) from exc
    if count < 0:
        raise OperationError("Vertex count was invalid.")
    return tuple(_point(polyop.getVert(node, index)) for index in range(1, count + 1))


def _edges_using_vert(runtime: Any, node: Any, vertex: int) -> Optional[Tuple[int, ...]]:
    polyop = _polyop(runtime)
    try:
        bitarray = polyop.getEdgesUsingVert(node, int(vertex))
    except Exception:
        return None
    return bitarray_indices(bitarray, runtime)


def _edge_other_vertex(
    runtime: Any, node: Any, edge: int, vertex: int
) -> Optional[int]:
    polyop = _polyop(runtime)
    try:
        endpoints = _as_ordered_indices(polyop.getEdgeVerts(node, int(edge)))
    except Exception:
        return None
    if endpoints is None or len(endpoints) != 2:
        return None
    first, second = endpoints
    if first == vertex:
        return second
    if second == vertex:
        return first
    return None


def _clear_level_selection(runtime: Any, node: Any, level: str) -> None:
    polyop = _polyop(runtime)
    none = _name(runtime, "none")
    if level == "vertex":
        polyop.setVertSelection(node, none)
    else:
        polyop.setEdgeSelection(node, none)


def _set_vertex_selection(runtime: Any, node: Any, indices: Sequence[int]) -> None:
    polyop = _polyop(runtime)
    polyop.setVertSelection(node, _native_array(runtime, indices))


def _raw_level_for(runtime: Any, level: ContextLevel) -> int:
    try:
        value = getattr(runtime, "subObjectLevel")
        return int(value)
    except Exception:
        return {
            ContextLevel.OBJECT: 0,
            ContextLevel.VERTEX: 1,
            ContextLevel.EDGE: 2,
            ContextLevel.BORDER: 3,
            ContextLevel.POLYGON: 4,
            ContextLevel.ELEMENT: 5,
        }[level]


def _collinear_redundant(
    runtime: Any,
    node: Any,
    vertex: int,
    positions: Sequence[Tuple[float, float, float]],
    scale: float,
) -> bool:
    """True only for a valence-2, nondegenerate, straight inline vertex."""

    edges = _edges_using_vert(runtime, node, vertex)
    if edges is None or len(edges) != 2:
        return False
    if len(set(edges)) != 2:
        return False
    others = []
    for edge in edges:
        other = _edge_other_vertex(runtime, node, edge, vertex)
        if other is None or other == vertex:
            return False
        if not (1 <= other <= len(positions)):
            return False
        others.append(other)
    if others[0] == others[1]:
        return False
    origin = positions[vertex - 1]
    first = positions[others[0] - 1]
    second = positions[others[1] - 1]
    vector_a = tuple(first[i] - origin[i] for i in range(3))
    vector_b = tuple(second[i] - origin[i] for i in range(3))
    length_a = sum(component * component for component in vector_a) ** 0.5
    length_b = sum(component * component for component in vector_b) ** 0.5
    epsilon = 1e-9 * max(1.0, scale)
    if length_a <= epsilon or length_b <= epsilon:
        return False
    dot = sum(vector_a[i] * vector_b[i] for i in range(3))
    cosine = dot / (length_a * length_b)
    return cosine <= -1.0 + 1e-6


def clean_dissolve_edges(
    runtime: Any,
    node: Any,
    ep: Any,
    level: ContextLevel | str,
    indices: Sequence[int],
    *,
    undo_factory: Optional[Callable[[bool, str], Any]] = None,
    hold_guard: Optional[Callable[[], Optional[str]]] = None,
) -> TransactionOutcome:
    """Dissolve selected edges and clean only the proven straight endpoints.

    Both ``EditablePoly.Remove`` passes and the verified selection cleanup share
    exactly one owned undo chunk.  Vertex-slot identity is proven by comparing
    the full position prefix before/after edge removal; a renumbering, coincident
    candidate slots, ambiguous connectivity, or a failed restore rolls the whole
    edit back instead of using stale indices.  Global weld/isolated-vertex
    cleanup is never performed.
    """

    try:
        context = level if isinstance(level, ContextLevel) else ContextLevel(str(level))
    except ValueError as exc:
        return TransactionOutcome(
            "error", "Unsupported context for clean dissolve.", exception="ValueError"
        )
    if context != ContextLevel.EDGE:
        return TransactionOutcome(
            "error",
            "Clean dissolve is defined only for a selected edge context.",
            exception="UnsupportedContext",
        )
    if not callable(getattr(ep, "Remove", None)):
        return TransactionOutcome(
            "error",
            "EditablePoly.Remove is unavailable; no dissolve was attempted.",
            exception="MissingMember",
        )
    try:
        selected = _require_indices(indices, 1, "edges")
    except OperationError as exc:
        return TransactionOutcome("error", str(exc), exception="OperationError")

    def edit() -> OperationOutcome:
        before_positions = _vertex_positions(runtime, node)
        before_verts = len(before_positions)
        polyop = _polyop(runtime)
        try:
            before_edges = int(polyop.getNumEdges(node))
        except Exception as exc:
            raise OperationError(
                "Edge count could not be read (%s)." % type(exc).__name__
            ) from exc

        candidate_verts = _verts_using_edges(runtime, node, selected)
        if not candidate_verts:
            raise OperationError(
                "Selected edges have no readable endpoints; dissolve was not attempted."
            )
        rounded: Dict[Tuple[float, float, float], int] = {}
        for vertex in candidate_verts:
            if not (1 <= vertex <= before_verts):
                raise OperationError("A candidate vertex slot is outside the mesh.")
            key = tuple(round(value, 9) for value in before_positions[vertex - 1])
            if key in rounded and rounded[key] != vertex:
                raise OperationError(
                    "Coincident vertex slots cannot be proven distinct; dissolve "
                    "was rolled back rather than conflating them."
                )
            rounded[key] = vertex

        previous_level = _raw_level_for(runtime, context)
        scale = max(
            (abs(component) for point in before_positions for component in point),
            default=1.0,
        )

        _require_true(
            ep.Remove(selLevel=_name(runtime, "Edge"), flag=1),
            "Remove selected edges",
        )
        after_positions = _vertex_positions(runtime, node)
        after_verts = len(after_positions)
        try:
            after_edges = int(polyop.getNumEdges(node))
        except Exception as exc:
            raise OperationError(
                "Edge count could not be re-read (%s)." % type(exc).__name__
            ) from exc
        if before_edges - after_edges != len(selected):
            raise OperationError(
                "Edge removal did not remove exactly the selected edges; "
                "topology identity is ambiguous."
            )

        # Vertex-slot identity guard: every surviving slot must keep its exact
        # recorded position.  Any shift means the mesh was renumbered.
        for index in range(min(before_verts, after_verts)):
            if after_positions[index] != before_positions[index]:
                raise OperationError(
                    "Vertex slots were renumbered by edge removal; dissolve was "
                    "rolled back rather than using stale indices."
                )
        if after_verts < before_verts:
            missing = tuple(range(after_verts + 1, before_verts + 1))
            for vertex in missing:
                if vertex not in candidate_verts:
                    raise OperationError(
                        "Edge removal deleted a non-candidate vertex; dissolve was "
                        "rolled back."
                    )

        cleanup = tuple(
            sorted(
                vertex
                for vertex in candidate_verts
                if vertex <= after_verts
                and vertex <= len(after_positions)
                and _collinear_redundant(
                    runtime, node, vertex, after_positions, scale
                )
            )
        )

        if cleanup:
            try:
                runtime.subObjectLevel = 1
                if int(runtime.subObjectLevel) != 1:
                    raise OperationError(
                        "The temporary vertex context could not be established."
                    )
            except OperationError:
                raise
            except Exception as exc:
                raise OperationError(
                    "The temporary vertex context could not be established (%s)."
                    % type(exc).__name__
                ) from exc
            _set_vertex_selection(runtime, node, cleanup)
            _require_true(
                ep.Remove(selLevel=_name(runtime, "Vertex"), flag=1),
                "Remove redundant inline vertices",
            )
            cleaned_positions = _vertex_positions(runtime, node)
            removed = after_verts - len(cleaned_positions)
            if removed != len(cleanup):
                raise OperationError(
                    "Inline vertex cleanup did not remove exactly the verified "
                    "candidate set; dissolve was rolled back."
                )

        # Removed edge/vertex identities are never restored.  Face/element
        # storage was not touched, and the original raw level is restored.
        try:
            _clear_level_selection(runtime, node, "edge")
            _clear_level_selection(runtime, node, "vertex")
            runtime.subObjectLevel = previous_level
            if int(runtime.subObjectLevel) != previous_level:
                raise OperationError(
                    "The original sub-object context could not be restored."
                )
        except OperationError:
            raise
        except Exception as exc:
            raise OperationError(
                "Selection/context restoration failed (%s); dissolve was rolled back."
                % type(exc).__name__
            ) from exc

        if not cleanup:
            return OperationOutcome(
                "applied",
                "Dissolved %d selected edge(s); no redundant inline vertices "
                "required cleanup." % len(selected),
            )
        return OperationOutcome(
            "applied",
            "Dissolved %d selected edge(s) and removed %d verified inline "
            "vertex/vertices." % (len(selected), len(cleanup)),
        )

    return run_undoable(
        "3dsModeler V2: clean dissolve edges",
        edit,
        undo_factory,
        hold_guard=hold_guard or (lambda: hold_reason(runtime)),
    )


def execute_operation(
    runtime: Any,
    node: Any,
    action_id: str,
    level: ContextLevel | str,
    indices: Sequence[int],
    parameters: Optional[Mapping[str, Any]],
) -> OperationOutcome:
    """Execute one documented mutation from a fixed action-ID mapping.

    The adapter calls this only after exact-target and stale-geometry guards.
    It revalidates the frozen parameter schema here as a second pass, requires
    the adapter's exact indices, and raises on every failure so the caller's
    undo context rolls temporary property writes and partial edits back.
    """

    if not isinstance(action_id, str) or action_id not in _HANDLERS:
        raise OperationError("Action is not a trusted native operation.")
    try:
        context = level if isinstance(level, ContextLevel) else ContextLevel(level)
    except ValueError as exc:
        raise OperationError("Unsupported context level: %r" % (level,)) from exc
    try:
        validated = validate_parameters(action_id, context, parameters)
    except ParameterError as exc:
        raise OperationError("Invalid native parameters: %s" % exc) from exc

    minimum = _MINIMUM_INDICES[action_id]
    cleaned = _require_indices(indices, minimum)
    ep = _editable_poly(node)
    return _HANDLERS[action_id](runtime, node, ep, context, cleaned, validated)


__all__ = [
    "GeometryInfo",
    "GeometryReadError",
    "MAX_MUTABLE_EDGES",
    "MAX_MUTABLE_FACES",
    "MAX_MUTABLE_VERTS",
    "MUTATION_ACTIONS",
    "MutableTarget",
    "OperationError",
    "OperationOutcome",
    "bitarray_count",
    "bitarray_indices",
    "clean_dissolve_edges",
    "execute_operation",
    "read_geometry",
    "read_selection_indices",
    "read_settings",
    "target_fingerprint",
]

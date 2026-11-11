"""Allowlisted V2 native handler dispatch for 3ds Max 2025.

The registry below is the host half of the frozen descriptor contract.  Its key
sets must equal ``core.maneuver_contracts.TARGETS_BY_KIND`` exactly; a mismatch is
a configuration error and blocks every V2 dispatch rather than falling back to a
nearby tool.  Handlers are reviewed Python functions in this module.  Neither
``api`` nor ``doc_url`` is ever an input: a descriptor can only select a fixed
``(kind, target)`` pair that already exists here.

Caddy targets are stable semantic names; ``Weld`` and ``MSmooth`` map to the
documented ``#WeldSelected`` and ``#MeshSmooth`` popup enums.  Empty-selection
bridge command modes have no published Editable Poly 2025 launcher, so they are
explicitly blocked and remain an incomplete live requirement.  SwiftLoop uses
the documented Graphite ``PolyToolsPolyDraw.SwiftLoop()`` handler and records
its mode provenance instead of pretending it is an EditablePoly enum.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Sequence, Tuple

from ..core.contracts import ContextLevel
from .bindings import (
    MAX_MOVE_COMMAND,
    MAX_ROTATE_COMMAND,
    MAX_SCALE_COMMAND,
    MAX_ZOOM_EXTENTS_SELECTED_COMMAND,
)
from .operations import (
    OperationError,
    clean_dissolve_edges,
    read_geometry,
    read_selection_indices,
)
from .preparation import (
    HOLD_REASON,
    PreparedTarget,
    context_value,
    switch_prepared_context,
)
from .transactions import run_undoable

# ---------------------------------------------------------------------------
# Frozen registry
# ---------------------------------------------------------------------------

CADDY_TARGETS: FrozenSet[str] = frozenset(
    {
        "Chamfer",
        "Extrude",
        "Bevel",
        "Inset",
        "ConnectEdges",
        "BridgeEdge",
        "BridgeBorder",
        "BridgePolygon",
        "Weld",
        "Tessellate",
        "MSmooth",
    }
)

COMMAND_MODE_TARGETS: FrozenSet[str] = frozenset(
    {
        "ChamferVertex",
        "ChamferEdge",
        "ExtrudeVertex",
        "ExtrudeEdge",
        "ExtrudeFace",
        "Bevel",
        "InsetFace",
        "BridgeEdge",
        "BridgeBorder",
        "BridgePolygon",
        "Weld",
        "CutVertex",
        "CutEdge",
        "CutFace",
        "QuickSlice",
        "SwiftLoop",
    }
)

BUTTON_OP_TARGETS: FrozenSet[str] = frozenset(
    {
        "Delete",
        "Remove",
        "Cap",
        "Collapse",
        "SplitEdges",
        "BreakVertex",
        "SelectEdgeLoop",
        "SelectEdgeRing",
        "GrowSelection",
        "ShrinkSelection",
        "FlipNormals",
        "MakePlanarInX",
        "MakePlanarInY",
        "MakePlanarInZ",
    }
)

POLYOP_TARGETS: FrozenSet[str] = frozenset({"clear_selection"})

MAX_COMMAND_TARGETS: FrozenSet[str] = frozenset(
    {"move", "rotate", "scale", "frame_selection"}
)

COMPOSITE_TARGETS: FrozenSet[str] = frozenset(
    {"clean_dissolve_edges", "convert_to_poly", "collapse_to_poly"}
)

CONTEXT_SWITCH_TARGETS: FrozenSet[str] = frozenset(
    {"vertex", "edge", "border", "face"}
)

KIND_ORDER: Tuple[str, ...] = (
    "caddy",
    "command_mode",
    "button_op",
    "polyop",
    "max_command",
    "composite",
    "context_switch",
)

TARGETS_BY_KIND: Dict[str, FrozenSet[str]] = {
    "caddy": CADDY_TARGETS,
    "command_mode": COMMAND_MODE_TARGETS,
    "button_op": BUTTON_OP_TARGETS,
    "polyop": POLYOP_TARGETS,
    "max_command": MAX_COMMAND_TARGETS,
    "composite": COMPOSITE_TARGETS,
    "context_switch": CONTEXT_SWITCH_TARGETS,
}

#: Stable semantic caddy names -> documented ``EditablePoly.PopupDialog`` enums.
CADDY_DIALOG_MAP: Dict[str, str] = {
    "Chamfer": "Chamfer",
    "Extrude": "Extrude",
    "Bevel": "Bevel",
    "Inset": "Inset",
    "ConnectEdges": "ConnectEdges",
    "BridgeEdge": "BridgeEdge",
    "BridgeBorder": "BridgeBorder",
    "BridgePolygon": "BridgePolygon",
    "Weld": "WeldSelected",
    "Tessellate": "Tessellate",
    "MSmooth": "MeshSmooth",
}

MAX_COMMAND_MAP: Dict[str, str] = {
    "move": MAX_MOVE_COMMAND,
    "rotate": MAX_ROTATE_COMMAND,
    "scale": MAX_SCALE_COMMAND,
    "frame_selection": MAX_ZOOM_EXTENTS_SELECTED_COMMAND,
}

#: Blocked until a Max 2025 bridge command-mode launcher is verified.
BRIDGE_GATE_REASON = (
    "Bridge command mode has no verified Editable Poly 2025 launcher; this "
    "empty-selection route is explicitly blocked as an incomplete live "
    "requirement. No Edit Poly modifier or default bridge edit is substituted."
)
SWIFT_LOOP_MESSAGE = (
    "Entered the Graphite SwiftLoop tool. Its mode provenance is Graphite, not a "
    "published EditablePoly command-mode enum; exit/ownership behavior requires "
    "live 2025 validation, so further ambiguous chaining is blocked."
)
IDLE_EP_MODES: FrozenSet[str] = frozenset({"none", "-1", ""})

_SELECTION_OPS: FrozenSet[str] = frozenset(
    {"SelectEdgeLoop", "SelectEdgeRing", "GrowSelection", "ShrinkSelection"}
)
_GEOMETRY_OPS: FrozenSet[str] = frozenset(
    {
        "Delete",
        "Remove",
        "Cap",
        "Collapse",
        "SplitEdges",
        "BreakVertex",
        "FlipNormals",
        "MakePlanarInX",
        "MakePlanarInY",
        "MakePlanarInZ",
    }
)


@dataclass(frozen=True)
class DispatchOutcome:
    status: str  # launched | applied | noop | blocked | error
    message: str = ""
    ambiguous_mode: bool = False
    mode_token: str = ""

    def __post_init__(self) -> None:
        if self.status not in ("launched", "applied", "noop", "blocked", "error"):
            raise ValueError("Unsupported dispatch status: %r" % (self.status,))


@dataclass(frozen=True)
class DispatchOptions:
    undo_factory: Any = None
    hold_guard: Any = None
    confirmed: bool = False


@dataclass(frozen=True)
class HandlerSpec:
    kind: str
    target: str
    handler: Callable[[Any, PreparedTarget, str, DispatchOptions], DispatchOutcome]
    members: Tuple[Tuple[str, str], ...] = ()
    undoable: bool = False
    gated_reason: str = ""


# ---------------------------------------------------------------------------
# Small read-only helpers
# ---------------------------------------------------------------------------


def _is_set(value: Any) -> bool:
    if value is None:
        return False
    try:
        return str(value).strip().lower() != "undefined"
    except Exception:
        return False


def _normalize_mode(value: Any) -> str:
    if not _is_set(value):
        return "none"
    try:
        text = str(value).strip().lower()
    except Exception:
        return "unknown"
    text = text.lstrip("#").strip("\"'")
    if text in ("-1", "none"):
        return "none"
    return text or "none"


def _name(runtime: Any, text: str) -> Any:
    factory = getattr(runtime, "Name", None)
    if not callable(factory):
        raise OperationError("pymxs.runtime.Name is unavailable.")
    return factory(text)


def _ep(prepared: PreparedTarget) -> Any:
    ep = prepared.ep
    if ep is None:
        raise OperationError("The target EditablePoly interface is unavailable.")
    return ep


def _indices(prepared: PreparedTarget) -> Tuple[int, ...]:
    return tuple(prepared.indices)


def _read_level_indices(
    runtime: Any, prepared: PreparedTarget, context: str
) -> Tuple[int, ...]:
    from .preparation import V2_LEGACY_LEVEL

    try:
        return tuple(
            read_selection_indices(
                runtime, V2_LEGACY_LEVEL[context], prepared.node
            )
            or ()
        )
    except OperationError:
        return ()


def _open_edges(runtime: Any, prepared: PreparedTarget) -> Tuple[int, ...]:
    polyop = getattr(runtime, "polyop", None)
    if polyop is None or not _is_set(polyop):
        raise OperationError("polyop is unavailable.")
    try:
        bitarray = polyop.getOpenEdges(prepared.node)
    except Exception as exc:
        raise OperationError(
            "Open edges could not be read (%s)." % type(exc).__name__
        ) from exc
    try:
        return tuple(sorted(int(value) for value in bitarray))
    except Exception as exc:
        raise OperationError("Open-edge indices could not be coerced.") from exc


def read_ep_mode(runtime: Any, ep: Any) -> Tuple[str, bool]:
    """Return ``(normalized_mode, readable)`` for an EditablePoly interface."""

    try:
        value = getattr(ep, "getCommandMode")
        if callable(value):
            value = value()
    except Exception:
        return "unknown", False
    if not _is_set(value):
        return "none", True
    text = _normalize_mode(value)
    if text == "unknown":
        return text, False
    return text, True


def exit_idle_editable_poly_mode(runtime: Any, ep: Any) -> Tuple[bool, str]:
    """Q13: exit one verified active EditablePoly mode before chaining.

    Foreign tool/pick modes are not touched here.  An unreadable mode blocks
    chaining because ownership cannot be proven.
    """

    mode, readable = read_ep_mode(runtime, ep)
    if not readable:
        return (
            False,
            "The active Editable Poly command mode could not be verified; "
            "another maneuver will not be launched.",
        )
    if mode in IDLE_EP_MODES:
        return True, ""
    exit_function = getattr(ep, "exitCommandModes", None)
    if not callable(exit_function):
        return (
            False,
            "An active Editable Poly command mode exists but exitCommandModes is "
            "unavailable; chaining is blocked.",
        )
    try:
        exit_function()
    except Exception as exc:
        return (
            False,
            "The idle Editable Poly command mode could not be exited (%s); "
            "chaining is blocked." % type(exc).__name__,
        )
    mode, readable = read_ep_mode(runtime, ep)
    if not readable or mode not in IDLE_EP_MODES:
        return (
            False,
            "EditablePoly.exitCommandModes() did not return the interface to an "
            "idle mode; chaining is blocked.",
        )
    return True, ""


# ---------------------------------------------------------------------------
# Caddy handlers
# ---------------------------------------------------------------------------


def _caddy_handler(semantic: str):
    native = CADDY_DIALOG_MAP[semantic]

    def handler(
        runtime: Any, prepared: PreparedTarget, context: str, options: DispatchOptions
    ) -> DispatchOutcome:
        ep = _ep(prepared)
        try:
            result = ep.PopupDialog(_name(runtime, native))
        except Exception as exc:
            return DispatchOutcome(
                "error",
                "The native %s caddy could not be opened (%s). No geometry result "
                "is claimed." % (semantic, type(exc).__name__),
            )
        if result is False:
            return DispatchOutcome(
                "error",
                "EditablePoly.PopupDialog reported failure for the %s caddy; this "
                "is not a completed edit." % semantic,
            )
        return DispatchOutcome(
            "launched",
            "Opened the native %s caddy (#%s). The native dialog owns its preview, "
            "accept, cancel and undo; no geometry result is claimed."
            % (semantic, native),
        )

    return handler


# ---------------------------------------------------------------------------
# Command-mode handlers
# ---------------------------------------------------------------------------


def _standard_mode_handler(semantic: str):
    def handler(
        runtime: Any, prepared: PreparedTarget, context: str, options: DispatchOptions
    ) -> DispatchOutcome:
        ep = _ep(prepared)
        try:
            ep.toggleCommandMode(_name(runtime, semantic))
        except Exception as exc:
            return DispatchOutcome(
                "error",
                "Native %s command mode entry failed (%s)."
                % (semantic, type(exc).__name__),
            )
        observed, readable = read_ep_mode(runtime, ep)
        if not readable or observed != _normalize_mode(semantic):
            return DispatchOutcome(
                "error",
                "Native mode readback was %r, expected %r; no launch is claimed."
                % (observed, _normalize_mode(semantic)),
            )
        return DispatchOutcome(
            "launched",
            "Entered the native %s command mode. Viewport interaction and native "
            "cancellation/undo are Max-owned." % semantic,
            mode_token=observed,
        )

    return handler


def _swift_loop_handler(
    runtime: Any, prepared: PreparedTarget, context: str, options: DispatchOptions
) -> DispatchOutcome:
    graphite = getattr(runtime, "PolyToolsPolyDraw", None)
    launcher = getattr(graphite, "SwiftLoop", None) if graphite is not None else None
    if not callable(launcher):
        return DispatchOutcome(
            "blocked",
            "PolyToolsPolyDraw.SwiftLoop is unavailable; the Graphite SwiftLoop "
            "route is blocked rather than substituted.",
        )
    try:
        launcher()
    except Exception as exc:
        return DispatchOutcome(
            "error",
            "Graphite SwiftLoop could not be launched (%s)." % type(exc).__name__,
        )
    return DispatchOutcome(
        "launched",
        SWIFT_LOOP_MESSAGE,
        ambiguous_mode=True,
        mode_token="graphite:swiftloop",
    )


def _bridge_mode_handler(
    runtime: Any, prepared: PreparedTarget, context: str, options: DispatchOptions
) -> DispatchOutcome:
    return DispatchOutcome("blocked", BRIDGE_GATE_REASON)


# ---------------------------------------------------------------------------
# Button-op handlers
# ---------------------------------------------------------------------------


def _button_op_handler(target: str):
    def handler(
        runtime: Any, prepared: PreparedTarget, context: str, options: DispatchOptions
    ) -> DispatchOutcome:
        ep = _ep(prepared)
        if context == "object":
            return DispatchOutcome(
                "blocked",
                "Component button operations are never run at object level.",
            )
        indices = _indices(prepared)
        if not indices:
            return DispatchOutcome(
                "blocked", "Select components first; no operation was launched."
            )
        if target == "Cap":
            try:
                open_edges = set(_open_edges(runtime, prepared))
            except OperationError as exc:
                return DispatchOutcome("blocked", str(exc))
            if not set(indices).issubset(open_edges):
                return DispatchOutcome(
                    "blocked",
                    "Cap requires selected open border edges only; closed edges "
                    "are never capped.",
                )

        def edit():
            if target == "Cap":
                open_edges = set(_open_edges(runtime, prepared))
                if not set(indices).issubset(open_edges):
                    raise OperationError(
                        "The border selection changed to include closed edges; "
                        "cap was rolled back."
                    )
            before_geometry = None
            before_selection = None
            if target in _SELECTION_OPS:
                before_selection = _read_level_indices(runtime, prepared, context)
            else:
                before_geometry = read_geometry(runtime, prepared.node)
            try:
                result = ep.buttonOp(_name(runtime, target))
            except Exception as exc:
                raise OperationError(
                    "EditablePoly.buttonOp #%s failed (%s)."
                    % (target, type(exc).__name__)
                ) from exc
            if result is False:
                raise OperationError("EditablePoly.buttonOp #%s reported failure." % target)
            if target in _SELECTION_OPS:
                after = _read_level_indices(runtime, prepared, context)
                if after == before_selection:
                    return _ButtonOutcome("noop", "The component selection did not change.")
                return _ButtonOutcome(
                    "applied",
                    "%s changed the component selection (%d -> %d)."
                    % (target, len(before_selection or ()), len(after)),
                )
            after_geometry = read_geometry(runtime, prepared.node)
            if (
                after_geometry.counts == before_geometry.counts
                and after_geometry.digest == before_geometry.digest
            ):
                return _ButtonOutcome("noop", "The mesh did not change.")
            return _ButtonOutcome(
                "applied",
                "%s applied (%d/%d/%d -> %d/%d/%d vertices/edges/faces)."
                % (
                    target,
                    before_geometry.verts,
                    before_geometry.edges,
                    before_geometry.faces,
                    after_geometry.verts,
                    after_geometry.edges,
                    after_geometry.faces,
                ),
            )

        outcome = run_undoable(
            "3dsModeler V2: " + target,
            edit,
            options.undo_factory,
            hold_guard=options.hold_guard,
        )
        if outcome.status == "error":
            return DispatchOutcome("error", outcome.message)
        return DispatchOutcome(outcome.status, outcome.message)

    return handler


@dataclass(frozen=True)
class _ButtonOutcome:
    status: str
    message: str


# ---------------------------------------------------------------------------
# Typed polyop clear
# ---------------------------------------------------------------------------


def _clear_selection_handler(
    runtime: Any, prepared: PreparedTarget, context: str, options: DispatchOptions
) -> DispatchOutcome:
    if context == "object":
        return DispatchOutcome(
            "blocked", "Object selection is not cleared by the component handler."
        )
    if not _read_level_indices(runtime, prepared, context):
        return DispatchOutcome("noop", "The component selection is already empty.")
    polyop = getattr(runtime, "polyop", None)
    if polyop is None:
        return DispatchOutcome("blocked", "polyop is unavailable.")
    none = _name(runtime, "none")
    try:
        if context == "vertex":
            polyop.setVertSelection(prepared.node, none)
        elif context in ("edge", "border"):
            polyop.setEdgeSelection(prepared.node, none)
        elif context in ("face", "element"):
            polyop.setFaceSelection(prepared.node, none)
        else:
            return DispatchOutcome("blocked", "Unsupported selection context.")
    except Exception as exc:
        return DispatchOutcome(
            "error", "Selection clear failed (%s)." % type(exc).__name__
        )
    if _read_level_indices(runtime, prepared, context):
        return DispatchOutcome(
            "error", "The component selection is still nonempty after the clear."
        )
    return DispatchOutcome(
        "applied", "Cleared the %s component selection; no geometry changed." % context
    )


# ---------------------------------------------------------------------------
# Fixed max command handlers
# ---------------------------------------------------------------------------


def _max_command_handler(semantic: str):
    literal = MAX_COMMAND_MAP[semantic]

    def handler(
        runtime: Any, prepared: PreparedTarget, context: str, options: DispatchOptions
    ) -> DispatchOutcome:
        try:
            runtime.execute(literal)
        except Exception as exc:
            return DispatchOutcome(
                "error",
                "The fixed %r command failed (%s)." % (literal, type(exc).__name__),
            )
        return DispatchOutcome(
            "launched",
            "Requested the fixed %r command. Entering a transform tool is not a "
            "modeling result; the transform remains interactive." % literal,
        )

    return handler


# ---------------------------------------------------------------------------
# Composite handlers
# ---------------------------------------------------------------------------


def _clean_dissolve_handler(
    runtime: Any, prepared: PreparedTarget, context: str, options: DispatchOptions
) -> DispatchOutcome:
    if context != "edge":
        return DispatchOutcome(
            "blocked", "Clean dissolve is available only in the edge context."
        )
    outcome = clean_dissolve_edges(
        runtime,
        prepared.node,
        _ep(prepared),
        ContextLevel.EDGE,
        _indices(prepared),
        undo_factory=options.undo_factory,
        hold_guard=options.hold_guard,
    )
    return DispatchOutcome(outcome.status, outcome.message)


def _convert_to_poly_handler(
    runtime: Any, prepared: PreparedTarget, context: str, options: DispatchOptions
) -> DispatchOutcome:
    if context != "object":
        return DispatchOutcome(
            "blocked", "Convert to Poly is an object-level action."
        )
    if prepared.converted:
        return DispatchOutcome(
            "applied",
            "Converted the selected node to an Editable Poly base object in one "
            "undoable preparation step.",
        )
    return DispatchOutcome(
        "noop", "The selected node is already an editable poly; no stack was baked."
    )


def _collapse_to_poly_handler(
    runtime: Any, prepared: PreparedTarget, context: str, options: DispatchOptions
) -> DispatchOutcome:
    if context != "object":
        return DispatchOutcome("blocked", "Collapse to Poly is an object-level action.")
    if not options.confirmed:
        return DispatchOutcome(
            "blocked",
            "Collapsing the stack is destructive and requires explicit confirmation.",
        )
    if prepared.collapsed:
        return DispatchOutcome(
            "applied",
            "Baked the modifier stack into an Editable Poly base object in one owned "
            "undo chunk.",
        )
    if prepared.status == "noop":
        return DispatchOutcome(
            "noop", prepared.reason or "No modifier stack was present to collapse."
        )
    return DispatchOutcome(
        "blocked", "The target was not prepared for an explicit stack collapse."
    )


# ---------------------------------------------------------------------------
# Context switch handler
# ---------------------------------------------------------------------------


def _context_switch_handler(
    runtime: Any, prepared: PreparedTarget, context: str, options: DispatchOptions
) -> DispatchOutcome:
    destination = context
    outcome = switch_prepared_context(
        runtime,
        prepared,
        destination,
        convert_selection=True,
        hold_guard=options.hold_guard,
    )
    if not outcome.ok:
        return DispatchOutcome(
            "blocked" if outcome.status == "blocked" else "error", outcome.reason
        )
    if outcome.status == "noop":
        return DispatchOutcome("noop", outcome.reason)
    return DispatchOutcome(
        "launched",
        outcome.reason
        + (
            " Selection-only change; no geometry was edited."
            if outcome.converted
            else ""
        ),
    )


# ---------------------------------------------------------------------------
# Registry construction
# ---------------------------------------------------------------------------


def _caddy_members(target: str) -> Tuple[Tuple[str, str], ...]:
    return (("editable_poly", "PopupDialog"),)


def _mode_members(target: str) -> Tuple[Tuple[str, str], ...]:
    if target == "SwiftLoop":
        return (("runtime", "PolyToolsPolyDraw"), ("PolyToolsPolyDraw", "SwiftLoop"))
    return (
        ("editable_poly", "toggleCommandMode"),
        ("editable_poly", "getCommandMode"),
    )


def _button_members(target: str) -> Tuple[Tuple[str, str], ...]:
    if target == "Cap":
        return (("editable_poly", "buttonOp"), ("polyop", "getOpenEdges"))
    return (("editable_poly", "buttonOp"),)


def _build_handlers() -> Dict[Tuple[str, str], HandlerSpec]:
    registry: Dict[Tuple[str, str], HandlerSpec] = {}
    for target in sorted(CADDY_TARGETS):
        registry[("caddy", target)] = HandlerSpec(
            "caddy", target, _caddy_handler(target), _caddy_members(target)
        )
    for target in sorted(COMMAND_MODE_TARGETS):
        if target in ("BridgeEdge", "BridgeBorder", "BridgePolygon"):
            handler = _bridge_mode_handler
            gated = BRIDGE_GATE_REASON
        elif target == "SwiftLoop":
            handler = _swift_loop_handler
            gated = ""
        else:
            handler = _standard_mode_handler(target)
            gated = ""
        registry[("command_mode", target)] = HandlerSpec(
            "command_mode",
            target,
            handler,
            _mode_members(target),
            undoable=False,
            gated_reason=gated,
        )
    for target in sorted(BUTTON_OP_TARGETS):
        registry[("button_op", target)] = HandlerSpec(
            "button_op",
            target,
            _button_op_handler(target),
            _button_members(target),
            undoable=True,
        )
    for target in sorted(POLYOP_TARGETS):
        registry[("polyop", target)] = HandlerSpec(
            "polyop",
            target,
            _clear_selection_handler,
            (
                ("runtime", "polyop"),
                ("polyop", "setVertSelection"),
                ("polyop", "setEdgeSelection"),
                ("polyop", "setFaceSelection"),
            ),
        )
    for target in sorted(MAX_COMMAND_TARGETS):
        registry[("max_command", target)] = HandlerSpec(
            "max_command",
            target,
            _max_command_handler(target),
            (("runtime", "execute"),),
        )
    for target, handler in (
        ("clean_dissolve_edges", _clean_dissolve_handler),
        ("convert_to_poly", _convert_to_poly_handler),
        ("collapse_to_poly", _collapse_to_poly_handler),
    ):
        registry[("composite", target)] = HandlerSpec(
            "composite",
            target,
            handler,
            (
                ("editable_poly", "Remove"),
                ("runtime", "polyop"),
                ("polyop", "getVertsUsingEdge"),
                ("polyop", "getEdgesUsingVert"),
                ("polyop", "getEdgeVerts"),
                ("polyop", "getVertSelection"),
                ("polyop", "setVertSelection"),
                ("polyop", "setEdgeSelection"),
                ("polyop", "getNumVerts"),
                ("polyop", "getNumEdges"),
                ("polyop", "getVert"),
            )
            if target == "clean_dissolve_edges"
            else (),
            undoable=target == "clean_dissolve_edges",
        )
    for target in sorted(CONTEXT_SWITCH_TARGETS):
        registry[("context_switch", target)] = HandlerSpec(
            "context_switch",
            target,
            _context_switch_handler,
            (("editable_poly", "ConvertSelection"), ("runtime", "subObjectLevel")),
        )
    return registry


HANDLERS: Dict[Tuple[str, str], HandlerSpec] = _build_handlers()


def handler_for(kind: str, target: str) -> Optional[HandlerSpec]:
    return HANDLERS.get((str(kind), str(target)))


def required_members(kind: str, target: str) -> Tuple[Tuple[str, str], ...]:
    spec = handler_for(kind, target)
    return spec.members if spec is not None else ()


def is_allowed(kind: str, target: str) -> bool:
    """Membership check against the frozen per-kind target sets."""

    if not isinstance(kind, str) or kind not in TARGETS_BY_KIND:
        return False
    if not isinstance(target, str):
        return False
    return target in TARGETS_BY_KIND[kind]


def missing_handlers() -> Tuple[Tuple[str, str], ...]:
    """Registry parity: every frozen ``(kind, target)`` has one fixed handler."""

    missing: List[Tuple[str, str]] = []
    for kind, targets in TARGETS_BY_KIND.items():
        for target in sorted(targets):
            if (kind, target) not in HANDLERS:
                missing.append((kind, target))
    return tuple(missing)


def contract_parity(contract_targets: Any = None) -> Tuple[str, ...]:
    """Return structural mismatches against ``core.maneuver_contracts``.

    Only key sets and exact target membership are compared.  Handler existence,
    API text, and documentation URLs are host concerns and never influence the
    frozen descriptor registry.
    """

    issues: List[str] = []
    if contract_targets is None:
        try:  # pragma: no cover - depends on stream A landing first
            from ..core import maneuver_contracts  # type: ignore[attr-defined]

            contract_targets = getattr(maneuver_contracts, "TARGETS_BY_KIND", None)
        except Exception:
            contract_targets = None
    if contract_targets is None:
        return tuple(issues)
    try:
        contract = {
            str(kind): frozenset(str(target) for target in targets)
            for kind, targets in dict(contract_targets).items()
        }
    except Exception:
        return ("contract TARGETS_BY_KIND is not a kind->targets mapping",)
    if set(contract) != set(TARGETS_BY_KIND):
        issues.append(
            "contract kinds %s != host kinds %s"
            % (sorted(contract), sorted(TARGETS_BY_KIND))
        )
    for kind in sorted(set(contract) & set(TARGETS_BY_KIND)):
        if contract[kind] != TARGETS_BY_KIND[kind]:
            issues.append(
                "contract/host target mismatch for %s: contract-only=%s host-only=%s"
                % (
                    kind,
                    sorted(contract[kind] - TARGETS_BY_KIND[kind]),
                    sorted(TARGETS_BY_KIND[kind] - contract[kind]),
                )
            )
    return tuple(issues)


def dispatch(
    runtime: Any,
    descriptor: Any,
    prepared: PreparedTarget,
    context: Any,
    *,
    undo_factory: Any = None,
    hold_guard: Any = None,
    confirmed: bool = False,
) -> DispatchOutcome:
    """Dispatch exactly one fixed handler selected by ``kind`` and ``target``.

    ``descriptor.api``/``doc_url`` are intentionally never read.  An unknown or
    disallowed descriptor is blocked before any native call.
    """

    kind = getattr(descriptor, "kind", None)
    if kind is None and isinstance(descriptor, dict):
        kind = descriptor.get("kind")
    target = getattr(descriptor, "target", None)
    if target is None and isinstance(descriptor, dict):
        target = descriptor.get("target")
    if not isinstance(kind, str) or not isinstance(target, str):
        return DispatchOutcome("blocked", "The descriptor kind/target is invalid.")
    if not is_allowed(kind, target):
        return DispatchOutcome(
            "blocked",
            "The descriptor %s/%s is not in the frozen target registry."
            % (kind, target),
        )
    spec = handler_for(kind, target)
    if spec is None:
        return DispatchOutcome(
            "blocked",
            "No reviewed native handler exists for %s/%s." % (kind, target),
        )
    try:
        destination = context_value(context)
    except ValueError as exc:
        return DispatchOutcome("blocked", str(exc))
    if not isinstance(prepared, PreparedTarget) or not prepared.ok:
        return DispatchOutcome(
            "blocked",
            prepared.reason
            if isinstance(prepared, PreparedTarget)
            else "The target is not prepared.",
        )
    return spec.handler(
        runtime,
        prepared,
        destination,
        DispatchOptions(
            undo_factory=undo_factory,
            hold_guard=hold_guard,
            confirmed=confirmed,
        ),
    )


__all__ = [
    "BRIDGE_GATE_REASON",
    "BUTTON_OP_TARGETS",
    "CADDY_DIALOG_MAP",
    "CADDY_TARGETS",
    "COMMAND_MODE_TARGETS",
    "COMPOSITE_TARGETS",
    "CONTEXT_SWITCH_TARGETS",
    "DispatchOptions",
    "DispatchOutcome",
    "HANDLERS",
    "HandlerSpec",
    "IDLE_EP_MODES",
    "KIND_ORDER",
    "MAX_COMMAND_MAP",
    "MAX_COMMAND_TARGETS",
    "POLYOP_TARGETS",
    "SWIFT_LOOP_MESSAGE",
    "TARGETS_BY_KIND",
    "contract_parity",
    "dispatch",
    "exit_idle_editable_poly_mode",
    "handler_for",
    "is_allowed",
    "missing_handlers",
    "read_ep_mode",
    "required_members",
]

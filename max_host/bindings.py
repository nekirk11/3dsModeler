"""Fixed native action-to-command routing for the Max 2025 adapter.

Coverage is derived from :mod:`max2025_menu_prototype.core.catalog`; this
module never maintains a divergent handwritten action list.  Every binding is
an original mapping to a documented MAXScript/Python member.  ``runtime_status``
is ``not_run`` for all of them because no 3ds Max 2025 installation was
available for this prototype.

Nothing here accepts a profile string, node name, script fragment, filename,
callable, or arbitrary property name.  ``max`` command literals are module
constants and the only strings ever passed to ``runtime.execute``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, FrozenSet, Optional, Tuple

from ..core.catalog import ACTIONS
from ..core.contracts import (
    Availability,
    ContextLevel,
    ContextSnapshot,
    LaunchResult,
)
from ..core.parameters import ParameterError, validate_parameters
from .operations import MutableTarget, execute_operation
from .transactions import hold_reason, run_undoable

# Documented `max` commands.
MAX_MOVE_COMMAND = "max move"
MAX_ROTATE_COMMAND = "max rotate"
MAX_SCALE_COMMAND = "max scale"
MAX_ZOOM_EXTENTS_SELECTED_COMMAND = "max zoomext sel"

FIXED_MAX_COMMAND_LITERALS = frozenset(
    {
        MAX_MOVE_COMMAND,
        MAX_ROTATE_COMMAND,
        MAX_SCALE_COMMAND,
        MAX_ZOOM_EXTENTS_SELECTED_COMMAND,
    }
)

# Evidence URLs (Autodesk 2025 MAXScript/Python documentation).
COMMANDS_REFERENCE = (
    "3ds Max MAXScript Help, '3ds MAX Commands' (archived ENU page): "
    "https://help.autodesk.com/cloudhelp/2017/ENU/MAXScript-Help/files/"
    "GUID-A96857E7-73FE-4F42-BE71-E8185356F4C9.htm"
)
EDITABLEPOLY_REFERENCE = (
    "3ds Max 2025 MAXScript Help, Interface: EditablePoly: "
    "https://help.autodesk.com/cloudhelp/2025/ENU/MAXScript-Help/files/"
    "3ds-Max-Objects-and-Interfaces/Editable-Meshes-Splines-Patches/"
    "Editable_Poly/GUID-90843EC5-AE3A-43EB-9406-A3631DAEADEF.html"
)
EDITABLEPOLY_PROPERTIES_REFERENCE = (
    "3ds Max 2025 MAXScript Help, Editable_Poly Properties: "
    "https://help.autodesk.com/cloudhelp/2025/ENU/MAXScript-Help/files/"
    "3ds-Max-Objects-and-Interfaces/Editable-Meshes-Splines-Patches/"
    "Editable_Poly/GUID-0196A023-116F-47F8-99E9-AF3CB52F302C.html"
)
POLYOP_EXTRUDE_REFERENCE = (
    "3ds Max 2025 MAXScript Help, Editable_Poly Extrude, Bevel and Chamfer "
    "Methods: https://help.autodesk.com/cloudhelp/2025/ENU/MAXScript-Help/"
    "files/3ds-Max-Objects-and-Interfaces/Editable-Meshes-Splines-Patches/"
    "Editable_Poly/Editable_Poly-Methods/GUID-942EDBDB-D13B-4D37-8098-"
    "868EFA68BA6E.html"
)
POLYOP_SELECTION_REFERENCE = (
    "3ds Max 2025 MAXScript Help, Editable_Poly Get/Set Selection Methods: "
    "https://help.autodesk.com/cloudhelp/2025/ENU/MAXScript-Help/files/"
    "3ds-Max-Objects-and-Interfaces/Editable-Meshes-Splines-Patches/"
    "Editable_Poly/Editable_Poly-Methods/GUID-37771642-5CC2-49F3-B77F-"
    "4F390E7ABD9E.html"
)
POLYOP_OPEN_EDGES_REFERENCE = (
    "3ds Max 2025 MAXScript Help, Editable_Poly Open Edges Methods: "
    "https://help.autodesk.com/cloudhelp/2025/ENU/MAXScript-Help/files/"
    "3ds-Max-Objects-and-Interfaces/Editable-Meshes-Splines-Patches/"
    "Editable_Poly/Editable_Poly-Methods/GUID-77A69BBB-6E1C-488B-8973-"
    "CDBF54FF2486.html"
)
POLYOP_COLLAPSE_REFERENCE = (
    "3ds Max 2025 MAXScript Help, Editable_Poly Collapse Methods: "
    "https://help.autodesk.com/cloudhelp/2025/ENU/MAXScript-Help/files/"
    "3ds-Max-Objects-and-Interfaces/Editable-Meshes-Splines-Patches/"
    "Editable_Poly/Editable_Poly-Methods/GUID-DCD5813F-1956-4E9D-BC57-"
    "DD2ED6C02293.html"
)
POLYOP_WELD_REFERENCE = (
    "3ds Max 2025 MAXScript Help, Editable_Poly Weld Methods: "
    "https://help.autodesk.com/cloudhelp/2025/ENU/MAXScript-Help/files/"
    "3ds-Max-Objects-and-Interfaces/Editable-Meshes-Splines-Patches/"
    "Editable_Poly/Editable_Poly-Methods/GUID-B801F6E2-FCA6-43CF-A736-"
    "B7C157135A65.html"
)
POLYOP_DELETE_REFERENCE = (
    "3ds Max 2025 MAXScript Help, Editable_Poly Delete Methods: "
    "https://help.autodesk.com/cloudhelp/2025/ENU/MAXScript-Help/files/"
    "3ds-Max-Objects-and-Interfaces/Editable-Meshes-Splines-Patches/"
    "Editable_Poly/Editable_Poly-Methods/GUID-12ABA4CC-0600-49E7-A543-"
    "069BA1952A98.html"
)
BITARRAY_REFERENCE = (
    "3ds Max 2025 MAXScript Help, BitArray Values (numberSet, iteration, "
    "as array): https://help.autodesk.com/cloudhelp/2025/ENU/MAXScript-Help/"
    "files/MAXScript-Language-Reference/Collections/Collection-Types/"
    "GUID-0F53E04C-0760-4B9C-A1BE-0E53162DE479.html"
)
PYMXS_UNDO_REFERENCE = (
    "3ds Max 2025 Python Developer Help, pymxs module (undo exception "
    "rollback and suppression): https://help.autodesk.com/cloudhelp/2025/"
    "ENU/MAXDEV-Python/files/using_pymxs/"
    "MAXDEV_Python_using_pymxs_pymxs_module_html.html"
)
PYMXS_REFERENCE = (
    "3ds Max 2025 Python Developer Help, Using pymxs (runtime.Name enums, "
    "1-based method indices, equality not identity): "
    "https://help.autodesk.com/cloudhelp/2025/ENU/MAXDEV-Python/files/"
    "MAXDEV_Python_using_pymxs_html.html"
)
MODIFY_PANEL_REFERENCE = (
    "3ds Max 2025 MAXScript Help, Modify panel (subObjectLevel and "
    "modPanel.getCurrentObject): https://help.autodesk.com/cloudhelp/2025/"
    "ENU/MAXScript-Help/files/MAXScript-Tools-and-Interaction/"
    "Interacting-with-the-3ds-Max/Command-Panels/"
    "GUID-BAC3CC23-9D1D-44CE-B4AF-C0CEBDD7099E.html"
)
DEPENDENT_NODES_REFERENCE = (
    "3ds Max 2025 MAXScript Help, MAXWrapper common methods "
    "(refs.dependentNodes baseObjectOnly): https://help.autodesk.com/"
    "cloudhelp/2025/ENU/MAXScript-Help/files/3ds-Max-Objects-and-Interfaces/"
    "MAXWrapper-Value/GUID-95453E22-A022-4543-B31C-A052CECD3598.html"
)
THREADING_REFERENCE = (
    "3ds Max 2025 Python Developer Help, Threading in Python with 3ds Max: "
    "https://help.autodesk.com/cloudhelp/2025/ENU/MAXDEV-Python/files/"
    "MAXDEV_Python_threading_html.html"
)

_ALL_LEVELS = frozenset(ContextLevel)
_SUBOBJECT_LEVELS = frozenset(
    {
        ContextLevel.VERTEX,
        ContextLevel.EDGE,
        ContextLevel.BORDER,
        ContextLevel.POLYGON,
        ContextLevel.ELEMENT,
    }
)
_LEVEL_NOUNS = {
    ContextLevel.OBJECT: "objects",
    ContextLevel.VERTEX: "vertices",
    ContextLevel.EDGE: "edges",
    ContextLevel.BORDER: "border edges",
    ContextLevel.POLYGON: "polygons",
    ContextLevel.ELEMENT: "elements",
}

#: All catalog IDs, derived from the shared core catalog.
CATALOG_ACTION_IDS: Tuple[str, ...] = tuple(action.id for action in ACTIONS)
CATALOG_ACTION_IDS_SET = frozenset(CATALOG_ACTION_IDS)

# A sentinel prefix in the opaque selection token marks an active sub-object
# level whose target cannot be verified.
UNVERIFIED_SUBOBJECT_PREFIX = "unverified-subobject:"


@dataclass(frozen=True)
class NativeBinding:
    """One frozen action-to-native mapping and its live preflight check."""

    action_id: str
    handler: str  # command | level | operation | mode | exit
    launch_kind: str
    allowed_levels: FrozenSet[ContextLevel]
    documentation: Tuple[str, ...]
    check: Callable[[ContextSnapshot], Availability]
    dispatch: Optional[Callable[[Any, ContextSnapshot], str]] = None
    required_members: Tuple[Tuple[str, str], ...] = field(default_factory=tuple)
    undo_label: str = ""
    runtime_status: str = "not_run"

    HANDLERS = ("command", "level", "operation", "mode", "exit")

    def __post_init__(self) -> None:
        if self.handler not in self.HANDLERS:
            raise ValueError(f"Unsupported native handler: {self.handler!r}")
        if self.handler in ("command", "level") and self.dispatch is None:
            raise ValueError(f"{self.action_id} requires a dispatch callable")
        if self.required_members and not all(
            isinstance(item, tuple) and len(item) == 2 for item in self.required_members
        ):
            raise ValueError("required_members must be (owner, member) tuples")

    @property
    def is_mutation(self) -> bool:
        return self.handler == "operation"


def topology_target_reason(context: ContextSnapshot) -> str:
    """Explain why a conservative Editable Poly target is not usable, if so."""

    kind = context.target_kind
    if kind == "none":
        return "Select one supported object"
    if kind == "multiple":
        return "Select a single Editable Poly object"
    if kind == "edit_poly":
        return "Active Edit Poly target not supported in this experiment"
    if kind != "editable_poly":
        return (
            "Only an unmodified Editable Poly base object is supported "
            "(no conversion)"
        )
    if not context.panel_ready:
        return "Open the Modify panel on the Editable Poly"
    return ""


def _selection_reason(context: ContextSnapshot) -> str:
    noun = _LEVEL_NOUNS.get(context.level, "components")
    return "Select %s first" % noun


def unsupported_subobject_target(context: ContextSnapshot) -> bool:
    """True when the snapshot describes an active but unverifiable sub-object target."""

    return str(getattr(context, "selection_token", "")).startswith(
        UNVERIFIED_SUBOBJECT_PREFIX
    )


def _object_or_component_check(context: ContextSnapshot) -> Availability:
    if context.selection_count < 1:
        if context.level == ContextLevel.OBJECT:
            return Availability(False, "Select at least one object")
        return Availability(False, _selection_reason(context))
    return Availability(True, "")


def object_transform_check(context: ContextSnapshot) -> Availability:
    if unsupported_subobject_target(context):
        return Availability(
            False,
            "Active sub-object target cannot be verified; transforms are disabled.",
        )
    if context.level != ContextLevel.OBJECT:
        return subobject_transform_check(context)
    return _object_or_component_check(context)


def subobject_transform_check(context: ContextSnapshot) -> Availability:
    reason = topology_target_reason(context)
    if reason:
        return Availability(False, reason)
    return _object_or_component_check(context)


def frame_selection_check(context: ContextSnapshot) -> Availability:
    if context.level == ContextLevel.OBJECT:
        return _object_or_component_check(context)
    reason = topology_target_reason(context)
    if reason:
        return Availability(False, reason)
    return _object_or_component_check(context)


def level_check(context: ContextSnapshot) -> Availability:
    reason = topology_target_reason(context)
    if reason:
        return Availability(False, reason)
    return Availability(True, "")


def _subobject_check(
    context: ContextSnapshot, minimum: int = 1, allow_empty: bool = False
) -> Availability:
    if context.level == ContextLevel.OBJECT:
        return Availability(False, "Choose a sub-object context first")
    reason = topology_target_reason(context)
    if reason:
        return Availability(False, reason)
    if allow_empty:
        return Availability(True, "")
    if context.selection_count < minimum:
        if minimum == 1:
            return Availability(False, _selection_reason(context))
        noun = _LEVEL_NOUNS.get(context.level, "components")
        return Availability(False, "Select at least %d %s first" % (minimum, noun))
    return Availability(True, "")


def selection_change_check(context: ContextSnapshot) -> Availability:
    return _subobject_check(context)


def selection_clear_check(context: ContextSnapshot) -> Availability:
    return _subobject_check(context, allow_empty=True)


def selection_pair_check(context: ContextSnapshot) -> Availability:
    return _subobject_check(context, minimum=2)


def polygon_only_check(context: ContextSnapshot) -> Availability:
    if context.level != ContextLevel.POLYGON:
        return Availability(False, "Available only in the polygon context.")
    return _subobject_check(context)


def flip_check(context: ContextSnapshot) -> Availability:
    if context.level not in (ContextLevel.POLYGON, ContextLevel.ELEMENT):
        return Availability(False, "Flip normals requires polygon or element context.")
    return _subobject_check(context)


def chamfer_check(context: ContextSnapshot) -> Availability:
    if context.level not in (ContextLevel.VERTEX, ContextLevel.EDGE):
        return Availability(False, "Chamfer requires a vertex or edge context.")
    return _subobject_check(context)


def connect_check(context: ContextSnapshot) -> Availability:
    if context.level not in (ContextLevel.VERTEX, ContextLevel.EDGE):
        return Availability(False, "Connect requires a vertex or edge context.")
    return _subobject_check(context, minimum=2)


def bridge_check(context: ContextSnapshot) -> Availability:
    if context.level not in (
        ContextLevel.EDGE,
        ContextLevel.BORDER,
        ContextLevel.POLYGON,
    ):
        return Availability(False, "Bridge requires an edge, border, or polygon context.")
    return _subobject_check(context)


def weld_check(context: ContextSnapshot) -> Availability:
    if context.level != ContextLevel.VERTEX:
        return Availability(False, "Threshold weld requires a vertex context.")
    return _subobject_check(context, minimum=2)


def cap_check(context: ContextSnapshot) -> Availability:
    if context.level != ContextLevel.BORDER:
        return Availability(False, "Cap requires a border context.")
    return _subobject_check(context)


def remove_check(context: ContextSnapshot) -> Availability:
    if context.level not in (ContextLevel.VERTEX, ContextLevel.EDGE):
        return Availability(False, "Remove requires a vertex or edge context.")
    return _subobject_check(context)


def collapse_check(context: ContextSnapshot) -> Availability:
    if context.level not in (
        ContextLevel.VERTEX,
        ContextLevel.EDGE,
        ContextLevel.POLYGON,
    ):
        return Availability(False, "Collapse requires vertex, edge, or polygon context.")
    return _subobject_check(context)


def delete_check(context: ContextSnapshot) -> Availability:
    return _subobject_check(context)


def planar_check(context: ContextSnapshot) -> Availability:
    return _subobject_check(context)


def cut_check(context: ContextSnapshot) -> Availability:
    if context.level not in (
        ContextLevel.VERTEX,
        ContextLevel.EDGE,
        ContextLevel.POLYGON,
    ):
        return Availability(False, "Cut requires vertex, edge, or polygon context.")
    return _subobject_check(context, allow_empty=True)


def target_weld_check(context: ContextSnapshot) -> Availability:
    if context.level != ContextLevel.VERTEX:
        return Availability(False, "Target weld requires a vertex context.")
    return _subobject_check(context, allow_empty=True)


def exit_tool_check(context: ContextSnapshot) -> Availability:
    return _subobject_check(context, allow_empty=True)


def unverified_reason(action_id: str, context: ContextSnapshot) -> str:
    """Contextual reason for a catalog action with no native binding."""

    if action_id == "menu.repeat":
        return "Repeat is owned by the shared menu controller."
    if action_id.startswith("selection."):
        if context.level == ContextLevel.OBJECT:
            return "Available only in a sub-object context"
        target_reason = topology_target_reason(context)
        if target_reason:
            return target_reason
        if context.selection_count < 1:
            return _selection_reason(context)
    elif action_id.startswith("model."):
        if context.level == ContextLevel.OBJECT:
            return "Choose a sub-object context first"
        target_reason = topology_target_reason(context)
        if target_reason:
            return target_reason
        if context.selection_count < 1:
            return _selection_reason(context)
    return "Host binding not implemented for this action."


def _run_max_command(runtime: Any, literal: str) -> None:
    # ``literal`` is a module constant from FIXED_MAX_COMMAND_LITERALS.
    runtime.execute(literal)


def _dispatch_move(runtime: Any, context: ContextSnapshot) -> str:
    _run_max_command(runtime, MAX_MOVE_COMMAND)
    return (
        "Entered Select and Move mode; complete the transform in the viewport. "
        "No modeling result is claimed."
    )


def _dispatch_rotate(runtime: Any, context: ContextSnapshot) -> str:
    _run_max_command(runtime, MAX_ROTATE_COMMAND)
    return (
        "Entered Select and Rotate mode; complete the rotation in the viewport. "
        "No modeling result is claimed."
    )


def _dispatch_scale(runtime: Any, context: ContextSnapshot) -> str:
    _run_max_command(runtime, MAX_SCALE_COMMAND)
    return (
        "Entered Select and Scale mode; complete the scale in the viewport. "
        "No modeling result is claimed."
    )


def _dispatch_frame_selection(runtime: Any, context: ContextSnapshot) -> str:
    _run_max_command(runtime, MAX_ZOOM_EXTENTS_SELECTED_COMMAND)
    return "Requested Zoom Extents Selected for the active viewport."


def _dispatch_level(index: int):
    def dispatch(runtime: Any, context: ContextSnapshot) -> str:
        runtime.subObjectLevel = index
        try:
            observed = runtime.subObjectLevel
        except Exception as exc:
            raise RuntimeError(
                "subObjectLevel could not be read back: %s" % exc
            ) from exc
        try:
            observed_index = int(observed)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("subObjectLevel readback was not an integer") from exc
        if observed_index != index:
            raise RuntimeError(
                "subObjectLevel readback was %s, expected %d"
                % (observed_index, index)
            )
        names = {
            0: "object",
            1: "vertex",
            2: "edge",
            3: "border",
            4: "polygon",
            5: "element",
        }
        return "Entered native %s sub-object level." % names[index]

    return dispatch


def _level_binding(action_id: str, index: int) -> NativeBinding:
    return NativeBinding(
        action_id=action_id,
        handler="level",
        launch_kind="selection",
        allowed_levels=_ALL_LEVELS,
        documentation=(EDITABLEPOLY_REFERENCE, MODIFY_PANEL_REFERENCE),
        check=level_check,
        dispatch=_dispatch_level(index),
        required_members=(("runtime", "subObjectLevel"),),
    )


def _operation_binding(
    action_id: str,
    allowed_levels: FrozenSet[ContextLevel],
    check: Callable[[ContextSnapshot], Availability],
    documentation: Tuple[str, ...],
    required_members: Tuple[Tuple[str, str], ...],
    label: str,
) -> NativeBinding:
    return NativeBinding(
        action_id=action_id,
        handler="operation",
        launch_kind="immediate",
        allowed_levels=allowed_levels,
        documentation=documentation,
        check=check,
        required_members=required_members,
        undo_label="Max2025 prototype: " + label,
    )


NATIVE_BINDINGS = {
    "transform.move": NativeBinding(
        action_id="transform.move",
        handler="command",
        launch_kind="interactive",
        allowed_levels=_ALL_LEVELS,
        documentation=(COMMANDS_REFERENCE,),
        check=object_transform_check,
        dispatch=_dispatch_move,
        required_members=(("runtime", "execute"),),
    ),
    "transform.rotate": NativeBinding(
        action_id="transform.rotate",
        handler="command",
        launch_kind="interactive",
        allowed_levels=_ALL_LEVELS,
        documentation=(COMMANDS_REFERENCE,),
        check=object_transform_check,
        dispatch=_dispatch_rotate,
        required_members=(("runtime", "execute"),),
    ),
    "transform.scale": NativeBinding(
        action_id="transform.scale",
        handler="command",
        launch_kind="interactive",
        allowed_levels=_ALL_LEVELS,
        documentation=(COMMANDS_REFERENCE,),
        check=object_transform_check,
        dispatch=_dispatch_scale,
        required_members=(("runtime", "execute"),),
    ),
    "view.frame_selection": NativeBinding(
        action_id="view.frame_selection",
        handler="command",
        launch_kind="view",
        allowed_levels=_ALL_LEVELS,
        documentation=(COMMANDS_REFERENCE,),
        check=frame_selection_check,
        dispatch=_dispatch_frame_selection,
        required_members=(("runtime", "execute"),),
    ),
    "context.object": _level_binding("context.object", 0),
    "context.vertex": _level_binding("context.vertex", 1),
    "context.edge": _level_binding("context.edge", 2),
    "context.border": _level_binding("context.border", 3),
    "context.polygon": _level_binding("context.polygon", 4),
    "context.element": _level_binding("context.element", 5),
    "selection.grow": _operation_binding(
        "selection.grow",
        _SUBOBJECT_LEVELS,
        selection_change_check,
        (EDITABLEPOLY_REFERENCE,),
        (("editable_poly", "buttonOp"),),
        "grow selection",
    ),
    "selection.shrink": _operation_binding(
        "selection.shrink",
        _SUBOBJECT_LEVELS,
        selection_change_check,
        (EDITABLEPOLY_REFERENCE,),
        (("editable_poly", "buttonOp"),),
        "shrink selection",
    ),
    "selection.loop": _operation_binding(
        "selection.loop",
        frozenset({ContextLevel.EDGE}),
        selection_change_check,
        (EDITABLEPOLY_REFERENCE,),
        (("editable_poly", "SelectEdgeLoop"),),
        "select edge loop",
    ),
    "selection.ring": _operation_binding(
        "selection.ring",
        frozenset({ContextLevel.EDGE}),
        selection_change_check,
        (EDITABLEPOLY_REFERENCE,),
        (("editable_poly", "SelectEdgeRing"),),
        "select edge ring",
    ),
    "selection.clear": _operation_binding(
        "selection.clear",
        _SUBOBJECT_LEVELS,
        selection_clear_check,
        (POLYOP_SELECTION_REFERENCE,),
        (
            ("runtime", "polyop"),
            ("polyop", "setVertSelection"),
            ("polyop", "setEdgeSelection"),
            ("polyop", "setFaceSelection"),
        ),
        "clear component selection",
    ),
    "selection.to_border": _operation_binding(
        "selection.to_border",
        frozenset({ContextLevel.EDGE}),
        selection_change_check,
        (EDITABLEPOLY_REFERENCE,),
        (("editable_poly", "SelectBorder"),),
        "select border",
    ),
    "selection.to_element": _operation_binding(
        "selection.to_element",
        frozenset({ContextLevel.POLYGON}),
        selection_change_check,
        (EDITABLEPOLY_REFERENCE,),
        (("editable_poly", "SelectElement"),),
        "select element",
    ),
    "model.extrude": _operation_binding(
        "model.extrude",
        frozenset({ContextLevel.POLYGON}),
        polygon_only_check,
        (POLYOP_EXTRUDE_REFERENCE, EDITABLEPOLY_PROPERTIES_REFERENCE),
        (("polyop", "extrudeFaces"), ("base_object", "extrusionType")),
        "extrude polygons",
    ),
    "model.bevel": _operation_binding(
        "model.bevel",
        frozenset({ContextLevel.POLYGON}),
        polygon_only_check,
        (POLYOP_EXTRUDE_REFERENCE, EDITABLEPOLY_PROPERTIES_REFERENCE),
        (("polyop", "bevelFaces"), ("base_object", "extrusionType")),
        "bevel polygons",
    ),
    "model.inset": _operation_binding(
        "model.inset",
        frozenset({ContextLevel.POLYGON}),
        polygon_only_check,
        (EDITABLEPOLY_REFERENCE, EDITABLEPOLY_PROPERTIES_REFERENCE),
        (
            ("editable_poly", "buttonOp"),
            ("base_object", "insetAmount"),
            ("base_object", "insetType"),
        ),
        "inset polygons",
    ),
    "model.chamfer": _operation_binding(
        "model.chamfer",
        frozenset({ContextLevel.VERTEX, ContextLevel.EDGE}),
        chamfer_check,
        (EDITABLEPOLY_REFERENCE, EDITABLEPOLY_PROPERTIES_REFERENCE),
        (
            ("editable_poly", "chamferVertices"),
            ("editable_poly", "chamferEdges"),
        ),
        "chamfer components",
    ),
    "model.cap": _operation_binding(
        "model.cap",
        frozenset({ContextLevel.BORDER}),
        cap_check,
        (POLYOP_OPEN_EDGES_REFERENCE, EDITABLEPOLY_REFERENCE),
        (("polyop", "capHolesByEdge"), ("polyop", "getOpenEdges")),
        "cap selected border",
    ),
    "model.connect": _operation_binding(
        "model.connect",
        frozenset({ContextLevel.VERTEX, ContextLevel.EDGE}),
        connect_check,
        (EDITABLEPOLY_REFERENCE, EDITABLEPOLY_PROPERTIES_REFERENCE),
        (
            ("editable_poly", "ConnectVertices"),
            ("editable_poly", "ConnectEdges"),
            ("base_object", "connectEdgeSegments"),
            ("base_object", "connectEdgePinch"),
            ("base_object", "connectEdgeSlide"),
        ),
        "connect components",
    ),
    "model.bridge": _operation_binding(
        "model.bridge",
        frozenset(
            {ContextLevel.EDGE, ContextLevel.BORDER, ContextLevel.POLYGON}
        ),
        bridge_check,
        (
            EDITABLEPOLY_REFERENCE,
            EDITABLEPOLY_PROPERTIES_REFERENCE,
            POLYOP_OPEN_EDGES_REFERENCE,
        ),
        (
            ("editable_poly", "ReadyToBridgeFlagged"),
            ("editable_poly", "buttonOp"),
            ("base_object", "bridgeSelected"),
            ("base_object", "bridgeSegments"),
            ("base_object", "bridgeTaper"),
            ("base_object", "bridgeBias"),
            ("base_object", "bridgeTwist1"),
            ("base_object", "bridgeTwist2"),
        ),
        "bridge components",
    ),
    "model.weld": _operation_binding(
        "model.weld",
        frozenset({ContextLevel.VERTEX}),
        weld_check,
        (
            POLYOP_WELD_REFERENCE,
            POLYOP_OPEN_EDGES_REFERENCE,
            EDITABLEPOLY_PROPERTIES_REFERENCE,
        ),
        (
            ("polyop", "weldVertsByThreshold"),
            ("polyop", "getOpenEdges"),
            ("polyop", "getVertsUsingEdge"),
            ("base_object", "weldThreshold"),
        ),
        "threshold weld border vertices",
    ),
    "model.collapse": _operation_binding(
        "model.collapse",
        frozenset(
            {ContextLevel.VERTEX, ContextLevel.EDGE, ContextLevel.POLYGON}
        ),
        collapse_check,
        (POLYOP_COLLAPSE_REFERENCE,),
        (
            ("polyop", "collapseVerts"),
            ("polyop", "collapseEdges"),
            ("polyop", "collapseFaces"),
        ),
        "collapse selected components",
    ),
    "model.delete": _operation_binding(
        "model.delete",
        _SUBOBJECT_LEVELS,
        delete_check,
        (POLYOP_DELETE_REFERENCE,),
        (
            ("polyop", "deleteVerts"),
            ("polyop", "deleteEdges"),
            ("polyop", "deleteFaces"),
            ("polyop", "deleteIsoVerts"),
        ),
        "delete selected components",
    ),
    "model.remove": _operation_binding(
        "model.remove",
        frozenset({ContextLevel.VERTEX, ContextLevel.EDGE}),
        remove_check,
        (EDITABLEPOLY_REFERENCE, POLYOP_DELETE_REFERENCE),
        (("editable_poly", "Remove"),),
        "remove selected components",
    ),
    "model.split_edges": _operation_binding(
        "model.split_edges",
        frozenset({ContextLevel.EDGE}),
        selection_change_check,
        (EDITABLEPOLY_REFERENCE,),
        (("editable_poly", "splitEdges"),),
        "split edges",
    ),
    "model.break_vertices": _operation_binding(
        "model.break_vertices",
        frozenset({ContextLevel.VERTEX}),
        selection_change_check,
        (EDITABLEPOLY_REFERENCE,),
        (("editable_poly", "breakVerts"),),
        "break vertices",
    ),
    "model.planar_x": _operation_binding(
        "model.planar_x",
        _SUBOBJECT_LEVELS,
        planar_check,
        (EDITABLEPOLY_REFERENCE,),
        (("editable_poly", "MakePlanarIn"),),
        "make planar X",
    ),
    "model.planar_y": _operation_binding(
        "model.planar_y",
        _SUBOBJECT_LEVELS,
        planar_check,
        (EDITABLEPOLY_REFERENCE,),
        (("editable_poly", "MakePlanarIn"),),
        "make planar Y",
    ),
    "model.planar_z": _operation_binding(
        "model.planar_z",
        _SUBOBJECT_LEVELS,
        planar_check,
        (EDITABLEPOLY_REFERENCE,),
        (("editable_poly", "MakePlanarIn"),),
        "make planar Z",
    ),
    "model.flip_normals": _operation_binding(
        "model.flip_normals",
        frozenset({ContextLevel.POLYGON, ContextLevel.ELEMENT}),
        flip_check,
        (EDITABLEPOLY_REFERENCE,),
        (("editable_poly", "flipNormals"),),
        "flip normals",
    ),
    "model.cut": NativeBinding(
        action_id="model.cut",
        handler="mode",
        launch_kind="interactive",
        allowed_levels=frozenset(
            {ContextLevel.VERTEX, ContextLevel.EDGE, ContextLevel.POLYGON}
        ),
        documentation=(EDITABLEPOLY_REFERENCE, PYMXS_REFERENCE),
        check=cut_check,
        required_members=(
            ("editable_poly", "toggleCommandMode"),
            ("editable_poly", "getCommandMode"),
            ("editable_poly", "exitCommandModes"),
        ),
    ),
    "model.target_weld": NativeBinding(
        action_id="model.target_weld",
        handler="mode",
        launch_kind="interactive",
        allowed_levels=frozenset({ContextLevel.VERTEX}),
        documentation=(EDITABLEPOLY_REFERENCE, PYMXS_REFERENCE),
        check=target_weld_check,
        required_members=(
            ("editable_poly", "toggleCommandMode"),
            ("editable_poly", "getCommandMode"),
        ),
    ),
    "model.exit_tool": NativeBinding(
        action_id="model.exit_tool",
        handler="exit",
        launch_kind="interactive",
        allowed_levels=_SUBOBJECT_LEVELS,
        documentation=(EDITABLEPOLY_REFERENCE,),
        check=exit_tool_check,
        required_members=(("editable_poly", "exitCommandModes"),),
    ),
}


def _validated_parameters(
    action_id: str, context: ContextSnapshot, parameters: Optional[dict]
) -> dict:
    return validate_parameters(action_id, context.level, parameters)


def launch_native(
    runtime: Any,
    action_id: str,
    context: ContextSnapshot,
    parameters: Optional[dict] = None,
    *,
    undo_factory: Optional[Callable[[bool, str], Any]] = None,
    target: Optional[MutableTarget] = None,
) -> LaunchResult:
    """Dispatch one fixed, documented native binding.

    Command/level routes are non-mutating.  ``operation`` routes require the
    adapter's exact :class:`MutableTarget`, are parameter-validated a second
    time, and run inside exactly one owned undo chunk.  Unknown actions,
    unsupported contexts, missing members, false native returns, and failures
    never become success.
    """

    if not isinstance(action_id, str) or action_id not in NATIVE_BINDINGS:
        return LaunchResult(
            status="blocked",
            action_id=str(action_id),
            message="Action is not an implemented native binding.",
        )

    binding = NATIVE_BINDINGS[action_id]
    if context.level not in binding.allowed_levels:
        return LaunchResult(
            status="blocked",
            action_id=action_id,
            message="Action is not available in the %s context." % context.level.value,
        )

    availability = binding.check(context)
    if not availability.enabled:
        return LaunchResult(
            status="blocked", action_id=action_id, message=availability.reason
        )

    if binding.handler in ("command", "level"):
        try:
            message = binding.dispatch(runtime, context)  # type: ignore[misc]
        except Exception as exc:
            return LaunchResult(
                status="error",
                action_id=action_id,
                message="Native dispatch failed (%s): %s"
                % (type(exc).__name__, exc),
            )
        return LaunchResult(
            status="launched",
            action_id=action_id,
            message=message or "Native command dispatched.",
        )

    if binding.handler == "operation":
        if target is None:
            return LaunchResult(
                status="blocked",
                action_id=action_id,
                message=(
                    "Exact mutable target and component indices are required for "
                    "this operation."
                ),
            )
        if target.level != context.level:
            return LaunchResult(
                status="blocked",
                action_id=action_id,
                message="Operation level does not match the revalidated context.",
            )
        try:
            _validated_parameters(action_id, context, parameters)
        except ParameterError as exc:
            return LaunchResult(
                status="blocked",
                action_id=action_id,
                message="Validated parameters are required: %s" % exc,
            )

        if action_id == "model.extrude" and float(parameters["height"]) == 0:
            return LaunchResult(
                status="noop",
                action_id=action_id,
                message=(
                    "Extrude height is zero; no geometry change was requested."
                ),
            )

        def edit():
            return execute_operation(
                runtime,
                target.node,
                action_id,
                target.level,
                target.indices,
                parameters,
            )

        outcome = run_undoable(
            binding.undo_label,
            edit,
            undo_factory,
            hold_guard=lambda: hold_reason(runtime),
        )
        if outcome.status == "error":
            return LaunchResult(
                status="error", action_id=action_id, message=outcome.message
            )
        return LaunchResult(
            status=outcome.status,
            action_id=action_id,
            message=outcome.message,
        )

    # Mode ownership and Exit Tool are deliberately handled by MaxHostAdapter
    # so that foreign modes and stale targets are never toggled or exited here.
    return LaunchResult(
        status="blocked",
        action_id=action_id,
        message="Native mode ownership is handled by the MaxHostAdapter.",
    )


__all__ = [
    "BITARRAY_REFERENCE",
    "CATALOG_ACTION_IDS",
    "CATALOG_ACTION_IDS_SET",
    "COMMANDS_REFERENCE",
    "DEPENDENT_NODES_REFERENCE",
    "EDITABLEPOLY_PROPERTIES_REFERENCE",
    "EDITABLEPOLY_REFERENCE",
    "FIXED_MAX_COMMAND_LITERALS",
    "MODIFY_PANEL_REFERENCE",
    "NATIVE_BINDINGS",
    "NativeBinding",
    "POLYOP_COLLAPSE_REFERENCE",
    "POLYOP_DELETE_REFERENCE",
    "POLYOP_EXTRUDE_REFERENCE",
    "POLYOP_OPEN_EDGES_REFERENCE",
    "POLYOP_SELECTION_REFERENCE",
    "POLYOP_WELD_REFERENCE",
    "PYMXS_REFERENCE",
    "PYMXS_UNDO_REFERENCE",
    "THREADING_REFERENCE",
    "UNVERIFIED_SUBOBJECT_PREFIX",
    "frame_selection_check",
    "launch_native",
    "object_transform_check",
    "topology_target_reason",
    "unverified_reason",
    "unsupported_subobject_target",
]

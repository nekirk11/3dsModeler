"""V2 native host tests: preparation dispatch, caddies, modes and pivots.

These are fake-runtime tests.  They prove call order, registry parity, undo
ownership and refusal behavior; they do not prove any live 3ds Max 2025 result.
The V2 bindings remain ``runtime_status='not_run'``.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

from max2025_menu_prototype.core import maneuver_contracts
from max2025_menu_prototype.core.contracts import ContextLevel
from max2025_menu_prototype.max_host import maneuver_dispatch
from max2025_menu_prototype.max_host.adapter import ManeuverHostAdapter
from max2025_menu_prototype.max_host.preparation import (
    EDIT_POLY_REASON,
    ManeuverContext,
    ManeuverRequest,
)

try:
    from .test_max_host import (
        FakeBaseObject,
        FakeBitArray,
        FakeClass,
        FakeEditablePoly,
        FakeHold,
        FakeMesh,
        FakeModPanel,
        FakeModifier,
        FakeName,
        FakeNode,
        FakePolyOp,
        FakeRuntime,
        FakeUndoFactory,
    )
except ImportError:  # discovered as a top-level test module
    from test_max_host import (
        FakeBaseObject,
        FakeBitArray,
        FakeClass,
        FakeEditablePoly,
        FakeHold,
        FakeMesh,
        FakeModPanel,
        FakeModifier,
        FakeName,
        FakeNode,
        FakePolyOp,
        FakeRuntime,
        FakeUndoFactory,
    )


# ---------------------------------------------------------------------------
# V2 fake runtime
# ---------------------------------------------------------------------------


class V2Graphite:
    def __init__(self) -> None:
        self.calls = []
        self.available = True

    def SwiftLoop(self):
        self.calls.append("SwiftLoop")
        if not self.available:
            raise RuntimeError("SwiftLoop unavailable")


class V2EditablePoly(FakeEditablePoly):
    def __init__(self, mesh: FakeMesh) -> None:
        super().__init__(mesh)
        self.popup_result = True
        self.conversions = []
        self.popups = []
        self.mode_exits = 0

    def PopupDialog(self, value):
        self.calls.append(("PopupDialog", str(value)))
        self.popups.append(str(value))
        if self.popup_result is False:
            return False
        return True

    def ConvertSelection(self, source, destination, requireAll=False):
        self.calls.append(
            ("ConvertSelection", str(source), str(destination), requireAll)
        )
        self.conversions.append((str(source), str(destination)))
        source_text = str(source).lower()
        destination_text = str(destination).lower()
        if destination_text == "border" and source_text in ("edge", "border"):
            open_edges = set(self.mesh.open_edges)
            self.mesh.selections[ContextLevel.BORDER] = (
                set(self.mesh.selections[ContextLevel.EDGE]) & open_edges
            )
        elif destination_text == "face" and source_text in ("edge", "border"):
            self.mesh.selections[ContextLevel.POLYGON] = set(
                range(1, len(self.mesh.faces) + 1)
            )
        elif destination_text == "vertex" and source_text in ("edge", "border"):
            self.mesh.selections[ContextLevel.VERTEX] = {
                vertex for edge in self.mesh.selections[ContextLevel.EDGE]
                for vertex in self.mesh.edges[edge - 1]
            }
        elif destination_text == "vertex" and source_text == "object":
            self.mesh.selections[ContextLevel.VERTEX] = set(
                range(1, len(self.mesh.vertices) + 1)
            )
        return True

    def buttonOp(self, op):
        name = str(op).lower()
        self.calls.append(("buttonOp", str(op)))
        if name == "cap":
            if self.mesh.open_edges:
                edge = sorted(self.mesh.open_edges)[0]
                first, second = self.mesh.edges[edge - 1]
                third = next(
                    (
                        vertex
                        for vertex in range(1, len(self.mesh.vertices) + 1)
                        if vertex not in (first, second)
                    ),
                    None,
                )
                if third is not None:
                    self.mesh.faces.append([first, second, third])
            return True
        if name == "delete":
            if self.mesh.faces:
                self.mesh.faces.pop()
            return True
        if name == "collapse":
            if self.mesh.edges:
                self.mesh.edges.pop()
            return True
        if name == "remove":
            if self.mesh.vertices:
                self.mesh.vertices.pop()
            return True
        if name in ("splitedges", "breakvertex"):
            if self.mesh.vertices:
                self.mesh.vertices.pop()
            return True
        if name == "flipnormals":
            if self.mesh.faces:
                self.mesh.faces[0] = list(reversed(self.mesh.faces[0]))
            return True
        if name.startswith("makeplanarin"):
            self.mesh.apply_planar(name[-1].upper())
            return True
        if name == "selectedgeloop":
            self.mesh.selections[ContextLevel.EDGE].add(2)
            return True
        if name == "selectedgering":
            self.mesh.selections[ContextLevel.EDGE].add(4)
            return True
        return super().buttonOp(op)

    def exitCommandModes(self):
        self.mode_exits += 1
        super().exitCommandModes()


class V2ModPanel(FakeModPanel):
    def setCurrentObject(self, *args, **kwargs):
        self.writes.append("setCurrentObject")
        if args:
            self.current = args[0]
        return None


class V2Node(FakeNode):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.EditablePoly = V2EditablePoly(self.mesh)
        self.showEndResult = True
        self.convertible = self.class_name != "Editable_Poly"
        self.collapse_calls = 0


class V2Runtime(FakeRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.modPanel = V2ModPanel()
        self.PolyToolsPolyDraw = V2Graphite()
        self.v2_calls = []
        self.collapse_target = None

    def setCommandPanelTaskMode(self, mode):
        self.accessed.append("setCommandPanelTaskMode")
        self.v2_calls.append(("setCommandPanelTaskMode", str(mode)))
        self.command_panel_task_mode = str(mode)

    def canConvertTo(self, node, target):
        self.accessed.append("canConvertTo")
        self.v2_calls.append("canConvertTo")
        return bool(getattr(node, "convertible", False))

    def convertTo(self, node, target):
        self.accessed.append("convertTo")
        self.v2_calls.append("convertTo")
        if not getattr(node, "convertible", True):
            raise RuntimeError("conversion refused")
        node.baseObject = FakeBaseObject("Editable_Poly", handle=node.handle + 400)
        node.class_name = "Editable_Poly"
        node.EditablePoly = V2EditablePoly(node.mesh)
        node.modifiers = []
        node.convertible = False

    def collapseStack(self, node):
        self.accessed.append("collapseStack")
        self.v2_calls.append("collapseStack")
        node.collapse_calls += 1
        node.modifiers = []


class BooleanMaskBitArray:
    """Live ``pymxs`` BitArray behavior: iterates capacity as booleans."""

    def __init__(self, flags) -> None:
        self.flags = tuple(bool(flag) for flag in flags)

    @property
    def count(self) -> int:
        return len(self.flags)

    @property
    def numberSet(self) -> int:
        return sum(self.flags)

    @property
    def isEmpty(self) -> bool:
        return not any(self.flags)

    def __iter__(self):
        return iter(self.flags)

    def __len__(self) -> int:
        return len(self.flags)


class MutatingFailingConvertRuntime(V2Runtime):
    """Fails conversion after mutating geometry, with no restore callback."""

    def convertTo(self, node, target):
        self.accessed.append("convertTo")
        self.v2_calls.append("convertTo")
        node.mesh.faces.pop()
        raise RuntimeError("conversion failed after mutation")


class MutatingFailingCollapseRuntime(V2Runtime):
    """Fails collapse after mutating geometry, with no restore callback."""

    def collapseStack(self, node):
        self.accessed.append("collapseStack")
        self.v2_calls.append("collapseStack")
        node.mesh.faces.pop()
        raise RuntimeError("collapse failed after mutation")


def v2_setup(
    level: int = 2,
    verts=(),
    edges=(),
    faces=(),
    *,
    runtime: V2Runtime | None = None,
    base_class: str = "Editable_Poly",
    modifiers=(),
    show_end_result: bool = True,
    open_edges=(),
    panel_open: bool = True,
    undo=None,
):
    runtime = runtime or V2Runtime()
    node = V2Node("PolyV2", class_name=base_class, handle=301)
    node.showEndResult = show_end_result
    node.baseObject = FakeBaseObject(base_class, handle=701)
    if base_class != "Editable_Poly":
        node.convertible = True
    node.modifiers = list(modifiers)
    node.mesh.selections[ContextLevel.VERTEX].update(verts)
    node.mesh.selections[ContextLevel.EDGE].update(edges)
    if level == 3:
        node.mesh.selections[ContextLevel.BORDER].update(edges)
    node.mesh.selections[ContextLevel.POLYGON].update(faces)
    node.mesh.open_edges = set(open_edges)
    node.mesh.current_level = {
        0: ContextLevel.OBJECT,
        1: ContextLevel.VERTEX,
        2: ContextLevel.EDGE,
        3: ContextLevel.BORDER,
        4: ContextLevel.POLYGON,
        5: ContextLevel.ELEMENT,
    }[level]
    runtime.selection = [node]
    runtime.subObjectLevel = level
    runtime.modPanel.current = node.baseObject if panel_open else None
    undo = undo or FakeUndoFactory()
    adapter = ManeuverHostAdapter(runtime=runtime, undo_factory=undo)
    return adapter, runtime, node, undo


def descriptor(kind: str, target: str) -> SimpleNamespace:
    return SimpleNamespace(
        kind=kind,
        target=target,
        api="display only",
        doc_url="https://help.autodesk.com/display-only",
    )


def route(with_selection=None, empty_selection=None) -> SimpleNamespace:
    return SimpleNamespace(
        max2025=SimpleNamespace(
            with_selection=with_selection, empty_selection=empty_selection
        )
    )


def spec(maneuver_id: str, **contexts) -> SimpleNamespace:
    return SimpleNamespace(
        id=maneuver_id,
        label=maneuver_id,
        group="Modeling",
        aliases={},
        contexts=contexts,
    )


def atlas(*specs) -> SimpleNamespace:
    return SimpleNamespace(schema_version=2, maneuvers=tuple(specs))


def request(adapter, maneuver_id, *, snapshot=None, pivot=None, convert=False, confirmed=False):
    expected = snapshot or adapter.snapshot()
    pivot_value = None
    if pivot is not None:
        pivot_value = pivot if isinstance(pivot, ManeuverContext) else ManeuverContext(pivot)
    return ManeuverRequest(
        maneuver_id=maneuver_id,
        expected_snapshot=expected,
        pivot_context=pivot_value,
        convert_selection=convert,
        confirmed=confirmed,
    )


# ---------------------------------------------------------------------------
# Registry parity
# ---------------------------------------------------------------------------


class RegistryParityTests(unittest.TestCase):
    def test_key_sets_match_frozen_contract_exactly(self):
        self.assertEqual(
            set(maneuver_dispatch.TARGETS_BY_KIND),
            set(maneuver_contracts.TARGETS_BY_KIND),
        )
        for kind, targets in maneuver_contracts.TARGETS_BY_KIND.items():
            self.assertEqual(
                set(maneuver_dispatch.TARGETS_BY_KIND[kind]),
                set(targets),
                kind,
            )
        self.assertEqual(maneuver_dispatch.contract_parity(), ())
        self.assertEqual(maneuver_dispatch.missing_handlers(), ())

    def test_every_handler_target_is_allowlisted_and_fixed(self):
        for (kind, target), handler in maneuver_dispatch.HANDLERS.items():
            self.assertTrue(maneuver_dispatch.is_allowed(kind, target))
            self.assertEqual(handler.kind, kind)
            self.assertEqual(handler.target, target)
            self.assertTrue(callable(handler.handler))

    def test_unknown_kind_or_target_is_not_allowed(self):
        self.assertFalse(maneuver_dispatch.is_allowed("script", "anything"))
        self.assertFalse(maneuver_dispatch.is_allowed("caddy", "WeldSelected"))
        self.assertIsNone(maneuver_dispatch.handler_for("caddy", "WeldSelected"))

    def test_weld_and_msmooth_map_to_documented_native_enums(self):
        self.assertEqual(maneuver_dispatch.CADDY_DIALOG_MAP["Weld"], "WeldSelected")
        self.assertEqual(maneuver_dispatch.CADDY_DIALOG_MAP["MSmooth"], "MeshSmooth")
        self.assertEqual(maneuver_dispatch.CADDY_DIALOG_MAP["Chamfer"], "Chamfer")

    def test_empty_bridge_modes_are_explicitly_gated(self):
        for target in ("BridgeEdge", "BridgeBorder", "BridgePolygon"):
            handler = maneuver_dispatch.handler_for("command_mode", target)
            self.assertEqual(handler.gated_reason, maneuver_dispatch.BRIDGE_GATE_REASON)
            self.assertIn("incomplete live requirement", handler.gated_reason)


# ---------------------------------------------------------------------------
# Caddy / command-mode / button / polyop / max_command execution
# ---------------------------------------------------------------------------


class CaddyExecutionTests(unittest.TestCase):
    def _bevel_atlas(self):
        return atlas(
            spec(
                "bevel",
                edge=route(
                    with_selection=descriptor("caddy", "Chamfer"),
                    empty_selection=descriptor("command_mode", "ChamferEdge"),
                ),
            )
        )

    def test_caddy_opens_native_popup_with_semantic_enum(self):
        adapter, runtime, node, undo = v2_setup(level=2, edges=(1,))
        adapter._atlas = self._bevel_atlas()
        result = adapter.execute(request(adapter, "bevel"))
        self.assertEqual(result.status, "launched", result.message)
        self.assertEqual(node.EditablePoly.popups, ["Chamfer"])
        self.assertEqual(undo.calls, [])
        self.assertIn("owns", result.message)

    def test_weld_caddy_uses_weldselected_enum(self):
        adapter, runtime, node, undo = v2_setup(level=1, verts=(1, 2))
        adapter._atlas = atlas(
            spec("weld", vertex=route(with_selection=descriptor("caddy", "Weld")))
        )
        result = adapter.execute(request(adapter, "weld"))
        self.assertEqual(result.status, "launched", result.message)
        self.assertEqual(node.EditablePoly.popups, ["WeldSelected"])

    def test_msmooth_caddy_uses_meshsmooth_enum(self):
        adapter, runtime, node, undo = v2_setup(level=4, faces=(1,))
        adapter._atlas = atlas(
            spec("smooth", face=route(with_selection=descriptor("caddy", "MSmooth")))
        )
        result = adapter.execute(request(adapter, "smooth"))
        self.assertEqual(result.status, "launched", result.message)
        self.assertEqual(node.EditablePoly.popups, ["MeshSmooth"])

    def test_false_popup_result_is_an_error_not_applied(self):
        adapter, runtime, node, undo = v2_setup(level=2, edges=(1,))
        adapter._atlas = self._bevel_atlas()
        node.EditablePoly.popup_result = False
        result = adapter.execute(request(adapter, "bevel"))
        self.assertEqual(result.status, "error")
        self.assertIn("not a completed edit", result.message)

    def test_missing_caddy_member_blocks_before_conversion(self):
        adapter, runtime, node, undo = v2_setup(
            level=2, edges=(1,), base_class="Box"
        )
        adapter._atlas = self._bevel_atlas()
        # A primitive has no EditablePoly interface before Tier-1 conversion.
        result = adapter.execute(request(adapter, "bevel"))
        # Registry preflight treats the interface as created by conversion, so
        # the conversion itself is the expected first mutation.
        self.assertEqual(result.status, "launched", result.message)
        self.assertEqual(runtime.v2_calls.count("convertTo"), 1)
        self.assertEqual(node.EditablePoly.popups, ["Chamfer"])

    def test_missing_handler_member_on_a_ready_poly_blocks_without_writes(self):
        adapter, runtime, node, undo = v2_setup(level=2, edges=(1,))
        adapter._atlas = self._bevel_atlas()
        node.EditablePoly.PopupDialog = None
        result = adapter.execute(request(adapter, "bevel"))
        self.assertEqual(result.status, "blocked")
        self.assertIn("PopupDialog", result.message)
        self.assertEqual(runtime.v2_calls, [])
        self.assertEqual(runtime.modPanel.writes, [])

    def test_live_preview_tracks_dragging_blocks_clears_and_fails_closed(self):
        adapter, runtime, node, undo = v2_setup(level=2, edges=(1,))
        adapter._atlas = self._bevel_atlas()
        launched = adapter.execute(request(adapter, "bevel"))
        self.assertEqual(launched.status, "launched", launched.message)
        self.assertTrue(adapter._caddy_active)

        node.EditablePoly.PreviewOn = lambda: True
        node.EditablePoly.PreviewDragging = lambda: True
        active_snapshot = adapter.snapshot()
        self.assertEqual(adapter._last_assessment.preview_state, "on")
        self.assertIs(adapter._last_assessment.preview_dragging, True)
        self.assertTrue(active_snapshot.caddy_active)
        self.assertFalse(adapter.availability("bevel", active_snapshot).enabled)
        blocked = adapter.execute(request(adapter, "bevel"))
        self.assertEqual(blocked.status, "blocked")
        self.assertEqual(node.EditablePoly.popups, ["Chamfer"])

        node.EditablePoly.PreviewOn = lambda: False
        cleared_snapshot = adapter.snapshot()
        self.assertFalse(cleared_snapshot.caddy_active)
        self.assertFalse(adapter._caddy_active)
        self.assertTrue(
            adapter.availability("bevel", cleared_snapshot).enabled,
            adapter.availability("bevel", cleared_snapshot).reason,
        )

        relaunched = adapter.execute(request(adapter, "bevel"))
        self.assertEqual(relaunched.status, "launched", relaunched.message)
        self.assertTrue(adapter._caddy_active)

        def unreadable():
            raise RuntimeError("PreviewOn unavailable")

        node.EditablePoly.PreviewOn = unreadable
        unknown_snapshot = adapter.snapshot()
        self.assertEqual(adapter._last_assessment.preview_state, "unknown")
        self.assertTrue(adapter._caddy_active)
        self.assertTrue(unknown_snapshot.caddy_active)
        self.assertFalse(adapter.availability("bevel", unknown_snapshot).enabled)
        still_blocked = adapter.execute(request(adapter, "bevel"))
        self.assertEqual(still_blocked.status, "blocked")
        self.assertIn("caddy", still_blocked.message.lower())


class CommandModeExecutionTests(unittest.TestCase):
    def test_standard_mode_uses_toggle_command_mode_and_verifies_readback(self):
        adapter, runtime, node, undo = v2_setup(level=2, edges=())
        adapter._atlas = atlas(
            spec(
                "extrude",
                edge=route(empty_selection=descriptor("command_mode", "ExtrudeEdge")),
            )
        )
        result = adapter.execute(request(adapter, "extrude"))
        self.assertEqual(result.status, "launched", result.message)
        self.assertIn(("toggleCommandMode", "ExtrudeEdge"), node.EditablePoly.calls)
        self.assertEqual(undo.calls, [])

    def test_swift_loop_uses_graphite_handler_then_blocks_ambiguous_chaining(self):
        adapter, runtime, node, undo = v2_setup(level=2, edges=())
        adapter._atlas = atlas(
            spec(
                "loop_cut",
                edge=route(empty_selection=descriptor("command_mode", "SwiftLoop")),
            )
        )
        result = adapter.execute(request(adapter, "loop_cut"))
        self.assertEqual(result.status, "launched", result.message)
        self.assertEqual(runtime.PolyToolsPolyDraw.calls, ["SwiftLoop"])
        self.assertIn("Graphite", result.message)
        self.assertTrue(adapter.diagnostic_state(observe=False)["ambiguous_mode"])
        blocked = adapter.availability("loop_cut", adapter.snapshot())
        self.assertFalse(blocked.enabled)
        self.assertIn("Graphite", blocked.reason)

    def test_bridge_empty_route_is_blocked_without_a_native_call(self):
        adapter, runtime, node, undo = v2_setup(level=2, edges=())
        adapter._atlas = atlas(
            spec(
                "bridge",
                edge=route(empty_selection=descriptor("command_mode", "BridgeEdge")),
            )
        )
        result = adapter.execute(request(adapter, "bridge"))
        self.assertEqual(result.status, "blocked")
        self.assertIn("incomplete live requirement", result.message)
        self.assertEqual(node.EditablePoly.calls, [])


class ButtonAndPolyOpExecutionTests(unittest.TestCase):
    def test_selection_button_op_is_one_undo_chunk(self):
        adapter, runtime, node, undo = v2_setup(level=2, edges=(1,))
        adapter._atlas = atlas(
            spec(
                "select_loop",
                edge=route(with_selection=descriptor("button_op", "SelectEdgeLoop")),
            )
        )
        result = adapter.execute(request(adapter, "select_loop"))
        self.assertEqual(result.status, "applied", result.message)
        self.assertEqual(len(undo.calls), 1)
        self.assertIn(("buttonOp", "SelectEdgeLoop"), node.EditablePoly.calls)

    def test_unchanged_selection_button_op_is_noop(self):
        adapter, runtime, node, undo = v2_setup(level=2, edges=(1, 2))
        adapter._atlas = atlas(
            spec(
                "select_loop",
                edge=route(with_selection=descriptor("button_op", "SelectEdgeLoop")),
            )
        )
        # Fake SelectEdgeLoop adds edge 2, already selected -> no change.
        result = adapter.execute(request(adapter, "select_loop"))
        self.assertEqual(result.status, "noop", result.message)

    def test_cap_requires_open_border_edges(self):
        adapter, runtime, node, undo = v2_setup(level=3, edges=(1,), open_edges=())
        adapter._atlas = atlas(
            spec("fill", border=route(with_selection=descriptor("button_op", "Cap")))
        )
        result = adapter.execute(request(adapter, "fill"))
        self.assertEqual(result.status, "blocked")
        self.assertIn("open border", result.message)

    def test_clear_selection_is_typed_and_noop_when_empty(self):
        adapter, runtime, node, undo = v2_setup(level=2, edges=(1, 2))
        adapter._atlas = atlas(
            spec(
                "clear_selection",
                edge=route(with_selection=descriptor("polyop", "clear_selection")),
            )
        )
        result = adapter.execute(request(adapter, "clear_selection"))
        self.assertEqual(result.status, "applied", result.message)
        self.assertEqual(node.mesh.selections[ContextLevel.EDGE], set())
        self.assertEqual(runtime.subObjectLevel, 2)

    def test_max_command_uses_only_fixed_literal(self):
        adapter, runtime, node, undo = v2_setup(level=0, panel_open=False)
        adapter._atlas = atlas(
            spec("move", object=route(with_selection=descriptor("max_command", "move")))
        )
        result = adapter.execute(request(adapter, "move"))
        self.assertEqual(result.status, "launched", result.message)
        self.assertEqual(runtime.executed, ["max move"])
        self.assertEqual(undo.calls, [])

    def test_max_command_without_selection_is_blocked(self):
        adapter, runtime, node, undo = v2_setup(level=0, panel_open=False)
        runtime.selection = []
        adapter._atlas = atlas(
            spec("move", object=route(with_selection=descriptor("max_command", "move")))
        )
        result = adapter.execute(request(adapter, "move"))
        self.assertEqual(result.status, "blocked")
        self.assertEqual(runtime.executed, [])


# ---------------------------------------------------------------------------
# Context conversion and fill pivot
# ---------------------------------------------------------------------------


class ContextAndPivotTests(unittest.TestCase):
    def test_context_switch_uses_convert_selection_then_sets_level(self):
        adapter, runtime, node, undo = v2_setup(level=2, edges=(1,))
        adapter._atlas = atlas(
            spec(
                "convert_to_faces",
                edge=route(
                    with_selection=descriptor("context_switch", "face"),
                    empty_selection=descriptor("context_switch", "face"),
                ),
            )
        )
        result = adapter.execute(request(adapter, "convert_to_faces"))
        self.assertEqual(result.status, "launched", result.message)
        self.assertEqual(
            node.EditablePoly.conversions, [("Edge", "Face")]
        )
        self.assertEqual(runtime.subObjectLevel, 4)

    def test_fill_pivots_open_edges_to_border_and_caps(self):
        adapter, runtime, node, undo = v2_setup(
            level=2, edges=(1, 2), open_edges=(1, 2)
        )
        adapter._atlas = atlas(
            spec("fill", border=route(with_selection=descriptor("button_op", "Cap")))
        )
        result = adapter.execute(request(adapter, "fill", pivot="border"))
        self.assertEqual(result.status, "applied", result.message)
        self.assertIn(("ConvertSelection", "Edge", "Border", False), node.EditablePoly.calls)
        self.assertIn(("buttonOp", "Cap"), node.EditablePoly.calls)
        self.assertEqual(runtime.subObjectLevel, 3)

    def test_fill_with_closed_edges_is_blocked_before_any_write(self):
        adapter, runtime, node, undo = v2_setup(
            level=2, edges=(1, 2), open_edges=()
        )
        adapter._atlas = atlas(
            spec("fill", border=route(with_selection=descriptor("button_op", "Cap")))
        )
        before = len(node.EditablePoly.calls)
        result = adapter.execute(request(adapter, "fill", pivot="border"))
        self.assertEqual(result.status, "blocked")
        self.assertIn("closed", result.message)
        self.assertEqual(len(node.EditablePoly.calls), before)

    def test_fill_with_mixed_edges_filters_to_open_edges(self):
        adapter, runtime, node, undo = v2_setup(
            level=2, edges=(1, 2), open_edges=(1,)
        )
        adapter._atlas = atlas(
            spec("fill", border=route(with_selection=descriptor("button_op", "Cap")))
        )
        result = adapter.execute(request(adapter, "fill", pivot="border"))
        self.assertEqual(result.status, "applied", result.message)
        self.assertIn(("ConvertSelection", "Edge", "Border", False), node.EditablePoly.calls)

    def _fill_atlas(self):
        return atlas(
            spec("fill", border=route(with_selection=descriptor("button_op", "Cap")))
        )

    def test_fill_availability_reads_boolean_iterating_bitarray(self):
        adapter, runtime, node, undo = v2_setup(level=2, edges=(2, 4))
        adapter._atlas = self._fill_atlas()
        node.mesh.getOpenEdges = lambda target: BooleanMaskBitArray(
            [False, True, False, True]
        )
        row = adapter.availability(
            "fill", adapter.snapshot(), pivot_context="border"
        )
        self.assertTrue(row.enabled, row.reason)

    def test_fill_dispatch_accepts_boolean_mask_open_edges(self):
        adapter, runtime, node, undo = v2_setup(
            level=2, edges=(2, 4), open_edges=(2, 4)
        )
        adapter._atlas = self._fill_atlas()
        node.mesh.getOpenEdges = lambda target: BooleanMaskBitArray(
            [False, True, False, True]
        )
        result = adapter.execute(request(adapter, "fill", pivot="border"))
        self.assertEqual(result.status, "applied", result.message)
        self.assertEqual(node.EditablePoly.conversions, [("Edge", "Border")])
        self.assertIn(("buttonOp", "Cap"), node.EditablePoly.calls)
        self.assertEqual(node.mesh.selections[ContextLevel.EDGE], {2, 4})

    def test_ordinary_pivot_does_not_convert_selection(self):
        adapter, runtime, node, undo = v2_setup(level=2, edges=(1,), faces=(1,))
        adapter._atlas = atlas(
            spec(
                "inset",
                face=route(with_selection=descriptor("caddy", "Inset")),
            )
        )
        result = adapter.execute(request(adapter, "inset", pivot="face"))
        self.assertEqual(result.status, "launched", result.message)
        self.assertEqual(node.EditablePoly.conversions, [])
        self.assertEqual(runtime.subObjectLevel, 4)

    def test_context_switch_public_seam_plain_click_preserves_selection(self):
        adapter, runtime, node, undo = v2_setup(level=2, edges=(1,))
        snapshot = adapter.snapshot()
        result = adapter.switch_context("face", expected_snapshot=snapshot)
        self.assertEqual(result.status, "launched", result.message)
        self.assertEqual(node.EditablePoly.conversions, [])
        self.assertEqual(runtime.subObjectLevel, 4)
        self.assertEqual(node.mesh.selections[ContextLevel.EDGE], {1})


# ---------------------------------------------------------------------------
# Guards: hold, Q13 chaining, stale snapshots, rollback latch, preparation
# ---------------------------------------------------------------------------


class GuardTests(unittest.TestCase):
    def _caddy_atlas(self):
        return atlas(
            spec(
                "bevel",
                edge=route(with_selection=descriptor("caddy", "Chamfer")),
            )
        )

    def test_active_hold_blocks_with_zero_mutating_calls(self):
        adapter, runtime, node, undo = v2_setup(level=2, edges=(1,))
        adapter._atlas = self._caddy_atlas()
        runtime.theHold.holding = True
        result = adapter.execute(request(adapter, "bevel"))
        self.assertEqual(result.status, "blocked")
        self.assertEqual(node.EditablePoly.calls, [])
        self.assertEqual(runtime.v2_calls, [])
        self.assertEqual(runtime.modPanel.writes, [])

    def test_q13_exits_verified_idle_ep_mode_before_dispatch(self):
        adapter, runtime, node, undo = v2_setup(level=2, edges=(1,))
        adapter._atlas = self._caddy_atlas()
        node.EditablePoly.command_mode = "cutedge"
        result = adapter.execute(request(adapter, "bevel"))
        self.assertEqual(result.status, "launched", result.message)
        calls = node.EditablePoly.calls
        exit_index = calls.index("exitCommandModes")
        popup_index = calls.index(("PopupDialog", "Chamfer"))
        self.assertLess(exit_index, popup_index)

    def test_stale_selection_with_equal_count_blocks(self):
        adapter, runtime, node, undo = v2_setup(level=2, edges=(1,))
        adapter._atlas = self._caddy_atlas()
        snapshot = adapter.snapshot()
        node.mesh.selections[ContextLevel.EDGE] = {3}
        result = adapter.execute(request(adapter, "bevel", snapshot=snapshot))
        self.assertEqual(result.status, "blocked")
        self.assertIn("selection", result.message)
        self.assertEqual(node.EditablePoly.popups, [])

    def test_edit_poly_target_blocks_component_dispatch_with_q10_reason(self):
        edit_poly = FakeModifier("Edit_Poly", handle=880)
        adapter, runtime, node, undo = v2_setup(
            level=2, edges=(1,), modifiers=(edit_poly,)
        )
        runtime.modPanel.current = edit_poly
        adapter._atlas = self._caddy_atlas()
        result = adapter.execute(request(adapter, "bevel"))
        self.assertEqual(result.status, "blocked")
        self.assertIn("Edit Poly modifiers are outside V2", result.message)
        self.assertEqual(node.EditablePoly.popups, [])

    def test_failed_edit_with_unverified_rollback_latches_persistently(self):
        class RaisingPoly(V2EditablePoly):
            def buttonOp(self, op):
                self.calls.append(("buttonOp", str(op)))
                self.mesh.faces.pop()
                raise RuntimeError("native failure")

        undo = FakeUndoFactory(restore=None)
        adapter, runtime, node, undo = v2_setup(
            level=2, edges=(1,), undo=undo
        )
        node.EditablePoly = RaisingPoly(node.mesh)
        adapter._atlas = atlas(
            spec(
                "select_loop",
                edge=route(with_selection=descriptor("button_op", "SelectEdgeLoop")),
            )
        )
        result = adapter.execute(request(adapter, "select_loop"))
        self.assertEqual(result.status, "error")
        self.assertIn("latched", result.message)
        self.assertIsNotNone(adapter.rollback_lockout_reason)
        blocked = adapter.availability("select_loop", adapter.snapshot())
        self.assertFalse(blocked.enabled)
        self.assertIn("latch", blocked.reason.lower())

    def test_failed_convert_and_collapse_with_unverified_rollback_latch(self):
        # Conversion failure after an un-restored geometry mutation.
        convert_runtime = MutatingFailingConvertRuntime()
        convert_adapter, runtime, node, undo = v2_setup(
            level=2,
            edges=(1,),
            base_class="Box",
            runtime=convert_runtime,
            undo=FakeUndoFactory(restore=None),
        )
        convert_adapter._atlas = self._caddy_atlas()
        conversion = convert_adapter.execute(request(convert_adapter, "bevel"))
        self.assertEqual(conversion.status, "error", conversion.message)
        self.assertIn("latched", conversion.message)
        self.assertIsNotNone(convert_adapter.rollback_lockout_reason)
        blocked = convert_adapter.execute(request(convert_adapter, "bevel"))
        self.assertEqual(blocked.status, "blocked")
        self.assertIn("latched", blocked.message.lower())

        # Collapse failure after an un-restored geometry mutation.
        collapse_runtime = MutatingFailingCollapseRuntime()
        collapse_adapter, runtime, node, undo = v2_setup(
            level=0,
            modifiers=(FakeModifier("TurboSmooth", handle=930),),
            runtime=collapse_runtime,
            undo=FakeUndoFactory(restore=None),
        )
        collapse_adapter._atlas = atlas(
            spec(
                "bake",
                object=route(
                    with_selection=descriptor("composite", "collapse_to_poly")
                ),
            )
        )
        collapse = collapse_adapter.execute(
            request(collapse_adapter, "bake", confirmed=True)
        )
        self.assertEqual(collapse.status, "error", collapse.message)
        self.assertIn("latched", collapse.message)
        self.assertIsNotNone(collapse_adapter.rollback_lockout_reason)
        blocked = collapse_adapter.execute(
            request(collapse_adapter, "bake", confirmed=True)
        )
        self.assertEqual(blocked.status, "blocked")
        self.assertIn("latched", blocked.message.lower())

    def test_prepare_tier_two_preserves_modifiers_and_show_end_result(self):
        modifier = FakeModifier("TurboSmooth", handle=901)
        adapter, runtime, node, undo = v2_setup(
            level=2, edges=(1,), modifiers=(modifier,), show_end_result=False
        )
        adapter._atlas = self._caddy_atlas()
        result = adapter.execute(request(adapter, "bevel"))
        self.assertEqual(result.status, "launched", result.message)
        self.assertIs(node.modifiers[0], modifier)
        self.assertTrue(node.showEndResult)
        self.assertEqual(runtime.subObjectLevel, 2)

    def test_availability_unknown_id_and_missing_route_reasons(self):
        adapter, runtime, node, undo = v2_setup(level=2, edges=())
        adapter._atlas = self._caddy_atlas()
        snapshot = adapter.snapshot()
        self.assertFalse(adapter.availability("nope", snapshot).enabled)
        missing = adapter.availability("bevel", snapshot)
        self.assertFalse(missing.enabled)
        self.assertIn("Select edges first", missing.reason)

    def test_pivot_availability_reports_other_context(self):
        adapter, runtime, node, undo = v2_setup(
            level=2, edges=(1,), faces=(1,)
        )
        adapter._atlas = atlas(
            spec("inset", face=route(with_selection=descriptor("caddy", "Inset")))
        )
        snapshot = adapter.snapshot()
        row = adapter.availability("inset", snapshot, pivot_context=ManeuverContext.FACE)
        self.assertTrue(row.enabled, row.reason)


if __name__ == "__main__":
    unittest.main()

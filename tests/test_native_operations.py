"""Native Editable Poly mapping tests (fake runtime; not Max validation).

These tests assert the exact documented property/method names, selection
providers, boolean failure handling, setting restoration, and no-op/failure
distinctions.  They do not prove that 3ds Max 2025 accepts any call.
"""

from __future__ import annotations

import unittest

from max2025_menu_prototype.core.contracts import ContextLevel
from max2025_menu_prototype.max_host.operations import (
    GeometryReadError,
    OperationError,
    bitarray_count,
    bitarray_indices,
    execute_operation,
    read_geometry,
    read_settings,
    target_fingerprint,
    MutableTarget,
)


class FakeName:
    def __init__(self, text: str) -> None:
        self.text = text

    def __str__(self) -> str:
        return self.text

    def __repr__(self) -> str:
        return "FakeName(%r)" % self.text


class FakeArray:
    """Host array distinct from a plain Python list/tuple."""

    def __init__(self, *values) -> None:
        self.values = tuple(values)

    def __iter__(self):
        return iter(self.values)

    def __len__(self) -> int:
        return len(self.values)

    def __getitem__(self, index):
        # Native code may index a MAXScript array (capHolesByEdge does).
        return self.values[index]

    def __repr__(self) -> str:
        return "FakeArray(%r)" % (self.values,)


class FakeBitArray:
    """Models the documented numberSet versus count-capacity distinction."""

    def __init__(self, values=(), capacity: int = 0) -> None:
        self._values = tuple(sorted(set(int(value) for value in values)))
        self._capacity = max(capacity, self._values[-1] if self._values else 0)

    @property
    def count(self) -> int:
        return self._capacity

    @property
    def numberSet(self) -> int:
        return len(self._values)

    @property
    def isEmpty(self) -> bool:
        return not self._values

    def __iter__(self):
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __repr__(self) -> str:
        return "FakeBitArray(%r, capacity=%r)" % (self._values, self._capacity)


class FakeMesh:
    def __init__(self, level: ContextLevel) -> None:
        self.level = level
        self.vertices = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0),
                         (0.0, 1.0, 0.0)]
        self.edges = [(1, 2), (2, 3), (3, 4), (4, 1)]
        self.faces = [[1, 2, 3, 4]]
        self.selections = {
            ContextLevel.VERTEX: set(),
            ContextLevel.EDGE: set(),
            ContextLevel.BORDER: set(),
            ContextLevel.POLYGON: set(),
            ContextLevel.ELEMENT: set(),
        }
        self.open_edges = set()
        self.calls = []
        self.raw_args = []

    # -- topology -------------------------------------------------------
    def getNumVerts(self, node):
        self.calls.append("getNumVerts")
        return len(self.vertices)

    def getNumEdges(self, node):
        self.calls.append("getNumEdges")
        return len(self.edges)

    def getNumFaces(self, node):
        self.calls.append("getNumFaces")
        return len(self.faces)

    def getVert(self, node, index):
        self.calls.append("getVert")
        return self.vertices[index - 1]

    def getEdgeVerts(self, node, index):
        self.calls.append("getEdgeVerts")
        return self.edges[index - 1]

    def getFaceVerts(self, node, index):
        self.calls.append("getFaceVerts")
        return self.faces[index - 1]

    # -- selection ------------------------------------------------------
    def _selection(self, level):
        values = self.selections[level]
        if level == ContextLevel.ELEMENT:
            values = values or self.selections[ContextLevel.POLYGON]
        return FakeBitArray(values, capacity=len(self.vertices) + 20)

    def getVertSelection(self, node):
        self.calls.append("getVertSelection")
        return self._selection(ContextLevel.VERTEX)

    def getEdgeSelection(self, node):
        self.calls.append("getEdgeSelection")
        return FakeBitArray(
            self.selections[ContextLevel.EDGE] | self.selections[ContextLevel.BORDER]
        )

    def getFaceSelection(self, node):
        self.calls.append("getFaceSelection")
        return FakeBitArray(
            self.selections[ContextLevel.POLYGON]
            | self.selections[ContextLevel.ELEMENT]
        )

    def _set_selection(self, level, value):
        self.calls.append("set%sSelection" % level.value.title())
        if isinstance(value, FakeName):
            assert str(value) == "none"
            self.selections[level] = set()
        else:
            values = list(value)
            if values and isinstance(values[0], int):
                self.selections[level] = set(int(item) for item in values)
            else:
                self.selections[level] = set(values)

    def setVertSelection(self, node, value):
        self._set_selection(ContextLevel.VERTEX, value)

    def setEdgeSelection(self, node, value):
        self._set_selection(ContextLevel.EDGE, value)

    def setFaceSelection(self, node, value):
        self._set_selection(ContextLevel.POLYGON, value)

    # -- open borders ---------------------------------------------------
    def getOpenEdges(self, node):
        self.calls.append("getOpenEdges")
        return FakeBitArray(self.open_edges, capacity=len(self.edges))

    def getBorderFromEdge(self, node, edge):
        self.calls.append("getBorderFromEdge")
        return FakeBitArray(self.border_loops.get(int(edge), ()))

    def getVertsUsingEdge(self, node, edges):
        self.calls.append("getVertsUsingEdge")
        self.raw_args.append(("getVertsUsingEdge", edges))
        verts = set()
        for edge in edges:
            if 1 <= int(edge) <= len(self.edges):
                verts.update(self.edges[int(edge) - 1])
        return FakeBitArray(verts)

    # -- mutations ------------------------------------------------------
    def _grow_mesh(self, count: int = 1) -> None:
        for _ in range(count):
            index = len(self.vertices) + 1
            self.vertices.append((float(index), 0.0, 0.0))
            # Keep the fake invariant used by read_geometry: every face loop
            # has at least three distinct vertices.  The previous two-vertex
            # loop was an invalid fixture, not a real Max mesh.
            other = index - 1 if index - 1 not in (1, index) else 2
            loop = [1, other, index]
            if len(set(loop)) < 3:
                loop = [1, 2, index]
            self.faces.append(loop)

    def extrudeFaces(self, node, face_list, amount):
        self.calls.append(("extrudeFaces", tuple(face_list), amount))
        self.raw_args.append(("extrudeFaces", face_list))
        self._grow_mesh(len(tuple(face_list)))

    def bevelFaces(self, node, face_list, height, outline):
        self.calls.append(("bevelFaces", tuple(face_list), height, outline))
        self.raw_args.append(("bevelFaces", face_list))
        self._grow_mesh(len(tuple(face_list)))

    def capHolesByEdge(self, node, edges):
        self.calls.append(("capHolesByEdge", tuple(edges)))
        self.raw_args.append(("capHolesByEdge", edges))
        edge = self.edges[int(edges[0]) - 1]
        third = next(
            (vertex for vertex in range(1, len(self.vertices) + 1)
             if vertex not in edge),
            None,
        )
        if third is not None:
            self.faces.append([int(edge[0]), int(edge[1]), third])
        return True

    def collapseVerts(self, node, values):
        self.calls.append(("collapseVerts", tuple(values)))
        self.raw_args.append(("collapseVerts", values))
        self.vertices = self.vertices[:-1]
        self.faces = self.faces[:-1]

    def collapseEdges(self, node, values):
        self.calls.append(("collapseEdges", tuple(values)))
        self.raw_args.append(("collapseEdges", values))
        self.edges = self.edges[:-1]
        self.vertices = self.vertices[:-1]

    def collapseFaces(self, node, values):
        self.calls.append(("collapseFaces", tuple(values)))
        self.raw_args.append(("collapseFaces", values))
        self.faces = self.faces[:-1]

    def deleteVerts(self, node, values):
        self.calls.append(("deleteVerts", tuple(values)))
        self.raw_args.append(("deleteVerts", values))
        self.vertices = self.vertices[:-1]
        self.faces = self.faces[:-1]

    def deleteIsoVerts(self, node):
        self.calls.append("deleteIsoVerts")
        if self.vertices:
            self.vertices = self.vertices[:-1]

    def deleteEdges(self, node, values, delIsoVerts=True):
        self.calls.append(("deleteEdges", tuple(values), delIsoVerts))
        self.raw_args.append(("deleteEdges", values))
        self.edges = self.edges[:-1]

    def deleteFaces(self, node, values, delIsoVerts=True):
        self.calls.append(("deleteFaces", tuple(values), delIsoVerts))
        self.raw_args.append(("deleteFaces", values))
        self.faces = self.faces[:-1]

    def weldVertsByThreshold(self, node, values):
        self.calls.append(("weldVertsByThreshold", tuple(values)))
        self.raw_args.append(("weldVertsByThreshold", values))
        self.vertices = self.vertices[:-1]

    def apply_planar(self, axis):
        self.calls.append(("planar", axis))
        self.vertices = [
            (point[0], point[1], 0.0) if axis == "Z" else
            (0.0, point[1], point[2]) if axis == "X" else
            (point[0], 0.0, point[2])
            for point in self.vertices
        ]


class FakePolyOp:
    def __init__(self, mesh: FakeMesh) -> None:
        self.mesh = mesh

    def __getattr__(self, name):
        if name in {
            "getNumVerts", "getNumEdges", "getNumFaces", "getVert",
            "getEdgeVerts", "getFaceVerts", "getVertSelection",
            "getEdgeSelection", "getFaceSelection", "setVertSelection",
            "setEdgeSelection", "setFaceSelection", "getOpenEdges",
            "getBorderFromEdge", "getVertsUsingEdge", "extrudeFaces",
            "bevelFaces", "capHolesByEdge", "collapseVerts", "collapseEdges",
            "collapseFaces", "deleteVerts", "deleteIsoVerts", "deleteEdges",
            "deleteFaces", "weldVertsByThreshold",
        }:
            return getattr(self.mesh, name)
        raise AttributeError(name)


class FakePropertyOwner:
    """Documented Editable Poly object properties live here, not on the interface."""

    def __init__(self) -> None:
        self.extrusionType = 0
        self._extrusion_set_count = 0
        self._fail_extrusion_set = False
        self._fail_extrusion_restore = False
        self.insetAmount = 1.0
        self.insetType = 0
        self.connectEdgeSegments = 1
        self.connectEdgePinch = 0
        self.connectEdgeSlide = 0
        self.bridgeSelected = 1
        self.bridgeSegments = 1
        self.bridgeTaper = 0.0
        self.bridgeBias = 0.0
        self.bridgeTwist1 = 0
        self.bridgeTwist2 = 0
        self.bridge_twist_writes = []
        self.weldThreshold = 0.1
        self.useSoftSel = False
        self.ssUseEdgeDist = False
        self.ssEdgeDist = 1
        self.affectBackfacing = True
        self.falloff = 20.0
        self.pinch = 0.0
        self.bubble = 0.0
        self.lockSoftSel = False
        self.constrainType = 0
        self.preserveUVs = False
        self.handle = 501

    def __setattr__(self, name, value):
        if name == "extrusionType":
            if getattr(self, "_fail_extrusion_set", False):
                raise RuntimeError("setting write failed")
            count = getattr(self, "_extrusion_set_count", 0)
            if getattr(self, "_fail_extrusion_restore", False) and count >= 1:
                raise RuntimeError("setting restore failed")
            object.__setattr__(self, "_extrusion_set_count", count + 1)
        log = getattr(self, "bridge_twist_writes", None)
        if name in ("bridgeTwist1", "bridgeTwist2") and log is not None:
            log.append((name, value))
        object.__setattr__(self, name, value)


class FakeEditablePoly:
    def __init__(self, mesh: FakeMesh) -> None:
        self.mesh = mesh
        self.calls = []
        self.deleteIsolatedVerts = True
        self.command_mode = "none"
        self.connect_edges_result = True
        self.connect_vertices_result = True
        self.remove_result = True
        self.split_result = True
        self.break_result = True
        self.planar_result = True
        self.flip_result = True
        self.ready_to_bridge = True

    def buttonOp(self, op):
        self.calls.append(("buttonOp", str(op)))
        name = str(op).lower()
        if name == "growselection":
            self.mesh.selections[self.mesh.level].add(1)
        elif name == "shrinkselection":
            selection = self.mesh.selections[self.mesh.level]
            if selection:
                selection.pop()
        elif name == "inset":
            self.mesh._grow_mesh(1)
        elif name.startswith("bridge"):
            self.mesh._grow_mesh(1)

    def chamferVertices(self, amount, open=False):
        self.calls.append(("chamferVertices", amount, open))
        self.mesh._grow_mesh(1)

    def chamferEdges(self, amount, open=False):
        self.calls.append(("chamferEdges", amount, open))
        self.mesh._grow_mesh(1)

    def ConnectVertices(self, vertexFlag=1):
        self.calls.append(("ConnectVertices", vertexFlag))
        if self.connect_vertices_result:
            self.mesh._grow_mesh(1)
        return self.connect_vertices_result

    def ConnectEdges(self, edgeFlag=1):
        self.calls.append(("ConnectEdges", edgeFlag))
        if self.connect_edges_result:
            self.mesh._grow_mesh(1)
        return self.connect_edges_result

    def ReadyToBridgeFlagged(self, selLevel=None, flag=1):
        self.calls.append(("ReadyToBridgeFlagged", str(selLevel), flag))
        return self.ready_to_bridge

    def Remove(self, selLevel=None, flag=1):
        self.calls.append(("Remove", str(selLevel), flag))
        self.mesh.vertices = self.mesh.vertices[:-1]
        return self.remove_result

    def splitEdges(self, edgeFlags=1):
        self.calls.append(("splitEdges", edgeFlags))
        self.mesh.vertices = self.mesh.vertices[:-1]
        return self.split_result

    def breakVerts(self, vertFlags=1):
        self.calls.append(("breakVerts", vertFlags))
        self.mesh.vertices = self.mesh.vertices[:-1]
        return self.break_result

    def MakePlanarIn(self, axis, selLevel=None, flag=1):
        self.calls.append(("MakePlanarIn", str(axis), str(selLevel), flag))
        self.mesh.apply_planar(str(axis))
        return self.planar_result

    def flipNormals(self, faceFlags=1):
        self.calls.append(("flipNormals", faceFlags))
        self.mesh.faces[0] = list(reversed(self.mesh.faces[0]))
        return self.flip_result

    def SelectEdgeLoop(self):
        self.calls.append("SelectEdgeLoop")
        self.mesh.selections[ContextLevel.EDGE].add(2)

    def SelectEdgeRing(self):
        self.calls.append("SelectEdgeRing")
        self.mesh.selections[ContextLevel.EDGE].add(4)

    def SelectBorder(self):
        self.calls.append("SelectBorder")
        self.mesh.selections[ContextLevel.EDGE].update({1, 2})

    def SelectElement(self):
        self.calls.append("SelectElement")
        self.mesh.selections[ContextLevel.POLYGON].update({1, 2})

    def getCommandMode(self):
        return self.command_mode

    def toggleCommandMode(self, value):
        self.calls.append(("toggleCommandMode", str(value)))
        self.command_mode = str(value)

    def exitCommandModes(self):
        self.calls.append("exitCommandModes")
        self.command_mode = "none"


class FakeNode:
    def __init__(self, level: ContextLevel) -> None:
        self.mesh = FakeMesh(level)
        self.polyop = FakePolyOp(self.mesh)
        self.EditablePoly = FakeEditablePoly(self.mesh)
        self.baseObject = FakePropertyOwner()
        self.handle = 101
        self.transform = "matrix"


class FakeRuntime:
    def __init__(self, level: ContextLevel) -> None:
        self.node = FakeNode(level)
        self.polyop = self.node.polyop
        self.Name = FakeName
        self.Array = FakeArray
        self.currentTime = 0
        self.executed = []
        self.extra_calls = []

    def getHandleByAnim(self, value):
        return getattr(value, "handle", None)

    def execute(self, literal):
        self.executed.append(literal)
        if literal == "(fn (b) (b as array))":
            return lambda bitarray: list(bitarray)
        raise AssertionError("unexpected script literal: %r" % (literal,))

    def redrawViews(self):
        self.extra_calls.append("redrawViews")


def setup(level: ContextLevel, selections=None, open_edges=(), border_loops=None):
    runtime = FakeRuntime(level)
    mesh = runtime.node.mesh
    if selections:
        mesh.selections[level].update(selections)
    mesh.open_edges = set(open_edges)
    mesh.border_loops = border_loops or {}
    return runtime, runtime.node, runtime.node.EditablePoly, mesh


def run(runtime, node, action_id, level, indices, parameters=None):
    return execute_operation(runtime, node, action_id, level, indices, parameters)


class SelectionOperationTests(unittest.TestCase):
    def test_grow_uses_documented_button_op_and_reports_change(self):
        runtime, node, ep, mesh = setup(ContextLevel.VERTEX, {5})
        outcome = run(runtime, node, "selection.grow", ContextLevel.VERTEX, (5,), {})
        self.assertEqual(outcome.status, "applied")
        self.assertIn(("buttonOp", "GrowSelection"), ep.calls)

    def test_unchanged_grow_is_noop_not_failure(self):
        runtime, node, ep, mesh = setup(ContextLevel.POLYGON, {1})
        ep.buttonOp = lambda op: ep.calls.append(("buttonOp", str(op)))
        outcome = run(runtime, node, "selection.grow", ContextLevel.POLYGON, (1,), {})
        self.assertEqual(outcome.status, "noop")

    def test_shrink_loop_and_ring_call_their_documented_members(self):
        runtime, node, ep, mesh = setup(ContextLevel.EDGE, {1, 3})
        self.assertEqual(
            run(runtime, node, "selection.shrink", ContextLevel.EDGE, (1, 3), {}).status,
            "applied",
        )
        self.assertIn(("buttonOp", "ShrinkSelection"), ep.calls)
        self.assertEqual(
            run(runtime, node, "selection.loop", ContextLevel.EDGE, (1,), {}).status,
            "applied",
        )
        self.assertIn("SelectEdgeLoop", ep.calls)
        self.assertEqual(
            run(runtime, node, "selection.ring", ContextLevel.EDGE, (1,), {}).status,
            "applied",
        )
        self.assertIn("SelectEdgeRing", ep.calls)

    def test_clear_uses_none_provider_and_empty_is_noop(self):
        runtime, node, ep, mesh = setup(ContextLevel.EDGE, {1, 2})
        outcome = run(runtime, node, "selection.clear", ContextLevel.EDGE, (1, 2), {})
        self.assertEqual(outcome.status, "applied")
        self.assertEqual(mesh.selections[ContextLevel.EDGE], set())
        self.assertIn("setEdgeSelection", mesh.calls)
        empty = run(runtime, node, "selection.clear", ContextLevel.EDGE, (), {})
        self.assertEqual(empty.status, "noop")

    def test_to_border_and_to_element_change_selection_not_level(self):
        runtime, node, ep, mesh = setup(ContextLevel.EDGE, {1})
        self.assertEqual(
            run(runtime, node, "selection.to_border", ContextLevel.EDGE, (1,), {}).status,
            "applied",
        )
        self.assertIn("SelectBorder", ep.calls)
        runtime, node, ep, mesh = setup(ContextLevel.POLYGON, {1})
        self.assertEqual(
            run(runtime, node, "selection.to_element", ContextLevel.POLYGON, (1,), {}).status,
            "applied",
        )
        self.assertIn("SelectElement", ep.calls)


class PolygonOperationTests(unittest.TestCase):
    def test_extrude_sets_and_restores_extrusion_type(self):
        runtime, node, ep, mesh = setup(ContextLevel.POLYGON, {1})
        node.baseObject.extrusionType = 2
        outcome = run(
            runtime,
            node,
            "model.extrude",
            ContextLevel.POLYGON,
            (1,),
            {"height": 2.0, "extrusion_type": "local_normal"},
        )
        self.assertEqual(outcome.status, "applied")
        self.assertIn(("extrudeFaces", (1,), 2.0), mesh.calls)
        self.assertEqual(node.baseObject.extrusionType, 2)

    def test_extrude_zero_height_is_noop_and_calls_nothing(self):
        runtime, node, ep, mesh = setup(ContextLevel.POLYGON, {1})
        outcome = run(
            runtime,
            node,
            "model.extrude",
            ContextLevel.POLYGON,
            (1,),
            {"height": 0.0, "extrusion_type": "group"},
        )
        self.assertEqual(outcome.status, "noop")
        self.assertNotIn("extrudeFaces", [call for call in mesh.calls if isinstance(call, str)])

    def test_bevel_uses_extrusion_type_and_restores_it_on_failure(self):
        runtime, node, ep, mesh = setup(ContextLevel.POLYGON, {1})
        node.baseObject.extrusionType = 1
        original = mesh.bevelFaces

        def explode(*args, **kwargs):
            raise RuntimeError("bevel failed")

        mesh.bevelFaces = explode
        with self.assertRaises(RuntimeError):
            run(
                runtime,
                node,
                "model.bevel",
                ContextLevel.POLYGON,
                (1,),
                {"height": 1.0, "outline": -0.1, "extrusion_type": "by_polygon"},
            )
        self.assertEqual(node.baseObject.extrusionType, 1)

    def test_inset_uses_button_op_not_invented_polyop_method(self):
        runtime, node, ep, mesh = setup(ContextLevel.POLYGON, {1})
        node.baseObject.insetAmount = 5.0
        node.baseObject.insetType = 1
        outcome = run(
            runtime,
            node,
            "model.inset",
            ContextLevel.POLYGON,
            (1,),
            {"amount": 0.25, "inset_type": "by_polygon"},
        )
        self.assertEqual(outcome.status, "applied")
        self.assertIn(("buttonOp", "Inset"), ep.calls)
        self.assertEqual(node.baseObject.insetAmount, 5.0)
        self.assertEqual(node.baseObject.insetType, 1)
        self.assertFalse(hasattr(runtime.polyop, "insetFaces"))

    def test_chamfer_uses_vertex_and_edge_methods_with_open_flag(self):
        runtime, node, ep, mesh = setup(ContextLevel.VERTEX, {1, 2})
        run(runtime, node, "model.chamfer", ContextLevel.VERTEX, (1, 2),
            {"amount": 0.5, "open": True})
        self.assertIn(("chamferVertices", 0.5, True), ep.calls)
        runtime, node, ep, mesh = setup(ContextLevel.EDGE, {1})
        run(runtime, node, "model.chamfer", ContextLevel.EDGE, (1,),
            {"amount": 0.5, "open": False})
        self.assertIn(("chamferEdges", 0.5, False), ep.calls)

    def test_first_setting_write_failure_is_not_swallowed(self):
        runtime, node, ep, mesh = setup(ContextLevel.POLYGON, {1})
        node.baseObject.extrusionType = 7
        node.baseObject._fail_extrusion_set = True
        with self.assertRaises(RuntimeError):
            run(runtime, node, "model.extrude", ContextLevel.POLYGON, (1,),
                {"height": 1.0, "extrusion_type": "group"})
        self.assertEqual(node.baseObject.extrusionType, 7)

    def test_setting_restore_failure_is_an_error(self):
        runtime, node, ep, mesh = setup(ContextLevel.POLYGON, {1})
        node.baseObject.extrusionType = 7
        node.baseObject._extrusion_set_count = 0
        node.baseObject._fail_extrusion_restore = True
        with self.assertRaises(RuntimeError):
            run(runtime, node, "model.extrude", ContextLevel.POLYGON, (1,),
                {"height": 1.0, "extrusion_type": "group"})


class BorderAndConnectTests(unittest.TestCase):
    def test_cap_rejects_non_open_edges_before_call(self):
        runtime, node, ep, mesh = setup(ContextLevel.BORDER, {1, 2}, open_edges={2})
        with self.assertRaises(OperationError):
            run(runtime, node, "model.cap", ContextLevel.BORDER, (1, 2), {})
        self.assertFalse(any(isinstance(call, tuple) and call[0] == "capHolesByEdge" for call in mesh.calls))

    def test_cap_uses_selected_open_edges_only(self):
        runtime, node, ep, mesh = setup(ContextLevel.BORDER, {1, 2}, open_edges={1, 2})
        outcome = run(runtime, node, "model.cap", ContextLevel.BORDER, (1, 2), {})
        self.assertEqual(outcome.status, "applied")
        self.assertIn(("capHolesByEdge", (1, 2)), mesh.calls)

    def test_connect_vertices_and_edges_with_settings_restored(self):
        runtime, node, ep, mesh = setup(ContextLevel.VERTEX, {1, 2})
        node.baseObject.connectEdgeSegments = 9
        run(runtime, node, "model.connect", ContextLevel.VERTEX, (1, 2), {})
        self.assertIn(("ConnectVertices", 1), ep.calls)
        self.assertEqual(node.baseObject.connectEdgeSegments, 9)

        runtime, node, ep, mesh = setup(ContextLevel.EDGE, {1, 2})
        node.baseObject.connectEdgeSegments = 9
        node.baseObject.connectEdgePinch = 4
        node.baseObject.connectEdgeSlide = -3
        run(runtime, node, "model.connect", ContextLevel.EDGE, (1, 2),
            {"segments": 3, "pinch": 10, "slide": -20})
        self.assertIn(("ConnectEdges", 1), ep.calls)
        self.assertEqual(
            (node.baseObject.connectEdgeSegments, node.baseObject.connectEdgePinch, node.baseObject.connectEdgeSlide),
            (9, 4, -3),
        )

    def test_connect_false_return_is_error_and_settings_restore(self):
        runtime, node, ep, mesh = setup(ContextLevel.EDGE, {1, 2})
        ep.connect_edges_result = False
        node.baseObject.connectEdgeSegments = 7
        with self.assertRaises(OperationError):
            run(runtime, node, "model.connect", ContextLevel.EDGE, (1, 2),
                {"segments": 2, "pinch": 0, "slide": 0})
        self.assertEqual(node.baseObject.connectEdgeSegments, 7)

    def test_bridge_border_requires_two_complete_disjoint_loops(self):
        loops = {1: {1, 2}, 2: {1, 2}, 3: {3, 4}, 4: {3, 4}}
        runtime, node, ep, mesh = setup(
            ContextLevel.BORDER, {1, 2, 3, 4}, open_edges={1, 2, 3, 4},
            border_loops=loops,
        )
        outcome = run(runtime, node, "model.bridge", ContextLevel.BORDER,
                      (1, 2, 3, 4), {"segments": 2, "taper": 0.0, "bias": 0.0, "twist": 0})
        self.assertEqual(outcome.status, "applied")
        self.assertIn(("ReadyToBridgeFlagged", "Border", 1), ep.calls)
        self.assertIn(("buttonOp", "BridgeBorder"), ep.calls)
        writes = [value for name, value in node.baseObject.bridge_twist_writes
                  if name == "bridgeTwist1"]
        self.assertEqual(writes[0], 0)
        self.assertIs(type(writes[0]), int)

        runtime, node, ep, mesh = setup(
            ContextLevel.BORDER, {1, 2}, open_edges={1, 2, 3, 4},
            border_loops=loops,
        )
        with self.assertRaises(OperationError):
            run(runtime, node, "model.bridge", ContextLevel.BORDER, (1, 2),
                {"segments": 1, "taper": 0.0, "bias": 0.0, "twist": 0})

    def test_bridge_polygon_requires_exactly_two_faces(self):
        runtime, node, ep, mesh = setup(ContextLevel.POLYGON, {1, 2})
        with self.assertRaises(OperationError):
            run(runtime, node, "model.bridge", ContextLevel.POLYGON, (1,),
                {"segments": 1, "taper": 0.0, "bias": 0.0, "twist": 0})
        outcome = run(runtime, node, "model.bridge", ContextLevel.POLYGON, (1, 2),
                      {"segments": 3, "taper": 2.0, "bias": -1.0, "twist": 5})
        self.assertEqual(outcome.status, "applied")
        self.assertIn(("buttonOp", "BridgePolygon"), ep.calls)

    def test_bridge_twist_write_is_integer_and_rejects_fraction_or_bool(self):
        loops = {1: {1, 2}, 2: {1, 2}, 3: {3, 4}, 4: {3, 4}}
        runtime, node, ep, mesh = setup(
            ContextLevel.BORDER, {1, 2, 3, 4}, open_edges={1, 2, 3, 4},
            border_loops=loops,
        )
        node.baseObject.bridgeTwist1 = 4
        node.baseObject.bridgeTwist2 = 4
        outcome = run(runtime, node, "model.bridge", ContextLevel.BORDER,
                      (1, 2, 3, 4), {"segments": 2, "taper": 0.0, "bias": 0.0,
                                     "twist": -6})
        self.assertEqual(outcome.status, "applied")
        writes = [value for _name, value in node.baseObject.bridge_twist_writes]
        self.assertIn(-6, writes)
        self.assertTrue(all(type(value) is int for value in writes))
        self.assertEqual(node.baseObject.bridgeTwist1, 4)
        self.assertEqual(node.baseObject.bridgeTwist2, 4)

        for bad in (1.0, 1.5, True, False):
            with self.subTest(bad=bad):
                with self.assertRaises(OperationError):
                    run(runtime, node, "model.bridge", ContextLevel.BORDER,
                        (1, 2, 3, 4), {"segments": 1, "taper": 0.0,
                                       "bias": 0.0, "twist": bad})

    def test_bridge_readiness_false_is_error(self):
        runtime, node, ep, mesh = setup(ContextLevel.EDGE, {1, 2})
        ep.ready_to_bridge = False
        with self.assertRaises(OperationError):
            run(runtime, node, "model.bridge", ContextLevel.EDGE, (1, 2),
                {"segments": 1, "taper": 0.0, "bias": 0.0, "twist": 0})

    def test_weld_requires_border_vertices_and_restores_threshold(self):
        runtime, node, ep, mesh = setup(
            ContextLevel.VERTEX, {1, 2}, open_edges={1, 4}
        )
        node.baseObject.weldThreshold = 3.0
        outcome = run(runtime, node, "model.weld", ContextLevel.VERTEX, (1, 2),
                      {"threshold": 0.2})
        self.assertEqual(outcome.status, "applied")
        self.assertIn(("weldVertsByThreshold", (1, 2)), mesh.calls)
        self.assertEqual(node.baseObject.weldThreshold, 3.0)

        runtime, node, ep, mesh = setup(
            ContextLevel.VERTEX, {3}, open_edges={1, 4}
        )
        with self.assertRaises(OperationError):
            run(runtime, node, "model.weld", ContextLevel.VERTEX, (3,),
                {"threshold": 0.2})


class DestructiveOperationTests(unittest.TestCase):
    def test_collapse_provider_per_level(self):
        cases = {
            ContextLevel.VERTEX: "collapseVerts",
            ContextLevel.EDGE: "collapseEdges",
            ContextLevel.POLYGON: "collapseFaces",
        }
        for level, call_name in cases.items():
            with self.subTest(level=level):
                runtime, node, ep, mesh = setup(level, {1})
                outcome = run(runtime, node, "model.collapse", level, (1,),
                              {"confirm": True})
                self.assertEqual(outcome.status, "applied")
                self.assertTrue(any(
                    isinstance(call, tuple) and call[0] == call_name
                    for call in mesh.calls
                ))

    def test_delete_provider_and_isolated_vertex_cleanup(self):
        runtime, node, ep, mesh = setup(ContextLevel.VERTEX, {1})
        run(runtime, node, "model.delete", ContextLevel.VERTEX, (1,),
            {"confirm": True, "cleanup": True})
        self.assertTrue(any(isinstance(call, tuple) and call[0] == "deleteVerts" for call in mesh.calls))
        self.assertIn("deleteIsoVerts", mesh.calls)

        runtime, node, ep, mesh = setup(ContextLevel.EDGE, {1})
        run(runtime, node, "model.delete", ContextLevel.EDGE, (1,), {"confirm": True})
        self.assertIn(("deleteEdges", (1,), True), mesh.calls)

        runtime, node, ep, mesh = setup(ContextLevel.POLYGON, {1})
        run(runtime, node, "model.delete", ContextLevel.POLYGON, (1,), {"confirm": True})
        self.assertIn(("deleteFaces", (1,), True), mesh.calls)

        runtime, node, ep, mesh = setup(ContextLevel.ELEMENT, {1})
        run(runtime, node, "model.delete", ContextLevel.ELEMENT, (1,), {"confirm": True})
        self.assertIn(("deleteFaces", (1,), True), mesh.calls)

    def test_remove_uses_editable_poly_remove_and_not_polyop(self):
        runtime, node, ep, mesh = setup(ContextLevel.VERTEX, {1})
        run(runtime, node, "model.remove", ContextLevel.VERTEX, (1,), {"confirm": True})
        self.assertIn(("Remove", "Vertex", 1), ep.calls)
        self.assertFalse(any(
            isinstance(call, tuple) and call[0] in ("deleteVerts", "deleteEdges")
            for call in mesh.calls
        ))

    def test_unconfirmed_destructive_edit_is_rejected(self):
        runtime, node, ep, mesh = setup(ContextLevel.VERTEX, {1})
        with self.assertRaises(OperationError):
            run(runtime, node, "model.collapse", ContextLevel.VERTEX, (1,),
                {"confirm": False})


class TopologyOperationTests(unittest.TestCase):
    def test_split_break_planar_flip_use_documented_members(self):
        runtime, node, ep, mesh = setup(ContextLevel.EDGE, {1})
        self.assertEqual(
            run(runtime, node, "model.split_edges", ContextLevel.EDGE, (1,), {}).status,
            "applied",
        )
        self.assertIn(("splitEdges", 1), ep.calls)

        runtime, node, ep, mesh = setup(ContextLevel.VERTEX, {1})
        self.assertEqual(
            run(runtime, node, "model.break_vertices", ContextLevel.VERTEX, (1,), {}).status,
            "applied",
        )
        self.assertIn(("breakVerts", 1), ep.calls)

        runtime, node, ep, mesh = setup(ContextLevel.VERTEX, {1})
        self.assertEqual(
            run(runtime, node, "model.planar_x", ContextLevel.VERTEX, (1,), {}).status,
            "applied",
        )
        self.assertIn(("MakePlanarIn", "X", "CurrentLevel", 1), ep.calls)

        runtime, node, ep, mesh = setup(ContextLevel.POLYGON, {1})
        self.assertEqual(
            run(runtime, node, "model.flip_normals", ContextLevel.POLYGON, (1,), {}).status,
            "applied",
        )
        self.assertIn(("flipNormals", 1), ep.calls)

    def test_false_boolean_returns_never_become_applied(self):
        runtime, node, ep, mesh = setup(ContextLevel.EDGE, {1})
        ep.split_result = False
        with self.assertRaises(OperationError):
            run(runtime, node, "model.split_edges", ContextLevel.EDGE, (1,), {})
        runtime, node, ep, mesh = setup(ContextLevel.VERTEX, {1})
        ep.break_result = False
        with self.assertRaises(OperationError):
            run(runtime, node, "model.break_vertices", ContextLevel.VERTEX, (1,), {})
        runtime, node, ep, mesh = setup(ContextLevel.POLYGON, {1})
        ep.flip_result = False
        with self.assertRaises(OperationError):
            run(runtime, node, "model.flip_normals", ContextLevel.POLYGON, (1,), {})


class OperationGuardTests(unittest.TestCase):
    def test_unknown_action_and_missing_parameters_are_rejected(self):
        runtime, node, ep, mesh = setup(ContextLevel.POLYGON, {1})
        with self.assertRaises(OperationError):
            run(runtime, node, "model.exit_tool", ContextLevel.POLYGON, (1,), {})
        with self.assertRaises(OperationError):
            run(runtime, node, "model.extrude", ContextLevel.POLYGON, (1,), None)
        with self.assertRaises(OperationError):
            run(runtime, node, "model.extrude", ContextLevel.POLYGON, (1,),
                {"height": 1.0, "extrusion_type": "bad"})

    def test_empty_required_selection_is_rejected(self):
        runtime, node, ep, mesh = setup(ContextLevel.POLYGON, set())
        with self.assertRaises(OperationError):
            run(runtime, node, "model.extrude", ContextLevel.POLYGON, (),
                {"height": 1.0, "extrusion_type": "group"})

    def test_native_component_arguments_are_host_arrays_not_python_lists(self):
        runtime, node, ep, mesh = setup(ContextLevel.POLYGON, {1})
        run(runtime, node, "model.extrude", ContextLevel.POLYGON, (1, 3),
            {"height": 1.0, "extrusion_type": "group"})
        name, argument = mesh.raw_args[-1]
        self.assertEqual(name, "extrudeFaces")
        self.assertIsInstance(argument, FakeArray)
        self.assertNotIsInstance(argument, (list, tuple))
        self.assertEqual(tuple(argument), (1, 3))

        runtime, node, ep, mesh = setup(ContextLevel.EDGE, {1, 2})
        run(runtime, node, "model.delete", ContextLevel.EDGE, (1, 2),
            {"confirm": True})
        name, argument = mesh.raw_args[-1]
        self.assertEqual(name, "deleteEdges")
        self.assertIsInstance(argument, FakeArray)
        self.assertEqual(tuple(argument), (1, 2))

    def test_object_properties_come_from_base_object_not_interface(self):
        runtime, node, ep, mesh = setup(ContextLevel.POLYGON, {1})
        self.assertFalse(hasattr(ep, "extrusionType"))
        node.baseObject.extrusionType = 2
        run(runtime, node, "model.extrude", ContextLevel.POLYGON, (1,),
            {"height": 1.0, "extrusion_type": "group"})
        self.assertEqual(node.baseObject.extrusionType, 2)
        self.assertIn("extrusionType=2", read_settings(node)[0])

    def test_unreadable_operation_setting_fails_fingerprint_closed(self):
        runtime, node, ep, mesh = setup(ContextLevel.POLYGON, {1})
        del node.baseObject.insetAmount
        _, readable = read_settings(node)
        self.assertFalse(readable)

    def test_fingerprint_reads_node_owner_and_rejects_unreadable_settings(self):
        runtime, node, ep, mesh = setup(ContextLevel.POLYGON, {1})
        target = MutableTarget(
            node=node,
            ep=ep,
            level=ContextLevel.POLYGON,
            raw_level=4,
            indices=(1,),
            geometry=read_geometry(runtime, node),
            settings_token="",
            target_token="target",
            node_token="node",
            base_token="base",
        )
        # The properties live on node.baseObject, not on the EditablePoly
        # method interface.  A readable pre-edit fingerprint therefore proves
        # the node owner is used; the interface itself has no such attributes.
        self.assertFalse(hasattr(ep, "insetAmount"))
        self.assertRegex(target_fingerprint(runtime, target), "^[0-9a-f]{64}$")

        del node.baseObject.insetAmount
        with self.assertRaises(OperationError) as raised:
            target_fingerprint(runtime, target)
        self.assertIn("unreadable", str(raised.exception))

    def test_fingerprint_uses_anim_handles_not_base_handle_property(self):
        runtime, node, ep, mesh = setup(ContextLevel.POLYGON, {1})
        del node.baseObject.handle
        handles = {id(node): 101, id(node.baseObject): 501}
        runtime.getHandleByAnim = lambda value: handles.get(id(value))
        target = MutableTarget(
            node=node,
            ep=ep,
            level=ContextLevel.POLYGON,
            raw_level=4,
            indices=(1,),
            geometry=read_geometry(runtime, node),
            settings_token="",
            target_token="target",
            node_token="node",
            base_token="base",
        )
        self.assertRegex(target_fingerprint(runtime, target), "^[0-9a-f]{64}$")
        self.assertFalse(hasattr(node.baseObject, "handle"))

        for bad in (None, "undefined", object(), True):
            with self.subTest(bad=bad):
                runtime.getHandleByAnim = lambda value, bad=bad: bad
                with self.assertRaises(OperationError) as raised:
                    target_fingerprint(runtime, target)
                self.assertIn("anim handle", str(raised.exception))

        runtime.getHandleByAnim = None
        with self.assertRaises(OperationError) as raised:
            target_fingerprint(runtime, target)
        self.assertIn("getHandleByAnim", str(raised.exception))

    def test_bitarray_count_uses_numberset_not_capacity(self):
        bitarray = FakeBitArray((1, 5, 10), capacity=100)
        self.assertEqual(bitarray.count, 100)
        self.assertEqual(bitarray_count(bitarray), 3)
        self.assertEqual(bitarray_indices(bitarray), (1, 5, 10))

    def test_unreadable_bitarray_returns_none_without_inventing_indices(self):
        class Broken:
            @property
            def isEmpty(self):
                return False

            @property
            def numberSet(self):
                return 4

            def __iter__(self):
                raise RuntimeError("cannot iterate")

        self.assertIsNone(bitarray_indices(Broken()))
        self.assertEqual(bitarray_count(Broken()), 4)

    def test_geometry_limits_are_enforced(self):
        runtime, node, ep, mesh = setup(ContextLevel.POLYGON, {1})
        mesh.vertices = [(0.0, 0.0, 0.0)] * 20001
        with self.assertRaises(GeometryReadError) as raised:
            read_geometry(runtime, node)
        self.assertTrue(raised.exception.over_limit)
        self.assertIn("limit", raised.exception.reason)

    def test_geometry_fingerprint_detects_equal_count_position_change(self):
        runtime, node, ep, mesh = setup(ContextLevel.POLYGON, {1})
        before = read_geometry(runtime, node)
        mesh.vertices[0] = (9.0, 9.0, 9.0)
        after = read_geometry(runtime, node)
        self.assertEqual(before.counts, after.counts)
        self.assertNotEqual(before.digest, after.digest)

    def test_unreadable_edge_endpoints_disable_geometry_fingerprint(self):
        runtime, node, ep, mesh = setup(ContextLevel.POLYGON, {1})
        mesh.getEdgeVerts = lambda _node, _index: None
        with self.assertRaises(GeometryReadError) as raised:
            read_geometry(runtime, node)
        self.assertIn("Edge", raised.exception.reason)


class UndefinedTimeWrapper:
    """Models MAXScript undefined text without depending on a real Max type."""

    def __str__(self) -> str:
        return "  Undefined "


class UnstringableTime:
    def __str__(self) -> str:
        raise RuntimeError("currentTime text failed")


class ThrowingTimeRuntime(FakeRuntime):
    """Readable fake runtime whose currentTime getter can fail on demand."""

    def __init__(self, level: ContextLevel) -> None:
        self.fail_time_read = True
        super().__init__(level)

    @property
    def currentTime(self):
        if self.fail_time_read:
            raise RuntimeError("currentTime read failed")
        return self._time_value

    @currentTime.setter
    def currentTime(self, value):
        object.__setattr__(self, "_time_value", value)


class TimeFingerprintTests(unittest.TestCase):
    """The raw current-time read must fail closed without rejecting zero."""

    def _runtime(self, level=ContextLevel.POLYGON):
        runtime, node, ep, _mesh = setup(level, {1})
        return runtime, node, ep

    def _fingerprint(self, runtime, node, ep):
        target = MutableTarget(
            node=node,
            ep=ep,
            level=ContextLevel.POLYGON,
            raw_level=4,
            indices=(1,),
            geometry=read_geometry(runtime, node),
            settings_token="",
            target_token="target",
            node_token="node",
            base_token="base",
        )
        return target_fingerprint(runtime, target)

    def test_valid_zero_and_readable_times_produce_a_digest(self):
        runtime, node, ep = self._runtime()
        for readable in (0, 0.0, "0", "0f", 160, -160, "160f", "time:160"):
            with self.subTest(readable=readable):
                runtime.currentTime = readable
                value = self._fingerprint(runtime, node, ep)
                self.assertRegex(value, "^[0-9a-f]{64}$")

    def test_distinct_readable_times_change_the_digest(self):
        runtime, node, ep = self._runtime()
        digests = []
        for readable in (0, 160, -160, "160f"):
            runtime.currentTime = readable
            digests.append(self._fingerprint(runtime, node, ep))
        self.assertEqual(len(set(digests)), len(digests))

    def test_missing_or_sentinel_time_is_rejected(self):
        runtime, node, ep = self._runtime()
        for unreadable in (
            None,
            True,
            False,
            "",
            "   ",
            "\t\n",
            "undefined",
            "UNDEFINED",
            "  undefined  ",
            "none",
            "NONE",
            "time:unreadable",
            "TIME:UNREADABLE",
            "  time:unreadable  ",
            UndefinedTimeWrapper(),
            UnstringableTime(),
        ):
            with self.subTest(unreadable=repr(unreadable)):
                runtime.currentTime = unreadable
                with self.assertRaises(OperationError) as raised:
                    self._fingerprint(runtime, node, ep)
                message = str(raised.exception)
                self.assertIn("current time", message)
                self.assertIn("unreadable", message)

    def test_throwing_current_time_getter_fails_closed(self):
        runtime = ThrowingTimeRuntime(ContextLevel.POLYGON)
        mesh = runtime.node.mesh
        mesh.selections[ContextLevel.POLYGON].update({1})
        node = runtime.node
        ep = node.EditablePoly
        with self.assertRaises(OperationError) as raised:
            self._fingerprint(runtime, node, ep)
        message = str(raised.exception)
        self.assertIn("current time", message)
        self.assertIn("unreadable", message)
        self.assertIn("RuntimeError", message)

        runtime.fail_time_read = False
        runtime.currentTime = 0
        self.assertRegex(self._fingerprint(runtime, node, ep), "^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main(verbosity=2)

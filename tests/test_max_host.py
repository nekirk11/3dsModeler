"""Contract and safety tests for the native Max 2025 adapter.

These are fake-runtime tests.  They prove the Python boundary, exact-target
guards, parameter/mode safety, rollback handling, and refusal to fake success.
They do **not** prove anything about a real 3ds Max 2025 install; the runtime
checklist is explicitly NOT RUN in ``docs/MAX2025-VALIDATION.md``.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

from max2025_menu_prototype.core.catalog import ACTIONS
from max2025_menu_prototype.core.contracts import ContextLevel, ContextSnapshot, MenuEvent
from max2025_menu_prototype.core.controller import MenuController
from max2025_menu_prototype.core.profile import default_profile
from max2025_menu_prototype.max_host import bootstrap
from max2025_menu_prototype.max_host.adapter import MaxHostAdapter
from max2025_menu_prototype.max_host.bindings import (
    CATALOG_ACTION_IDS,
    FIXED_MAX_COMMAND_LITERALS,
    NATIVE_BINDINGS,
    launch_native,
)
from max2025_menu_prototype.max_host.probe import build_report

try:
    from . import checkout_support
except ImportError:  # discovered as a top-level test module
    import checkout_support


# ---------------------------------------------------------------------------
# Fake pymxs surface used only by contract tests
# ---------------------------------------------------------------------------


class FakeClass:
    def __init__(self, name: str) -> None:
        self.name = name

    @property
    def __name__(self) -> str:
        return self.name

    def __str__(self) -> str:
        return self.name

    def __eq__(self, other: object) -> bool:
        return isinstance(other, FakeClass) and other.name == self.name

    def __hash__(self) -> int:
        return hash(self.name)


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

    def __repr__(self) -> str:
        return "FakeArray(%r)" % (self.values,)


class FakeHold:
    def __init__(self) -> None:
        self.holding = False
        self.redoing = False

    def Holding(self):
        return self.holding

    def RestoreOrRedoing(self):
        return self.redoing


class FakeBitArray:
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


class BrokenBitArray:
    @property
    def isEmpty(self):
        return False

    @property
    def numberSet(self):
        return 3

    def __iter__(self):
        raise RuntimeError("cannot iterate")


class FakeBaseObject:
    def __init__(self, class_name: str = "Editable_Poly", handle: int = 501) -> None:
        self.class_name = class_name
        self.handle = handle
        # Documented Editable Poly object properties belong to the node/base
        # object; the EditablePoly interface is only for methods.
        self.extrusionType = 0
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


class FakeModifier(FakeBaseObject):
    def __init__(self, class_name: str, handle: int = 900) -> None:
        super().__init__(class_name, handle)
        self.enabled = True


class FakeMesh:
    def __init__(self) -> None:
        self.vertices = [
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
            (1.0, 1.0, 0.0),
            (0.0, 1.0, 0.0),
        ]
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
        self.border_loops = {}
        self.current_level = ContextLevel.POLYGON
        self.broken_level = None
        self.calls = []

    def snapshot(self):
        return (
            list(self.vertices),
            list(self.edges),
            [list(face) for face in self.faces],
            {level: set(values) for level, values in self.selections.items()},
            set(self.open_edges),
        )

    def restore(self, snapshot) -> None:
        (
            self.vertices,
            self.edges,
            self.faces,
            self.selections,
            self.open_edges,
        ) = (
            list(snapshot[0]),
            list(snapshot[1]),
            [list(face) for face in snapshot[2]],
            {level: set(values) for level, values in snapshot[3].items()},
            set(snapshot[4]),
        )

    # -- topology reads -------------------------------------------------
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
    def getVertSelection(self, node):
        self.calls.append("getVertSelection")
        if self.broken_level == ContextLevel.VERTEX:
            return BrokenBitArray()
        return FakeBitArray(self.selections[ContextLevel.VERTEX], capacity=50)

    def getEdgeSelection(self, node):
        self.calls.append("getEdgeSelection")
        if self.broken_level in (ContextLevel.EDGE, ContextLevel.BORDER):
            return BrokenBitArray()
        return FakeBitArray(
            self.selections[ContextLevel.EDGE]
            | self.selections[ContextLevel.BORDER],
            capacity=50,
        )

    def getFaceSelection(self, node):
        self.calls.append("getFaceSelection")
        if self.broken_level in (ContextLevel.POLYGON, ContextLevel.ELEMENT):
            return BrokenBitArray()
        return FakeBitArray(
            self.selections[ContextLevel.POLYGON]
            | self.selections[ContextLevel.ELEMENT],
            capacity=50,
        )

    def _set_selection(self, level, value):
        self.calls.append("setSelection")
        if isinstance(value, FakeName):
            self.selections[level] = set()
        else:
            self.selections[level] = set(int(item) for item in value)

    def setVertSelection(self, node, value):
        self._set_selection(ContextLevel.VERTEX, value)

    def setEdgeSelection(self, node, value):
        self._set_selection(ContextLevel.EDGE, value)

    def setFaceSelection(self, node, value):
        self._set_selection(ContextLevel.POLYGON, value)

    # -- borders --------------------------------------------------------
    def getOpenEdges(self, node):
        self.calls.append("getOpenEdges")
        return FakeBitArray(self.open_edges, capacity=len(self.edges))

    def getBorderFromEdge(self, node, edge):
        self.calls.append("getBorderFromEdge")
        return FakeBitArray(self.border_loops.get(int(edge), ()))

    def getVertsUsingEdge(self, node, edges):
        self.calls.append("getVertsUsingEdge")
        verts = set()
        for edge in edges:
            if 1 <= int(edge) <= len(self.edges):
                verts.update(self.edges[int(edge) - 1])
        return FakeBitArray(verts)

    # -- mutations ------------------------------------------------------
    def grow_mesh(self, count: int = 1) -> None:
        for _ in range(count):
            index = len(self.vertices) + 1
            self.vertices.append((float(index), 0.0, 0.0))
            # Every face loop must have at least three distinct vertices; the
            # old two-vertex loop was an invalid fake mesh, not a Max result.
            other = index - 1 if index - 1 not in (1, index) else 2
            loop = [1, other, index]
            if len(set(loop)) < 3:
                loop = [1, 2, index]
            self.faces.append(loop)

    def extrudeFaces(self, node, face_list, amount):
        self.calls.append(("extrudeFaces", tuple(face_list), amount))
        self.grow_mesh(len(tuple(face_list)))

    def bevelFaces(self, node, face_list, height, outline):
        self.calls.append(("bevelFaces", tuple(face_list), height, outline))
        self.grow_mesh(len(tuple(face_list)))

    def capHolesByEdge(self, node, edges):
        self.calls.append(("capHolesByEdge", tuple(edges)))
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
        self.vertices = self.vertices[:-1]
        self.faces = self.faces[:-1]

    def collapseEdges(self, node, values):
        self.calls.append(("collapseEdges", tuple(values)))
        self.edges = self.edges[:-1]
        self.vertices = self.vertices[:-1]

    def collapseFaces(self, node, values):
        self.calls.append(("collapseFaces", tuple(values)))
        self.faces = self.faces[:-1]

    def deleteVerts(self, node, values):
        self.calls.append(("deleteVerts", tuple(values)))
        self.vertices = self.vertices[:-1]
        self.faces = self.faces[:-1]

    def deleteIsoVerts(self, node):
        self.calls.append("deleteIsoVerts")
        if self.vertices:
            self.vertices = self.vertices[:-1]

    def deleteEdges(self, node, values, delIsoVerts=True):
        self.calls.append(("deleteEdges", tuple(values), delIsoVerts))
        self.edges = self.edges[:-1]

    def deleteFaces(self, node, values, delIsoVerts=True):
        self.calls.append(("deleteFaces", tuple(values), delIsoVerts))
        self.faces = self.faces[:-1]

    def weldVertsByThreshold(self, node, values):
        self.calls.append(("weldVertsByThreshold", tuple(values)))
        self.vertices = self.vertices[:-1]

    def apply_planar(self, axis):
        self.vertices = [
            (0.0, point[1], point[2]) if axis == "X" else
            (point[0], 0.0, point[2]) if axis == "Y" else
            (point[0], point[1], 0.0)
            for point in self.vertices
        ]


class FakePolyOp:
    def __init__(self) -> None:
        pass

    def __getattr__(self, name):
        mesh_methods = {
            "getNumVerts", "getNumEdges", "getNumFaces", "getVert",
            "getEdgeVerts", "getFaceVerts", "getVertSelection",
            "getEdgeSelection", "getFaceSelection", "setVertSelection",
            "setEdgeSelection", "setFaceSelection", "getOpenEdges",
            "getBorderFromEdge", "getVertsUsingEdge", "extrudeFaces",
            "bevelFaces", "capHolesByEdge", "collapseVerts", "collapseEdges",
            "collapseFaces", "deleteVerts", "deleteIsoVerts", "deleteEdges",
            "deleteFaces", "weldVertsByThreshold",
        }
        if name in mesh_methods:
            def dispatch(node, *args, **kwargs):
                return getattr(node.mesh, name)(node, *args, **kwargs)
            return dispatch
        raise AttributeError(name)


class FakeEditablePoly:
    def __init__(self, mesh: FakeMesh) -> None:
        self.mesh = mesh
        self.calls = []
        self.deleteIsolatedVerts = True
        self.command_mode = "none"
        self.connect_edges_result = True
        self.connect_vertices_result = True
        self.ready_to_bridge = True
        self.split_result = True
        self.break_result = True
        self.planar_result = True
        self.flip_result = True
        self.remove_result = True

    def buttonOp(self, op):
        self.calls.append(("buttonOp", str(op)))
        name = str(op).lower()
        if name == "growselection":
            self.mesh.selections[self.mesh.current_level].add(99)
        elif name == "shrinkselection":
            selection = self.mesh.selections[self.mesh.current_level]
            if selection:
                selection.pop()
        elif name == "inset":
            self.mesh.grow_mesh(1)
        elif name.startswith("bridge"):
            self.mesh.grow_mesh(1)

    def chamferVertices(self, amount, open=False):
        self.calls.append(("chamferVertices", amount, open))
        self.mesh.grow_mesh(1)

    def chamferEdges(self, amount, open=False):
        self.calls.append(("chamferEdges", amount, open))
        self.mesh.grow_mesh(1)

    def ConnectVertices(self, vertexFlag=1):
        self.calls.append(("ConnectVertices", vertexFlag))
        if self.connect_vertices_result:
            self.mesh.grow_mesh(1)
        return self.connect_vertices_result

    def ConnectEdges(self, edgeFlag=1):
        self.calls.append(("ConnectEdges", edgeFlag))
        if self.connect_edges_result:
            self.mesh.grow_mesh(1)
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
        self.mesh.selections[ContextLevel.EDGE].add(2)

    def SelectElement(self):
        self.calls.append("SelectElement")
        self.mesh.selections[ContextLevel.POLYGON].add(2)

    def getCommandMode(self):
        return self.command_mode

    def toggleCommandMode(self, value):
        self.calls.append(("toggleCommandMode", str(value)))
        self.command_mode = str(value)

    def exitCommandModes(self):
        self.calls.append("exitCommandModes")
        self.command_mode = "none"


class FakeNode:
    def __init__(
        self,
        name: str = "Poly001",
        class_name: str = "Editable_Poly",
        handle: int = 101,
        base_handle: int = 501,
    ) -> None:
        self.name = name
        self.class_name = class_name
        self.handle = handle
        self.baseObject = FakeBaseObject(class_name, base_handle)
        self.modifiers = []
        self.mesh = FakeMesh()
        self.EditablePoly = FakeEditablePoly(self.mesh)
        self.transform = "matrix3"


class FakeModPanel:
    def __init__(self) -> None:
        self.current = None
        self.writes = []

    def getCurrentObject(self):
        return self.current

    def setCurrentObject(self, *args, **kwargs):
        self.writes.append("setCurrentObject")
        raise AssertionError("prototype must never write the active stack item")


class FakeRefs:
    def __init__(self, runtime: "FakeRuntime") -> None:
        self.runtime = runtime

    def dependentNodes(self, base, firstOnly=False, baseObjectOnly=True):
        self.runtime.accessed.append("dependentNodes")
        override = self.runtime.dependent_overrides.get(id(base))
        if override is not None:
            return list(override)
        for node in self.runtime.selection:
            if node.baseObject is base:
                return [node]
        return []


class FakeToolMode:
    def __init__(self) -> None:
        self.commandMode = "select"


class FakeUndoFactory:
    def __init__(self, restore=None, suppress: bool = False) -> None:
        self.restore = restore
        self.suppress = suppress
        self.calls = []
        self.entered = []
        self.exited = []
        self._label = ""

    def __call__(self, enabled, label):
        self.calls.append((enabled, label))
        self._label = label
        return self

    def __enter__(self):
        self.entered.append(self._label)
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.exited.append((self._label, exc_type))
        if exc_type is not None and self.restore is not None:
            self.restore()
        return self.suppress


_FORBIDDEN_RUNTIME_NAMES = frozenset(
    {
        "addModifier",
        "addmodifier",
        "collapseStack",
        "convertToMesh",
        "convertToPoly",
        "delete",
        "resetMaxFile",
        "run_redo",
        "run_undo",
        "setCommandPanelTaskMode",
        "setSelectionLevel",
        "undo",
    }
)


class FakeRuntime:
    def __init__(self) -> None:
        self.selection = []
        self.subObjectLevel = 0
        self.modPanel = FakeModPanel()
        self.polyop = FakePolyOp()
        self.refs = FakeRefs(self)
        self.toolMode = FakeToolMode()
        self.Name = FakeName
        self.Array = FakeArray
        self.theHold = FakeHold()
        self.command_panel_task_mode = "modify"
        self.currentTime = 0
        self.executed = []
        self.accessed = []
        self.dependent_overrides = {}
        self.Editable_Poly = FakeClass("Editable_Poly")
        self.Edit_Poly = FakeClass("Edit_Poly")

    def classOf(self, value):
        self.accessed.append("classOf")
        return FakeClass(getattr(value, "class_name", "unknown"))

    def isKindOf(self, value, expected_class):
        self.accessed.append("isKindOf")
        return self.classOf(value) == expected_class

    def getHandleByAnim(self, value):
        self.accessed.append("getHandleByAnim")
        return getattr(value, "handle", None)

    def execute(self, literal):
        self.accessed.append("execute")
        if literal in FIXED_MAX_COMMAND_LITERALS:
            self.executed.append(literal)
            return None
        if literal == "(fn (b) (b as array))":
            return lambda bitarray: list(bitarray)
        raise AssertionError("unexpected script literal: %r" % (literal,))

    def redrawViews(self):
        self.accessed.append("redrawViews")

    def getCommandPanelTaskMode(self):
        self.accessed.append("getCommandPanelTaskMode")
        return self.command_panel_task_mode

    def maxVersion(self):
        return "2025.0 fake"

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        self.accessed.append(name)
        if name in _FORBIDDEN_RUNTIME_NAMES:
            raise AssertionError("forbidden host API accessed: %s" % name)
        raise AttributeError(name)


_LEVEL_BY_INDEX = {
    0: ContextLevel.OBJECT,
    1: ContextLevel.VERTEX,
    2: ContextLevel.EDGE,
    3: ContextLevel.BORDER,
    4: ContextLevel.POLYGON,
    5: ContextLevel.ELEMENT,
}


def editable_setup(
    level: int = 0,
    verts=(),
    edges=(),
    faces=(),
    panel_open: bool = True,
    runtime: FakeRuntime | None = None,
):
    runtime = runtime or FakeRuntime()
    node = FakeNode("Poly001", class_name="Editable_Poly", handle=101)
    node.mesh.selections[ContextLevel.VERTEX].update(verts)
    node.mesh.selections[ContextLevel.EDGE].update(edges)
    node.mesh.selections[ContextLevel.BORDER].update(edges)
    node.mesh.selections[ContextLevel.POLYGON].update(faces)
    node.mesh.current_level = _LEVEL_BY_INDEX.get(level, ContextLevel.OBJECT)
    runtime.selection = [node]
    runtime.subObjectLevel = level
    runtime.modPanel.current = node.baseObject if panel_open else None
    undo = FakeUndoFactory()
    adapter = MaxHostAdapter(runtime=runtime, undo_factory=undo)
    return adapter, runtime, node, undo


def edit_poly_setup(level: int = 2, panel_open: bool = True):
    runtime = FakeRuntime()
    node = FakeNode("Poly001", class_name="Editable_Poly", handle=77)
    edit_poly = FakeModifier("Edit_Poly", handle=78)
    node.modifiers = [edit_poly]
    runtime.selection = [node]
    runtime.subObjectLevel = level
    runtime.modPanel.current = edit_poly if panel_open else None
    undo = FakeUndoFactory()
    adapter = MaxHostAdapter(runtime=runtime, undo_factory=undo)
    return adapter, runtime, node, edit_poly, undo


def manual_snapshot(
    level: ContextLevel,
    target_kind: str = "editable_poly",
    selection_count: int = 1,
    panel_ready: bool = True,
) -> ContextSnapshot:
    return ContextSnapshot(
        level=level,
        target_kind=target_kind,
        target_token="target-token",
        selection_count=selection_count,
        selection_token="selection-token",
        panel_ready=panel_ready,
        conflicting_mode=False,
        revision=7,
    )


class ImportAndBoundaryTests(unittest.TestCase):
    def test_importing_host_does_not_import_pymxs_or_qt(self):
        script = (
            "import sys\n"
            "import max2025_menu_prototype.max_host as host\n"
            "print(host.MaxHostAdapter.__name__)\n"
            "print('pymxs' in sys.modules)\n"
            "print('PySide6' in sys.modules)\n"
        )
        completed = checkout_support.run_python(script)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        lines = completed.stdout.strip().splitlines()
        self.assertEqual(lines[0], "MaxHostAdapter")
        self.assertEqual(lines[1], "False")
        self.assertEqual(lines[2], "False")

    def test_missing_pymxs_is_a_lazy_clear_error(self):
        adapter = MaxHostAdapter()
        with mock.patch.dict(sys.modules, {"pymxs": None}):
            with self.assertRaises(RuntimeError) as raised:
                adapter.snapshot()
        self.assertIn("pymxs", str(raised.exception))

    def test_bootstrap_uninstall_is_idempotent_outside_max(self):
        bootstrap.uninstall()
        bootstrap.uninstall()

    def test_availability_without_a_runtime_is_conservative(self):
        adapter = MaxHostAdapter()
        result = adapter.availability(manual_snapshot(ContextLevel.POLYGON))
        self.assertFalse(result["model.extrude"].enabled)
        self.assertIn("runtime", result["model.extrude"].reason)


class LevelAndTargetTests(unittest.TestCase):
    def test_subobject_levels_map_zero_to_five_for_supported_target(self):
        expected = list(_LEVEL_BY_INDEX.values())
        for index, level in enumerate(expected):
            adapter, _runtime, _node, _undo = editable_setup(
                level=index, verts=(1,), edges=(1,), faces=(1,)
            )
            self.assertEqual(adapter.detect_context(), level)
            self.assertEqual(adapter.snapshot().level, level)

    def test_unknown_level_is_not_guessed_into_a_modeling_context(self):
        adapter, runtime, _node, _undo = editable_setup(level=99)
        self.assertEqual(adapter.detect_context(), ContextLevel.OBJECT)
        snapshot = adapter.snapshot()
        self.assertEqual(snapshot.level, ContextLevel.OBJECT)
        self.assertTrue(snapshot.selection_token.startswith("unverified-subobject:"))
        availability = adapter.availability(snapshot)
        self.assertFalse(availability["context.vertex"].enabled)

    def test_no_selection_reports_none_and_readable_reasons(self):
        runtime = FakeRuntime()
        adapter = MaxHostAdapter(runtime=runtime)
        snapshot = adapter.snapshot()
        self.assertEqual(snapshot.level, ContextLevel.OBJECT)
        self.assertEqual(snapshot.target_kind, "none")
        self.assertEqual(snapshot.selection_count, 0)
        availability = adapter.availability(snapshot)
        self.assertFalse(availability["transform.move"].enabled)
        self.assertIn("Select", availability["transform.move"].reason)

    def test_primitive_stays_other_and_is_never_converted(self):
        runtime = FakeRuntime()
        node = FakeNode("Box001", class_name="Box", handle=55)
        runtime.selection = [node]
        runtime.modPanel.current = node.baseObject
        adapter = MaxHostAdapter(runtime=runtime)
        snapshot = adapter.snapshot()
        self.assertEqual(snapshot.target_kind, "other")
        availability = adapter.availability(snapshot)
        self.assertFalse(availability["model.chamfer"].enabled)
        manual = adapter.availability(
            manual_snapshot(ContextLevel.VERTEX, target_kind="other")
        )
        self.assertFalse(manual["model.chamfer"].enabled)
        self.assertIn("no conversion", manual["model.chamfer"].reason)
        self.assertNotIn("convertToPoly", runtime.accessed)
        self.assertNotIn("convertToMesh", runtime.accessed)

    def test_active_edit_poly_is_explicitly_unsupported(self):
        adapter, runtime, _node, _edit_poly, _undo = edit_poly_setup(level=1)
        snapshot = adapter.snapshot()
        self.assertEqual(snapshot.target_kind, "edit_poly")
        self.assertEqual(snapshot.level, ContextLevel.OBJECT)
        manual = adapter.availability(
            manual_snapshot(ContextLevel.VERTEX, target_kind="edit_poly")
        )
        self.assertFalse(manual["model.chamfer"].enabled)
        self.assertIn("Edit Poly", manual["model.chamfer"].reason)
        self.assertNotIn("addModifier", runtime.accessed)

    def test_edit_poly_subobject_transforms_are_blocked(self):
        adapter, runtime, _node, _edit_poly, _undo = edit_poly_setup(level=2)
        snapshot = adapter.snapshot()
        self.assertEqual(snapshot.level, ContextLevel.OBJECT)
        availability = adapter.availability(snapshot)
        for action_id in ("transform.move", "transform.rotate", "transform.scale"):
            self.assertFalse(availability[action_id].enabled, action_id)
            self.assertIn("cannot be verified", availability[action_id].reason)
        result = adapter.launch("transform.move", snapshot)
        self.assertEqual(result.status, "blocked")
        direct = launch_native(runtime, "transform.move", snapshot)
        self.assertEqual(direct.status, "blocked")
        self.assertEqual(runtime.executed, [])

    def test_edit_poly_object_level_transform_still_allowed(self):
        adapter, runtime, _node, _edit_poly, _undo = edit_poly_setup(level=0)
        snapshot = adapter.snapshot()
        self.assertEqual(snapshot.target_kind, "edit_poly")
        self.assertTrue(adapter.availability(snapshot)["transform.move"].enabled)
        result = adapter.launch("transform.move", snapshot)
        self.assertEqual(result.status, "launched")
        self.assertEqual(runtime.executed, ["max move"])

    def test_modifier_stack_above_editable_poly_is_unsupported(self):
        adapter, runtime, node, _undo = editable_setup(level=4, faces=(1,))
        node.modifiers = [FakeModifier("Bend", handle=300)]
        runtime.modPanel.current = node.baseObject
        snapshot = adapter.snapshot()
        self.assertEqual(snapshot.target_kind, "other")
        self.assertEqual(snapshot.level, ContextLevel.OBJECT)
        manual = adapter.availability(
            manual_snapshot(ContextLevel.POLYGON, target_kind="other")
        )
        self.assertFalse(manual["model.bevel"].enabled)
        self.assertIn("unmodified", manual["model.bevel"].reason)

    def test_multiple_objects_are_unsupported_for_subobjects(self):
        runtime = FakeRuntime()
        runtime.selection = [
            FakeNode("Poly001", handle=1),
            FakeNode("Poly002", handle=2),
        ]
        runtime.subObjectLevel = 2
        runtime.modPanel.current = runtime.selection[0].baseObject
        adapter = MaxHostAdapter(runtime=runtime)
        snapshot = adapter.snapshot()
        self.assertEqual(snapshot.target_kind, "multiple")
        self.assertEqual(snapshot.level, ContextLevel.OBJECT)
        manual = adapter.availability(
            manual_snapshot(ContextLevel.EDGE, target_kind="multiple")
        )
        self.assertFalse(manual["selection.loop"].enabled)
        self.assertIn("single", manual["selection.loop"].reason)

    def test_closed_modify_panel_disables_topology_with_panel_reason(self):
        adapter, runtime, _node, _undo = editable_setup(level=1, panel_open=False)
        runtime.subObjectLevel = 1
        snapshot = adapter.snapshot()
        self.assertFalse(snapshot.panel_ready)
        self.assertEqual(snapshot.level, ContextLevel.OBJECT)
        self.assertIn("cannot be verified", adapter.availability(snapshot)["transform.move"].reason)
        manual = adapter.availability(
            manual_snapshot(ContextLevel.VERTEX, panel_ready=False)
        )
        self.assertFalse(manual["model.chamfer"].enabled)
        self.assertIn("Modify panel", manual["model.chamfer"].reason)

    def test_non_modify_task_mode_disables_topology_but_keeps_object_transform(self):
        adapter, runtime, _node, _undo = editable_setup(level=0)
        for task_mode in ("create", "hierarchy", "motion", "display", "utility"):
            with self.subTest(task_mode=task_mode):
                runtime.command_panel_task_mode = task_mode
                snapshot = adapter.snapshot()
                self.assertFalse(snapshot.panel_ready)
                self.assertEqual(snapshot.level, ContextLevel.OBJECT)
                availability = adapter.availability(snapshot)
                self.assertFalse(availability["context.vertex"].enabled)
                self.assertIn("Modify task", availability["context.vertex"].reason)
                # Deliberate object-level transform eligibility is unchanged
                # when no sub-object state needs unverifiable-target protection.
                self.assertTrue(availability["transform.move"].enabled)

    def test_unreadable_or_missing_task_mode_fails_closed(self):
        adapter, runtime, _node, _undo = editable_setup(level=4, faces=(1,))
        runtime.command_panel_task_mode = None
        snapshot = adapter.snapshot()
        self.assertFalse(snapshot.panel_ready)
        self.assertIn(
            "getCommandPanelTaskMode",
            adapter.availability(snapshot)["context.vertex"].reason,
        )

        runtime.getCommandPanelTaskMode = None
        snapshot = adapter.snapshot()
        self.assertFalse(snapshot.panel_ready)
        self.assertEqual(snapshot.level, ContextLevel.OBJECT)

    def test_task_mode_change_between_snapshot_and_launch_blocks(self):
        adapter, runtime, _node, undo = editable_setup(level=0)
        snapshot = adapter.snapshot()
        runtime.command_panel_task_mode = "create"
        fresh = adapter.snapshot()
        # The command-panel task mode is part of the stale-context revision.
        self.assertNotEqual(snapshot.revision, fresh.revision)
        result = adapter.launch("context.vertex", snapshot)
        self.assertEqual(result.status, "blocked")
        self.assertIn("Context changed", result.message)
        self.assertEqual(undo.calls, [])

    def test_scale_tool_modes_are_idle_not_foreign(self):
        adapter, runtime, _node, _undo = editable_setup(level=4, faces=(1,))
        for mode in ("scale", "uscale", "nuscale", "squash"):
            with self.subTest(mode=mode):
                runtime.toolMode.commandMode = mode
                snapshot = adapter.snapshot()
                self.assertFalse(snapshot.conflicting_mode)
                availability = adapter.availability(snapshot)
                self.assertTrue(availability["model.extrude"].enabled)

    def test_duplicate_names_are_not_identity(self):
        runtime = FakeRuntime()
        first = FakeNode("SameName", handle=10)
        second = FakeNode("SameName", handle=20)
        runtime.selection = [first]
        runtime.modPanel.current = first.baseObject
        adapter = MaxHostAdapter(runtime=runtime)
        first_snapshot = adapter.snapshot()
        runtime.selection = [second]
        runtime.modPanel.current = second.baseObject
        second_snapshot = adapter.snapshot()
        self.assertNotEqual(first_snapshot.target_token, second_snapshot.target_token)
        self.assertNotEqual(first_snapshot.revision, second_snapshot.revision)

    def test_missing_stable_handle_fails_closed_without_name_fallback(self):
        runtime = FakeRuntime()
        node = FakeNode("StillHasName", handle=None)
        runtime.selection = [node]
        runtime.modPanel.current = node.baseObject
        adapter = MaxHostAdapter(runtime=runtime)
        snapshot = adapter.snapshot()
        self.assertEqual(snapshot.target_kind, "other")
        reason = adapter.availability(snapshot)["context.vertex"].reason
        self.assertIn("stable node handle", reason)

    def test_base_object_equality_is_not_python_identity(self):
        adapter, runtime, node, _undo = editable_setup(level=2)
        other_base = FakeBaseObject("Editable_Poly", handle=999)
        runtime.modPanel.current = other_base
        snapshot = adapter.snapshot()
        self.assertFalse(snapshot.panel_ready)
        self.assertEqual(snapshot.level, ContextLevel.OBJECT)
        self.assertTrue(snapshot.selection_token.startswith("unverified-subobject:"))

    def test_shared_or_instanced_base_geometry_is_rejected(self):
        adapter, runtime, node, _undo = editable_setup(level=4, faces=(1,))
        other = FakeNode("Instance2", handle=202)
        runtime.dependent_overrides[id(node.baseObject)] = [node, other]
        snapshot = adapter.snapshot()
        self.assertEqual(snapshot.target_kind, "editable_poly")
        availability = adapter.availability(snapshot)
        self.assertFalse(availability["model.extrude"].enabled)
        self.assertIn("Instanced", availability["model.extrude"].reason)
        result = adapter.launch("model.extrude", snapshot)
        self.assertEqual(result.status, "blocked")

    def test_motion_panel_or_no_current_object_is_not_a_supported_target(self):
        adapter, runtime, _node, _undo = editable_setup(level=1, panel_open=False)
        runtime.modPanel.current = None
        snapshot = adapter.snapshot()
        self.assertFalse(snapshot.panel_ready)
        self.assertTrue(snapshot.selection_token.startswith("unverified-subobject:"))

    def test_every_disabled_reason_is_nonempty(self):
        adapter, _runtime, _node, _undo = editable_setup(level=4, faces=(1,))
        snapshots = [
            adapter.snapshot(),
            manual_snapshot(ContextLevel.VERTEX, selection_count=0),
            manual_snapshot(ContextLevel.BORDER, target_kind="edit_poly"),
            manual_snapshot(ContextLevel.EDGE, target_kind="multiple"),
        ]
        for snapshot in snapshots:
            for action_id, availability in adapter.availability(snapshot).items():
                if not availability.enabled:
                    self.assertTrue(
                        availability.reason.strip(),
                        "%s disabled without reason" % action_id,
                    )


def adapter_reason(runtime: FakeRuntime, snapshot: ContextSnapshot) -> str:
    adapter = MaxHostAdapter(runtime=runtime)
    availability = adapter.availability(snapshot)
    return availability["model.chamfer"].reason


class IdentityAndRevisionTests(unittest.TestCase):
    def test_equal_component_counts_with_different_selection_are_tokens(self):
        adapter, _runtime, node, _undo = editable_setup(level=1, verts=(1,))
        first = adapter.snapshot()
        node.mesh.selections[ContextLevel.VERTEX] = {2}
        second = adapter.snapshot()
        self.assertEqual(first.selection_count, 1)
        self.assertEqual(second.selection_count, 1)
        self.assertNotEqual(first.selection_token, second.selection_token)
        self.assertNotEqual(first.revision, second.revision)

    def test_same_count_vertex_move_changes_revision(self):
        adapter, _runtime, node, _undo = editable_setup(level=4, faces=(1,))
        first = adapter.snapshot()
        node.mesh.vertices[0] = (9.0, 9.0, 9.0)
        second = adapter.snapshot()
        self.assertEqual(first.selection_count, second.selection_count)
        self.assertNotEqual(first.revision, second.revision)

    def test_same_count_face_winding_changes_revision(self):
        adapter, _runtime, node, _undo = editable_setup(level=4, faces=(1,))
        first = adapter.snapshot()
        node.mesh.faces[0] = list(reversed(node.mesh.faces[0]))
        second = adapter.snapshot()
        self.assertNotEqual(first.revision, second.revision)

    def test_changed_selection_blocks_before_dispatch(self):
        adapter, runtime, node, _undo = editable_setup(level=4, faces=(1,))
        snapshot = adapter.snapshot()
        node.mesh.selections[ContextLevel.POLYGON] = {1, 2}
        result = adapter.launch(
            "model.extrude",
            snapshot,
            {"height": 1.0, "extrusion_type": "group"},
        )
        self.assertEqual(result.status, "blocked")
        self.assertIn("Context changed", result.message)
        self.assertNotIn("extrudeFaces", [call for call in node.mesh.calls if isinstance(call, str)])

    def test_changed_target_handle_blocks_before_dispatch(self):
        adapter, _runtime, node, _undo = editable_setup(level=0)
        snapshot = adapter.snapshot()
        node.handle = 202
        result = adapter.launch("transform.rotate", snapshot)
        self.assertEqual(result.status, "blocked")

    def test_stack_change_changes_revision_and_blocks_stale_launch(self):
        adapter, runtime, node, _undo = editable_setup(level=0)
        snapshot = adapter.snapshot()
        node.modifiers.append(FakeModifier("Bend", handle=444))
        runtime.modPanel.current = node.baseObject
        fresh = adapter.snapshot()
        self.assertNotEqual(snapshot.revision, fresh.revision)
        self.assertEqual(fresh.target_kind, "other")
        result = adapter.launch("transform.move", snapshot)
        self.assertEqual(result.status, "blocked")

    def test_level_change_blocks_before_dispatch(self):
        adapter, runtime, _node, _undo = editable_setup(level=0)
        snapshot = adapter.snapshot()
        runtime.subObjectLevel = 1
        result = adapter.launch("transform.scale", snapshot)
        self.assertEqual(result.status, "blocked")

    def test_deleted_target_blocks_before_dispatch(self):
        adapter, runtime, _node, _undo = editable_setup(level=0)
        snapshot = adapter.snapshot()
        runtime.selection = []
        runtime.modPanel.current = None
        result = adapter.launch("view.frame_selection", snapshot)
        self.assertEqual(result.status, "blocked")
        self.assertIn("Context changed", result.message)
        self.assertEqual(runtime.executed, [])

    def test_over_limit_geometry_disables_topology_not_level_buttons(self):
        adapter, _runtime, node, _undo = editable_setup(level=4, faces=(1,))
        node.mesh.vertices = [(0.0, 0.0, 0.0)] * 20001
        snapshot = adapter.snapshot()
        availability = adapter.availability(snapshot)
        self.assertFalse(availability["model.extrude"].enabled)
        self.assertIn("limit", availability["model.extrude"].reason)
        self.assertTrue(availability["context.vertex"].enabled)

    def test_unreadable_component_indices_disable_topology(self):
        adapter, _runtime, node, _undo = editable_setup(level=1, verts=(1, 2, 3))
        node.mesh.broken_level = ContextLevel.VERTEX
        snapshot = adapter.snapshot()
        self.assertEqual(snapshot.selection_count, 3)
        availability = adapter.availability(snapshot)
        self.assertFalse(availability["model.chamfer"].enabled)
        self.assertIn("indices", availability["model.chamfer"].reason)

    def test_active_command_mode_disables_topology_with_reason(self):
        adapter, _runtime, node, _undo = editable_setup(level=4, faces=(1,))
        node.EditablePoly.command_mode = "ChamferEdge"
        snapshot = adapter.snapshot()
        self.assertTrue(snapshot.conflicting_mode)
        availability = adapter.availability(snapshot)
        self.assertFalse(availability["model.extrude"].enabled)
        self.assertIn("command mode", availability["model.extrude"].reason)

    def test_unknown_tool_mode_disables_topology(self):
        adapter, runtime, _node, _undo = editable_setup(level=4, faces=(1,))
        runtime.toolMode.commandMode = None
        snapshot = adapter.snapshot()
        self.assertTrue(snapshot.conflicting_mode)
        availability = adapter.availability(snapshot)
        self.assertFalse(availability["model.bevel"].enabled)
        self.assertIn("toolMode", availability["model.bevel"].reason)


class ParameterAndDispatchTests(unittest.TestCase):
    def test_parameterized_action_without_parameters_is_blocked(self):
        adapter, runtime, node, undo = editable_setup(level=4, faces=(1,))
        snapshot = adapter.snapshot()
        result = adapter.launch("model.extrude", snapshot)
        self.assertEqual(result.status, "blocked")
        self.assertIn("parameters", result.message)
        self.assertEqual(undo.calls, [])

    def test_invalid_or_extra_parameters_are_blocked(self):
        adapter, _runtime, _node, undo = editable_setup(level=4, faces=(1,))
        snapshot = adapter.snapshot()
        result = adapter.launch(
            "model.extrude", snapshot, {"height": 1.0, "extrusion_type": "bad"}
        )
        self.assertEqual(result.status, "blocked")
        extra = adapter.launch(
            "model.extrude",
            snapshot,
            {"height": 1.0, "extrusion_type": "group", "evil": 1},
        )
        self.assertEqual(extra.status, "blocked")
        self.assertEqual(undo.calls, [])

    def test_extrude_applies_in_one_undo_chunk(self):
        adapter, _runtime, node, undo = editable_setup(level=4, faces=(1,))
        snapshot = adapter.snapshot()
        result = adapter.launch(
            "model.extrude",
            snapshot,
            {"height": 2.0, "extrusion_type": "local_normal"},
        )
        self.assertEqual(result.status, "applied")
        self.assertEqual(len(undo.calls), 1)
        self.assertTrue(any(
            isinstance(call, tuple) and call[0] == "extrudeFaces"
            for call in node.mesh.calls
        ))
        self.assertEqual(node.baseObject.extrusionType, 0)

    def test_zero_height_extrude_is_noop_without_an_undo_chunk(self):
        adapter, _runtime, _node, undo = editable_setup(level=4, faces=(1,))
        snapshot = adapter.snapshot()
        result = adapter.launch(
            "model.extrude",
            snapshot,
            {"height": 0.0, "extrusion_type": "group"},
        )
        self.assertEqual(result.status, "noop")
        self.assertEqual(undo.calls, [])

    def test_p0_operations_apply_through_documented_members(self):
        cases = [
            (ContextLevel.POLYGON, (1,), "model.bevel",
             {"height": 1.0, "outline": -0.1, "extrusion_type": "group"}),
            (ContextLevel.POLYGON, (1,), "model.inset",
             {"amount": 0.1, "inset_type": "group"}),
            (ContextLevel.VERTEX, (1,), "model.chamfer",
             {"amount": 0.1, "open": False}),
            (ContextLevel.EDGE, (1,), "model.chamfer",
             {"amount": 0.1, "open": False}),
            (ContextLevel.VERTEX, (1,), "selection.grow", {}),
            (ContextLevel.EDGE, (1,), "selection.loop", {}),
        ]
        for level, selection, action_id, parameters in cases:
            with self.subTest(action=action_id, level=level):
                kwargs = {
                    "verts": selection if level == ContextLevel.VERTEX else (),
                    "edges": selection if level == ContextLevel.EDGE else (),
                    "faces": selection if level == ContextLevel.POLYGON else (),
                }
                index = {value: key for key, value in _LEVEL_BY_INDEX.items()}[level]
                adapter, _runtime, node, undo = editable_setup(level=index, **kwargs)
                snapshot = adapter.snapshot()
                result = adapter.launch(action_id, snapshot, parameters)
                self.assertEqual(result.status, "applied", result.message)
                self.assertEqual(len(undo.calls), 1)

    def test_level_actions_set_native_level_without_undo(self):
        adapter, runtime, _node, undo = editable_setup(level=0)
        snapshot = adapter.snapshot()
        result = adapter.launch("context.vertex", snapshot)
        self.assertEqual(result.status, "launched")
        self.assertEqual(runtime.subObjectLevel, 1)
        self.assertEqual(undo.calls, [])

    def test_transform_and_frame_dispatch_use_only_fixed_literals(self):
        adapter, runtime, _node, undo = editable_setup(level=0)
        snapshot = adapter.snapshot()
        self.assertEqual(adapter.launch("transform.move", snapshot).status, "launched")
        self.assertEqual(adapter.launch("view.frame_selection", snapshot).status, "launched")
        self.assertEqual(runtime.executed, ["max move", "max zoomext sel"])
        self.assertTrue(set(runtime.executed).issubset(FIXED_MAX_COMMAND_LITERALS))
        self.assertEqual(undo.calls, [])

    def test_host_exception_becomes_error_never_success(self):
        adapter, runtime, _node, _undo = editable_setup(level=0)
        snapshot = adapter.snapshot()

        def explode(_literal):
            raise RuntimeError("host exploded")

        runtime.execute = explode
        result = adapter.launch("transform.move", snapshot)
        self.assertEqual(result.status, "error")
        self.assertIn("RuntimeError", result.message)

    def test_unknown_and_injected_action_strings_never_reach_execute(self):
        adapter, runtime, _node, _undo = editable_setup(level=0)
        snapshot = adapter.snapshot()
        for injected in (
            "max move; delete objects",
            "model.unknown",
            "__import__('os').system('echo x')",
        ):
            result = adapter.launch(injected, snapshot)
            self.assertEqual(result.status, "blocked")
        self.assertEqual(runtime.executed, [])

    def test_no_forbidden_scene_or_stack_mutation_apis_are_used(self):
        adapter, runtime, node, _undo = editable_setup(level=4, faces=(1,))
        snapshot = adapter.snapshot()
        adapter.availability(snapshot)
        adapter.launch(
            "model.extrude",
            snapshot,
            {"height": 1.0, "extrusion_type": "group"},
        )
        forbidden = _FORBIDDEN_RUNTIME_NAMES.intersection(runtime.accessed)
        self.assertEqual(forbidden, set())
        self.assertEqual(runtime.modPanel.writes, [])


class UndoAndRollbackTests(unittest.TestCase):
    def _extrude_with_failure(self, suppress: bool, restore: bool):
        adapter, _runtime, node, undo = editable_setup(level=4, faces=(1,))
        snapshot = adapter.snapshot()
        state = node.mesh.snapshot()
        undo.suppress = suppress
        if restore:
            undo.restore = lambda: node.mesh.restore(state)
        original = node.mesh.extrudeFaces

        def mutate_then_raise(node_arg, face_list, amount):
            original(node_arg, face_list, amount)
            raise RuntimeError("partial native failure")

        node.mesh.extrudeFaces = mutate_then_raise
        result = adapter.launch(
            "model.extrude",
            snapshot,
            {"height": 1.0, "extrusion_type": "group"},
        )
        return result, adapter, snapshot, node

    def test_failure_with_rollback_is_error_not_applied(self):
        result, adapter, snapshot, node = self._extrude_with_failure(
            suppress=False, restore=True
        )
        self.assertEqual(result.status, "error")
        self.assertIn("restored", result.message)
        self.assertNotIn("latched", result.message)
        self.assertTrue(adapter.availability(snapshot)["model.extrude"].enabled)

    def test_unreadable_pre_edit_time_fails_before_undo_or_native_edit(self):
        for unreadable in (None, "   ", "time:unreadable", "TIME:UNREADABLE"):
            with self.subTest(unreadable=repr(unreadable)):
                adapter, runtime, node, undo = editable_setup(
                    level=4, faces=(1,)
                )
                runtime.currentTime = unreadable
                snapshot = adapter.snapshot()
                before = node.mesh.snapshot()
                result = adapter.launch(
                    "model.extrude",
                    snapshot,
                    {"height": 1.0, "extrusion_type": "group"},
                )
                self.assertEqual(result.status, "error")
                self.assertIn("pre-edit state", result.message)
                self.assertIn("current time", result.message)
                self.assertIn("unreadable", result.message)
                self.assertNotIn("restored", result.message.lower())
                self.assertEqual(undo.calls, [])
                self.assertEqual(node.mesh.snapshot(), before)
                self.assertFalse(
                    any(
                        isinstance(call, tuple) and call[0] == "extrudeFaces"
                        for call in node.mesh.calls
                    )
                )
                # No edit began, so this is not a post-failure latch.
                self.assertTrue(
                    adapter.availability(snapshot)["model.extrude"].enabled
                )

    def test_unreadable_post_failure_time_latches_without_verified_rollback(self):
        for suppress in (False, True):
            with self.subTest(suppress=suppress):
                adapter, runtime, node, undo = editable_setup(
                    level=4, faces=(1,)
                )
                snapshot = adapter.snapshot()
                state = node.mesh.snapshot()
                undo.suppress = suppress
                undo.restore = lambda: node.mesh.restore(state)
                original = node.mesh.extrudeFaces

                def mutate_then_raise(node_arg, face_list, amount):
                    original(node_arg, face_list, amount)
                    # The fake undo restores geometry, but the post-failure
                    # time read stays unreadable so rollback cannot be proven.
                    runtime.currentTime = None
                    raise RuntimeError("partial native failure")

                node.mesh.extrudeFaces = mutate_then_raise
                result = adapter.launch(
                    "model.extrude",
                    snapshot,
                    {"height": 1.0, "extrusion_type": "group"},
                )
                self.assertEqual(result.status, "error")
                self.assertIn("latched", result.message)
                self.assertIn("could not be re-read", result.message)
                self.assertNotIn("restored the pre-edit", result.message)
                self.assertNotIn("restored", result.message.lower())
                self.assertEqual(len(undo.calls), 1)
                self.assertNotIn("run_undo", runtime.accessed)
                self.assertEqual(node.mesh.snapshot(), state)
                fresh = adapter.snapshot()
                availability = adapter.availability(fresh)
                self.assertFalse(availability["model.bevel"].enabled)
                self.assertIn("latched", availability["model.bevel"].reason)

    def test_suppressed_exception_without_rollback_latches_topology(self):
        result, adapter, snapshot, _node = self._extrude_with_failure(
            suppress=True, restore=False
        )
        self.assertEqual(result.status, "error")
        self.assertIn("latched", result.message)
        fresh = adapter.snapshot()
        availability = adapter.availability(fresh)
        self.assertFalse(availability["model.bevel"].enabled)
        self.assertIn("latched", availability["model.bevel"].reason)

    def test_unreadable_post_settings_latches_instead_of_false_rollback(self):
        adapter, _runtime, node, undo = editable_setup(level=4, faces=(1,))
        snapshot = adapter.snapshot()
        state = node.mesh.snapshot()
        undo.suppress = True
        undo.restore = lambda: node.mesh.restore(state)
        original = node.mesh.extrudeFaces

        def mutate_then_raise(node_arg, face_list, amount):
            original(node_arg, face_list, amount)
            del node.baseObject.insetAmount
            raise RuntimeError("partial native failure")

        node.mesh.extrudeFaces = mutate_then_raise
        result = adapter.launch(
            "model.extrude",
            snapshot,
            {"height": 1.0, "extrusion_type": "group"},
        )
        self.assertEqual(result.status, "error")
        self.assertIn("latched", result.message)
        fresh = adapter.snapshot()
        availability = adapter.availability(fresh)
        self.assertFalse(availability["model.bevel"].enabled)
        self.assertIn("latched", availability["model.bevel"].reason)

    def test_active_hold_blocks_launch_before_the_undo_chunk(self):
        adapter, runtime, _node, undo = editable_setup(level=4, faces=(1,))
        snapshot = adapter.snapshot()
        runtime.theHold.holding = True
        fresh = adapter.snapshot()
        self.assertNotEqual(snapshot.revision, fresh.revision)
        result = adapter.launch(
            "model.extrude",
            snapshot,
            {"height": 1.0, "extrusion_type": "group"},
        )
        self.assertEqual(result.status, "blocked")
        self.assertIn("Context changed", result.message)
        self.assertEqual(undo.calls, [])

    def test_anim_handle_identity_works_without_base_handle_property(self):
        adapter, runtime, node, undo = editable_setup(level=4, faces=(1,))
        del node.baseObject.handle
        handles = {id(node): 101, id(node.baseObject): 501}
        runtime.getHandleByAnim = lambda value: handles.get(id(value))
        snapshot = adapter.snapshot()
        result = adapter.launch(
            "model.extrude",
            snapshot,
            {"height": 1.0, "extrusion_type": "group"},
        )
        self.assertEqual(result.status, "applied")
        self.assertEqual(len(undo.calls), 1)
        self.assertFalse(hasattr(node.baseObject, "handle"))

    def test_invalid_anim_handle_rejects_before_the_undo_chunk(self):
        adapter, runtime, _node, undo = editable_setup(level=4, faces=(1,))
        calls = {"count": 0}

        def handle_by_anim(_value):
            calls["count"] += 1
            # Snapshot and launch inspection each query node token, base token,
            # and object-selection token; the pre-edit fingerprint is the first
            # query after those six and fails closed.
            return 101 if calls["count"] <= 6 else None

        runtime.getHandleByAnim = handle_by_anim
        snapshot = adapter.snapshot()
        result = adapter.launch(
            "model.extrude",
            snapshot,
            {"height": 1.0, "extrusion_type": "group"},
        )
        self.assertEqual(result.status, "error")
        self.assertIn("anim handle", result.message)
        self.assertEqual(undo.calls, [])

    def test_false_boolean_return_never_becomes_applied(self):
        adapter, runtime, node, undo = editable_setup(level=2, edges=(1, 2))
        snapshot = adapter.snapshot()
        state = node.mesh.snapshot()
        undo.restore = lambda: node.mesh.restore(state)
        node.EditablePoly.connect_edges_result = False
        result = adapter.launch(
            "model.connect",
            snapshot,
            {"segments": 2, "pinch": 0, "slide": 0},
        )
        self.assertEqual(result.status, "error")
        self.assertIn("failed", result.message.lower())

    def test_missing_documented_member_has_a_specific_reason(self):
        adapter, runtime, _node, _undo = editable_setup(level=4, faces=(1,))
        runtime.polyop.extrudeFaces = None
        snapshot = adapter.snapshot()
        availability = adapter.availability(snapshot)
        self.assertFalse(availability["model.extrude"].enabled)
        self.assertIn("polyop.extrudeFaces", availability["model.extrude"].reason)


class ModeOwnershipTests(unittest.TestCase):
    def test_cut_and_target_weld_are_never_toggled_blindly(self):
        adapter, _runtime, node, undo = editable_setup(level=2, edges=(1,))
        snapshot = adapter.snapshot()
        entered = adapter.launch("model.cut", snapshot)
        self.assertEqual(entered.status, "launched")
        self.assertEqual(node.EditablePoly.command_mode, "CutEdge")
        self.assertEqual(undo.calls, [])

        # A same-mode relaunch must not toggle the mode off.
        second_snapshot = adapter.snapshot()
        second = adapter.launch("model.cut", second_snapshot)
        self.assertEqual(second.status, "blocked")
        toggles = [
            call for call in node.EditablePoly.calls
            if isinstance(call, tuple) and call[0] == "toggleCommandMode"
        ]
        self.assertEqual(len(toggles), 1)

        foreign = adapter.launch("model.target_weld", second_snapshot)
        self.assertEqual(foreign.status, "blocked")
        self.assertEqual(node.EditablePoly.command_mode, "CutEdge")

    def test_exit_tool_only_exits_exact_owned_mode(self):
        adapter, runtime, node, _undo = editable_setup(level=2, edges=(1,))
        snapshot = adapter.snapshot()
        self.assertEqual(adapter.launch("model.cut", snapshot).status, "launched")
        active = adapter.snapshot()
        self.assertTrue(adapter.availability(active)["model.exit_tool"].enabled)
        exited = adapter.launch("model.exit_tool", active)
        self.assertEqual(exited.status, "launched")
        self.assertEqual(node.EditablePoly.command_mode, "none")
        self.assertIn("exitCommandModes", node.EditablePoly.calls)

        # A foreign mode is never exited.
        node.EditablePoly.command_mode = "ChamferEdge"
        foreign = adapter.snapshot()
        self.assertFalse(adapter.availability(foreign)["model.exit_tool"].enabled)
        blocked = adapter.launch("model.exit_tool", foreign)
        self.assertEqual(blocked.status, "blocked")
        self.assertEqual(node.EditablePoly.command_mode, "ChamferEdge")

    def test_exit_tool_does_not_use_a_stale_target(self):
        adapter, _runtime, node, _undo = editable_setup(level=2, edges=(1,))
        snapshot = adapter.snapshot()
        self.assertEqual(adapter.launch("model.cut", snapshot).status, "launched")
        active = adapter.snapshot()
        node.handle = 303
        stale = adapter.snapshot()
        self.assertFalse(adapter.availability(stale)["model.exit_tool"].enabled)
        result = adapter.launch("model.exit_tool", stale)
        self.assertEqual(result.status, "blocked")
        self.assertEqual(node.EditablePoly.command_mode, "CutEdge")

    def test_public_release_owned_mode_is_exact_and_does_not_exit_foreign_tools(self):
        adapter, _runtime, node, _undo = editable_setup(level=2, edges=(1,))
        snapshot = adapter.snapshot()
        self.assertEqual(adapter.launch("model.cut", snapshot).status, "launched")

        released = adapter.release_owned_mode()
        self.assertEqual(released.status, "launched")
        self.assertEqual(node.EditablePoly.command_mode, "none")
        self.assertIn("exitCommandModes", node.EditablePoly.calls)
        self.assertEqual(adapter.release_owned_mode().status, "blocked")

        # Re-enter, then let a foreign tool take the mode.  Ownership is stale.
        active = adapter.snapshot()
        self.assertEqual(adapter.launch("model.cut", active).status, "launched")
        node.EditablePoly.command_mode = "ChamferEdge"
        calls_before = len(node.EditablePoly.calls)
        foreign = adapter.release_owned_mode()
        self.assertEqual(foreign.status, "blocked")
        self.assertEqual(node.EditablePoly.command_mode, "ChamferEdge")
        self.assertEqual(len(node.EditablePoly.calls), calls_before)


class CatalogAndReadOnlyTests(unittest.TestCase):
    def test_binding_coverage_is_derived_from_core_catalog(self):
        self.assertEqual(CATALOG_ACTION_IDS, tuple(action.id for action in ACTIONS))
        for action in ACTIONS:
            if action.id == "menu.repeat":
                self.assertNotIn(action.id, NATIVE_BINDINGS)
                continue
            binding = NATIVE_BINDINGS[action.id]
            self.assertTrue(binding.documentation)
            self.assertEqual(binding.runtime_status, "not_run")
            self.assertTrue(binding.check)
            self.assertTrue(binding.allowed_levels)
            self.assertTrue(set(binding.allowed_levels).issubset(set(ContextLevel)))

    def test_snapshot_availability_and_probe_are_read_only(self):
        adapter, runtime, node, undo = editable_setup(level=4, faces=(1,))
        before = node.mesh.snapshot()
        before_modifiers = list(node.modifiers)
        before_level = runtime.subObjectLevel
        snapshot = adapter.snapshot()
        adapter.availability(snapshot)
        build_report(runtime)
        self.assertEqual(node.mesh.snapshot(), before)
        self.assertEqual(node.modifiers, before_modifiers)
        self.assertEqual(runtime.subObjectLevel, before_level)
        self.assertEqual(undo.calls, [])
        self.assertNotIn("toggleCommandMode", [str(call) for call in node.EditablePoly.calls])
        self.assertNotIn("addModifier", runtime.accessed)

    def test_probe_reports_no_runtime_validation(self):
        _adapter, runtime, _node, _undo = editable_setup(level=4, faces=(1,))
        text = build_report(runtime)
        self.assertIn("read-only", text)
        self.assertIn("enabled (runtime not validated)", text)
        self.assertNotIn("run_undo", runtime.accessed)


class ControllerIntegrationTests(unittest.TestCase):
    def test_controller_prompt_applies_extrude_and_blocks_stale_context(self):
        adapter, runtime, node, undo = editable_setup(level=4, faces=(1,))
        controller = MenuController(adapter, default_profile())
        controller.set_parameter_prompt(
            lambda spec, context, schema: {
                "height": 2.0,
                "extrusion_type": "group",
            }
        )
        view = controller.open((0.0, 0.0), 0.0, invocation="latched")
        event = MenuEvent(
            session_id=view.session_id,
            sequence=view.sequence + 1,
            kind="click_row",
            time_ms=250.0,
            action_id="model.extrude",
        )
        closed = controller.handle(event)
        self.assertEqual(closed.last_result.status, "applied")
        self.assertEqual(len(undo.calls), 1)
        self.assertIsNotNone(closed.last_result)

        # A stale face selection after the prompt is blocked before dispatch.
        view = controller.open((0.0, 0.0), 500.0, invocation="latched")
        node.mesh.selections[ContextLevel.POLYGON] = {1, 2}

        def change_selection(*_args):
            return {
                "height": 1.0,
                "outline": -0.1,
                "extrusion_type": "group",
            }

        controller.set_parameter_prompt(change_selection)
        event = MenuEvent(
            session_id=view.session_id,
            sequence=view.sequence + 1,
            kind="click_row",
            time_ms=750.0,
            action_id="model.bevel",
        )
        blocked = controller.handle(event)
        self.assertEqual(blocked.last_result.status, "blocked")
        self.assertEqual(len(undo.calls), 1)

    def test_controller_release_hook_exits_exact_owned_mode(self):
        adapter, _runtime, node, _undo = editable_setup(level=2, edges=(1,))
        controller = MenuController(adapter, default_profile())
        entered = controller.invoke("model.cut")
        self.assertEqual(entered.last_result.status, "launched")
        self.assertEqual(node.EditablePoly.command_mode, "CutEdge")

        released = controller.release_owned_native_mode()
        self.assertEqual(released.status, "launched")
        self.assertEqual(node.EditablePoly.command_mode, "none")
        self.assertEqual(controller.release_owned_native_mode().status, "blocked")


if __name__ == "__main__":
    unittest.main(verbosity=2)

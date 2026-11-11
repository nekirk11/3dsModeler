"""Clean dissolve tests: identity guards, collinearity and one undo chunk."""

from __future__ import annotations

import unittest

from max2025_menu_prototype.core.contracts import ContextLevel
from max2025_menu_prototype.max_host.operations import clean_dissolve_edges

try:
    from .test_maneuver_host import (
        FakeBaseObject,
        FakeBitArray,
        FakeName,
        FakePolyOp,
        FakeUndoFactory,
        V2EditablePoly,
        V2Node,
        V2Runtime,
    )
except ImportError:  # discovered as a top-level test module
    from test_maneuver_host import (
        FakeBaseObject,
        FakeBitArray,
        FakeName,
        FakePolyOp,
        FakeUndoFactory,
        V2EditablePoly,
        V2Node,
        V2Runtime,
    )


class DissolveMesh:
    def __init__(self, vertices, edges, faces=()) -> None:
        self.vertices = list(vertices)
        self.edges = list(edges)
        self.faces = [list(face) for face in faces]
        self.selections = {
            ContextLevel.VERTEX: set(),
            ContextLevel.EDGE: set(),
            ContextLevel.BORDER: set(),
            ContextLevel.POLYGON: set(),
            ContextLevel.ELEMENT: set(),
        }
        self.open_edges = set()
        self.calls = []

    def snapshot(self):
        return (
            list(self.vertices),
            list(self.edges),
            [list(face) for face in self.faces],
            {level: set(values) for level, values in self.selections.items()},
        )

    def restore(self, snapshot) -> None:
        self.vertices = list(snapshot[0])
        self.edges = list(snapshot[1])
        self.faces = [list(face) for face in snapshot[2]]
        self.selections = {
            level: set(values) for level, values in snapshot[3].items()
        }

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

    def getEdgesUsingVert(self, node, vertex):
        self.calls.append(("getEdgesUsingVert", int(vertex)))
        return FakeBitArray(
            [
                index
                for index, edge in enumerate(self.edges, start=1)
                if int(vertex) in edge
            ]
        )

    def getVertsUsingEdge(self, node, edges):
        self.calls.append("getVertsUsingEdge")
        verts = set()
        for edge in edges:
            if 1 <= int(edge) <= len(self.edges):
                verts.update(self.edges[int(edge) - 1])
        return FakeBitArray(verts)

    def getVertSelection(self, node):
        return FakeBitArray(self.selections[ContextLevel.VERTEX])

    def getEdgeSelection(self, node):
        return FakeBitArray(
            self.selections[ContextLevel.EDGE]
            | self.selections[ContextLevel.BORDER]
        )

    def getFaceSelection(self, node):
        return FakeBitArray(
            self.selections[ContextLevel.POLYGON]
            | self.selections[ContextLevel.ELEMENT]
        )

    def setVertSelection(self, node, value):
        self.selections[ContextLevel.VERTEX] = (
            set() if isinstance(value, FakeName) else {int(item) for item in value}
        )

    def setEdgeSelection(self, node, value):
        self.selections[ContextLevel.EDGE] = (
            set() if isinstance(value, FakeName) else {int(item) for item in value}
        )
        self.selections[ContextLevel.BORDER] = set()

    def getOpenEdges(self, node):
        return FakeBitArray(self.open_edges)


class MeshUndoFactory:
    """One-chunk undo fake that restores the mesh snapshot on failure."""

    def __init__(self, mesh) -> None:
        self.mesh = mesh
        self.calls = []
        self.exited = []
        self.snapshot = None

    def __call__(self, enabled, label):
        self.calls.append((enabled, label))
        self.snapshot = self.mesh.snapshot()
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.exited.append((None, exc_type))
        if exc_type is not None and self.snapshot is not None:
            self.mesh.restore(self.snapshot)
        return False


class DissolvePolyOp(FakePolyOp):
    def getVertsUsingEdge(self, node, edges):
        return node.mesh.getVertsUsingEdge(node, edges)

    def getEdgesUsingVert(self, node, vertex):
        return node.mesh.getEdgesUsingVert(node, vertex)


class DissolveEditablePoly(V2EditablePoly):
    def __init__(self, mesh) -> None:
        super().__init__(mesh)
        self.remove_result = True
        self.edge_removes = 0
        self.vertex_removes = 0
        self.renumber_on_edge_remove = False

    def Remove(self, selLevel=None, flag=1):
        level = str(selLevel).lower()
        self.calls.append(("Remove", level, flag))
        if not self.remove_result:
            return False
        if "edge" in level:
            self.edge_removes += 1
            selected = set(self.mesh.selections[ContextLevel.EDGE])
            self.mesh.edges = [
                edge
                for index, edge in enumerate(self.mesh.edges, start=1)
                if index not in selected
            ]
            self.mesh.selections[ContextLevel.EDGE] = set()
            if self.renumber_on_edge_remove:
                # A host bug that compacts slot 1 must be detected, not followed.
                self.mesh.vertices.pop(0)
                self.mesh.edges = [
                    tuple(max(1, vertex - 1) for vertex in edge)
                    for edge in self.mesh.edges
                ]
            return True
        if "vertex" in level:
            self.vertex_removes += 1
            selected = set(self.mesh.selections[ContextLevel.VERTEX])
            keep = [
                vertex
                for index, vertex in enumerate(self.mesh.vertices, start=1)
                if index not in selected
            ]
            mapping = {}
            next_index = 1
            for index in range(1, len(self.mesh.vertices) + 1):
                if index not in selected:
                    mapping[index] = next_index
                    next_index += 1
            self.mesh.vertices = keep
            self.mesh.edges = [
                (mapping[first], mapping[second])
                for first, second in self.mesh.edges
                if first in mapping and second in mapping
            ]
            self.mesh.faces = [
                [mapping[vertex] for vertex in face if vertex in mapping]
                for face in self.mesh.faces
            ]
            self.mesh.faces = [face for face in self.mesh.faces if len(face) >= 3]
            self.mesh.selections[ContextLevel.VERTEX] = set()
            return True
        return True


class DissolveRuntime(V2Runtime):
    def __init__(self) -> None:
        super().__init__()
        self.polyop = DissolvePolyOp()


def dissolve_setup(
    vertices,
    edges,
    selected,
    faces=(),
    *,
    remove_result=True,
    renumber=False,
):
    runtime = DissolveRuntime()
    node = V2Node("Dissolve", class_name="Editable_Poly", handle=1201)
    node.baseObject = FakeBaseObject("Editable_Poly", handle=1501)
    node.mesh = DissolveMesh(vertices, edges, faces)
    node.EditablePoly = DissolveEditablePoly(node.mesh)
    node.EditablePoly.remove_result = remove_result
    node.EditablePoly.renumber_on_edge_remove = renumber
    node.mesh.selections[ContextLevel.EDGE].update(selected)
    runtime.selection = [node]
    runtime.subObjectLevel = 2
    runtime.modPanel.current = node.baseObject
    undo = MeshUndoFactory(node.mesh)
    return runtime, node, undo


def run_dissolve(runtime, node, undo, selected):
    return clean_dissolve_edges(
        runtime,
        node,
        node.EditablePoly,
        ContextLevel.EDGE,
        selected,
        undo_factory=undo,
        hold_guard=lambda: None,
    )


LINE = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0), (1.0, 1.0, 0.0)]


class CleanDissolveTests(unittest.TestCase):
    def test_straight_redundant_endpoint_is_removed_in_one_chunk(self):
        runtime, node, undo = dissolve_setup(
            LINE, [(1, 2), (2, 3), (2, 4)], (3,)
        )
        outcome = run_dissolve(runtime, node, undo, (3,))
        self.assertEqual(outcome.status, "applied", outcome.message)
        self.assertEqual(node.EditablePoly.edge_removes, 1)
        self.assertEqual(node.EditablePoly.vertex_removes, 1)
        self.assertEqual(len(undo.calls), 1)
        self.assertIn("inline vertex", outcome.message)
        self.assertEqual(len(node.mesh.vertices), 3)
        self.assertEqual(runtime.subObjectLevel, 2)
        self.assertEqual(node.mesh.selections[ContextLevel.EDGE], set())
        self.assertEqual(node.mesh.selections[ContextLevel.VERTEX], set())

    def test_bent_endpoint_is_preserved(self):
        bent = list(LINE)
        bent[1] = (1.0, 0.5, 0.0)
        runtime, node, undo = dissolve_setup(
            bent, [(1, 2), (2, 3), (2, 4)], (3,)
        )
        outcome = run_dissolve(runtime, node, undo, (3,))
        self.assertEqual(outcome.status, "applied", outcome.message)
        self.assertEqual(node.EditablePoly.vertex_removes, 0)
        self.assertEqual(len(node.mesh.vertices), 4)

    def test_valence_three_endpoint_is_preserved(self):
        runtime, node, undo = dissolve_setup(
            LINE + [(1.0, -1.0, 0.0)],
            [(1, 2), (2, 3), (2, 4), (2, 5)],
            (3,),
        )
        outcome = run_dissolve(runtime, node, undo, (3,))
        self.assertEqual(outcome.status, "applied", outcome.message)
        self.assertEqual(node.EditablePoly.vertex_removes, 0)

    def test_unrelated_valence_two_vertex_is_untouched(self):
        vertices = LINE + [(3.0, 0.0, 0.0), (4.0, 0.0, 0.0)]
        runtime, node, undo = dissolve_setup(
            vertices,
            [(1, 2), (2, 3), (2, 4), (3, 5), (5, 6), (2, 6)],
            (4,),
        )
        outcome = run_dissolve(runtime, node, undo, (4,))
        self.assertEqual(outcome.status, "applied", outcome.message)
        self.assertEqual(node.EditablePoly.vertex_removes, 0)
        self.assertEqual(len(node.mesh.vertices), 6)

    def test_multi_edge_candidates_are_deduplicated(self):
        vertices = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0), (1.0, 1.0, 0.0)]
        runtime, node, undo = dissolve_setup(
            vertices, [(1, 2), (2, 3), (2, 4), (4, 3)], (3, 4)
        )
        outcome = run_dissolve(runtime, node, undo, (3, 4))
        self.assertEqual(outcome.status, "applied", outcome.message)
        self.assertEqual(node.EditablePoly.vertex_removes, 1)
        self.assertEqual(len(undo.calls), 1)

    def test_coincident_candidate_slots_roll_back(self):
        coincident = list(LINE)
        coincident[3] = coincident[1]
        runtime, node, undo = dissolve_setup(
            coincident, [(1, 2), (2, 3), (2, 4)], (3,)
        )
        outcome = run_dissolve(runtime, node, undo, (3,))
        self.assertEqual(outcome.status, "error")
        self.assertIn("Coincident vertex slots", outcome.message)
        self.assertEqual(node.EditablePoly.edge_removes, 0)
        self.assertEqual(len(node.mesh.edges), 3)
        self.assertEqual(len(undo.calls), 1)
        self.assertEqual(undo.exited[0][1].__name__, "OperationError")

    def test_renumbering_is_detected_and_rolled_back(self):
        runtime, node, undo = dissolve_setup(
            LINE, [(1, 2), (2, 3), (2, 4)], (3,), renumber=True
        )
        outcome = run_dissolve(runtime, node, undo, (3,))
        self.assertEqual(outcome.status, "error")
        self.assertIn("renumbered", outcome.message)
        self.assertEqual(len(node.mesh.vertices), 4)
        self.assertEqual(len(node.mesh.edges), 3)

    def test_native_false_return_rolls_back(self):
        runtime, node, undo = dissolve_setup(
            LINE, [(1, 2), (2, 3), (2, 4)], (3,), remove_result=False
        )
        outcome = run_dissolve(runtime, node, undo, (3,))
        self.assertEqual(outcome.status, "error")
        self.assertEqual(node.EditablePoly.edge_removes, 0)
        self.assertIn(("Remove", "edge", 1), node.EditablePoly.calls)
        self.assertEqual(len(node.mesh.edges), 3)
        self.assertEqual(undo.exited[0][1].__name__, "OperationError")

    def test_non_edge_context_is_rejected_without_an_undo_chunk(self):
        runtime, node, undo = dissolve_setup(
            LINE, [(1, 2), (2, 3), (2, 4)], (3,)
        )
        outcome = clean_dissolve_edges(
            runtime,
            node,
            node.EditablePoly,
            ContextLevel.VERTEX,
            (1,),
            undo_factory=undo,
            hold_guard=lambda: None,
        )
        self.assertEqual(outcome.status, "error")
        self.assertEqual(undo.calls, [])


if __name__ == "__main__":
    unittest.main()

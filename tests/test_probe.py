"""Read-only diagnostic/probe tests.

These use fake pymxs values only.  They prove privacy, owner separation,
cached-versus-fresh labeling, busy/thread/unavailable behavior, and that a
report never mutates geometry, selection, settings, modes, undo, or controller
state.  They are not a 3ds Max 2025 runtime test.
"""

from __future__ import annotations

import builtins
import socket
import subprocess
import threading
import unittest
from unittest import mock

from max2025_menu_prototype.core.catalog import ACTIONS
from max2025_menu_prototype.core.contracts import (
    Availability,
    ContextLevel,
    ControllerSummary,
    LaunchResult,
)
from max2025_menu_prototype.max_host import bootstrap, probe


# ---------------------------------------------------------------------------
# Minimal fake pymxs surface
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


class FakeBitArray:
    def __init__(self, values=(), capacity: int = 50) -> None:
        self._values = tuple(sorted(set(int(value) for value in values)))
        self._capacity = max(capacity, self._values[-1] if self._values else 0)

    @property
    def isEmpty(self) -> bool:
        return not self._values

    @property
    def numberSet(self) -> int:
        return len(self._values)

    def __iter__(self):
        return iter(self._values)


class FakeBase:
    def __init__(self, class_name: str = "Editable_Poly", handle: int = 501) -> None:
        self.class_name = class_name
        self.handle = handle
        # Documented Editable Poly object properties belong here, not on the
        # EditablePoly method interface.
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
        self.calls = []

    def snapshot(self):
        return (
            list(self.vertices),
            list(self.edges),
            [list(face) for face in self.faces],
            {level: set(values) for level, values in self.selections.items()},
        )

    def getNumVerts(self, node):
        return len(self.vertices)

    def getNumEdges(self, node):
        return len(self.edges)

    def getNumFaces(self, node):
        return len(self.faces)

    def getVert(self, node, index):
        return self.vertices[index - 1]

    def getEdgeVerts(self, node, index):
        return self.edges[index - 1]

    def getFaceVerts(self, node, index):
        return self.faces[index - 1]

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

    def getOpenEdges(self, node):
        return FakeBitArray(())

    def getBorderFromEdge(self, node, edge):
        return FakeBitArray(())

    def getVertsUsingEdge(self, node, edges):
        return FakeBitArray(())


class FakePolyOp:
    _REQUIRED_CALLABLES = frozenset(
        {
            "weldVertsByThreshold",
            "collapseVerts",
            "collapseEdges",
            "collapseFaces",
            "deleteVerts",
            "deleteFaces",
            "deleteEdges",
            "deleteIsoVerts",
            "setVertSelection",
            "setEdgeSelection",
            "setFaceSelection",
            "extrudeFaces",
            "bevelFaces",
            "capHolesByEdge",
        }
    )

    def __getattr__(self, name):
        mesh_methods = {
            "getNumVerts",
            "getNumEdges",
            "getNumFaces",
            "getVert",
            "getEdgeVerts",
            "getFaceVerts",
            "getVertSelection",
            "getEdgeSelection",
            "getFaceSelection",
            "getOpenEdges",
            "getBorderFromEdge",
            "getVertsUsingEdge",
        }
        if name in mesh_methods:
            def dispatch(node, *args, **kwargs):
                return getattr(node.mesh, name)(node, *args, **kwargs)
            return dispatch
        if name in self._REQUIRED_CALLABLES:
            # Presence only.  A diagnostic must never call a mutation method.
            def placeholder(*args, **kwargs):
                raise AssertionError("diagnostic invoked polyop.%s" % name)
            return placeholder
        raise AttributeError(name)


class FakeEditablePoly:
    def __init__(self, mesh: FakeMesh) -> None:
        self.mesh = mesh
        self.calls = []
        self.command_mode = "none"

    def buttonOp(self, op):
        self.calls.append(("buttonOp", str(op)))

    def chamferVertices(self, *args, **kwargs):
        self.calls.append("chamferVertices")

    def chamferEdges(self, *args, **kwargs):
        self.calls.append("chamferEdges")

    def ConnectVertices(self, *args, **kwargs):
        self.calls.append("ConnectVertices")

    def ConnectEdges(self, *args, **kwargs):
        self.calls.append("ConnectEdges")

    def ReadyToBridgeFlagged(self, *args, **kwargs):
        self.calls.append("ReadyToBridgeFlagged")
        return True

    def Remove(self, *args, **kwargs):
        self.calls.append("Remove")

    def splitEdges(self, *args, **kwargs):
        self.calls.append("splitEdges")

    def breakVerts(self, *args, **kwargs):
        self.calls.append("breakVerts")

    def MakePlanarIn(self, *args, **kwargs):
        self.calls.append("MakePlanarIn")

    def flipNormals(self, *args, **kwargs):
        self.calls.append("flipNormals")

    def SelectBorder(self):
        self.calls.append("SelectBorder")

    def SelectElement(self):
        self.calls.append("SelectElement")

    def SelectEdgeLoop(self):
        self.calls.append("SelectEdgeLoop")

    def SelectEdgeRing(self):
        self.calls.append("SelectEdgeRing")

    def getCommandMode(self):
        self.calls.append("getCommandMode")
        return self.command_mode

    def toggleCommandMode(self, value):
        self.calls.append(("toggleCommandMode", str(value)))
        self.command_mode = str(value)

    def exitCommandModes(self):
        self.calls.append("exitCommandModes")


class FakeNode:
    def __init__(
        self,
        name: str = "Poly001",
        class_name: str = "Editable_Poly",
        handle: int = 101,
    ) -> None:
        self.name = name
        self.class_name = class_name
        self.handle = handle
        self.baseObject = FakeBase(class_name)
        self.modifiers = []
        self.mesh = FakeMesh()
        self.EditablePoly = FakeEditablePoly(self.mesh)
        self.transform = "matrix3"


class FakeModPanel:
    def __init__(self) -> None:
        self.current = None

    def getCurrentObject(self):
        return self.current

    def setCurrentObject(self, *args, **kwargs):
        raise AssertionError("probe must not write the active stack item")


class FakeRefs:
    def __init__(self, runtime: "FakeRuntime") -> None:
        self.runtime = runtime

    def dependentNodes(self, base, firstOnly=False, baseObjectOnly=True):
        for node in self.runtime.selection:
            if node.baseObject is base:
                return [node]
        return []


class FakeToolMode:
    def __init__(self) -> None:
        self.commandMode = "select"


class FakeHold:
    def __init__(self) -> None:
        self.holding = False
        self.redoing = False

    def Holding(self):
        return self.holding

    def RestoreOrRedoing(self):
        return self.redoing


_FORBIDDEN = frozenset(
    {
        "addModifier",
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
        self.theHold = FakeHold()
        self.command_panel_task_mode = "modify"
        self.currentTime = 0
        self.executed = []
        self.accessed = []
        self.Editable_Poly = FakeClass("Editable_Poly")
        self.Edit_Poly = FakeClass("Edit_Poly")

    def classOf(self, value):
        return FakeClass(getattr(value, "class_name", "unknown"))

    def isKindOf(self, value, expected_class):
        return self.classOf(value) == expected_class

    def getHandleByAnim(self, value):
        return getattr(value, "handle", None)

    def getCommandPanelTaskMode(self):
        self.accessed.append("getCommandPanelTaskMode")
        return self.command_panel_task_mode

    def execute(self, literal):
        self.executed.append(literal)
        return None

    def maxVersion(self):
        return "2025.0 fake"

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        self.accessed.append(name)
        if name in _FORBIDDEN:
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
    level: int = 4,
    faces=(1,),
    panel_open: bool = True,
):
    runtime = FakeRuntime()
    node = FakeNode()
    node.mesh.selections[ContextLevel.POLYGON].update(faces)
    node.mesh.selections[ContextLevel.POLYGON].update()
    runtime.selection = [node]
    runtime.subObjectLevel = level
    runtime.modPanel.current = node.baseObject if panel_open else None
    return runtime, node


def _summary(**overrides) -> ControllerSummary:
    values = {
        "phase": "closed",
        "context": ContextLevel.POLYGON,
        "target_kind": "editable_poly",
        "selection_count": 1,
        "panel_ready": True,
        "conflicting_mode": False,
        "busy": False,
        "status": "Ready.",
        "last_result": None,
        "last_repeat_id": None,
        "availability": (),
    }
    values.update(overrides)
    return ControllerSummary(**values)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class ProbeOwnerAndReadOnlyTests(unittest.TestCase):
    def test_properties_are_checked_on_base_object_not_interface(self) -> None:
        runtime, node = editable_setup()
        text = probe.build_report(runtime)

        self.assertIn(
            "extrusionType: present (required by model.bevel, model.extrude)",
            text,
        )
        self.assertIn("chamferVertices: callable", text)
        self.assertNotIn("extrusionType: absent", text)

        # If the property exists only on the EditablePoly interface, the probe
        # must not conflate owners and claim the base object has it.
        del node.baseObject.extrusionType
        node.EditablePoly.extrusionType = 0
        text = probe.build_report(runtime)
        self.assertIn("extrusionType: absent", text)
        self.assertIn("P0 member health: missing/unreadable required members", text)

    def test_presence_probe_never_invokes_edit_methods_and_is_read_only(self) -> None:
        runtime, node = editable_setup()
        geometry_before = node.mesh.snapshot()
        properties_before = dict(vars(node.baseObject))
        modifiers_before = list(node.modifiers)
        level_before = runtime.subObjectLevel
        mode_before = node.EditablePoly.command_mode
        calls_before = list(node.EditablePoly.calls)

        probe.build_report(runtime)

        self.assertEqual(node.mesh.snapshot(), geometry_before)
        self.assertEqual(vars(node.baseObject), properties_before)
        self.assertEqual(node.modifiers, modifiers_before)
        self.assertEqual(runtime.subObjectLevel, level_before)
        self.assertEqual(node.EditablePoly.command_mode, mode_before)
        self.assertEqual(node.EditablePoly.calls, calls_before + ["getCommandMode"])
        self.assertEqual(runtime.selection, [node])
        self.assertEqual(runtime.executed, [])

    def test_missing_owner_and_throwing_getter_are_bounded(self) -> None:
        runtime, node = editable_setup()
        runtime.selection = []
        text = probe.build_report(runtime)
        self.assertIn("owner unavailable", text)
        self.assertIn("P0 member health: not evaluated", text)

        runtime, node = editable_setup()
        class BrokenBase(FakeBase):
            @property
            def preserveUVs(self):
                raise RuntimeError("SECRET_EXCEPTION C:/private/path")

            @preserveUVs.setter
            def preserveUVs(self, value):
                pass

        node.baseObject = BrokenBase()
        runtime.modPanel.current = node.baseObject
        text = probe.build_report(runtime)
        self.assertIn("preserveUVs: unreadable", text)
        self.assertNotIn("SECRET_EXCEPTION", text)
        self.assertNotIn("C:/private/path", text)

    def test_missing_informational_property_does_not_imply_p0_broken(self) -> None:
        runtime, node = editable_setup()
        del node.baseObject.preserveUVs
        text = probe.build_report(runtime)
        self.assertIn("preserveUVs: absent", text)
        self.assertIn(
            "P0 member health: required members present", text
        )

    def test_report_prints_and_returns_build_text(self) -> None:
        runtime, _node = editable_setup()
        with mock.patch("builtins.print") as printer:
            text = probe.report(runtime)
        self.assertEqual(printer.call_args.args, (text,))
        self.assertIn("read-only", text)
        self.assertIn("Native operation validation: NOT RUN", text)

    def test_full_catalog_and_not_run_metadata(self) -> None:
        runtime, _node = editable_setup()
        text = probe.build_report(runtime)
        for action in ACTIONS:
            self.assertIn("%s:" % action.id, text)
        self.assertIn("enabled (runtime not validated)", text)
        self.assertIn("Native operation validation: NOT RUN", text)
        self.assertIn("runtime_status=not_run", text)
        self.assertIn("menu.repeat:", text)
        self.assertIn("view.frame_selection:", text)


class ProbeStateCategoriesTests(unittest.TestCase):
    def test_no_target_multiple_wrong_panel_empty_selection(self) -> None:
        runtime, _node = editable_setup()
        runtime.selection = []
        text = probe.build_report(runtime)
        self.assertIn("target kind: none", text)
        self.assertIn("[target] Select one disposable", text)

        runtime, node = editable_setup()
        second = FakeNode("Second", handle=202)
        runtime.selection = [node, second]
        text = probe.build_report(runtime)
        self.assertIn("target kind: multiple", text)
        self.assertIn("[target] Select exactly one", text)

        runtime, _node = editable_setup(panel_open=False)
        text = probe.build_report(runtime)
        self.assertIn("panel ready: False", text)
        self.assertIn("Open the Modify panel/base object", text)

        runtime, _node = editable_setup(faces=())
        text = probe.build_report(runtime)
        self.assertIn("selection count at level: 0", text)
        self.assertIn("[context/selection] Select the required components", text)

    def test_active_mode_hold_and_unreadable_time(self) -> None:
        runtime, node = editable_setup()
        node.EditablePoly.command_mode = "ChamferEdge"
        text = probe.build_report(runtime)
        self.assertIn("EditablePoly command mode: chamferedge", text)
        self.assertIn("[command mode] Return from the foreign native mode", text)

        runtime, _node = editable_setup()
        runtime.theHold.holding = True
        text = probe.build_report(runtime)
        self.assertIn("theHold.Holding(): true", text)
        self.assertIn("[hold] Close any pending hold/undo/redo", text)

        runtime, _node = editable_setup()
        runtime.currentTime = None
        text = probe.build_report(runtime)
        self.assertIn("current time: unreadable", text)
        self.assertIn("[unreadable state] Current time or host state", text)

    def test_unknown_host_mode_is_reported_as_unknown(self) -> None:
        runtime, _node = editable_setup()
        runtime.toolMode.commandMode = "SecretModeName"
        text = probe.build_report(runtime)
        self.assertIn("tool command mode: unknown", text)
        self.assertNotIn("SecretModeName", text)

    def test_absent_runtime_is_a_helpful_category_not_an_exception(self) -> None:
        with mock.patch.object(
            probe,
            "_import_pymxs_runtime",
            side_effect=ImportError("SECRET_IMPORT_ERROR"),
        ):
            text = probe.build_report(None)
        self.assertIn("host runtime unavailable", text)
        self.assertIn("skipped: host runtime unavailable", text)
        self.assertIn("Native operation validation: NOT RUN", text)
        self.assertNotIn("SECRET_IMPORT_ERROR", text)


class ProbeCachedFreshTests(unittest.TestCase):
    def test_cached_lockout_survives_fresh_eligibility(self) -> None:
        runtime, _node = editable_setup()
        summary = _summary(
            last_result=LaunchResult(
                "blocked",
                "model.extrude",
                "Native topology is latched disabled after rollback failure.",
            ),
            availability=(
                (
                    "model.extrude",
                    Availability(
                        False,
                        "An edit failed and rollback could not be verified; "
                        "native topology is latched disabled.",
                    ),
                ),
            ),
        )
        text = probe.build_report(runtime, summary=summary)

        self.assertIn("cached rollback lockout: present", text)
        self.assertIn("does not clear or override this cached lockout", text)
        self.assertIn("model.extrude: disabled - rollback lockout (cached)", text)
        self.assertIn("model.extrude: enabled (runtime not validated)", text)
        self.assertIn("no knowledge of the live adapter's historical latch", text)
        self.assertIn("does not certify live latch state", text)
        self.assertNotIn("latch is clear", text)

    def test_busy_summary_skips_all_fresh_host_access(self) -> None:
        runtime, _node = editable_setup()
        summary = _summary(busy=True, last_result=LaunchResult("applied", "model.extrude"))
        with mock.patch.object(
            probe, "_import_pymxs_runtime", side_effect=AssertionError("host import")
        ), mock.patch.object(
            probe, "MaxHostAdapter", side_effect=AssertionError("adapter created")
        ):
            text = probe.build_report(runtime, summary=summary)
        self.assertIn("busy: True", text)
        self.assertIn("skipped: a prototype invocation/dialog is busy", text)
        self.assertIn("fresh probing did not access the host", text)
        self.assertIn("Native operation validation: NOT RUN", text)
        self.assertEqual(runtime.accessed, [])

    def test_default_redacts_names_paths_modes_errors_results_and_reasons(self) -> None:
        runtime, node = editable_setup()
        node.name = "C:/Users/SecretUser/private_node"
        node.baseObject.preserveUVs = True
        node.EditablePoly.command_mode = "SecretModeName"
        runtime.toolMode.commandMode = "SecretModeName"
        summary = _summary(
            last_result=LaunchResult(
                "error",
                "not-a-known-action",
                "SECRET_RESULT_TEXT C:/Users/SecretUser/private.log",
            ),
            availability=(
                (
                    "model.extrude",
                    Availability(
                        False,
                        "SECRET_AVAILABILITY_REASON C:/Users/SecretUser/private",
                    ),
                ),
            ),
        )
        text = probe.build_report(runtime, summary=summary)

        for secret in (
            "SecretUser",
            "private_node",
            "private.log",
            "SECRET_RESULT_TEXT",
            "SECRET_AVAILABILITY_REASON",
            "SecretModeName",
            "not-a-known-action",
        ):
            self.assertNotIn(secret, text)
        self.assertIn("last result: action=unknown", text)
        self.assertIn("tool command mode: unknown", text)
        self.assertIn("details withheld", text)
        self.assertIn("Default privacy mode", text)

    def test_include_details_warns_bounds_and_strips_control_characters(self) -> None:
        runtime, _node = editable_setup()
        secret = "\x1b[31mSECRET_DETAIL\x07" + "A" * 900
        summary = _summary(
            last_result=LaunchResult("error", "model.extrude", secret)
        )
        text = probe.build_report(runtime, summary=summary, include_details=True)

        self.assertIn("LOCAL PRIVATE DETAILS", text)
        self.assertIn("review before sharing", text)
        self.assertIn("SECRET_DETAIL", text)
        self.assertNotIn("\x1b", text)
        self.assertNotIn("\x07", text)
        self.assertLess(len(text), 40_000)
        message_lines = [
            line for line in text.splitlines() if "last result message:" in line
        ]
        self.assertEqual(len(message_lines), 1)
        self.assertLessEqual(len(message_lines[0]), 530)

    def test_no_filesystem_network_or_subprocess_writes(self) -> None:
        runtime, _node = editable_setup()
        with mock.patch.object(
            builtins, "open", side_effect=AssertionError("file opened")
        ), mock.patch.object(
            socket, "socket", side_effect=AssertionError("socket opened")
        ), mock.patch.object(
            subprocess, "run", side_effect=AssertionError("subprocess run")
        ):
            text = probe.build_report(runtime, include_details=True)
        self.assertIn("read-only", text)


class FakeV2Host:
    def __init__(self, **state) -> None:
        self.state = {
            "atlas_schema": 2,
            "atlas_entries": 36,
            "overlay_warning": "none",
            "context": "edge",
            "prep_tier": "base_with_modifiers",
            "blocked_category": "",
            "handler_coverage": "complete",
            "idle_mode": "none",
            "caddy_active": False,
            "ambiguous_mode": False,
            "rollback_lockout": False,
            "loaded_sha": "1" * 40,
        }
        self.state.update(state)
        self.observes = []

    def diagnostic_state(self, *, observe=True):
        self.observes.append(observe)
        return dict(self.state)


class ProbeV2Tests(unittest.TestCase):
    def test_v2_section_reports_redacted_state_and_not_run_status(self) -> None:
        host = FakeV2Host(overlay_warning="invalid overlay ignored", rollback_lockout=True)
        text = probe.build_report(v2=host, loaded_sha="a" * 40)
        self.assertIn("V2 maneuver host", text)
        self.assertIn("atlas schema: 2", text)
        self.assertIn("atlas entries: 36", text)
        self.assertIn("overlay warning: invalid overlay ignored", text)
        self.assertIn("active context: edge", text)
        self.assertIn("preparation tier: base_with_modifiers", text)
        self.assertIn("fixed handler coverage: complete", text)
        self.assertIn("rollback lockout: present", text)
        self.assertIn("loaded SHA: " + "a" * 40, text)
        self.assertIn("native operation validation: NOT RUN", text)
        self.assertNotIn("Poly001", text)

    def test_busy_report_uses_cached_state_without_observing(self) -> None:
        host = FakeV2Host()
        summary = _summary(busy=True)
        text = probe.build_report(summary=summary, v2=host)
        self.assertEqual(host.observes, [False])
        self.assertIn("cached state used", text)

    def test_overlay_path_and_bad_sha_never_leak(self) -> None:
        private_prefix = "/" + "home" + "/"
        host = FakeV2Host(
            overlay_warning=private_prefix + "private/user_aliases.json"
        )
        text = probe.build_report(
            v2=host, loaded_sha=private_prefix + "private/checkout"
        )
        self.assertNotIn("user_aliases.json", text)
        self.assertNotIn(private_prefix, text)
        self.assertNotIn("private", text)
        self.assertIn("overlay warning (details withheld)", text)
        self.assertIn("loaded SHA: unknown", text)

    def test_legacy_report_without_v2_stays_readable(self) -> None:
        text = probe.build_report()
        self.assertIn("V2 host: not attached", text)
        self.assertIn("Native operation validation: NOT RUN", text)


class ProbeThreadTests(unittest.TestCase):
    def test_worker_thread_fails_before_host_or_summary_access(self) -> None:
        runtime, _node = editable_setup()
        accessed = []

        class Summary:
            @property
            def busy(self):
                accessed.append("summary")
                raise AssertionError("summary accessed")

        result = {}

        def target() -> None:
            try:
                probe.build_report(runtime, summary=Summary())
            except Exception as exc:  # noqa: BLE001 - test captures the type
                result["error"] = exc

        thread = threading.Thread(target=target)
        thread.start()
        thread.join(timeout=10)

        self.assertIsInstance(result.get("error"), RuntimeError)
        self.assertIn("main thread", str(result["error"]))
        self.assertEqual(accessed, [])
        self.assertEqual(runtime.accessed, [])


if __name__ == "__main__":
    unittest.main()

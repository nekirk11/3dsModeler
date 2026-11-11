"""Three-tier V2 target inspection and preparation tests (fake pymxs)."""

from __future__ import annotations

import unittest

from max2025_menu_prototype.core.contracts import ContextLevel
from max2025_menu_prototype.max_host.preparation import (
    EDIT_POLY_REASON,
    INSTANCE_REASON,
    V2_LEVEL_INDEX,
    collapse_to_poly,
    context_value,
    from_legacy_context,
    inspect_target,
    pivot_open_edges_to_border,
    prepare_target,
    switch_prepared_context,
    to_legacy_context,
)

try:
    from .test_maneuver_host import (
        BooleanMaskBitArray,
        FakeBaseObject,
        FakeModifier,
        FakeUndoFactory,
        V2Runtime,
        v2_setup,
    )
except ImportError:  # discovered as a top-level test module
    from test_maneuver_host import (
        BooleanMaskBitArray,
        FakeBaseObject,
        FakeModifier,
        FakeUndoFactory,
        V2Runtime,
        v2_setup,
    )


def assessment(runtime=None, **kwargs):
    adapter, runtime, node, undo = v2_setup(runtime=runtime, **kwargs)
    return inspect_target(runtime), runtime, node, undo


class OwnedHoldUndoFactory(FakeUndoFactory):
    """Fake ``pymxs.undo`` that sets ``theHold.Holding()`` inside the block."""

    def __init__(self, runtime, restore=None, suppress: bool = False) -> None:
        super().__init__(restore=restore, suppress=suppress)
        self.runtime = runtime
        self._previous = False

    def __enter__(self):
        self._previous = bool(self.runtime.theHold.holding)
        self.runtime.theHold.holding = True
        return super().__enter__()

    def __exit__(self, exc_type, exc, traceback):
        self.runtime.theHold.holding = self._previous
        return super().__exit__(exc_type, exc, traceback)


class PanelDriftRuntime(V2Runtime):
    """Mutates the scene from the Modify-panel switch callback."""

    panel_callback = None

    def setCommandPanelTaskMode(self, mode):
        super().setCommandPanelTaskMode(mode)
        callback = self.panel_callback
        if callback is not None:
            callback(self)


class InspectionTests(unittest.TestCase):
    def test_ready_poly_is_read_only_and_never_touches_the_panel(self):
        assessment_value, runtime, node, undo = assessment(level=0)
        self.assertEqual(assessment_value.tier, "ready")
        self.assertEqual(assessment_value.reason, "")
        self.assertEqual(runtime.modPanel.writes, [])
        self.assertEqual(runtime.v2_calls, [])
        self.assertEqual(node.EditablePoly.calls, [])
        self.assertEqual(runtime.subObjectLevel, 0)

    def test_primitive_without_modifiers_requires_tier_one(self):
        assessment_value, runtime, node, undo = assessment(
            level=2, base_class="Box"
        )
        self.assertEqual(assessment_value.tier, "convert")
        self.assertTrue(assessment_value.can_convert)
        self.assertNotIn("convertTo", runtime.v2_calls)
        self.assertEqual(runtime.modPanel.writes, [])

    def test_poly_with_modifiers_is_tier_two_not_a_conversion(self):
        modifier = FakeModifier("TurboSmooth", handle=910)
        assessment_value, runtime, node, undo = assessment(
            level=2, modifiers=(modifier,), show_end_result=True
        )
        self.assertEqual(assessment_value.tier, "base_with_modifiers")
        self.assertEqual(assessment_value.modifier_classes, ("TurboSmooth",))
        self.assertTrue(assessment_value.show_end_result)
        self.assertEqual(runtime.v2_calls, [])

    def test_edit_poly_anywhere_is_excluded_with_q10_guidance(self):
        edit_poly = FakeModifier("Edit_Poly", handle=911)
        assessment_value, runtime, node, undo = assessment(
            level=2, modifiers=(edit_poly,)
        )
        self.assertEqual(assessment_value.tier, "blocked")
        self.assertTrue(assessment_value.has_edit_poly)
        self.assertEqual(assessment_value.reason, EDIT_POLY_REASON)
        self.assertIn("Collapse to Poly", assessment_value.reason)
        self.assertEqual(runtime.modPanel.writes, [])
        self.assertEqual(runtime.v2_calls, [])

    def test_instanced_or_shared_base_is_blocked_before_writes(self):
        assessment_value, runtime, node, undo = assessment(level=2)
        runtime.dependent_overrides[id(node.baseObject)] = []
        assessment_value = inspect_target(runtime)
        self.assertEqual(assessment_value.tier, "blocked")
        self.assertTrue(assessment_value.instanced)
        self.assertEqual(assessment_value.reason, INSTANCE_REASON)
        self.assertIn("Make Unique", assessment_value.reason)
        self.assertEqual(runtime.modPanel.writes, [])

    def test_non_poly_base_with_modifiers_names_explicit_collapse(self):
        modifier = FakeModifier("TurboSmooth", handle=912)
        assessment_value, runtime, node, undo = assessment(
            level=2, base_class="Box", modifiers=(modifier,)
        )
        self.assertEqual(assessment_value.tier, "blocked")
        self.assertIn("Collapse to Poly", assessment_value.reason)
        self.assertIn("stack", assessment_value.reason)

    def test_unreadable_modifier_stack_fails_closed(self):
        assessment_value, runtime, node, undo = assessment(level=2)
        node.modifiers = object()
        assessment_value = inspect_target(runtime)
        self.assertEqual(assessment_value.tier, "blocked")
        self.assertIn("modifier stack could not be read", assessment_value.reason)

    def test_unknown_hold_state_fails_closed(self):
        assessment_value, runtime, node, undo = assessment(level=2)
        runtime.theHold = None
        assessment_value = inspect_target(runtime)
        self.assertEqual(assessment_value.tier, "blocked")
        self.assertIn("Undo/redo state could not be verified", assessment_value.reason)

    def test_active_hold_fails_closed(self):
        assessment_value, runtime, node, undo = assessment(level=2)
        runtime.theHold.holding = True
        assessment_value = inspect_target(runtime)
        self.assertEqual(assessment_value.tier, "blocked")
        self.assertIn("theHold", assessment_value.reason)

    def test_conversion_capability_false_blocks_tier_one(self):
        assessment_value, runtime, node, undo = assessment(
            level=2, base_class="Box"
        )
        node.convertible = False
        assessment_value = inspect_target(runtime)
        self.assertEqual(assessment_value.tier, "blocked")
        self.assertIn("cannot be converted", assessment_value.reason)

    def test_context_mapping_only_translates_face_and_polygon(self):
        self.assertEqual(context_value("face"), "face")
        self.assertEqual(to_legacy_context("face"), ContextLevel.POLYGON)
        self.assertEqual(from_legacy_context(ContextLevel.POLYGON), "face")
        with self.assertRaises(ValueError):
            context_value("polygon")
        self.assertEqual(V2_LEVEL_INDEX["face"], 4)


class PreparationTests(unittest.TestCase):
    def test_ready_preparation_opens_modify_and_base_without_conversion(self):
        runtime = V2Runtime()
        adapter, runtime, node, undo = v2_setup(level=2, edges=(1,), runtime=runtime)
        assessment_value = inspect_target(runtime)
        prepared = prepare_target(runtime, assessment_value, undo_factory=undo)
        self.assertTrue(prepared.ok, prepared.reason)
        self.assertFalse(prepared.converted)
        self.assertEqual(runtime.v2_calls.count("convertTo"), 0)
        self.assertEqual(undo.calls, [])
        self.assertTrue(node.baseObject is runtime.modPanel.current)

    def test_primitive_conversion_is_one_undo_chunk_and_verified(self):
        runtime = V2Runtime()
        undo = OwnedHoldUndoFactory(runtime)
        adapter, runtime, node, undo = v2_setup(
            level=2, edges=(1,), base_class="Box", runtime=runtime, undo=undo
        )
        assessment_value = inspect_target(runtime)
        prepared = prepare_target(runtime, assessment_value, undo_factory=undo)
        self.assertTrue(prepared.ok, prepared.reason)
        self.assertTrue(prepared.converted)
        self.assertEqual(len(undo.calls), 1)
        self.assertEqual(undo.calls[0][1], "3dsModeler V2: convert to Editable Poly")
        self.assertEqual(runtime.v2_calls.count("convertTo"), 1)
        self.assertIsNotNone(prepared.ep)
        self.assertEqual(prepared.tier, "convert")
        self.assertFalse(runtime.theHold.holding)

    def test_failed_conversion_reports_error_and_restores_via_undo(self):
        runtime = V2Runtime()
        adapter, runtime, node, undo = v2_setup(
            level=2, edges=(1,), base_class="Box", runtime=runtime
        )

        def boom(node_value, target):
            raise RuntimeError("conversion exploded")

        runtime.convertTo = boom
        assessment_value = inspect_target(runtime)
        prepared = prepare_target(runtime, assessment_value, undo_factory=undo)
        self.assertFalse(prepared.ok)
        self.assertEqual(prepared.status, "error")
        self.assertFalse(prepared.converted)
        self.assertEqual(len(undo.calls), 1)
        self.assertEqual(undo.exited[0][1], RuntimeError)

    def test_foreign_hold_after_panel_switch_blocks_before_native_mutation(self):
        runtime = PanelDriftRuntime()
        adapter, runtime, node, undo = v2_setup(
            level=2, edges=(1,), base_class="Box", runtime=runtime
        )
        runtime.panel_callback = lambda rt: setattr(rt.theHold, "holding", True)
        prepared = prepare_target(runtime, inspect_target(runtime), undo_factory=undo)
        self.assertFalse(prepared.ok, prepared.reason)
        self.assertNotIn("convertTo", runtime.v2_calls)
        self.assertEqual(undo.calls, [])

        modifier = FakeModifier("TurboSmooth", handle=962)
        runtime = PanelDriftRuntime()
        adapter, runtime, node, undo = v2_setup(
            level=2, edges=(1,), modifiers=(modifier,), runtime=runtime
        )
        runtime.panel_callback = lambda rt: setattr(rt.theHold, "holding", True)
        prepared = collapse_to_poly(
            runtime, inspect_target(runtime), confirmed=True, undo_factory=undo
        )
        self.assertFalse(prepared.ok, prepared.reason)
        self.assertNotIn("collapseStack", runtime.v2_calls)
        self.assertEqual(undo.calls, [])

    def test_primitive_preparation_refuses_without_capturable_state(self):
        runtime = V2Runtime()
        adapter, runtime, node, undo = v2_setup(
            level=2, edges=(1,), base_class="Box", runtime=runtime
        )
        del node.baseObject.useSoftSel
        prepared = prepare_target(runtime, inspect_target(runtime), undo_factory=undo)
        self.assertFalse(prepared.ok, prepared.reason)
        self.assertEqual(prepared.status, "blocked")
        self.assertIn("primitive state could not be captured", prepared.reason)
        self.assertNotIn("convertTo", runtime.v2_calls)
        self.assertEqual(undo.calls, [])

    def test_box_conversion_rollback_detects_unrestored_primitive_state(self):
        class PrimitiveFailingRuntime(V2Runtime):
            def convertTo(self, node_value, target):
                self.v2_calls.append("convertTo")
                node_value.baseObject.extrusionType = 99
                raise RuntimeError("conversion failed after a property write")

        runtime = PrimitiveFailingRuntime()
        undo = OwnedHoldUndoFactory(runtime, restore=None)
        adapter, runtime, node, undo = v2_setup(
            level=2, edges=(1,), base_class="Box", runtime=runtime, undo=undo
        )
        prepared = prepare_target(runtime, inspect_target(runtime), undo_factory=undo)
        self.assertFalse(prepared.ok, prepared.reason)
        self.assertEqual(prepared.status, "error")
        self.assertTrue(prepared.rollback_unverified)
        self.assertIn("Rollback could not be verified", prepared.reason)
        self.assertFalse(runtime.theHold.holding)

    def test_tier_two_sets_show_end_result_and_preserves_modifiers(self):
        modifier = FakeModifier("TurboSmooth", handle=913)
        runtime = V2Runtime()
        adapter, runtime, node, undo = v2_setup(
            level=2,
            edges=(1,),
            modifiers=(modifier,),
            show_end_result=False,
            runtime=runtime,
        )
        assessment_value = inspect_target(runtime)
        self.assertEqual(assessment_value.tier, "base_with_modifiers")
        prepared = prepare_target(runtime, assessment_value, undo_factory=undo)
        self.assertTrue(prepared.ok, prepared.reason)
        self.assertTrue(node.showEndResult)
        self.assertIs(node.modifiers[0], modifier)
        self.assertEqual(len(node.modifiers), 1)
        self.assertTrue(runtime.modPanel.current is node.baseObject)
        self.assertEqual(undo.calls, [])

    def test_tier_two_retains_existing_show_end_result_true(self):
        modifier = FakeModifier("Symmetry", handle=914)
        runtime = V2Runtime()
        adapter, runtime, node, undo = v2_setup(
            level=2,
            edges=(1,),
            modifiers=(modifier,),
            show_end_result=True,
            runtime=runtime,
        )
        prepared = prepare_target(runtime, inspect_target(runtime), undo_factory=undo)
        self.assertTrue(prepared.ok, prepared.reason)
        self.assertTrue(node.showEndResult)

    def test_tier_two_unreadable_show_end_result_blocks_before_writes(self):
        modifier = FakeModifier("TurboSmooth", handle=915)
        runtime = V2Runtime()
        adapter, runtime, node, undo = v2_setup(
            level=2,
            edges=(1,),
            modifiers=(modifier,),
            show_end_result=False,
            runtime=runtime,
            panel_open=False,
        )
        node.showEndResult = None
        assessment_value = inspect_target(runtime)
        self.assertEqual(assessment_value.tier, "base_with_modifiers")
        self.assertIsNone(assessment_value.show_end_result)
        prepared = prepare_target(runtime, assessment_value, undo_factory=undo)
        self.assertFalse(prepared.ok)
        self.assertEqual(prepared.status, "blocked")
        self.assertEqual(runtime.modPanel.writes, [])

    def test_edit_poly_blocks_preparation_before_any_write(self):
        edit_poly = FakeModifier("Edit_Poly", handle=916)
        runtime = V2Runtime()
        adapter, runtime, node, undo = v2_setup(
            level=2, edges=(1,), modifiers=(edit_poly,), runtime=runtime
        )
        prepared = prepare_target(runtime, inspect_target(runtime), undo_factory=undo)
        self.assertFalse(prepared.ok)
        self.assertEqual(prepared.status, "blocked")
        self.assertIn("Edit Poly modifiers are outside V2", prepared.reason)
        self.assertEqual(runtime.modPanel.writes, [])
        self.assertEqual(runtime.v2_calls, [])

    def test_instanced_target_blocks_preparation_before_any_write(self):
        runtime = V2Runtime()
        adapter, runtime, node, undo = v2_setup(level=2, runtime=runtime)
        runtime.dependent_overrides[id(node.baseObject)] = []
        prepared = prepare_target(runtime, inspect_target(runtime), undo_factory=undo)
        self.assertFalse(prepared.ok)
        self.assertIn("Make Unique", prepared.reason)
        self.assertEqual(runtime.modPanel.writes, [])
        self.assertEqual(runtime.v2_calls, [])

    def test_stale_assessment_is_rejected(self):
        runtime = V2Runtime()
        adapter, runtime, node, undo = v2_setup(level=2, edges=(1,), runtime=runtime)
        stale = inspect_target(runtime)
        node.mesh.selections[ContextLevel.EDGE] = {2}
        prepared = prepare_target(runtime, stale, undo_factory=undo)
        self.assertFalse(prepared.ok)
        self.assertEqual(prepared.status, "blocked")
        self.assertIn("changed before preparation", prepared.reason)

    def test_panel_switch_drift_aborts_before_convert_collapse_and_level_write(self):
        drifts = (
            ("selection", lambda rt: setattr(rt, "selection", [])),
            (
                "stack",
                lambda rt: rt.selection[0].modifiers.append(
                    FakeModifier("TurboSmooth", handle=960)
                ),
            ),
            (
                "base",
                lambda rt: setattr(
                    rt.selection[0],
                    "baseObject",
                    FakeBaseObject(
                        rt.selection[0].baseObject.class_name, handle=970
                    ),
                ),
            ),
            (
                "transform",
                lambda rt: setattr(rt.selection[0], "transform", "matrix3-drift"),
            ),
            ("time", lambda rt: setattr(rt, "currentTime", 42)),
        )
        for label, drift in drifts:
            with self.subTest(tier="convert", drift=label):
                runtime = PanelDriftRuntime()
                adapter, runtime, node, undo = v2_setup(
                    level=2, edges=(1,), base_class="Box", runtime=runtime
                )
                runtime.panel_callback = drift
                prepared = prepare_target(
                    runtime, inspect_target(runtime), undo_factory=undo
                )
                self.assertFalse(prepared.ok, prepared.reason)
                self.assertNotIn("convertTo", runtime.v2_calls)
                self.assertEqual(runtime.modPanel.writes, [])
                self.assertEqual(runtime.subObjectLevel, 2)

            with self.subTest(tier="collapse", drift=label):
                runtime = PanelDriftRuntime()
                modifier = FakeModifier("TurboSmooth", handle=961)
                adapter, runtime, node, undo = v2_setup(
                    level=2, edges=(1,), modifiers=(modifier,), runtime=runtime
                )
                runtime.panel_callback = drift
                prepared = collapse_to_poly(
                    runtime,
                    inspect_target(runtime),
                    confirmed=True,
                    undo_factory=undo,
                )
                self.assertFalse(prepared.ok, prepared.reason)
                self.assertNotIn("collapseStack", runtime.v2_calls)
                self.assertEqual(runtime.modPanel.writes, [])
                self.assertEqual(runtime.subObjectLevel, 2)


class CollapseTests(unittest.TestCase):
    def test_unconfirmed_collapse_is_blocked_without_mutation(self):
        modifier = FakeModifier("TurboSmooth", handle=917)
        runtime = V2Runtime()
        adapter, runtime, node, undo = v2_setup(
            level=2, edges=(1,), modifiers=(modifier,), runtime=runtime
        )
        prepared = collapse_to_poly(
            runtime, inspect_target(runtime), confirmed=False, undo_factory=undo
        )
        self.assertFalse(prepared.ok)
        self.assertIn("requires explicit confirmation", prepared.reason)
        self.assertEqual(runtime.v2_calls, [])
        self.assertEqual(node.collapse_calls, 0)

    def test_confirmed_collapse_bakes_stack_in_one_chunk(self):
        modifier = FakeModifier("TurboSmooth", handle=918)
        runtime = V2Runtime()
        undo = OwnedHoldUndoFactory(runtime)
        adapter, runtime, node, undo = v2_setup(
            level=2, edges=(1,), modifiers=(modifier,), runtime=runtime, undo=undo
        )
        prepared = collapse_to_poly(
            runtime, inspect_target(runtime), confirmed=True, undo_factory=undo
        )
        self.assertTrue(prepared.ok, prepared.reason)
        self.assertTrue(prepared.collapsed)
        self.assertEqual(node.modifiers, [])
        self.assertEqual(node.collapse_calls, 1)
        self.assertEqual(len(undo.calls), 1)
        self.assertEqual(undo.calls[0][1], "3dsModeler V2: collapse to Editable Poly")
        self.assertFalse(runtime.theHold.holding)

    def test_confirmed_collapse_converts_non_poly_with_modifiers(self):
        modifier = FakeModifier("TurboSmooth", handle=919)
        runtime = V2Runtime()
        undo = OwnedHoldUndoFactory(runtime)
        adapter, runtime, node, undo = v2_setup(
            level=2,
            edges=(1,),
            base_class="Box",
            modifiers=(modifier,),
            runtime=runtime,
            undo=undo,
        )
        prepared = collapse_to_poly(
            runtime, inspect_target(runtime), confirmed=True, undo_factory=undo
        )
        self.assertTrue(prepared.ok, prepared.reason)
        self.assertEqual(runtime.v2_calls.count("convertTo"), 1)
        self.assertEqual(node.modifiers, [])
        self.assertFalse(runtime.theHold.holding)

    def test_collapse_of_instanced_target_is_refused(self):
        runtime = V2Runtime()
        adapter, runtime, node, undo = v2_setup(level=2, runtime=runtime)
        runtime.dependent_overrides[id(node.baseObject)] = []
        prepared = collapse_to_poly(
            runtime, inspect_target(runtime), confirmed=True, undo_factory=undo
        )
        self.assertFalse(prepared.ok)
        self.assertIn("Make Unique", prepared.reason)
        self.assertEqual(runtime.v2_calls, [])

    def test_collapse_of_already_clean_poly_is_noop(self):
        runtime = V2Runtime()
        adapter, runtime, node, undo = v2_setup(level=2, runtime=runtime)
        prepared = collapse_to_poly(
            runtime, inspect_target(runtime), confirmed=True, undo_factory=undo
        )
        self.assertTrue(prepared.ok)
        self.assertEqual(prepared.status, "noop")
        self.assertEqual(runtime.v2_calls, [])


class SwitchAndPivotTests(unittest.TestCase):
    def _prepared(self, level=2, edges=(1,), faces=(1,), open_edges=()):
        runtime = V2Runtime()
        adapter, runtime, node, undo = v2_setup(
            level=level,
            edges=edges,
            faces=faces,
            open_edges=open_edges,
            runtime=runtime,
        )
        prepared = prepare_target(runtime, inspect_target(runtime), undo_factory=undo)
        self.assertTrue(prepared.ok, prepared.reason)
        return runtime, node, prepared, undo

    def test_switch_without_conversion_preserves_selection(self):
        runtime, node, prepared, undo = self._prepared(level=2, edges=(1,))
        outcome = switch_prepared_context(runtime, prepared, "face")
        self.assertTrue(outcome.ok, outcome.reason)
        self.assertFalse(outcome.converted)
        self.assertEqual(runtime.subObjectLevel, 4)
        self.assertEqual(node.EditablePoly.conversions, [])

    def test_switch_with_conversion_uses_documented_enums(self):
        runtime, node, prepared, undo = self._prepared(level=2, edges=(1,))
        outcome = switch_prepared_context(
            runtime, prepared, "face", convert_selection=True
        )
        self.assertTrue(outcome.ok, outcome.reason)
        self.assertTrue(outcome.converted)
        self.assertEqual(node.EditablePoly.conversions, [("Edge", "Face")])
        self.assertEqual(runtime.subObjectLevel, 4)

    def test_same_context_switch_is_a_noop(self):
        runtime, node, prepared, undo = self._prepared(level=2, edges=(1,))
        outcome = switch_prepared_context(runtime, prepared, "edge")
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.status, "noop")
        self.assertEqual(node.EditablePoly.conversions, [])

    def test_invalid_v2_context_is_rejected(self):
        runtime, node, prepared, undo = self._prepared(level=2, edges=(1,))
        outcome = switch_prepared_context(runtime, prepared, "polygon")
        self.assertFalse(outcome.ok)
        self.assertIn("polygon", outcome.reason)

    def test_fill_pivot_converts_open_edges_only(self):
        runtime, node, prepared, undo = self._prepared(
            level=2, edges=(1, 2), open_edges=(1,)
        )
        outcome = pivot_open_edges_to_border(runtime, prepared)
        self.assertTrue(outcome.ok, outcome.reason)
        self.assertEqual(outcome.context, "border")
        self.assertEqual(node.EditablePoly.conversions, [("Edge", "Border")])
        self.assertEqual(node.mesh.selections[ContextLevel.EDGE], {1})
        self.assertEqual(runtime.subObjectLevel, 3)

    def test_fill_pivot_reads_boolean_iterating_bitarray(self):
        runtime = V2Runtime()
        adapter, runtime, node, undo = v2_setup(
            level=2, edges=(2, 4), runtime=runtime
        )
        prepared = prepare_target(runtime, inspect_target(runtime), undo_factory=undo)
        self.assertTrue(prepared.ok, prepared.reason)
        node.mesh.getOpenEdges = lambda target: BooleanMaskBitArray(
            [False, True, False, True]
        )
        outcome = pivot_open_edges_to_border(runtime, prepared)
        self.assertTrue(outcome.ok, outcome.reason)
        self.assertEqual(outcome.indices, (2, 4))
        self.assertEqual(node.EditablePoly.conversions, [("Edge", "Border")])
        self.assertEqual(runtime.subObjectLevel, 3)

    def test_fill_pivot_with_no_open_edges_is_blocked_without_writes(self):
        runtime, node, prepared, undo = self._prepared(
            level=2, edges=(1, 2), open_edges=()
        )
        outcome = pivot_open_edges_to_border(runtime, prepared)
        self.assertFalse(outcome.ok)
        self.assertIn("closed", outcome.reason)
        self.assertEqual(node.EditablePoly.conversions, [])
        self.assertEqual(runtime.subObjectLevel, 2)


if __name__ == "__main__":
    unittest.main()

"""Palette controller lifecycle, stale-state, confirmation and dispatch tests."""

from __future__ import annotations

import unittest
from pathlib import Path

from max2025_menu_prototype.core.atlas import load_atlas
from max2025_menu_prototype.core.contracts import LaunchResult
from max2025_menu_prototype.core.maneuver_contracts import ManeuverContext, SystemCommand
from max2025_menu_prototype.core.maneuver_simulated import SimulatedManeuverHost
from max2025_menu_prototype.core.palette_controller import PaletteController

CHECKOUT_ROOT = Path(__file__).resolve().parents[1]


class RecordingHost(SimulatedManeuverHost):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.requests = []
        self.switches = []
        self.order = []

    def execute(self, request):
        self.order.append("execute")
        self.requests.append(request)
        return super().execute(request)

    def switch_context(self, context, *, expected_snapshot, convert_selection=False):
        self.switches.append((context, convert_selection, expected_snapshot))
        return super().switch_context(
            context,
            expected_snapshot=expected_snapshot,
            convert_selection=convert_selection,
        )


class ResultHost(RecordingHost):
    def __init__(self, *args, result=None, error=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.result = result
        self.error = error

    def execute(self, request):
        self.order.append("execute")
        self.requests.append(request)
        if self.error:
            raise RuntimeError("fake host failure")
        return self.result


class PaletteControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.atlas = load_atlas(CHECKOUT_ROOT).atlas

    def make(self, **kwargs) -> tuple[PaletteController, RecordingHost]:
        host = RecordingHost(self.atlas, **kwargs)
        return PaletteController(self.atlas, host), host

    def activate(self, controller: PaletteController, row_id: str):
        view = controller.view()
        return controller.activate(
            row_id, session_id=view.session_id, generation=view.generation
        )

    def row(self, view, maneuver_id: str):
        return next(row for row in view.rows if row.row_id == maneuver_id)

    # ------------------------------------------------------------------ open

    def test_open_returns_a_fresh_non_mutating_session(self):
        controller, host = self.make()
        view = controller.open()
        self.assertTrue(view.session_id)
        self.assertGreaterEqual(view.generation, 1)
        self.assertEqual(view.context, ManeuverContext.VERTEX)
        self.assertEqual(len(view.rows), 36)
        self.assertIsNotNone(view.highlighted_row_id)
        self.assertFalse(view.busy)
        self.assertIsNone(view.pending_confirmation)
        self.assertEqual(host.launch_log, [])

    def test_set_query_narrows_rows_and_changes_generation(self):
        controller, _host = self.make()
        opened = controller.open()
        searched = controller.set_query("bevel")
        self.assertGreater(searched.generation, opened.generation)
        self.assertIn("bevel", {row.row_id for row in searched.rows})
        self.assertTrue(all("bevel" in row.row_id or row.score for row in searched.rows))

    def test_navigate_clamps_to_available_rows(self):
        controller, _host = self.make()
        view = controller.open()
        for delta in (1, 999, -999):
            view = controller.navigate(delta)
            self.assertIn(
                view.highlighted_row_id, {row.row_id for row in view.rows}
            )

    # -------------------------------------------------------------- activate

    def test_activate_dispatches_once_and_closes_on_success(self):
        controller, host = self.make()
        view = controller.open()
        result = self.activate(controller, "bevel")
        self.assertEqual(result.last_result.status, "simulated")
        self.assertEqual(result.rows, ())
        self.assertEqual(len(host.launch_log), 1)
        self.assertEqual(host.launch_log[0]["target"], "Chamfer")
        before = len(host.launch_log)
        again = controller.activate(
            "bevel", session_id=view.session_id, generation=view.generation
        )
        self.assertIn("stale", again.status)
        self.assertEqual(len(host.launch_log), before)

    def test_stale_session_generation_and_unknown_rows_do_not_dispatch(self):
        controller, host = self.make()
        view = controller.open()
        stale_session = controller.activate(
            "bevel", session_id="wrong", generation=view.generation
        )
        self.assertIn("stale", stale_session.status)
        stale_generation = controller.activate(
            "bevel", session_id=view.session_id, generation=view.generation + 99
        )
        self.assertIn("stale", stale_generation.status)
        unknown = controller.activate(
            "no_such_row",
            session_id=controller.view().session_id,
            generation=controller.view().generation,
        )
        self.assertIn("no longer available", unknown.status)
        self.assertEqual(host.launch_log, [])

    def test_disabled_rows_report_reason_without_dispatch(self):
        controller, host = self.make()
        host.set_blocked_reason("Undo is holding; finish the operation.")
        view = controller.open()
        row = self.row(view, "bevel")
        self.assertFalse(row.enabled)
        result = self.activate(controller, "bevel")
        self.assertEqual(result.last_result.status, "blocked")
        self.assertIn("Undo is holding", result.status)
        self.assertEqual(host.launch_log, [])

    def test_equal_count_selection_change_is_stale(self):
        controller, host = self.make()
        controller.open()
        host.set_context(
            ManeuverContext.VERTEX, selection_count=1, selection_token="changed-identity"
        )
        result = self.activate(controller, "bevel")
        self.assertEqual(result.last_result.status, "blocked")
        self.assertEqual(host.launch_log, [])

    def test_pre_dispatch_runs_before_execute_and_can_block(self):
        controller, host = self.make()
        controller.open()
        controller.set_pre_dispatch(lambda maneuver_id: host.order.append("pre"))
        self.activate(controller, "bevel")
        self.assertEqual(host.order, ["pre", "execute"])

        failing, failing_host = self.make()
        failing.open()

        def boom(maneuver_id):
            failing_host.order.append("pre")
            raise RuntimeError("focus hook failed")

        failing.set_pre_dispatch(boom)
        result = self.activate(failing, "bevel")
        self.assertEqual(failing_host.order, ["pre"])
        self.assertEqual(result.last_result.status, "error")
        self.assertTrue(result.rows)
        self.assertEqual(failing_host.launch_log, [])

    def test_reentrant_activation_during_pre_dispatch_is_rejected(self):
        controller, host = self.make()
        view = controller.open()

        def reenter(maneuver_id):
            controller.activate(
                "bevel", session_id=view.session_id, generation=view.generation
            )

        controller.set_pre_dispatch(reenter)
        result = self.activate(controller, "bevel")
        self.assertEqual(len(host.requests), 1)
        self.assertEqual(result.last_result.status, "simulated")

    def test_blocked_and_error_results_reopen_with_reason(self):
        blocked_host = ResultHost(
            self.atlas,
            result=LaunchResult("blocked", "bevel", "Target is not safe."),
        )
        controller = PaletteController(self.atlas, blocked_host)
        controller.open()
        result = self.activate(controller, "bevel")
        self.assertEqual(result.last_result.status, "blocked")
        self.assertTrue(result.rows)
        self.assertIn("Blocked", result.status)

        error_host = ResultHost(self.atlas, error=True)
        controller = PaletteController(self.atlas, error_host)
        controller.open()
        result = self.activate(controller, "bevel")
        self.assertEqual(result.last_result.status, "error")
        self.assertTrue(result.rows)
        self.assertIn("Error", result.status)

    # ----------------------------------------------------------- confirmation

    def test_collapse_requires_confirmation_and_token_is_one_use(self):
        controller, host = self.make(context="object", selection_count=1)
        controller.open()
        pending = self.activate(controller, "collapse_to_poly")
        self.assertIsNotNone(pending.pending_confirmation)
        self.assertEqual(host.launch_log, [])
        prompt = pending.pending_confirmation

        wrong = controller.confirm("not-the-token", True)
        self.assertIn("no longer valid", wrong.status)
        self.assertIsNotNone(wrong.pending_confirmation)

        declined = controller.confirm(prompt.token, False)
        self.assertIsNone(declined.pending_confirmation)
        self.assertEqual(declined.last_result.status, "cancelled")
        self.assertEqual(host.launch_log, [])

        pending = self.activate(controller, "collapse_to_poly")
        prompt = pending.pending_confirmation
        accepted = controller.confirm(prompt.token, True)
        self.assertEqual(accepted.last_result.status, "simulated")
        self.assertTrue(host.requests[-1].confirmed)
        self.assertIsNone(accepted.pending_confirmation)

        reused = controller.confirm(prompt.token, True)
        self.assertIn("no longer valid", reused.status)
        self.assertEqual(len(host.requests), 1)

    def test_confirmation_is_invalidated_by_a_scene_change(self):
        controller, host = self.make(context="object", selection_count=1)
        controller.open()
        pending = self.activate(controller, "collapse_to_poly")
        prompt = pending.pending_confirmation
        host.advance_revision()
        result = controller.confirm(prompt.token, True)
        self.assertEqual(result.last_result.status, "blocked")
        self.assertEqual(host.launch_log, [])

    def test_cancel_clears_pending_confirmation(self):
        controller, host = self.make(context="object", selection_count=1)
        controller.open()
        pending = self.activate(controller, "collapse_to_poly")
        self.assertIsNotNone(pending.pending_confirmation)
        cancelled = controller.cancel("escape")
        self.assertIsNone(cancelled.pending_confirmation)
        self.assertEqual(cancelled.rows, ())
        self.assertEqual(cancelled.last_result.status, "cancelled")
        self.assertEqual(host.launch_log, [])

    # ------------------------------------------------------------ conversion

    def test_context_switch_requests_are_forwarded_and_refresh_the_view(self):
        controller, host = self.make(context="vertex", selection_count=1)
        controller.open()
        switched = controller.switch_context("border", convert_selection=True)
        self.assertEqual(switched.context, ManeuverContext.BORDER)
        self.assertTrue(host.switches[0][1])
        self.assertTrue(switched.rows)
        self.assertTrue(
            all(
                row.pivot_context is None
                for row in switched.rows
                if self.atlas.by_id(row.maneuver_id).supports(ManeuverContext.BORDER)
            )
        )

    def test_convert_maneuver_sets_convert_selection_on_the_request(self):
        controller, host = self.make(context="object", selection_count=1)
        controller.open()
        result = self.activate(controller, "convert_to_verts")
        self.assertEqual(result.last_result.status, "simulated")
        self.assertTrue(host.requests[-1].convert_selection)

    def test_pivot_request_carries_the_pivot_context(self):
        controller, host = self.make(context="edge", selection_count=1)
        view = controller.open()
        fill = self.row(view, "fill")
        self.assertEqual(fill.pivot_context, ManeuverContext.BORDER)
        result = self.activate(controller, "fill")
        self.assertEqual(result.last_result.status, "simulated")
        self.assertEqual(host.requests[-1].pivot_context, ManeuverContext.BORDER)

    def test_closed_palette_context_switch_does_not_repopulate_rows(self):
        controller, _host = self.make(context="vertex", selection_count=1)
        controller.open()
        controller.cancel()
        switched = controller.switch_context("edge")
        self.assertEqual(switched.context, ManeuverContext.EDGE)
        self.assertEqual(switched.rows, ())

    # ---------------------------------------------------------------- system

    def test_system_callbacks_dispatch_and_pull_unload_close_the_palette(self):
        calls = []
        callbacks = {
            SystemCommand.DIAGNOSTICS: lambda: calls.append("diag") or "cached report",
            SystemCommand.PULL_UPDATES: lambda: calls.append("pull"),
            SystemCommand.UNLOAD_UI: lambda: calls.append("unload") or "done",
        }
        controller = PaletteController(self.atlas, SimulatedManeuverHost(self.atlas), callbacks)
        controller.open()
        view = controller.set_query("> diag")
        result = self.activate(controller, "system:diagnostics")
        self.assertEqual(calls, ["diag"])
        self.assertEqual(result.last_result.status, "applied")
        self.assertTrue(result.rows)

        controller.set_query("> pull")
        result = self.activate(controller, "system:pull_updates")
        self.assertEqual(calls, ["diag", "pull"])
        self.assertEqual(result.rows, ())

        controller.open()
        controller.set_query("> unload")
        result = self.activate(controller, "system:unload_ui")
        self.assertEqual(calls, ["diag", "pull", "unload"])
        self.assertEqual(result.rows, ())

    def test_missing_system_callback_is_a_blocked_result(self):
        controller = PaletteController(self.atlas, SimulatedManeuverHost(self.atlas))
        controller.open()
        controller.set_query("> diag")
        result = self.activate(controller, "system:diagnostics")
        self.assertEqual(result.last_result.status, "blocked")
        self.assertIn("not installed", result.status)

    # -------------------------------------------------------------- lifecycle

    def test_dispose_is_idempotent_and_blocks_further_use(self):
        controller, _host = self.make()
        controller.open()
        first = controller.dispose()
        second = controller.dispose()
        self.assertEqual(first.status, "Disposed.")
        self.assertEqual(second.status, "Disposed.")
        self.assertEqual(second.rows, ())
        after = self.activate(controller, "bevel")
        self.assertEqual(after.rows, ())
        controller.set_pre_dispatch(lambda: None)
        self.assertEqual(controller.view().rows, ())

    def test_warnings_are_sanitized_into_the_open_status(self):
        controller = PaletteController(
            self.atlas,
            SimulatedManeuverHost(self.atlas),
            warnings=("User alias overlay ignored: the overlay file is invalid.",),
        )
        view = controller.open()
        self.assertIn("overlay ignored", view.status)
        self.assertNotIn("/", view.status)

    def test_view_is_immutable_and_contains_no_host_handle(self):
        controller, _host = self.make()
        view = controller.open()
        with self.assertRaises((TypeError, AttributeError)):
            view.rows = ()
        self.assertFalse(hasattr(view, "_host"))
        payload = view.to_dict()
        self.assertEqual(payload["context"], "vertex")
        self.assertIsInstance(payload["rows"], list)


if __name__ == "__main__":
    unittest.main()

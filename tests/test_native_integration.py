"""Controller integration tests for the frozen parameter/prompt lifecycle."""

from __future__ import annotations

import math
import unittest

from max2025_menu_prototype.core.catalog import ACTIONS
from max2025_menu_prototype.core.contracts import (
    Availability,
    ContextLevel,
    ContextSnapshot,
    LaunchResult,
    MenuEvent,
)
from max2025_menu_prototype.core.controller import (
    PARAMETER_DIALOG_UNAVAILABLE,
    MenuController,
)
from max2025_menu_prototype.core.profile import default_profile


def snapshot(level: ContextLevel = ContextLevel.POLYGON, **overrides) -> ContextSnapshot:
    values = dict(
        level=level,
        target_kind="editable_poly",
        target_token="poly:base",
        selection_count=1,
        selection_token=f"sel:{level.value}:1",
        panel_ready=True,
        conflicting_mode=False,
        revision=1,
    )
    values.update(overrides)
    return ContextSnapshot(**values)


class RecordingHost:
    """Contract fake: records snapshots, availability, and exact launch args."""

    def __init__(self, current: ContextSnapshot | None = None) -> None:
        self.current = current or snapshot()
        self.availability_map = {
            action.id: Availability(True)
            for action in ACTIONS
            if action.id != "menu.repeat"
        }
        self.snapshot_calls = 0
        self.availability_calls = 0
        self.launch_calls: list[tuple[str, ContextSnapshot, dict | None]] = []
        self.results: dict[str, LaunchResult] = {}

    def snapshot(self) -> ContextSnapshot:
        self.snapshot_calls += 1
        return self.current

    def availability(self, context: ContextSnapshot) -> dict[str, Availability]:
        self.availability_calls += 1
        return dict(self.availability_map)

    def launch(
        self,
        action_id: str,
        expected_context: ContextSnapshot,
        parameters: dict | None = None,
    ) -> LaunchResult:
        self.launch_calls.append((action_id, expected_context, parameters))
        return self.results.get(
            action_id, LaunchResult("applied", action_id, "fake postcondition checked")
        )


class LegacyHost:
    """An older parameter-free adapter that must stay callable as-is."""

    def __init__(self, current: ContextSnapshot | None = None) -> None:
        self.current = current or snapshot(ContextLevel.VERTEX)
        self.calls: list[tuple[str, ContextSnapshot]] = []

    def snapshot(self) -> ContextSnapshot:
        return self.current

    def availability(self, context: ContextSnapshot) -> dict[str, Availability]:
        return {
            action.id: Availability(True)
            for action in ACTIONS
            if action.id != "menu.repeat"
        }

    def launch(self, action_id: str, expected_context: ContextSnapshot) -> LaunchResult:
        self.calls.append((action_id, expected_context))
        return LaunchResult("applied", action_id)


class ControllerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.host = RecordingHost()
        self.ctl = MenuController(self.host, default_profile())
        self.sequence = 0

    def open(self, invocation: str = "latched"):
        view = self.ctl.open((10.0, 10.0), 1000.0, invocation)
        self.sequence = view.sequence
        return view

    def send(self, view, kind: str, **fields):
        self.sequence += 1
        fields.setdefault("session_id", view.session_id)
        fields.setdefault("sequence", self.sequence)
        fields.setdefault("time_ms", 1000.0)
        return self.ctl.handle(MenuEvent(kind=kind, **fields))

    def row(self, view, action_id: str):
        return next(row for row in view.rows if row.action_id == action_id)


class PromptUnavailableTests(ControllerTestCase):
    def test_parameterized_rows_are_disabled_without_prompt(self) -> None:
        view = self.open()
        extrude = self.row(view, "model.extrude")
        self.assertFalse(extrude.enabled)
        self.assertEqual(extrude.reason, PARAMETER_DIALOG_UNAVAILABLE)

        result = self.ctl.invoke("model.extrude")
        self.assertEqual(result.last_result.status, "blocked")
        self.assertEqual(result.last_result.message, PARAMETER_DIALOG_UNAVAILABLE)
        self.assertEqual(self.host.launch_calls, [])

        self.send(view, "click_row", action_id="model.extrude")
        self.assertEqual(self.host.launch_calls, [])
        self.assertIn("parameter dialog", self.ctl.view().status)

    def test_parameter_free_rows_still_launch(self) -> None:
        view = self.open()
        self.assertTrue(self.row(view, "selection.grow").enabled)
        self.send(view, "click_row", action_id="selection.grow")
        self.assertEqual([call[0] for call in self.host.launch_calls], ["selection.grow"])
        self.assertIsNone(self.host.launch_calls[0][2])


class PromptLifecycleTests(ControllerTestCase):
    def test_prompt_receives_frozen_input_and_menu_is_closed_busy(self) -> None:
        seen = {}

        def prompt(spec, context, schema):
            seen.update(
                spec=spec,
                context=context,
                schema=schema,
                phase=self.ctl.view().phase,
                busy=self.ctl.summary().busy,
            )
            return {"height": 2, "extrusion_type": "local_normal"}

        self.ctl.set_parameter_prompt(prompt)
        view = self.open()
        result = self.send(view, "click_row", action_id="model.extrude")

        self.assertEqual(seen["spec"].id, "model.extrude")
        self.assertEqual(seen["context"], self.host.current)
        self.assertEqual(
            [item.name for item in seen["schema"]], ["height", "extrusion_type"]
        )
        self.assertEqual(seen["phase"], "closed")
        self.assertTrue(seen["busy"])
        self.assertEqual(result.phase, "closed")
        self.assertEqual(result.last_result.status, "applied")
        self.assertFalse(self.ctl.summary().busy)
        self.assertEqual(
            self.host.launch_calls,
            [
                (
                    "model.extrude",
                    self.host.current,
                    {"height": 2.0, "extrusion_type": "local_normal"},
                )
            ],
        )

    def test_pre_dispatch_runs_after_close_and_before_prompt_and_launch(self) -> None:
        order: list[tuple[str, str]] = []
        self.ctl.set_pre_dispatch(
            lambda action_id: order.append((f"pre:{action_id}", self.ctl.view().phase))
        )

        def prompt(spec, context, schema):
            order.append(("prompt", self.ctl.view().phase))
            return {"height": 1.0, "extrusion_type": "group"}

        self.ctl.set_parameter_prompt(prompt)
        original = self.host.launch

        def launch(action_id, expected, parameters=None):
            order.append(("launch", self.ctl.view().phase))
            return original(action_id, expected, parameters)

        self.host.launch = launch
        view = self.open()
        self.send(view, "click_row", action_id="model.extrude")
        self.assertEqual(
            order,
            [
                ("pre:model.extrude", "closed"),
                ("prompt", "closed"),
                ("launch", "closed"),
            ],
        )

    def test_cancel_launches_nothing_and_keeps_previous_repeat(self) -> None:
        self.open()
        self.send(self.ctl.view(), "click_row", action_id="selection.grow")
        promoted = self.host.launch_calls[-1][0]
        self.assertEqual(promoted, "selection.grow")

        self.ctl.set_parameter_prompt(lambda spec, context, schema: None)
        view = self.open()
        result = self.send(view, "click_row", action_id="model.extrude")
        self.assertEqual(result.last_result.status, "cancelled")
        self.assertIn("nothing applied", result.status)
        self.assertEqual([call[0] for call in self.host.launch_calls], ["selection.grow"])
        self.assertFalse(self.ctl.summary().busy)

        repeat_view = self.open()
        self.assertEqual(
            self.row(repeat_view, "menu.repeat").label,
            "Repeat: relaunch Grow selection",
        )

    def test_invalid_parameters_launch_nothing_and_keep_repeat(self) -> None:
        self.open()
        self.send(self.ctl.view(), "click_row", action_id="selection.grow")
        self.ctl.set_parameter_prompt(
            lambda spec, context, schema: {"height": math.inf, "extrusion_type": "group"}
        )
        view = self.open()
        result = self.send(view, "click_row", action_id="model.extrude")
        self.assertEqual(result.last_result.status, "error")
        self.assertIn("finite", result.status)
        self.assertEqual([call[0] for call in self.host.launch_calls], ["selection.grow"])
        self.assertFalse(self.ctl.summary().busy)

    def test_prompt_exception_launches_nothing(self) -> None:
        def broken(spec, context, schema):
            raise RuntimeError("dialog exploded")

        self.ctl.set_parameter_prompt(broken)
        view = self.open()
        result = self.send(view, "click_row", action_id="model.extrude")
        self.assertEqual(result.last_result.status, "error")
        self.assertIn("dialog exploded", result.status)
        self.assertEqual(self.host.launch_calls, [])
        self.assertFalse(self.ctl.summary().busy)

    def test_revalidation_after_prompt_blocks_changed_selection(self) -> None:
        def prompt(spec, context, schema):
            self.host.current = snapshot(
                selection_token="sel:polygon:different", revision=2
            )
            return {"height": 1.0, "extrusion_type": "group"}

        self.ctl.set_parameter_prompt(prompt)
        view = self.open()
        result = self.send(view, "click_row", action_id="model.extrude")
        self.assertEqual(result.last_result.status, "blocked")
        self.assertIn("changed", result.status)
        self.assertEqual(self.host.launch_calls, [])

    def test_revalidation_after_prompt_blocks_changed_level(self) -> None:
        def prompt(spec, context, schema):
            self.host.current = snapshot(ContextLevel.EDGE, revision=2)
            return {"height": 1.0, "extrusion_type": "group"}

        self.ctl.set_parameter_prompt(prompt)
        view = self.open()
        result = self.send(view, "click_row", action_id="model.extrude")
        self.assertEqual(result.last_result.status, "blocked")
        self.assertEqual(self.host.launch_calls, [])

    def test_revalidation_after_prompt_blocks_capability_loss(self) -> None:
        def prompt(spec, context, schema):
            self.host.availability_map["model.extrude"] = Availability(
                False, "Target became unsupported."
            )
            return {"height": 1.0, "extrusion_type": "group"}

        self.ctl.set_parameter_prompt(prompt)
        view = self.open()
        result = self.send(view, "click_row", action_id="model.extrude")
        self.assertEqual(result.last_result.status, "blocked")
        self.assertIn("Target became unsupported", result.status)
        self.assertEqual(self.host.launch_calls, [])

    def test_reentrant_open_during_prompt_invalidates_token(self) -> None:
        def prompt(spec, context, schema):
            reopened = self.ctl.open((0, 0), 2000.0, "latched")
            self.assertIn("rejected", reopened.status)
            return {"height": 1.0, "extrusion_type": "group"}

        self.ctl.set_parameter_prompt(prompt)
        view = self.open()
        result = self.send(view, "click_row", action_id="model.extrude")
        self.assertEqual(result.last_result.status, "cancelled")
        self.assertIn("invalidated", result.status)
        self.assertEqual(self.host.launch_calls, [])
        self.assertFalse(self.ctl.summary().busy)

    def test_reentrant_event_during_prompt_invalidates_token(self) -> None:
        def prompt(spec, context, schema):
            self.ctl.handle(MenuEvent(kind="outside_click", session_id="late", sequence=1))
            return {"height": 1.0, "extrusion_type": "group"}

        self.ctl.set_parameter_prompt(prompt)
        view = self.open()
        result = self.send(view, "click_row", action_id="model.extrude")
        self.assertEqual(result.last_result.status, "cancelled")
        self.assertEqual(self.host.launch_calls, [])

    def test_profile_replacement_during_prompt_invalidates_token(self) -> None:
        def prompt(spec, context, schema):
            self.ctl.set_profile(default_profile())
            return {"height": 1.0, "extrusion_type": "group"}

        self.ctl.set_parameter_prompt(prompt)
        view = self.open()
        result = self.send(view, "click_row", action_id="model.extrude")
        self.assertEqual(result.last_result.status, "cancelled")
        self.assertEqual(self.host.launch_calls, [])

    def test_parameterized_repeat_reopens_dialog_with_current_values(self) -> None:
        calls: list[dict] = []

        def prompt(spec, context, schema):
            calls.append({item.name: item.default for item in schema})
            height = float(len(calls))
            return {"height": height, "extrusion_type": "group"}

        self.ctl.set_parameter_prompt(prompt)
        view = self.open()
        first = self.send(view, "click_row", action_id="model.extrude")
        self.assertEqual(first.last_result.status, "applied")

        repeat_view = self.open()
        repeat = self.row(repeat_view, "menu.repeat")
        self.assertTrue(repeat.enabled)
        self.assertEqual(repeat.label, "Repeat: relaunch Extrude")
        second = self.send(repeat_view, "click_row", action_id="menu.repeat")
        self.assertEqual(second.last_result.status, "applied")
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.host.launch_calls[0][2]["height"], 1.0)
        self.assertEqual(self.host.launch_calls[1][2]["height"], 2.0)

    def test_noop_or_cancelled_result_does_not_replace_repeat(self) -> None:
        for status in ("noop", "cancelled"):
            with self.subTest(status=status):
                self.setUp()
                self.host.results["model.extrude"] = LaunchResult(
                    status, "model.extrude", "no change"
                )
                self.ctl.set_parameter_prompt(
                    lambda spec, context, schema: {
                        "height": 0.0,
                        "extrusion_type": "group",
                    }
                )
                self.open()
                self.send(self.ctl.view(), "click_row", action_id="selection.grow")
                view = self.open()
                result = self.send(view, "click_row", action_id="model.extrude")
                self.assertEqual(result.last_result.status, status)
                repeat_view = self.open()
                self.assertEqual(
                    self.row(repeat_view, "menu.repeat").label,
                    "Repeat: relaunch Grow selection",
                )

    def test_destructive_confirmation_runs_through_prompt(self) -> None:
        values = iter(({"confirm": False}, {"confirm": True}))
        self.ctl.set_parameter_prompt(lambda spec, context, schema: next(values))
        self.host.current = snapshot(ContextLevel.VERTEX)
        view = self.open()
        denied = self.send(view, "click_row", action_id="model.collapse")
        self.assertEqual(denied.last_result.status, "error")
        self.assertIn("confirmation", denied.status)

        view = self.open()
        accepted = self.send(view, "click_row", action_id="model.collapse")
        self.assertEqual(accepted.last_result.status, "applied")
        self.assertEqual(
            [call[0] for call in self.host.launch_calls], ["model.collapse"]
        )
        self.assertEqual(
            self.host.launch_calls[0][2], {"confirm": True}
        )


class HostSignatureTests(ControllerTestCase):
    def test_parameterized_dispatch_without_third_argument_is_an_error(self) -> None:
        host = LegacyHost(snapshot(ContextLevel.POLYGON))
        ctl = MenuController(host, default_profile())
        ctl.set_parameter_prompt(
            lambda spec, context, schema: {
                "height": 1.0,
                "extrusion_type": "group",
            }
        )
        result = ctl.invoke("model.extrude")
        self.assertEqual(result.last_result.status, "error")
        self.assertIn("does not accept validated parameters", result.status)
        self.assertEqual(host.calls, [])

    def test_parameter_free_legacy_dispatch_uses_two_arguments(self) -> None:
        host = LegacyHost()
        ctl = MenuController(host, default_profile())
        ctl.open((0, 0), 0.0, "latched")
        result = ctl.invoke("selection.grow")
        self.assertEqual(result.last_result.status, "applied")
        self.assertEqual([call[0] for call in host.calls], ["selection.grow"])


class InvokeAndSummaryTests(ControllerTestCase):
    def test_invoke_ignores_profile_visibility_and_uses_same_dispatch(self) -> None:
        profile = default_profile()
        profile.set_visible("object", "context.vertex", False)
        self.ctl.set_profile(profile)
        self.host.results["context.vertex"] = LaunchResult(
            "launched", "context.vertex", "native level entry accepted"
        )
        result = self.ctl.invoke("context.vertex")
        self.assertEqual(result.last_result.status, "launched")
        self.assertEqual([call[0] for call in self.host.launch_calls], ["context.vertex"])
        self.assertIsNone(self.host.launch_calls[0][2])
        self.assertIn("Launched", result.status)

    def test_invoke_rejects_unknown_and_busy_without_dispatch(self) -> None:
        result = self.ctl.invoke("does.not.exist")
        self.assertEqual(result.last_result.status, "blocked")
        self.assertEqual(self.host.launch_calls, [])

        during_prompt = {}

        def prompt(spec, context, schema):
            busy_view = self.ctl.invoke("selection.grow")
            during_prompt["status"] = busy_view.status
            during_prompt["launches"] = len(self.host.launch_calls)
            return {"height": 1.0, "extrusion_type": "group"}

        self.ctl.set_parameter_prompt(prompt)
        view = self.open()
        self.send(view, "click_row", action_id="model.extrude")
        self.assertIn("awaiting parameters", during_prompt["status"])
        self.assertEqual(during_prompt["launches"], 0)
        self.assertEqual([call[0] for call in self.host.launch_calls], ["model.extrude"])

    def test_summary_is_read_only_and_reports_effective_state(self) -> None:
        view = self.open()
        summary = self.ctl.summary()
        self.assertEqual(summary.phase, view.phase)
        self.assertEqual(summary.context, ContextLevel.POLYGON)
        self.assertEqual(summary.target_kind, "editable_poly")
        self.assertEqual(summary.selection_count, 1)
        self.assertFalse(summary.busy)
        self.assertIsNone(summary.last_result)
        self.assertIsNone(summary.last_repeat_id)
        self.assertFalse(summary.availability_for("model.extrude").enabled)
        self.assertTrue(summary.availability_for("selection.grow").enabled)
        payload = summary.to_dict()
        self.assertEqual(payload["context"], "polygon")
        self.assertIsInstance(payload["availability"], list)

    def test_summary_after_invoke_reports_result(self) -> None:
        self.host.results["selection.grow"] = LaunchResult(
            "applied", "selection.grow", "grew"
        )
        result = self.ctl.invoke("selection.grow")
        self.assertEqual(result.last_result.status, "applied")
        summary = self.ctl.summary()
        self.assertEqual(summary.last_result.status, "applied")
        self.assertEqual(summary.last_repeat_id, "selection.grow")

    def test_new_launch_statuses_are_frozen_and_valid(self) -> None:
        for status in ("applied", "cancelled", "noop"):
            LaunchResult(status, "model.extrude", "x")
        with self.assertRaises(ValueError):
            LaunchResult("finished", "model.extrude", "x")

    def test_release_owned_native_mode_delegates_only_public_host_hook(self) -> None:
        self.assertIsNone(self.ctl.release_owned_native_mode())

        calls: list[int] = []

        def release():
            calls.append(1)
            return LaunchResult("launched", "model.exit_tool", "owned mode exited")

        self.host.release_owned_mode = release
        result = self.ctl.release_owned_native_mode()
        self.assertEqual(result.status, "launched")
        self.assertEqual(calls, [1])
        self.assertEqual(self.host.launch_calls, [])


if __name__ == "__main__":
    unittest.main()

"""Scoped core tests for the experimental menu prototype.

Run from the session root:

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
        -s max2025_menu_prototype/tests -p 'test_core.py' -v
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from max2025_menu_prototype.core.catalog import ACTIONS, build_rows
from max2025_menu_prototype.core.contracts import (
    Availability,
    ContextLevel,
    ContextSnapshot,
    LaunchResult,
    MenuEvent,
    MenuRow,
    MenuView,
)
from max2025_menu_prototype.core.controller import MenuController
from max2025_menu_prototype.core.parameters import requires_parameters
from max2025_menu_prototype.core.profile import (
    ProfileError,
    default_profile,
    dump_profile,
    load_profile,
)
from max2025_menu_prototype.core.simulated import SCENARIO_NAMES, SimulatedHost


def vertex_snapshot(**overrides) -> ContextSnapshot:
    values = dict(
        level=ContextLevel.VERTEX,
        target_kind="editable_poly",
        target_token="poly:base",
        selection_count=1,
        selection_token="sel:vertex:1",
        panel_ready=True,
        conflicting_mode=False,
        revision=1,
    )
    values.update(overrides)
    return ContextSnapshot(**values)


class FakeHost:
    """Records calls; never touches a scene.  Results are deterministic."""

    def __init__(self, snapshot: ContextSnapshot | None = None) -> None:
        self.current = snapshot or vertex_snapshot()
        self.availability_map = {
            action.id: Availability(True) for action in ACTIONS if action.id != "menu.repeat"
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
            action_id, LaunchResult("launched", action_id, "dispatch accepted")
        )


class CoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.host = FakeHost()
        self.ctl = MenuController(self.host, default_profile())
        self.session = ""
        self._sequence = 0

    def open(self, time_ms: float = 1000.0, invocation: str = "latched") -> MenuView:
        view = self.ctl.open((10.0, 10.0), time_ms, invocation)
        self.session = view.session_id
        self._sequence = 0
        return view

    def event(self, kind: str, **kwargs) -> MenuView:
        self._sequence += 1
        kwargs.setdefault("session_id", self.session)
        kwargs.setdefault("sequence", self._sequence)
        kwargs.setdefault("time_ms", 1000.0)
        return self.ctl.handle(MenuEvent(kind=kind, **kwargs))

    def ids(self, view: MenuView) -> list[str]:
        return [row.action_id for row in view.rows]

    def row(self, view: MenuView, action_id: str) -> MenuRow:
        return next(row for row in view.rows if row.action_id == action_id)


class ContextAndOrderTests(CoreTestCase):
    def test_six_contexts_are_distinct(self) -> None:
        self.assertEqual(
            [level.value for level in ContextLevel],
            ["object", "vertex", "edge", "border", "polygon", "element"],
        )
        profile = default_profile()
        self.assertEqual(set(profile.contexts), set(level.value for level in ContextLevel))
        for level in ContextLevel:
            view = MenuController(FakeHost(vertex_snapshot(level=level)), profile).open(
                (0, 0), 0.0, "latched"
            )
            self.assertEqual(view.context, level)

    def test_stable_order_under_capability_changes_and_search_reset(self) -> None:
        first = self.open()
        original = self.ids(first)
        self.assertEqual(original[-1], "menu.repeat")

        self.host.availability_map["transform.move"] = Availability(
            False, "Select one supported object."
        )
        self.event("cancel")
        second = self.open()
        self.assertEqual(self.ids(second), original)
        self.assertFalse(self.row(second, "transform.move").enabled)

        self.event("key_down", key="char", text="grow")
        filtered = self.ctl.view()
        self.assertEqual(self.ids(filtered), ["selection.grow"])
        for _ in range(6):
            self.event("key_down", key="Backspace", text="")
        restored = self.ctl.view()
        self.assertEqual(restored.query, "")
        self.assertEqual(self.ids(restored), original)

    def test_search_uses_labels_and_aliases(self) -> None:
        self.open()
        self.event("key_down", key="", text="translate")
        self.assertEqual(self.ids(self.ctl.view()), ["transform.move"])

    def test_no_results_explains_and_enter_does_nothing(self) -> None:
        self.open()
        self.event("key_down", key="", text="zzz")
        self.assertEqual(self.ctl.view().rows, [])
        result = self.event("key_down", key="Enter")
        self.assertEqual(result.phase, "search")
        self.assertIn("No actions match", result.status)
        self.assertEqual(self.host.launch_calls, [])


class ProfileTests(unittest.TestCase):
    def test_roundtrip_and_deep_copy_semantics(self) -> None:
        profile = default_profile()
        dumped = dump_profile(profile)
        reloaded = load_profile(dumped)
        self.assertEqual(profile, reloaded)

        dumped["contexts"]["object"][0]["visible"] = False
        dumped["tap_ms"] = 500
        self.assertTrue(profile.contexts["object"][0].visible)
        self.assertEqual(profile.tap_ms, 220)

        source = dump_profile(profile)
        loaded = load_profile(source)
        source["contexts"]["object"][0]["visible"] = False
        self.assertTrue(loaded.contexts["object"][0].visible)

        clone = profile.clone()
        clone.tap_ms = 300
        self.assertEqual(profile.tap_ms, 220)

    def test_rejects_malformed_and_unknown(self) -> None:
        base = dump_profile(default_profile())
        mutations = []
        for mutate in (
            lambda d: d.__setitem__("schema_version", 2),
            lambda d: d.__setitem__("tap_ms", 99),
            lambda d: d.__setitem__("neutral_px", 49),
            lambda d: d.__setitem__("name", ""),
            lambda d: d.__setitem__("extra", 1),
            lambda d: d["contexts"].pop("element"),
            lambda d: d["contexts"].__setitem__("extra", []),
            lambda d: d["contexts"]["object"].append(
                {"id": "does.not.exist", "visible": True}
            ),
            lambda d: d["contexts"]["object"].append(
                {"id": "transform.move", "visible": True}
            ),
            lambda d: d["contexts"]["object"][0].__setitem__("visible", "yes"),
            lambda d: d["contexts"]["object"][0].__setitem__("script", "evil"),
            lambda d: d["contexts"]["object"].append(
                {"id": "model.extrude", "visible": True}
            ),
        ):
            data = json.loads(json.dumps(base))
            mutate(data)
            mutations.append(data)

        for data in mutations:
            with self.assertRaises(ProfileError):
                load_profile(data)

    def test_default_profile_loads_relative_to_module(self) -> None:
        old_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                self.assertEqual(default_profile().tap_ms, 220)
            finally:
                os.chdir(old_cwd)

    def test_gesture_bounds_and_editing_helpers(self) -> None:
        profile = default_profile()
        profile.set_gesture(tap_ms=300, neutral_px=20)
        self.assertEqual((profile.tap_ms, profile.neutral_px), (300, 20))
        with self.assertRaises(ProfileError):
            profile.set_gesture(tap_ms=601)
        with self.assertRaises(ProfileError):
            profile.set_gesture(neutral_px=3)

        profile.set_visible("vertex", "model.chamfer", False)
        self.assertFalse(profile.entries_for("vertex")[5].visible)
        profile.move_action("vertex", "model.chamfer", 0)
        self.assertEqual(profile.entries_for("vertex")[0].id, "model.chamfer")
        with self.assertRaises(ProfileError):
            profile.move_action("vertex", "nope", 0)


class GestureTests(CoreTestCase):
    def test_tap_boundaries_below_at_and_above_threshold(self) -> None:
        # Below threshold: the menu latches in place; nothing is armed or run.
        self.open(1000.0, "held")
        tapped = self.event("trigger_up", time_ms=1219.0, x=100, y=100)
        self.assertEqual(tapped.phase, "latched")
        self.assertIsNone(tapped.armed_id)
        self.assertIsNone(tapped.highlighted_id)
        self.assertIn("latched", tapped.status)
        self.assertEqual(self.host.launch_calls, [])

        # The latched tap still requires an explicit click or Enter.
        committed = self.event("click_row", action_id="transform.move")
        self.assertEqual(committed.phase, "closed")
        self.assertEqual([call[0] for call in self.host.launch_calls], ["transform.move"])

        # At the threshold a deliberately hovered row can commit on release.
        self.open(2000.0, "held")
        self.event("pointer_move", x=100, y=100, action_id="transform.move")
        view = self.event("trigger_up", time_ms=2220.0, x=100, y=100)
        self.assertEqual(view.phase, "closed")
        self.assertEqual(
            [call[0] for call in self.host.launch_calls],
            ["transform.move", "transform.move"],
        )

        # Above the threshold behaves like a hold release.
        self.open(3000.0, "held")
        self.event("pointer_move", x=100, y=100, action_id="transform.rotate")
        self.event("trigger_up", time_ms=3300.0, x=100, y=100)
        self.assertEqual(
            [call[0] for call in self.host.launch_calls],
            ["transform.move", "transform.move", "transform.rotate"],
        )

    def test_tap_latch_disarms_a_hover_and_ignores_later_release(self) -> None:
        self.open(1000.0, "held")
        self.event("pointer_move", x=100, y=100, action_id="transform.move")
        self.assertEqual(self.ctl.view().armed_id, "transform.move")
        tapped = self.event("trigger_up", time_ms=1100.0, x=100, y=100)
        self.assertEqual(tapped.phase, "latched")
        self.assertIsNone(tapped.armed_id)
        late = self.event("trigger_up", time_ms=1600.0, x=100, y=100)
        self.assertEqual(late.phase, "latched")
        self.assertEqual(self.host.launch_calls, [])
        self.event("click_row", action_id="transform.rotate")
        self.assertEqual([call[0] for call in self.host.launch_calls], ["transform.rotate"])

    def test_tap_latches_even_inside_the_neutral_zone(self) -> None:
        self.open(1000.0, "held")
        tapped = self.event("trigger_up", time_ms=1050.0, x=0, y=0)
        self.assertEqual(tapped.phase, "latched")
        self.assertIn("latched", tapped.status)
        self.assertEqual(self.host.launch_calls, [])

    def test_hover_arms_and_neutral_return_cancels(self) -> None:
        self.open(1000.0, "held")
        self.event("pointer_move", x=100, y=100, action_id="transform.move")
        self.assertEqual(self.ctl.view().armed_id, "transform.move")
        self.event("pointer_move", x=1, y=1, action_id=None)
        self.assertIsNone(self.ctl.view().armed_id)
        view = self.event("trigger_up", time_ms=1500.0, x=100, y=100)
        self.assertEqual(view.phase, "closed")
        self.assertEqual(self.host.launch_calls, [])

    def test_release_coordinates_in_neutral_zone_cancel(self) -> None:
        self.open(1000.0, "held")
        self.event("pointer_move", x=100, y=100, action_id="transform.move")
        view = self.event("trigger_up", time_ms=1500.0, x=2, y=2)
        self.assertEqual(view.phase, "closed")
        self.assertEqual(self.host.launch_calls, [])

    def test_placement_does_not_arm_an_action(self) -> None:
        view = self.open(1000.0, "held")
        self.assertIsNone(view.armed_id)
        self.assertIsNone(view.highlighted_id)
        view = self.event("trigger_up", time_ms=1500.0, x=100, y=100)
        self.assertEqual(view.phase, "closed")
        self.assertEqual(self.host.launch_calls, [])

    def test_autorepeat_and_unrelated_input_are_ignored(self) -> None:
        self.open(1000.0, "held")
        before = self.ctl.view()
        after_down = self.event("trigger_down", auto_repeat=True)
        self.assertEqual(after_down.phase, "held")
        after_up = self.event("trigger_up", auto_repeat=True, time_ms=1500.0)
        self.assertEqual(after_up.phase, "held")
        self.event("key_down", key="F13")
        self.assertEqual(self.ctl.view().query, "")
        self.assertEqual(self.host.launch_calls, [])
        self.assertEqual(before.session_id, after_up.session_id)

    def test_typing_during_hold_disarms_trigger_release(self) -> None:
        self.open(1000.0, "held")
        self.event("pointer_move", x=100, y=100, action_id="transform.move")
        typed = self.event("key_down", key="", text="g")
        self.assertEqual(typed.phase, "search")
        self.assertIsNone(typed.armed_id)
        released = self.event("trigger_up", time_ms=1500.0, x=100, y=100)
        self.assertEqual(released.phase, "search")
        self.assertEqual(self.host.launch_calls, [])
        escaped = self.event("key_down", key="Escape")
        self.assertEqual(escaped.phase, "closed")


class CancellationTests(CoreTestCase):
    def test_escape_outside_and_focus_loss_cancel(self) -> None:
        for kind, kwargs in (
            ("key_down", {"key": "Escape"}),
            ("outside_click", {}),
            ("focus_lost", {}),
            ("cancel", {}),
        ):
            self.setUp()
            self.open()
            view = self.event(kind, **kwargs)
            self.assertEqual(view.phase, "closed")
            self.assertEqual(view.rows, [])
            self.assertEqual(self.host.launch_calls, [])

    def test_second_invocation_cancels_without_reopening(self) -> None:
        first = self.open()
        old_session = first.session_id
        second = self.ctl.open((20.0, 20.0), 1100.0, "held")
        self.assertEqual(second.phase, "closed")
        self.assertEqual(second.session_id, old_session)
        self.assertEqual(second.rows, [])

        third = self.ctl.open((30.0, 30.0), 1200.0, "latched")
        self.assertEqual(third.phase, "latched")
        self.assertNotEqual(third.session_id, old_session)

    def test_stale_session_and_duplicate_sequence_are_rejected(self) -> None:
        self.open()
        self.event("pointer_move", x=100, y=100, action_id="transform.move")
        stale = self.ctl.handle(
            MenuEvent(
                session_id="other",
                sequence=99,
                kind="click_row",
                action_id="transform.rotate",
            )
        )
        self.assertEqual(stale.phase, "latched")
        duplicate = self.ctl.handle(
            MenuEvent(
                session_id=self.session,
                sequence=1,
                kind="click_row",
                action_id="transform.rotate",
            )
        )
        self.assertEqual(duplicate.phase, "latched")
        self.assertEqual(self.ctl.view().highlighted_id, "transform.move")
        self.assertEqual(self.host.launch_calls, [])

    def test_no_double_dispatch_after_commit(self) -> None:
        self.open()
        first = self.event("click_row", action_id="transform.move")
        self.assertEqual(first.phase, "closed")
        self.event("click_row", action_id="transform.rotate")
        self.event("trigger_up", time_ms=1500.0, x=100, y=100)
        self.assertEqual([call[0] for call in self.host.launch_calls], ["transform.move"])

    def test_second_session_rejects_late_events(self) -> None:
        first = self.open()
        self.event("pointer_move", x=100, y=100, action_id="transform.move")
        self.ctl.open((0, 0), 1100.0, "latched")  # cancels first
        second = self.ctl.open((0, 0), 1200.0, "latched")
        late = self.ctl.handle(
            MenuEvent(
                session_id=first.session_id,
                sequence=50,
                kind="click_row",
                action_id="transform.move",
            )
        )
        self.assertEqual(late.session_id, second.session_id)
        self.assertEqual(self.host.launch_calls, [])


class RevalidationAndRepeatTests(CoreTestCase):
    def test_stale_selection_with_equal_count_is_blocked(self) -> None:
        self.open(1000.0, "held")
        self.event("pointer_move", x=100, y=100, action_id="selection.grow")
        self.host.current = vertex_snapshot(
            selection_token="sel:vertex:different", revision=2
        )
        view = self.event("trigger_up", time_ms=1500.0, x=100, y=100)
        self.assertEqual(view.phase, "closed")
        self.assertEqual(view.last_result.status, "blocked")
        self.assertIn("changed", view.status)
        self.assertEqual(self.host.launch_calls, [])

    def test_revision_or_level_change_is_blocked(self) -> None:
        for changed in (
            vertex_snapshot(revision=5),
            ContextSnapshot(
                ContextLevel.EDGE,
                "editable_poly",
                "poly:base",
                1,
                "sel:edge:1",
                True,
                False,
                1,
            ),
        ):
            self.setUp()
            self.open()
            self.host.current = changed
            self.event("click_row", action_id="selection.grow")
            view = self.ctl.view()
            self.assertEqual(view.last_result.status, "blocked")
            self.assertEqual(self.host.launch_calls, [])

    def test_successful_repeat_relaunches_current_context(self) -> None:
        self.open()
        self.event("click_row", action_id="selection.grow")
        self.assertEqual(self.host.launch_calls[-1][0], "selection.grow")

        view = self.open()
        repeat = self.row(view, "menu.repeat")
        self.assertTrue(repeat.enabled)
        self.assertEqual(repeat.label, "Repeat: relaunch Grow selection")
        result = self.event("click_row", action_id="menu.repeat")
        self.assertEqual(result.phase, "closed")
        self.assertEqual(self.host.launch_calls[-1][0], "selection.grow")
        self.assertEqual(result.last_result.status, "launched")

    def test_repeat_disabled_when_context_does_not_allow_action(self) -> None:
        self.open()
        self.event("click_row", action_id="selection.grow")
        self.host.current = ContextSnapshot(
            ContextLevel.OBJECT,
            "editable_poly",
            "poly:base",
            1,
            "sel:object:1",
            True,
            False,
            2,
        )
        view = self.open()
        repeat = self.row(view, "menu.repeat")
        self.assertFalse(repeat.enabled)
        self.assertIn("not available", repeat.reason)
        result = self.event("click_row", action_id="menu.repeat")
        self.assertEqual(result.phase, "latched")
        self.assertEqual(len(self.host.launch_calls), 1)

    def test_failed_launch_does_not_overwrite_repeat_record(self) -> None:
        self.host.results["selection.shrink"] = LaunchResult(
            "error", "selection.shrink", "native dispatch failed"
        )
        self.open()
        self.event("click_row", action_id="selection.grow")
        view = self.open()
        self.event("click_row", action_id="selection.shrink")
        failed = self.ctl.view()
        self.assertEqual(failed.last_result.status, "error")

        view = self.open()
        self.assertEqual(
            self.row(view, "menu.repeat").label, "Repeat: relaunch Grow selection"
        )
        self.event("click_row", action_id="menu.repeat")
        self.assertEqual([call[0] for call in self.host.launch_calls][-1], "selection.grow")

    def test_blocked_launch_does_not_overwrite_repeat_record(self) -> None:
        self.host.results["selection.shrink"] = LaunchResult(
            "blocked", "selection.shrink", "host rejected"
        )
        self.open()
        self.event("click_row", action_id="selection.grow")
        self.open()
        self.event("click_row", action_id="selection.shrink")
        view = self.open()
        self.assertEqual(
            self.row(view, "menu.repeat").label, "Repeat: relaunch Grow selection"
        )

    def test_availability_change_keeps_menu_open_with_reason(self) -> None:
        view = self.open()
        self.assertTrue(self.row(view, "transform.move").enabled)
        self.host.availability_map["transform.move"] = Availability(
            False, "Target became unsupported."
        )
        result = self.event("click_row", action_id="transform.move")
        self.assertEqual(result.phase, "latched")
        self.assertFalse(self.row(result, "transform.move").enabled)
        self.assertEqual(result.status, "Target became unsupported.")
        self.assertEqual(self.host.launch_calls, [])


class KeyboardTests(CoreTestCase):
    def test_traversal_includes_disabled_rows_and_enter_blocks(self) -> None:
        self.host.availability_map["transform.move"] = Availability(
            False, "Select one supported object."
        )
        view = self.open()
        self.assertFalse(self.row(view, "transform.move").enabled)

        first = self.event("key_down", key="ArrowDown")
        self.assertEqual(first.highlighted_id, "transform.move")
        self.assertEqual(first.status, "Select one supported object.")
        blocked = self.event("key_down", key="Enter")
        self.assertEqual(blocked.phase, "latched")
        self.assertEqual(blocked.last_result, None)
        self.assertEqual(self.host.launch_calls, [])

        second = self.event("key_down", key="ArrowDown")
        self.assertEqual(second.highlighted_id, "transform.rotate")
        committed = self.event("key_down", key="Enter")
        self.assertEqual(committed.phase, "closed")
        self.assertEqual([call[0] for call in self.host.launch_calls], ["transform.rotate"])

    def test_arrow_up_wraps_to_last_row_and_home_end(self) -> None:
        self.open()
        up = self.event("key_down", key="ArrowUp")
        self.assertEqual(up.highlighted_id, "menu.repeat")
        home = self.event("key_down", key="Home")
        self.assertEqual(home.highlighted_id, "transform.move")
        end = self.event("key_down", key="End")
        self.assertEqual(end.highlighted_id, "menu.repeat")

    def test_enter_without_highlight_does_nothing(self) -> None:
        self.open()
        view = self.event("key_down", key="Enter")
        self.assertEqual(view.phase, "latched")
        self.assertIn("Highlight a row", view.status)
        self.assertEqual(self.host.launch_calls, [])


class HostInteractionTests(CoreTestCase):
    def test_open_and_hover_do_not_launch_or_mutate(self) -> None:
        self.open(1000.0, "held")
        self.event("pointer_move", x=100, y=100, action_id="transform.move")
        self.event("pointer_move", x=200, y=200, action_id="transform.rotate")
        self.assertEqual(self.host.launch_calls, [])
        self.assertEqual(self.host.snapshot_calls, 1)
        self.assertEqual(self.host.availability_calls, 1)

    def test_pre_dispatch_hook_runs_before_host_launch(self) -> None:
        order: list[tuple[str, str, str]] = []
        self.ctl.set_pre_dispatch(
            lambda action_id: order.append(("pre", action_id, self.ctl.view().phase))
        )
        original_launch = self.host.launch

        def launch(action_id, expected):
            order.append(("launch", action_id, self.ctl.view().phase))
            return original_launch(action_id, expected)

        self.host.launch = launch
        self.open()
        self.event("click_row", action_id="transform.move")
        self.assertEqual(
            order,
            [
                ("pre", "transform.move", "latched"),
                ("launch", "transform.move", "latched"),
            ],
        )

    def test_pre_dispatch_hook_skips_blocked_commits(self) -> None:
        calls: list[str] = []
        self.ctl.set_pre_dispatch(calls.append)
        self.open()
        self.host.availability_map["transform.move"] = Availability(
            False, "Target became unsupported."
        )
        self.event("click_row", action_id="transform.move")
        self.assertEqual(calls, [])
        self.assertEqual(self.host.launch_calls, [])

        self.host.current = vertex_snapshot(revision=9)
        self.event("click_row", action_id="transform.rotate")
        self.assertEqual(calls, [])
        self.assertEqual(self.host.launch_calls, [])

    def test_pre_dispatch_failure_blocks_dispatch(self) -> None:
        def cannot_close(action_id: str) -> None:
            raise RuntimeError("cannot close popup")

        self.ctl.set_pre_dispatch(cannot_close)
        self.open()
        view = self.event("click_row", action_id="transform.move")
        self.assertEqual(view.phase, "closed")
        self.assertEqual(view.last_result.status, "error")
        self.assertIn("could not close", view.status)
        self.assertEqual(self.host.launch_calls, [])

        self.ctl.clear_pre_dispatch(cannot_close)
        self.open()
        self.event("click_row", action_id="transform.move")
        self.assertEqual([call[0] for call in self.host.launch_calls], ["transform.move"])

    def test_disabled_availability_requires_reason(self) -> None:
        with self.assertRaises(ValueError):
            Availability(False, "")
        Availability(False, "why")
        Availability(True, "")

    def test_set_profile_while_open_cancels(self) -> None:
        self.open()
        updated = default_profile()
        updated.tap_ms = 300
        view = self.ctl.set_profile(updated)
        self.assertEqual(view.phase, "closed")
        self.assertEqual(self.host.launch_calls, [])

        self.open(1000.0, "held")
        self.event("pointer_move", x=100, y=100, action_id="transform.move")
        view = self.event("trigger_up", time_ms=1250.0, x=100, y=100)
        # 250 ms is below the replaced 300 ms threshold, so it latches.
        self.assertEqual(view.phase, "latched")
        self.assertIsNone(view.armed_id)
        self.assertEqual(self.host.launch_calls, [])

    def test_log_is_bounded_and_has_required_fields(self) -> None:
        for _ in range(30):
            self.open()
            self.event("cancel")
        view = self.ctl.view()
        self.assertLessEqual(len(view.log), 50)
        for entry in view.log:
            for key in ("session", "context", "action", "result"):
                self.assertIn(key, entry)
        self.assertIn("cancelled", {entry["result"] for entry in view.log})

    def test_view_is_json_compatible(self) -> None:
        view = self.open()
        payload = json.loads(json.dumps(view.to_dict()))
        self.assertEqual(payload["phase"], "latched")
        self.assertEqual(payload["context"], "vertex")
        self.assertEqual(payload["rows"][0]["action_id"], "transform.move")
        self.assertIn("session_id", payload)
        self.assertEqual(MenuView(phase="closed").phase, "closed")

    def test_build_rows_ignores_hidden_entries_without_reordering(self) -> None:
        profile = default_profile()
        profile.set_visible("vertex", "transform.rotate", False)
        rows = build_rows(profile, ContextLevel.VERTEX, {}, None, "")
        self.assertNotIn("transform.rotate", [row.action_id for row in rows])
        self.assertEqual(rows[0].action_id, "transform.move")
        self.assertEqual(rows[1].action_id, "transform.scale")
        self.assertFalse(rows[-1].enabled)
        self.assertIn("No repeatable", rows[-1].reason)


class SimulatedHostTests(unittest.TestCase):
    def test_scenarios_are_deterministic_and_honest(self) -> None:
        for name in SCENARIO_NAMES:
            host = SimulatedHost(name)
            snapshot = host.snapshot()
            self.assertEqual(host.snapshot(), snapshot)
            availability = host.availability(snapshot)
            self.assertNotIn("menu.repeat", availability)
            for value in availability.values():
                self.assertIsInstance(value, Availability)
                if not value.enabled:
                    self.assertTrue(value.reason.strip())

    def test_level_scenarios_enable_matching_subobject_actions(self) -> None:
        for level in ContextLevel:
            host = SimulatedHost(f"editable_poly_{level.value}")
            availability = host.availability(host.snapshot())
            for action_id, value in availability.items():
                action = next(spec for spec in ACTIONS if spec.id == action_id)
                if not action_id.startswith(("selection.", "model.")):
                    continue
                if level not in action.allowed_levels:
                    continue
                if requires_parameters(action_id, level):
                    self.assertFalse(value.enabled, (level, action_id))
                    self.assertIn("parameter dialog", value.reason)
                elif action_id != "model.cap":
                    self.assertTrue(value.enabled, (level, action_id))

        empty = SimulatedHost("editable_poly_vertex_empty")
        self.assertEqual(
            empty.availability(empty.snapshot())["selection.grow"].reason,
            "Select vertices.",
        )
        primitive = SimulatedHost("editable_poly_vertex")
        primitive.set_context(target_kind="other", target_token="mesh:primitive")
        self.assertIn(
            "Edit Poly",
            primitive.availability(primitive.snapshot())["selection.grow"].reason,
        )
        multiple = SimulatedHost("editable_poly_edge")
        multiple.set_context(target_kind="multiple", selection_count=3)
        self.assertIn(
            "Multiple objects",
            multiple.availability(multiple.snapshot())["selection.grow"].reason,
        )
        conflicting = SimulatedHost("conflicting_mode")
        self.assertIn(
            "conflicting command mode",
            conflicting.availability(conflicting.snapshot())["selection.grow"].reason,
        )
        deleted = SimulatedHost("deleted_target")
        self.assertEqual(
            deleted.availability(deleted.snapshot())["transform.move"].reason,
            "Select one supported object.",
        )

    def test_simulated_launch_labels_and_logs(self) -> None:
        host = SimulatedHost("editable_poly_vertex")
        ctl = MenuController(host, default_profile())
        view = ctl.open((0, 0), 0.0, "latched")
        session = view.session_id
        result = ctl.handle(
            MenuEvent(
                session_id=session,
                sequence=1,
                kind="click_row",
                action_id="selection.grow",
            )
        )
        self.assertEqual(result.phase, "closed")
        self.assertEqual(result.last_result.status, "simulated")
        self.assertIn("SIMULATED", result.status)
        log = host.launch_log
        self.assertEqual(log[-1]["action_id"], "selection.grow")
        self.assertEqual(log[-1]["status"], "simulated")
        self.assertFalse(hasattr(host, "mesh"))

    def test_simulated_revalidation_after_context_mutation(self) -> None:
        host = SimulatedHost("editable_poly_vertex")
        ctl = MenuController(host, default_profile())
        view = ctl.open((0, 0), 0.0, "latched")
        session = view.session_id
        host.set_context(selection_token="sel:vertex:other", selection_count=1)
        result = ctl.handle(
            MenuEvent(
                session_id=session,
                sequence=1,
                kind="click_row",
                action_id="selection.grow",
            )
        )
        self.assertEqual(result.last_result.status, "blocked")
        self.assertEqual(host.launch_log, [])

    def test_simulated_context_change_while_open_blocks_repeat(self) -> None:
        host = SimulatedHost("editable_poly_vertex")
        ctl = MenuController(host, default_profile())
        ctl.open((0, 0), 0.0, "latched")
        ctl.handle(
            MenuEvent(
                session_id=ctl.view().session_id,
                sequence=1,
                kind="click_row",
                action_id="selection.grow",
            )
        )
        view = ctl.open((0, 0), 10.0, "latched")
        self.assertEqual(ctl.view().phase, "latched")
        host.set_scenario("editable_poly_edge")
        result = ctl.handle(
            MenuEvent(
                session_id=view.session_id,
                sequence=1,
                kind="click_row",
                action_id="menu.repeat",
            )
        )
        self.assertEqual(result.last_result.status, "blocked")


if __name__ == "__main__":
    unittest.main()

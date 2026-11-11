"""End-to-end V2 composition tests for bootstrap, controller, host and palette.

These are host-free fakes/Qt optional.  They prove the integration seam that
streams A (controller), B (host) and C (palette) share: one controller, one
palette, fixed system callbacks, context macros without UI, and UI-only
teardown that preserves persistent state.
"""

from __future__ import annotations

import sys
import types
import unittest
from types import SimpleNamespace
from unittest import mock

from max2025_menu_prototype.core.atlas import load_atlas
from max2025_menu_prototype.core.contracts import LaunchResult
from max2025_menu_prototype.core.maneuver_contracts import (
    ManeuverContext,
    SystemCommand,
)
from max2025_menu_prototype.core.maneuver_simulated import SimulatedManeuverHost
from max2025_menu_prototype.core.palette_controller import PaletteController
from max2025_menu_prototype.max_host import bootstrap
from max2025_menu_prototype.max_host.adapter import ManeuverHostAdapter

try:
    from . import checkout_support  # noqa: F401  (shared subprocess helper)
except ImportError:  # discovered as a top-level test module
    import checkout_support  # noqa: F401

CHECKOUT_ROOT = checkout_support.CHECKOUT_ROOT
ATLAS = load_atlas(CHECKOUT_ROOT).atlas


class FakeApplication:
    @staticmethod
    def instance():
        return FakeApplication()


class FakeQtWidgets:
    QApplication = FakeApplication


class FakeQtGui:
    class QCursor:
        @staticmethod
        def pos():
            return "cursor-position"


def fake_qt_parts():
    return (object(), FakeQtGui, FakeQtWidgets)


class FakeV2Controller:
    def __init__(self) -> None:
        self.opened = 0
        self.disposed = 0
        self.switches: list = []
        self.pre_dispatch: list = []
        self._disposed = False
        self.view_state = SimpleNamespace(
            session_id="v2-session",
            generation=1,
            status="Ready.",
            busy=False,
            rows=(),
            last_result=None,
        )

    def open(self):
        self.opened += 1
        return self.view_state

    def view(self):
        return self.view_state

    def set_query(self, text):
        return self.view_state

    def activate(self, row_id, *, session_id, generation):
        return self.view_state

    def switch_context(self, context, *, convert_selection=False):
        self.switches.append((context, convert_selection))
        return self.view_state

    def cancel(self, reason=""):
        return self.view_state

    def dispose(self):
        self.disposed += 1
        self._disposed = True
        return self.view_state

    def set_pre_dispatch(self, callback):
        if self.pre_dispatch and self.pre_dispatch[-1] is callback:
            return
        self.pre_dispatch.append(callback)

    def clear_pre_dispatch(self, callback=None):
        if callback is None or (self.pre_dispatch and self.pre_dispatch[-1] is callback):
            if self.pre_dispatch:
                self.pre_dispatch.pop()


class FakePalette:
    instances: list = []

    def __init__(self, controller, parent=None) -> None:
        self.controller = controller
        self.parent = parent
        self.shows: list = []
        self.refreshes = 0
        self.disposed = 0
        self._open = False
        self._disposed = False
        self._hook = lambda *args, **kwargs: None
        setter = getattr(controller, "set_pre_dispatch", None)
        if callable(setter):
            setter(self._hook)
        FakePalette.instances.append(self)

    def show_at(self, global_pos=None, *, return_focus=None) -> None:
        self.shows.append(global_pos)
        self._open = True
        return self.controller.open()

    def refresh(self) -> None:
        self.refreshes += 1

    def dispose(self) -> None:
        self.disposed += 1
        self._open = False
        self._disposed = True
        clearer = getattr(self.controller, "clear_pre_dispatch", None)
        if callable(clearer):
            clearer(self._hook)


class RecordingHost(SimulatedManeuverHost):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.order: list = []

    def execute(self, request):
        self.order.append("execute")
        return super().execute(request)


class V2IntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        bootstrap.uninstall()
        bootstrap._palette_host = None
        bootstrap._palette_controller = None
        bootstrap._controller = None
        FakePalette.instances = []
        self.patches = [
            mock.patch.object(bootstrap, "_import_palette", lambda: FakePalette),
            mock.patch.object(bootstrap, "_qt_parts", fake_qt_parts),
            mock.patch.object(bootstrap, "_max_main_window", lambda: object()),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self) -> None:
        try:
            bootstrap.uninstall()
            bootstrap._palette_host = None
            bootstrap._palette_controller = None
            bootstrap._controller = None
        finally:
            for patch in self.patches:
                patch.stop()

    def make_controller(self, host=None, sim_kwargs=None):
        host = host or SimulatedManeuverHost(ATLAS, **(sim_kwargs or {}))
        controller = bootstrap._create_palette_controller(host=host, atlas=ATLAS)
        return controller, host

    # ------------------------------------------------------------ composition

    def test_default_show_palette_composes_real_v2_controller_and_adapter(self) -> None:
        palette = bootstrap.show_palette()

        self.assertIsInstance(bootstrap._palette_controller, PaletteController)
        self.assertIsInstance(bootstrap._palette_host, ManeuverHostAdapter)
        self.assertIs(palette.controller, bootstrap._palette_controller)
        self.assertEqual(len(FakePalette.instances), 1)
        self.assertIs(bootstrap._palette, palette)

    def test_all_legacy_entry_points_route_to_one_palette_with_one_hook(self) -> None:
        controller = FakeV2Controller()

        tools = bootstrap.show_tools(controller)
        menu = bootstrap.show_menu(controller)
        launcher = bootstrap.install_launcher(controller)

        self.assertIs(tools, menu)
        self.assertIs(menu, launcher)
        self.assertEqual(len(FakePalette.instances), 1)
        self.assertEqual(controller.opened, 3)
        self.assertEqual(len(controller.pre_dispatch), 1)
        self.assertIs(bootstrap._launcher, launcher)

    def test_v1_panel_and_parameter_dialog_are_retired_on_v2_launch(self) -> None:
        legacy = SimpleNamespace(
            disposed=0, dispose=lambda: setattr(legacy, "disposed", 1)
        )
        prompt_calls: list = []

        class LegacyController:
            def clear_parameter_prompt(self, callback=None):
                prompt_calls.append(callback)

        bootstrap._panel = legacy
        bootstrap._panel_controller = object()
        bootstrap._controller = LegacyController()
        bootstrap._prompt_callback = "callback"
        bootstrap._prompt_controller = LegacyController()

        controller = FakeV2Controller()
        bootstrap.show_palette(controller)

        self.assertEqual(legacy.disposed, 1)
        self.assertIsNone(bootstrap._panel)
        self.assertIsNone(bootstrap._prompt_callback)
        self.assertIsNone(bootstrap._prompt_controller)
        self.assertEqual(len(FakePalette.instances), 1)

    # --------------------------------------------------------- system commands

    def test_system_callbacks_work_with_zero_selection_and_no_palette(self) -> None:
        controller, _host = self.make_controller(
            sim_kwargs={"context": "object", "selection_count": 0}
        )
        callbacks = bootstrap._system_callbacks()

        seen: list = []
        fake_qtcore = SimpleNamespace(
            QTimer=SimpleNamespace(singleShot=lambda delay, callback: callback())
        )
        with mock.patch.object(
            bootstrap, "report_diagnostics", lambda: seen.append("diag") or "REPORT"
        ), mock.patch.object(
            bootstrap, "uninstall", lambda: seen.append("unload")
        ), mock.patch(
            "max2025_menu_prototype.tools.max_pull_and_load.start",
            lambda **kwargs: seen.append(("pull", kwargs)),
        ), mock.patch.dict(
            sys.modules,
            {"PySide6": SimpleNamespace(QtCore=fake_qtcore)},
        ):
            self.assertIn("Diagnostics", callbacks[SystemCommand.DIAGNOSTICS]())
            pull = callbacks[SystemCommand.PULL_UPDATES]()
            unload = callbacks[SystemCommand.UNLOAD_UI]()

        self.assertEqual(seen[0], "diag")
        self.assertEqual(seen[1][0], "pull")
        self.assertIsInstance(pull, LaunchResult)
        self.assertEqual(pull.status, "applied")
        self.assertIn("unload", seen)
        self.assertEqual(unload.status, "applied")

    def test_system_rows_dispatch_through_controller_with_no_eligible_node(self) -> None:
        host = SimulatedManeuverHost(ATLAS, context="object", selection_count=0)
        calls: list = []
        controller = PaletteController(
            ATLAS,
            host,
            {
                SystemCommand.DIAGNOSTICS: lambda: calls.append("diag") or "cached",
                SystemCommand.PULL_UPDATES: lambda: calls.append("pull"),
                SystemCommand.UNLOAD_UI: lambda: calls.append("unload") or "done",
            },
        )
        view = controller.open()
        view = controller.set_query(">")
        row = next(r for r in view.rows if r.system_command is SystemCommand.DIAGNOSTICS)
        view = controller.activate(
            row.row_id, session_id=view.session_id, generation=view.generation
        )
        self.assertEqual(calls, ["diag"])
        self.assertEqual(view.last_result.status, "applied")

    # --------------------------------------------------------- context macros

    def test_context_macro_switch_works_without_opening_palette(self) -> None:
        controller, host = self.make_controller(
            sim_kwargs={"context": "object", "selection_count": 1}
        )
        with mock.patch.object(
            bootstrap, "_create_palette_controller", lambda **kwargs: controller
        ):
            view = bootstrap.switch_context("edge", convert_selection=True)

        self.assertIsNone(bootstrap._palette)
        self.assertEqual(host.snapshot().context, ManeuverContext.EDGE)
        self.assertEqual(controller._last_result.status, "simulated")
        self.assertIsNotNone(view)

    def test_context_macro_propagates_native_launch_status(self) -> None:
        class FakeMaxSwitchHost(SimulatedManeuverHost):
            """A Max-shaped host whose switch reports native success."""

            def __init__(self, *args, **kwargs) -> None:
                super().__init__(*args, **kwargs)
                self.switch_calls: list = []

            def switch_context(self, context, *, expected_snapshot, convert_selection=False):
                self.switch_calls.append((context, convert_selection))
                return LaunchResult("applied", "context:%s" % context.value, "Native level set.")

        host = FakeMaxSwitchHost(ATLAS, context="object", selection_count=1)
        controller = bootstrap._create_palette_controller(host=host, atlas=ATLAS)
        with mock.patch.object(
            bootstrap, "_create_palette_controller", lambda **kwargs: controller
        ):
            view = bootstrap.switch_context("border", convert_selection=True)

        self.assertEqual(host.switch_calls, [(ManeuverContext.BORDER, True)])
        self.assertEqual(controller._last_result.status, "applied")
        self.assertEqual(view.last_result.status, "applied")

    def test_context_macro_rejects_polygon_and_unknown_values(self) -> None:
        with self.assertRaises(ValueError):
            bootstrap.switch_context("polygon")
        with self.assertRaises(ValueError):
            bootstrap.switch_context("nurbs")
        with self.assertRaises(TypeError):
            bootstrap.switch_context(4)

    def test_open_palette_refreshes_after_external_context_switch(self) -> None:
        controller, _host = self.make_controller(
            sim_kwargs={"context": "object", "selection_count": 1}
        )
        palette = FakePalette(controller)
        bootstrap._palette = palette
        bootstrap._palette_controller = controller
        bootstrap._owns_palette_controller = True
        palette._open = True

        with mock.patch.object(
            bootstrap, "_create_palette_controller", lambda **kwargs: controller
        ):
            bootstrap.switch_context("face")

        self.assertEqual(palette.refreshes, 1)

    # -------------------------------------------------------------- teardown

    def test_uninstall_is_idempotent_and_preserves_host_and_macros(self) -> None:
        controller, host = self.make_controller()
        palette = FakePalette(controller)
        bootstrap._palette = palette
        bootstrap._palette_controller = controller
        bootstrap._palette_host = host
        bootstrap._owns_palette_controller = True

        with mock.patch(
            "max2025_menu_prototype.tools.max_pull_and_load.register_macros"
        ) as register:
            bootstrap.uninstall()
            bootstrap.uninstall()

        self.assertEqual(palette.disposed, 1)
        self.assertTrue(controller._disposed)
        self.assertIs(bootstrap._palette_host, host, "lockout host is retained")
        self.assertIsNone(bootstrap._palette)
        self.assertIsNone(bootstrap._palette_controller)
        register.assert_not_called()

    def test_injected_controller_is_not_disposed_and_host_is_retained(self) -> None:
        controller = FakeV2Controller()
        host = object()
        bootstrap._palette_host = host
        bootstrap.show_palette(controller)

        bootstrap.uninstall()

        self.assertEqual(controller.disposed, 0)
        self.assertIs(bootstrap._palette_host, host)

    def test_report_diagnostics_passes_v2_host_and_loaded_sha(self) -> None:
        host = object()
        bootstrap._palette_host = host
        session = types.ModuleType("_max2025_pull_and_load_session")
        session.loaded_head_sha = "a" * 40
        previous = sys.modules.get("_max2025_pull_and_load_session")
        sys.modules["_max2025_pull_and_load_session"] = session
        try:
            with mock.patch(
                "max2025_menu_prototype.max_host.probe.report", return_value="R"
            ) as report:
                self.assertEqual(bootstrap.report_diagnostics(), "R")
        finally:
            if previous is None:
                sys.modules.pop("_max2025_pull_and_load_session", None)
            else:
                sys.modules["_max2025_pull_and_load_session"] = previous

        self.assertIs(report.call_args.kwargs["v2"], host)
        self.assertEqual(report.call_args.kwargs["loaded_sha"], "a" * 40)

    # --------------------------------------------------- dispatch/focus order

    def test_pre_dispatch_runs_before_one_host_dispatch(self) -> None:
        host = RecordingHost(ATLAS)
        controller = PaletteController(ATLAS, host)
        order: list = []
        controller.set_pre_dispatch(lambda *args: order.append("pre"))
        view = controller.open()
        row = next(
            r
            for r in view.rows
            if r.enabled and r.maneuver_id is not None
        )
        view = controller.activate(
            row.row_id, session_id=view.session_id, generation=view.generation
        )
        self.assertEqual(order, ["pre"])
        self.assertEqual(host.order, ["execute"])
        self.assertIn(view.last_result.status, ("launched", "applied", "simulated"))

    def test_mixed_mouse_and_enter_cannot_dispatch_twice(self) -> None:
        host = RecordingHost(ATLAS)
        controller = PaletteController(ATLAS, host)
        view = controller.open()
        row = next(r for r in view.rows if r.enabled and r.maneuver_id is not None)
        first = controller.activate(
            row.row_id, session_id=view.session_id, generation=view.generation
        )
        second = controller.activate(
            row.row_id, session_id=view.session_id, generation=view.generation
        )
        self.assertEqual(len(host.order), 1)
        self.assertEqual(second.rows, ())
        self.assertIsNotNone(first.last_result)


if __name__ == "__main__":
    unittest.main()

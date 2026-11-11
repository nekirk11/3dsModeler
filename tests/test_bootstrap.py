"""Qt-independent bootstrap composition tests.

These use fake controllers, panels, popups, and a fake parameter module.  They
prove ownership, lazy-import, prompt-registration, and teardown ordering only;
they are not 3ds Max or PySide runtime tests.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from max2025_menu_prototype.core.catalog import ACTIONS
from max2025_menu_prototype.core.contracts import (
    Availability,
    ContextLevel,
    ContextSnapshot,
    ControllerSummary,
    LaunchResult,
)
from max2025_menu_prototype.core.controller import MenuController
from max2025_menu_prototype.core.profile import default_profile
from max2025_menu_prototype.max_host import bootstrap, probe

try:
    from . import checkout_support
except ImportError:  # discovered as a top-level test module
    import checkout_support


class FakeSignal:
    def __init__(self) -> None:
        self.slots: list = []

    def connect(self, slot) -> None:
        self.slots.append(slot)

    def emit(self) -> None:
        for slot in list(self.slots):
            slot()


class FakeController:
    def __init__(self) -> None:
        self.prompts: list = []
        self.cleared: list = []
        self.disposed = 0
        self.released = 0
        self.open_calls: list = []
        self.events: list = []
        self.summary_state = SimpleNamespace(
            phase="closed", busy=False, context=None, last_result=None
        )

    def set_parameter_prompt(self, callback) -> None:
        self.prompts.append(callback)

    def clear_parameter_prompt(self, callback=None) -> None:
        self.cleared.append(callback)
        if callback is None or (self.prompts and self.prompts[-1] is callback):
            if self.prompts:
                self.prompts.pop()

    def summary(self):
        return self.summary_state

    def open(self, origin, time_ms, invocation="latched"):
        self.open_calls.append((origin, time_ms, invocation))
        return SimpleNamespace(phase="latched", session_id="session", sequence=4)

    def handle(self, event):
        self.events.append(event)
        return SimpleNamespace(phase="closed")

    def invoke(self, action_id):
        return SimpleNamespace(status=f"invoked {action_id}")

    def dispose(self) -> None:
        self.disposed += 1

    def release_owned_native_mode(self) -> None:
        self.released += 1


class FakePanel:
    instances: list = []

    def __init__(self, controller, parent=None) -> None:
        self.controller = controller
        self.parent = parent
        self.menu_requested = FakeSignal()
        self.refresh_callback = None
        self.show_calls = 0
        self.raise_calls = 0
        self.refresh_calls = 0
        self.disposed = 0
        FakePanel.instances.append(self)

    def set_refresh_callback(self, callback) -> None:
        self.refresh_callback = callback

    def show(self) -> None:
        self.show_calls += 1

    def raise_(self) -> None:
        self.raise_calls += 1

    def activateWindow(self) -> None:
        pass

    def refresh(self) -> None:
        self.refresh_calls += 1
        if self.refresh_callback is not None:
            self.refresh_callback()

    def dispose(self) -> None:
        self.disposed += 1

    def close(self) -> None:
        pass

    def deleteLater(self) -> None:
        pass


class FakePopup:
    instances: list = []

    def __init__(self, controller, parent=None) -> None:
        self.controller = controller
        self.parent = parent
        self.shows: list = []
        self.disposed = 0
        FakePopup.instances.append(self)

    def show_at(self, position, invocation="latched") -> None:
        self.shows.append((position, invocation))

    def dispose(self) -> None:
        self.disposed += 1


class FakeParameters:
    def __init__(self) -> None:
        self.collected: list = []
        self.closed = 0
        self.result = {"height": 2.0, "extrusion_type": "group"}

    def collect_parameters(self, spec, context, schema, parent=None):
        self.collected.append((spec, context, schema, parent))
        return self.result

    def close_pending_dialogs(self) -> None:
        self.closed += 1


class UninstallingParameters(FakeParameters):
    """Simulates bootstrap teardown while a modal collector is pending."""

    def collect_parameters(self, spec, context, schema, parent=None):
        self.collected.append((spec, context, schema, parent))
        bootstrap.uninstall()
        return {"height": 1.0, "extrusion_type": "group"}


class ParameterHost:
    def __init__(self) -> None:
        self.current = ContextSnapshot(
            ContextLevel.POLYGON,
            "editable_poly",
            "poly:base",
            1,
            "sel:polygon:1",
            True,
            False,
            1,
        )
        self.launch_calls: list = []

    def snapshot(self) -> ContextSnapshot:
        return self.current

    def availability(self, context: ContextSnapshot) -> dict[str, Availability]:
        return {
            spec.id: Availability(True)
            for spec in ACTIONS
            if spec.id != "menu.repeat"
        }

    def launch(self, action_id, expected_context, parameters=None):
        self.launch_calls.append((action_id, expected_context, parameters))
        return LaunchResult("applied", action_id, "fake")


class FakeQtGui:
    class QCursor:
        @staticmethod
        def pos():
            return "cursor-position"


class BootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        bootstrap.uninstall()
        FakePanel.instances = []
        FakePopup.instances = []
        self.parameters = FakeParameters()
        self.parent = object()
        self.patches = [
            mock.patch.object(bootstrap, "_import_panel", lambda: FakePanel),
            mock.patch.object(
                bootstrap, "_import_parameters", lambda: self.parameters
            ),
            mock.patch.object(bootstrap, "_max_main_window", lambda: self.parent),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self) -> None:
        try:
            bootstrap.uninstall()
        finally:
            for patch in self.patches:
                patch.stop()

    def test_show_tools_reuses_one_controller_panel_and_prompt(self) -> None:
        controller = FakeController()

        first = bootstrap.show_tools(controller)
        second = bootstrap.show_tools(controller)

        self.assertIs(first, second)
        self.assertIs(first.controller, controller)
        self.assertIs(first.parent, self.parent)
        self.assertEqual(len(controller.prompts), 1)
        self.assertEqual(first.show_calls, 2)
        self.assertEqual(first.raise_calls, 2)
        self.assertIs(bootstrap._controller, controller)
        self.assertIs(bootstrap._panel, first)

    def test_refresh_reads_context_only_through_public_controller_api(self) -> None:
        controller = FakeController()
        panel = bootstrap.show_tools(controller)
        self.assertEqual(len(controller.open_calls), 1)
        self.assertEqual(len(controller.events), 1)
        self.assertEqual(controller.events[0].kind, "cancel")
        self.assertEqual(controller.events[0].session_id, "session")

        panel.refresh()
        self.assertEqual(len(controller.open_calls), 2)

        controller.summary_state.busy = True
        panel.refresh()
        controller.summary_state.busy = False
        controller.summary_state.phase = "latched"
        panel.refresh()
        self.assertEqual(len(controller.open_calls), 2, "active input is never stolen")

    def test_prompt_collects_values_and_cancel_returns_none(self) -> None:
        controller = FakeController()
        bootstrap.show_tools(controller)
        prompt = controller.prompts[0]
        spec = SimpleNamespace(id="model.extrude", label="Extrude")
        context = SimpleNamespace(level=SimpleNamespace(value="polygon"))
        schema = (object(),)

        self.assertEqual(prompt(spec, context, schema), self.parameters.result)
        self.assertIs(self.parameters.collected[0][0], spec)
        self.assertIs(self.parameters.collected[0][3], self.parent)

        self.parameters.result = None
        self.assertIsNone(prompt(spec, context, schema))

    def test_show_menu_shares_controller_and_does_not_reregister_prompt(self) -> None:
        controller = FakeController()
        panel = bootstrap.show_tools(controller)
        with mock.patch.object(bootstrap, "_import_popup", lambda: FakePopup), \
                mock.patch.object(
                    bootstrap,
                    "_qt_parts",
                    lambda: (object(), FakeQtGui, object()),
                ):
            first = bootstrap.show_menu(controller)
            second = bootstrap.show_menu(controller)

        self.assertIs(first, second)
        self.assertEqual(len(FakePopup.instances), 1)
        self.assertEqual(
            first.shows,
            [("cursor-position", "latched"), ("cursor-position", "latched")],
        )
        self.assertEqual(len(controller.prompts), 1)
        self.assertIs(bootstrap._controller, controller)
        self.assertIs(bootstrap._panel, panel)

    def test_install_launcher_is_click_to_open_alias(self) -> None:
        controller = FakeController()
        launcher = bootstrap.install_launcher(controller)
        self.assertIs(launcher, bootstrap.show_tools(controller))
        self.assertIs(bootstrap._launcher, launcher)
        self.assertEqual(len(controller.prompts), 1)
        self.assertEqual(launcher.show_calls, 2)

    def test_uninstall_closes_dialog_clears_our_hook_and_is_idempotent(self) -> None:
        controller = FakeController()
        panel = bootstrap.show_tools(controller)
        callback = controller.prompts[0]

        bootstrap.uninstall()
        bootstrap.uninstall()

        self.assertEqual(self.parameters.closed, 1)
        self.assertIn(callback, controller.cleared)
        self.assertEqual(controller.disposed, 1)
        self.assertEqual(panel.disposed, 1)
        self.assertEqual(controller.released, 1)
        self.assertIsNone(bootstrap._controller)
        self.assertIsNone(bootstrap._panel)

    def test_uninstall_during_prompt_invalidates_invocation(self) -> None:
        self.parameters = UninstallingParameters()
        host = ParameterHost()
        controller = MenuController(host, default_profile())

        bootstrap.show_tools(controller)
        result = controller.invoke("model.extrude")

        self.assertEqual(result.last_result.status, "cancelled")
        self.assertEqual(host.launch_calls, [])
        self.assertFalse(controller.summary().busy)
        self.assertEqual(self.parameters.closed, 1)
        self.assertIsNone(bootstrap._controller)

    def test_uninstall_does_not_release_mode_while_parameters_are_pending(self) -> None:
        controller = FakeController()
        bootstrap.show_tools(controller)
        controller.summary_state.busy = True

        bootstrap.uninstall()

        self.assertEqual(controller.released, 0)
        self.assertEqual(controller.disposed, 1)

    def test_replacing_controller_disposes_old_panel_and_clears_old_hook(self) -> None:
        first_controller = FakeController()
        second_controller = FakeController()
        first_panel = bootstrap.show_tools(first_controller)
        old_prompt = first_controller.prompts[0]
        bootstrap.show_tools(second_controller)

        self.assertEqual(first_panel.disposed, 1)
        self.assertIn(old_prompt, first_controller.cleared)
        self.assertEqual(len(second_controller.prompts), 1)
        self.assertIs(bootstrap._controller, second_controller)

    def test_panel_menu_signal_routes_to_shared_latched_menu(self) -> None:
        controller = FakeController()
        panel = bootstrap.show_tools(controller)
        with mock.patch.object(bootstrap, "_import_popup", lambda: FakePopup), \
                mock.patch.object(
                    bootstrap,
                    "_qt_parts",
                    lambda: (object(), FakeQtGui, object()),
                ):
            panel.menu_requested.emit()

        self.assertEqual(len(FakePopup.instances), 1)
        self.assertEqual(FakePopup.instances[0].shows[0][1], "latched")

    def test_missing_qt_dialog_dependency_is_a_clear_error(self) -> None:
        controller = FakeController()
        with mock.patch.object(
            bootstrap,
            "_import_parameters",
            side_effect=ImportError("typed parameter dialog requires PySide6"),
        ):
            with self.assertRaises(ImportError) as raised:
                bootstrap.show_tools(controller)
        self.assertIn("PySide6", str(raised.exception))
        self.assertIsNone(bootstrap._controller)


class BootstrapImportTests(unittest.TestCase):
    def test_import_does_not_load_pyside_or_pymxs(self) -> None:
        script = (
            "import sys\n"
            "import max2025_menu_prototype.max_host.bootstrap as bootstrap\n"
            "print(callable(bootstrap.show_tools))\n"
            "print('PySide6' in sys.modules)\n"
            "print('pymxs' in sys.modules)\n"
        )
        completed = checkout_support.run_python(script)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        lines = completed.stdout.strip().splitlines()
        self.assertEqual(lines, ["True", "False", "False"])


def diagnostic_summary(**overrides) -> ControllerSummary:
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


class DiagnosticController:
    """Public-summary-only fake; private host/menu access is an error."""

    def __init__(self, summary=None, summary_error=None) -> None:
        self.summary_state = (
            summary if summary is not None else diagnostic_summary()
        )
        self.summary_error = summary_error
        self.summary_calls = 0
        self.open_calls = []
        self.handle_calls = []

    def summary(self):
        self.summary_calls += 1
        if self.summary_error is not None:
            raise self.summary_error
        return self.summary_state

    @property
    def _host(self):
        raise AssertionError("diagnostics must not access private host fields")

    def open(self, *args, **kwargs):
        self.open_calls.append((args, kwargs))
        raise AssertionError("diagnostics must not refresh/open the menu")

    def handle(self, *args, **kwargs):
        self.handle_calls.append((args, kwargs))
        raise AssertionError("diagnostics must not cancel/handle menu input")


class DiagnosticsTests(unittest.TestCase):
    def setUp(self) -> None:
        bootstrap.uninstall()
        bootstrap._controller = None

    def tearDown(self) -> None:
        bootstrap.uninstall()

    def test_reads_existing_summary_without_creating_controller_or_widgets(self) -> None:
        controller = DiagnosticController()
        bootstrap._controller = controller
        panels_before = list(FakePanel.instances)

        with mock.patch.object(
            bootstrap, "_create_controller", side_effect=AssertionError("created")
        ), mock.patch.object(probe, "report", return_value="REPORT") as report_call:
            text = bootstrap.report_diagnostics()

        self.assertEqual(text, "REPORT")
        self.assertIs(bootstrap._controller, controller)
        self.assertEqual(controller.summary_calls, 1)
        self.assertEqual(controller.open_calls, [])
        self.assertEqual(controller.handle_calls, [])
        self.assertEqual(FakePanel.instances, panels_before)
        kwargs = report_call.call_args.kwargs
        self.assertIsNone(kwargs["runtime"])
        self.assertIs(kwargs["summary"], controller.summary_state)
        self.assertFalse(kwargs["include_details"])

    def test_passes_include_details_flag(self) -> None:
        controller = DiagnosticController()
        bootstrap._controller = controller
        with mock.patch.object(probe, "report", return_value="REPORT") as report_call:
            bootstrap.report_diagnostics(include_details=True)
        self.assertTrue(report_call.call_args.kwargs["include_details"])

    def test_no_controller_reports_unavailable_without_initializing(self) -> None:
        with mock.patch.object(
            bootstrap, "_create_controller", side_effect=AssertionError("created")
        ), mock.patch.object(
            probe, "_import_pymxs_runtime", side_effect=ImportError("no host")
        ), mock.patch("builtins.print"):
            text = bootstrap.report_diagnostics()

        self.assertIsNone(bootstrap._controller)
        self.assertIn("controller summary: not available", text)
        self.assertIn("host runtime unavailable", text)
        self.assertIn("Native operation validation: NOT RUN", text)

    def test_busy_summary_skips_fresh_probe_and_dialog_interaction(self) -> None:
        controller = DiagnosticController(summary=diagnostic_summary(busy=True))
        bootstrap._controller = controller

        with mock.patch.object(
            probe, "MaxHostAdapter", side_effect=AssertionError("adapter")
        ), mock.patch.object(
            probe, "_import_pymxs_runtime", side_effect=AssertionError("host")
        ), mock.patch("builtins.print"):
            text = bootstrap.report_diagnostics()

        self.assertIn("busy: True", text)
        self.assertIn("skipped: a prototype invocation/dialog is busy", text)
        self.assertEqual(controller.summary_calls, 1)
        self.assertEqual(controller.open_calls, [])
        self.assertEqual(controller.handle_calls, [])
        self.assertIs(bootstrap._controller, controller)

    def test_unavailable_summary_is_bounded_and_does_not_leak_error_text(self) -> None:
        controller = DiagnosticController(
            summary_error=RuntimeError("SECRET_SUMMARY C:/private/path")
        )
        bootstrap._controller = controller

        with mock.patch.object(
            bootstrap, "_create_controller", side_effect=AssertionError("created")
        ), mock.patch.object(
            probe, "_import_pymxs_runtime", side_effect=ImportError("no host")
        ), mock.patch("builtins.print"):
            text = bootstrap.report_diagnostics()

        self.assertIn("controller summary: not available", text)
        self.assertNotIn("SECRET_SUMMARY", text)
        self.assertNotIn("C:/private/path", text)
        self.assertEqual(controller.summary_calls, 1)

    def test_worker_thread_is_rejected_before_summary_access(self) -> None:
        controller = DiagnosticController()
        bootstrap._controller = controller
        result = {}

        def target() -> None:
            try:
                bootstrap.report_diagnostics()
            except Exception as exc:  # noqa: BLE001 - test captures the type
                result["error"] = exc

        import threading

        thread = threading.Thread(target=target)
        thread.start()
        thread.join(timeout=10)

        self.assertIsInstance(result.get("error"), RuntimeError)
        self.assertIn("main thread", str(result["error"]))
        self.assertEqual(controller.summary_calls, 0)
        self.assertEqual(controller.open_calls, [])

    def test_does_not_touch_private_host_field(self) -> None:
        controller = DiagnosticController()
        bootstrap._controller = controller
        with mock.patch.object(probe, "report", return_value="REPORT"):
            text = bootstrap.report_diagnostics()
        self.assertEqual(text, "REPORT")


if __name__ == "__main__":
    unittest.main()

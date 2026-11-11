"""Optional PySide6 popup checks.

These tests skip with an explicit reason when PySide6 is unavailable.  Even
when they run offscreen, they are renderer contract tests, not Max focus/input
validation.
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:  # The explicit skip is part of the deliverable.
    from PySide6 import QtCore, QtGui, QtWidgets  # type: ignore[import-not-found]
    from PySide6.QtTest import QTest  # type: ignore[import-not-found]
    from PySide6 import __version__ as PYSIDE_VERSION
except Exception as exc:  # pragma: no cover - exercised on this host
    raise unittest.SkipTest(f"PySide6 is not installed; optional Qt tests skipped ({exc})")

from max2025_menu_prototype.core.catalog import ACTIONS
from max2025_menu_prototype.core.contracts import (
    Availability,
    ContextLevel,
    ContextSnapshot,
    LaunchResult,
)
from max2025_menu_prototype.core.controller import MenuController
from max2025_menu_prototype.core.profile import default_profile
from max2025_menu_prototype.core.simulated import SimulatedHost
from max2025_menu_prototype.qt_ui.popup import MenuPopup

APP = None


class ParameterHost:
    """Fake host that accepts validated parameter mappings and records calls."""

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
        self.calls: list[tuple[str, ContextSnapshot, dict | None]] = []

    def snapshot(self) -> ContextSnapshot:
        return self.current

    def availability(self, context: ContextSnapshot) -> dict[str, Availability]:
        return {
            spec.id: Availability(True)
            for spec in ACTIONS
            if spec.id != "menu.repeat"
        }

    def launch(self, action_id, expected_context, parameters=None):
        self.calls.append((action_id, expected_context, parameters))
        return LaunchResult("applied", action_id, "fake postcondition checked")


def setUpModule() -> None:  # noqa: N802 - unittest hook
    global APP
    APP = QtWidgets.QApplication.instance()
    if APP is None:
        APP = QtWidgets.QApplication(["context-menu-qt-tests"])


def tearDownModule() -> None:  # noqa: N802 - unittest hook
    global APP
    # Never destroy an application owned by another test module.
    APP = None


class QtPopupTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.host = SimulatedHost("editable_poly_vertex")
        self.controller = MenuController(self.host, default_profile())
        self.popup = MenuPopup(self.controller)
        self.addCleanup(self._cleanup_popup)
        QTest.qWait(1)

    def _cleanup_popup(self) -> None:
        try:
            self.popup.dispose()
        finally:
            QTest.qWait(1)

    def open(self, invocation: str = "latched", position: QtCore.QPoint | None = None):
        position = position or QtCore.QPoint(120, 120)
        view = self.popup.show_at(position, invocation=invocation)
        QTest.qWait(2)
        return view

    def key(self, key: QtCore.Qt.Key, modifiers: QtCore.Qt.KeyboardModifier = QtCore.Qt.KeyboardModifier.NoModifier):
        QTest.keyClick(self.popup, key, modifiers)
        QTest.qWait(1)

    def click_row(self, action_id: str) -> bool:
        for frame in self.popup._row_widgets:
            if frame.property("actionId") == action_id:
                center = frame.rect().center()
                QTest.mouseClick(
                    self.popup,
                    QtCore.Qt.MouseButton.LeftButton,
                    QtCore.Qt.KeyboardModifier.NoModifier,
                    self.popup.mapFromGlobal(frame.mapToGlobal(center)),
                )
                QTest.qWait(2)
                return True
        return False


class RenderingTests(QtPopupTestCase):
    def test_factory_signature_and_single_application(self) -> None:
        self.assertIs(QtWidgets.QApplication.instance(), APP)
        self.assertIsInstance(self.popup, QtWidgets.QWidget)
        self.assertEqual(self.popup.objectName(), "contextMenuPopup")
        self.assertTrue(callable(self.popup.show_at))
        self.assertTrue(callable(self.popup.cancel))
        self.assertTrue(callable(self.popup.dispose))
        self.assertTrue(str(PYSIDE_VERSION))

    def test_open_latched_has_no_initial_hover_arming(self) -> None:
        view = self.open("latched")
        self.assertEqual(view.phase, "latched")
        self.assertTrue(self.popup.isVisible())
        self.assertIsNone(self.controller.view().armed_id)
        self.assertIsNone(self.controller.view().highlighted_id)

    def test_disabled_rows_keep_readable_reasons(self) -> None:
        self.host.set_scenario("no_selection")
        self.open("latched")
        reasons = [
            label.text()
            for label in self.popup.findChildren(QtWidgets.QLabel, "rowReason")
        ]
        self.assertTrue(reasons)
        self.assertTrue(any("Select" in reason for reason in reasons))
        disabled = [row for row in self.controller.view().rows if not row.enabled]
        self.assertTrue(disabled)
        self.assertTrue(all(row.reason for row in disabled))

    def test_keyboard_traversal_reads_disabled_reason(self) -> None:
        self.host.set_scenario("no_selection")
        self.open("latched")
        self.key(QtCore.Qt.Key.Key_Down)
        view = self.controller.view()
        self.assertIsNotNone(view.highlighted_id)
        row = next(item for item in view.rows if item.action_id == view.highlighted_id)
        self.assertFalse(row.enabled)
        self.assertEqual(view.status, row.reason)

    def test_search_renders_and_resets_on_reopen(self) -> None:
        self.open("latched")
        QTest.keyClicks(self.popup, "grow")
        QTest.qWait(2)
        view = self.controller.view()
        self.assertEqual(view.phase, "search")
        self.assertEqual(view.query, "grow")
        self.assertTrue(view.rows)
        self.assertTrue(all("grow" in row.label.lower() for row in view.rows))

        self.key(QtCore.Qt.Key.Key_Escape)
        self.assertFalse(self.popup.isVisible())
        self.assertEqual(self.controller.view().phase, "closed")

        self.open("latched")
        view = self.controller.view()
        self.assertEqual(view.query, "")
        self.assertEqual(view.phase, "latched")
        self.assertGreater(len(view.rows), 1)

    def test_escape_cancels_and_cleans_filter(self) -> None:
        self.open("latched")
        self.assertTrue(self.popup._filter_installed)
        self.key(QtCore.Qt.Key.Key_Escape)
        self.assertEqual(self.controller.view().phase, "closed")
        self.assertFalse(self.popup.isVisible())
        self.assertFalse(self.popup._filter_installed)


class InputTests(QtPopupTestCase):
    def test_mouse_click_commits_once_and_closes(self) -> None:
        view = self.open("latched")
        enabled = next(row for row in view.rows if row.enabled)
        self.assertTrue(self.click_row(enabled.action_id))
        self.assertEqual(self.controller.view().phase, "closed")
        self.assertFalse(self.popup.isVisible())
        self.assertEqual(len(self.host.launch_log), 1)
        self.assertEqual(self.host.launch_log[0]["action_id"], enabled.action_id)
        self.assertEqual(self.host.launch_log[0]["status"], "simulated")

    def test_popup_hides_and_restores_focus_before_host_dispatch(self) -> None:
        origin = QtWidgets.QLineEdit()
        origin.setText("origin widget")
        origin.show()
        origin.setFocus()
        self.addCleanup(origin.deleteLater)
        QTest.qWait(2)

        self.popup.show_at(
            QtCore.QPoint(160, 140), invocation="latched", return_focus=origin
        )
        QTest.qWait(2)
        view = self.controller.view()
        enabled = next(row for row in view.rows if row.enabled)

        observed: list[tuple[bool, bool]] = []
        original_launch = self.host.launch

        def launch(action_id, expected):
            observed.append((self.popup.isVisible(), origin.hasFocus()))
            return original_launch(action_id, expected)

        self.host.launch = launch
        self.assertTrue(self.click_row(enabled.action_id))
        self.assertTrue(observed)
        self.assertFalse(observed[0][0], "popup must hide before host dispatch")
        self.assertTrue(observed[0][1], "focus must restore before host dispatch")
        self.assertEqual(self.controller.view().phase, "closed")

    def test_parameter_dialog_focus_does_not_cancel_accepted_request(self) -> None:
        host = ParameterHost()
        controller = MenuController(host, default_profile())
        popup = MenuPopup(controller)
        self.addCleanup(popup.dispose)

        def prompt(spec, context, schema):
            dialog = QtWidgets.QDialog(popup)
            dialog.setModal(True)
            QtCore.QTimer.singleShot(0, dialog.accept)
            dialog.exec()
            dialog.deleteLater()
            return {"height": 1.0, "extrusion_type": "group"}

        controller.set_parameter_prompt(prompt)
        popup.show_at(QtCore.QPoint(140, 120), invocation="latched")
        QTest.qWait(2)

        target = None
        for frame in popup._row_widgets:
            if frame.property("actionId") == "model.extrude":
                target = frame
                break
        self.assertIsNotNone(target)
        QTest.mouseClick(
            popup,
            QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.KeyboardModifier.NoModifier,
            popup.mapFromGlobal(target.mapToGlobal(target.rect().center())),
        )
        QTest.qWait(5)

        self.assertEqual([call[0] for call in host.calls], ["model.extrude"])
        self.assertEqual(
            host.calls[0][2], {"height": 1.0, "extrusion_type": "group"}
        )
        self.assertEqual(controller.view().last_result.status, "applied")
        self.assertFalse(controller.summary().busy)
        self.assertFalse(popup.isVisible())

    def test_hover_arms_only_after_pointer_move(self) -> None:
        origin = QtCore.QPoint(200, 160)
        self.open("held", origin)
        self.assertIsNone(self.controller.view().armed_id)

        neutral = self.popup.mapFromGlobal(origin + QtCore.QPoint(2, 2))
        QTest.mouseMove(self.popup, neutral)
        QTest.qWait(1)
        self.assertIsNone(self.controller.view().armed_id)

        target = None
        for frame in self.popup._row_widgets:
            if frame.property("actionId") == "transform.move":
                target = frame
                break
        self.assertIsNotNone(target)
        local = self.popup.mapFromGlobal(target.mapToGlobal(target.rect().center()))
        QTest.mouseMove(self.popup, local)
        QTest.qWait(1)
        self.assertEqual(self.controller.view().armed_id, "transform.move")

    def test_hide_event_sends_cancellation(self) -> None:
        self.open("latched")
        self.popup.hide()
        QTest.qWait(2)
        self.assertEqual(self.controller.view().phase, "closed")

    def test_second_invocation_cancels_old_menu(self) -> None:
        self.open("latched")
        first_session = self.controller.view().session_id
        view = self.popup.show_at(QtCore.QPoint(80, 80), invocation="latched")
        QTest.qWait(2)
        self.assertEqual(view.phase, "closed")
        self.assertFalse(self.popup.isVisible())
        self.assertEqual(self.controller.view().session_id, first_session)

    def test_cancel_and_dispose_are_idempotent(self) -> None:
        self.open("latched")
        self.popup.cancel()
        self.popup.cancel()
        self.assertEqual(self.controller.view().phase, "closed")
        self.popup.dispose()
        self.popup.dispose()
        self.assertFalse(self.popup.isVisible())


class ClampingTests(QtPopupTestCase):
    def test_popup_clamps_to_available_screen(self) -> None:
        app = QtWidgets.QApplication.instance()
        screen = app.primaryScreen()
        self.assertIsNotNone(screen)
        bounds = screen.availableGeometry()
        corner = bounds.bottomRight() - QtCore.QPoint(2, 2)
        self.open("latched", corner)
        geometry = self.popup.frameGeometry()
        self.assertGreaterEqual(geometry.left(), bounds.left())
        self.assertGreaterEqual(geometry.top(), bounds.top())
        self.assertLessEqual(geometry.right(), bounds.right())
        self.assertLessEqual(geometry.bottom(), bounds.bottom())
        self.assertIsNone(self.controller.view().armed_id)

    def test_show_at_requires_existing_application(self) -> None:
        self.assertIs(MenuPopup._application(), QtWidgets.QApplication.instance())


if __name__ == "__main__":
    unittest.main()

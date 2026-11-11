"""Native dialog/panel contract tests.

The pure helpers always run.  Qt widget tests skip with an explicit reason when
PySide6 is unavailable; they are renderer tests, not 3ds Max validation.
"""

from __future__ import annotations

import os
import unittest
from dataclasses import replace
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from max2025_menu_prototype.core.catalog import ACTIONS_BY_ID
from max2025_menu_prototype.core.contracts import (
    Availability,
    ContextLevel,
    ContextSnapshot,
    ControllerSummary,
    LaunchResult,
    ParameterSpec,
)
from max2025_menu_prototype.core.parameters import (
    ParameterError,
    parameter_schema,
)
from max2025_menu_prototype.qt_ui import parameter_model

try:
    from PySide6 import QtCore, QtWidgets  # type: ignore[import-not-found]
    from PySide6.QtTest import QTest  # type: ignore[import-not-found]

    HAVE_QT = True
    QT_ERROR = ""
except Exception as exc:  # pragma: no cover - exercised on this host
    HAVE_QT = False
    QT_ERROR = str(exc)

if HAVE_QT:
    from max2025_menu_prototype.qt_ui.modeling_panel import ModelingPanel
    from max2025_menu_prototype.qt_ui.parameters import (
        ParameterDialog,
        close_pending_dialogs,
        collect_parameters,
    )


def context(level: ContextLevel = ContextLevel.POLYGON) -> ContextSnapshot:
    return ContextSnapshot(
        level=level,
        target_kind="editable_poly",
        target_token="poly:base",
        selection_count=2,
        selection_token=f"sel:{level.value}:2",
        panel_ready=True,
        conflicting_mode=False,
        revision=3,
    )


class ParameterModelTests(unittest.TestCase):
    def test_defaults_are_typed_fresh_and_ordered(self) -> None:
        schema = parameter_schema("model.extrude", ContextLevel.POLYGON)
        values = parameter_model.initial_values(schema)
        self.assertEqual(list(values), ["height", "extrusion_type"])
        self.assertEqual(values["height"], 1.0)
        self.assertEqual(values["extrusion_type"], "group")
        values["height"] = 99.0
        self.assertEqual(schema[0].default, 1.0)

    def test_numeric_bounds_use_schema_and_prototype_limit(self) -> None:
        schema = {
            spec.name: spec
            for spec in parameter_schema("model.bridge", ContextLevel.EDGE)
        }
        self.assertEqual(parameter_model.numeric_bounds(schema["segments"]), (1, 100))
        self.assertEqual(parameter_model.numeric_bounds(schema["taper"]), (-100.0, 100.0))
        height = parameter_schema("model.extrude", ContextLevel.POLYGON)[0]
        self.assertEqual(parameter_model.numeric_bounds(height)[0], -1_000_000.0)
        with self.assertRaises(ValueError):
            parameter_model.numeric_bounds(
                ParameterSpec("flag", "Flag", "bool", False)
            )

    def test_bridge_twist_is_consumed_as_an_integer_control(self) -> None:
        schema = parameter_schema("model.bridge", ContextLevel.EDGE)
        specs = {spec.name: spec for spec in schema}
        twist = specs["twist"]
        self.assertEqual(twist.kind, "int")
        self.assertEqual(parameter_model.numeric_bounds(twist), (-100, 100))
        self.assertIs(type(twist.default), int)
        self.assertEqual(
            type(parameter_model.initial_values(schema)["twist"]), int
        )

    def test_enum_and_unit_display_does_not_change_wire_values(self) -> None:
        schema = parameter_schema("model.extrude", ContextLevel.POLYGON)
        self.assertEqual(parameter_model.display_choice("local_normal"), "local normal")
        self.assertEqual(parameter_model.unit_suffix(schema[0]), " (system units)")
        self.assertEqual(parameter_model.unit_suffix(schema[1]), "")

    def test_dialog_notes_show_no_preview_units_and_confirmation(self) -> None:
        spec = ACTIONS_BY_ID["model.bevel"]
        schema = parameter_schema("model.bevel", ContextLevel.POLYGON)
        notes = parameter_model.dialog_notes(spec, context(), schema)
        text = "\n".join(notes)
        self.assertIn("No live preview", text)
        self.assertIn("system units", text)
        self.assertIn("polygon context", text)
        self.assertIn("current Max selection", text)
        self.assertTrue(parameter_model.action_summary(spec).startswith("immediate edit"))

        destructive = parameter_schema("model.collapse", ContextLevel.VERTEX)
        self.assertTrue(parameter_model.has_confirmation(destructive))
        self.assertIn(
            "destructive edit",
            "\n".join(
                parameter_model.dialog_notes(
                    ACTIONS_BY_ID["model.collapse"], context(ContextLevel.VERTEX), destructive
                )
            ),
        )

    def test_validation_delegates_to_frozen_allowlist(self) -> None:
        schema = parameter_schema("model.extrude", ContextLevel.POLYGON)
        valid = {"height": 2, "extrusion_type": "local_normal"}
        self.assertEqual(
            parameter_model.validate_dialog_values(
                "model.extrude", ContextLevel.POLYGON, schema, valid
            ),
            {"height": 2.0, "extrusion_type": "local_normal"},
        )
        for bad in (
            {"height": 1.0},
            {"height": 1.0, "extrusion_type": "group", "extra": 1},
            {"height": float("inf"), "extrusion_type": "group"},
            {"height": 1.0, "extrusion_type": "sideways"},
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(ParameterError):
                    parameter_model.validate_dialog_values(
                        "model.extrude", ContextLevel.POLYGON, schema, bad
                    )

    def test_negative_amount_and_unconfirmed_destructive_values_fail(self) -> None:
        inset = parameter_schema("model.inset", ContextLevel.POLYGON)
        with self.assertRaises(ParameterError):
            parameter_model.validate_dialog_values(
                "model.inset",
                ContextLevel.POLYGON,
                inset,
                {"amount": -0.1, "inset_type": "group"},
            )
        collapse = parameter_schema("model.collapse", ContextLevel.VERTEX)
        with self.assertRaises(ParameterError):
            parameter_model.validate_dialog_values(
                "model.collapse",
                ContextLevel.VERTEX,
                collapse,
                {"confirm": False},
            )

    def test_target_and_result_text_distinguish_statuses(self) -> None:
        summary = ControllerSummary(
            phase="closed",
            context=None,
            target_kind="none",
            selection_count=0,
            panel_ready=False,
            conflicting_mode=False,
            busy=False,
            status="",
            last_result=None,
            last_repeat_id=None,
        )
        self.assertIn("not read yet", parameter_model.describe_target(summary))
        summary = replace(
            summary,
            context=ContextLevel.POLYGON,
            target_kind="editable_poly",
            selection_count=2,
            panel_ready=True,
        )
        target = parameter_model.describe_target(summary)
        self.assertIn("polygon context", target)
        self.assertIn("2 selected", target)

        applied = parameter_model.describe_launch_result(
            LaunchResult("applied", "model.extrude", "checked")
        )
        launched = parameter_model.describe_launch_result(
            LaunchResult("launched", "transform.move", "mode")
        )
        blocked = parameter_model.describe_launch_result(
            LaunchResult("blocked", "model.inset", "no selection")
        )
        self.assertTrue(applied.startswith("APPLIED ONCE"))
        self.assertIn("mode entry only", launched)
        self.assertTrue(blocked.startswith("BLOCKED"))

    def test_enabled_action_ids_ignore_disabled_entries(self) -> None:
        summary = SimpleNamespace(
            availability=(
                ("selection.grow", Availability(True)),
                ("model.extrude", Availability(False, "needs parameters")),
            )
        )
        self.assertEqual(
            parameter_model.enabled_action_ids(summary), ("selection.grow",)
        )


def setUpModule() -> None:  # noqa: N802 - unittest hook
    global APP
    APP = None
    if not HAVE_QT:
        return
    APP = QtWidgets.QApplication.instance()
    if APP is None:
        APP = QtWidgets.QApplication(["native-ui-tests"])


def tearDownModule() -> None:  # noqa: N802 - unittest hook
    global APP
    APP = None


@unittest.skipUnless(HAVE_QT, f"PySide6 is unavailable; Qt dialog/panel tests skipped ({QT_ERROR})")
class ParameterDialogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.addCleanup(close_pending_dialogs)
        QTest.qWait(1)

    def dialog(self, action_id: str, level: ContextLevel = ContextLevel.POLYGON):
        spec = ACTIONS_BY_ID[action_id]
        return ParameterDialog(spec, context(level), parameter_schema(action_id, level))

    def test_dialog_uses_typed_controls_and_defaults(self) -> None:
        dialog = self.dialog("model.extrude")
        self.assertIsInstance(
            dialog._widgets["height"], QtWidgets.QDoubleSpinBox
        )
        self.assertIsInstance(
            dialog._widgets["extrusion_type"], QtWidgets.QComboBox
        )
        self.assertEqual(
            dialog.values(), {"height": 1.0, "extrusion_type": "group"}
        )
        self.assertIsNone(dialog.validated_values())
        dialog.reject()

    def test_apply_once_returns_validated_plain_values(self) -> None:
        dialog = self.dialog("model.extrude")
        dialog.set_values({"height": 2, "extrusion_type": "local_normal"})
        dialog.accept()
        self.assertEqual(dialog.result(), QtWidgets.QDialog.DialogCode.Accepted.value)
        self.assertEqual(
            dialog.validated_values(),
            {"height": 2.0, "extrusion_type": "local_normal"},
        )

    def test_invalid_value_keeps_dialog_open_with_readable_error(self) -> None:
        dialog = self.dialog("model.inset")
        dialog.set_values({"amount": 0.0, "inset_type": "group"})
        dialog.accept()
        self.assertNotEqual(dialog.result(), QtWidgets.QDialog.DialogCode.Accepted.value)
        self.assertIn("greater than zero", dialog._error.text())
        self.assertIsNone(dialog.validated_values())

        dialog.set_values({"amount": 0.5, "inset_type": "by_polygon"})
        dialog.accept()
        self.assertEqual(dialog.validated_values()["amount"], 0.5)
        self.assertEqual(
            dialog.validated_values()["inset_type"], "by_polygon"
        )

    def test_destructive_confirmation_is_required(self) -> None:
        dialog = self.dialog("model.collapse", ContextLevel.VERTEX)
        self.assertIsInstance(dialog._widgets["confirm"], QtWidgets.QCheckBox)
        dialog.accept()
        self.assertIn("confirmation", dialog._error.text())
        self.assertIsNone(dialog.validated_values())
        dialog.set_values({"confirm": True})
        dialog.accept()
        self.assertEqual(dialog.validated_values(), {"confirm": True})

    def test_cancel_close_and_pending_teardown_return_none(self) -> None:
        QtCore.QTimer.singleShot(0, close_pending_dialogs)
        self.assertIsNone(
            collect_parameters(
                ACTIONS_BY_ID["model.extrude"],
                context(),
                parameter_schema("model.extrude", ContextLevel.POLYGON),
            )
        )

    def test_dialog_is_parented_modal_and_does_not_import_pymxs(self) -> None:
        import sys

        parent = QtWidgets.QWidget()
        self.addCleanup(parent.deleteLater)
        spec = ACTIONS_BY_ID["model.extrude"]
        dialog = ParameterDialog(
            spec,
            context(),
            parameter_schema("model.extrude", ContextLevel.POLYGON),
            parent,
        )
        self.assertIs(dialog.parent(), parent)
        self.assertTrue(dialog.isModal())
        self.assertNotIn("pymxs", sys.modules)
        dialog.reject()


class FakePanelController:
    def __init__(self) -> None:
        self.summary_state = ControllerSummary(
            phase="closed",
            context=ContextLevel.POLYGON,
            target_kind="editable_poly",
            selection_count=2,
            panel_ready=True,
            conflicting_mode=False,
            busy=False,
            status="Ready.",
            last_result=None,
            last_repeat_id=None,
            availability=(
                ("context.polygon", Availability(True)),
                ("selection.grow", Availability(True)),
            ),
        )
        self.invoked: list[str] = []

    def summary(self):
        return self.summary_state

    def invoke(self, action_id: str):
        self.invoked.append(action_id)
        return SimpleNamespace(status=f"invoked {action_id}")


@unittest.skipUnless(HAVE_QT, f"PySide6 is unavailable; Qt dialog/panel tests skipped ({QT_ERROR})")
class ModelingPanelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = FakePanelController()
        self.panel = ModelingPanel(self.controller)
        self.addCleanup(self.panel.dispose)
        QTest.qWait(1)

    def button(self, name: str) -> QtWidgets.QPushButton:
        found = self.panel.findChild(QtWidgets.QPushButton, name)
        self.assertIsNotNone(found)
        return found

    def test_single_application_and_honest_header(self) -> None:
        self.assertIs(QtWidgets.QApplication.instance(), APP)
        self.assertEqual(self.panel.objectName(), "nativeModelingPanel")
        titles = [
            label.text()
            for label in self.panel.findChildren(QtWidgets.QLabel)
        ]
        self.assertTrue(
            any("experimental, not host-validated" in text for text in titles)
        )
        self.assertIn("polygon context", self.panel.values_shown()["target"])

    def test_level_buttons_call_controller_invoke(self) -> None:
        buttons = self.panel.findChildren(QtWidgets.QPushButton, "nativeLevelButton")
        self.assertEqual(len(buttons), 6)
        polygon = next(
            item for item in buttons if item.property("actionId") == "context.polygon"
        )
        QTest.mouseClick(polygon, QtCore.Qt.MouseButton.LeftButton)
        self.assertEqual(self.controller.invoked, ["context.polygon"])

    def test_result_area_distinguishes_applied_blocked_and_launched(self) -> None:
        for status, expected in (
            ("applied", "APPLIED ONCE"),
            ("blocked", "BLOCKED"),
            ("launched", "mode entry only"),
            ("error", "ERROR"),
        ):
            self.controller.summary_state = replace(
                self.controller.summary_state,
                last_result=LaunchResult(status, "model.extrude", "detail"),
            )
            self.panel.refresh()
            self.assertIn(expected, self.panel.values_shown()["result"])

    def test_menu_signal_and_refresh_callback_are_bootstrap_seams(self) -> None:
        emitted: list[int] = []
        self.panel.menu_requested.connect(lambda: emitted.append(1))
        QTest.mouseClick(
            self.button("nativeOpenMenuButton"),
            QtCore.Qt.MouseButton.LeftButton,
        )
        self.assertEqual(emitted, [1])

        calls: list[int] = []

        def refresh() -> None:
            calls.append(1)
            self.controller.summary_state = replace(
                self.controller.summary_state, context=ContextLevel.VERTEX
            )

        self.panel.set_refresh_callback(refresh)
        QTest.mouseClick(
            self.button("nativeRefreshButton"),
            QtCore.Qt.MouseButton.LeftButton,
        )
        self.assertEqual(calls, [1])
        self.assertIn("vertex context", self.panel.values_shown()["target"])
        self.assertIn("Context refreshed", self.panel.values_shown()["status"])

    def test_dispose_is_idempotent(self) -> None:
        self.panel.dispose()
        self.panel.dispose()
        self.assertFalse(self.panel.isVisible())


if __name__ == "__main__":
    unittest.main()

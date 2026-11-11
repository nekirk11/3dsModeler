"""Typed, one-shot Qt parameter dialog for native modeling actions.

The dialog only collects plain values.  It never imports ``pymxs``, calls a
host, previews geometry, writes settings, or creates undo state.  Commit says
``Apply once``; Cancel, Escape, window close, or bootstrap teardown returns
``None`` from :func:`collect_parameters`.

The dialog is modal and parented by the caller (normally Max's main window).
It installs no application event filter or global shortcut, so closing it does
not consume later viewport key events.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from PySide6 import QtCore, QtWidgets  # type: ignore[import-not-found]

from ..core.contracts import ContextLevel, ParameterSpec
from ..core.parameters import ParameterError
from .parameter_model import (
    dialog_notes,
    display_choice,
    numeric_bounds,
    unit_suffix,
    validate_dialog_values,
)

_ACTIVE_DIALOGS: list["ParameterDialog"] = []

_STYLE = """
QDialog#parameterDialog { background: #12151d; }
QLabel#parameterTitle { color: #e8ebf2; font-size: 15px; font-weight: 700; }
QLabel#parameterNote { color: #9aa3b5; font-size: 11px; }
QLabel#parameterWarning { color: #f2a33c; font-size: 11px; }
QLabel#parameterError { color: #ff8f8f; font-size: 11px; font-weight: 600; }
QLabel#parameterFieldLabel { color: #c9cfdd; font-size: 11px; }
QLabel#parameterUnit { color: #6c7487; font-size: 10px; }
QDialogButtonBox QPushButton { min-width: 96px; padding: 5px 10px; }
"""


def _assert_gui_thread() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is None:
        raise RuntimeError(
            "ParameterDialog requires an existing QApplication; the prototype "
            "never creates a second one."
        )
    if QtCore.QThread.currentThread() is not app.thread():
        raise RuntimeError("ParameterDialog must run on the Qt GUI thread.")
    return app


def _register_dialog(dialog: "ParameterDialog") -> None:
    if dialog not in _ACTIVE_DIALOGS:
        _ACTIVE_DIALOGS.append(dialog)


def _unregister_dialog(dialog: "ParameterDialog") -> None:
    try:
        _ACTIVE_DIALOGS.remove(dialog)
    except ValueError:
        pass


def close_pending_dialogs() -> None:
    """Reject every live parameter dialog; safe outside a dialog and repeatedly.

    Bootstrap teardown calls this so a pending modal loop returns ``None`` and
    the controller invalidates the in-flight invocation.
    """

    for dialog in list(_ACTIVE_DIALOGS):
        try:
            dialog.reject()
        except RuntimeError:
            _unregister_dialog(dialog)


class ParameterDialog(QtWidgets.QDialog):
    """One modal, typed, no-preview parameter editor."""

    def __init__(
        self,
        spec: Any,
        context: Any,
        schema: Sequence[ParameterSpec],
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        _assert_gui_thread()
        if not schema:
            raise ValueError("ParameterDialog requires a non-empty schema")
        if not isinstance(schema, (tuple, list)):
            raise TypeError("schema must be a sequence of ParameterSpec")
        for item in schema:
            if not isinstance(item, ParameterSpec):
                raise TypeError("schema entries must be ParameterSpec values")

        super().__init__(parent)
        self.setObjectName("parameterDialog")
        self.setWindowTitle(
            f"Apply once — {getattr(spec, 'label', 'native action')}"
        )
        self.setModal(True)
        self.setWindowModality(QtCore.Qt.WindowModality.ApplicationModal)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setMinimumWidth(400)
        self.setStyleSheet(_STYLE)

        self._spec = spec
        self._context = context
        self._schema = tuple(schema)
        self._validated: dict[str, Any] | None = None
        self._completed = False
        self._widgets: dict[str, QtWidgets.QWidget] = {}

        self._build()
        _register_dialog(self)

    # ------------------------------------------------------------------- build

    def _build(self) -> None:
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(8)

        title = QtWidgets.QLabel(
            f"{getattr(self._spec, 'label', 'Action')} — {getattr(self._spec, 'id', '')}",
            self,
        )
        title.setObjectName("parameterTitle")
        outer.addWidget(title)

        notes = QtWidgets.QWidget(self)
        notes_layout = QtWidgets.QVBoxLayout(notes)
        notes_layout.setContentsMargins(0, 0, 0, 0)
        notes_layout.setSpacing(2)
        for index, line in enumerate(dialog_notes(self._spec, self._context, self._schema)):
            label = QtWidgets.QLabel(line, notes)
            label.setObjectName(
                "parameterWarning" if index >= 4 else "parameterNote"
            )
            label.setWordWrap(True)
            notes_layout.addWidget(label)
        outer.addWidget(notes)

        fields = QtWidgets.QWidget(self)
        fields_layout = QtWidgets.QFormLayout(fields)
        fields_layout.setContentsMargins(0, 4, 0, 4)
        fields_layout.setSpacing(6)
        for parameter in self._schema:
            widget = self._make_widget(parameter, fields)
            self._widgets[parameter.name] = widget
            if parameter.kind == "bool":
                fields_layout.addRow(widget)
                continue
            label = QtWidgets.QLabel(
                f"{parameter.label}{unit_suffix(parameter)}", fields
            )
            label.setObjectName("parameterFieldLabel")
            fields_layout.addRow(label, widget)
        outer.addWidget(fields)

        self._error = QtWidgets.QLabel("", self)
        self._error.setObjectName("parameterError")
        self._error.setWordWrap(True)
        self._error.hide()
        outer.addWidget(self._error)

        buttons = QtWidgets.QDialogButtonBox(self)
        self._apply_button = buttons.addButton(
            "Apply once", QtWidgets.QDialogButtonBox.ButtonRole.AcceptRole
        )
        self._apply_button.setObjectName("parameterApplyOnce")
        self._apply_button.setDefault(True)
        self._apply_button.setAutoDefault(True)
        cancel = buttons.addButton(QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        cancel.setObjectName("parameterCancel")
        cancel.setAutoDefault(False)
        buttons.rejected.connect(self.reject)
        self._apply_button.clicked.connect(self.accept)
        outer.addWidget(buttons)

    def _make_widget(
        self, parameter: ParameterSpec, parent: QtWidgets.QWidget
    ) -> QtWidgets.QWidget:
        if parameter.kind == "bool":
            widget = QtWidgets.QCheckBox(parameter.label, parent)
            widget.setObjectName(f"parameter_{parameter.name}")
            widget.setChecked(bool(parameter.default))
            widget.toggled.connect(self._clear_error)
            return widget
        if parameter.kind == "enum":
            widget = QtWidgets.QComboBox(parent)
            widget.setObjectName(f"parameter_{parameter.name}")
            for choice in parameter.choices:
                widget.addItem(display_choice(choice), choice)
            try:
                widget.setCurrentIndex(parameter.choices.index(parameter.default))
            except ValueError:
                widget.setCurrentIndex(0)
            widget.setEditable(False)
            widget.currentIndexChanged.connect(self._clear_error)
            return widget
        if parameter.kind == "int":
            widget = QtWidgets.QSpinBox(parent)
            low, high = numeric_bounds(parameter)
            widget.setRange(int(low), int(high))
            widget.setSingleStep(1)
            widget.setValue(int(parameter.default))
        else:
            widget = QtWidgets.QDoubleSpinBox(parent)
            low, high = numeric_bounds(parameter)
            widget.setRange(float(low), float(high))
            widget.setDecimals(6)
            widget.setSingleStep(0.1)
            widget.setValue(float(parameter.default))
        widget.setObjectName(f"parameter_{parameter.name}")
        widget.setKeyboardTracking(False)
        widget.setAccelerated(False)
        widget.valueChanged.connect(self._clear_error)
        return widget

    # -------------------------------------------------------------- public API

    def values(self) -> dict[str, Any]:
        """Return the current typed widget values (not yet validated)."""

        result: dict[str, Any] = {}
        for parameter in self._schema:
            widget = self._widgets[parameter.name]
            if parameter.kind == "bool":
                result[parameter.name] = bool(widget.isChecked())
            elif parameter.kind == "enum":
                data = widget.currentData()
                if data is None:
                    data = parameter.choices[widget.currentIndex()]
                result[parameter.name] = str(data)
            elif parameter.kind == "int":
                result[parameter.name] = int(widget.value())
            else:
                result[parameter.name] = float(widget.value())
        return result

    def validated_values(self) -> dict[str, Any] | None:
        """Return the validated mapping only after a successful Apply once."""

        return dict(self._validated) if self._validated is not None else None

    def set_values(self, values: Mapping[str, Any]) -> None:
        """Set controls from plain values (used by tests, never by a host)."""

        for parameter in self._schema:
            if parameter.name not in values:
                continue
            widget = self._widgets[parameter.name]
            value = values[parameter.name]
            if parameter.kind == "bool":
                widget.setChecked(bool(value))
            elif parameter.kind == "enum":
                try:
                    widget.setCurrentIndex(parameter.choices.index(str(value)))
                except ValueError:
                    continue
            elif parameter.kind == "int":
                widget.setValue(int(value))
            else:
                widget.setValue(float(value))

    # ---------------------------------------------------------------- lifecycle

    def accept(self) -> None:  # noqa: N802 - Qt signature
        """Validate once, then accept; invalid input keeps the dialog open."""

        if self._completed:
            return
        try:
            validated = validate_dialog_values(
                str(getattr(self._spec, "id", "")),
                getattr(self._context, "level", ContextLevel.OBJECT),
                self._schema,
                self.values(),
            )
        except ParameterError as exc:
            self._error.setText(str(exc))
            self._error.show()
            return
        self._validated = validated
        self._completed = True
        super().accept()

    def reject(self) -> None:  # noqa: N802 - Qt signature
        """Cancel without values; repeated calls stay safe."""

        self._validated = None
        self._completed = False
        super().reject()

    def done(self, result: int) -> None:  # noqa: N802 - Qt signature
        _unregister_dialog(self)
        super().done(result)

    def _clear_error(self, *_args: Any) -> None:
        if self._error.isVisible():
            self._error.clear()
            self._error.hide()


def collect_parameters(
    spec: Any,
    context: Any,
    schema: Sequence[ParameterSpec],
    parent: QtWidgets.QWidget | None = None,
) -> dict[str, Any] | None:
    """Show one modal dialog and return plain values, or ``None`` on cancel.

    The callback must never launch a host action; the controller revalidates and
    dispatches after this returns.  An empty schema is a caller error because a
    parameterized action must never fall back to invisible defaults.
    """

    if not schema:
        raise ValueError("collect_parameters requires a non-empty schema")
    dialog = ParameterDialog(spec, context, schema, parent)
    result = dialog.exec()
    if result != QtWidgets.QDialog.DialogCode.Accepted.value:
        return None
    return dialog.validated_values()


__all__ = [
    "ParameterDialog",
    "collect_parameters",
    "close_pending_dialogs",
]

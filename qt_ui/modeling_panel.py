"""Max-parented native modeling tools panel.

The panel is a renderer only:

* level buttons call ``MenuController.invoke`` (never ``host.launch``);
* read-only state comes from ``MenuController.summary``;
* the context-menu button asks the bootstrap to open the shared popup;
* no ``pymxs`` import, host geometry call, numeric transform, live preview, or
  second ``QApplication`` exists here.
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6 import QtCore, QtGui, QtWidgets  # type: ignore[import-not-found]

from ..core.contracts import LEVEL_ORDER, ContextLevel
from .parameter_model import (
    describe_launch_result,
    describe_target,
    enabled_action_ids,
)

_LEVEL_ACTION_IDS = {
    ContextLevel.OBJECT: "context.object",
    ContextLevel.VERTEX: "context.vertex",
    ContextLevel.EDGE: "context.edge",
    ContextLevel.BORDER: "context.border",
    ContextLevel.POLYGON: "context.polygon",
    ContextLevel.ELEMENT: "context.element",
}

_STYLE = """
QWidget#nativeModelingPanel { background: #12151d; }
QLabel#nativePanelTitle { color: #e8ebf2; font-size: 16px; font-weight: 700; }
QLabel#nativePanelSubtitle { color: #f2a33c; font-size: 10px; font-weight: 700; }
QLabel#nativePanelHelp { color: #9aa3b5; font-size: 10px; }
QLabel#nativePanelTarget { color: #c9cfdd; font-size: 11px; background: #0b0d11;
                           border: 1px solid #2a3040; border-radius: 5px;
                           padding: 6px 7px; }
QLabel#nativePanelStatus { color: #9aa3b5; font-size: 10px; }
QLabel#nativePanelResult { color: #c9cfdd; font-size: 11px; }
QPushButton#nativeLevelButton { padding: 5px 8px; }
QPushButton#nativeOpenMenuButton { padding: 6px 10px; font-weight: 600; }
"""


def _assert_gui_thread() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is None:
        raise RuntimeError(
            "ModelingPanel requires an existing QApplication; the prototype "
            "never creates a second one."
        )
    if QtCore.QThread.currentThread() is not app.thread():
        raise RuntimeError("ModelingPanel must run on the Qt GUI thread.")
    return app


class ModelingPanel(QtWidgets.QWidget):
    """Small native-first panel sharing one controller with the popup."""

    menu_requested = QtCore.Signal()

    def __init__(self, controller: Any, parent: QtWidgets.QWidget | None = None) -> None:
        _assert_gui_thread()
        super().__init__(parent)
        self.setObjectName("nativeModelingPanel")
        self.setWindowTitle("Native modeling tools")
        self.setWindowFlag(QtCore.Qt.WindowType.Tool, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setMinimumWidth(390)
        self.setStyleSheet(_STYLE)

        self._controller = controller
        self._disposed = False
        self._refresh_callback: Callable[[], Any] | None = None
        self._notice = (
            "Ready. Choose a level, Refresh context, or Open context menu."
        )
        self._last_result: Any = None
        self._level_buttons: list[QtWidgets.QPushButton] = []
        self._build()
        self._render_current()

    # ------------------------------------------------------------------- build

    def _build(self) -> None:
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(7)

        title = QtWidgets.QLabel("Native modeling", self)
        title.setObjectName("nativePanelTitle")
        outer.addWidget(title)

        subtitle = QtWidgets.QLabel(
            "Native — experimental, not host-validated here", self
        )
        subtitle.setObjectName("nativePanelSubtitle")
        subtitle.setWordWrap(True)
        outer.addWidget(subtitle)

        help_text = QtWidgets.QLabel(
            "In 3ds Max select one unmodified Editable Poly and open the Modify "
            "panel. Choose a sub-object level, select components in the native "
            "viewport, then open the context menu. Numeric actions use a one-shot "
            "parameter dialog; no live preview is performed. Max Undo remains "
            "native.",
            self,
        )
        help_text.setObjectName("nativePanelHelp")
        help_text.setWordWrap(True)
        outer.addWidget(help_text)

        self._target_label = QtWidgets.QLabel("", self)
        self._target_label.setObjectName("nativePanelTarget")
        self._target_label.setWordWrap(True)
        outer.addWidget(self._target_label)

        level_heading = QtWidgets.QLabel("Sub-object level", self)
        level_heading.setObjectName("nativePanelHelp")
        outer.addWidget(level_heading)

        level_row = QtWidgets.QHBoxLayout()
        level_row.setContentsMargins(0, 0, 0, 0)
        level_row.setSpacing(4)
        for level in LEVEL_ORDER:
            action_id = _LEVEL_ACTION_IDS[level]
            button = QtWidgets.QPushButton(level.value.capitalize(), self)
            button.setObjectName("nativeLevelButton")
            button.setProperty("actionId", action_id)
            button.setToolTip(
                f"Set Max subObjectLevel to {level.value} via {action_id}. "
                "This changes the native context; it does not edit geometry."
            )
            button.clicked.connect(
                lambda _checked=False, target=action_id: self._invoke(target)
            )
            level_row.addWidget(button)
            self._level_buttons.append(button)
        outer.addLayout(level_row)

        command_row = QtWidgets.QHBoxLayout()
        command_row.setContentsMargins(0, 0, 0, 0)
        command_row.setSpacing(5)
        self._menu_button = QtWidgets.QPushButton("Open context menu", self)
        self._menu_button.setObjectName("nativeOpenMenuButton")
        self._menu_button.setToolTip(
            "Open the shared latched menu. Parameterized rows use the native "
            "one-shot dialog registered at bootstrap lifetime."
        )
        self._menu_button.clicked.connect(self._request_menu)
        command_row.addWidget(self._menu_button, 1)

        self._refresh_button = QtWidgets.QPushButton("Refresh context", self)
        self._refresh_button.setObjectName("nativeRefreshButton")
        self._refresh_button.setToolTip(
            "Read a fresh host snapshot through the shared controller without "
            "editing geometry."
        )
        self._refresh_button.clicked.connect(self.refresh)
        command_row.addWidget(self._refresh_button, 0)
        outer.addLayout(command_row)

        self._status_label = QtWidgets.QLabel("", self)
        self._status_label.setObjectName("nativePanelStatus")
        self._status_label.setWordWrap(True)
        outer.addWidget(self._status_label)

        self._result_label = QtWidgets.QLabel("", self)
        self._result_label.setObjectName("nativePanelResult")
        self._result_label.setWordWrap(True)
        outer.addWidget(self._result_label)

        outer.addStretch(1)

    # -------------------------------------------------------------- public API

    def set_refresh_callback(self, callback: Callable[[], Any] | None) -> None:
        """Install the bootstrap-owned read-only context probe, or clear it."""

        if callback is not None and not callable(callback):
            raise TypeError("refresh callback must be callable or None")
        self._refresh_callback = callback

    def refresh(self) -> None:
        """Refresh context and read-only state; never edits geometry."""

        if self._disposed:
            return
        callback = self._refresh_callback
        if callback is not None:
            try:
                outcome = callback()
            except Exception as exc:  # a host read failure must stay readable
                self._notice = f"Refresh error: {exc}"
            else:
                if isinstance(outcome, str) and outcome:
                    self._notice = outcome
                else:
                    self._notice = (
                        "Context refreshed through the shared controller "
                        "(read-only host snapshot)."
                    )
        self._render_current()

    def dispose(self) -> None:
        """Idempotently hide and release this panel; never touches the controller."""

        if self._disposed:
            return
        self._disposed = True
        self._refresh_callback = None
        self._level_buttons.clear()
        self.hide()
        self.deleteLater()

    def values_shown(self) -> dict[str, str]:
        """Expose displayed text for lightweight tests and diagnostics."""

        return {
            "target": self._target_label.text(),
            "status": self._status_label.text(),
            "result": self._result_label.text(),
        }

    # ---------------------------------------------------------------- internal

    def _request_menu(self) -> None:
        if not self._disposed:
            self.menu_requested.emit()

    def _invoke(self, action_id: str) -> None:
        if self._disposed:
            return
        self._notice = f"Invoking {action_id}"
        self._render_current()
        try:
            view = self._controller.invoke(action_id)
        except Exception as exc:
            self._notice = f"Invoke error: {exc}"
        else:
            self._notice = str(getattr(view, "status", "") or "Invocation finished.")
        self._render_current()

    def _render_current(self) -> None:
        try:
            summary = self._controller.summary()
        except Exception as exc:
            self._target_label.setText(f"Context summary error: {exc}")
            self._status_label.setText(self._notice)
            self._result_label.clear()
            return
        self._render(summary)

    def _render(self, summary: Any) -> None:
        self._target_label.setText(describe_target(summary))

        enabled = enabled_action_ids(summary)
        phase = str(getattr(summary, "phase", "closed"))
        status = self._notice or str(getattr(summary, "status", "") or "")
        detail = f"Phase: {phase}."
        if enabled:
            detail += f" {len(enabled)} host-enabled action(s) in this context."
        else:
            detail += " No host-enabled action reported for this context."
        self._status_label.setText(f"{status}\n{detail}")

        result = getattr(summary, "last_result", None)
        if result is not None:
            self._last_result = result
        self._result_label.setText(describe_launch_result(self._last_result))

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802 - Qt signature
        # Closing the tool window only hides it; bootstrap keeps the one shared
        # instance for repeated show/uninstall.
        self.hide()
        event.accept()


__all__ = ["ModelingPanel"]

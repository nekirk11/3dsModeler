"""Optional PySide6 popup shared by the Max bootstrap and desktop demo.

Importing this module does not create a ``QApplication``, look up a host, or
import ``pymxs``.  The popup is a thin renderer over the shared Python
controller: it normalizes Qt input into ``MenuEvent`` records and renders the
returned ``MenuView``.  It never calls a host adapter directly.

Frozen public factory (agreed with the Max bridge workstream)::

    MenuPopup(controller, parent=None)
    show_at(global_pos, invocation="latched", trigger_key=None, return_focus=None)
    cancel()
    dispose()

The popup does not claim Max viewport key-release routing or native validation.
"""

from __future__ import annotations

import sys
import time
import weakref
from typing import Any

from PySide6 import QtCore, QtGui, QtWidgets  # type: ignore[import-not-found]

from ..core.catalog import ACTIONS_BY_ID
from ..core.contracts import MenuEvent
from ..core.parameters import ParameterError, parameter_schema

_STYLE = """
QFrame#contextMenuPopup {
    background: #12151d;
    border: 1px solid #333b4d;
    border-radius: 10px;
}
QLabel#menuContext { color: #f2a33c; font-size: 10px; font-weight: 700; letter-spacing: 1px; }
QLabel#menuPhase { color: #8b93a6; font-size: 10px; }
QLabel#menuSearch { color: #9aa3b5; background: #0b0d11; border: 1px solid #2a3040;
                    border-radius: 5px; padding: 5px 7px; font-family: monospace; font-size: 11px; }
QLabel#groupHeading { color: #6c7487; font-size: 9px; font-weight: 700;
                      letter-spacing: 1px; padding: 5px 6px 2px 6px; }
QFrame#menuRow { border-radius: 5px; background: transparent; }
QFrame#menuRow[highlighted="true"] { background: rgba(103, 184, 255, 26); }
QFrame#menuRow[armed="true"] { background: rgba(242, 163, 60, 41); }
QFrame#menuRow[disabled="true"] { background: transparent; }
QLabel#rowLabel { color: #e8ebf2; font-size: 12px; }
QLabel#rowLabel[disabled="true"] { color: #6c7487; }
QLabel#rowReason { color: #d7a27a; font-size: 10px; }
QLabel#rowMeta { color: #5c6374; font-size: 9px; font-family: monospace; }
QLabel#rowKind { color: #6c7487; font-size: 8px; border: 1px solid #2a3040;
                 border-radius: 3px; padding: 1px 4px; }
QLabel#menuEmpty { color: #9aa3b5; font-size: 11px; padding: 14px 8px; }
QLabel#menuStatus { color: #9aa3b5; font-size: 10px; }
QLabel#menuHint { color: #5c6374; font-size: 10px; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { width: 8px; background: transparent; }
QScrollBar::handle:vertical { background: #394154; border-radius: 4px; min-height: 20px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""

_HINT_LATCHED = "Up/Down move · Enter runs · Esc cancels"
_HINT_HELD = "Qt demo: no trigger-release input · Esc cancels"
_HINT_SEARCH = "Backspace edits · Esc cancels"


def _action_uses_parameters(action_id: str) -> bool:
    """Whether any declared level for this action has a parameter schema."""

    spec = ACTIONS_BY_ID.get(action_id)
    if spec is None:
        return False
    for level in spec.allowed_levels:
        try:
            if parameter_schema(action_id, level):
                return True
        except ParameterError:
            continue
    return False


class MenuPopup(QtWidgets.QFrame):
    """One compact, grouped context-menu popup rendered from ``MenuView``."""

    def __init__(self, controller: Any, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("contextMenuPopup")
        self.setWindowFlags(
            QtCore.Qt.WindowType.Popup
            | QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.setFixedWidth(352)
        self.setMaximumHeight(520)
        self.setStyleSheet(_STYLE)

        self._controller = controller
        self._view: Any = None
        self._origin_global = QtCore.QPoint(0, 0)
        self._invocation = "latched"
        self._trigger_key: Any = None
        self._return_focus_ref: weakref.ref | None = None
        self._disposed = False
        self._suppress_hide = 0
        self._last_pointer_key: tuple[Any, ...] | None = None
        self._last_pointer_ms = 0.0
        self._row_widgets: list[QtWidgets.QWidget] = []
        self._signature: Any = None
        self._filter_installed = False
        self._had_focus = False
        self.setMouseTracking(True)

        # Hide and restore focus before the controller dispatches a native
        # command mode.  The hook is cleared on dispose if it is still ours.
        self._dispatch_hook = self._prepare_dispatch
        setter = getattr(controller, "set_pre_dispatch", None)
        if callable(setter):
            setter(self._dispatch_hook)

        self._build_chrome()
        for widget in (self._scroll, self._scroll.viewport(), self._rows_host):
            widget.setMouseTracking(True)

    # ------------------------------------------------------------------ chrome

    def _build_chrome(self) -> None:
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QtWidgets.QWidget(self)
        header_layout = QtWidgets.QHBoxLayout(header)
        header_layout.setContentsMargins(10, 8, 10, 7)
        self._context_label = QtWidgets.QLabel("context", header)
        self._context_label.setObjectName("menuContext")
        self._phase_label = QtWidgets.QLabel("", header)
        self._phase_label.setObjectName("menuPhase")
        header_layout.addWidget(self._context_label)
        header_layout.addStretch(1)
        header_layout.addWidget(self._phase_label)
        outer.addWidget(header)

        self._search_label = QtWidgets.QLabel("", self)
        self._search_label.setObjectName("menuSearch")
        self._search_label.setContentsMargins(10, 0, 10, 0)
        search_wrap = QtWidgets.QWidget(self)
        search_layout = QtWidgets.QVBoxLayout(search_wrap)
        search_layout.setContentsMargins(9, 0, 9, 3)
        search_layout.addWidget(self._search_label)
        outer.addWidget(search_wrap)

        self._scroll = QtWidgets.QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self._rows_host = QtWidgets.QWidget(self._scroll)
        self._rows_layout = QtWidgets.QVBoxLayout(self._rows_host)
        self._rows_layout.setContentsMargins(5, 2, 5, 5)
        self._rows_layout.setSpacing(0)
        self._rows_layout.addStretch(1)
        self._scroll.setWidget(self._rows_host)
        outer.addWidget(self._scroll, 1)

        footer = QtWidgets.QWidget(self)
        footer_layout = QtWidgets.QHBoxLayout(footer)
        footer_layout.setContentsMargins(10, 5, 10, 6)
        self._status_label = QtWidgets.QLabel("", footer)
        self._status_label.setObjectName("menuStatus")
        self._status_label.setWordWrap(True)
        self._hint_label = QtWidgets.QLabel("", footer)
        self._hint_label.setObjectName("menuHint")
        footer_layout.addWidget(self._status_label, 1)
        footer_layout.addWidget(self._hint_label, 0)
        outer.addWidget(footer)

    # -------------------------------------------------------------- public API

    def show_at(
        self,
        global_pos: QtCore.QPoint,
        invocation: str = "latched",
        trigger_key: Any = None,
        return_focus: QtWidgets.QWidget | None = None,
    ) -> Any:
        """Open the shared menu at a global (logical-pixel) position."""

        if self._disposed:
            raise RuntimeError("This MenuPopup has been disposed.")
        app = self._application()
        self._assert_gui_thread(app)
        if invocation not in ("held", "latched"):
            raise ValueError("invocation must be 'held' or 'latched'")

        if return_focus is not None:
            self._return_focus_ref = self._weak_ref(return_focus)
        elif self._return_focus_ref is None or self._return_focus_ref() is None:
            self._return_focus_ref = self._weak_ref(app.focusWidget())

        self._origin_global = QtCore.QPoint(global_pos)
        self._invocation = invocation
        self._trigger_key = trigger_key
        self._last_pointer_key = None
        self._had_focus = False

        time_ms = time.monotonic() * 1000.0
        view = self._controller.open((0.0, 0.0), time_ms, invocation=invocation)
        self._view = view
        if getattr(view, "phase", "closed") == "closed":
            self._hide_without_cancel()
            self._restore_focus()
            return view

        self._render(view)
        self.adjustSize()
        self._position_near(global_pos, app)
        self._install_filter(app)
        self._suppress_hide += 1
        try:
            self.show()
            self.raise_()
            self.activateWindow()
            self.setFocus(QtCore.Qt.FocusReason.PopupFocusReason)
        finally:
            self._suppress_hide -= 1
        return view

    def cancel(self) -> None:
        """Cancel through the controller, then hide and restore focus."""

        if self._disposed:
            return
        view = self._view
        if view is not None and getattr(view, "phase", "closed") != "closed":
            self._send("cancel", key="Escape")
        else:
            self._hide_without_cancel()

    def dispose(self) -> None:
        """Idempotently cancel, hide, and release this prototype widget."""

        if self._disposed:
            return
        try:
            self.cancel()
        except Exception:
            pass
        self._disposed = True
        self._view = None
        self._return_focus_ref = None
        self._row_widgets.clear()
        self._remove_filter()
        clearer = getattr(self._controller, "clear_pre_dispatch", None)
        if callable(clearer):
            clearer(self._dispatch_hook)
        self.hide()
        self.deleteLater()

    # ---------------------------------------------------------------- render

    def _render(self, view: Any) -> None:
        phase = str(getattr(view, "phase", "closed"))
        context = getattr(view, "context", None)
        context_name = getattr(context, "value", context) or "—"
        self._context_label.setText(f"{context_name} context".upper())
        self._phase_label.setText(self._phase_text(phase))

        query = str(getattr(view, "query", "") or "")
        self._search_label.setText(query if query else "Type to search actions")
        self._search_label.setStyleSheet(
            "" if query else "color: #5c6374;"
        )
        self._hint_label.setText(self._hint_text(phase))

        rows = list(getattr(view, "rows", []) or [])
        signature = (
            phase,
            context_name,
            query,
            tuple(
                (row.action_id, row.label, row.group, bool(row.enabled), row.reason)
                for row in rows
            ),
        )
        if signature != self._signature:
            self._rebuild_rows(rows, query)
            self._signature = signature
            self._status_label.setText(str(getattr(view, "status", "") or ""))
        else:
            self._status_label.setText(str(getattr(view, "status", "") or ""))
            self._update_row_state(view)

        self._apply_state_classes(view)
        self._scroll_rows_to_highlight(view)
        self.adjustSize()

    def _rebuild_rows(self, rows: list[Any], query: str) -> None:
        scroll_value = self._scroll.verticalScrollBar().value()
        while self._rows_layout.count():
            item = self._rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._row_widgets = []

        if not rows:
            empty = QtWidgets.QLabel(
                "No matching actions. Backspace clears the search."
                if query
                else "No visible actions in this profile.",
                self._rows_host,
            )
            empty.setObjectName("menuEmpty")
            empty.setWordWrap(True)
            empty.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self._rows_layout.addWidget(empty)
            self._rows_layout.addStretch(1)
            self._scroll.verticalScrollBar().setValue(scroll_value)
            return

        previous_group = None
        for row in rows:
            if row.group != previous_group:
                heading = QtWidgets.QLabel(str(row.group).upper(), self._rows_host)
                heading.setObjectName("groupHeading")
                heading.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
                self._rows_layout.addWidget(heading)
                previous_group = row.group

            frame = QtWidgets.QFrame(self._rows_host)
            frame.setObjectName("menuRow")
            frame.setProperty("actionId", row.action_id)
            frame.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            layout = QtWidgets.QVBoxLayout(frame)
            layout.setContentsMargins(7, 4, 7, 4)
            layout.setSpacing(1)

            top = QtWidgets.QHBoxLayout()
            top.setContentsMargins(0, 0, 0, 0)
            label = QtWidgets.QLabel(str(row.label), frame)
            label.setObjectName("rowLabel")
            label.setProperty("disabled", not bool(row.enabled))
            label.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            top.addWidget(label, 1)
            kind = QtWidgets.QLabel(str(row.execution_kind or "action").upper(), frame)
            kind.setObjectName("rowKind")
            kind.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            top.addWidget(kind, 0)
            layout.addLayout(top)

            meta = QtWidgets.QLabel(str(row.action_id), frame)
            meta.setObjectName("rowMeta")
            meta.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            layout.addWidget(meta)

            if not row.enabled and row.reason:
                reason = QtWidgets.QLabel(str(row.reason), frame)
                reason.setObjectName("rowReason")
                reason.setWordWrap(True)
                reason.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
                layout.addWidget(reason)

            self._rows_layout.addWidget(frame)
            self._row_widgets.append(frame)

        self._rows_layout.addStretch(1)
        self._scroll.verticalScrollBar().setValue(scroll_value)

    def _update_row_state(self, view: Any) -> None:
        # Row backgrounds are property-based; re-polish after arming changes.
        for frame in self._row_widgets:
            self._style_row(frame)

    def _apply_state_classes(self, view: Any) -> None:
        highlighted = getattr(view, "highlighted_id", None)
        armed = getattr(view, "armed_id", None)
        for frame in self._row_widgets:
            action_id = frame.property("actionId")
            frame.setProperty("highlighted", bool(highlighted) and action_id == highlighted)
            frame.setProperty("armed", bool(armed) and action_id == armed)
            self._style_row(frame)

    @staticmethod
    def _style_row(frame: QtWidgets.QWidget) -> None:
        style = frame.style()
        style.unpolish(frame)
        style.polish(frame)
        frame.update()

    def _scroll_rows_to_highlight(self, view: Any) -> None:
        highlighted = getattr(view, "highlighted_id", None)
        if not highlighted:
            return
        for frame in self._row_widgets:
            if frame.property("actionId") == highlighted:
                self._scroll.ensureWidgetVisible(frame, 0, 8)
                return

    @staticmethod
    def _phase_text(phase: str) -> str:
        return {
            "held": "held · no trigger-release input",
            "latched": "latched · click or Enter",
            "search": "search · type or Enter",
        }.get(phase, phase)

    @staticmethod
    def _hint_text(phase: str) -> str:
        if phase == "held":
            return _HINT_HELD
        if phase == "search":
            return _HINT_SEARCH
        return _HINT_LATCHED

    # ------------------------------------------------------------ positioning

    def _position_near(self, global_pos: QtCore.QPoint, app: QtWidgets.QApplication) -> None:
        screen = QtGui.QGuiApplication.screenAt(global_pos)
        if screen is None:
            screen = app.primaryScreen()
        if screen is None:
            self.move(global_pos)
            return
        bounds = screen.availableGeometry()
        width = max(self.sizeHint().width(), self.width())
        height = max(self.sizeHint().height(), self.height())
        x = global_pos.x() + 14
        y = global_pos.y() + 12
        if x + width > bounds.right():
            x = global_pos.x() - width - 14
        if y + height > bounds.bottom():
            y = global_pos.y() - height - 12
        x = max(bounds.left() + 8, min(x, bounds.right() - width - 8))
        y = max(bounds.top() + 8, min(y, bounds.bottom() - height - 8))
        self.move(int(x), int(y))

    # ---------------------------------------------------------------- events

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:  # noqa: N802 - Qt signature
        if self._disposed:
            return
        view = self._view
        if view is None or getattr(view, "phase", "closed") == "closed":
            super().keyPressEvent(event)
            return

        key = event.key()
        if key == QtCore.Qt.Key.Key_Escape:
            if not event.isAutoRepeat():
                self._send("cancel", key="Escape")
            event.accept()
            return

        name = {
            QtCore.Qt.Key.Key_Up: "ArrowUp",
            QtCore.Qt.Key.Key_Down: "ArrowDown",
            QtCore.Qt.Key.Key_Return: "Enter",
            QtCore.Qt.Key.Key_Enter: "Enter",
            QtCore.Qt.Key.Key_Backspace: "Backspace",
            QtCore.Qt.Key.Key_Delete: "Delete",
            QtCore.Qt.Key.Key_Home: "Home",
            QtCore.Qt.Key.Key_End: "End",
        }.get(key)
        if name is not None:
            self._send("key_down", key=name, auto_repeat=event.isAutoRepeat())
            event.accept()
            return

        modifiers = event.modifiers()
        blocked = (
            QtCore.Qt.KeyboardModifier.ControlModifier
            | QtCore.Qt.KeyboardModifier.AltModifier
            | QtCore.Qt.KeyboardModifier.MetaModifier
        )
        text = event.text()
        if text and text.isprintable() and not (modifiers & blocked):
            self._send(
                "key_down",
                key=text if len(text) == 1 else "",
                text=text,
                auto_repeat=event.isAutoRepeat(),
            )
            event.accept()
            return
        super().keyPressEvent(event)

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:  # noqa: N802
        if self._disposed:
            return super().eventFilter(watched, event)
        if event.type() == QtCore.QEvent.Type.MouseMove:
            if self.isVisible() and isinstance(watched, QtWidgets.QWidget):
                mouse = event
                self._pointer_move(mouse.globalPosition().toPoint())
        elif isinstance(watched, QtWidgets.QWidget) and (
            watched is self or self.isAncestorOf(watched)
        ):
            if event.type() == QtCore.QEvent.Type.MouseButtonRelease:
                mouse = event
                if (
                    mouse.button() == QtCore.Qt.MouseButton.LeftButton
                    and self._view is not None
                    and getattr(self._view, "phase", "closed") != "closed"
                ):
                    row = self._row_at_global(mouse.globalPosition().toPoint())
                    if row is not None:
                        self._send("click_row", action_id=row.property("actionId"))
                        return True
        return super().eventFilter(watched, event)

    def hideEvent(self, event: QtGui.QHideEvent) -> None:  # noqa: N802 - Qt signature
        if self._suppress_hide == 0 and self._view is not None:
            if getattr(self._view, "phase", "closed") != "closed":
                self._send("outside_click", x=0, y=0)
        super().hideEvent(event)

    def focusInEvent(self, event: QtGui.QFocusEvent) -> None:  # noqa: N802 - Qt signature
        self._had_focus = True
        super().focusInEvent(event)

    def focusOutEvent(self, event: QtGui.QFocusEvent) -> None:  # noqa: N802 - Qt signature
        if self._had_focus and self._suppress_hide == 0 and self._view is not None:
            if getattr(self._view, "phase", "closed") != "closed":
                self._send("focus_lost")
        super().focusOutEvent(event)

    # ---------------------------------------------------------------- plumbing

    def _send(
        self,
        kind: str,
        *,
        x: float = 0.0,
        y: float = 0.0,
        action_id: str | None = None,
        text: str = "",
        key: str = "",
        auto_repeat: bool = False,
    ) -> Any:
        view = self._view
        if view is None or getattr(view, "phase", "closed") == "closed":
            return view
        event = MenuEvent(
            session_id=str(view.session_id),
            sequence=int(view.sequence) + 1,
            kind=kind,
            time_ms=time.monotonic() * 1000.0,
            x=float(x),
            y=float(y),
            action_id=action_id,
            text=text,
            key=key,
            auto_repeat=auto_repeat,
        )
        new_view = self._controller.handle(event)
        self._view = new_view
        if getattr(new_view, "phase", "closed") == "closed":
            self._finish_close()
        else:
            self._render(new_view)
        return new_view

    def _pointer_move(self, global_pos: QtCore.QPoint) -> None:
        row = self._row_at_global(global_pos)
        action_id = row.property("actionId") if row is not None else None
        relative = global_pos - self._origin_global
        inside = False
        profile = getattr(self._controller, "profile", None) or getattr(
            self._controller, "_profile", None
        )
        radius = getattr(profile, "neutral_px", 12)
        try:
            inside = (relative.x() ** 2 + relative.y() ** 2) <= int(radius) ** 2
        except (TypeError, ValueError):
            inside = False
        key = (action_id, bool(inside))
        stamp = time.monotonic()
        if key == self._last_pointer_key and (stamp - self._last_pointer_ms) < 0.045:
            return
        self._last_pointer_key = key
        self._last_pointer_ms = stamp
        self._send(
            "pointer_move",
            x=relative.x(),
            y=relative.y(),
            action_id=None if inside else action_id,
        )

    def _row_at_global(self, global_pos: QtCore.QPoint) -> QtWidgets.QWidget | None:
        for frame in self._row_widgets:
            local = frame.mapFromGlobal(global_pos)
            if frame.rect().contains(local) and frame.visibleRegion().contains(local):
                return frame
        return None

    def _finish_close(self) -> None:
        self._hide_without_cancel()
        self._restore_focus()

    def _prepare_dispatch(self, action_id: str) -> None:
        """Close the popup before the controller's parameter/launch phase.

        Parameter-free actions restore the originating widget before native
        dispatch so the popup never owns focus while a mode is entered.
        Parameterized actions keep the focus reference for the final restore,
        because the modal parameter dialog must receive focus first.  In both
        cases the local view is marked closed so a late hide/focus-loss event
        cannot invalidate the controller's accepted request.
        """

        self._hide_without_cancel()
        view_getter = getattr(self._controller, "view", None)
        if callable(view_getter):
            try:
                self._view = view_getter()
            except Exception:
                self._view = None
        else:
            self._view = None
        if not _action_uses_parameters(action_id):
            self._restore_focus()

    def _hide_without_cancel(self) -> None:
        self._remove_filter()
        self._suppress_hide += 1
        try:
            self.hide()
        finally:
            self._suppress_hide -= 1

    def _install_filter(self, app: QtWidgets.QApplication | None = None) -> None:
        if self._filter_installed:
            return
        app = app or QtWidgets.QApplication.instance()
        if app is None:
            return
        app.installEventFilter(self)
        self._filter_installed = True

    def _remove_filter(self) -> None:
        if not self._filter_installed:
            return
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self._filter_installed = False

    def _restore_focus(self) -> None:
        ref = self._return_focus_ref
        self._return_focus_ref = None
        if ref is None:
            return
        try:
            widget = ref()
        except TypeError:
            return
        if widget is None:
            return
        try:
            if not widget.isVisible():
                return
            widget.setFocus(QtCore.Qt.FocusReason.OtherFocusReason)
        except RuntimeError:
            return

    # ----------------------------------------------------------------- helpers

    @staticmethod
    def _weak_ref(widget: Any) -> weakref.ref | None:
        if widget is None:
            return None
        try:
            return weakref.ref(widget)
        except TypeError:
            return None

    @staticmethod
    def _application() -> QtWidgets.QApplication:
        app = QtWidgets.QApplication.instance()
        if app is None:
            raise RuntimeError("MenuPopup requires an existing QApplication.")
        return app

    @staticmethod
    def _assert_gui_thread(app: QtWidgets.QApplication) -> None:
        if QtCore.QThread.currentThread() is not app.thread():
            raise RuntimeError("MenuPopup must be used on the Qt GUI thread.")


class _DemoWindow(QtWidgets.QMainWindow):
    """Tiny optional desktop shell; only used by ``python -m ...qt_ui``."""

    def __init__(self) -> None:
        super().__init__()
        from ..core.controller import MenuController
        from ..core.profile import default_profile
        from ..core.simulated import SCENARIO_NAMES, SimulatedHost

        self.setWindowTitle("Context Menu Lab — Qt simulation")
        self.resize(760, 520)
        self.host = SimulatedHost("editable_poly_vertex")
        self.controller = MenuController(self.host, default_profile())
        self.popup = MenuPopup(self.controller, self)

        central = QtWidgets.QWidget(self)
        layout = QtWidgets.QVBoxLayout(central)
        title = QtWidgets.QLabel("SIMULATED Qt renderer — shared Python controller", central)
        title.setStyleSheet("font-size: 16px; font-weight: 600; color: #e8ebf2;")
        layout.addWidget(title)
        note = QtWidgets.QLabel(
            "This optional window proves the popup factory on a display-capable "
            "machine. It does not validate Max input routing or native bindings.",
            central,
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #9aa3b5;")
        layout.addWidget(note)

        row = QtWidgets.QHBoxLayout()
        self._scenario = QtWidgets.QComboBox(central)
        self._scenario.addItems(SCENARIO_NAMES)
        self._scenario.setCurrentText("editable_poly_vertex")
        self._scenario.currentTextChanged.connect(self.host.set_scenario)
        row.addWidget(QtWidgets.QLabel("Scenario:", central))
        row.addWidget(self._scenario, 1)
        layout.addLayout(row)

        open_button = QtWidgets.QPushButton("Open latched menu here", central)
        open_button.clicked.connect(self._open_popup)
        layout.addWidget(open_button)
        layout.addStretch(1)
        self.setCentralWidget(central)
        self.setStyleSheet("QMainWindow { background: #0e1014; } QWidget { font-size: 12px; }")

    def _open_popup(self) -> None:
        self.popup.show_at(QtGui.QCursor.pos(), invocation="latched", return_focus=self)


def main(argv: list[str] | None = None) -> int:
    """Run the optional desktop simulation.  Never imports pymxs."""

    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(argv if argv is not None else sys.argv)
    window = _DemoWindow()
    window.show()
    return int(app.exec())


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

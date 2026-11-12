"""Search palette tests: source contracts + a minimal fake-Qt harness.

The fake binding lets the focus, ShortcutOverride and dispatch lifecycle run on
hosts without PySide6.  When PySide6 *is* installed one offscreen smoke test
also runs; otherwise it is skipped with an explicit reason.
"""

from __future__ import annotations

import ast
import os
import unittest
from dataclasses import dataclass, field, replace
from enum import IntFlag
from pathlib import Path
from types import SimpleNamespace
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:  # Real runtime assertions are optional; source tests always run.
    from PySide6 import QtCore as _RealQtCore  # type: ignore[import-not-found]
    from PySide6 import QtWidgets as _RealQtWidgets  # type: ignore[import-not-found]
except Exception as exc:  # pragma: no cover - exercised on this host
    REAL_QT_ERROR = f"{type(exc).__name__}: {exc}"
else:
    REAL_QT_ERROR = ""

from max2025_menu_prototype.qt_ui import palette as palette_module
from max2025_menu_prototype.qt_ui import palette_model

ROOT = Path(__file__).resolve().parents[1]

# --------------------------------------------------------------------- fakes


class _WindowType(IntFlag):
    Popup = 1
    FramelessWindowHint = 2
    NoDropShadowWindowHint = 4
    Dialog = 8


class _WidgetAttribute(IntFlag):
    WA_DeleteOnClose = 1
    WA_TransparentForMouseEvents = 2


class _FocusPolicy(IntFlag):
    NoFocus = 0
    StrongFocus = 1


class _FocusReason(IntFlag):
    PopupFocusReason = 1
    OtherFocusReason = 2


class _Modifier(IntFlag):
    ShiftModifier = 1
    ControlModifier = 2
    AltModifier = 4
    MetaModifier = 8


class _Key(IntFlag):
    Key_Space = 0x20
    Key_0 = 0x30
    Key_1 = 0x31
    Key_2 = 0x32
    Key_3 = 0x33
    Key_4 = 0x34
    Key_5 = 0x35
    Key_A = 0x41
    Key_C = 0x43
    Key_E = 0x45
    Key_R = 0x52
    Key_T = 0x54
    Key_W = 0x57
    Key_X = 0x58
    Key_Escape = 0x01000000
    Key_Tab = 0x01000001
    Key_Backtab = 0x01000002
    Key_Backspace = 0x01000003
    Key_Return = 0x01000004
    Key_Enter = 0x01000005
    Key_Delete = 0x01000007
    Key_Home = 0x01000010
    Key_End = 0x01000011
    Key_Left = 0x01000012
    Key_Up = 0x01000013
    Key_Right = 0x01000014
    Key_Down = 0x01000015


class _MouseButton(IntFlag):
    LeftButton = 1
    RightButton = 2


class _ScrollBarPolicy(IntFlag):
    ScrollBarAlwaysOff = 1


class _EventType(IntFlag):
    ShortcutOverride = 1
    KeyPress = 2
    FocusOut = 3
    WindowDeactivate = 4
    FocusIn = 5
    WindowTitleChange = 6
    KeyRelease = 7
    MouseButtonPress = 8


class _Qt:
    WindowType = _WindowType
    WidgetAttribute = _WidgetAttribute
    FocusPolicy = _FocusPolicy
    FocusReason = _FocusReason
    KeyboardModifier = _Modifier
    Key = _Key
    MouseButton = _MouseButton
    ScrollBarPolicy = _ScrollBarPolicy


class _Event:
    Type = _EventType

    def __init__(self, event_type: _EventType = _EventType.FocusOut) -> None:
        self._type = event_type
        self._accepted = True

    def type(self) -> _EventType:
        return self._type

    def accept(self) -> None:
        self._accepted = True

    def ignore(self) -> None:
        self._accepted = False

    def isAccepted(self) -> bool:  # noqa: N802 - Qt spelling
        return self._accepted


class _KeyEvent(_Event):
    def __init__(
        self,
        key: Any,
        text: str = "",
        modifiers: int = 0,
        auto_repeat: bool = False,
        event_type: _EventType = _EventType.KeyPress,
    ) -> None:
        super().__init__(event_type)
        self._key = key
        self._text = text
        self._modifiers = _Modifier(modifiers)
        self._auto_repeat = auto_repeat

    def key(self) -> Any:
        return self._key

    def text(self) -> str:
        return self._text

    def modifiers(self) -> _Modifier:
        return self._modifiers

    def isAutoRepeat(self) -> bool:  # noqa: N802 - Qt spelling
        return self._auto_repeat


class _MouseEvent(_Event):
    def __init__(
        self,
        button: Any = _MouseButton.LeftButton,
        global_position: Any = None,
    ) -> None:
        super().__init__(_EventType.MouseButtonPress)
        self._button = button
        self._global_position = (
            _PointF() if global_position is None else global_position
        )

    def button(self) -> Any:
        return self._button

    def globalPosition(self) -> Any:  # noqa: N802 - Qt spelling
        return self._global_position


class _Point:
    def __init__(self, x: int = 0, y: int = 0) -> None:
        self._x = int(x)
        self._y = int(y)

    def x(self) -> int:
        return self._x

    def y(self) -> int:
        return self._y


class _PointF:
    """Qt 6 QPointF stand-in: only sane after .toPoint()."""

    def __init__(self, x: float = 0.0, y: float = 0.0) -> None:
        self._x = float(x)
        self._y = float(y)

    def toPoint(self) -> _Point:  # noqa: N802 - Qt spelling
        return _Point(int(self._x), int(self._y))


class _Size:
    def __init__(self, width: int, height: int) -> None:
        self._width = width
        self._height = height

    def width(self) -> int:
        return self._width

    def height(self) -> int:
        return self._height


class _Rect:
    def __init__(self, x: int, y: int, width: int, height: int) -> None:
        self._x = x
        self._y = y
        self._width = width
        self._height = height

    def contains(self, point: Any) -> bool:
        if not isinstance(point, _Point):
            raise TypeError(
                "QPoint expected, got %s (call toPoint() first)"
                % type(point).__name__
            )
        return (
            self._x <= point.x() < self._x + self._width
            and self._y <= point.y() < self._y + self._height
        )


class _Style:
    def unpolish(self, widget: Any) -> None:
        return None

    def polish(self, widget: Any) -> None:
        return None


_STYLE = _Style()


class _Signal:
    def __init__(self, *types: Any) -> None:
        self._types = types
        self._slots: list[Any] = []

    def __set_name__(self, owner: Any, name: str) -> None:
        self._name = name

    def __get__(self, instance: Any, owner: Any = None) -> Any:
        if instance is None:
            return self
        key = "_bound_signal_" + getattr(self, "_name", "signal")
        bound = instance.__dict__.get(key)
        if bound is None:
            bound = _Signal(*self._types)
            instance.__dict__[key] = bound
        return bound

    def connect(self, slot: Any) -> None:
        self._slots.append(slot)

    def emit(self, *args: Any) -> None:
        for slot in list(self._slots):
            slot(*args)


_THREAD = object()


class _Thread:
    @staticmethod
    def currentThread() -> Any:  # noqa: N802 - Qt spelling
        return _THREAD


class _Timer:
    pending: list[Any] = []

    @classmethod
    def singleShot(cls, milliseconds: int, callback: Any) -> None:  # noqa: N802
        cls.pending.append(callback)

    @classmethod
    def flush(cls) -> None:
        callbacks = list(cls.pending)
        cls.pending.clear()
        for callback in callbacks:
            callback()


class _ScrollBar:
    def __init__(self) -> None:
        self._value = 0

    def value(self) -> int:
        return self._value

    def setValue(self, value: int) -> None:  # noqa: N802
        self._value = int(value)


class _Widget:
    def __init__(self, parent: Any = None) -> None:
        self._parent = parent
        self._children: list[Any] = []
        if parent is not None and hasattr(parent, "_children"):
            parent._children.append(self)
        self._hidden = False
        self._shown = parent is None
        self._focus = False
        self._enabled = True
        self._deleted = False
        self._object_name = ""
        self._properties: dict[str, Any] = {}
        self._tooltip = ""
        self._fixed_width = 0
        self._max_height = 0
        self._checkable = False
        self._checked = False
        self._position: Any = None

    # identity / naming
    def setObjectName(self, name: str) -> None:  # noqa: N802
        self._object_name = str(name)

    def objectName(self) -> str:  # noqa: N802
        return self._object_name

    def setWindowTitle(self, title: str) -> None:  # noqa: N802
        self._title = str(title)

    def setWindowFlags(self, flags: Any) -> None:  # noqa: N802
        self._flags = flags

    def setAttribute(self, attribute: Any, value: bool = True) -> None:  # noqa: N802
        return None

    def setFocusPolicy(self, policy: Any) -> None:  # noqa: N802
        self._policy = policy

    def setFixedWidth(self, width: int) -> None:  # noqa: N802
        self._fixed_width = int(width)

    def setMaximumHeight(self, height: int) -> None:  # noqa: N802
        self._max_height = int(height)

    def setMouseTracking(self, enabled: bool) -> None:  # noqa: N802
        return None

    def setStyleSheet(self, style: str) -> None:  # noqa: N802
        self._stylesheet = str(style)

    def setToolTip(self, text: str) -> None:  # noqa: N802
        self._tooltip = str(text)

    def setProperty(self, name: str, value: Any) -> None:  # noqa: N802
        self._properties[str(name)] = value

    def property(self, name: str) -> Any:
        return self._properties.get(str(name))

    def setCheckable(self, value: bool) -> None:  # noqa: N802
        self._checkable = bool(value)

    def setChecked(self, value: bool) -> None:  # noqa: N802
        self._checked = bool(value)

    def isChecked(self) -> bool:  # noqa: N802
        return self._checked

    def setEnabled(self, value: bool) -> None:  # noqa: N802
        self._enabled = bool(value)

    def isEnabled(self) -> bool:  # noqa: N802
        return self._enabled

    # hierarchy
    def parentWidget(self) -> Any:  # noqa: N802
        return self._parent

    def isAncestorOf(self, other: Any) -> bool:  # noqa: N802
        current = other
        while current is not None:
            if current is self:
                return True
            current = current.parentWidget()
        return False

    # visibility
    def show(self) -> None:
        self._hidden = False
        self._shown = True

    def hide(self) -> None:
        was_visible = self.isVisible()
        self._hidden = True
        self._shown = False
        if was_visible:
            self.hideEvent(_Event())

    def isVisible(self) -> bool:  # noqa: N802
        if self._hidden:
            return False
        if self._parent is not None:
            return self._parent.isVisible()
        return self._shown

    def raise_(self) -> None:
        return None

    def activateWindow(self) -> None:  # noqa: N802
        return None

    def adjustSize(self) -> None:  # noqa: N802
        return None

    # focus
    def setFocus(self, reason: Any = None) -> None:  # noqa: N802
        app = _Application.instance()
        if app is not None:
            app._set_focus(self)
        self._focus = True

    def hasFocus(self) -> bool:  # noqa: N802
        app = _Application.instance()
        return app is not None and app.focusWidget() is self

    # geometry
    def width(self) -> int:
        return self._fixed_width or 460

    def height(self) -> int:
        return 300

    def sizeHint(self) -> _Size:  # noqa: N802
        return _Size(self.width(), self.height())

    def rect(self) -> _Rect:  # noqa: N802
        return _Rect(0, 0, self.width(), self.height())

    def frameGeometry(self) -> _Rect:  # noqa: N802 - Qt spelling
        return _Rect(0, 0, self.width(), self.height())

    def move(self, x: Any, y: Any = None) -> None:
        self._position = x if y is None else _Point(x, y)

    # lifecycle / painting
    def deleteLater(self) -> None:  # noqa: N802
        self._deleted = True

    def style(self) -> _Style:
        return _STYLE

    def update(self) -> None:
        return None

    def changeEvent(self, event: Any) -> None:  # noqa: N802
        return None

    def hideEvent(self, event: Any) -> None:  # noqa: N802
        return None

    def keyPressEvent(self, event: Any) -> None:  # noqa: N802
        return None

    def mouseReleaseEvent(self, event: Any) -> None:  # noqa: N802
        return None


class _Frame(_Widget):
    class Shape:
        NoFrame = 0

    def setWindowTitle(self, title: str) -> None:  # noqa: N802
        super().setWindowTitle(title)
        # Real PySide6 delivers a synchronous WindowTitleChange changeEvent
        # from inside SearchPalette.__init__, before its attributes exist.
        self.changeEvent(_Event(_EventType.WindowTitleChange))


class _Label(_Widget):
    def __init__(self, text: str = "", parent: Any = None) -> None:
        super().__init__(parent)
        self._text = str(text)

    def setText(self, text: str) -> None:  # noqa: N802
        self._text = str(text)

    def text(self) -> str:
        return self._text

    def setWordWrap(self, enabled: bool) -> None:  # noqa: N802
        return None


class _LineEdit(_Widget):
    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._text = ""
        self._placeholder = ""
        self._cursor = 0
        self.textEdited = _Signal()

    def setPlaceholderText(self, text: str) -> None:  # noqa: N802
        self._placeholder = str(text)

    def text(self) -> str:
        return self._text

    def setText(self, text: str) -> None:  # noqa: N802
        self._text = str(text)
        self._cursor = len(self._text)

    def setCursorPosition(self, position: int) -> None:  # noqa: N802
        self._cursor = int(position)

    def cursorPosition(self) -> int:  # noqa: N802
        return self._cursor

    def keyPressEvent(self, event: Any) -> None:  # noqa: N802
        if event.key() == _Key.Key_Backspace:
            self._text = self._text[:-1]
            self.textEdited.emit(self._text)
            return
        if event.modifiers() & (_Modifier.ControlModifier | _Modifier.AltModifier):
            return
        text = event.text()
        if text and all(ord(character) >= 32 for character in text):
            self._text += text
            self._cursor = len(self._text)
            self.textEdited.emit(self._text)


class _PushButton(_Widget):
    def __init__(self, text: str = "", parent: Any = None) -> None:
        super().__init__(parent)
        self._text = str(text)
        self.clicked = _Signal()

    def setText(self, text: str) -> None:  # noqa: N802
        self._text = str(text)

    def text(self) -> str:
        return self._text

    def click(self) -> None:
        self.clicked.emit()

    def mouseReleaseEvent(self, event: Any) -> None:  # noqa: N802
        if event.button() == _MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _ScrollArea(_Frame):
    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._widget = None
        self._scrollbar = _ScrollBar()

    def setWidgetResizable(self, value: bool) -> None:  # noqa: N802
        return None

    def setHorizontalScrollBarPolicy(self, policy: Any) -> None:  # noqa: N802
        return None

    def setFrameShape(self, shape: Any) -> None:  # noqa: N802
        return None

    def setWidget(self, widget: Any) -> None:  # noqa: N802
        self._widget = widget

    def ensureWidgetVisible(self, widget: Any, x: int = 0, y: int = 0) -> None:  # noqa: N802
        return None

    def verticalScrollBar(self) -> _ScrollBar:  # noqa: N802
        return self._scrollbar


class _LayoutItem:
    def __init__(self, widget: Any = None, layout: Any = None) -> None:
        self._widget = widget
        self._layout = layout

    def widget(self) -> Any:
        return self._widget

    def layout(self) -> Any:
        return self._layout


class _BoxLayout:
    def __init__(self, parent: Any = None) -> None:
        self._items: list[_LayoutItem] = []

    def addWidget(self, widget: Any, stretch: int = 0) -> None:  # noqa: N802
        self._items.append(_LayoutItem(widget=widget))

    def addLayout(self, layout: Any, stretch: int = 0) -> None:  # noqa: N802
        self._items.append(_LayoutItem(layout=layout))

    def addStretch(self, stretch: int = 0) -> None:  # noqa: N802
        self._items.append(_LayoutItem())

    def setContentsMargins(self, *args: Any) -> None:  # noqa: N802
        return None

    def setSpacing(self, spacing: int) -> None:  # noqa: N802
        return None

    def count(self) -> int:
        return len(self._items)

    def takeAt(self, index: int) -> Any:  # noqa: N802
        return self._items.pop(index)


class _VBoxLayout(_BoxLayout):
    pass


class _HBoxLayout(_BoxLayout):
    pass


class _Application:
    _instance: Any = None

    def __init__(self) -> None:
        self._filters: list[Any] = []
        self._focus: Any = None
        self._active: Any = None
        self._modifiers = _Modifier(0)

    @classmethod
    def reset(cls) -> Any:
        cls._instance = cls()
        return cls._instance

    @classmethod
    def instance(cls) -> Any:
        return cls._instance

    @classmethod
    def keyboardModifiers(cls) -> Any:  # noqa: N802
        if cls._instance is None:
            return _Modifier(0)
        return cls._instance._modifiers

    def thread(self) -> Any:
        return _THREAD

    def installEventFilter(self, watcher: Any) -> None:  # noqa: N802
        self._filters.append(watcher)

    def removeEventFilter(self, watcher: Any) -> None:  # noqa: N802
        if watcher in self._filters:
            self._filters.remove(watcher)

    def focusWidget(self) -> Any:  # noqa: N802
        return self._focus

    def activeWindow(self) -> Any:  # noqa: N802
        return self._active

    def primaryScreen(self) -> Any:  # noqa: N802
        return None

    def _set_focus(self, widget: Any) -> None:
        if self._focus is not None and self._focus is not widget:
            self._focus._focus = False
        self._focus = widget


class _Cursor:
    @staticmethod
    def pos() -> _Point:
        return _Point(0, 0)


class _GuiApplication:
    @staticmethod
    def screenAt(point: Any) -> Any:  # noqa: N802
        return None


def _make_qt() -> tuple[Any, Any, Any]:
    qtcore = SimpleNamespace(
        Qt=_Qt,
        Signal=_Signal,
        QEvent=_Event,
        QPoint=_Point,
        QTimer=_Timer,
        QObject=_Widget,
        QThread=_Thread,
    )
    qtgui = SimpleNamespace(
        QCursor=_Cursor,
        QGuiApplication=_GuiApplication,
        QKeyEvent=_KeyEvent,
    )
    qtwidgets = SimpleNamespace(
        QFrame=_Frame,
        QWidget=_Widget,
        QLabel=_Label,
        QLineEdit=_LineEdit,
        QPushButton=_PushButton,
        QScrollArea=_ScrollArea,
        QVBoxLayout=_VBoxLayout,
        QHBoxLayout=_HBoxLayout,
        QApplication=_Application,
    )
    return qtcore, qtgui, qtwidgets


# ------------------------------------------------------------------ fixtures


@dataclass(frozen=True)
class FakeResult:
    status: str
    message: str = ""


@dataclass(frozen=True)
class FakeRow:
    row_id: str
    label: str = "Bevel"
    group: str = "Modeling"
    enabled: bool = True
    reason: str = ""
    pivot_context: str | None = None
    system_command: str | None = None


@dataclass(frozen=True)
class FakeView:
    session_id: str = "session-1"
    generation: int = 1
    context: str = "edge"
    selection_count: int = 2
    query: str = ""
    rows: tuple[FakeRow, ...] = field(default_factory=tuple)
    highlighted_row_id: str | None = "bevel"
    status: str = ""
    busy: bool = False
    last_result: Any = None
    pending_confirmation: Any = None


@dataclass(frozen=True)
class FakePending:
    token: str
    message: str = (
        "Collapse the selected node's modifier stack into an Editable Poly base?"
    )
    destructive_scope: str = "Only the selected node is targeted."


def _default_rows() -> tuple[FakeRow, ...]:
    return (
        FakeRow("bevel", label="Bevel", pivot_context="border"),
        FakeRow(
            "weld",
            label="Weld",
            enabled=False,
            reason="Select edges first",
        ),
    )


class FakeController:
    """Duck-typed stand-in for ``PaletteController``."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.hook: Any = None
        self.open_count = 0
        self.activate_result: Any = None
        self.pre_dispatch_on_activate = False
        self._view = FakeView(rows=_default_rows())

    def _record(self, *call: Any) -> None:
        self.calls.append(call)

    def count(self, name: str) -> int:
        return sum(1 for call in self.calls if call and call[0] == name)

    def names(self) -> list[str]:
        return [str(call[0]) for call in self.calls]

    def open(self) -> FakeView:
        self.open_count += 1
        self._record("open")
        return self._view

    def view(self) -> FakeView:
        self._record("view")
        return self._view

    def mutate(self, **changes: Any) -> FakeView:
        self._view = replace(self._view, **changes)
        return self._view

    def set_query(self, text: str) -> FakeView:
        self._record("set_query", text)
        return self.mutate(query=text)

    def navigate(self, delta: int) -> FakeView:
        self._record("navigate", delta)
        enabled = [row.row_id for row in self._view.rows if row.enabled]
        if not enabled:
            return self._view
        current = self._view.highlighted_row_id
        index = enabled.index(current) if current in enabled else 0
        index = (index + int(delta)) % len(enabled)
        return self.mutate(highlighted_row_id=enabled[index])

    def switch_context(self, context: str, *, convert_selection: bool = False) -> FakeView:
        self._record("switch_context", context, bool(convert_selection))
        return self.mutate(
            context=context,
            generation=self._view.generation + 1,
            pending_confirmation=None,
            last_result=None,
            status=f"{context} context",
        )

    def activate(self, row_id: str, *, session_id: Any, generation: Any) -> Any:
        self._record("activate", row_id, session_id, generation)
        if self.pre_dispatch_on_activate and self.hook is not None:
            self.hook()
        if self.activate_result is not None:
            self._view = self.activate_result
            return self._view
        self._view = replace(
            self._view,
            rows=(),
            highlighted_row_id=None,
            last_result=FakeResult("launched", "mode entry only"),
            status="Launched.",
        )
        return self._view

    def confirm(self, token: str, accepted: bool) -> FakeView:
        self._record("confirm", token, bool(accepted))
        return self.mutate(
            pending_confirmation=None,
            generation=self._view.generation + 1,
            last_result=(
                None if accepted else FakeResult("cancelled", "Confirmation declined.")
            ),
            status="confirmation declined" if not accepted else "confirmation accepted",
        )

    def cancel(self, reason: str = "") -> FakeView:
        self._record("cancel", reason)
        return self._view

    def set_pre_dispatch(self, callback: Any) -> None:
        self._record("set_pre_dispatch")
        self.hook = callback

    def clear_pre_dispatch(self, callback: Any = None) -> None:
        self._record("clear_pre_dispatch")
        if callback is None or callback is self.hook:
            self.hook = None

    def trigger_pre_dispatch(self) -> None:
        if self.hook is not None:
            self.hook()


_KEY_NAMES = {
    "Escape": _Key.Key_Escape,
    "Tab": _Key.Key_Tab,
    "Backtab": _Key.Key_Backtab,
    "Backspace": _Key.Key_Backspace,
    "Return": _Key.Key_Return,
    "Enter": _Key.Key_Enter,
    "Delete": _Key.Key_Delete,
    "Left": _Key.Key_Left,
    "Right": _Key.Key_Right,
    "Up": _Key.Key_Up,
    "Down": _Key.Key_Down,
    "Home": _Key.Key_Home,
    "End": _Key.Key_End,
    "Space": _Key.Key_Space,
}


def _key_for(token: str) -> Any:
    if token in _KEY_NAMES:
        return _KEY_NAMES[token]
    if len(token) == 1:
        return _Key(ord(token.upper()))
    raise AssertionError(f"unknown key token {token!r}")


# --------------------------------------------------------------------- tests


class FakeQtPaletteTests(unittest.TestCase):
    def setUp(self) -> None:
        _Timer.pending.clear()
        self.app = _Application.reset()
        self.qtcore, self.qtgui, self.qtwidgets = _make_qt()
        self.Palette = palette_module.build_search_palette_class(
            self.qtcore, self.qtgui, self.qtwidgets
        )
        self.controller = FakeController()
        self.palette = None
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        if self.palette is not None:
            try:
                self.palette.dispose()
            except Exception:
                pass
        _Timer.pending.clear()
        _Application._instance = None

    def make(self, return_focus: Any = None) -> Any:
        self.palette = self.Palette(self.controller, None)
        self.palette.show_at(_Point(50, 50), return_focus=return_focus)
        return self.palette

    def key_event(
        self,
        token: str,
        text: str = "",
        modifiers: int = 0,
        auto_repeat: bool = False,
        event_type: _EventType = _EventType.KeyPress,
    ) -> _KeyEvent:
        return _KeyEvent(
            _key_for(token),
            text,
            modifiers,
            auto_repeat,
            event_type,
        )

    def shortcut_override(self, token: str, text: str = "", modifiers: int = 0) -> _KeyEvent:
        event = self.key_event(
            token, text, modifiers, event_type=_EventType.ShortcutOverride
        )
        handled = False
        for watcher in list(self.app._filters):
            if watcher.eventFilter(self.palette._input, event):
                handled = True
                break
        self.assertTrue(handled, f"ShortcutOverride for {token!r} was not accepted")
        self.assertTrue(event.isAccepted())
        return event

    def press(self, token: str, text: str = "", modifiers: int = 0, auto_repeat: bool = False) -> bool:
        event = self.key_event(token, text, modifiers, auto_repeat)
        for watcher in list(self.app._filters):
            if watcher.eventFilter(self.palette._input, event):
                return True
        if event.type() == _EventType.KeyPress:
            self.palette._input.keyPressEvent(event)
        return False

    def last_call(self, name: str) -> Any:
        for call in reversed(self.controller.calls):
            if call and call[0] == name:
                return call
        return None

    # ------------------------------------------------------------- rendering

    def test_init_survives_synchronous_change_event_from_set_window_title(self) -> None:
        palette = self.Palette(self.controller, None)
        self.palette = palette

        self.assertFalse(palette._disposed)
        self.assertFalse(palette._open)
        self.assertIs(palette.input_widget(), palette._input)
        self.assertEqual(palette._title, palette_model.WINDOW_TITLE)
        self.assertEqual(self.controller.count("set_pre_dispatch"), 1)

    def test_show_at_focuses_real_line_edit_and_never_duplicates(self) -> None:
        palette = self.make()
        self.assertTrue(palette.isVisible())
        self.assertIs(self.app.focusWidget(), palette._input)
        self.assertEqual(palette._input.objectName(), palette_model.INPUT_OBJECT)
        self.assertEqual(palette._input._placeholder.count("Type"), 1)
        self.assertEqual(self.controller.open_count, 1)

        palette.show_at(_Point(80, 80))
        self.assertEqual(self.controller.open_count, 1)
        self.assertIs(self.app.focusWidget(), palette._input)

    def test_pills_are_exact_and_mark_the_active_context(self) -> None:
        palette = self.make()
        self.assertEqual(
            [pill.text() for pill in palette._pills.values()],
            list(palette_model.PILL_LABELS),
        )
        self.assertTrue(palette._pills["edge"].isChecked())
        self.assertFalse(palette._pills["face"].isChecked())

    # -------------------------------------------------- typing / shortcuts

    def test_typing_letters_digits_and_space_never_trigger_tools(self) -> None:
        palette = self.make()
        typed = ("W", "E", "R", "X", "T", "C", "0", "1", "2", "3", "4", "5", "Space")
        expected = ("w", "e", "r", "x", "t", "c", "0", "1", "2", "3", "4", "5", " ")
        for token, text in zip(typed, expected):
            with self.subTest(token=token):
                self.shortcut_override(token, text)
                self.press(token, text)
        self.assertEqual(palette._input.text(), "werxtc012345 ")
        self.assertEqual(self.controller.count("activate"), 0)
        self.assertEqual(self.controller.count("switch_context"), 0)
        self.assertEqual(self.controller.count("navigate"), 0)

    def test_textless_shortcut_override_is_accepted_by_both_paths(self) -> None:
        palette = self.make()

        via_filter = self.key_event("B", "", event_type=_EventType.ShortcutOverride)
        for watcher in list(self.app._filters):
            if watcher.eventFilter(palette._input, via_filter):
                break
        self.assertTrue(via_filter.isAccepted())

        via_widget = self.key_event("B", "", event_type=_EventType.ShortcutOverride)
        self.assertTrue(palette._input.event(via_widget))
        self.assertTrue(via_widget.isAccepted())

    def test_typing_and_enter_navigate_once_across_filter_and_widget(self) -> None:
        palette = self.make()
        self.controller.mutate(
            rows=(
                FakeRow("bevel", label="Bevel"),
                FakeRow("bend", label="Bend"),
            ),
            highlighted_row_id="bevel",
        )
        for token, text in (("B", "b"), ("E", "e")):
            self.shortcut_override(token, text)
            self.press(token, text)
        self.assertEqual(palette._input.text(), "be")
        self.assertEqual(self.controller.count("set_query"), 2)

        down = self.key_event("Down")
        for watcher in list(self.app._filters):
            if watcher.eventFilter(palette._input, down):
                break
        palette._input.keyPressEvent(down)  # same event object, second path
        self.assertEqual(self.controller.count("navigate"), 1)
        self.assertEqual(self.controller._view.highlighted_row_id, "bend")

        enter = self.key_event("Return")
        for watcher in list(self.app._filters):
            if watcher.eventFilter(palette._input, enter):
                break
        palette._input.keyPressEvent(enter)
        self.assertEqual(self.controller.count("activate"), 1)
        self.assertEqual(self.last_call("activate")[1], "bend")

    def test_ctrl_editing_shortcuts_are_preserved(self) -> None:
        palette = self.make()
        for token in ("A", "C", "V", "X"):
            with self.subTest(token=token):
                self.shortcut_override(token, "\x01", int(_Modifier.ControlModifier))
                self.press(token, "\x01", int(_Modifier.ControlModifier))
        self.assertEqual(palette._input.text(), "")
        self.assertEqual(self.controller.count("activate"), 0)
        self.assertEqual(self.controller.count("switch_context"), 0)

    def test_navigation_context_and_alt_digit_switch(self) -> None:
        self.make()
        self.press("Down")
        self.assertEqual(self.controller.calls[-1], ("navigate", 1))
        self.press("Up")
        self.assertEqual(self.controller.calls[-1], ("navigate", -1))
        self.press("Tab")
        self.assertEqual(self.controller.calls[-1], ("switch_context", "border", False))
        self.press("Backtab")
        self.assertEqual(self.controller.calls[-1], ("switch_context", "edge", False))
        self.press("4", "", int(_Modifier.AltModifier))
        self.assertEqual(self.controller.calls[-1], ("switch_context", "face", False))

    def test_context_pills_plain_click_switches_and_ctrl_click_converts(self) -> None:
        palette = self.make()
        self.app._modifiers = _Modifier(0)
        palette._pills["vertex"].mouseReleaseEvent(_MouseEvent())
        self.assertEqual(
            self.controller.calls[-1], ("switch_context", "vertex", False)
        )
        self.app._modifiers = _Modifier.ControlModifier
        palette._pills["face"].mouseReleaseEvent(_MouseEvent())
        self.assertEqual(
            self.controller.calls[-1], ("switch_context", "face", True)
        )

    # ------------------------------------------------------------- dispatch

    def test_enter_activates_highlighted_row_once_and_ignores_autorepeat(self) -> None:
        palette = self.make()
        self.controller.activate_result = replace(
            self.controller._view,
            generation=1,
            last_result=FakeResult("blocked", "selection changed"),
            status="Blocked: selection changed",
        )
        palette._clock = lambda: 100.0
        self.press("Return")
        self.assertEqual(self.controller.count("activate"), 1)
        self.assertEqual(
            self.last_call("activate")[:3], ("activate", "bevel", "session-1")
        )
        # A repeated/stale Enter on the freshly reopened view is suppressed.
        self.press("Return")
        self.assertEqual(self.controller.count("activate"), 1)
        palette._clock = lambda: 101.0
        self.press("Return")
        self.assertEqual(self.controller.count("activate"), 2)
        self.press("Return", auto_repeat=True)
        self.assertEqual(self.controller.count("activate"), 2)

    def test_blocked_result_reopens_the_same_palette_with_reason(self) -> None:
        palette = self.make()
        self.controller.pre_dispatch_on_activate = True
        self.controller.activate_result = replace(
            self.controller._view,
            generation=1,
            last_result=FakeResult("blocked", "selection changed"),
            status="Blocked: selection changed",
        )
        self.press("Return")
        self.assertTrue(palette.isVisible())
        self.assertIn("Blocked", palette._status_label.text())
        self.assertEqual(len(self.app._filters), 1)

    def test_launch_result_closes_and_restores_viewport_focus(self) -> None:
        viewport = _Widget()
        viewport.show()
        viewport.setFocus()
        palette = self.make(return_focus=viewport)
        self.press("Return")
        self.assertFalse(palette.isVisible())
        self.assertIs(self.app.focusWidget(), viewport)
        self.assertEqual(self.app._filters, [])

    # ------------------------------------------------------- destructiveness

    def test_destructive_confirmation_ignores_enter_and_defaults_to_cancel(self) -> None:
        palette = self.make()
        self.controller.mutate(pending_confirmation=FakePending("token-1"))
        palette._render(self.controller._view)
        self.assertTrue(palette._confirm_bar.isVisible())
        self.assertIs(palette._confirm_cancel.property("default"), True)
        self.assertEqual(palette._confirm_cancel.text(), palette_model.CONFIRM_CANCEL_LABEL)

        self.press("Return")
        self.assertEqual(self.controller.count("activate"), 0)
        self.assertEqual(self.controller.count("confirm"), 0)

        palette._confirm_cancel.click()
        self.assertEqual(
            self.last_call("confirm"), ("confirm", "token-1", False)
        )
        self.assertFalse(palette._confirm_bar.isVisible())
        # Declining keeps the palette open with the same rows.
        self.assertTrue(palette.isVisible())
        self.assertIn("declined", palette._status_label.text())

        self.controller.mutate(pending_confirmation=FakePending("token-2"))
        palette._render(self.controller._view)
        palette._confirm_accept.click()
        self.assertEqual(
            self.last_call("confirm"), ("confirm", "token-2", True)
        )

    def test_escape_declines_confirmation_then_cancels(self) -> None:
        palette = self.make()
        self.controller.mutate(pending_confirmation=FakePending("token-9"))
        palette._render(self.controller._view)
        self.press("Escape")
        names = self.controller.names()
        self.assertIn("confirm", names)
        self.assertIn("cancel", names)
        self.assertEqual(
            self.controller.calls[names.index("confirm")], ("confirm", "token-9", False)
        )
        self.assertFalse(palette.isVisible())

    # ---------------------------------------------------- focus lifecycle

    def test_outside_click_converts_qpointf_before_hit_testing(self) -> None:
        palette = self.make()
        inside = _MouseEvent(global_position=_PointF(10.4, 10.4))
        outside = _MouseEvent(global_position=_PointF(500.6, 10.4))

        for watcher in list(self.app._filters):
            watcher.eventFilter(object(), inside)
        self.assertEqual(self.controller.count("cancel"), 0)
        self.assertTrue(palette.isVisible())

        for watcher in list(self.app._filters):
            watcher.eventFilter(object(), outside)
        self.assertEqual(self.controller.count("cancel"), 1)
        self.assertFalse(palette.isVisible())

    def test_escape_cancels_exactly_once_and_restores_focus(self) -> None:
        viewport = _Widget()
        viewport.show()
        viewport.setFocus()
        palette = self.make(return_focus=viewport)
        self.press("Escape")
        self.assertEqual(self.controller.count("cancel"), 1)
        self.assertFalse(palette.isVisible())
        self.assertIs(self.app.focusWidget(), viewport)
        self.press("Escape")
        self.assertEqual(self.controller.count("cancel"), 1)
        self.assertEqual(self.controller.count("activate"), 0)

    def test_lifecycle_hooks_fire_once_on_escape_and_pre_dispatch(self) -> None:
        events: list = []
        palette = self.Palette(self.controller, None)
        self.palette = palette
        palette.set_lifecycle_hooks(
            on_open=lambda: events.append("open"),
            on_close=lambda: events.append("close"),
        )

        palette.show_at(_Point(50, 50))
        self.assertEqual(events, ["open"])

        self.press("Escape")
        self.assertEqual(events, ["open", "close"])

        palette.show_at(_Point(50, 50))
        self.assertEqual(events, ["open", "close", "open"])
        self.controller.trigger_pre_dispatch()
        self.assertEqual(events, ["open", "close", "open", "close"])
        palette.dispose()
        self.assertEqual(events, ["open", "close", "open", "close"])

    def test_deferred_refocus_skips_a_deactivated_palette(self) -> None:
        palette = self.make()
        outside = _Widget()
        outside.show()
        self.app._focus = outside
        self.app._active = outside

        _Timer.flush()
        self.assertIs(self.app.focusWidget(), outside)

        self.app._active = palette
        palette._refocus_input(respect_activation=True)
        self.assertIs(self.app.focusWidget(), palette._input)

    def test_child_focus_is_not_external_blur_but_outside_focus_cancels(self) -> None:
        palette = self.make()
        focus_out = _Event(_EventType.FocusOut)

        self.app._focus = palette._pills["edge"]
        for watcher in list(self.app._filters):
            watcher.eventFilter(palette._input, focus_out)
        _Timer.flush()
        self.assertEqual(self.controller.count("cancel"), 0)

        outside = _Widget()
        outside.show()
        self.app._focus = outside
        for watcher in list(self.app._filters):
            watcher.eventFilter(palette._input, focus_out)
        _Timer.flush()
        self.assertEqual(self.controller.count("cancel"), 1)
        _Timer.flush()
        self.assertEqual(self.controller.count("cancel"), 1)

    def test_window_deactivate_cancels_without_stealing_outside_focus(self) -> None:
        palette = self.make()
        outside = _Widget()
        outside.show()
        self.app._active = outside
        self.app._focus = outside
        palette.changeEvent(_Event(_EventType.WindowDeactivate))
        _Timer.flush()
        self.assertEqual(self.controller.count("cancel"), 1)
        self.assertIs(self.app.focusWidget(), outside)

    def test_window_deactivate_cancels_even_when_a_child_still_has_qt_focus(self) -> None:
        """Win32 viewport clicks leave the input as focusWidget while inactive."""

        palette = self.make()
        _Timer.flush()  # the initial tick-0 show/focus settle
        self.assertTrue(palette._shown_ready)
        self.assertIs(self.app.focusWidget(), palette._input)
        # Qt still reports the search field as focused, but the window itself
        # is no longer active: this must close, not bail out on child focus.
        palette.isActiveWindow = lambda: False

        palette.changeEvent(_Event(_EventType.WindowDeactivate))
        _Timer.flush()
        self.assertEqual(self.controller.count("cancel"), 1)
        self.assertFalse(palette._open)
        _Timer.flush()
        self.assertEqual(self.controller.count("cancel"), 1)

    def test_window_close_cancels_and_clears_open_state(self) -> None:
        palette = self.make()
        self.assertTrue(palette._open)

        palette.closeEvent(_Event(_EventType.WindowTitleChange))

        self.assertFalse(palette._open)
        self.assertEqual(self.controller.count("cancel"), 1)

    def test_window_hide_cancels_immediately_and_clears_open_state(self) -> None:
        palette = self.make()

        palette.hide()  # OS-level hide / Alt+F4 without a controller call

        self.assertFalse(palette._open)
        self.assertEqual(self.controller.count("cancel"), 1)

    def test_outside_popup_hide_cancels_once(self) -> None:
        palette = self.make()
        outside = _Widget()
        outside.show()
        self.app._focus = outside
        palette.hide()  # simulates the Qt.Popup outside-click hide
        _Timer.flush()
        self.assertEqual(self.controller.count("cancel"), 1)
        _Timer.flush()
        self.assertEqual(self.controller.count("cancel"), 1)

    def test_pre_dispatch_hides_removes_filter_and_restores_focus(self) -> None:
        viewport = _Widget()
        viewport.show()
        viewport.setFocus()
        palette = self.make(return_focus=viewport)
        self.assertIn("set_pre_dispatch", self.controller.names())

        self.controller.trigger_pre_dispatch()
        self.assertFalse(palette.isVisible())
        self.assertEqual(self.app._filters, [])
        self.assertIs(self.app.focusWidget(), viewport)
        self.assertEqual(self.controller.count("cancel"), 0)

    def test_dispose_is_idempotent_and_clears_the_hook(self) -> None:
        palette = self.make()
        palette.dispose()
        palette.dispose()
        self.assertEqual(self.controller.count("clear_pre_dispatch"), 1)
        self.assertTrue(palette._deleted)
        self.assertIsNone(self.controller.hook)

    def test_errors_surface_inline_instead_of_raising(self) -> None:
        palette = self.make()

        def explode(_delta: int) -> Any:
            raise RuntimeError("controller exploded")

        self.controller.navigate = explode  # type: ignore[assignment]
        self.press("Down")
        self.assertIn("controller exploded", palette._status_label.text())


class SourceContractTests(unittest.TestCase):
    FORBIDDEN_METHOD_NAMES = {"connect", "disconnect", "emit"}

    def _trees(self) -> list[tuple[str, ast.Module]]:
        trees = []
        for name in ("palette.py", "palette_model.py"):
            source = (ROOT / "qt_ui" / name).read_text(encoding="utf-8")
            tree = ast.parse(source, filename=name, feature_version=(3, 11))
            compile(tree, name, "exec")
            trees.append((name, tree))
        return trees

    def test_python311_ast_and_no_qobject_method_collisions(self) -> None:
        for name, tree in self._trees():
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    with self.subTest(file=name, method=node.name):
                        self.assertNotIn(node.name, self.FORBIDDEN_METHOD_NAMES)

    def test_no_annotated_slot_decorators(self) -> None:
        for name, tree in self._trees():
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for decorator in node.decorator_list:
                    text = ast.unparse(decorator)
                    if text.split(".")[-1] != "Slot":
                        continue
                    annotations = [
                        arg.annotation
                        for arg in list(node.args.args) + list(node.args.kwonlyargs)
                        if arg.annotation is not None
                    ]
                    with self.subTest(file=name, slot=node.name):
                        self.assertIsNone(node.returns)
                        self.assertEqual(annotations, [])

    def test_palette_model_has_no_qt_or_host_imports(self) -> None:
        source = (ROOT / "qt_ui" / "palette_model.py").read_text(encoding="utf-8")
        tree = ast.parse(source, feature_version=(3, 11))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn(
                        alias.name.split(".")[0],
                        {"PySide6", "pymxs", "qtmax", "max_host", "core"},
                    )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                self.assertNotIn(
                    module.split(".")[0],
                    {"PySide6", "pymxs", "qtmax", "max_host", "core"},
                )

    def test_palette_module_is_importable_and_exposes_the_contract(self) -> None:
        self.assertTrue(palette_module.__doc__)
        self.assertIsNotNone(palette_module.SearchPalette)
        for name, tree in self._trees():
            if name != "palette.py":
                continue
            classes = [
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.ClassDef)
                and node.name == "SearchPalette"
                and any(
                    isinstance(child, ast.FunctionDef) and child.name == "show_at"
                    for child in node.body
                )
            ]
            self.assertEqual(len(classes), 1)
            methods = {
                node.name
                for node in classes[0].body
                if isinstance(node, ast.FunctionDef)
            }
            for required in ("show_at", "cancel", "dispose", "eventFilter"):
                self.assertIn(required, methods)
        source = (ROOT / "qt_ui" / "palette.py").read_text(encoding="utf-8")
        for marker in (
            "ShortcutOverride",
            "set_pre_dispatch",
            "clear_pre_dispatch",
            "convert_selection",
            "confirmation_model",
            "return_focus",
            "view_is_closed",
        ):
            self.assertIn(marker, source)


@unittest.skipUnless(
    not REAL_QT_ERROR,
    f"PySide6 runtime unavailable; optional offscreen palette smoke test skipped ({REAL_QT_ERROR})",
)
class RealQtSmokeTests(unittest.TestCase):
    def test_palette_constructs_shows_and_cancels_offscreen(self) -> None:
        app = _RealQtWidgets.QApplication.instance()
        if app is None:
            app = _RealQtWidgets.QApplication([])
        controller = FakeController()
        palette = palette_module.SearchPalette(controller, None)
        self.addCleanup(palette.dispose)
        palette.show_at(_RealQtCore.QPoint(20, 20))
        self.assertTrue(palette.isVisible())
        self.assertEqual(palette._input.objectName(), palette_model.INPUT_OBJECT)
        palette.cancel()
        self.assertFalse(palette.isVisible())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

"""Spotlight-style search palette for the V2 maneuver controller.

The palette is a *renderer*: it owns widget state, focus lifecycle and input
isolation only.  Every search, navigation, context switch, activation and
confirmation request goes through the duck-typed controller methods frozen in
the plan (``open``, ``view``, ``set_query``, ``navigate``, ``activate``,
``switch_context``, ``confirm``, ``cancel``, ``set_pre_dispatch``,
``clear_pre_dispatch``).  It never imports ``pymxs`` and never calls a host.

Importing this module does not import PySide6 when it is missing; the public
``SearchPalette`` class is built from the available Qt binding and otherwise
raises a finite error on first use.  ``build_search_palette_class`` is an
internal seam for fake-Qt tests.
"""

from __future__ import annotations

import time
import weakref
from typing import Any, Callable

from . import palette_model as presentation
from .palette_model import (
    CONFIRM_ACCEPT_OBJECT,
    CONFIRM_BAR_OBJECT,
    CONFIRM_CANCEL_OBJECT,
    HINT_OBJECT,
    INPUT_OBJECT,
    PALETTE_OBJECT,
    PILL_OBJECT,
    PILL_LABELS,
    PILL_TOOLTIP,
    ROW_OBJECT,
    STATUS_OBJECT,
    WINDOW_TITLE,
    confirmation_model,
    context_badge,
    context_for_index,
    context_value,
    cycle_context,
    hint_text,
    keyboard_intent,
    row_presentation,
    row_signature,
    route_badge,
    selection_badge,
    status_line,
    stylesheet,
    view_is_closed,
)

_ACTIVATION_COOLDOWN_SECONDS = 0.35


def _key_token(qtcore: Any, key: Any) -> str:
    """Translate a Qt key code to the small vocabulary ``palette_model`` uses."""

    keys = qtcore.Qt.Key
    for name in (
        "Escape",
        "Return",
        "Enter",
        "Up",
        "Down",
        "Tab",
        "Backtab",
        "Backspace",
        "Delete",
        "Left",
        "Right",
        "Home",
        "End",
        "Space",
    ):
        candidate = getattr(keys, "Key_" + name, None)
        if candidate is not None and key == candidate:
            return name
    # Printable symbol keys needed by ShortcutOverride handling; Windows Qt
    # reports these by key code while ``event.text()`` may be empty.
    for name, symbol in (
        ("Greater", ">"),
        ("Minus", "-"),
        ("Underscore", "_"),
        ("Period", "."),
        ("Slash", "/"),
    ):
        candidate = getattr(keys, "Key_" + name, None)
        if candidate is not None and key == candidate:
            return symbol
    try:
        value = int(key)
    except (TypeError, ValueError):
        return ""
    if 0x30 <= value <= 0x39 or 0x41 <= value <= 0x5A:
        return chr(value)
    if 0x20 <= value <= 0x7E:
        character = chr(value)
        if character in (">", "-", "_", ".", "/"):
            return character
    return ""


def _event_intent(qtcore: Any, event: Any) -> presentation.KeyIntent:
    modifiers = event.modifiers()
    return keyboard_intent(
        _key_token(qtcore, event.key()),
        event.text(),
        ctrl=bool(modifiers & qtcore.Qt.KeyboardModifier.ControlModifier),
        alt=bool(modifiers & qtcore.Qt.KeyboardModifier.AltModifier),
        shift=bool(modifiers & qtcore.Qt.KeyboardModifier.ShiftModifier),
        auto_repeat=bool(event.isAutoRepeat()),
    )


def _event_type(qtcore: Any, name: str) -> Any:
    """Return a QEvent type when the running binding/fake exposes it."""

    try:
        return getattr(qtcore.QEvent.Type, name, None)
    except AttributeError:
        return None


def _exception_text(exc: BaseException) -> str:
    message = " ".join(str(exc).split())
    if len(message) > 200:
        message = message[:197] + "…"
    name = type(exc).__name__
    return f"{name}: {message}" if message else name


def _session(view: Any) -> Any:
    return getattr(view, "session_id", None)


def _generation(view: Any) -> Any:
    return getattr(view, "generation", None)


def build_search_palette_class(qtcore: Any, qtgui: Any, qtwidgets: Any) -> type:
    """Build ``SearchPalette`` for one Qt binding.

    Tests pass a minimal fake binding; production passes the real PySide6
    modules.  Keeping the class construction in a factory lets the module stay
    importable (and AST-checkable) on hosts without Qt.
    """

    class _ContextPill(qtwidgets.QPushButton):
        """No-focus header pill; Ctrl+Click asks the controller to convert."""

        activated = qtcore.Signal(str, bool)

        def __init__(self, context: str, label: str, parent: Any = None) -> None:
            # Qt can deliver change/focus events synchronously while the base
            # class initializes, so every plain attribute exists first.
            self._context = str(context)
            super().__init__(label, parent)
            self.setObjectName(PILL_OBJECT)
            self.setFocusPolicy(qtcore.Qt.FocusPolicy.NoFocus)
            self.setCheckable(True)
            self.setToolTip(PILL_TOOLTIP)

        def context_name(self) -> str:
            return self._context

        def mouseReleaseEvent(self, event: Any) -> None:  # noqa: N802 - Qt signature
            if event.button() == qtcore.Qt.MouseButton.LeftButton:
                modifiers = qtwidgets.QApplication.keyboardModifiers()
                convert = bool(
                    modifiers & qtcore.Qt.KeyboardModifier.ControlModifier
                )
                self.activated.emit(self._context, convert)
                event.accept()
                return
            super().mouseReleaseEvent(event)

    class _RowFrame(qtwidgets.QFrame):
        """No-focus result row; the line edit keeps keyboard ownership."""

        activated = qtcore.Signal(str)

        def __init__(self, row_id: str, parent: Any = None) -> None:
            # See _ContextPill: attributes must precede base-class init.
            self._row_id = str(row_id)
            super().__init__(parent)
            self.setObjectName(ROW_OBJECT)
            self.setFocusPolicy(qtcore.Qt.FocusPolicy.NoFocus)
            self.setProperty("highlighted", False)
            self.setProperty("disabled", False)

        def row_identifier(self) -> str:
            return self._row_id

        def mouseReleaseEvent(self, event: Any) -> None:  # noqa: N802 - Qt signature
            if event.button() == qtcore.Qt.MouseButton.LeftButton:
                self.activated.emit(self._row_id)
                event.accept()
                return
            super().mouseReleaseEvent(event)

    class _SearchLineEdit(qtwidgets.QLineEdit):
        """Search field that owns every printable/navigation key directly.

        The application-level filter is the first line of defence, but Windows
        can route a ``ShortcutOverride``/``KeyPress`` to the focused widget
        without a matching filter delivery.  Handling the keys here as well
        (deduplicated by the owning palette) keeps typing, arrows, Enter, Esc
        and Tab working in every event order.
        """

        def __init__(self, owner: Any, parent: Any = None) -> None:
            self._owner = owner
            super().__init__(parent)

        def focusNextPrevChild(self, next: bool) -> bool:  # noqa: N802
            # Tab is a context cycle, never Qt focus traversal.
            return False

        def event(self, event: Any) -> bool:
            if event.type() == qtcore.QEvent.Type.ShortcutOverride:
                owner = self._owner
                handler = getattr(owner, "_handle_search_shortcut_override", None)
                if handler is not None and handler(event):
                    event.accept()
                    return True
            parent_event = getattr(qtwidgets.QLineEdit, "event", None)
            if parent_event is None:
                return True
            return parent_event(self, event)

        def keyPressEvent(self, event: Any) -> None:  # noqa: N802 - Qt signature
            owner = self._owner
            handler = getattr(owner, "_handle_search_key_press", None)
            if handler is not None and handler(event):
                event.accept()
                return
            parent_event = getattr(qtwidgets.QLineEdit, "keyPressEvent", None)
            if parent_event is None:
                return
            parent_event(self, event)

        def keyReleaseEvent(self, event: Any) -> None:  # noqa: N802 - Qt signature
            owner = self._owner
            handler = getattr(owner, "_handle_search_key_release", None)
            if handler is not None and handler(event):
                event.accept()
                return
            parent_event = getattr(qtwidgets.QLineEdit, "keyReleaseEvent", None)
            if parent_event is None:
                return
            parent_event(self, event)

    class SearchPalette(qtwidgets.QFrame):
        """One shared, reusable palette window over a palette controller."""

        def __init__(self, controller: Any, parent: Any = None) -> None:
            # Real PySide6 fires changeEvent() synchronously from setWindowTitle
            # before this method finishes, so every instance attribute must be
            # initialized *before* the QFrame base class is constructed and
            # before any self.set* call.
            self._controller = controller
            self._view: Any = None
            self._rows: tuple[Any, ...] = ()
            self._signature: Any = None
            self._row_widgets: dict[str, Any] = {}
            self._pills: dict[str, Any] = {}
            self._disposed = False
            self._open = False
            self._dispatching = False
            self._pre_dispatched = False
            self._cancelled = True
            self._suppress_hide = 0
            self._filter_installed = False
            self._return_focus_ref: Any = None
            self._focus_epoch: Any = None
            self._confirmation_token = ""
            self._last_error = ""
            self._activation_key: Any = None
            self._activation_at = 0.0
            self._input_had_focus = False
            self._clock = time.monotonic
            self._dispatch_hook = self._prepare_dispatch
            self._last_key_event: Any = None
            self._suppress_tab = 0
            self._lifecycle_open = False
            self._on_open: Any = None
            self._on_close: Any = None

            super().__init__(parent)
            self.setObjectName(PALETTE_OBJECT)
            self.setWindowTitle(WINDOW_TITLE)
            # ``Qt.Popup`` leaves Win32 GetFocus() on Max's native DirectX
            # viewport HWND, so typing never reaches the search field.  An
            # activatable frameless Dialog parented to Max's QMainWindow is
            # what actually transfers keyboard focus on Windows.
            self.setWindowFlags(
                qtcore.Qt.WindowType.Dialog
                | qtcore.Qt.WindowType.FramelessWindowHint
                | qtcore.Qt.WindowType.NoDropShadowWindowHint
            )
            self.setAttribute(qtcore.Qt.WidgetAttribute.WA_DeleteOnClose, False)
            # The real line edit owns all keyboard focus; a click on the frame
            # background must not steal it from the text field.
            self.setFocusPolicy(qtcore.Qt.FocusPolicy.NoFocus)
            self.setFixedWidth(460)
            self.setMaximumHeight(560)
            self.setStyleSheet(stylesheet())

            setter = getattr(controller, "set_pre_dispatch", None)
            if callable(setter):
                setter(self._dispatch_hook)

            self._build_chrome()
            for context, label in zip(presentation.CONTEXT_ORDER, PILL_LABELS):
                pill = _ContextPill(context, label, self)
                pill.activated.connect(self._on_pill_activated)
                self._pills[context] = pill
                self._pills_row.addWidget(pill)

        # ------------------------------------------------------------- chrome

        def _build_chrome(self) -> None:
            outer = qtwidgets.QVBoxLayout(self)
            outer.setContentsMargins(9, 9, 9, 7)
            outer.setSpacing(5)

            header = qtwidgets.QHBoxLayout()
            header.setContentsMargins(0, 0, 0, 0)
            header.setSpacing(6)
            self._title_label = qtwidgets.QLabel("Search maneuvers", self)
            self._title_label.setObjectName("paletteTitle")
            self._context_label = qtwidgets.QLabel("", self)
            self._context_label.setObjectName("paletteContext")
            self._selection_label = qtwidgets.QLabel("", self)
            self._selection_label.setObjectName("paletteSelection")
            header.addWidget(self._title_label, 0)
            header.addStretch(1)
            header.addWidget(self._context_label, 0)
            header.addWidget(self._selection_label, 0)
            outer.addLayout(header)

            self._pills_row = qtwidgets.QHBoxLayout()
            self._pills_row.setContentsMargins(0, 0, 0, 0)
            self._pills_row.setSpacing(3)
            outer.addLayout(self._pills_row)

            self._input = _SearchLineEdit(self, self)
            self._input.setObjectName(INPUT_OBJECT)
            self._input.setPlaceholderText("Type a maneuver, alias or > command")
            self._input.textEdited.connect(self._on_text_edited)
            outer.addWidget(self._input)

            self._scroll = qtwidgets.QScrollArea(self)
            self._scroll.setObjectName("paletteScroll")
            self._scroll.setWidgetResizable(True)
            self._scroll.setHorizontalScrollBarPolicy(
                qtcore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            )
            self._scroll.setFrameShape(qtwidgets.QFrame.Shape.NoFrame)
            self._rows_host = qtwidgets.QWidget(self._scroll)
            self._rows_host.setObjectName("paletteRows")
            self._rows_layout = qtwidgets.QVBoxLayout(self._rows_host)
            self._rows_layout.setContentsMargins(0, 0, 0, 0)
            self._rows_layout.setSpacing(1)
            self._rows_layout.addStretch(1)
            self._scroll.setWidget(self._rows_host)
            outer.addWidget(self._scroll, 1)

            self._confirm_bar = qtwidgets.QFrame(self)
            self._confirm_bar.setObjectName(CONFIRM_BAR_OBJECT)
            confirm_layout = qtwidgets.QVBoxLayout(self._confirm_bar)
            confirm_layout.setContentsMargins(8, 6, 8, 6)
            confirm_layout.setSpacing(3)
            self._confirm_title = qtwidgets.QLabel("", self._confirm_bar)
            self._confirm_title.setObjectName("confirmTitle")
            self._confirm_detail = qtwidgets.QLabel("", self._confirm_bar)
            self._confirm_detail.setObjectName("confirmDetail")
            self._confirm_detail.setWordWrap(True)
            buttons = qtwidgets.QHBoxLayout()
            buttons.setContentsMargins(0, 0, 0, 0)
            buttons.setSpacing(5)
            self._confirm_accept = qtwidgets.QPushButton("", self._confirm_bar)
            self._confirm_accept.setObjectName(CONFIRM_ACCEPT_OBJECT)
            self._confirm_accept.setFocusPolicy(qtcore.Qt.FocusPolicy.NoFocus)
            self._confirm_accept.clicked.connect(self._on_confirm_accept)
            self._confirm_cancel = qtwidgets.QPushButton("", self._confirm_bar)
            self._confirm_cancel.setObjectName(CONFIRM_CANCEL_OBJECT)
            self._confirm_cancel.setFocusPolicy(qtcore.Qt.FocusPolicy.NoFocus)
            self._confirm_cancel.setProperty("default", True)
            self._confirm_cancel.clicked.connect(self._on_confirm_cancel)
            buttons.addStretch(1)
            buttons.addWidget(self._confirm_cancel, 0)
            buttons.addWidget(self._confirm_accept, 0)
            confirm_layout.addWidget(self._confirm_title)
            confirm_layout.addWidget(self._confirm_detail)
            confirm_layout.addLayout(buttons)
            self._confirm_bar.hide()
            outer.addWidget(self._confirm_bar)

            footer = qtwidgets.QHBoxLayout()
            footer.setContentsMargins(0, 0, 0, 0)
            footer.setSpacing(6)
            self._status_label = qtwidgets.QLabel("", self)
            self._status_label.setObjectName(STATUS_OBJECT)
            self._status_label.setWordWrap(True)
            self._hint_label = qtwidgets.QLabel("", self)
            self._hint_label.setObjectName(HINT_OBJECT)
            footer.addWidget(self._status_label, 1)
            footer.addWidget(self._hint_label, 0)
            outer.addLayout(footer)

        # --------------------------------------------------------- public API

        def input_widget(self) -> Any:
            """Return the real line edit that owns keyboard focus."""

            return self._input

        def set_lifecycle_hooks(
            self, on_open: Any = None, on_close: Any = None
        ) -> None:
            """Install host-neutral open/close callbacks (no Qt/pymxs imports).

            The host uses these to couple Max's Win32 accelerator state to the
            palette lifetime without this module importing ``pymxs``.
            """

            self._on_open = on_open
            self._on_close = on_close

        def suppress_next_tab(self) -> None:
            """Swallow the Tab that opened this palette in the same tick.

            The global hotkey listener returns ``True`` for the opening
            KeyPress, but Windows can still deliver that press (or its release)
            to the newly focused search field.  Exactly one stray Tab event is
            consumed, and the guard expires on the next event-loop turn.
            """

            self._suppress_tab = 1
            try:
                qtcore.QTimer.singleShot(0, self._clear_tab_suppression)
            except (AttributeError, RuntimeError, TypeError):
                self._suppress_tab = 0

        def _clear_tab_suppression(self) -> None:
            self._suppress_tab = 0

        def _notify_open(self) -> None:
            if self._lifecycle_open:
                return
            self._lifecycle_open = True
            callback = self._on_open
            if callback is None:
                return
            try:
                callback()
            except Exception:
                pass

        def _notify_close(self) -> None:
            if not self._lifecycle_open:
                return
            self._lifecycle_open = False
            callback = self._on_close
            if callback is None:
                return
            try:
                callback()
            except Exception:
                pass

        def show_at(self, global_pos: Any = None, *, return_focus: Any = None) -> Any:
            """Open/raise the one palette and focus its real line edit."""

            if self._disposed:
                raise RuntimeError("This SearchPalette has been disposed.")
            app = self._application()
            self._assert_gui_thread(app)

            if self._open:
                self.raise_()
                self.activateWindow()
                self._refocus_input()
                return self._view

            if return_focus is not None:
                self._return_focus_ref = self._weak_ref(return_focus)
            elif self._return_focus_ref is None or self._return_focus_ref() is None:
                self._return_focus_ref = self._weak_ref(app.focusWidget())

            self._cancelled = False
            self._pre_dispatched = False
            self._dispatching = False
            self._last_error = ""
            self._confirmation_token = ""
            self._signature = None
            self._activation_key = None

            try:
                view = self._controller.open()
            except Exception as exc:
                self._render_error(exc)
                self._restore_focus()
                return None
            if view is None or view_is_closed(view):
                self._open = False
                self._view = None
                self._hide_without_cancel()
                self._restore_focus()
                return view

            self._open = True
            self._view = view
            self._render(view)
            self._notify_open()
            self._position_near(global_pos, app)
            self._install_filter(app)
            # Windows emits transient activation changes during show();
            # only treat a deactivate/focus-out as a close once the input has
            # actually held focus.
            self._input_had_focus = False
            self.show()
            self.raise_()
            self.activateWindow()
            self._refocus_input()
            # Win32 keyboard focus can land on Max's native viewport HWND even
            # after activateWindow(); one zero-delay refocus moves the real
            # caret into the search field once the window is mapped.  It may
            # not reclaim focus if the palette was deactivated in between.
            qtcore.QTimer.singleShot(0, self._refocus_deferred)
            return view

        def refresh(self) -> Any:
            """Re-render the current public view without opening a closed palette."""

            if self._disposed:
                return None
            reader = getattr(self._controller, "view", None)
            if not callable(reader):
                return None
            try:
                view = reader()
            except Exception:
                return None
            self._view = view
            if self._open and view is not None and not view_is_closed(view):
                self._render(view)
            return view

        def cancel(self) -> None:
            """Cancel once: decline any pending confirmation, then close."""

            if self._disposed:
                return
            if self._cancelled and not self._open:
                return
            self._cancelled = True
            token = self._confirmation_token
            self._confirmation_token = ""
            if token:
                try:
                    self._controller.confirm(token, False)
                except Exception:
                    pass
            if self._open and not self._pre_dispatched:
                try:
                    self._controller.cancel(reason="")
                except Exception:
                    pass
            self._finish_close(restore=True)
            self._notify_close()

        def dispose(self) -> None:
            """Idempotently release this widget; never touch the QApplication."""

            if self._disposed:
                return
            try:
                self.cancel()
            except Exception:
                pass
            self._disposed = True
            self._open = False
            self._view = None
            self._remove_filter()
            self._notify_close()
            clearer = getattr(self._controller, "clear_pre_dispatch", None)
            if callable(clearer):
                try:
                    clearer(self._dispatch_hook)
                except Exception:
                    pass
            self._return_focus_ref = None
            self._row_widgets.clear()
            self._pills.clear()
            self._hide_without_cancel()
            self.deleteLater()

        # ------------------------------------------------------------ render

        def _render(self, view: Any) -> None:
            if self._disposed or view is None:
                return
            if view_is_closed(view):
                self._finish_close(restore=True)
                return
            self._open = True
            self._view = view
            self._last_error = ""
            context = context_value(getattr(view, "context", None))
            self._context_label.setText(context_badge(getattr(view, "context", None)))
            count = getattr(view, "selection_count", 0)
            self._selection_label.setText(
                f"{selection_badge(count)}  {route_badge(count)}"
            )
            for name, pill in self._pills.items():
                pill.setChecked(name == context)

            query = str(getattr(view, "query", "") or "")
            if self._input.text() != query:
                self._input.setText(query)
                self._input.setCursorPosition(len(query))

            rows = tuple(getattr(view, "rows", ()) or ())
            signature = (_generation(view), row_signature(rows))
            if signature != self._signature:
                self._rebuild_rows(rows)
                self._signature = signature
            self._rows = rows
            self._update_highlight(getattr(view, "highlighted_row_id", None))
            self._apply_confirmation(view)
            self._status_label.setText(status_line(view))
            self._hint_label.setText(hint_text(view))
            self._focus_epoch = (_session(view), _generation(view))
            self.adjustSize()

        def _rebuild_rows(self, rows: tuple[Any, ...]) -> None:
            while self._rows_layout.count():
                item = self._rows_layout.takeAt(0)
                widget = item.widget() if item is not None else None
                if widget is not None:
                    widget.deleteLater()
            self._row_widgets = {}

            if not rows:
                empty = qtwidgets.QLabel(
                    "No matching maneuvers. Backspace edits the search.",
                    self._rows_host,
                )
                empty.setObjectName("paletteEmpty")
                empty.setWordWrap(True)
                empty.setAttribute(
                    qtcore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
                )
                self._rows_layout.addWidget(empty)
                self._rows_layout.addStretch(1)
                return

            previous_group = None
            for row in rows:
                projected = row_presentation(row)
                if projected.group and projected.group != previous_group:
                    heading = qtwidgets.QLabel(projected.group.upper(), self._rows_host)
                    heading.setObjectName("paletteGroup")
                    heading.setAttribute(
                        qtcore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
                    )
                    self._rows_layout.addWidget(heading)
                    previous_group = projected.group

                frame = _RowFrame(projected.row_id, self._rows_host)
                frame.setProperty("disabled", not projected.enabled)
                frame.activated.connect(self._on_row_activated)
                layout = qtwidgets.QVBoxLayout(frame)
                layout.setContentsMargins(8, 4, 8, 4)
                layout.setSpacing(1)

                top = qtwidgets.QHBoxLayout()
                top.setContentsMargins(0, 0, 0, 0)
                label = qtwidgets.QLabel(projected.label, frame)
                label.setObjectName("rowLabel")
                label.setProperty("disabled", not projected.enabled)
                label.setAttribute(
                    qtcore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
                )
                top.addWidget(label, 1)
                if projected.pivot_badge:
                    pivot = qtwidgets.QLabel(projected.pivot_badge, frame)
                    pivot.setObjectName("pivotBadge")
                    pivot.setAttribute(
                        qtcore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
                    )
                    top.addWidget(pivot, 0)
                route = qtwidgets.QLabel(
                    "SELECT" if projected.enabled else "BLOCKED", frame
                )
                route.setObjectName("routeBadge")
                route.setAttribute(
                    qtcore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
                )
                top.addWidget(route, 0)
                layout.addLayout(top)

                meta = qtwidgets.QLabel(projected.meta, frame)
                meta.setObjectName("rowMeta")
                meta.setAttribute(
                    qtcore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
                )
                layout.addWidget(meta)

                if not projected.enabled and projected.reason:
                    reason = qtwidgets.QLabel(projected.reason, frame)
                    reason.setObjectName("rowReason")
                    reason.setWordWrap(True)
                    reason.setAttribute(
                        qtcore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
                    )
                    layout.addWidget(reason)

                self._rows_layout.addWidget(frame)
                self._row_widgets[projected.row_id] = frame

            self._rows_layout.addStretch(1)

        def _update_highlight(self, row_id: Any) -> None:
            wanted = str(row_id or "")
            for identifier, frame in self._row_widgets.items():
                frame.setProperty("highlighted", identifier == wanted)
                self._repolish(frame)
            frame = self._row_widgets.get(wanted)
            if frame is not None:
                self._scroll.ensureWidgetVisible(frame, 0, 8)

        @staticmethod
        def _repolish(widget: Any) -> None:
            style = widget.style()
            style.unpolish(widget)
            style.polish(widget)
            widget.update()

        def _apply_confirmation(self, view: Any) -> None:
            model = confirmation_model(view)
            if model is None:
                self._confirmation_token = ""
                self._confirm_bar.hide()
                return
            self._confirmation_token = model.token
            self._confirm_title.setText(model.title)
            self._confirm_detail.setText(model.detail)
            self._confirm_accept.setText(model.accept_label)
            self._confirm_cancel.setText(model.cancel_label)
            self._confirm_cancel.setProperty(
                "default", model.default_action == "cancel"
            )
            self._confirm_bar.show()

        # ------------------------------------------------------------ events

        def eventFilter(self, watched: Any, event: Any) -> bool:  # noqa: N802
            if (
                getattr(self, "_disposed", True)
                or not getattr(self, "_open", False)
                or not self.isVisible()
            ):
                return False
            event_type = event.type()
            if event_type == _event_type(qtcore, "MouseButtonPress"):
                # A Dialog (unlike Qt.Popup) stays open on outside clicks.
                if not self._owns_widget(watched) and self._outside_click(event):
                    self.cancel()
                    return False
            if not self._owns_widget(watched):
                return False
            if event_type == qtcore.QEvent.Type.ShortcutOverride:
                return self._handle_search_shortcut_override(event)
            if event_type == qtcore.QEvent.Type.KeyPress:
                return self._handle_search_key_press(event)
            if event_type == _event_type(qtcore, "KeyRelease"):
                return self._handle_search_key_release(event)
            if event_type == qtcore.QEvent.Type.FocusIn:
                if watched is self._input:
                    self._input_had_focus = True
                return False
            if event_type == qtcore.QEvent.Type.FocusOut:
                if getattr(self, "_input_had_focus", False):
                    self._schedule_focus_check()
                return False
            return False

        # --------------------------------------------------- direct key paths

        def _claim_key_event(self, event: Any) -> bool:
            """True the first time this exact event object is handled."""

            if getattr(self, "_last_key_event", None) is event:
                return False
            self._last_key_event = event
            return True

        def _handle_search_shortcut_override(self, event: Any) -> bool:
            if self._disposed or not self._open:
                return False
            intent = _event_intent(qtcore, event)
            if intent.action == "ignore":
                return False
            # Accept the override so Qt/host shortcuts never fire; the real
            # KeyPress still edits or navigates.
            if not self._claim_key_event(event):
                return True
            event.accept()
            return True

        def _handle_search_key_press(self, event: Any) -> bool:
            if self._disposed or not self._open:
                return False
            if (
                self._suppress_tab > 0
                and _key_token(qtcore, event.key()) == "Tab"
            ):
                self._suppress_tab = 0
                return True
            intent = _event_intent(qtcore, event)
            if intent.action in ("insert_text", "edit", "ignore"):
                # Normal QLineEdit editing (typing, Backspace, cursor,
                # Ctrl+A/C/V/X) must reach the real widget untouched.
                return False
            if not self._claim_key_event(event):
                return True
            return self._handle_key_intent(intent)

        def _handle_search_key_release(self, event: Any) -> bool:
            if self._disposed or not self._open:
                return False
            if (
                self._suppress_tab > 0
                and _key_token(qtcore, event.key()) == "Tab"
            ):
                self._suppress_tab = 0
                return True
            return False

        def _outside_click(self, event: Any) -> bool:
            point = None
            getter = getattr(event, "globalPosition", None)
            if callable(getter):
                try:
                    point = getter()
                except (RuntimeError, TypeError):
                    point = None
            if point is None:
                getter = getattr(event, "globalPos", None)
                if callable(getter):
                    try:
                        point = getter()
                    except (RuntimeError, TypeError):
                        point = None
            if point is None:
                return False
            # Qt 6 hands back a QPointF from globalPosition(); frameGeometry()
            # only accepts a QPoint.
            to_point = getattr(point, "toPoint", None)
            if callable(to_point):
                try:
                    point = to_point()
                except (AttributeError, TypeError, RuntimeError):
                    return False
            try:
                return not self.frameGeometry().contains(point)
            except (AttributeError, TypeError, RuntimeError):
                return False

        def keyPressEvent(self, event: Any) -> None:  # noqa: N802 - Qt signature
            if self._handle_search_key_press(event):
                event.accept()
                return
            super().keyPressEvent(event)

        def keyReleaseEvent(self, event: Any) -> None:  # noqa: N802 - Qt signature
            if self._handle_search_key_release(event):
                event.accept()
                return
            super().keyReleaseEvent(event)

        def focusNextPrevChild(self, next: bool) -> bool:  # noqa: N802
            # Tab cycles the palette context; it must never move focus away
            # from the search field.
            return False

        def _handle_key_intent(self, intent: presentation.KeyIntent) -> bool:
            action = intent.action
            if action in ("insert_text", "edit"):
                return False
            if action == "ignore":
                return False
            if action == "cancel":
                self.cancel()
                return True
            if action == "activate":
                if self._confirmation_token:
                    # Never accept a destructive confirmation from Enter.
                    return True
                view = self._view
                if view is None:
                    return True
                row = presentation.highlighted_row(
                    tuple(getattr(view, "rows", ()) or ()),
                    getattr(view, "highlighted_row_id", None),
                )
                if row is None:
                    return True
                self._activate_row(str(getattr(row, "row_id", "") or ""))
                return True
            if action == "navigate":
                self._navigate(int(intent.delta))
                return True
            if action == "context_cycle":
                self._switch_context(
                    cycle_context(
                        getattr(self._view, "context", None), int(intent.delta)
                    ),
                    False,
                )
                return True
            if action == "context_switch":
                context = context_for_index(intent.context_index)
                if context is not None:
                    self._switch_context(context, bool(intent.convert_selection))
                return True
            return True

        def changeEvent(self, event: Any) -> None:  # noqa: N802 - Qt signature
            if not getattr(self, "_disposed", True) and getattr(
                self, "_open", False
            ):
                try:
                    if event.type() == qtcore.QEvent.Type.WindowDeactivate:
                        if getattr(self, "_input_had_focus", False):
                            self._schedule_focus_check()
                except Exception:
                    pass
            super().changeEvent(event)

        def hideEvent(self, event: Any) -> None:  # noqa: N802 - Qt signature
            # An outside click on a Qt.Popup hides the window without a matching
            # controller call; treat that like an external focus loss, but never
            # for the palette's own hide-before-dispatch/close paths.
            if (
                not getattr(self, "_disposed", True)
                and getattr(self, "_open", False)
                and getattr(self, "_suppress_hide", 0) == 0
            ):
                self._schedule_focus_check()
            super().hideEvent(event)

        # ---------------------------------------------------------- intents

        def _on_text_edited(self, text: str) -> None:
            if self._disposed or not self._open or self._dispatching:
                return
            try:
                view = self._controller.set_query(str(text))
            except Exception as exc:
                self._render_error(exc)
                return
            if view is not None:
                self._render(view)

        def _on_pill_activated(self, context: str, convert: bool) -> None:
            self._switch_context(str(context), bool(convert))

        def _on_row_activated(self, row_id: str) -> None:
            self._activate_row(str(row_id))

        def _on_confirm_accept(self) -> None:
            self._resolve_confirmation(True)

        def _on_confirm_cancel(self) -> None:
            self._resolve_confirmation(False)

        def _navigate(self, delta: int) -> None:
            if self._disposed or not self._open or self._dispatching:
                return
            try:
                view = self._controller.navigate(int(delta))
            except Exception as exc:
                self._render_error(exc)
                return
            if view is not None:
                self._render(view)

        def _switch_context(self, context: str, convert: bool) -> None:
            if self._disposed or not self._open or self._dispatching:
                return
            try:
                view = self._controller.switch_context(
                    context, convert_selection=bool(convert)
                )
            except Exception as exc:
                self._render_error(exc)
                return
            if view is not None:
                self._render(view)
            if self._open:
                self._refocus_input()

        def _activate_row(self, row_id: str) -> None:
            if self._disposed or not self._open or self._dispatching:
                return
            view = self._view
            if view is None or bool(getattr(view, "busy", False)):
                return
            if self._confirmation_token:
                return
            rows = tuple(getattr(view, "rows", ()) or ())
            row = presentation.highlighted_row(rows, row_id)
            if row is None or not bool(getattr(row, "enabled", False)):
                return
            now = self._clock()
            key = (_session(view), _generation(view), str(row_id))
            if (
                self._activation_key == key
                and (now - self._activation_at) < _ACTIVATION_COOLDOWN_SECONDS
            ):
                return
            self._activation_key = key
            self._activation_at = now
            session_id = _session(view)
            generation = _generation(view)
            self._dispatch(
                lambda: self._controller.activate(
                    row_id, session_id=session_id, generation=generation
                )
            )

        def _resolve_confirmation(self, accepted: bool) -> None:
            if self._disposed or not self._open or self._dispatching:
                return
            token = self._confirmation_token
            if not token:
                return
            self._confirmation_token = ""
            self._dispatch(lambda: self._controller.confirm(token, bool(accepted)))

        def _dispatch(self, action: Callable[[], Any]) -> None:
            if self._dispatching:
                return
            self._dispatching = True
            self._pre_dispatched = False
            try:
                result = action()
            except Exception as exc:
                self._render_error(exc)
                return
            finally:
                self._dispatching = False
            # The controller's dispatch return can still carry ``busy=True``
            # from its in-flight guard (the ``finally`` runs after the return
            # value is built).  Re-read the settled public view so a terminal
            # result is recognized as closed and a blocked one reopens.
            reader = getattr(self._controller, "view", None)
            if callable(reader):
                try:
                    latest = reader()
                except Exception:
                    latest = None
                if latest is not None:
                    result = latest
            if self._pre_dispatched:
                # The pre-dispatch hook already hid the palette, removed input
                # interception and restored viewport focus.  A blocked/error
                # result may deliberately reopen with a freshly observed view.
                if result is not None and not view_is_closed(result):
                    self._open = True
                    self._pre_dispatched = False
                    self._notify_open()
                    self._render(result)
                    self._install_filter(qtwidgets.QApplication.instance())
                    self.show()
                    self.raise_()
                    self.activateWindow()
                    self._refocus_input()
                else:
                    self._finish_close(restore=False)
                return
            if result is None or view_is_closed(result):
                self._finish_close(restore=True)
                return
            self._render(result)
            if self._open:
                self._refocus_input()

        # ------------------------------------------------------- pre-dispatch

        def _prepare_dispatch(self, *args: Any, **kwargs: Any) -> None:
            """Hide and drop input interception before native dispatch.

            Called synchronously by the controller.  The palette must not hold
            focus or a ShortcutOverride filter while a Max caddy/mode runs.
            """

            if self._disposed:
                return
            self._pre_dispatched = True
            self._open = False
            # Restore the host's Win32 accelerator state *before* the native
            # caddy/mode starts; it must not inherit the palette's lockout.
            self._notify_close()
            self._remove_filter()
            self._confirmation_token = ""
            self._confirm_bar.hide()
            self._hide_without_cancel()
            self._restore_focus()

        # ------------------------------------------------------------- close

        def _finish_close(self, restore: bool) -> None:
            self._open = False
            self._view = None
            self._confirmation_token = ""
            self._confirm_bar.hide()
            self._remove_filter()
            self._notify_close()
            self._hide_without_cancel()
            if restore:
                self._restore_focus()

        def _hide_without_cancel(self) -> None:
            self._suppress_hide += 1
            try:
                self.hide()
            finally:
                self._suppress_hide -= 1

        def _restore_focus(self) -> None:
            ref = self._return_focus_ref
            self._return_focus_ref = None
            if ref is None:
                return
            try:
                widget = ref()
            except TypeError:
                return
            if widget is None or not self._application_active():
                return
            try:
                if not widget.isVisible():
                    return
                widget.setFocus(qtcore.Qt.FocusReason.OtherFocusReason)
            except (RuntimeError, AttributeError):
                return

        def _refocus_deferred(self) -> None:
            """One event-loop-turn refocus, only while still the active window.

            Win32 may deliver activation changes between ``show_at`` and this
            callback; without the guard the palette would steal the caret back
            from Max's viewport (or another application) after an outside
            click or Alt+Tab.
            """

            self._refocus_input(respect_activation=True)

        def _refocus_input(self, respect_activation: bool = False) -> None:
            if self._disposed or not self._open:
                return
            # Never reclaim focus after the palette stopped being the active
            # window (outside click / alt-tab); the deferred focus check will
            # cancel instead.
            if respect_activation and not self._application_active():
                return
            try:
                if self._input.isVisible():
                    self._input.setFocus(qtcore.Qt.FocusReason.OtherFocusReason)
                    if self._input.hasFocus():
                        self._input_had_focus = True
            except RuntimeError:
                return

        # ------------------------------------------------------- focus watch

        def _schedule_focus_check(self) -> None:
            if self._disposed or not self._open or self._cancelled:
                return
            epoch = self._focus_epoch
            qtcore.QTimer.singleShot(0, lambda: self._after_focus_check(epoch))

        def _after_focus_check(self, epoch: Any) -> None:
            if (
                self._disposed
                or not self._open
                or self._cancelled
                or self._pre_dispatched
            ):
                return
            if epoch != self._focus_epoch:
                return  # stale generation/context; never act on it
            app = qtwidgets.QApplication.instance()
            if app is None:
                return
            focus = app.focusWidget()
            if focus is not None and self._owns_widget(focus):
                return  # child pill/confirmation focus is not an external blur
            active = app.activeWindow()
            external = (
                active is not None
                and active is not self
                and active is not self.parentWidget()
            )
            self._cancelled = True
            try:
                self._controller.cancel(reason="")
            except Exception:
                pass
            self._finish_close(restore=not external)

        # ----------------------------------------------------------- helpers

        def _owns_widget(self, watched: Any) -> bool:
            try:
                return watched is self or self.isAncestorOf(watched)
            except (RuntimeError, AttributeError, TypeError):
                return False

        def _application_active(self) -> bool:
            # Prefer Qt's application-activation state: on Windows another
            # foreground app must never have focus reclaimed by a palette close.
            try:
                state_getter = getattr(qtgui.QGuiApplication, "applicationState")
                active_state = qtcore.Qt.ApplicationState.ApplicationActive
                if state_getter() != active_state:
                    return False
            except (AttributeError, TypeError, RuntimeError):
                pass
            app = qtwidgets.QApplication.instance()
            if app is None:
                return False
            try:
                active = app.activeWindow()
            except AttributeError:
                return True
            return active is None or active is self or active is self.parentWidget()

        def _render_error(self, exc: BaseException) -> None:
            self._last_error = _exception_text(exc)
            try:
                self._status_label.setText(f"Palette error: {self._last_error}")
            except RuntimeError:
                pass

        def _install_filter(self, app: Any) -> None:
            if self._filter_installed or app is None:
                return
            app.installEventFilter(self)
            self._filter_installed = True

        def _remove_filter(self) -> None:
            if not self._filter_installed:
                return
            app = qtwidgets.QApplication.instance()
            if app is not None:
                app.removeEventFilter(self)
            self._filter_installed = False

        def _position_near(self, global_pos: Any, app: Any) -> None:
            if global_pos is None:
                global_pos = qtgui.QCursor.pos()
            screen = qtgui.QGuiApplication.screenAt(global_pos)
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

        @staticmethod
        def _weak_ref(widget: Any) -> Any:
            if widget is None:
                return None
            try:
                return weakref.ref(widget)
            except TypeError:
                return None

        @staticmethod
        def _application() -> Any:
            app = qtwidgets.QApplication.instance()
            if app is None:
                raise RuntimeError("SearchPalette requires an existing QApplication.")
            return app

        @staticmethod
        def _assert_gui_thread(app: Any) -> None:
            if qtcore.QThread.currentThread() is not app.thread():
                raise RuntimeError("SearchPalette must be used on the Qt GUI thread.")

    SearchPalette.__name__ = "SearchPalette"
    SearchPalette.__qualname__ = "SearchPalette"
    return SearchPalette


try:  # pragma: no cover - exercised on hosts with PySide6
    from PySide6 import QtCore as _QtCore  # type: ignore[import-not-found]
    from PySide6 import QtGui as _QtGui  # type: ignore[import-not-found]
    from PySide6 import QtWidgets as _QtWidgets  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - Qt-less hosts and tests

    class SearchPalette:  # type: ignore[no-redef]
        """Placeholder that fails finitely until PySide6 is importable."""

        def __init__(self, controller: Any, parent: Any = None) -> None:
            raise RuntimeError(
                "SearchPalette requires PySide6 in the host environment."
            )

else:
    SearchPalette = build_search_palette_class(_QtCore, _QtGui, _QtWidgets)


__all__ = ["SearchPalette", "build_search_palette_class"]

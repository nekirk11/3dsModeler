"""Explicit, manual bootstrap for experimental native 3ds Max 2025 tools.

There is no startup-script installation, registry change, global hotkey, or
right-click replacement.  Importing this module outside Max is safe: Qt,
``qtmax``, ``pymxs``, the controller, the panel, and the dialog are imported
inside the public functions.

Primary launch inside Max 2025's Python (main thread)::

    from max2025_menu_prototype.max_host import bootstrap
    bootstrap.show_tools()      # Max-parented native modeling tools panel
    bootstrap.show_menu()       # optional latched shared context menu
    bootstrap.uninstall()       # cancel pending input and remove prototype UI

``install_launcher`` is now a click-to-open alias for ``show_tools`` rather than
the old hold-to-open widget.  A click opens/raises the same shared panel; the
prototype still makes no viewport hold-release or shortcut claim.

The panel, popup, and modal parameter dialog share one controller and one
existing ``QApplication``.  Native geometry, mode entry, and undo are W2/host
responsibilities; this module only composes renderers.
"""

from __future__ import annotations

import time
from typing import Any, Optional

# Module-level strong references.  Qt widgets/timers must not be garbage
# collected while Max owns the parent window.
_controller: Any = None
_panel: Any = None
_panel_controller: Any = None
_popup: Any = None
_popup_controller: Any = None
_launcher: Any = None
_prompt_callback: Any = None
_prompt_controller: Any = None
_parameter_module: Any = None


# --------------------------------------------------------------------- imports


def _qt_parts() -> tuple:
    """Import Qt 6 modules lazily; Max 2025 ships PySide6 6.5.3."""

    from PySide6 import QtCore, QtGui, QtWidgets  # type: ignore[import-not-found]

    return QtCore, QtGui, QtWidgets


def _max_main_window() -> Optional[Any]:
    try:
        import qtmax  # type: ignore[import-not-found]
    except Exception:
        return None
    try:
        return qtmax.GetQMaxMainWindow()
    except Exception:
        return None


def _import_popup() -> Any:
    try:
        from ..qt_ui.popup import MenuPopup
    except ImportError as exc:
        raise ImportError(
            "The Qt popup is unavailable. Run this bootstrap inside 3ds Max "
            "with PySide6 installed, and keep the prototype package tree "
            "intact."
        ) from exc
    return MenuPopup


def _import_panel() -> Any:
    try:
        from ..qt_ui.modeling_panel import ModelingPanel
    except ImportError as exc:
        raise ImportError(
            "The native tools panel requires PySide6. Run this bootstrap "
            "inside 3ds Max 2025, and keep the prototype package tree intact."
        ) from exc
    return ModelingPanel


def _import_parameters() -> Any:
    try:
        from ..qt_ui import parameters as parameter_dialogs
    except ImportError as exc:
        raise ImportError(
            "The typed parameter dialog requires PySide6. Run this bootstrap "
            "inside 3ds Max 2025; no separate Qt install is supported."
        ) from exc
    if not callable(getattr(parameter_dialogs, "collect_parameters", None)):
        raise ImportError("The parameter dialog module is incomplete.")
    return parameter_dialogs


# ---------------------------------------------------------------- composition


def _create_controller() -> Any:
    from ..core.controller import MenuController
    from ..core.profile import default_profile
    from .adapter import MaxHostAdapter

    return MenuController(MaxHostAdapter(), default_profile())


def _set_controller(controller: Any = None) -> Any:
    global _controller

    if controller is not None:
        if _controller is controller:
            return controller
        # Replace only prototype-owned renderers/hooks; never dispose a caller's
        # previous controller here.
        _dispose_popup()
        _dispose_panel()
        _clear_parameter_prompt()
        _controller = controller
        return controller
    if _controller is None:
        _controller = _create_controller()
    return _controller


def _panel_for(controller: Any) -> Any:
    global _panel, _panel_controller

    if (
        _panel is not None
        and _panel_controller is controller
        and not getattr(_panel, "_disposed", False)
    ):
        return _panel
    if _panel is not None:
        _dispose_panel()

    ModelingPanel = _import_panel()
    panel = ModelingPanel(controller, _max_main_window())
    _panel = panel
    _panel_controller = controller

    setter = getattr(panel, "set_refresh_callback", None)
    if callable(setter):
        setter(lambda ctl=controller: _refresh_context(ctl))
    signal = getattr(panel, "menu_requested", None)
    connector = getattr(signal, "connect", None)
    if callable(connector):
        connector(lambda ctl=controller: show_menu(ctl))
    return panel


def _popup_for(controller: Any) -> Any:
    global _popup, _popup_controller

    if (
        _popup is not None
        and _popup_controller is controller
        and not getattr(_popup, "_disposed", False)
    ):
        return _popup
    if _popup is not None:
        _dispose_popup()

    MenuPopup = _import_popup()
    popup = MenuPopup(controller, _max_main_window())
    _popup = popup
    _popup_controller = controller
    return popup


def _ensure_parameter_prompt(controller: Any) -> None:
    """Register one dialog collector for the panel/bootstrap lifetime."""

    global _prompt_callback, _prompt_controller, _parameter_module

    if (
        _prompt_controller is controller
        and _prompt_callback is not None
        and _parameter_module is not None
    ):
        return

    _clear_parameter_prompt()
    setter = getattr(controller, "set_parameter_prompt", None)
    if not callable(setter):
        return
    module = _import_parameters()

    def prompt(spec: Any, context: Any, schema: Any) -> Any:
        parent = _max_main_window()
        if parent is None:
            parent = _panel
        return module.collect_parameters(spec, context, schema, parent=parent)

    _prompt_callback = prompt
    _prompt_controller = controller
    _parameter_module = module
    try:
        setter(prompt)
    except Exception:
        _prompt_callback = None
        _prompt_controller = None
        _parameter_module = None
        raise


def _clear_parameter_prompt() -> None:
    """Clear only the hook this module registered on its controller."""

    global _prompt_callback, _prompt_controller, _parameter_module

    callback = _prompt_callback
    controller = _prompt_controller
    _prompt_callback = None
    _prompt_controller = None
    _parameter_module = None
    if controller is None or callback is None:
        return
    clearer = getattr(controller, "clear_parameter_prompt", None)
    if callable(clearer):
        clearer(callback)


def _close_pending_dialogs() -> None:
    module = _parameter_module
    if module is None:
        return
    closer = getattr(module, "close_pending_dialogs", None)
    if callable(closer):
        try:
            closer()
        except Exception:
            pass


def _refresh_context(controller: Any) -> str | None:
    """Read a fresh context without showing the menu or editing geometry.

    The controller has no separate public refresh entry, so this uses the same
    ``open``/``handle`` contract with an immediate cancel.  It is skipped while
    a menu or parameter request is active, so it never steals another widget's
    pending input.  A string return is shown by the panel as a skipped/failed
    notice; ``None`` means the read succeeded.
    """

    summary_getter = getattr(controller, "summary", None)
    if callable(summary_getter):
        summary = summary_getter()
        if bool(getattr(summary, "busy", False)):
            return "Refresh skipped: another invocation is awaiting parameters."
        if str(getattr(summary, "phase", "closed")) != "closed":
            return "Refresh skipped: the shared menu is open."
    opener = getattr(controller, "open", None)
    if not callable(opener):
        return "Refresh unavailable: the controller has no public context probe."
    view = opener((0.0, 0.0), time.monotonic() * 1000.0, invocation="latched")
    if str(getattr(view, "phase", "closed")) == "closed":
        status = str(getattr(view, "status", "") or "host context unavailable")
        return f"Context refresh failed: {status}"
    handler = getattr(controller, "handle", None)
    if not callable(handler):
        return "Refresh unavailable: the controller cannot close the probe."
    from ..core.contracts import MenuEvent

    handler(
        MenuEvent(
            session_id=str(getattr(view, "session_id", "")),
            sequence=int(getattr(view, "sequence", 0)) + 1,
            kind="cancel",
            time_ms=time.monotonic() * 1000.0,
        )
    )
    return None


def _release_owned_native_mode(controller: Any) -> None:
    """Ask an optional W2 public hook to exit an exactly-owned native mode.

    No such hook exists in the frozen core API, so nothing is called when it is
    absent.  This deliberately never reaches through private adapter fields or
    exits a tool owned by the user.
    """

    summary_getter = getattr(controller, "summary", None)
    if callable(summary_getter):
        try:
            if bool(getattr(summary_getter(), "busy", False)):
                return
        except Exception:
            return
    for name in (
        "release_owned_native_mode",
        "release_owned_mode",
        "release_native_mode",
        "exit_owned_native_mode",
        "exit_owned_mode",
    ):
        release = getattr(controller, name, None)
        if callable(release):
            try:
                release()
            except Exception:
                pass
            return


# ------------------------------------------------------------------ public API


def report_diagnostics(*, include_details: bool = False) -> str:
    """Return read-only diagnostics for the current prototype/controller state.

    Existing controller state is read through the public ``summary()`` seam only
    and is passed to :func:`max_host.probe.report` as cached UI state.  This
    never creates a controller/panel, refreshes/cancels a menu, or reaches into
    controller private host fields.  When no summary exists, a bounded
    unavailable category is shown instead of initializing one.

    The main-thread check intentionally runs before any summary or host access.
    A busy summary makes probe skip all fresh host probing; the pending dialog
    is never inspected or dismissed here.
    """

    import threading

    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError(
            "Report diagnostics must run on the 3ds Max main thread."
        )

    summary = None
    controller = _controller
    if controller is not None:
        summary_getter = getattr(controller, "summary", None)
        if callable(summary_getter):
            try:
                summary = summary_getter()
            except Exception:
                # Never expose an arbitrary exception here; probe reports the
                # bounded unavailable category instead.
                summary = None

    from . import probe

    return probe.report(
        runtime=None, summary=summary, include_details=include_details
    )


def show_tools(controller: Any = None) -> Any:
    """Show or raise the primary Max-parented native modeling tools panel."""

    global _controller

    active_controller = _set_controller(controller)
    try:
        _ensure_parameter_prompt(active_controller)
        panel = _panel_for(active_controller)
    except Exception:
        # Do not leave half-composed prototype state after a failed launch.
        _clear_parameter_prompt()
        _dispose_panel()
        if _controller is active_controller:
            _controller = None
        raise
    panel.show()
    raise_method = getattr(panel, "raise_", None)
    if callable(raise_method):
        raise_method()
    activate = getattr(panel, "activateWindow", None)
    if callable(activate):
        activate()
    refresh = getattr(panel, "refresh", None)
    if callable(refresh):
        refresh()
    return panel


def show_menu(controller: Any = None) -> Any:
    """Open the optional shared menu in latched mode at the cursor."""

    global _controller

    _QtCore, QtGui, _QtWidgets = _qt_parts()
    active_controller = _set_controller(controller)
    try:
        _ensure_parameter_prompt(active_controller)
        popup = _popup_for(active_controller)
    except Exception:
        _clear_parameter_prompt()
        _dispose_popup()
        if _controller is active_controller:
            _controller = None
        raise
    popup.show_at(QtGui.QCursor.pos(), invocation="latched")
    return popup


def install_launcher(controller: Any = None) -> Any:
    """Click-to-open alias for :func:`show_tools`.

    The old hold-to-open launcher is intentionally retired.  The returned panel
    is the same shared instance used by later ``show_tools``/``show_menu`` calls.
    """

    global _launcher
    panel = show_tools(controller)
    _launcher = panel
    return panel


def uninstall() -> None:
    """Cancel pending prototype input and remove only prototype widgets.

    Safe to call repeatedly and outside Max.  Already committed scene edits are
    native Max history and are not undone here.  Foreign viewport tools are
    never cancelled.
    """

    global _controller, _launcher

    _close_pending_dialogs()
    _clear_parameter_prompt()
    _dispose_popup()
    _dispose_panel()
    _launcher = None

    controller = _controller
    _controller = None
    if controller is None:
        return
    _release_owned_native_mode(controller)
    dispose = getattr(controller, "dispose", None)
    if callable(dispose):
        try:
            dispose()
        except Exception:
            pass


# -------------------------------------------------------------------- disposal


def _dispose_popup() -> None:
    global _popup, _popup_controller

    popup = _popup
    _popup = None
    _popup_controller = None
    if popup is None:
        return
    try:
        popup.dispose()
    except Exception:
        pass


def _dispose_panel() -> None:
    global _panel, _panel_controller, _launcher

    panel = _panel
    _panel = None
    _panel_controller = None
    if _launcher is panel:
        _launcher = None
    if panel is None:
        return
    dispose = getattr(panel, "dispose", None)
    if callable(dispose):
        try:
            dispose()
            return
        except Exception:
            pass
    try:
        panel.close()
    except Exception:
        pass
    try:
        panel.deleteLater()
    except Exception:
        pass

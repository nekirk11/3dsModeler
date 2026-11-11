"""Explicit, manual bootstrap for the 3dsModeler V2 spotlight palette.

There is no startup-script installation, registry change, global hotkey, or
right-click replacement.  Importing this module outside Max is safe: Qt,
``qtmax``, ``pymxs``, the palette controller, the legacy controllers, and the
V1 widgets are imported inside the public functions.

Primary launch inside Max 2025's Python (main thread)::

    from max2025_menu_prototype.max_host import bootstrap
    bootstrap.show_palette()          # search-first V2 palette
    bootstrap.switch_context("edge")  # macro context switch, no palette needed
    bootstrap.uninstall()             # release only in-memory UI/hooks

``show_tools``, ``show_menu`` and ``install_launcher`` are compatibility entry
points for native callers; all three now open the same V2 palette instead of
the retired one-shot tools panel or blind parameter dialog.  The legacy panel,
popup and typed parameter dialog remain available for the sandbox/desktop tests
but are never composed on a normal native launch.

The palette and controller share one existing ``QApplication``.  Native
geometry, mode entry, and undo remain host responsibilities; this module only
composes renderers, system callbacks, and teardown.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any, Mapping, Optional

# Module-level strong references.  Qt widgets/timers must not be garbage
# collected while Max owns the parent window.  The V2 host is intentionally kept
# across ``uninstall()`` so its rollback lockout survives a UI-only unload.
_controller: Any = None
_panel: Any = None
_panel_controller: Any = None
_popup: Any = None
_popup_controller: Any = None
_launcher: Any = None
_prompt_callback: Any = None
_prompt_controller: Any = None
_parameter_module: Any = None

_palette: Any = None
_palette_controller: Any = None
_palette_host: Any = None
_owns_palette_controller: bool = False


# --------------------------------------------------------------------- imports


def _qt_parts() -> tuple:
    """Import Qt 6 modules lazily; Max 2025 ships PySide6 6.5.3."""

    from PySide6 import QtCore, QtGui, QtWidgets  # type: ignore[import-not-found]

    return QtCore, QtGui, QtWidgets


def _qt_application() -> Any:
    """Return Max's existing QApplication or raise a finite, actionable error."""

    _QtCore, _QtGui, QtWidgets = _qt_parts()
    application = QtWidgets.QApplication.instance()
    if application is None:
        raise RuntimeError(
            "Start 3ds Max 2025 before opening the 3dsModeler palette; its Qt "
            "application was not found."
        )
    return application


def _assert_main_thread() -> None:
    import threading

    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError(
            "The 3dsModeler palette must be opened on the 3ds Max main thread."
        )


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


def _import_palette() -> Any:
    try:
        from ..qt_ui.palette import SearchPalette
    except ImportError as exc:
        raise ImportError(
            "The V2 spotlight palette requires PySide6. Run this bootstrap "
            "inside 3ds Max 2025, and keep the package tree intact."
        ) from exc
    return SearchPalette


# ---------------------------------------------------------------- composition


def _create_controller() -> Any:
    """Create the frozen V1 controller (legacy sandbox/renderer tests only)."""

    from ..core.controller import MenuController
    from ..core.profile import default_profile
    from .adapter import MaxHostAdapter

    return MenuController(MaxHostAdapter(), default_profile())


def _checkout_root() -> Path:
    """Return the package checkout root, never the process working directory."""

    return Path(__file__).resolve().parents[1]


def _loaded_checkout_sha() -> str:
    """Read the launcher's already-verified local SHA without starting Git."""

    session = sys.modules.get("_max2025_pull_and_load_session")
    value = getattr(session, "loaded_head_sha", "") if session is not None else ""
    return value if isinstance(value, str) else ""


def _create_palette_controller(
    *,
    host: Any = None,
    atlas: Any = None,
    checkout_root: Path | str | None = None,
) -> Any:
    """Compose the immutable atlas, host adapter, controller and callbacks.

    The host is retained across a later UI-only uninstall so its persistent
    rollback lockout cannot be erased by reopening the palette in the same Max
    session.  A supplied host/atlas is used verbatim for integration tests.
    """

    global _palette_host

    from ..core.atlas import load_atlas
    from ..core.palette_controller import PaletteController

    root = Path(checkout_root) if checkout_root is not None else _checkout_root()
    loaded = load_atlas(root)
    active_atlas = atlas if atlas is not None else loaded.atlas

    active_host = host
    if active_host is None:
        if _palette_host is not None:
            active_host = _palette_host
        else:
            from .adapter import ManeuverHostAdapter

            active_host = ManeuverHostAdapter(
                atlas=active_atlas,
                overlay_warning=loaded.overlay_warning or "",
                loaded_sha=_loaded_checkout_sha(),
            )
    _palette_host = active_host

    return PaletteController(
        active_atlas,
        active_host,
        _system_callbacks(),
        warnings=loaded.warnings,
    )


def _validate_palette_controller(controller: Any) -> Any:
    """Reject a legacy controller rather than feeding it into the V2 seam."""

    required = (
        "open",
        "view",
        "set_query",
        "activate",
        "switch_context",
        "cancel",
        "dispose",
    )
    for name in required:
        if not callable(getattr(controller, name, None)):
            raise TypeError(
                "show_palette() requires the V2 PaletteController interface "
                "(missing %s); the legacy V1 controller is not accepted." % name
            )
    return controller


def _set_palette_controller(controller: Any = None) -> Any:
    global _palette_controller, _owns_palette_controller

    if controller is None:
        existing = _palette_controller
        if existing is not None and not getattr(existing, "_disposed", False):
            return existing
        controller = _create_palette_controller()
        owns = True
    else:
        _validate_palette_controller(controller)
        owns = False

    if _palette_controller is not controller:
        _dispose_palette()
        _palette_controller = controller
        _owns_palette_controller = owns
    return controller


def _disable_max_accelerators(palette: Any) -> None:
    """Keep 3ds Max's Win32 shortcuts from stealing search-field typing."""

    try:
        import qtmax  # type: ignore[import-not-found]
    except Exception:
        return
    disable = getattr(qtmax, "DisableMaxAcceleratorsOnFocus", None)
    if not callable(disable):
        return
    try:
        disable(palette.input_widget(), True)
    except Exception:
        # Accelerator lockout is best-effort; never fail palette construction.
        pass


def _palette_for(controller: Any) -> Any:
    global _palette

    if (
        _palette is not None
        and _palette_controller is controller
        and not getattr(_palette, "_disposed", False)
    ):
        return _palette
    if _palette is not None:
        _dispose_palette()

    SearchPalette = _import_palette()
    palette = SearchPalette(controller, _max_main_window())
    _disable_max_accelerators(palette)
    _palette = palette
    return palette


def _panel_for(controller: Any) -> Any:
    """Legacy V1 panel composition; kept private/testable, not a launch path."""

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
    """Legacy V1 popup composition; kept private/testable, not a launch path."""

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
    """Legacy V1 parameter prompt registration; never used by V2 launch."""

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
    """Read a fresh V1 context without showing the menu or editing geometry."""

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
    """Ask an optional V1 public hook to exit an exactly-owned native mode."""

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


# ------------------------------------------------------------- system callbacks


def _system_callbacks() -> Mapping[Any, Any]:
    """Return the fixed, bootstrap-owned ``>`` system callback mapping."""

    from ..core.maneuver_contracts import SystemCommand

    return {
        SystemCommand.PULL_UPDATES: _system_pull_updates,
        SystemCommand.DIAGNOSTICS: _system_diagnostics,
        SystemCommand.UNLOAD_UI: _system_unload_ui,
    }


def _system_pull_updates() -> Any:
    """Start the trusted asynchronous update flow for the resolved checkout."""

    from ..core.contracts import LaunchResult

    try:
        from ..tools import max_pull_and_load as launcher
    except Exception:
        return LaunchResult(
            "blocked",
            "pull_updates",
            "The launcher companion is unavailable in this checkout.",
        )
    try:
        launcher.start(checkout=_checkout_root())
    except launcher.LauncherError as exc:
        return LaunchResult("blocked", "pull_updates", str(exc))
    return LaunchResult(
        "applied", "pull_updates", "Update check scheduled; results appear in the Listener."
    )


def _system_diagnostics() -> str:
    """Print the read-only report and return a short palette status."""

    report_diagnostics()
    return "Diagnostics written to the Listener."


def _system_unload_ui() -> Any:
    """Close the palette, then uninstall UI/hooks after the controller settles."""

    from ..core.contracts import LaunchResult

    def run() -> None:
        try:
            uninstall()
        except Exception:
            pass

    scheduled = False
    try:
        from PySide6 import QtCore  # type: ignore[import-not-found]

        QtCore.QTimer.singleShot(0, run)
        scheduled = True
    except Exception:
        scheduled = False
    if not scheduled:
        run()
    return LaunchResult(
        "applied",
        "unload_ui",
        "UI unloaded; macros, hotkeys, scene edits and native lockout are unchanged.",
    )


# ------------------------------------------------------------------ public API


def report_diagnostics(*, include_details: bool = False) -> str:
    """Return read-only diagnostics for the current prototype/V2 host state.

    Existing legacy controller state is read through the public ``summary()``
    seam only.  The V2 adapter supplies redacted atlas/prep/handler/lockout
    fields through ``diagnostic_state()``; no controller, palette, host
    observation, preparation, or Git operation is started here.
    """

    _assert_main_thread()

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
        runtime=None,
        summary=summary,
        include_details=include_details,
        v2=_palette_host,
        loaded_sha=_loaded_checkout_sha(),
    )


def show_palette(controller: Any = None) -> Any:
    """Show or raise the one search-first V2 palette.

    A supplied controller must implement the V2 :class:`PaletteController`
    seam.  It is caller-owned and is not disposed by :func:`uninstall`.
    """

    global _palette_controller, _owns_palette_controller

    _assert_main_thread()
    _qt_application()
    _retire_legacy_ui()

    active_controller = _set_palette_controller(controller)
    try:
        palette = _palette_for(active_controller)
        palette.show_at()
    except Exception:
        # Never leave half-composed prototype state after a failed launch.
        _dispose_palette()
        if _owns_palette_controller and _palette_controller is active_controller:
            _palette_controller = None
            _owns_palette_controller = False
        raise
    return palette


def show_tools(controller: Any = None) -> Any:
    """Compatibility alias: open the V2 palette, never the retired V1 panel."""

    return show_palette(controller)


def show_menu(controller: Any = None) -> Any:
    """Compatibility alias: open the V2 palette, never the V1 blind popup."""

    return show_palette(controller)


def install_launcher(controller: Any = None) -> Any:
    """Compatibility alias: the click-to-open launcher is the V2 palette."""

    global _launcher

    palette = show_palette(controller)
    _launcher = palette
    return palette


def switch_context(context: Any, convert_selection: bool = False) -> Any:
    """Switch the prepared Max context without opening the palette.

    The value may be a :class:`ManeuverContext` or one of the six public V2
    strings.  The V1 ``polygon`` spelling is intentionally not accepted.
    """

    _assert_main_thread()

    from ..core.maneuver_contracts import ManeuverContext

    if isinstance(context, ManeuverContext):
        target = context
    elif isinstance(context, str):
        try:
            target = ManeuverContext(context)
        except ValueError as exc:
            raise ValueError(
                "Unknown V2 context %r; use object, vertex, edge, border, face "
                "or element." % (context,)
            ) from exc
    else:
        raise TypeError("context must be a ManeuverContext or string")

    active_controller = _palette_controller
    if active_controller is None or getattr(active_controller, "_disposed", False):
        active_controller = _create_palette_controller()
        _set_palette_controller(active_controller)
        # Injected controllers are not expected here; mark this one owned.
        globals()["_owns_palette_controller"] = True

    was_open = _palette_is_open()
    view = active_controller.switch_context(
        target, convert_selection=bool(convert_selection)
    )
    if was_open and _palette is not None:
        refresh = getattr(_palette, "refresh", None)
        if callable(refresh):
            try:
                refresh()
            except Exception:
                pass
    return view


def uninstall() -> None:
    """Release only in-memory prototype UI and owned hooks.

    Safe to call repeatedly and outside Max.  Already committed scene edits are
    native Max history and are not undone here.  Persistent MacroScripts,
    assigned hotkeys, the launcher's loaded-SHA/restart state, native modes and
    the V2 rollback lockout are deliberately untouched.  A caller-supplied
    palette controller is not disposed.
    """

    global _controller, _launcher, _palette_controller, _owns_palette_controller

    _close_pending_dialogs()
    _clear_parameter_prompt()
    _dispose_popup()
    _dispose_panel()
    _launcher = None

    _dispose_palette()
    palette_controller = _palette_controller
    owns_palette = _owns_palette_controller
    _palette_controller = None
    _owns_palette_controller = False
    if palette_controller is not None and owns_palette:
        dispose = getattr(palette_controller, "dispose", None)
        if callable(dispose):
            try:
                dispose()
            except Exception:
                pass

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


def _retire_legacy_ui() -> None:
    """Dispose only V1 renderers/prompt hooks before a V2 launch."""

    global _launcher

    _close_pending_dialogs()
    _clear_parameter_prompt()
    _dispose_popup()
    _dispose_panel()
    _launcher = None


def _palette_is_open() -> bool:
    palette = _palette
    if palette is None:
        return False
    state = getattr(palette, "_open", None)
    if isinstance(state, bool):
        return state
    visible = getattr(palette, "isVisible", None)
    if callable(visible):
        try:
            return bool(visible())
        except Exception:
            return False
    return False


def _dispose_palette() -> None:
    global _palette

    palette = _palette
    _palette = None
    if palette is None:
        return
    try:
        palette.dispose()
    except Exception:
        pass


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

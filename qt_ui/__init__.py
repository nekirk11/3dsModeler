"""Optional Qt renderer package.

Public symbols are imported lazily so that importing the package never pulls in
PySide6, creates a ``QApplication``, or looks up a host.  The native parameter
dialog and panel therefore remain safe to import outside Max.
"""

from __future__ import annotations

from typing import Any

__all__ = ["MenuPopup"]

# Additional native renderers are available lazily by name but stay out of
# ``__all__`` so the historical optional-Qt package contract is unchanged.


def __getattr__(name: str) -> Any:
    if name == "MenuPopup":
        from .popup import MenuPopup

        return MenuPopup
    if name == "ModelingPanel":
        from .modeling_panel import ModelingPanel

        return ModelingPanel
    if name == "ParameterDialog":
        from .parameters import ParameterDialog

        return ParameterDialog
    if name == "collect_parameters":
        from .parameters import collect_parameters

        return collect_parameters
    if name == "close_pending_dialogs":
        from .parameters import close_pending_dialogs

        return close_pending_dialogs
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

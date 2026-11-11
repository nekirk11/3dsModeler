"""Entry point for ``python3 -m max2025_menu_prototype.qt_ui``.

This optional desktop simulation requires PySide6 and a display-capable
machine.  It uses only ``SimulatedHost`` and never imports ``pymxs``.
"""

from .popup import main

if __name__ == "__main__":
    raise SystemExit(main())

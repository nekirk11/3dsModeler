"""Experimental Max 2025 context-menu prototype.

This package is original prototype code.  It deliberately has no runtime
dependency on the Houdini reference tree, no import-time UI or host access,
and no global hooks.  The shared, stdlib-only core lives in
:mod:`max2025_menu_prototype.core`; renderers and host adapters are separate.
"""

__all__ = ["__version__"]

__version__ = "0.1.0-experiment"

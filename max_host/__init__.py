"""Experimental native 3ds Max 2025 host bridge.

Importing this package never imports ``pymxs``, PySide6, or ``qtmax``.  The
runtime is resolved lazily on the first host read, or injected for contract
tests.  ``bootstrap`` is owned by a separate stream and is intentionally not
imported here.

No operation in this package was exercised against a real 3ds Max 2025 install;
the documented bindings are ``runtime_status='not_run'``.  See
``docs/MAX2025-VALIDATION.md`` and ``docs/NATIVE-MODELING.md``.
"""

from .adapter import MaxHostAdapter
from .bindings import (
    CATALOG_ACTION_IDS,
    FIXED_MAX_COMMAND_LITERALS,
    NATIVE_BINDINGS,
    NativeBinding,
    launch_native,
)
from .transactions import TransactionOutcome, run_undoable

_LAZY_SUBMODULES = frozenset({"operations", "probe", "transactions"})

__all__ = [
    "CATALOG_ACTION_IDS",
    "FIXED_MAX_COMMAND_LITERALS",
    "MaxHostAdapter",
    "NATIVE_BINDINGS",
    "NativeBinding",
    "TransactionOutcome",
    "launch_native",
    "run_undoable",
]


def __getattr__(name: str):
    if name in _LAZY_SUBMODULES:
        import importlib

        module = importlib.import_module("%s.%s" % (__name__, name))
        globals()[name] = module
        return module
    raise AttributeError("module %r has no attribute %r" % (__name__, name))

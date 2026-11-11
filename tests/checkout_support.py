"""Fresh-subprocess helpers that bootstrap the package alias for tests.

The prototype checkout may be named ``max2025_menu_prototype`` (the original
package directory) or ``3dsModeler`` (the public Git checkout).  Subprocess
tests must work in both layouts without relying on ``PYTHONPATH`` or a
package-named parent directory.

``run_python`` starts a clean interpreter in a neutral temporary working
directory, removes ``PYTHONPATH``, and installs the checkout directory itself
as ``max2025_menu_prototype`` before running the caller's snippet.  This mirrors
the launcher's alias seam; it does not import Qt, ``pymxs``, or Max.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

CHECKOUT_ROOT = Path(__file__).resolve().parents[1]

_ALIAS_TEMPLATE = """\
import importlib.util as _importlib_util
import sys as _sys
from pathlib import Path as _Path

_ROOT = _Path({root!r})
_spec = _importlib_util.spec_from_file_location(
    "max2025_menu_prototype",
    _ROOT / "__init__.py",
    submodule_search_locations=[str(_ROOT)],
)
if _spec is None or _spec.loader is None:
    raise RuntimeError("test checkout has no package initializer")
_package = _importlib_util.module_from_spec(_spec)
_sys.modules["max2025_menu_prototype"] = _package
_spec.loader.exec_module(_package)
"""


def alias_source(root: Path | str = CHECKOUT_ROOT) -> str:
    """Return Python source that registers *root* as the package alias."""

    return _ALIAS_TEMPLATE.format(root=str(Path(root)))


def run_python(
    code: str,
    *,
    timeout: float = 60.0,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run *code* in a fresh interpreter with the checkout alias installed."""

    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    if extra_env:
        environment.update(extra_env)
    program = alias_source() + "\n" + code
    with tempfile.TemporaryDirectory(prefix="max2025-test-") as workdir:
        return subprocess.run(
            [sys.executable, "-B", "-c", program],
            cwd=workdir,
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

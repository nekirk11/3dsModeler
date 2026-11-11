"""Regression: the whole suite works from a checkout literally named 3dsModeler.

The nested child run is guarded by an environment sentinel so this test can be
part of the suite it launches.  The copy is built from this repository alone;
private siblings are never inspected.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

CHECKOUT_ROOT = Path(__file__).resolve().parents[1]
SENTINEL = "MAX2025_LITERAL_CHECKOUT_CHILD"
SKIP_REASON = "nested literal-checkout run"

IGNORED_DIRS = {".git", "__pycache__", ".mypy_cache", ".pytest_cache"}
IGNORED_FILES = {"user_aliases.json"}


def _ignore(directory: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        path = Path(directory) / name
        if name in IGNORED_DIRS or name in IGNORED_FILES:
            ignored.add(name)
        elif name.endswith((".pyc", ".pyo", ".zip", ".sha256")):
            ignored.add(name)
        elif name.endswith(".manifest.json"):
            ignored.add(name)
        elif path.is_symlink():
            ignored.add(name)
    return ignored


ALIAS_BLOCK = """\
import importlib.util as _importlib_util
import sys as _sys
from pathlib import Path as _Path

_ROOT = _Path({root!r})
_spec = _importlib_util.spec_from_file_location(
    "max2025_menu_prototype",
    _ROOT / "__init__.py",
    submodule_search_locations=[str(_ROOT)],
)
_package = _importlib_util.module_from_spec(_spec)
_sys.modules["max2025_menu_prototype"] = _package
_spec.loader.exec_module(_package)
"""

CHILD_PROGRAM = ALIAS_BLOCK + """\
import os
import unittest

os.environ[{sentinel!r}] = "1"
suite = unittest.defaultTestLoader.discover(
    str(_ROOT / "tests"), pattern="test_*.py"
)
result = unittest.TextTestRunner(verbosity=1).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
"""


class LiteralCheckoutTests(unittest.TestCase):
    @unittest.skipIf(os.environ.get(SENTINEL) == "1", SKIP_REASON)
    def test_full_suite_from_literal_3dsmodeler_checkout(self) -> None:
        with tempfile.TemporaryDirectory(prefix="max2025-literal-") as workdir:
            parent = Path(workdir) / "parent with space" / "Unicodé"
            target = parent / "3dsModeler"
            parent.mkdir(parents=True)
            shutil.copytree(
                CHECKOUT_ROOT,
                target,
                ignore=_ignore,
                symlinks=False,
            )

            self.assertEqual(target.name, "3dsModeler")
            self.assertTrue((target / "__init__.py").is_file())
            self.assertFalse((target / "user_aliases.json").exists())

            environment = dict(os.environ)
            environment.pop("PYTHONPATH", None)
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            environment.pop(SENTINEL, None)
            neutral = Path(workdir) / "neutral cwd"
            neutral.mkdir()

            program = CHILD_PROGRAM.format(root=str(target), sentinel=SENTINEL)
            completed = subprocess.run(
                [sys.executable, "-B", "-c", program],
                cwd=str(neutral),
                env=environment,
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )

        self.assertEqual(
            completed.returncode,
            0,
            msg=(completed.stdout[-4000:] + completed.stderr[-4000:]),
        )
        self.assertNotIn("FAILED", completed.stderr)

    @unittest.skipIf(os.environ.get(SENTINEL) == "1", SKIP_REASON)
    def test_literal_checkout_alias_loads_json_and_stays_host_free(self) -> None:
        with tempfile.TemporaryDirectory(prefix="max2025-literal-") as workdir:
            target = Path(workdir) / "3dsModeler"
            shutil.copytree(CHECKOUT_ROOT, target, ignore=_ignore, symlinks=False)
            program = ALIAS_BLOCK.format(root=str(target)) + textwrap.dedent(
                """\
                import os
                from pathlib import Path

                import max2025_menu_prototype
                from max2025_menu_prototype.core.atlas import load_atlas

                root = Path(max2025_menu_prototype.__file__).resolve().parent
                assert root.name == "3dsModeler", root
                for name in ("core", "max_host", "qt_ui", "tools"):
                    assert (root / name).is_dir(), name
                assert "PySide6" not in os.environ.get("LEAK", "")
                assert "pymxs" not in __import__("sys").modules
                assert "PySide6" not in __import__("sys").modules
                result = load_atlas(root)
                assert len(result.atlas.ids) == 36
                print("host-free-ok")
                """
            )
            environment = dict(os.environ)
            environment.pop("PYTHONPATH", None)
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            completed = subprocess.run(
                [sys.executable, "-B", "-c", program],
                cwd=workdir,
                env=environment,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )

        self.assertEqual(
            completed.returncode,
            0,
            msg=(completed.stdout[-4000:] + completed.stderr[-4000:]),
        )
        self.assertIn("host-free-ok", completed.stdout)

    @unittest.skipIf(os.environ.get(SENTINEL) == "1", SKIP_REASON)
    def test_checkout_metadata_is_relative_and_complete(self) -> None:
        atlas = json.loads(
            (CHECKOUT_ROOT / "core" / "maneuvers.json").read_text(encoding="utf-8")
        )
        self.assertEqual(atlas["schema_version"], 2)
        self.assertEqual(len(atlas["maneuvers"]), 36)
        self.assertTrue((CHECKOUT_ROOT / "core" / "maneuvers.json").is_file())


if __name__ == "__main__":
    unittest.main()

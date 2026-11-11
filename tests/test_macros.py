"""Persistent MacroScript ownership, activation, and local hotkey tests."""

from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from max2025_menu_prototype.max_host import bootstrap
from max2025_menu_prototype.tools import max_pull_and_load as launcher

SHA_ONE = "1" * 40


def write_minimal_checkout(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "__init__.py").write_text("", encoding="utf-8")
    (root / "core").mkdir(exist_ok=True)
    (root / "max_host").mkdir(exist_ok=True)
    (root / "qt_ui").mkdir(exist_ok=True)
    (root / "core" / "default_profile.json").write_text(
        '{"schema_version": 1}\n', encoding="utf-8"
    )
    return root


class MacroSourceTests(unittest.TestCase):
    def test_committed_ms_matches_generator_exactly(self) -> None:
        ms_path = Path(launcher.__file__).parent / "max_pull_and_load.ms"
        self.assertEqual(
            ms_path.read_text(encoding="utf-8"), launcher.render_ms_source()
        )

    def test_all_thirteen_names_keep_the_stable_category(self) -> None:
        source = launcher.render_macro_source()
        self.assertEqual(launcher.MACRO_CATEGORY, "3dsModeler")
        self.assertEqual(len(launcher.MACRO_NAMES), 13)
        self.assertEqual(len(set(launcher.MACRO_NAMES)), 13)
        for name in launcher.MACRO_NAMES:
            with self.subTest(name=name):
                self.assertIn("macroScript %s\n" % name, source)
                self.assertIn('category:"3dsModeler"', source)
        self.assertIn("ThreeDsModeler_Search", source)
        for context in ("object", "vertex", "edge", "border", "face", "element"):
            self.assertIn(
                launcher.MACRO_CONTEXT_NAMES[context], launcher.MACRO_NAMES
            )
        for context in ("object", "vertex", "edge", "border", "face", "element"):
            self.assertIn(
                launcher.MACRO_CONVERT_NAMES[context], launcher.MACRO_NAMES
            )

    def test_macro_file_header_is_stable_and_ownership_is_explicit(self) -> None:
        source = launcher.render_macro_source()
        self.assertTrue(source.startswith(launcher.MACRO_FILE_HEADER))
        self.assertIn("do not edit", launcher.MACRO_FILE_HEADER)


class ActivationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.directory = Path(self._temp.name) / "MacroScripts"
        self.addCleanup(self._temp.cleanup)

    def _fake_pymxs(self, file_in=None, macros_load=None, macros_dir=None) -> types.ModuleType:
        runtime = SimpleNamespace()
        if file_in is not None:
            runtime.fileIn = file_in
        if macros_load is not None:
            runtime.macros = SimpleNamespace(load=macros_load)
        if macros_dir is not None:
            runtime.execute = lambda command: str(macros_dir)
        module = types.ModuleType("pymxs")
        module.runtime = runtime
        return module

    def test_register_macros_loads_the_file_in_the_live_session(self) -> None:
        calls: list = []
        fake = self._fake_pymxs(file_in=lambda path: calls.append(path))
        with mock.patch.dict(sys.modules, {"pymxs": fake}):
            path = launcher.register_macros(target_dir=self.directory)

        self.assertEqual(calls, [str(path)])
        self.assertTrue(path.is_file())
        self.assertTrue(path.read_text(encoding="utf-8").startswith(
            launcher.MACRO_FILE_HEADER
        ))

    def test_activated_existing_file_still_registers_without_rewriting(self) -> None:
        calls: list = []
        fake = self._fake_pymxs(file_in=lambda path: calls.append(path))
        with mock.patch.dict(sys.modules, {"pymxs": fake}):
            first = launcher.register_macros(target_dir=self.directory)
            stamp = first.stat().st_mtime_ns
            second = launcher.register_macros(target_dir=self.directory)

        self.assertEqual(first, second)
        self.assertEqual(second.stat().st_mtime_ns, stamp)
        self.assertEqual(calls, [str(first), str(second)])

    def test_file_in_failure_falls_back_to_macros_load(self) -> None:
        loaded: list = []

        def fail(_path):
            raise RuntimeError("fileIn unavailable")

        fake = self._fake_pymxs(
            file_in=fail, macros_load=lambda directory: loaded.append(directory)
        )
        with mock.patch.dict(sys.modules, {"pymxs": fake}):
            path = launcher.register_macros(target_dir=self.directory)

        self.assertEqual(loaded, [str(self.directory)])
        self.assertTrue(path.is_file())

    def test_embedded_installer_compresses_and_activates(self) -> None:
        namespace: dict = {"__name__": "embedded_test"}
        embedded = launcher.extract_embedded_python(launcher.render_ms_source())
        exec(compile(embedded, "<3dsModeler-embedded>", "exec"), namespace)
        self.assertNotEqual(namespace["MAX_MACROS_SOURCE_B64"], "")
        calls: list = []
        fake = self._fake_pymxs(
            file_in=lambda path: calls.append(path), macros_dir=self.directory
        )
        with mock.patch.dict(sys.modules, {"pymxs": fake}):
            self.assertTrue(namespace["_install_macros"]())
        self.assertEqual(len(calls), 1)
        written = Path(calls[0])
        self.assertEqual(
            written.read_text(encoding="utf-8"), launcher.render_macro_source()
        )
        written.unlink()


class ConflictAndTeardownTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.directory = Path(self._temp.name) / "MacroScripts"
        self.addCleanup(self._temp.cleanup)

    def test_foreign_mcr_is_never_overwritten(self) -> None:
        self.directory.mkdir(parents=True)
        foreign = self.directory / launcher.MACRO_FILE_NAME
        foreign.write_text("-- user macro\n", encoding="utf-8")

        with self.assertRaises(launcher.LauncherError) as raised:
            launcher.register_macros(target_dir=self.directory)

        self.assertEqual(raised.exception.category, "macro_conflict")
        self.assertEqual(foreign.read_text(encoding="utf-8"), "-- user macro\n")

    def test_uninstall_leaves_the_macro_file_untouched(self) -> None:
        path = launcher.register_macros(target_dir=self.directory)
        before = path.read_bytes()

        bootstrap.uninstall()
        bootstrap.uninstall()

        self.assertTrue(path.is_file())
        self.assertEqual(path.read_bytes(), before)


class LocalInvokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = write_minimal_checkout(Path(self._temp.name) / "3dsModeler")
        self.addCleanup(self._temp.cleanup)
        sys.modules.pop(launcher.SESSION_MODULE_NAME, None)
        self.addCleanup(sys.modules.pop, launcher.SESSION_MODULE_NAME, None)

    class FakeBootstrap:
        def __init__(self) -> None:
            self.shown = 0
            self.switches: list = []

        def show_palette(self):
            self.shown += 1
            return "palette"

        def switch_context(self, context, *, convert_selection=False):
            self.switches.append((context, convert_selection))
            return "switch"

    def test_invoke_local_is_network_free_and_uses_the_local_head(self) -> None:
        fake_bootstrap = self.FakeBootstrap()
        loaded: list = []
        session = launcher._session_state()
        session.bootstrap = fake_bootstrap

        with mock.patch.object(
            launcher, "_validate_checkout", return_value={"head": SHA_ONE}
        ), mock.patch.object(
            launcher,
            "load_checkout_package",
            side_effect=lambda root, sha: loaded.append((root, sha)),
        ), mock.patch.object(
            launcher, "start", side_effect=AssertionError("network update")
        ), mock.patch.object(
            launcher, "_fetch_origin_dev", side_effect=AssertionError("fetch")
        ), mock.patch.object(
            launcher, "_clone_checkout", side_effect=AssertionError("clone")
        ), mock.patch(
            "subprocess.run", side_effect=AssertionError("subprocess")
        ), mock.patch(
            "urllib.request.urlopen", side_effect=AssertionError("network")
        ):
            result = launcher.invoke_local(checkout=self.root)

        self.assertEqual(result, "palette")
        self.assertEqual(fake_bootstrap.shown, 1)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0][1], SHA_ONE)
        self.assertTrue(loaded[0][0].name == "3dsModeler")

    def test_invoke_local_context_macro_does_not_open_the_palette(self) -> None:
        fake_bootstrap = self.FakeBootstrap()
        session = launcher._session_state()
        session.bootstrap = fake_bootstrap

        with mock.patch.object(
            launcher, "_validate_checkout", return_value={"head": SHA_ONE}
        ), mock.patch.object(launcher, "load_checkout_package"):
            result = launcher.invoke_local(
                action="edge", checkout=self.root, convert_selection=True
            )

        self.assertEqual(result, "switch")
        self.assertEqual(fake_bootstrap.shown, 0)
        self.assertEqual(fake_bootstrap.switches, [("edge", True)])

    def test_invoke_local_rejects_unknown_fixed_enums(self) -> None:
        with self.assertRaises(launcher.LauncherError):
            launcher.invoke_local(action="shell", checkout=self.root)
        with self.assertRaises(launcher.LauncherError):
            launcher.invoke_local(action="edge", context="face", checkout=self.root)
        with self.assertRaises(launcher.LauncherError):
            launcher.invoke_local(action="edge", convert_selection="yes", checkout=self.root)


if __name__ == "__main__":
    unittest.main()

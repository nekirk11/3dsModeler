"""Persistent MacroScript ownership, activation, and local hotkey tests."""

from __future__ import annotations

import contextlib
import io
import json
import ntpath
import os
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
COMPANION_NAME = "_max2025_pull_and_load_companion"


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

    def _fake_pymxs(self, file_in=None, macros_load=None, macros_dir=None, startup_dir=None) -> types.ModuleType:
        runtime = SimpleNamespace()
        if file_in is not None:
            runtime.fileIn = file_in
        if macros_load is not None:
            runtime.macros = SimpleNamespace(load=macros_load)
        if macros_dir is not None or startup_dir is not None:
            runtime.execute = lambda command: str(
                startup_dir if "userStartupScripts" in command else macros_dir
            )
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
            file_in=lambda path: calls.append(path),
            macros_dir=self.directory,
            startup_dir=Path(self._temp.name) / "Startup",
        )
        with mock.patch.dict(sys.modules, {"pymxs": fake}):
            self.assertTrue(namespace["_install_macros"]())
        self.assertEqual(len(calls), 1)
        written = Path(calls[0])
        self.assertEqual(
            written.read_text(encoding="utf-8"), launcher.render_macro_source()
        )
        written.unlink()

    def test_register_macros_writes_the_owned_startup_listener(self) -> None:
        import base64
        import re

        startup = Path(self._temp.name) / "Startup"
        fake = self._fake_pymxs(
            macros_dir=self.directory, startup_dir=startup
        )
        with mock.patch.dict(sys.modules, {"pymxs": fake}):
            launcher.register_macros(target_dir=self.directory)

        path = startup / launcher.STARTUP_FILE_NAME
        data = path.read_text(encoding="utf-8")
        self.assertTrue(data.startswith(launcher.STARTUP_FILE_HEADER))
        self.assertEqual(data, launcher.render_startup_source())
        encoded = re.search(r"b64decode\('([^']+)'\)", data)
        self.assertIsNotNone(encoded)
        body = base64.b64decode(encoded.group(1)).decode("utf-8")
        self.assertIn("ACTION = 'install_hotkey'", body)

    def test_foreign_startup_script_is_never_overwritten(self) -> None:
        startup = Path(self._temp.name) / "Startup"
        startup.mkdir(parents=True)
        foreign = startup / launcher.STARTUP_FILE_NAME
        foreign.write_text("-- user startup\n", encoding="utf-8")
        fake = self._fake_pymxs(
            macros_dir=self.directory, startup_dir=startup
        )

        with mock.patch.dict(sys.modules, {"pymxs": fake}):
            with self.assertRaises(launcher.LauncherError) as raised:
                launcher.register_macros(target_dir=self.directory)

        self.assertEqual(raised.exception.category, "macro_conflict")
        self.assertEqual(
            raised.exception.message, launcher.STARTUP_CONFLICT_MESSAGE
        )
        self.assertEqual(foreign.read_text(encoding="utf-8"), "-- user startup\n")


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
            self.ensured = 0
            self.installed = 0

        def show_palette(self):
            self.shown += 1
            return "palette"

        def ensure_hotkey(self):
            self.ensured += 1
            return "filter"

        def install_tab_hotkey(self):
            self.installed += 1
            return "filter"

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

    def test_invoke_local_install_hotkey_uses_only_the_local_bootstrap(self) -> None:
        fake_bootstrap = self.FakeBootstrap()
        session = launcher._session_state()
        session.bootstrap = fake_bootstrap
        loaded: list = []

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
            result = launcher.invoke_local(
                action="install_hotkey", checkout=self.root
            )

        self.assertEqual(result, "filter")
        self.assertEqual(fake_bootstrap.installed, 1)
        self.assertEqual(fake_bootstrap.ensured, 1)
        self.assertEqual(fake_bootstrap.shown, 0)
        self.assertEqual(fake_bootstrap.switches, [])
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0][1], SHA_ONE)

    def test_invoke_local_rejects_unknown_fixed_enums(self) -> None:
        with self.assertRaises(launcher.LauncherError):
            launcher.invoke_local(action="shell", checkout=self.root)
        with self.assertRaises(launcher.LauncherError):
            launcher.invoke_local(action="edge", context="face", checkout=self.root)
        with self.assertRaises(launcher.LauncherError):
            launcher.invoke_local(action="edge", convert_selection="yes", checkout=self.root)
        with self.assertRaises(launcher.LauncherError):
            launcher.invoke_local(
                action="install_hotkey", context="edge", checkout=self.root
            )


class RenderedMacroBodyTests(unittest.TestCase):
    """Execute the real rendered macro body against a fake mixed-case Git.

    The live bug compared the session's lower-cased origin against a
    mixed-case ``3dsModeler`` constant and used case-sensitive path equality on
    Windows.  These tests drive the actual generated Python (not a copy) with a
    Windows-cased checkout, a mixed-case origin, and count `git` spawns.
    """

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.temp = Path(self._temp.name)
        self.previous = Path.cwd()
        os.chdir(self.temp)
        self.addCleanup(os.chdir, self.previous)

        self.root = Path("c:/dev/3dsmodeler")
        self.actual = self.temp / self.root
        write_minimal_checkout(self.actual)
        self.sha = "a" * 40
        git_dir = self.actual / ".git"
        (git_dir / "refs" / "heads").mkdir(parents=True)
        (git_dir / "HEAD").write_text("ref: refs/heads/dev\n", encoding="utf-8")
        (git_dir / "refs" / "heads" / "dev").write_text(
            self.sha + "\n", encoding="utf-8"
        )

        self.calls = self.temp / "calls.json"
        companion = self.actual / "tools"
        companion.mkdir()
        (companion / "max_pull_and_load.py").write_text(
            "import json\n"
            "from pathlib import Path\n"
            "\n"
            "CALLS = Path(%r)\n"
            "\n"
            "\n"
            "def invoke_local(**kwargs):\n"
            "    record = {}\n"
            "    for key, value in kwargs.items():\n"
            "        if value is None or isinstance(value, (str, bool, int)):\n"
            "            record[key] = value\n"
            "        else:\n"
            "            record[key] = str(value)\n"
            "    CALLS.write_text(json.dumps(record), encoding='utf-8')\n"
            "    return 'invoked'\n" % str(self.calls),
            encoding="utf-8",
        )

        self.saved = {
            name: module
            for name, module in sys.modules.items()
            if name == launcher.PACKAGE_NAME
            or name.startswith(launcher.PACKAGE_NAME + ".")
        }
        for name in self.saved:
            sys.modules.pop(name, None)
        self.addCleanup(self._restore_modules)

    def _restore_modules(self) -> None:
        sys.modules.pop(launcher.SESSION_MODULE_NAME, None)
        sys.modules.pop(COMPANION_NAME, None)
        sys.modules.update(self.saved)

    def _body(self, *, catch_errors: bool = True) -> str:
        return launcher._local_loader_source(
            action="search",
            context=None,
            convert_selection=False,
            checkout=self.root,
            catch_errors=catch_errors,
        )

    def _responses(self, remote: str = launcher.REPO_URL) -> dict:
        return {
            ("rev-parse", "--show-toplevel"): "C:/DEV/3dsModeler",
            ("rev-parse", "--is-bare-repository"): "false",
            ("symbolic-ref", "--quiet", "--short", "HEAD"): "dev",
            ("remote", "get-url", "origin"): remote,
            ("status", "--porcelain=v1", "--untracked-files=all"): "",
            ("rev-parse", "HEAD"): self.sha,
        }

    def _fake_git(self, responses: dict, calls: list):
        def run(command, **_kwargs):
            key = tuple(str(item) for item in command[1:])
            calls.append(key)
            value = responses.get(key, "")
            if isinstance(value, BaseException):
                raise value
            return SimpleNamespace(
                returncode=0,
                stdout=(value + "\n").encode("utf-8"),
                stderr=b"",
            )

        return run

    def _record(self) -> dict:
        return json.loads(self.calls.read_text(encoding="utf-8"))

    def _seed_warm_session(self) -> None:
        session = types.ModuleType(launcher.SESSION_MODULE_NAME)
        session.schema_version = launcher.SESSION_SCHEMA_VERSION
        session.checkout_root = str(self.actual.resolve())
        session.loaded_head_sha = self.sha
        session.restart_required = False
        session.in_flight = False
        session.first_clone_head_sha = self.sha
        session.bootstrap = None
        session.bridge = None
        session.worker = None
        sys.modules[launcher.SESSION_MODULE_NAME] = session

    def test_cold_and_warm_hotkeys_use_the_real_macro_body(self) -> None:
        calls: list = []
        with mock.patch.object(os.path, "normcase", ntpath.normcase), mock.patch(
            "subprocess.run", self._fake_git(self._responses(), calls)
        ):
            exec(compile(self._body(), "<macro-cold>", "exec"), {})

        cold = self._record()
        self.assertEqual(cold["_prevalidated_head"], self.sha)
        self.assertNotIn("_warm_verified_sha", cold)
        # One validation pass (six reads), never the old duplicate second pass.
        self.assertEqual(len(calls), 6)

        self._seed_warm_session()
        with mock.patch.object(os.path, "normcase", ntpath.normcase), mock.patch(
            "subprocess.run", side_effect=AssertionError("warm hotkey spawned git")
        ):
            exec(compile(self._body(), "<macro-warm>", "exec"), {})

        warm = self._record()
        self.assertEqual(warm["_warm_verified_sha"], self.sha)
        self.assertNotIn("_prevalidated_head", warm)

    def test_rendered_macro_reports_clean_failures_without_maxscript_raise(self) -> None:
        responses = self._responses("https://github.com/someone/other.git")
        output = io.StringIO()
        with mock.patch.object(os.path, "normcase", ntpath.normcase), mock.patch(
            "subprocess.run", self._fake_git(responses, [])
        ), contextlib.redirect_stdout(output):
            exec(compile(self._body(), "<macro-catch>", "exec"), {})

        self.assertEqual(
            output.getvalue().strip(),
            "[3dsModeler] The local checkout origin is not the expected "
            "repository. Repair it outside Max.",
        )
        self.assertFalse(self.calls.exists())

        with mock.patch.object(os.path, "normcase", ntpath.normcase), mock.patch(
            "subprocess.run", self._fake_git(responses, [])
        ), self.assertRaises(RuntimeError) as raised:
            exec(compile(self._body(catch_errors=False), "<macro-raise>", "exec"), {})
        self.assertEqual(
            str(raised.exception),
            "The local checkout origin is not the expected repository. "
            "Repair it outside Max.",
        )


if __name__ == "__main__":
    unittest.main()

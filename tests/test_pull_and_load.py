"""Host-free launcher tests (stdlib only; no network, Max, or Qt required)."""

from __future__ import annotations

import ast
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from max2025_menu_prototype.tools import max_pull_and_load as launcher

try:
    from . import checkout_support  # noqa: F401  (documents the shared helper)
except ImportError:  # discovered as a top-level test module
    import checkout_support  # noqa: F401

SHA_ONE = "1" * 40
SHA_TWO = "2" * 40
SHA_THREE = "3" * 40

DEFAULT_BOOTSTRAP = """\
import os
from pathlib import Path

VALUE = "bootstrap"
_import_log = os.environ.get("MAX2025_IMPORT_LOG")
if _import_log:
    with open(_import_log, "a", encoding="utf-8") as handle:
        handle.write("import\\n")


def show_tools():
    _log = os.environ.get("MAX2025_SHOW_LOG")
    if _log:
        Path(_log).write_text("shown", encoding="utf-8")
    return "shown"


def uninstall():
    _log = os.environ.get("MAX2025_UNINSTALL_LOG")
    if _log:
        path = Path(_log)
        count = int(path.read_text(encoding="utf-8") or "0") if path.exists() else 0
        path.write_text(str(count + 1), encoding="utf-8")
    if os.environ.get("MAX2025_UNINSTALL_FAIL") == "1":
        raise RuntimeError("cleanup failed")
"""


def write_checkout(root: Path, bootstrap: str | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    files = {
        "__init__.py": (
            "from . import core\n"
            "from . import max_host\n"
            "from . import qt_ui\n"
        ),
        "core/__init__.py": "from . import profile\n",
        "core/profile.py": "VALUE = 'core'\n",
        "core/default_profile.json": '{"schema_version": 1}\n',
        "max_host/__init__.py": "from . import bootstrap\n",
        "max_host/bootstrap.py": bootstrap if bootstrap is not None else DEFAULT_BOOTSTRAP,
        "qt_ui/__init__.py": "VALUE = 'qt'\n",
    }
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def launcher_script(body: str) -> str:
    python = str(Path(launcher.__file__).resolve())
    return (
        "import os\n"
        "import runpy\n"
        "import sys\n"
        "import types\n"
        "from pathlib import Path\n"
        f"LAUNCHER = Path({python!r})\n"
        "ns = runpy.run_path(str(LAUNCHER), run_name='launcher_under_test')\n"
        "Load = ns['load_checkout_package']\n"
        "Error = ns['LauncherError']\n"
        "SESSION = '_max2025_pull_and_load_session'\n"
        + body
    )


def run_scenario(body: str, *, timeout: float = 60.0) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    with tempfile.TemporaryDirectory(prefix="max2025-launcher-") as workdir:
        return subprocess.run(
            [sys.executable, "-B", "-c", launcher_script(body)],
            cwd=workdir,
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )


class AliasScenarioTests(unittest.TestCase):
    def assertOk(self, completed: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(
            completed.returncode,
            0,
            "stdout:\n%s\nstderr:\n%s" % (completed.stdout, completed.stderr),
        )
        self.assertIn("ok", completed.stdout)

    def _roots(self, temp: Path) -> tuple[Path, Path, Path]:
        a = write_checkout(temp / "par ent-ü" / "3dsModeler")
        b = write_checkout(temp / "other space-é" / "3dsModeler")
        outside = temp / "outside"
        outside.mkdir()
        return a, b, outside

    def test_fresh_alias_identity_resource_and_no_path_leakage(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root, _other, _outside = self._roots(Path(temp))
            import_log = Path(temp) / "imports.log"
            script = f"""
ROOT = Path({str(root)!r})
SHA = {SHA_ONE!r}
pkg = Load(ROOT, SHA)
assert Path(pkg.__file__).resolve() == (ROOT / "__init__.py").resolve()
assert Path(pkg.__path__[0]).resolve() == ROOT.resolve()
import max2025_menu_prototype.core.profile as profile
import max2025_menu_prototype.max_host.bootstrap as bootstrap
import max2025_menu_prototype.qt_ui as qt
for module in (profile, bootstrap, qt):
    assert Path(module.__file__).resolve().is_relative_to(ROOT.resolve())
assert (ROOT / "core" / "default_profile.json").is_file()
assert str(ROOT.parent) not in [entry for entry in sys.path if entry]
assert "PYTHONPATH" not in os.environ
package = sys.modules["max2025_menu_prototype"]
assert Load(ROOT, SHA) is package
assert sys.modules["max2025_menu_prototype"] is package
assert Path({str(import_log)!r}).read_text(encoding="utf-8").count("import") == 1
print("ok")
"""
            environment_backup = os.environ.copy()
            os.environ["MAX2025_IMPORT_LOG"] = str(import_log)
            try:
                completed = run_scenario(script)
            finally:
                os.environ.clear()
                os.environ.update(environment_backup)
            self.assertOk(completed)

    def test_stale_root_refuses_and_retires_old_bootstrap_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root_a, root_b, _outside = self._roots(Path(temp))
            uninstall = Path(temp) / "uninstall.log"
            imports = Path(temp) / "imports.log"
            script = f"""
ROOT_A = Path({str(root_a)!r})
ROOT_B = Path({str(root_b)!r})
Load(ROOT_A, {SHA_ONE!r})
first = sys.modules["max2025_menu_prototype"]
try:
    Load(ROOT_B, {SHA_ONE!r})
    raise SystemExit("expected stale root refusal")
except Error as exc:
    assert exc.category == "stale_import", exc.category
session = sys.modules[SESSION]
assert session.restart_required is True
assert sys.modules["max2025_menu_prototype"] is first
try:
    Load(ROOT_B, {SHA_ONE!r})
    raise SystemExit("expected restart refusal")
except Error as exc:
    assert exc.category == "restart_required", exc.category
assert Path({str(uninstall)!r}).read_text(encoding="utf-8") == "1"
assert Path({str(imports)!r}).read_text(encoding="utf-8").count("import") == 1
print("ok")
"""
            environment_backup = os.environ.copy()
            os.environ.update(
                MAX2025_UNINSTALL_LOG=str(uninstall),
                MAX2025_IMPORT_LOG=str(imports),
            )
            try:
                completed = run_scenario(script)
            finally:
                os.environ.clear()
                os.environ.update(environment_backup)
            self.assertOk(completed)

    def test_changed_sha_keeps_modules_uninstalls_once_and_refuses_later(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root, _other, _outside = self._roots(Path(temp))
            uninstall = Path(temp) / "uninstall.log"
            imports = Path(temp) / "imports.log"
            script = f"""
ROOT = Path({str(root)!r})
Load(ROOT, {SHA_ONE!r})
first = sys.modules["max2025_menu_prototype"]
try:
    Load(ROOT, {SHA_TWO!r})
    raise SystemExit("expected changed-commit refusal")
except Error as exc:
    assert exc.category == "restart_required", exc.category
session = sys.modules[SESSION]
assert session.restart_required is True
assert session.loaded_head_sha == {SHA_ONE!r}
assert sys.modules["max2025_menu_prototype"] is first
assert Path({str(uninstall)!r}).read_text(encoding="utf-8") == "1"
try:
    Load(ROOT, {SHA_TWO!r})
    raise SystemExit("expected persistent refusal")
except Error as exc:
    assert exc.category == "restart_required", exc.category
assert Path({str(uninstall)!r}).read_text(encoding="utf-8") == "1"
assert Path({str(imports)!r}).read_text(encoding="utf-8").count("import") == 1
print("ok")
"""
            environment_backup = os.environ.copy()
            os.environ.update(
                MAX2025_UNINSTALL_LOG=str(uninstall),
                MAX2025_IMPORT_LOG=str(imports),
            )
            try:
                completed = run_scenario(script)
            finally:
                os.environ.clear()
                os.environ.update(environment_backup)
            self.assertOk(completed)

    def test_stale_submodule_without_root_fails_before_import(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root, _other, outside = self._roots(Path(temp))
            script = f"""
ROOT = Path({str(root)!r})
OUTSIDE = Path({str(outside)!r})
stale = types.ModuleType("max2025_menu_prototype.core")
stale.__file__ = str(OUTSIDE / "core.py")
sys.modules["max2025_menu_prototype.core"] = stale
try:
    Load(ROOT, {SHA_ONE!r})
    raise SystemExit("expected stale submodule refusal")
except Error as exc:
    assert exc.category == "stale_import", exc.category
assert sys.modules["max2025_menu_prototype.core"] is stale
assert "max2025_menu_prototype" not in sys.modules
assert sys.modules[SESSION].restart_required is True
print("ok")
"""
            self.assertOk(run_scenario(script))

    def test_wrong_spec_origin_and_package_path_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root, _other, outside = self._roots(Path(temp))
            script = f"""
ROOT = Path({str(root)!r})
OUTSIDE = Path({str(outside)!r})

def seed(origin, path_entry):
    stale = types.ModuleType("max2025_menu_prototype")
    stale.__file__ = str(ROOT / "__init__.py")
    stale.__path__ = [str(path_entry)]
    stale.__spec__ = types.SimpleNamespace(origin=str(origin))
    sys.modules["max2025_menu_prototype"] = stale
    sys.modules.pop(SESSION, None)

seed(OUTSIDE / "__init__.py", ROOT)
try:
    Load(ROOT, {SHA_ONE!r})
    raise SystemExit("expected wrong-origin refusal")
except Error as exc:
    assert exc.category == "stale_import", exc.category

seed(ROOT / "__init__.py", OUTSIDE)
try:
    Load(ROOT, {SHA_ONE!r})
    raise SystemExit("expected wrong-path refusal")
except Error as exc:
    assert exc.category == "stale_import", exc.category
print("ok")
"""
            self.assertOk(run_scenario(script))

    def test_unstamped_same_path_import_is_not_adopted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root, _other, _outside = self._roots(Path(temp))
            script = f"""
ROOT = Path({str(root)!r})
stale = types.ModuleType("max2025_menu_prototype")
stale.__file__ = str(ROOT / "__init__.py")
stale.__path__ = [str(ROOT)]
stale.__spec__ = types.SimpleNamespace(origin=str(ROOT / "__init__.py"))
sys.modules["max2025_menu_prototype"] = stale
try:
    Load(ROOT, {SHA_ONE!r})
    raise SystemExit("expected unstamped refusal")
except Error as exc:
    assert exc.category == "stale_import", exc.category
assert sys.modules["max2025_menu_prototype"] is stale
assert sys.modules[SESSION].restart_required is True
print("ok")
"""
            self.assertOk(run_scenario(script))

    def test_malformed_and_mismatched_session_markers_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root, _other, _outside = self._roots(Path(temp))
            script = f"""
ROOT = Path({str(root)!r})

def session(schema=1, root_value=None):
    module = types.ModuleType(SESSION)
    module.schema_version = schema
    module.checkout_root = root_value
    module.loaded_head_sha = None
    module.restart_required = False
    module.in_flight = False
    module.first_clone_head_sha = None
    module.bootstrap = None
    module.bridge = None
    module.worker = None
    sys.modules[SESSION] = module
    return module

session(schema=2)
try:
    Load(ROOT, {SHA_ONE!r})
    raise SystemExit("expected unknown-schema refusal")
except Error as exc:
    assert exc.category == "stale_import", exc.category

del sys.modules[SESSION]
session(root_value="/somewhere/else")
try:
    Load(ROOT, {SHA_ONE!r})
    raise SystemExit("expected mismatched-root refusal")
except Error as exc:
    assert exc.category == "stale_import", exc.category
assert sys.modules[SESSION].restart_required is True

del sys.modules[SESSION]
broken = types.ModuleType(SESSION)
broken.schema_version = 1
sys.modules[SESSION] = broken
try:
    Load(ROOT, {SHA_ONE!r})
    raise SystemExit("expected incomplete-marker refusal")
except Error as exc:
    assert exc.category == "stale_import", exc.category
print("ok")
"""
            self.assertOk(run_scenario(script))

    def test_cleanup_failure_still_requires_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root, _other, _outside = self._roots(Path(temp))
            uninstall = Path(temp) / "uninstall.log"
            script = f"""
ROOT = Path({str(root)!r})
Load(ROOT, {SHA_ONE!r})
try:
    Load(ROOT, {SHA_TWO!r})
    raise SystemExit("expected changed-commit refusal")
except Error as exc:
    assert exc.category == "restart_required", exc.category
assert sys.modules[SESSION].restart_required is True
try:
    Load(ROOT, {SHA_TWO!r})
    raise SystemExit("expected persistent refusal")
except Error as exc:
    assert exc.category == "restart_required", exc.category
assert Path({str(uninstall)!r}).read_text(encoding="utf-8") == "1"
print("ok")
"""
            environment = dict(os.environ)
            environment["MAX2025_UNINSTALL_LOG"] = str(uninstall)
            environment["MAX2025_UNINSTALL_FAIL"] = "1"
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            environment.pop("PYTHONPATH", None)
            with tempfile.TemporaryDirectory(prefix="max2025-launcher-") as workdir:
                completed = subprocess.run(
                    [sys.executable, "-B", "-c", launcher_script(script)],
                    cwd=workdir,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                )
            self.assertOk(completed)

    def test_partial_initializer_failure_leaves_restart_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = write_checkout(
                Path(temp) / "par ent-ü" / "3dsModeler",
                bootstrap="VALUE = 'bootstrap'\n",
            )
            (root / "__init__.py").write_text(
                "from . import core\nraise RuntimeError('boom')\n", encoding="utf-8"
            )
            imports = Path(temp) / "imports.log"
            script = f"""
ROOT = Path({str(root)!r})
try:
    Load(ROOT, {SHA_ONE!r})
    raise SystemExit("expected partial-import refusal")
except Error as exc:
    assert exc.category == "partial_import", exc.category
session = sys.modules[SESSION]
assert session.restart_required is True
assert "max2025_menu_prototype" in sys.modules
assert "max2025_menu_prototype.core" in sys.modules
assert not Path({str(imports)!r}).exists()
try:
    Load(ROOT, {SHA_ONE!r})
    raise SystemExit("expected persistent refusal")
except Error as exc:
    assert exc.category == "restart_required", exc.category
print("ok")
"""
            environment = dict(os.environ)
            environment["MAX2025_IMPORT_LOG"] = str(imports)
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            environment.pop("PYTHONPATH", None)
            with tempfile.TemporaryDirectory(prefix="max2025-launcher-") as workdir:
                completed = subprocess.run(
                    [sys.executable, "-B", "-c", launcher_script(script)],
                    cwd=workdir,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                )
            self.assertOk(completed)


# ---------------------------------------------------------------------------
# Git worker tests
# ---------------------------------------------------------------------------


class GitStub:
    def __init__(self, responses=None):
        self.responses = dict(responses or {})
        self.calls: list[dict] = []

    def __call__(self, args, *, cwd, timeout):
        key = tuple(str(item) for item in args)
        self.calls.append({"args": key, "cwd": Path(cwd), "timeout": timeout})
        handler = self.responses.get(key, "")
        if callable(handler):
            handler = handler(self)
        if isinstance(handler, BaseException):
            raise handler
        if isinstance(handler, launcher.GitResult):
            return handler
        if isinstance(handler, tuple):
            return launcher.GitResult(*handler)
        return launcher.GitResult(0, str(handler), "")

    def count(self, *args) -> int:
        return sum(1 for call in self.calls if call["args"] == tuple(args))


def checkout_responses(root: Path, head: str, remote: str | None = None) -> dict:
    return {
        ("rev-parse", "--show-toplevel"): str(root),
        ("rev-parse", "--is-bare-repository"): "false",
        ("symbolic-ref", "--quiet", "--short", "HEAD"): "dev",
        ("remote", "get-url", "origin"): launcher.REPO_URL,
        ("rev-parse", "HEAD"): head,
        ("rev-parse", "--absolute-git-dir"): str(root / ".git"),
        ("status", "--porcelain=v1", "--untracked-files=all"): "",
        ("fetch", "--no-tags", "origin", "dev"): "",
        ("rev-parse", "--verify", "refs/remotes/origin/dev"): remote or head,
        ("merge-base", "--is-ancestor", head, remote or head): (0, "", ""),
        ("pull", "--ff-only", "origin", "dev"): "",
    }


class CheckoutValidationTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.temp = Path(self._temp.name)
        self.root = write_checkout(self.temp / "max2025_menu_prototype")
        self.addCleanup(self._temp.cleanup)

    def _validate(self, responses):
        stub = GitStub(responses)
        with mock.patch.object(launcher, "_run_git", stub):
            return launcher._validate_checkout(self.root), stub

    def test_clean_checkout_and_local_timeouts(self):
        info, stub = self._validate(checkout_responses(self.root, SHA_ONE))
        self.assertEqual(info["head"], SHA_ONE)
        self.assertEqual(info["branch"], "dev")
        self.assertTrue(all(call["timeout"] == 15.0 for call in stub.calls))
        self.assertEqual(
            stub.calls[0]["args"], ("rev-parse", "--show-toplevel")
        )

    def test_dirty_tracked_index_and_untracked_states_fail(self):
        for status in (" M tracked.py\n", "M  staged.py\n", "?? new.py\n"):
            responses = checkout_responses(self.root, SHA_ONE)
            responses[("status", "--porcelain=v1", "--untracked-files=all")] = status
            with self.assertRaises(launcher.LauncherError) as raised:
                self._validate(responses)
            self.assertEqual(raised.exception.category, "dirty_tree")
            self.assertNotIn(status, raised.exception.message)

    def test_wrong_branch_and_detached_head_fail(self):
        responses = checkout_responses(self.root, SHA_ONE)
        responses[("symbolic-ref", "--quiet", "--short", "HEAD")] = "main"
        with self.assertRaises(launcher.LauncherError) as raised:
            self._validate(responses)
        self.assertEqual(raised.exception.category, "wrong_branch")

        responses = checkout_responses(self.root, SHA_ONE)
        responses[("symbolic-ref", "--quiet", "--short", "HEAD")] = (1, "", "detached")
        with self.assertRaises(launcher.LauncherError) as raised:
            self._validate(responses)
        self.assertEqual(raised.exception.category, "detached_head")

    def test_missing_and_wrong_origin_fail(self):
        responses = checkout_responses(self.root, SHA_ONE)
        responses[("remote", "get-url", "origin")] = (2, "", "no such remote")
        with self.assertRaises(launcher.LauncherError) as raised:
            self._validate(responses)
        self.assertEqual(raised.exception.category, "wrong_origin")

        responses = checkout_responses(self.root, SHA_ONE)
        responses[("remote", "get-url", "origin")] = "git@example.com:other/repo.git"
        with self.assertRaises(launcher.LauncherError) as raised:
            self._validate(responses)
        self.assertEqual(raised.exception.category, "wrong_origin")

    def test_wrong_top_level_and_bare_layout_fail(self):
        responses = checkout_responses(self.root, SHA_ONE)
        responses[("rev-parse", "--show-toplevel")] = str(self.temp)
        with self.assertRaises(launcher.LauncherError) as raised:
            self._validate(responses)
        self.assertEqual(raised.exception.category, "wrong_top_level")

        responses = checkout_responses(self.root, SHA_ONE)
        responses[("rev-parse", "--is-bare-repository")] = "true"
        with self.assertRaises(launcher.LauncherError) as raised:
            self._validate(responses)
        self.assertEqual(raised.exception.category, "checkout_layout")

    def test_in_progress_git_operation_fails(self):
        git_dir = self.root / ".git"
        git_dir.mkdir()
        (git_dir / "MERGE_HEAD").write_text("x\n", encoding="utf-8")
        with self.assertRaises(launcher.LauncherError) as raised:
            self._validate(checkout_responses(self.root, SHA_ONE))
        self.assertEqual(raised.exception.category, "git_operation")


class GitExecutionTests(unittest.TestCase):
    def test_missing_git_is_a_finite_error(self):
        with mock.patch.object(launcher.shutil, "which", return_value=None):
            with self.assertRaises(launcher.LauncherError) as raised:
                launcher._run_git(["status"], cwd=Path("."), timeout=15)
        self.assertEqual(raised.exception.category, "missing_git")

    def test_command_array_and_process_safety_options(self):
        observed: dict = {}

        class FakeProcess:
            returncode = 0

            def __init__(self, command, **kwargs):
                observed["command"] = command
                observed["kwargs"] = kwargs

            def communicate(self, timeout=None):
                return b"out", b"err"

        with mock.patch.object(launcher.shutil, "which", return_value="/usr/bin/git"), \
                mock.patch.object(launcher.subprocess, "Popen", FakeProcess):
            result = launcher._run_git(
                ["status", "--porcelain"], cwd=Path("/tmp"), timeout=15
            )
        self.assertEqual(observed["command"], ["/usr/bin/git", "status", "--porcelain"])
        kwargs = observed["kwargs"]
        self.assertIs(kwargs["shell"], False)
        self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["creationflags"], 0)
        self.assertEqual(kwargs["env"]["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(kwargs["env"]["GCM_INTERACTIVE"], "Never")
        self.assertNotIn("GIT_ASKPASS", kwargs["env"])
        self.assertNotIn("SSH_ASKPASS", kwargs["env"])
        self.assertEqual(result.stdout, "out")

    def test_windows_creation_flags_are_simulated(self):
        observed: dict = {}

        class FakeProcess:
            returncode = 0

            def __init__(self, command, **kwargs):
                observed.update(kwargs)

            def communicate(self, timeout=None):
                return b"", b""

        with mock.patch.object(launcher.shutil, "which", return_value="git"), \
                mock.patch.object(launcher, "_creation_flags", return_value=0x08000000), \
                mock.patch.object(launcher.subprocess, "Popen", FakeProcess):
            launcher._run_git(["status"], cwd=Path("/tmp"), timeout=15)
        self.assertEqual(observed["creationflags"], 0x08000000)

    def test_timeout_kills_and_reaps_then_stops(self):
        class FakeProcess:
            returncode = -9

            def __init__(self):
                self.killed = False
                self.calls = 0

            def communicate(self, timeout=None):
                self.calls += 1
                if self.calls == 1:
                    raise subprocess.TimeoutExpired("git", timeout)
                return b"", b""

            def kill(self):
                self.killed = True

        process = FakeProcess()
        with mock.patch.object(launcher.shutil, "which", return_value="git"), \
                mock.patch.object(launcher.subprocess, "Popen", return_value=process):
            with self.assertRaises(launcher.LauncherError) as raised:
                launcher._run_git(["fetch"], cwd=Path("/tmp"), timeout=1)
        self.assertEqual(raised.exception.category, "timeout")
        self.assertTrue(process.killed)
        self.assertEqual(process.calls, 2)

    def test_failure_classification_never_exposes_raw_output(self):
        auth = launcher._classify_git_failure(
            launcher.GitResult(128, "", "fatal: repository not found for token")
        )
        self.assertEqual(auth.category, "auth")
        self.assertNotIn("token", auth.message)
        network = launcher._classify_git_failure(
            launcher.GitResult(128, "", "fatal: unable to access: Could not resolve host")
        )
        self.assertEqual(network.category, "network")
        other = launcher._classify_git_failure(
            launcher.GitResult(1, "", "fatal: some private detail")
        )
        self.assertEqual(other.category, "git_failed")
        self.assertNotIn("private detail", other.message)


class CloneTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.temp = Path(self._temp.name)
        self.addCleanup(self._temp.cleanup)

    def test_clone_command_is_exact_and_network_timeout_is_used(self):
        target = self.temp / "dev" / "3dsModeler"

        def clone(stub):
            write_checkout(target)
            return (0, "", "")

        responses = checkout_responses(target, SHA_ONE)
        responses[("clone", "--branch", "dev", launcher.REPO_URL, str(target))] = clone
        stub = GitStub(responses)
        with mock.patch.object(launcher, "_run_git", stub):
            head = launcher._clone_checkout(target)
        self.assertEqual(head, SHA_ONE)
        first = stub.calls[0]
        self.assertEqual(
            first["args"],
            ("clone", "--branch", "dev", launcher.REPO_URL, str(target)),
        )
        self.assertEqual(first["timeout"], 120.0)

    def test_existing_directory_is_a_collision(self):
        target = self.temp / "existing"
        target.mkdir()
        with self.assertRaises(launcher.LauncherError) as raised:
            launcher._clone_checkout(target)
        self.assertEqual(raised.exception.category, "permission")
        self.assertTrue(target.is_dir())

    def test_interrupted_clone_leaves_partial_directory(self):
        target = self.temp / "partial"

        def fail(stub):
            target.mkdir()
            return (1, "", "fatal: early EOF")

        stub = GitStub({("clone", "--branch", "dev", launcher.REPO_URL, str(target)): fail})
        with mock.patch.object(launcher, "_run_git", stub):
            with self.assertRaises(launcher.LauncherError) as raised:
                launcher._clone_checkout(target)
        self.assertEqual(raised.exception.category, "partial_clone")
        self.assertTrue(target.is_dir())


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.temp = Path(self._temp.name)
        self.root = write_checkout(self.temp / "max2025_menu_prototype")
        self.addCleanup(self._temp.cleanup)

    def _run(self, responses, **kwargs):
        stub = GitStub(responses)
        with mock.patch.object(launcher, "_run_git", stub):
            result = launcher._worker_update(
                self.root,
                kwargs.get("handoff"),
                kwargs.get("loaded"),
                kwargs.get("loaded_root", str(self.root) if kwargs.get("loaded") else None),
                kwargs.get("modules", False),
            )
        return result, stub

    def test_import_only_when_remote_equals_head(self):
        result, stub = self._run(checkout_responses(self.root, SHA_ONE))
        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "import-only")
        self.assertEqual(result["after_sha"], SHA_ONE)
        self.assertEqual(stub.count("fetch", "--no-tags", "origin", "dev"), 1)
        self.assertEqual(stub.count("pull", "--ff-only", "origin", "dev"), 0)

    def test_fast_forward_pull_imports_new_head(self):
        state = {"head": SHA_ONE, "fetched": False}

        def head(stub):
            return state["head"]

        def pull(stub):
            state["head"] = SHA_TWO
            return (0, "", "")

        responses = checkout_responses(self.root, SHA_ONE, remote=SHA_TWO)
        responses[("rev-parse", "HEAD")] = head
        responses[("pull", "--ff-only", "origin", "dev")] = pull
        result, _stub = self._run(responses)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["action"], "pull-import")
        self.assertEqual(result["before_sha"], SHA_ONE)
        self.assertEqual(result["after_sha"], SHA_TWO)

    def test_local_ahead_or_diverged_refuses(self):
        responses = checkout_responses(self.root, SHA_ONE, remote=SHA_TWO)
        responses[("merge-base", "--is-ancestor", SHA_ONE, SHA_TWO)] = (1, "", "")
        result, stub = self._run(responses)
        self.assertFalse(result["ok"])
        self.assertEqual(result["category"], "diverged")
        self.assertEqual(stub.count("pull", "--ff-only", "origin", "dev"), 0)

    def test_loaded_same_sha_is_idempotent_without_pull(self):
        result, stub = self._run(
            checkout_responses(self.root, SHA_ONE), loaded=SHA_ONE
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "noop")
        self.assertEqual(stub.count("pull", "--ff-only", "origin", "dev"), 0)

    def test_remote_moved_while_loaded_requires_restart(self):
        result, _stub = self._run(
            checkout_responses(self.root, SHA_ONE, remote=SHA_TWO), loaded=SHA_ONE
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["category"], "restart_required")
        self.assertEqual(result["message"], launcher.RESTART_MESSAGE)

    def test_changed_on_disk_head_while_loaded_requires_restart(self):
        result, _stub = self._run(
            checkout_responses(self.root, SHA_TWO), loaded=SHA_ONE
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["category"], "restart_required")

    def test_loaded_different_root_refuses(self):
        result, _stub = self._run(
            checkout_responses(self.root, SHA_ONE),
            loaded=SHA_ONE,
            loaded_root=str(self.temp / "elsewhere"),
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["category"], "stale_import")

    def test_changed_head_during_fetch_refuses(self):
        state = {"head": SHA_ONE, "fetches": 0}

        def head(stub):
            return state["head"]

        def fetch(stub):
            state["head"] = SHA_THREE
            return (0, "", "")

        responses = checkout_responses(self.root, SHA_ONE)
        responses[("rev-parse", "HEAD")] = head
        responses[("fetch", "--no-tags", "origin", "dev")] = fetch
        result, _stub = self._run(responses)
        self.assertFalse(result["ok"])
        self.assertEqual(result["category"], "checkout_changed")

    def test_modules_loaded_without_stamp_refuses(self):
        result, _stub = self._run(
            checkout_responses(self.root, SHA_ONE), modules=True
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["category"], "stale_import")

    def test_handoff_accepts_matching_head_and_rejects_drift(self):
        result, _stub = self._run(
            checkout_responses(self.root, SHA_ONE), handoff=SHA_ONE
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "handoff")

        result, _stub = self._run(
            checkout_responses(self.root, SHA_TWO), handoff=SHA_ONE
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["category"], "checkout_changed")

    def test_missing_checkout_clones_and_reports_head(self):
        target = self.temp / "dev" / "3dsModeler"

        def clone(stub):
            write_checkout(target)
            return (0, "", "")

        responses = checkout_responses(target, SHA_ONE)
        responses[("clone", "--branch", "dev", launcher.REPO_URL, str(target))] = clone
        stub = GitStub(responses)
        with mock.patch.object(launcher, "_run_git", stub):
            result = launcher._worker_update(target, None, None, None, False)
        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "clone")
        self.assertEqual(result["after_sha"], SHA_ONE)


# ---------------------------------------------------------------------------
# start() / bridge / session tests
# ---------------------------------------------------------------------------


class FakeSignal:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)

    def emit(self, value):
        for slot in list(self.slots):
            slot(value)


class FakeBridge:
    def __init__(self):
        self.completed = FakeSignal()
        self.pending = []

    def connect(self, slot):
        self.completed.connect(slot)

    def emit(self, result):
        self.pending.append(result)

    def deliver(self):
        result = self.pending.pop(0)
        self.completed.emit(result)
        return result


class FakeWorker:
    def __init__(self, target, bridge):
        self.target = target
        self.bridge = bridge
        self.started = False

    def start(self):
        self.started = True

    def run_target(self):
        result = self.target()
        self.bridge.emit(result)
        return result


class FakeBootstrap:
    def __init__(self):
        self.shown = 0
        self.uninstalled = 0

    def show_tools(self):
        self.shown += 1

    def uninstall(self):
        self.uninstalled += 1


class StartTests(unittest.TestCase):
    def setUp(self):
        self._prior = sys.modules.get(launcher.SESSION_MODULE_NAME)
        sys.modules.pop(launcher.SESSION_MODULE_NAME, None)
        self.addCleanup(self._restore_session)
        self._temp = tempfile.TemporaryDirectory()
        self.checkout = Path(self._temp.name) / "3dsModeler"
        self.checkout.mkdir()
        self.addCleanup(self._temp.cleanup)
        self.bridge = FakeBridge()
        self.worker = None

    def _restore_session(self):
        sys.modules.pop(launcher.SESSION_MODULE_NAME, None)
        if self._prior is not None:
            sys.modules[launcher.SESSION_MODULE_NAME] = self._prior

    def _worker_factory(self, target, bridge):
        self.worker = FakeWorker(target, bridge)
        return self.worker

    def _patch(self, update_result):
        return (
            mock.patch.object(launcher, "_bridge_factory", lambda: self.bridge),
            mock.patch.object(launcher, "_worker_factory", self._worker_factory),
            mock.patch.object(launcher, "_worker_update", return_value=update_result),
        )

    def test_start_requires_main_thread_before_side_effects(self):
        failures = []

        def run():
            try:
                launcher.start(checkout=self.checkout)
            except BaseException as exc:  # noqa: BLE001 - asserted below
                failures.append(exc)

        with mock.patch.object(launcher, "_worker_factory") as factory:
            thread = threading.Thread(target=run)
            thread.start()
            thread.join(timeout=10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], launcher.LauncherError)
        self.assertEqual(failures[0].category, "main_thread")
        factory.assert_not_called()

    def test_start_schedules_nonblocking_with_strong_references(self):
        result = launcher._result(ok=True, action="clone", after_sha=SHA_ONE)
        bridge_patch, worker_patch, update_patch = self._patch(result)
        with bridge_patch, worker_patch, update_patch:
            returned = launcher.start(checkout=self.checkout)
        self.assertIsNone(returned)
        self.assertTrue(self.worker.started)
        session = sys.modules[launcher.SESSION_MODULE_NAME]
        self.assertIs(session.bridge, self.bridge)
        self.assertIs(session.worker, self.worker)
        self.assertTrue(session.in_flight)

    def test_duplicate_invocation_returns_without_scheduling(self):
        session = launcher._session_state()
        session.in_flight = True
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream), \
                mock.patch.object(launcher, "_worker_factory") as factory:
            self.assertIsNone(launcher.start(checkout=self.checkout))
        factory.assert_not_called()
        self.assertIn("already in progress", stream.getvalue())

    def test_completion_waits_for_queued_delivery_then_shows_panel(self):
        result = launcher._result(ok=True, action="clone", after_sha=SHA_ONE)
        bootstrap = FakeBootstrap()
        loaded = []

        def fake_load(checkout, sha):
            loaded.append((checkout, sha))
            session = launcher._session_state()
            session.checkout_root = str(checkout)
            session.loaded_head_sha = sha
            session.bootstrap = bootstrap
            return SimpleNamespace(__file__=str(checkout / "__init__.py"))

        bridge_patch, worker_patch, update_patch = self._patch(result)
        with bridge_patch, worker_patch, update_patch, \
                mock.patch.object(launcher, "load_checkout_package", side_effect=fake_load) as load:
            launcher.start(checkout=self.checkout)
            self.worker.run_target()
            self.assertEqual(load.call_count, 0)
            self.assertEqual(bootstrap.shown, 0)
            self.bridge.deliver()
        self.assertEqual(load.call_count, 1)
        self.assertEqual(bootstrap.shown, 1)
        session = sys.modules[launcher.SESSION_MODULE_NAME]
        self.assertFalse(session.in_flight)
        self.assertIsNone(session.worker)
        self.assertIsNone(session.bridge)

    def test_completion_requires_main_thread(self):
        errors = []

        def run():
            try:
                launcher._on_git_finished(
                    launcher._result(ok=True, action="noop", after_sha=SHA_ONE),
                    self.checkout,
                )
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        thread = threading.Thread(target=run)
        thread.start()
        thread.join(timeout=10)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].category, "main_thread")

    def test_restart_required_uninstalls_once_and_refuses_later(self):
        bootstrap = FakeBootstrap()
        session = launcher._session_state()
        session.checkout_root = str(self.checkout)
        session.loaded_head_sha = SHA_ONE
        session.bootstrap = bootstrap
        result = launcher._failure("restart_required", launcher.RESTART_MESSAGE)
        bridge_patch, worker_patch, update_patch = self._patch(result)
        with bridge_patch, worker_patch, update_patch:
            launcher.start(checkout=self.checkout)
            self.worker.run_target()
            with self.assertRaises(launcher.LauncherError) as raised:
                self.bridge.deliver()
        self.assertEqual(raised.exception.category, "restart_required")
        self.assertTrue(session.restart_required)
        self.assertEqual(bootstrap.uninstalled, 1)
        with mock.patch.object(launcher, "_worker_factory") as factory:
            with self.assertRaises(launcher.LauncherError):
                launcher.start(checkout=self.checkout)
        factory.assert_not_called()
        self.assertEqual(bootstrap.uninstalled, 1)

    def test_failed_update_never_imports_or_shows(self):
        result = launcher._failure("dirty_tree", launcher.DIRTY_MESSAGE)
        bridge_patch, worker_patch, update_patch = self._patch(result)
        with bridge_patch, worker_patch, update_patch, \
                mock.patch.object(launcher, "load_checkout_package") as load:
            launcher.start(checkout=self.checkout)
            self.worker.run_target()
            with self.assertRaises(launcher.LauncherError):
                self.bridge.deliver()
        load.assert_not_called()
        self.assertFalse(sys.modules[launcher.SESSION_MODULE_NAME].restart_required)


# ---------------------------------------------------------------------------
# Standalone .ms embedded bootstrap tests
# ---------------------------------------------------------------------------


class EmbeddedLauncherTests(unittest.TestCase):
    def setUp(self):
        self._prior = sys.modules.get(launcher.SESSION_MODULE_NAME)
        sys.modules.pop(launcher.SESSION_MODULE_NAME, None)
        self.addCleanup(self._restore_session)
        self._temp = tempfile.TemporaryDirectory()
        self.temp = Path(self._temp.name)
        self.checkout = self.temp / "3dsModeler"
        self.checkout.mkdir()
        self.addCleanup(self._temp.cleanup)

    def _restore_session(self):
        sys.modules.pop(launcher.SESSION_MODULE_NAME, None)
        if self._prior is not None:
            sys.modules[launcher.SESSION_MODULE_NAME] = self._prior

    def _embedded_namespace(self):
        ms_path = Path(launcher.__file__).parent / "max_pull_and_load.ms"
        embedded = launcher.extract_embedded_python(
            ms_path.read_text(encoding="utf-8")
        )
        ast.parse(embedded)
        namespace: dict = {"__name__": "embedded_test"}
        exec(compile(embedded, "<3dsModeler-embedded>", "exec"), namespace)
        return namespace

    def test_committed_ms_matches_generator_and_constants(self):
        ms_path = Path(launcher.__file__).parent / "max_pull_and_load.ms"
        text = ms_path.read_text(encoding="utf-8")
        self.assertEqual(text, launcher.render_ms_source())
        self.assertIn('"C:\\\\dev\\\\3dsModeler"', text)
        namespace = self._embedded_namespace()
        self.assertEqual(namespace["PACKAGE_NAME"], launcher.PACKAGE_NAME)
        self.assertEqual(namespace["REPO_URL"], launcher.REPO_URL)
        self.assertEqual(namespace["BRANCH"], launcher.BRANCH)
        self.assertEqual(str(namespace["CHECKOUT"]), str(launcher.DEFAULT_CHECKOUT))
        self.assertEqual(namespace["_REQUIRED"], launcher.REQUIRED_ROOT_ENTRIES)
        self.assertEqual(namespace["LOCAL_GIT_TIMEOUT"], launcher.LOCAL_GIT_TIMEOUT)
        self.assertEqual(namespace["NETWORK_GIT_TIMEOUT"], launcher.NETWORK_GIT_TIMEOUT)
        self.assertEqual(namespace["RESTART_MESSAGE"], launcher.RESTART_MESSAGE)
        session = namespace["_session"]()
        for field in launcher.SESSION_FIELDS:
            self.assertTrue(hasattr(session, field), field)
        self.assertEqual(session.schema_version, launcher.SESSION_SCHEMA_VERSION)

    def test_renderer_escapes_spaces_and_non_ascii_checkout_paths(self):
        checkout = Path(r"C:\dev space\3dsModéler")
        with mock.patch.object(launcher, "DEFAULT_CHECKOUT", checkout):
            rendered = launcher.render_ms_source()
            embedded = launcher.extract_embedded_python(rendered)
        self.assertIn('"C:\\\\dev space\\\\3dsModéler"', rendered)
        ast.parse(embedded)
        self.assertTrue(embedded.strip().endswith("_schedule()"))

    def test_embedded_clone_handoff_uses_queued_fake_bridge(self):
        namespace = self._embedded_namespace()
        namespace["CHECKOUT"] = self.checkout
        calls = []
        namespace["_clone_result"] = lambda: {
            "ok": True,
            "head": SHA_ONE,
            "message": "cloned",
        }
        namespace["_invoke_companion"] = lambda: calls.append("companion")
        namespace["_alias_loaded"] = lambda: False
        bridge = FakeBridge()
        worker_holder = {}

        def worker_factory(target, bridge_object):
            worker = FakeWorker(target, bridge_object)
            worker_holder["worker"] = worker
            return worker

        session = namespace["_session"]()
        namespace["_schedule"](
            bridge_factory=lambda handler: (bridge.completed.connect(handler), bridge)[1],
            worker_factory=worker_factory,
        )
        self.assertTrue(session.in_flight)
        self.assertEqual(calls, [])
        worker = worker_holder["worker"]
        self.assertTrue(worker.started)
        worker.run_target()
        self.assertEqual(calls, [])
        bridge.deliver()
        self.assertEqual(calls, ["companion"])
        self.assertEqual(session.first_clone_head_sha, SHA_ONE)
        self.assertFalse(session.in_flight)
        self.assertIsNone(session.bridge)
        self.assertIsNone(session.worker)

    def test_embedded_refuses_off_main_thread(self):
        namespace = self._embedded_namespace()
        namespace["CHECKOUT"] = self.checkout
        errors = []

        def run():
            try:
                namespace["_schedule"](
                    bridge_factory=lambda handler: FakeBridge(),
                    worker_factory=lambda target, bridge: FakeWorker(target, bridge),
                )
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        thread = threading.Thread(target=run)
        thread.start()
        thread.join(timeout=10)
        self.assertEqual(len(errors), 1)
        self.assertIn("main thread", str(errors[0]))


if __name__ == "__main__":
    unittest.main()

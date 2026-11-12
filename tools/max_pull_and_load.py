"""Standalone asynchronous launcher for the experimental Max 2025 prototype.

This module is deliberately stdlib-importable: Qt, ``pymxs``, and every host
module are imported lazily.  It is the Python companion to
``tools/max_pull_and_load.ms`` and owns the only supported Git checkout
update, import-alias, commit-guard and restart policy.

Public surface (frozen by the delivery plan)::

    PACKAGE_NAME, REPO_URL, BRANCH, DEFAULT_CHECKOUT
    LauncherError
    start(*, checkout=DEFAULT_CHECKOUT) -> None
    load_checkout_package(checkout, head_sha) -> ModuleType

``start`` is asynchronous: it requires the Max main thread, schedules one
bounded Git/worker operation, and returns immediately.  Completion runs on the
main thread through a queued Qt bridge and either raises/shows the existing
tools panel or stops with a finite failure category.

``load_checkout_package`` is the host-free, synchronous import seam.  It
validates paths, an already verified full hexadecimal HEAD SHA, and the
session stamp, then installs the checkout directory itself as the
``max2025_menu_prototype`` package.  It never runs Git, never installs
dependencies, and never shows UI.

The standalone ``.ms`` embeds a tiny base64-encoded copy of the clone-only
bootstrap below; ``render_ms_source`` and ``extract_embedded_python`` are the
documented seam that keeps the two files from drifting.

Usage inside Max (main thread)::

    import runpy
    ns = runpy.run_path(r"C:\\dev\\3dsModeler\\tools\\max_pull_and_load.py",
                        run_name="_max2025_pull_and_load_companion")
    ns["start"]()
"""

from __future__ import annotations

import base64
import importlib
import importlib.util
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import types
import zlib
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

PACKAGE_NAME = "max2025_menu_prototype"
REPO_URL = "https://github.com/nekirk11/3dsModeler.git"
BRANCH = "dev"
DEFAULT_CHECKOUT = Path(r"C:\dev\3dsModeler")

SESSION_MODULE_NAME = "_max2025_pull_and_load_session"
SESSION_SCHEMA_VERSION = 1
SESSION_FIELDS = (
    "schema_version",
    "checkout_root",
    "loaded_head_sha",
    "restart_required",
    "in_flight",
    "first_clone_head_sha",
    "bootstrap",
    "bridge",
    "worker",
)

LOCAL_GIT_TIMEOUT = 15.0
NETWORK_GIT_TIMEOUT = 120.0
#: Short synchronous UX bound for the hotkey/local-load path (no network).
LOCAL_LOAD_TIMEOUT = 2.0
_MAX_CAPTURED_BYTES = 65536
# Bounded grace period for terminating and reaping a timed-out Git tree.
_KILL_WAIT = 5.0
_REAP_POLL = 0.05
_HEX_SHA = re.compile(r"^[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?$")

_TREE_REAP_STATE = threading.local()


def _begin_tree_reap_tracking() -> None:
    """Start one worker run with no recorded unreaped tree."""

    _TREE_REAP_STATE.unreaped = False


def _mark_tree_unreaped() -> None:
    """Sticky record: any unconfirmed tree kill poisons the whole run."""

    _TREE_REAP_STATE.unreaped = True


def _tree_reap_unreaped() -> bool:
    return bool(getattr(_TREE_REAP_STATE, "unreaped", False))


def _end_tree_reap_tracking() -> bool:
    unreaped = _tree_reap_unreaped()
    _TREE_REAP_STATE.unreaped = False
    return unreaped

REQUIRED_ROOT_ENTRIES = (
    "__init__.py",
    "core",
    "max_host",
    "qt_ui",
    "core/default_profile.json",
)

#: Persistent MacroScript category/names.  Stable across updates so an
#: operator's manually assigned Max hotkeys keep working.
MACRO_CATEGORY = "3dsModeler"
MACRO_SEARCH_NAME = "ThreeDsModeler_Search"
MACRO_CONTEXT_NAMES = {
    "object": "ThreeDsModeler_ContextObject",
    "vertex": "ThreeDsModeler_ContextVertex",
    "edge": "ThreeDsModeler_ContextEdge",
    "border": "ThreeDsModeler_ContextBorder",
    "face": "ThreeDsModeler_ContextFace",
    "element": "ThreeDsModeler_ContextElement",
}
MACRO_CONVERT_NAMES = {
    "object": "ThreeDsModeler_ContextObjectConvert",
    "vertex": "ThreeDsModeler_ContextVertexConvert",
    "edge": "ThreeDsModeler_ContextEdgeConvert",
    "border": "ThreeDsModeler_ContextBorderConvert",
    "face": "ThreeDsModeler_ContextFaceConvert",
    "element": "ThreeDsModeler_ContextElementConvert",
}
MACRO_NAMES = (
    MACRO_SEARCH_NAME,
    *MACRO_CONTEXT_NAMES.values(),
    *MACRO_CONVERT_NAMES.values(),
)
MACRO_FILE_NAME = "3dsModeler.mcr"
MACRO_FILE_HEADER = (
    "-- 3dsModeler macro definitions (managed by the 3dsModeler launcher; "
    "do not edit)\n"
)
STARTUP_FILE_NAME = "3dsModeler_startup.ms"
STARTUP_FILE_HEADER = (
    "-- 3dsModeler startup hotkey (managed by the 3dsModeler launcher; "
    "do not edit)\n"
)
LOCAL_CONTEXTS = ("object", "vertex", "edge", "border", "face", "element")
LOCAL_ACTIONS = ("search", "install_hotkey", *LOCAL_CONTEXTS)

RESTART_MESSAGE = "Checkout updated; restart 3ds Max before loading this commit."
PARTIAL_IMPORT_MESSAGE = (
    "The package did not load cleanly. Restart 3ds Max before retrying."
)
STALE_IMPORT_MESSAGE = (
    "A package is already loaded without a verified checkout commit stamp. "
    "Restart 3ds Max."
)
DIFFERENT_CHECKOUT_MESSAGE = (
    "A different checkout is already loaded. Restart 3ds Max before switching."
)
DIRTY_MESSAGE = (
    "The checkout has uncommitted changes. Commit or stash them outside Max, "
    "then retry."
)
BRANCH_MESSAGE = "The checkout is not on the dev branch. Repair it outside Max."
ORIGIN_MESSAGE = (
    "The checkout origin is not the expected HTTPS repository. Repair it "
    "outside Max."
)
LAYOUT_MESSAGE = (
    "The checkout layout is not a complete prototype package. Repair it "
    "outside Max."
)
TOP_LEVEL_MESSAGE = (
    "The Git top-level is not the checkout directory. Repair it outside Max."
)
GIT_OPERATION_MESSAGE = (
    "A Git merge, rebase, cherry-pick, or sequencer operation is in progress. "
    "Finish or abort it outside Max."
)
DIVERGED_MESSAGE = (
    "The local dev branch is ahead of or diverged from origin/dev. Refusing to "
    "discard local commits."
)
AUTH_MESSAGE = (
    "Git could not access the repository. Configure noninteractive credentials "
    "outside Max and retry."
)
NETWORK_MESSAGE = (
    "Git could not reach the repository. Check the network and retry."
)
GIT_FAILED_MESSAGE = "Git reported a failure. Repair the checkout outside Max."
MISSING_GIT_MESSAGE = "Git is not on PATH. Install Git for Windows outside Max."
TIMEOUT_MESSAGE = "Git did not finish in time. No retry was attempted."
TARGET_EXISTS_MESSAGE = (
    "The checkout path already exists but is not a validated checkout. Repair "
    "or move it outside Max, then retry."
)
PARTIAL_CLONE_MESSAGE = (
    "The clone did not finish. The partial directory was left in place for "
    "manual repair; nothing was deleted."
)
NO_QT_MESSAGE = (
    "Start 3ds Max 2025 before running the launcher; its Qt application was "
    "not found."
)
MAIN_THREAD_MESSAGE = "The launcher must run on the 3ds Max main thread."
DUPLICATE_MESSAGE = "An update is already in progress."
LOCAL_LOAD_MESSAGE = (
    "Run max_pull_and_load.ms once to install or update the local 3dsModeler "
    "checkout, then retry the hotkey."
)
LOCAL_CHANGED_MESSAGE = (
    "The local checkout changed after this session loaded a commit. Restart "
    "3ds Max before using the hotkey."
)
MACRO_CONFLICT_MESSAGE = (
    "A file named 3dsModeler.mcr already exists in the Max MacroScripts folder "
    "and was not created by this launcher. Move or rename it outside Max, then "
    "run max_pull_and_load.ms again."
)
MACRO_WRITE_MESSAGE = (
    "The 3dsModeler MacroScript file could not be written. Check that the Max "
    "user MacroScripts folder is writable, then run max_pull_and_load.ms again."
)
MACRO_DIR_MESSAGE = (
    "The Max user MacroScripts folder could not be resolved. Run "
    "max_pull_and_load.ms inside 3ds Max 2025, then retry."
)
STARTUP_CONFLICT_MESSAGE = (
    "A file named 3dsModeler_startup.ms already exists in the Max startup "
    "folder and was not created by this launcher. Move or rename it outside "
    "Max, then run max_pull_and_load.ms again."
)
STARTUP_WRITE_MESSAGE = (
    "The 3dsModeler startup script could not be written. Check that the Max "
    "user startup folder is writable, then run max_pull_and_load.ms again."
)
STARTUP_DIR_MESSAGE = (
    "The Max user startup folder could not be resolved. Run "
    "max_pull_and_load.ms inside 3ds Max 2025, then retry."
)
LISTENER_TIP = (
    "MAXScript tab diagnostics: python.Execute "
    '"from max2025_menu_prototype.max_host import bootstrap; '
    'bootstrap.report_diagnostics()"'
)
_NON_REPO_MAX_ENTRIES = 3
_NON_REPO_MAX_NAME = 40

_EMBEDDED_BEGIN = "-- BEGIN EMBEDDED PYTHON"
_EMBEDDED_END = "-- END EMBEDDED PYTHON"


class LauncherError(RuntimeError):
    """A bounded, user-facing launcher failure.

    ``category`` is one of the finite failure classes used by tests and the
    Listener; ``message`` never contains raw process output or credentials.
    """

    def __init__(
        self,
        message: str,
        category: str = "launcher",
        *,
        tree_reaped: bool = True,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.message = message
        # ``False`` means a timed-out Git child (or one of its helpers) could
        # not be confirmed stopped; the session must not allow a retry because
        # that helper may still be mutating the checkout.
        self.tree_reaped = tree_reaped


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------


def _emit(message: str) -> None:
    """Write one finite progress/outcome line to the Listener."""

    print(message, flush=True)


def _exception_detail(exc: BaseException) -> str:
    """Return a bounded, ASCII-clean ``<ExcType>: <reason>`` diagnostic.

    Mirrors the embedded companion's exception sanitizer so an unexpected
    non-``LauncherError`` failure on the main thread still leaves one useful
    Listener line before it is re-raised.
    """

    try:
        name = "".join(
            ch for ch in type(exc).__name__
            if ch.isascii() and (ch.isalnum() or ch == "_")
        )[:48] or "Exception"
    except Exception:
        name = "Exception"
    try:
        text = str(exc)
    except Exception:
        text = ""
    cleaned = "".join(
        ch if ch.isascii() and ch.isprintable() and ch not in "\r\n\t" else " "
        for ch in text
    )
    cleaned = " ".join(cleaned.split())[:160].rstrip()
    return "%s: %s" % (name, cleaned) if cleaned else name


def _assert_main_thread() -> None:
    if threading.current_thread() is not threading.main_thread():
        raise LauncherError(MAIN_THREAD_MESSAGE, "main_thread")


def _normalize_sha(value: Any) -> str:
    if not isinstance(value, str):
        raise LauncherError(
            "The expected commit identifier is not a full hexadecimal SHA.",
            "stale_import",
        )
    text = value.strip()
    if not _HEX_SHA.match(text):
        raise LauncherError(
            "The expected commit identifier is not a full hexadecimal SHA.",
            "stale_import",
        )
    return text.lower()


def _same_path(left: Path | str, right: Path | str) -> bool:
    try:
        left_text = os.path.normcase(str(Path(left).resolve()))
        right_text = os.path.normcase(str(Path(right).resolve()))
    except OSError:
        return False
    return left_text == right_text


def _inside(path: Path | str, root: Path) -> bool:
    try:
        return Path(path).resolve().is_relative_to(root.resolve())
    except (OSError, ValueError):
        return False


def _requested_root(checkout: Path | str) -> Path:
    """Canonicalize a requested checkout that may not exist yet."""

    path = Path(checkout)
    if path.is_symlink():
        raise LauncherError(
            "The checkout path is a filesystem redirect. Use a real directory.",
            "checkout_layout",
        )
    try:
        return path.resolve()
    except OSError as exc:
        raise LauncherError(LAYOUT_MESSAGE, "checkout_layout") from exc


def _checkout_root(checkout: Path | str) -> Path:
    """Canonicalize a checkout without accepting a redirected root."""

    path = Path(checkout)
    if path.is_symlink():
        raise LauncherError(
            "The checkout path is a filesystem redirect. Use a real directory.",
            "checkout_layout",
        )
    try:
        root = path.resolve()
    except OSError as exc:
        raise LauncherError(LAYOUT_MESSAGE, "checkout_layout") from exc
    if not root.is_dir():
        raise LauncherError(LAYOUT_MESSAGE, "checkout_layout")
    for relative in REQUIRED_ROOT_ENTRIES:
        candidate = root / relative
        if not candidate.exists():
            raise LauncherError(LAYOUT_MESSAGE, "checkout_layout")
        if candidate.is_symlink() and not _inside(candidate.resolve(), root):
            raise LauncherError(LAYOUT_MESSAGE, "checkout_layout")
    return root


def _is_empty_directory(path: Path) -> bool:
    """True only for a real, direct child-free directory."""

    try:
        if not path.is_dir() or path.is_symlink():
            return False
        return not any(path.iterdir())
    except OSError:
        return False


def _sanitize_entry_name(name: str) -> str:
    cleaned = "".join(
        char for char in name if char.isprintable() and char not in "\r\n\t"
    )
    return cleaned[:_NON_REPO_MAX_NAME] or "?"


def _not_a_checkout_message(checkout: Path) -> str:
    """Finite repair instruction naming a bounded, sanitized entry sample."""

    try:
        names = sorted(child.name for child in checkout.iterdir())
    except OSError:
        names = []
    shown = ", ".join(
        _sanitize_entry_name(name) for name in names[:_NON_REPO_MAX_ENTRIES]
    )
    if len(names) > _NON_REPO_MAX_ENTRIES:
        shown += ", ..."
    return (
        "%s already exists and is not a Git checkout (found: %s). Save "
        "max_pull_and_load.ms outside %s (for example %s) and leave %s empty "
        "or absent, then retry."
        % (
            checkout,
            shown,
            checkout,
            checkout.parent / "max_pull_and_load.ms",
            checkout,
        )
    )


def _emit_listener_tip() -> None:
    _emit(LISTENER_TIP)


def _expected_module_paths(name: str, root: Path) -> tuple[Path, ...]:
    suffix = name[len(PACKAGE_NAME) :]
    parts = tuple(part for part in suffix.split(".") if part)
    base = root.joinpath(*parts) if parts else root
    if not parts:
        return (root / "__init__.py",)
    return (base.with_suffix(".py"), base / "__init__.py")


def _verify_module_path(name: str, module: Any, root: Path) -> None:
    file_value = getattr(module, "__file__", None)
    if not file_value:
        raise LauncherError(
            "A stale package module has no file origin. Restart 3ds Max.",
            "stale_import",
        )
    try:
        file_path = Path(file_value).resolve()
    except OSError as exc:
        raise LauncherError(STALE_IMPORT_MESSAGE, "stale_import") from exc
    if not _inside(file_path, root):
        raise LauncherError(STALE_IMPORT_MESSAGE, "stale_import")
    expected = _expected_module_paths(name, root)
    if not any(_same_path(file_path, candidate) for candidate in expected):
        raise LauncherError(STALE_IMPORT_MESSAGE, "stale_import")

    spec = getattr(module, "__spec__", None)
    origin = getattr(spec, "origin", None) if spec is not None else None
    if origin is not None and not _same_path(origin, file_path):
        raise LauncherError(STALE_IMPORT_MESSAGE, "stale_import")

    package_path = getattr(module, "__path__", None)
    if package_path is not None:
        expected_dir = root.joinpath(*name[len(PACKAGE_NAME) :].split("."))
        expected_dir = expected_dir if name != PACKAGE_NAME else root
        for entry in package_path:
            if not _inside(entry, root) or not _same_path(entry, expected_dir):
                raise LauncherError(STALE_IMPORT_MESSAGE, "stale_import")


def _alias_modules() -> dict[str, Any]:
    return {
        name: module
        for name, module in list(sys.modules.items())
        if name == PACKAGE_NAME or name.startswith(PACKAGE_NAME + ".")
    }


def _verify_loaded_alias(root: Path) -> None:
    for name, module in _alias_modules().items():
        _verify_module_path(name, module, root)


def _session_state() -> types.ModuleType:
    """Return the dedicated, schema-checked session module.

    The module lives outside the aliased package tree so it survives repeated
    ``runpy.run_path`` execution.  Unknown schemas or roots fail closed.
    """

    module = sys.modules.get(SESSION_MODULE_NAME)
    if module is None:
        module = types.ModuleType(SESSION_MODULE_NAME)
        module.schema_version = SESSION_SCHEMA_VERSION
        module.checkout_root = None
        module.loaded_head_sha = None
        module.restart_required = False
        module.in_flight = False
        module.first_clone_head_sha = None
        module.bootstrap = None
        module.bridge = None
        module.worker = None
        sys.modules[SESSION_MODULE_NAME] = module
        return module
    if not isinstance(module, types.ModuleType):
        raise LauncherError(
            "The launcher session marker is invalid. Restart 3ds Max.",
            "stale_import",
        )
    for field in SESSION_FIELDS:
        if not hasattr(module, field):
            raise LauncherError(
                "The launcher session marker is incomplete. Restart 3ds Max.",
                "stale_import",
            )
    if module.schema_version != SESSION_SCHEMA_VERSION:
        raise LauncherError(
            "The launcher session marker uses an unknown schema. Restart "
            "3ds Max.",
            "stale_import",
        )
    if module.checkout_root is not None and not isinstance(module.checkout_root, str):
        raise LauncherError(
            "The launcher session root is invalid. Restart 3ds Max.",
            "stale_import",
        )
    for field in ("loaded_head_sha", "first_clone_head_sha"):
        value = getattr(module, field)
        if value is not None:
            setattr(module, field, _normalize_sha(value))
    return module


def _uninstall_trusted(session: types.ModuleType) -> None:
    module = session.bootstrap
    if module is None:
        return
    try:
        module.uninstall()
    except Exception:
        # Cleanup failure must not resurrect the updated code or clear the
        # persistent restart-required state.
        pass
    finally:
        session.bootstrap = None


# ---------------------------------------------------------------------------
# Git process execution (worker side only)
# ---------------------------------------------------------------------------


def _is_windows() -> bool:
    """Indirection so tests can simulate the Windows transport path."""

    return os.name == "nt"


def _creation_flags() -> int:
    if _is_windows():
        return int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
    return 0


def _process_kwargs() -> dict[str, Any]:
    """Return ``Popen`` keywords that put Git in its own kill-able group."""

    kwargs: dict[str, Any] = {"creationflags": _creation_flags()}
    if not _is_windows():
        # A dedicated session/process group lets ``killpg`` reach transport
        # and credential helpers that inherit Git's pipes.
        kwargs["start_new_session"] = True
    return kwargs


def _reap(process: subprocess.Popen) -> bool:
    """Bounded reap; True only when the child is confirmed exited."""

    try:
        process.communicate(timeout=_KILL_WAIT)
        return True
    except subprocess.TimeoutExpired:
        pass
    except Exception:
        return process.poll() is not None

    for stream in (getattr(process, "stdout", None), getattr(process, "stderr", None)):
        if stream is None:
            continue
        try:
            stream.close()
        except Exception:
            pass
    try:
        process.wait(timeout=_KILL_WAIT)
        return True
    except subprocess.TimeoutExpired:
        return False
    except Exception:
        return process.poll() is not None


def _wait_group_gone(pgid: int) -> bool:
    """Poll until no process in ``pgid`` remains, within ``_KILL_WAIT``."""

    deadline = time.monotonic() + _KILL_WAIT
    while True:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return True
        except OSError:
            return False
        if time.monotonic() >= deadline:
            return False
        time.sleep(_REAP_POLL)


def _terminate_tree(process: subprocess.Popen) -> bool:
    """Terminate the whole Git process tree; True only when proven stopped.

    Killing only the immediate ``git`` process lets spawned Windows transport
    helpers (``git-remote-https``, ``git-credential-manager``, ...) survive and
    keep stdout/stderr open.  A confirmed tree kill requires a successful
    ``taskkill /T`` on Windows, or a POSIX process-group kill followed by a
    bounded poll that proves the group is gone.  Reaping the immediate child is
    never treated as proof of the whole tree.
    """

    pid = getattr(process, "pid", None)
    windows = _is_windows()
    tree_confirmed = False
    pgid: Optional[int] = None
    if pid is None:
        tree_confirmed = False
    elif windows:
        try:
            completed = subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                creationflags=_creation_flags(),
                timeout=_KILL_WAIT,
                check=False,
            )
            tree_confirmed = getattr(completed, "returncode", None) == 0
        except Exception:
            tree_confirmed = False
    else:
        # ``start_new_session=True`` makes the child its own session and
        # process-group leader, so the group id is exactly its pid.  Using the
        # pid directly also reaches helpers after the leader has exited, when
        # ``getpgid`` would raise and the group could never be polled.
        pgid = pid
        try:
            os.killpg(pgid, signal.SIGKILL)
            tree_confirmed = True
        except ProcessLookupError:
            tree_confirmed = True
        except Exception:
            tree_confirmed = False
    try:
        process.kill()
    except Exception:
        pass
    reaped = _reap(process)
    if not windows and pgid is not None and tree_confirmed:
        tree_confirmed = _wait_group_gone(pgid)
    return bool(reaped and tree_confirmed)


def _git_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["GCM_INTERACTIVE"] = "Never"
    environment.pop("GIT_ASKPASS", None)
    environment.pop("SSH_ASKPASS", None)
    return environment


def _bounded(data: bytes | None) -> str:
    if not data:
        return ""
    return data[:_MAX_CAPTURED_BYTES].decode("utf-8", "replace")


def _run_git(
    args: Iterable[str],
    *,
    cwd: Path | str,
    timeout: float,
) -> "GitResult":
    """Run one noninteractive Git command with a hard deadline.

    Never called on the main thread.  Arguments are an array, the shell is
    disabled, stdin is ``DEVNULL``, output is bounded, prompts are disabled,
    and a timed-out process tree is terminated and reaped before the timeout
    error is returned.  If the tree cannot be confirmed reaped the error is
    flagged so the session stays locked instead of racing the survivor.
    """

    executable = shutil.which("git")
    if not executable:
        raise LauncherError(MISSING_GIT_MESSAGE, "missing_git")

    command = [executable, *(str(item) for item in args)]
    try:
        process = subprocess.Popen(
            command,
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            env=_git_environment(),
            **_process_kwargs(),
        )
    except OSError as exc:
        raise LauncherError(GIT_FAILED_MESSAGE, "git_failed") from exc

    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        reaped = _terminate_tree(process)
        if not reaped:
            _mark_tree_unreaped()
        raise LauncherError(
            TIMEOUT_MESSAGE, "timeout", tree_reaped=reaped
        ) from None
    except OSError as exc:
        raise LauncherError(GIT_FAILED_MESSAGE, "git_failed") from exc
    return GitResult(process.returncode, _bounded(stdout), _bounded(stderr))


class GitResult:
    """A bounded, shell-free Git process result."""

    __slots__ = ("returncode", "stdout", "stderr")

    def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _classify_git_failure(result: GitResult) -> LauncherError:
    text = (result.stderr + "\n" + result.stdout).lower()
    auth_markers = (
        "authentication failed",
        "could not read username",
        "could not read password",
        "permission denied",
        "repository not found",
        "not found",
        "access denied",
        "403",
    )
    network_markers = (
        "could not resolve host",
        "failed to connect",
        "connection timed out",
        "connection refused",
        "network is unreachable",
        "unable to access",
        "ssl",
        "tls",
        "rpc failed",
    )
    if any(marker in text for marker in auth_markers):
        return LauncherError(AUTH_MESSAGE, "auth")
    if any(marker in text for marker in network_markers):
        return LauncherError(NETWORK_MESSAGE, "network")
    return LauncherError(GIT_FAILED_MESSAGE, "git_failed")


def _git_stdout(args: Iterable[str], *, cwd: Path, timeout: float = LOCAL_GIT_TIMEOUT) -> str:
    result = _run_git(args, cwd=cwd, timeout=timeout)
    if result.returncode != 0:
        raise _classify_git_failure(result)
    return result.stdout.strip()


def _head(checkout: Path, *, timeout: float = LOCAL_GIT_TIMEOUT) -> str:
    value = _git_stdout(["rev-parse", "HEAD"], cwd=checkout, timeout=timeout)
    return _normalize_sha(value)


def _status_dirty_entries(text: str) -> list[str]:
    """Return dirty status lines, tolerating only the checked-out overlay file.

    ``user_aliases.json`` is normally gitignored; filtering the exact basename
    keeps an intentionally present overlay from being mistaken for tracked
    source edits while still refusing every other dirty path.
    """

    dirty: list[str] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split(None, 1)
        path = parts[1].strip() if len(parts) > 1 else ""
        if path == "user_aliases.json":
            continue
        dirty.append(stripped)
    return dirty


def _remote_dev_sha(checkout: Path) -> str:
    value = _git_stdout(
        ["rev-parse", "--verify", "refs/remotes/origin/dev"], cwd=checkout
    )
    return _normalize_sha(value)


def _normalize_remote(url: str) -> str:
    """Case-fold a remote URL so repository-name casing never mismatches."""

    text = (url or "").strip().rstrip("/")
    if text.lower().endswith(".git"):
        text = text[:-4]
    return text.rstrip("/").lower()


_EXPECTED_REMOTE = _normalize_remote(REPO_URL)


def _git_operation_in_progress(checkout: Path) -> bool:
    """Check for an in-progress Git operation without swallowing failures.

    A swallowed ``LauncherError`` here could discard a ``tree_reaped=False``
    timeout and let the update continue against a live Git child.
    """

    git_dir = Path(
        _git_stdout(["rev-parse", "--absolute-git-dir"], cwd=checkout)
    )
    for name in (
        "MERGE_HEAD",
        "CHERRY_PICK_HEAD",
        "REVERT_HEAD",
        "rebase-merge",
        "rebase-apply",
        "sequencer",
    ):
        if (git_dir / name).exists():
            return True
    return False


def _validate_checkout(
    checkout: Path, *, timeout: float = LOCAL_GIT_TIMEOUT
) -> dict[str, Any]:
    """Validate an existing checkout with bounded local Git reads only.

    ``timeout`` lets the synchronous hotkey path use a short UX bound while the
    asynchronous updater keeps its existing validation budget.
    """

    root = _checkout_root(checkout)
    top_level = _git_stdout(
        ["rev-parse", "--show-toplevel"], cwd=root, timeout=timeout
    )
    if not _same_path(top_level, root):
        raise LauncherError(TOP_LEVEL_MESSAGE, "wrong_top_level")
    bare = _git_stdout(
        ["rev-parse", "--is-bare-repository"], cwd=root, timeout=timeout
    ).lower()
    if bare == "true":
        raise LauncherError(LAYOUT_MESSAGE, "checkout_layout")
    branch_result = _run_git(
        ["symbolic-ref", "--quiet", "--short", "HEAD"], cwd=root, timeout=timeout
    )
    if branch_result.returncode != 0:
        raise LauncherError(BRANCH_MESSAGE, "detached_head")
    branch = branch_result.stdout.strip()
    if branch != BRANCH:
        raise LauncherError(BRANCH_MESSAGE, "wrong_branch")
    origin_result = _run_git(
        ["remote", "get-url", "origin"], cwd=root, timeout=timeout
    )
    if origin_result.returncode != 0:
        raise LauncherError(ORIGIN_MESSAGE, "wrong_origin")
    if _normalize_remote(origin_result.stdout) != _EXPECTED_REMOTE:
        raise LauncherError(ORIGIN_MESSAGE, "wrong_origin")
    head = _head(root, timeout=timeout)
    if _git_operation_in_progress(root):
        raise LauncherError(GIT_OPERATION_MESSAGE, "git_operation")
    status = _run_git(
        ["status", "--porcelain=v1", "--untracked-files=all"],
        cwd=root,
        timeout=timeout,
    )
    if status.returncode != 0:
        raise _classify_git_failure(status)
    if _status_dirty_entries(status.stdout):
        raise LauncherError(DIRTY_MESSAGE, "dirty_tree")
    return {
        "root": str(root),
        "head": head,
        "branch": branch,
    }


def _clone_checkout(checkout: Path) -> str:
    if checkout.exists() and not _is_empty_directory(checkout):
        raise LauncherError(TARGET_EXISTS_MESSAGE, "permission")
    try:
        checkout.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise LauncherError(TARGET_EXISTS_MESSAGE, "permission") from exc

    result = _run_git(
        ["clone", "--branch", BRANCH, REPO_URL, str(checkout)],
        cwd=checkout.parent,
        timeout=NETWORK_GIT_TIMEOUT,
    )
    if result.returncode != 0:
        if checkout.exists() and not _is_empty_directory(checkout):
            raise LauncherError(PARTIAL_CLONE_MESSAGE, "partial_clone")
        raise _classify_git_failure(result)
    try:
        return _validate_checkout(checkout)["head"]
    except LauncherError as exc:
        raise LauncherError(
            PARTIAL_CLONE_MESSAGE, "partial_clone", tree_reaped=exc.tree_reaped
        ) from exc


def _fetch_origin_dev(checkout: Path) -> None:
    result = _run_git(
        [
            "fetch",
            "--no-tags",
            "origin",
            "+refs/heads/dev:refs/remotes/origin/dev",
        ],
        cwd=checkout,
        timeout=NETWORK_GIT_TIMEOUT,
    )
    if result.returncode != 0:
        raise _classify_git_failure(result)


def _pull_ff_only(checkout: Path) -> None:
    result = _run_git(
        ["pull", "--ff-only", "origin", BRANCH],
        cwd=checkout,
        timeout=NETWORK_GIT_TIMEOUT,
    )
    if result.returncode != 0:
        raise _classify_git_failure(result)


def _is_ancestor(ancestor: str, descendant: str, checkout: Path) -> bool:
    result = _run_git(
        ["merge-base", "--is-ancestor", ancestor, descendant],
        cwd=checkout,
        timeout=LOCAL_GIT_TIMEOUT,
    )
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    raise _classify_git_failure(result)


# ---------------------------------------------------------------------------
# Worker result and update decision (no Qt, pymxs, or widget access)
# ---------------------------------------------------------------------------


def _result(
    *,
    ok: bool,
    action: str,
    before_sha: Optional[str] = None,
    after_sha: Optional[str] = None,
    category: str = "",
    message: str = "",
    unreaped: bool = False,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "action": action,
        "before_sha": before_sha,
        "after_sha": after_sha,
        "category": category,
        "message": message,
        "unreaped": unreaped,
    }


def _failure(category: str, message: str, *, unreaped: bool = False) -> dict[str, Any]:
    return _result(
        ok=False, action="failed", category=category, message=message, unreaped=unreaped
    )


def _worker_update_locked(
    checkout: Path,
    expected_handoff: Optional[str],
    loaded_sha: Optional[str],
    loaded_root: Optional[str],
    modules_loaded: bool,
) -> dict[str, Any]:
    """Decide and perform one bounded update, entirely off the main thread."""

    try:
        if expected_handoff is not None:
            if modules_loaded or loaded_sha is not None:
                return _failure("stale_import", STALE_IMPORT_MESSAGE)
            info = _validate_checkout(checkout)
            if info["head"] != expected_handoff:
                return _failure(
                    "checkout_changed",
                    "The cloned checkout changed before it could load. Restart "
                    "3ds Max and retry.",
                )
            return _result(
                ok=True,
                action="handoff",
                before_sha=info["head"],
                after_sha=info["head"],
            )

        if not checkout.exists() or _is_empty_directory(checkout):
            if modules_loaded or loaded_sha is not None:
                return _failure("stale_import", STALE_IMPORT_MESSAGE)
            head = _clone_checkout(checkout)
            return _result(ok=True, action="clone", after_sha=head)

        if not (checkout / ".git").exists():
            return _failure("not_a_checkout", _not_a_checkout_message(checkout))

        info = _validate_checkout(checkout)
        before = info["head"]
        root_text = info["root"]

        if loaded_sha is not None:
            if loaded_root is None or not _same_path(root_text, loaded_root):
                return _failure("stale_import", DIFFERENT_CHECKOUT_MESSAGE)
            if before != loaded_sha:
                return _failure("restart_required", RESTART_MESSAGE)
            _fetch_origin_dev(checkout)
            after = _head(checkout)
            if after != before:
                return _failure("restart_required", RESTART_MESSAGE)
            remote = _remote_dev_sha(checkout)
            if remote != before:
                return _failure("restart_required", RESTART_MESSAGE)
            return _result(
                ok=True,
                action="noop",
                before_sha=before,
                after_sha=after,
            )

        if modules_loaded:
            return _failure("stale_import", STALE_IMPORT_MESSAGE)

        _fetch_origin_dev(checkout)
        after_fetch = _head(checkout)
        if after_fetch != before:
            return _failure(
                "checkout_changed",
                "The checkout changed during the update check. No files were "
                "modified; retry.",
            )
        remote = _remote_dev_sha(checkout)
        if remote == before:
            return _result(
                ok=True,
                action="import-only",
                before_sha=before,
                after_sha=before,
            )
        if not _is_ancestor(before, remote, checkout):
            return _failure("diverged", DIVERGED_MESSAGE)
        # Recheck clean state and HEAD immediately before touching tracked files.
        try:
            revalidated = _validate_checkout(checkout)["head"]
        except LauncherError as exc:
            return _failure(
                exc.category, exc.message, unreaped=not exc.tree_reaped
            )
        if revalidated != before:
            return _failure(
                "checkout_changed",
                "The checkout changed before the fast-forward pull. No files "
                "were modified; retry.",
            )
        _pull_ff_only(checkout)
        after = _head(checkout)
        if after == before or after != remote:
            return _failure(
                "checkout_changed",
                "The checkout changed during the fast-forward pull. No retry "
                "was attempted.",
            )
        return _result(
            ok=True,
            action="pull-import",
            before_sha=before,
            after_sha=after,
        )
    except LauncherError as exc:
        return _failure(
            exc.category, exc.message, unreaped=not exc.tree_reaped
        )
    except Exception:
        return _failure(
            "internal",
            "The launcher stopped after an unexpected local failure. No retry "
            "was attempted.",
        )


def _worker_update(
    checkout: Path,
    expected_handoff: Optional[str],
    loaded_sha: Optional[str],
    loaded_root: Optional[str],
    modules_loaded: bool,
) -> dict[str, Any]:
    """Run one update decision and fold in sticky unreaped-tree state.

    Any Git timeout whose tree could not be confirmed stopped — including one
    converted to a failure by an inner handler — forces ``unreaped`` so the
    main thread latches ``restart_required`` instead of racing a survivor.
    """

    _begin_tree_reap_tracking()
    try:
        result = _worker_update_locked(
            checkout, expected_handoff, loaded_sha, loaded_root, modules_loaded
        )
    except LauncherError as exc:
        result = _failure(exc.category, exc.message, unreaped=not exc.tree_reaped)
    except Exception:
        result = _failure(
            "internal",
            "The launcher stopped after an unexpected local failure. No retry "
            "was attempted.",
        )
    if _end_tree_reap_tracking():
        result["unreaped"] = True
    return result


# ---------------------------------------------------------------------------
# Qt bridge and worker plumbing (created on the main thread)
# ---------------------------------------------------------------------------


class _SignalBridge:
    """Small bridge protocol shared by the real Qt object and test fakes."""

    def connect(self, slot: Callable[[Any], None]) -> None:  # pragma: no cover
        raise NotImplementedError

    def emit(self, result: Any) -> None:  # pragma: no cover
        raise NotImplementedError


def _qt_bridge_factory() -> _SignalBridge:
    """Create a main-thread QObject bridge over Max's existing QApplication."""

    from PySide6 import QtCore, QtWidgets  # type: ignore[import-not-found]

    application = QtWidgets.QApplication.instance()
    if application is None:
        raise LauncherError(NO_QT_MESSAGE, "no_qt")

    class _QtRelay(QtCore.QObject):
        completed = QtCore.Signal(object)

        # Keep these annotations off PySide6 slots: when this module is exec'd
        # from a plain namespace, PEP 563 string annotations make Shiboken
        # unable to resolve ``object``/``None`` during slot registration.
        #
        # Never define ``connect`` (or any other public QObject method) here.
        # In PySide6 6.5.3 ``SignalInstance.connect`` forwards to
        # ``QObject.connect(self, signal, receiver, slot, type)`` with five
        # positional arguments; an override would collide with that internal
        # call and raise ``TypeError: connect() takes 2 positional arguments
        # but 5 were given``.
        def __init__(self):
            super().__init__()
            self._slots = []
            self.completed.connect(self._deliver, QtCore.Qt.ConnectionType.QueuedConnection)

        @QtCore.Slot(object)
        def _deliver(self, result):
            for slot in list(self._slots):
                slot(result)

    class _Bridge(_SignalBridge):
        """Plain-Python handle; must not shadow any ``QObject`` method."""

        def __init__(self, relay):
            # A strong reference keeps the QObject alive for the worker.
            self._relay = relay

        def connect(self, slot):
            self._relay._slots.append(slot)

        def emit(self, result):
            self._relay.completed.emit(result)

    module_name = _QtRelay.__module__
    if module_name not in sys.modules:
        sys.modules[module_name] = types.ModuleType(module_name)

    relay = _QtRelay()
    relay.moveToThread(application.thread())
    return _Bridge(relay)


def _thread_worker_factory(
    target: Callable[[], dict[str, Any]], bridge: _SignalBridge
) -> Any:
    class _Worker:
        def __init__(self) -> None:
            self._thread = threading.Thread(
                target=self._run, name="max2025-launcher", daemon=True
            )

        def start(self) -> None:
            self._thread.start()

        def _run(self) -> None:
            try:
                result = target()
            except Exception:
                result = _failure(
                    "internal",
                    "The launcher worker stopped after an unexpected failure. "
                    "No retry was attempted.",
                )
            bridge.emit(result)

    return _Worker()


_bridge_factory: Callable[[], _SignalBridge] = _qt_bridge_factory
_worker_factory: Callable[[Callable[[], dict[str, Any]], _SignalBridge], Any] = (
    _thread_worker_factory
)


# ---------------------------------------------------------------------------
# Import alias and commit guard
# ---------------------------------------------------------------------------


def load_checkout_package(checkout: Path, head_sha: str) -> types.ModuleType:
    """Load the checkout directory itself as ``max2025_menu_prototype``.

    Main-thread only, synchronous, and host-free.  ``head_sha`` must already be
    verified as the actual full HEAD by a live caller; this seam performs path,
    namespace, schema, and SHA-format checks but no Git subprocesses.
    """

    _assert_main_thread()
    root = _checkout_root(checkout)
    sha = _normalize_sha(head_sha)
    session = _session_state()

    if session.restart_required:
        raise LauncherError(RESTART_MESSAGE, "restart_required")

    if session.checkout_root is not None:
        if not _same_path(session.checkout_root, root):
            session.restart_required = True
            _uninstall_trusted(session)
            raise LauncherError(DIFFERENT_CHECKOUT_MESSAGE, "stale_import")

    if session.loaded_head_sha is not None:
        if session.loaded_head_sha == sha:
            package = sys.modules.get(PACKAGE_NAME)
            if package is None:
                session.restart_required = True
                _uninstall_trusted(session)
                raise LauncherError(STALE_IMPORT_MESSAGE, "stale_import")
            _verify_loaded_alias(root)
            return package
        session.restart_required = True
        _uninstall_trusted(session)
        raise LauncherError(RESTART_MESSAGE, "restart_required")

    existing = _alias_modules()
    if existing:
        # Verify every stale entry so a genuinely foreign import is not
        # mistaken for a same-root module, then fail closed: an unstamped old
        # ZIP launch must never be adopted.
        session.restart_required = True
        for name, module in existing.items():
            _verify_module_path(name, module, root)
        raise LauncherError(STALE_IMPORT_MESSAGE, "stale_import")

    try:
        importlib.invalidate_caches()
        spec = importlib.util.spec_from_file_location(
            PACKAGE_NAME,
            root / "__init__.py",
            submodule_search_locations=[str(root)],
        )
        if spec is None or spec.loader is None:
            raise LauncherError(
                "Package initializer could not be loaded.", "partial_import"
            )
        package = importlib.util.module_from_spec(spec)
        sys.modules[PACKAGE_NAME] = package
        spec.loader.exec_module(package)
        bootstrap = importlib.import_module(PACKAGE_NAME + ".max_host.bootstrap")
        _verify_loaded_alias(root)
    except LauncherError:
        session.restart_required = True
        _uninstall_trusted(session)
        raise
    except BaseException as exc:
        session.restart_required = True
        _uninstall_trusted(session)
        raise LauncherError(PARTIAL_IMPORT_MESSAGE, "partial_import") from exc

    session.checkout_root = str(root)
    session.loaded_head_sha = sha
    session.bootstrap = bootstrap
    return package


# ---------------------------------------------------------------------------
# Asynchronous start and main-thread completion
# ---------------------------------------------------------------------------


def _enter_restart_required(session: types.ModuleType, message: str) -> None:
    session.restart_required = True
    _uninstall_trusted(session)
    _emit(message)


def _show_palette_checked(bootstrap: Any) -> None:
    """Open the V2 palette, printing one bounded diagnostic for errors."""

    try:
        bootstrap.show_palette()
    except LauncherError:
        raise
    except Exception as exc:
        _emit(_exception_detail(exc))
        raise


def _ensure_hotkey_checked(bootstrap: Any) -> None:
    """Install the default Tab listener on update completion; never fatal."""

    ensure = getattr(bootstrap, "ensure_hotkey", None)
    if not callable(ensure):
        return
    try:
        ensure()
    except Exception:
        pass


#: Backward-compatible private alias for callers/tests from the V1 launcher.
_show_tools_checked = _show_palette_checked


def _on_git_finished(result: dict[str, Any], checkout: Path) -> None:
    """Queued main-thread completion: import/show or stop without retrying."""

    _assert_main_thread()
    session = _session_state()
    session.in_flight = False
    session.bridge = None
    session.worker = None

    if not result.get("ok"):
        category = str(result.get("category", "internal"))
        message = str(result.get("message", GIT_FAILED_MESSAGE))
        if result.get("unreaped"):
            # A timed-out Git helper could not be confirmed stopped.  Keep the
            # session locked: a retry could race a live process mutating the
            # checkout.  The timeout message still reaches the caller.
            session.restart_required = True
            _uninstall_trusted(session)
        if category in ("restart_required", "stale_import", "partial_import"):
            _enter_restart_required(session, message)
        else:
            _emit(message)
        raise LauncherError(message, category)

    action = str(result.get("action", ""))
    after_sha = result.get("after_sha")
    try:
        sha = _normalize_sha(after_sha)
    except LauncherError:
        _enter_restart_required(session, RESTART_MESSAGE)
        raise LauncherError(RESTART_MESSAGE, "restart_required")

    if action == "noop":
        if session.loaded_head_sha != sha:
            _enter_restart_required(session, RESTART_MESSAGE)
            raise LauncherError(RESTART_MESSAGE, "restart_required")
        bootstrap = session.bootstrap
        if bootstrap is None:
            _enter_restart_required(session, STALE_IMPORT_MESSAGE)
            raise LauncherError(STALE_IMPORT_MESSAGE, "stale_import")
        _ensure_hotkey_checked(bootstrap)
        _show_palette_checked(bootstrap)
        _emit("Loaded commit %s." % sha)
        _emit_listener_tip()
        return

    if action not in ("clone", "handoff", "import-only", "pull-import"):
        _emit(GIT_FAILED_MESSAGE)
        raise LauncherError(GIT_FAILED_MESSAGE, "git_failed")

    try:
        package = load_checkout_package(checkout, sha)
    except LauncherError:
        raise
    except Exception as exc:
        _emit(_exception_detail(exc))
        raise
    bootstrap = getattr(session, "bootstrap", None) or sys.modules.get(
        PACKAGE_NAME + ".max_host.bootstrap"
    )
    if bootstrap is None:
        _enter_restart_required(session, STALE_IMPORT_MESSAGE)
        raise LauncherError(STALE_IMPORT_MESSAGE, "stale_import")
    _ensure_hotkey_checked(bootstrap)
    _show_palette_checked(bootstrap)
    _emit("Loaded commit %s from %s." % (sha, Path(package.__file__).parent))
    _emit_listener_tip()


def start(*, checkout: Path = DEFAULT_CHECKOUT) -> None:
    """Schedule one update/import operation and return promptly.

    Must be called on the Max main thread.  Duplicate invocations while a
    worker is in flight return the finite "already in progress" notice.
    """

    _assert_main_thread()
    session = _session_state()
    if session.restart_required:
        _emit(RESTART_MESSAGE)
        raise LauncherError(RESTART_MESSAGE, "restart_required")
    if session.in_flight:
        _emit(DUPLICATE_MESSAGE)
        return None

    root = _requested_root(checkout)
    # A missing or incomplete checkout is resolved in the worker, where clone
    # target collisions and layout failures have finite categories.
    if session.checkout_root is not None and not _same_path(session.checkout_root, root):
        _enter_restart_required(session, DIFFERENT_CHECKOUT_MESSAGE)
        raise LauncherError(DIFFERENT_CHECKOUT_MESSAGE, "stale_import")

    modules_loaded = bool(_alias_modules())
    expected_handoff = session.first_clone_head_sha
    session.first_clone_head_sha = None
    if expected_handoff is not None and (
        modules_loaded or session.loaded_head_sha is not None
    ):
        session.restart_required = True
        _uninstall_trusted(session)
        raise LauncherError(STALE_IMPORT_MESSAGE, "stale_import")

    session.in_flight = True
    bridge: Optional[_SignalBridge] = None
    worker: Any = None
    try:
        bridge = _bridge_factory()
        worker = _worker_factory(
            lambda: _worker_update(
                root,
                expected_handoff,
                session.loaded_head_sha,
                session.checkout_root,
                modules_loaded,
            ),
            bridge,
        )
        session.bridge = bridge
        session.worker = worker
        bridge.connect(lambda result: _on_git_finished(result, root))
        worker.start()
    except Exception:
        session.in_flight = False
        session.bridge = None
        session.worker = None
        if expected_handoff is not None:
            session.first_clone_head_sha = expected_handoff
        raise
    _emit("Checking %s for updates..." % root)
    return None


# ---------------------------------------------------------------------------
# Synchronous local hotkey load (no network, short bounded Git)
# ---------------------------------------------------------------------------


def _validate_local_action(value: Any) -> str:
    text = getattr(value, "value", value)
    if not isinstance(text, str) or text not in LOCAL_ACTIONS:
        raise LauncherError(
            "Unknown local action; use search, install_hotkey or a public V2 "
            "context.",
            "invalid_action",
        )
    return text


def _validate_local_context(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = getattr(value, "value", value)
    if not isinstance(text, str) or text not in LOCAL_CONTEXTS:
        raise LauncherError(
            "Unknown V2 context; use object, vertex, edge, border, face or element.",
            "invalid_context",
        )
    return text


def invoke_local(
    *,
    action: str = "search",
    context: Any = None,
    convert_selection: bool = False,
    checkout: Path = DEFAULT_CHECKOUT,
    _prevalidated_head: Optional[str] = None,
    _warm_verified_sha: Optional[str] = None,
) -> Any:
    """Load the trusted local checkout synchronously and open V2 UI.

    Main-thread only and deliberately network-free: it validates the local
    checkout with short bounded Git reads, obtains the actual full HEAD SHA,
    reuses the already-loaded package at the same root/SHA, and opens the
    palette or switches context.  It never clones, fetches, pulls, starts a
    worker, or prompts for credentials.

    ``_prevalidated_head`` lets the generated macro carry the HEAD it already
    proved so this seam does not repeat the same validation pass.
    ``_warm_verified_sha`` is the zero-Git path: the caller proved the on-disk
    HEAD still matches the imported session, so only in-memory checks run.
    """

    _assert_main_thread()
    local_action = _validate_local_action(action)
    selected_context = _validate_local_context(context)
    if local_action == "search":
        if selected_context is not None:
            raise LauncherError(
                "The search action does not accept a context.", "invalid_context"
            )
    elif local_action == "install_hotkey":
        if selected_context is not None:
            raise LauncherError(
                "The install_hotkey action does not accept a context.",
                "invalid_context",
            )
    else:
        if selected_context is not None and selected_context != local_action:
            raise LauncherError(
                "The action and context disagree.", "invalid_context"
            )
        selected_context = local_action
    if not isinstance(convert_selection, bool):
        raise LauncherError(
            "convert_selection must be a boolean.", "invalid_action"
        )

    session = _session_state()
    if session.restart_required:
        raise LauncherError(RESTART_MESSAGE, "restart_required")
    if session.in_flight:
        raise LauncherError(DUPLICATE_MESSAGE, "in_flight")

    root = _checkout_root(checkout)
    if _warm_verified_sha is not None:
        sha = _normalize_sha(_warm_verified_sha)
        if (
            session.checkout_root is None
            or not _same_path(session.checkout_root, root)
            or session.loaded_head_sha != sha
        ):
            raise LauncherError(STALE_IMPORT_MESSAGE, "stale_import")
    elif _prevalidated_head is not None:
        sha = _normalize_sha(_prevalidated_head)
    else:
        try:
            validated = _validate_checkout(root, timeout=LOCAL_LOAD_TIMEOUT)
        except LauncherError as exc:
            if exc.category in ("permission", "checkout_layout"):
                raise LauncherError(LOCAL_LOAD_MESSAGE, "local_unavailable") from exc
            raise
        sha = validated["head"]

    if session.checkout_root is not None and not _same_path(session.checkout_root, root):
        session.restart_required = True
        _uninstall_trusted(session)
        raise LauncherError(DIFFERENT_CHECKOUT_MESSAGE, "stale_import")
    if session.loaded_head_sha is not None and session.loaded_head_sha != sha:
        session.restart_required = True
        _uninstall_trusted(session)
        raise LauncherError(LOCAL_CHANGED_MESSAGE, "restart_required")

    load_checkout_package(root, sha)
    bootstrap = getattr(session, "bootstrap", None) or sys.modules.get(
        PACKAGE_NAME + ".max_host.bootstrap"
    )
    if bootstrap is None or not callable(getattr(bootstrap, "show_palette", None)):
        _enter_restart_required(session, STALE_IMPORT_MESSAGE)
        raise LauncherError(STALE_IMPORT_MESSAGE, "stale_import")

    ensure = getattr(bootstrap, "ensure_hotkey", None)
    installed = None
    if callable(ensure):
        try:
            installed = ensure()
        except Exception:
            installed = None
    if local_action == "install_hotkey":
        installer = getattr(bootstrap, "install_tab_hotkey", None)
        if not callable(installer):
            raise LauncherError(
                "The loaded 3dsModeler package cannot install the Tab hotkey. "
                "Restart 3ds Max and retry.",
                "install_hotkey",
            )
        return installer()

    if local_action == "search":
        return bootstrap.show_palette()
    return bootstrap.switch_context(
        selected_context, convert_selection=convert_selection
    )


# ---------------------------------------------------------------------------
# Persistent MacroScript installation
# ---------------------------------------------------------------------------


def _default_user_macros_dir() -> Optional[Path]:
    """Resolve Max's documented user MacroScripts directory lazily."""

    try:
        from pymxs import runtime as rt  # type: ignore[import-not-found]

        value = rt.execute("getDir #userMacros")
    except Exception:
        return None
    text = str(value).strip()
    if not text or text.lower() in ("undefined", "none", "null"):
        return None
    return Path(text)


def _default_user_startup_dir() -> Optional[Path]:
    """Resolve Max's documented user startup-script directory lazily."""

    try:
        from pymxs import runtime as rt  # type: ignore[import-not-found]

        value = rt.execute("getDir #userStartupScripts")
    except Exception:
        return None
    text = str(value).strip()
    if not text or text.lower() in ("undefined", "none", "null"):
        return None
    return Path(text)


def _write_owned_file(
    directory: Path,
    name: str,
    data: str,
    header: str,
    conflict_message: str,
    write_message: str,
) -> Path:
    """Atomically write one managed file without ever overwriting a foreign one."""

    import os
    import tempfile

    if directory.exists() and not directory.is_dir():
        raise LauncherError(write_message, "macro_write")
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise LauncherError(write_message, "macro_write") from exc

    path = directory / name
    if path.exists():
        try:
            existing = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise LauncherError(write_message, "macro_write") from exc
        if not existing.startswith(header):
            raise LauncherError(conflict_message, "macro_conflict")
        if existing == data:
            return path
    temporary = None
    try:
        handle = tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            dir=str(directory),
            prefix=".3dsModeler-",
            suffix=".tmp",
            delete=False,
        )
        temporary = Path(handle.name)
        with handle:
            handle.write(data)
        os.replace(str(temporary), str(path))
    except OSError as exc:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass
        raise LauncherError(write_message, "macro_write") from exc
    return path


def _local_loader_source(
    *,
    action: str,
    context: Optional[str],
    convert_selection: bool,
    checkout: Path,
    catch_errors: bool = True,
) -> str:
    """Return the fixed, self-contained Python body of one macro shim.

    The body validates the local checkout with stdlib/local Git, imports the
    companion by absolute path under a stable registered module name, and then
    calls ``invoke_local``.  It never uses the network or the ``.ms`` file.

    A warm session (the same checkout already imported in this Max session at
    an unchanged HEAD) is proven from ``.git`` files alone, so repeated hotkey
    presses spawn no ``git.exe`` processes.  ``catch_errors`` keeps an expected
    failure as one clean Listener line instead of an unhandled MAXScript
    traceback dialog; the raising ``_entry`` helper stays testable.
    """

    return '''\
import os
import sys
import threading
import types
from pathlib import Path

ROOT = Path(%(root)r)
PACKAGE_NAME = %(package)r
BRANCH = %(branch)r
EXPECTED_REMOTE = %(remote)r
COMPANION_MODULE = "_max2025_pull_and_load_companion"
SESSION_MODULE_NAME = %(session)r
COMPANION_PATH = ROOT / "tools" / "max_pull_and_load.py"
TIMEOUT = %(timeout)r
ACTION = %(action)r
CONTEXT = %(context)r
CONVERT_SELECTION = %(convert)r
CATCH_ERRORS = %(catch)r
OVERLAY_NAME = "user_aliases.json"


def _fail(message):
    raise RuntimeError(message)


def _is_sha(value):
    return len(value) in (40, 64) and all(
        ch in "0123456789abcdef" for ch in value
    )


def _under(path, root):
    try:
        left = os.path.normcase(str(Path(path).resolve()))
        right = os.path.normcase(str(Path(root).resolve()))
        Path(left).relative_to(right)
    except (OSError, TypeError, ValueError):
        return False
    return True


def _same(left, right):
    try:
        return os.path.normcase(str(Path(left).resolve())) == os.path.normcase(
            str(Path(right).resolve())
        )
    except (OSError, TypeError, ValueError):
        return False


def _git(args):
    import os
    import subprocess

    environment = dict(os.environ)
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["GCM_INTERACTIVE"] = "Never"
    environment.pop("GIT_ASKPASS", None)
    environment.pop("SSH_ASKPASS", None)
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(ROOT),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            check=False,
            timeout=TIMEOUT,
            env=environment,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if os.name == "nt" else 0,
        )
    except FileNotFoundError:
        _fail(%(missing_git)r)
    except subprocess.TimeoutExpired:
        _fail("Local Git did not finish in time. No retry was attempted.")
    if completed.returncode != 0:
        _fail("The local checkout could not be validated. Repair it outside Max.")
    return completed.stdout.decode("utf-8", "replace").strip()


def _normalized_remote(value):
    text = (value or "").strip().replace("\\\\", "/").rstrip("/").lower()
    if text.endswith(".git"):
        text = text[:-4]
    return text


def _dirty_lines(value):
    lines = []
    for line in (value or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split(None, 1)
        path = parts[1].strip() if len(parts) > 1 else ""
        if path == OVERLAY_NAME:
            continue
        lines.append(stripped)
    return lines


def _head_from_files():
    """Read the checked-out dev HEAD without spawning ``git.exe``.

    Returns ``None`` when the answer cannot be proven from plain files (for
    example packed refs), letting the caller fall back to one bounded read.
    """

    git_dir = ROOT / ".git"
    if git_dir.is_symlink() or not git_dir.is_dir():
        return None
    try:
        head = (git_dir / "HEAD").read_text(
            encoding="utf-8", errors="replace"
        ).strip()
    except OSError:
        return None
    if head.startswith("ref:"):
        ref = head[4:].strip()
        if ref != "refs/heads/" + BRANCH:
            return None
        try:
            value = (git_dir / "refs" / "heads" / BRANCH).read_text(
                encoding="utf-8", errors="replace"
            ).strip().lower()
        except OSError:
            value = ""
        if _is_sha(value):
            return value
        try:
            packed = (git_dir / "packed-refs").read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError:
            return None
        for line in packed.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("^"):
                continue
            parts = stripped.split(None, 1)
            if len(parts) != 2 or parts[1].strip() != ref:
                continue
            candidate = parts[0].strip().lower()
            if _is_sha(candidate):
                return candidate
        return None
    value = head.lower()
    return value if _is_sha(value) else None


def _warm_session():
    """Return ``(companion, head)`` when this session already loaded this HEAD.

    Warm means the session stamp is trustworthy, the checkout root is the
    expected one, the companion is the module we would load anyway, and the
    on-disk HEAD still equals the imported commit.  All checks are file reads;
    the single ``git`` fallback exists only for packed or detached HEADs.
    """

    if threading.current_thread() is not threading.main_thread():
        return None
    module = sys.modules.get(COMPANION_MODULE)
    session = sys.modules.get(SESSION_MODULE_NAME)
    if module is None or session is None:
        return None
    if not callable(getattr(module, "invoke_local", None)):
        return None
    if not _same(getattr(module, "__file__", None), COMPANION_PATH):
        return None
    loaded = getattr(session, "loaded_head_sha", None)
    if not isinstance(loaded, str):
        return None
    loaded = loaded.strip().lower()
    if not _is_sha(loaded):
        return None
    if getattr(session, "restart_required", True):
        return None
    if getattr(session, "in_flight", True):
        return None
    if not _same(getattr(session, "checkout_root", None), ROOT):
        return None
    head = _head_from_files()
    if head is None:
        try:
            head = _git(["rev-parse", "HEAD"]).lower()
        except RuntimeError:
            return None
    if head != loaded:
        return None
    return module, head


def _validate():
    if threading.current_thread() is not threading.main_thread():
        _fail("The 3dsModeler macro must run on the 3ds Max main thread.")
    if ROOT.is_symlink() or not ROOT.is_dir():
        _fail(%(local_load)r)
    for relative in %(required)r:
        if not (ROOT / relative).exists():
            _fail("The local 3dsModeler checkout is incomplete. Run max_pull_and_load.ms once.")
    if not _same(_git(["rev-parse", "--show-toplevel"]), ROOT):
        _fail("The Git top-level is not the 3dsModeler checkout. Repair it outside Max.")
    if _git(["rev-parse", "--is-bare-repository"]).lower() == "true":
        _fail("The local checkout is a bare repository. Repair it outside Max.")
    try:
        branch = _git(["symbolic-ref", "--quiet", "--short", "HEAD"])
    except RuntimeError:
        _fail("The local checkout is not on the dev branch. Repair it outside Max.")
    if branch != BRANCH:
        _fail("The local checkout is not on the dev branch. Repair it outside Max.")
    if _normalized_remote(_git(["remote", "get-url", "origin"])) != EXPECTED_REMOTE:
        _fail("The local checkout origin is not the expected repository. Repair it outside Max.")
    git_dir = ROOT / ".git"
    for marker in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "BISECT_LOG"):
        if (git_dir / marker).exists():
            _fail("A Git operation is in progress. Finish or abort it outside Max.")
    if (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists():
        _fail("A Git operation is in progress. Finish or abort it outside Max.")
    if _dirty_lines(_git(["status", "--porcelain=v1", "--untracked-files=all"])):
        _fail(%(dirty)r)
    head = _git(["rev-parse", "HEAD"]).lower()
    if len(head) not in (40, 64) or any(ch not in "0123456789abcdef" for ch in head):
        _fail("The local checkout did not report a full commit SHA. Retry after repair.")
    return head


def _load_companion():
    path = ROOT / "tools" / "max_pull_and_load.py"
    if path.is_symlink() or not path.is_file():
        _fail(%(companion_missing)r)
    for name, module in list(sys.modules.items()):
        if name == PACKAGE_NAME or name.startswith(PACKAGE_NAME + "."):
            origin = getattr(module, "__file__", None)
            if not origin or not _under(origin, ROOT):
                _fail("A different 3dsModeler checkout is already loaded. Restart 3ds Max.")
    module = sys.modules.get(COMPANION_MODULE)
    if module is not None:
        origin = getattr(module, "__file__", None)
        if not origin or not _same(origin, path):
            _fail("A different launcher companion is already registered. Restart 3ds Max.")
        return module
    try:
        data = path.read_bytes()
    except OSError:
        _fail(%(companion_missing)r)
    if len(data) > 4 * 1024 * 1024:
        _fail("The launcher companion is unexpectedly large. Restart 3ds Max and reinstall.")
    module = types.ModuleType(COMPANION_MODULE)
    module.__file__ = str(path)
    module.__package__ = ""
    sys.modules[COMPANION_MODULE] = module
    try:
        exec(compile(data, str(path), "exec"), module.__dict__)
    except BaseException:
        sys.modules.pop(COMPANION_MODULE, None)
        raise
    return module


def _entry():
    warm = _warm_session()
    if warm is not None:
        module, head = warm
        module.invoke_local(
            action=ACTION,
            context=CONTEXT,
            convert_selection=CONVERT_SELECTION,
            checkout=ROOT,
            _warm_verified_sha=head,
        )
        return
    head = _validate()
    module = _load_companion()
    invoke_local = getattr(module, "invoke_local", None)
    if not callable(invoke_local):
        _fail("The launcher companion is incomplete. Restart 3ds Max and reinstall.")
    invoke_local(
        action=ACTION,
        context=CONTEXT,
        convert_selection=CONVERT_SELECTION,
        checkout=ROOT,
        _prevalidated_head=head,
    )


def _clean_message(exc):
    text = str(exc).strip()
    if not text:
        text = exc.__class__.__name__
    return text.splitlines()[0]


def _run():
    if CATCH_ERRORS:
        try:
            _entry()
        except Exception as exc:
            print("[3dsModeler] " + _clean_message(exc), flush=True)
        return
    _entry()


_run()
''' % {
        "root": str(checkout),
        "package": PACKAGE_NAME,
        "branch": BRANCH,
        "remote": _normalize_remote(REPO_URL),
        "session": SESSION_MODULE_NAME,
        "timeout": LOCAL_LOAD_TIMEOUT,
        "action": action,
        "context": context,
        "convert": bool(convert_selection),
        "catch": bool(catch_errors),
        "required": REQUIRED_ROOT_ENTRIES,
        "missing_git": MISSING_GIT_MESSAGE,
        "local_load": LOCAL_LOAD_MESSAGE,
        "dirty": DIRTY_MESSAGE,
        "companion_missing": (
            "The launcher companion is missing from the local checkout. "
            "Run max_pull_and_load.ms once to install or update it."
        ),
    }


def render_macro_source(*, checkout: Path = DEFAULT_CHECKOUT) -> str:
    """Render the deterministic managed MacroScript file.

    Every shim is self-contained; this text is written to the Max user
    MacroScripts directory and survives restarts independently of UI memory.
    """

    import base64 as _base64

    lines = [MACRO_FILE_HEADER]
    entries = [
        (
            MACRO_SEARCH_NAME,
            "3dsModeler Search",
            "Search 3dsModeler maneuvers",
            "search",
            None,
            False,
        )
    ]
    for context, name in MACRO_CONTEXT_NAMES.items():
        entries.append(
            (
                name,
                "%s context" % context.title(),
                "3dsModeler: switch to the %s context" % context.title(),
                context,
                context,
                False,
            )
        )
    for context, name in MACRO_CONVERT_NAMES.items():
        entries.append(
            (
                name,
                "%s (convert)" % context.title(),
                "3dsModeler: convert the selection to the %s context"
                % context.title(),
                context,
                context,
                True,
            )
        )
    for name, button, tooltip, action, context, convert in entries:
        body = _local_loader_source(
            action=action,
            context=context,
            convert_selection=convert,
            checkout=checkout,
        )
        encoded = _base64.b64encode(body.encode("utf-8")).decode("ascii")
        slug = name.replace("ThreeDsModeler_", "").lower() or "macro"
        lines.append(
            "macroScript %s\n"
            'category:"%s"\n'
            'buttonText:"%s"\n'
            'tooltip:"%s"\n'
            "(\n"
            "  python.execute (\"import base64; exec(compile("
            "base64.b64decode('%s'), '<3dsModeler-macro-%s>', 'exec'))\")\n"
            ")\n"
            % (name, MACRO_CATEGORY, button, tooltip, encoded, slug)
        )
    return "\n".join(lines)


def render_startup_source(*, checkout: Path = DEFAULT_CHECKOUT) -> str:
    """Render the owned Max startup script that registers the Tab listener.

    The script is local-only: it validates/loads the existing checkout and
    calls ``invoke_local(action="install_hotkey")``.  It opens no window, makes
    no network request, and is never loaded by ``uninstall()``.
    """

    import base64 as _base64

    body = _local_loader_source(
        action="install_hotkey",
        context=None,
        convert_selection=False,
        checkout=checkout,
    )
    encoded = _base64.b64encode(body.encode("utf-8")).decode("ascii")
    return (
        STARTUP_FILE_HEADER
        + "-- Registers the default Tab hotkey for this Max session.\n"
        + "python.execute (\"import base64; exec(compile("
        + "base64.b64decode('%s'), '<3dsModeler-startup>', 'exec'))\")\n"
        % encoded
    )


def _activate_macros(path: Path, directory: Path) -> bool:
    """Register a freshly written ``.mcr`` in the live Max session.

    ``register_macros`` runs on Max's main thread inside a real session, so
    loading the owned file immediately makes the ``3dsModeler`` category
    visible in Customize > Hotkey Editor without a restart.  Outside Max (or on
    a worker thread) this is a no-op; a missing ``pymxs`` is not an error.
    """

    if threading.current_thread() is not threading.main_thread():
        return False
    try:
        from pymxs import runtime as rt  # type: ignore[import-not-found]
    except Exception:
        return False
    try:
        rt.fileIn(str(path))
        return True
    except Exception:
        pass
    try:
        rt.macros.load(str(directory))
        return True
    except Exception:
        return False


def register_macros(
    *, target_dir: Path | str | None = None, checkout: Path = DEFAULT_CHECKOUT
) -> Path:
    """Create/reuse the managed ``3dsModeler.mcr`` in Max's user macros.

    An existing file without this launcher's ownership header is never
    overwritten.  The operation is local, synchronous, and independent of Max
    UI lifetime; ``uninstall`` never removes the resulting definitions.
    """

    directory = Path(target_dir) if target_dir is not None else _default_user_macros_dir()
    if directory is None:
        raise LauncherError(MACRO_DIR_MESSAGE, "macro_dir")

    data = render_macro_source(checkout=checkout)
    path = _write_owned_file(
        directory,
        MACRO_FILE_NAME,
        data,
        MACRO_FILE_HEADER,
        MACRO_CONFLICT_MESSAGE,
        MACRO_WRITE_MESSAGE,
    )
    _activate_macros(path, directory)

    startup_directory = _default_user_startup_dir()
    if startup_directory is not None:
        startup_data = render_startup_source(checkout=checkout)
        _write_owned_file(
            startup_directory,
            STARTUP_FILE_NAME,
            startup_data,
            STARTUP_FILE_HEADER,
            STARTUP_CONFLICT_MESSAGE,
            STARTUP_WRITE_MESSAGE,
        )
    return path


# ---------------------------------------------------------------------------
# Embedded standalone .ms bootstrap (base64 to keep MAXScript quoting safe)
# ---------------------------------------------------------------------------

_EMBEDDED_PYTHON_TEMPLATE = r'''
import base64
import hashlib
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import zlib
from pathlib import Path

PACKAGE_NAME = "max2025_menu_prototype"
REPO_URL = "https://github.com/nekirk11/3dsModeler.git"
BRANCH = "dev"
CHECKOUT = Path(r"C:\dev\3dsModeler")
SESSION_MODULE_NAME = "_max2025_pull_and_load_session"
SESSION_SCHEMA_VERSION = 1
LOCAL_GIT_TIMEOUT = 15.0
NETWORK_GIT_TIMEOUT = 120.0
MAX_MACROS_SOURCE_B64 = "__MACRO_SOURCE_B64__"
STARTUP_SOURCE_B64 = "__STARTUP_SOURCE_B64__"
MACRO_FILE_NAME = "3dsModeler.mcr"
MACRO_FILE_HEADER = (
    "-- 3dsModeler macro definitions (managed by the 3dsModeler launcher; "
    "do not edit)\n"
)
STARTUP_FILE_NAME = "3dsModeler_startup.ms"
STARTUP_FILE_HEADER = (
    "-- 3dsModeler startup hotkey (managed by the 3dsModeler launcher; "
    "do not edit)\n"
)
_KILL_WAIT = 5.0
_REAP_POLL = 0.05
_MAX_CAPTURED_BYTES = 65536
_MAX_COMPANION_BYTES = 4 * 1024 * 1024
RESTART_MESSAGE = "Checkout updated; restart 3ds Max before loading this commit."
NO_QT_MESSAGE = "Start 3ds Max 2025 before running the launcher; its Qt application was not found."
MAIN_THREAD_MESSAGE = "The launcher must run on the 3ds Max main thread."
DUPLICATE_MESSAGE = "An update is already in progress."
LISTENER_TIP = (
    "MAXScript tab diagnostics: python.Execute "
    '"from max2025_menu_prototype.max_host import bootstrap; '
    'bootstrap.report_diagnostics()"'
)
MISSING_GIT_MESSAGE = "Git is not on PATH. Install Git for Windows outside Max."
TARGET_EXISTS_MESSAGE = "The checkout path already exists but is not a validated checkout. Repair or move it outside Max, then retry."
PARTIAL_CLONE_MESSAGE = "The clone did not finish. The partial directory was left in place for manual repair; nothing was deleted."
AUTH_MESSAGE = "Git could not access the repository. Configure noninteractive credentials outside Max and retry."
NETWORK_MESSAGE = "Git could not reach the repository. Check the network and retry."
GIT_FAILED_MESSAGE = "Git reported a failure. Repair the checkout outside Max."
TIMEOUT_MESSAGE = "Git did not finish in time. No retry was attempted."
LAYOUT_MESSAGE = "The checkout layout is not a complete prototype package. Repair it outside Max."
DIRTY_MESSAGE = "The checkout has uncommitted changes. Commit or stash them outside Max, then retry."
BRANCH_MESSAGE = "The checkout is not on the dev branch. Repair it outside Max."
ORIGIN_MESSAGE = "The checkout origin is not the expected HTTPS repository. Repair it outside Max."
TOP_LEVEL_MESSAGE = "The Git top-level is not the checkout directory. Repair it outside Max."
GIT_OPERATION_MESSAGE = "A Git merge, rebase, cherry-pick, or sequencer operation is in progress. Finish or abort it outside Max."
DIVERGED_MESSAGE = "The local dev branch is ahead of or diverged from origin/dev. Refusing to discard local commits."
COMPANION_MESSAGE = "The launcher companion is missing from the checkout. Repair it outside Max."
COMPANION_CHANGED_MESSAGE = "The launcher companion changed after validation. Restart 3ds Max before retrying."
COMPANION_FAILED_MESSAGE = "The launcher companion failed to start. Restart 3ds Max and retry."
MACRO_CONFLICT_MESSAGE = (
    "A file named 3dsModeler.mcr already exists in the Max MacroScripts folder "
    "and was not created by this launcher. Move or rename it outside Max, then "
    "run max_pull_and_load.ms again."
)
MACRO_WRITE_MESSAGE = (
    "The 3dsModeler MacroScript file could not be written. Check that the Max "
    "user MacroScripts folder is writable, then run max_pull_and_load.ms again."
)
MACRO_DIR_MESSAGE = (
    "The Max user MacroScripts folder could not be resolved. Run "
    "max_pull_and_load.ms inside 3ds Max 2025, then retry."
)
STARTUP_CONFLICT_MESSAGE = (
    "A file named 3dsModeler_startup.ms already exists in the Max startup "
    "folder and was not created by this launcher. Move or rename it outside "
    "Max, then run max_pull_and_load.ms again."
)
STARTUP_WRITE_MESSAGE = (
    "The 3dsModeler startup script could not be written. Check that the Max "
    "user startup folder is writable, then run max_pull_and_load.ms again."
)
STARTUP_DIR_MESSAGE = (
    "The Max user startup folder could not be resolved. Run "
    "max_pull_and_load.ms inside 3ds Max 2025, then retry."
)

_REQUIRED = ("__init__.py", "core", "max_host", "qt_ui", "core/default_profile.json")
_HEX = "0123456789abcdefABCDEF"
_REAP_STATE = {"unreaped": False}


def _emit(message):
    print(message, flush=True)


def _is_empty_directory(path):
    try:
        if not path.is_dir() or path.is_symlink():
            return False
        return not any(path.iterdir())
    except OSError:
        return False


def _sanitize_entry_name(name):
    cleaned = "".join(
        ch for ch in name if ch.isprintable() and ch not in "\r\n\t"
    )
    return cleaned[:40] or "?"


def _not_a_checkout_message():
    try:
        names = sorted(child.name for child in CHECKOUT.iterdir())
    except OSError:
        names = []
    shown = ", ".join(_sanitize_entry_name(name) for name in names[:3])
    if len(names) > 3:
        shown += ", ..."
    return (
        "%s already exists and is not a Git checkout (found: %s). Save "
        "max_pull_and_load.ms outside %s (for example %s) and leave %s empty "
        "or absent, then retry."
        % (
            CHECKOUT,
            shown,
            CHECKOUT,
            CHECKOUT.parent / "max_pull_and_load.ms",
            CHECKOUT,
        )
    )


def _emit_listener_tip():
    _emit(LISTENER_TIP)


def _user_macros_directory():
    try:
        from pymxs import runtime as rt
        value = str(rt.execute("getDir #userMacros")).strip()
    except Exception:
        return None
    if not value or value.lower() in ("undefined", "none", "null"):
        return None
    return Path(value)


def _user_startup_directory():
    try:
        from pymxs import runtime as rt
        value = str(rt.execute("getDir #userStartupScripts")).strip()
    except Exception:
        return None
    if not value or value.lower() in ("undefined", "none", "null"):
        return None
    return Path(value)


def _activate_macros(path, directory):
    """Register the written MacroScripts in the live Max session if possible."""

    if threading.current_thread() is not threading.main_thread():
        return False
    try:
        from pymxs import runtime as rt
    except Exception:
        return False
    try:
        rt.fileIn(str(path))
        return True
    except Exception:
        pass
    try:
        rt.macros.load(str(directory))
        return True
    except Exception:
        return False


def _install_macros():
    """Write the managed persistent macro file before starting the update."""

    directory = _user_macros_directory()
    if directory is None:
        _emit(MACRO_DIR_MESSAGE)
        return False
    try:
        data = zlib.decompress(base64.b64decode(MAX_MACROS_SOURCE_B64)).decode("utf-8")
    except Exception:
        _emit(MACRO_WRITE_MESSAGE)
        return False
    path = directory / MACRO_FILE_NAME
    try:
        if path.exists():
            existing = path.read_text(encoding="utf-8")
            if not existing.startswith(MACRO_FILE_HEADER):
                _emit(MACRO_CONFLICT_MESSAGE)
                return False
            if existing == data:
                _activate_macros(path, directory)
                _install_startup()
                return True
        directory.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(".3dsModeler-macros.tmp")
        temporary.write_text(data, encoding="utf-8", newline="\n")
        os.replace(str(temporary), str(path))
        _activate_macros(path, directory)
        _install_startup()
        return True
    except OSError:
        _emit(MACRO_WRITE_MESSAGE)
        return False


def _install_startup():
    """Write the owned startup Tab listener without activating it now."""

    directory = _user_startup_directory()
    if directory is None:
        _emit(STARTUP_DIR_MESSAGE)
        return False
    try:
        data = zlib.decompress(base64.b64decode(STARTUP_SOURCE_B64)).decode("utf-8")
    except Exception:
        _emit(STARTUP_WRITE_MESSAGE)
        return False
    path = directory / STARTUP_FILE_NAME
    try:
        if path.exists():
            existing = path.read_text(encoding="utf-8")
            if not existing.startswith(STARTUP_FILE_HEADER):
                _emit(STARTUP_CONFLICT_MESSAGE)
                return False
            if existing == data:
                return True
        directory.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(".3dsModeler-startup.tmp")
        temporary.write_text(data, encoding="utf-8", newline="\n")
        os.replace(str(temporary), str(path))
        return True
    except OSError:
        _emit(STARTUP_WRITE_MESSAGE)
        return False


def _companion_failed_message(exc):
    """Return a bounded, ASCII-clean companion failure for the Listener."""

    try:
        name = "".join(
            ch for ch in type(exc).__name__
            if ch.isascii() and (ch.isalnum() or ch == "_")
        )[:48] or "Exception"
    except Exception:
        name = "Exception"
    try:
        text = str(exc)
    except Exception:
        text = ""
    cleaned = "".join(
        ch if ch.isascii() and ch.isprintable() and ch not in "\r\n\t" else " "
        for ch in text
    )
    cleaned = " ".join(cleaned.split())[:160].rstrip()
    if cleaned:
        return (
            "The launcher companion failed to start (%s: %s). "
            "Restart 3ds Max and retry." % (name, cleaned)
        )
    return (
        "The launcher companion failed to start (%s). Restart 3ds Max and retry."
        % name
    )


def _session():
    module = sys.modules.get(SESSION_MODULE_NAME)
    if module is None:
        module = type(sys)(SESSION_MODULE_NAME)
        module.schema_version = SESSION_SCHEMA_VERSION
        module.checkout_root = None
        module.loaded_head_sha = None
        module.restart_required = False
        module.in_flight = False
        module.first_clone_head_sha = None
        module.bootstrap = None
        module.bridge = None
        module.worker = None
        sys.modules[SESSION_MODULE_NAME] = module
        return module
    if getattr(module, "schema_version", None) != SESSION_SCHEMA_VERSION:
        raise RuntimeError("The launcher session marker uses an unknown schema. Restart 3ds Max.")
    for field in ("checkout_root", "loaded_head_sha", "restart_required",
                  "in_flight", "first_clone_head_sha", "bootstrap", "bridge", "worker"):
        if not hasattr(module, field):
            raise RuntimeError("The launcher session marker is incomplete. Restart 3ds Max.")
    return module


def _alias_loaded():
    return any(
        name == PACKAGE_NAME or name.startswith(PACKAGE_NAME + ".")
        for name in list(sys.modules)
    )


def _is_windows():
    return os.name == "nt"


def _creation_flags():
    if _is_windows():
        return int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
    return 0


def _process_kwargs():
    kwargs = {"creationflags": _creation_flags()}
    if not _is_windows():
        kwargs["start_new_session"] = True
    return kwargs


def _environment():
    environment = dict(os.environ)
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["GCM_INTERACTIVE"] = "Never"
    environment.pop("GIT_ASKPASS", None)
    environment.pop("SSH_ASKPASS", None)
    return environment


def _bounded(data):
    if not data:
        return ""
    return data[:_MAX_CAPTURED_BYTES].decode("utf-8", "replace")


class _GitFailure(Exception):
    def __init__(self, message, category):
        Exception.__init__(self, message)
        self.message = message
        self.category = category


class _GitTimeout(Exception):
    def __init__(self, reaped):
        Exception.__init__(self, TIMEOUT_MESSAGE)
        self.reaped = reaped


class _CompanionChanged(Exception):
    pass


def _note_timeout_reaped(reaped):
    if not reaped:
        _REAP_STATE["unreaped"] = True


def _reap(process):
    try:
        process.communicate(timeout=_KILL_WAIT)
        return True
    except subprocess.TimeoutExpired:
        pass
    except Exception:
        return process.poll() is not None
    for stream in (getattr(process, "stdout", None), getattr(process, "stderr", None)):
        if stream is None:
            continue
        try:
            stream.close()
        except Exception:
            pass
    try:
        process.wait(timeout=_KILL_WAIT)
        return True
    except subprocess.TimeoutExpired:
        return False
    except Exception:
        return process.poll() is not None


def _wait_group_gone(pgid):
    deadline = time.monotonic() + _KILL_WAIT
    while True:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return True
        except OSError:
            return False
        if time.monotonic() >= deadline:
            return False
        time.sleep(_REAP_POLL)


def _terminate_tree(process):
    pid = getattr(process, "pid", None)
    windows = _is_windows()
    tree_confirmed = False
    pgid = None
    if pid is None:
        tree_confirmed = False
    elif windows:
        try:
            completed = subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                creationflags=_creation_flags(),
                timeout=_KILL_WAIT,
                check=False,
            )
            tree_confirmed = getattr(completed, "returncode", None) == 0
        except Exception:
            tree_confirmed = False
    else:
        # ``start_new_session=True`` makes the child its own session and
        # process-group leader, so the group id is exactly its pid.  Using the
        # pid directly also reaches helpers after the leader has exited, when
        # ``getpgid`` would raise and the group could never be polled.
        pgid = pid
        try:
            os.killpg(pgid, signal.SIGKILL)
            tree_confirmed = True
        except ProcessLookupError:
            tree_confirmed = True
        except Exception:
            tree_confirmed = False
    try:
        process.kill()
    except Exception:
        pass
    reaped = _reap(process)
    if not windows and pgid is not None and tree_confirmed:
        tree_confirmed = _wait_group_gone(pgid)
    return bool(reaped and tree_confirmed)


def _spawn(args, cwd):
    executable = shutil.which("git")
    if not executable:
        raise _GitFailure(MISSING_GIT_MESSAGE, "missing_git")
    try:
        return subprocess.Popen(
            [executable, *[str(item) for item in args]],
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            env=_environment(),
            **_process_kwargs(),
        )
    except OSError:
        raise _GitFailure(GIT_FAILED_MESSAGE, "git_failed") from None


def _capture(process, timeout):
    try:
        return process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        reaped = _terminate_tree(process)
        _note_timeout_reaped(reaped)
        raise _GitTimeout(reaped) from None
    except OSError:
        raise _GitFailure(GIT_FAILED_MESSAGE, "git_failed") from None


def _run_git(args, cwd, timeout):
    process = _spawn(args, cwd)
    stdout, stderr = _capture(process, timeout)
    return (process.returncode, _bounded(stdout), _bounded(stderr))


def _run_git_bytes(args, cwd, timeout):
    process = _spawn(args, cwd)
    stdout, stderr = _capture(process, timeout)
    return (process.returncode, stdout or b"", _bounded(stderr))


def _classify(stderr, stdout):
    text = (stderr + "\n" + stdout).lower()
    for marker in ("authentication failed", "could not read username",
                   "could not read password", "permission denied",
                   "repository not found", "not found", "access denied", "403"):
        if marker in text:
            return AUTH_MESSAGE, "auth"
    for marker in ("could not resolve host", "failed to connect",
                   "connection timed out", "connection refused",
                   "network is unreachable", "unable to access", "ssl", "tls",
                   "rpc failed"):
        if marker in text:
            return NETWORK_MESSAGE, "network"
    return GIT_FAILED_MESSAGE, "git_failed"


def _git_stdout(args, cwd, timeout=LOCAL_GIT_TIMEOUT):
    code, stdout, stderr = _run_git(args, cwd, timeout)
    if code != 0:
        message, category = _classify(stderr, stdout)
        raise _GitFailure(message, category)
    return stdout.strip()


def _same_path(left, right):
    try:
        left_text = os.path.normcase(str(Path(left).resolve()))
        right_text = os.path.normcase(str(Path(right).resolve()))
    except OSError:
        return False
    return left_text == right_text


def _normalize_remote(url):
    text = (url or "").strip().rstrip("/")
    if text.lower().endswith(".git"):
        text = text[:-4]
    return text.rstrip("/").lower()


_EXPECTED_REMOTE = _normalize_remote(REPO_URL)


def _full_sha(value):
    text = (value or "").strip()
    if len(text) not in (40, 64) or any(ch not in _HEX for ch in text):
        raise _GitFailure(
            "The checkout HEAD is not a full commit identifier. Repair it outside Max.",
            "checkout_layout",
        )
    return text.lower()


def _operation_in_progress(root):
    git_dir = Path(_git_stdout(["rev-parse", "--absolute-git-dir"], root))
    for name in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD",
                 "rebase-merge", "rebase-apply", "sequencer"):
        if (git_dir / name).exists():
            return True
    return False


def _inside(path, root):
    try:
        return Path(path).resolve().is_relative_to(root.resolve())
    except (OSError, ValueError):
        return False


def _layout(root):
    for relative in _REQUIRED:
        candidate = root / relative
        if not candidate.exists():
            raise _GitFailure(LAYOUT_MESSAGE, "checkout_layout")
        if candidate.is_symlink() and not _inside(candidate.resolve(), root):
            raise _GitFailure(LAYOUT_MESSAGE, "checkout_layout")


def _validate_checkout():
    if CHECKOUT.is_symlink():
        raise _GitFailure(LAYOUT_MESSAGE, "checkout_layout")
    try:
        root = CHECKOUT.resolve()
    except OSError:
        raise _GitFailure(LAYOUT_MESSAGE, "checkout_layout") from None
    if not root.is_dir():
        raise _GitFailure(LAYOUT_MESSAGE, "checkout_layout")
    _layout(root)
    top_level = _git_stdout(["rev-parse", "--show-toplevel"], root)
    if not _same_path(top_level, root):
        raise _GitFailure(TOP_LEVEL_MESSAGE, "wrong_top_level")
    if _git_stdout(["rev-parse", "--is-bare-repository"], root).lower() == "true":
        raise _GitFailure(LAYOUT_MESSAGE, "checkout_layout")
    code, stdout, _stderr = _run_git(
        ["symbolic-ref", "--quiet", "--short", "HEAD"], root, LOCAL_GIT_TIMEOUT
    )
    if code != 0:
        raise _GitFailure(BRANCH_MESSAGE, "detached_head")
    if stdout.strip() != BRANCH:
        raise _GitFailure(BRANCH_MESSAGE, "wrong_branch")
    code, stdout, _stderr = _run_git(
        ["remote", "get-url", "origin"], root, LOCAL_GIT_TIMEOUT
    )
    if code != 0:
        raise _GitFailure(ORIGIN_MESSAGE, "wrong_origin")
    if _normalize_remote(stdout) != _EXPECTED_REMOTE:
        raise _GitFailure(ORIGIN_MESSAGE, "wrong_origin")
    head = _full_sha(_git_stdout(["rev-parse", "HEAD"], root))
    if _operation_in_progress(root):
        raise _GitFailure(GIT_OPERATION_MESSAGE, "git_operation")
    code, stdout, stderr = _run_git(
        ["status", "--porcelain=v1", "--untracked-files=all"],
        root,
        LOCAL_GIT_TIMEOUT,
    )
    if code != 0:
        message, category = _classify(stderr, stdout)
        raise _GitFailure(message, category)
    if stdout.strip():
        raise _GitFailure(DIRTY_MESSAGE, "dirty_tree")
    return head


def _head():
    return _full_sha(_git_stdout(["rev-parse", "HEAD"], CHECKOUT))


def _fetch_origin_dev():
    code, stdout, stderr = _run_git(
        ["fetch", "--no-tags", "origin",
         "+refs/heads/dev:refs/remotes/origin/dev"],
        CHECKOUT,
        NETWORK_GIT_TIMEOUT,
    )
    if code != 0:
        message, category = _classify(stderr, stdout)
        raise _GitFailure(message, category)


def _pull_ff_only():
    code, stdout, stderr = _run_git(
        ["pull", "--ff-only", "origin", BRANCH], CHECKOUT, NETWORK_GIT_TIMEOUT
    )
    if code != 0:
        message, category = _classify(stderr, stdout)
        raise _GitFailure(message, category)


def _remote_dev_sha():
    value = _git_stdout(
        ["rev-parse", "--verify", "refs/remotes/origin/dev"], CHECKOUT
    )
    return _full_sha(value)


def _is_ancestor(ancestor, descendant):
    code, _stdout, stderr = _run_git(
        ["merge-base", "--is-ancestor", ancestor, descendant],
        CHECKOUT,
        LOCAL_GIT_TIMEOUT,
    )
    if code == 0:
        return True
    if code == 1:
        return False
    message, category = _classify(stderr, _stdout)
    raise _GitFailure(message, category)


def _sha256_bytes(data):
    if len(data) > _MAX_COMPANION_BYTES:
        raise _GitFailure(COMPANION_MESSAGE, "companion_invalid")
    return hashlib.sha256(data).hexdigest()


def _companion_digests(head):
    relative = "tools/max_pull_and_load.py"
    revision = "%s:%s" % (head, relative)
    code, blob, _stderr = _run_git_bytes(
        ["cat-file", "-p", revision], CHECKOUT, LOCAL_GIT_TIMEOUT
    )
    if code != 0 or not blob:
        raise _GitFailure(COMPANION_MESSAGE, "companion_missing")
    exact = _sha256_bytes(blob)
    normalized = _sha256_bytes(blob.replace(b"\r\n", b"\n"))
    return exact, normalized


def _update_result():
    _REAP_STATE["unreaped"] = False
    try:
        if not CHECKOUT.exists() or _is_empty_directory(CHECKOUT):
            result = _clone_result()
        else:
            result = _existing_result()
        if _REAP_STATE["unreaped"]:
            result["unreaped"] = True
        return result
    except _GitTimeout as exc:
        _note_timeout_reaped(exc.reaped)
        return {"ok": False, "message": TIMEOUT_MESSAGE, "category": "timeout",
                "unreaped": not exc.reaped or _REAP_STATE["unreaped"]}
    except _GitFailure as exc:
        return {"ok": False, "message": exc.message, "category": exc.category,
                "unreaped": _REAP_STATE["unreaped"]}
    except Exception:
        return {"ok": False, "message": GIT_FAILED_MESSAGE, "category": "internal",
                "unreaped": _REAP_STATE["unreaped"]}


def _existing_result():
    session = _session()
    loaded = session.loaded_head_sha
    if not (CHECKOUT / ".git").exists():
        raise _GitFailure(_not_a_checkout_message(), "not_a_checkout")
    head = _validate_checkout()
    if loaded is not None and head != loaded:
        raise _GitFailure(RESTART_MESSAGE, "restart_required")
    _fetch_origin_dev()
    if _head() != head:
        raise _GitFailure(
            "The checkout changed during the update check. No files were "
            "modified; retry.",
            "checkout_changed",
        )
    remote = _remote_dev_sha()
    if not _is_ancestor(head, remote):
        raise _GitFailure(DIVERGED_MESSAGE, "diverged")
    if loaded is not None and remote != loaded:
        raise _GitFailure(RESTART_MESSAGE, "restart_required")
    if loaded is None and not _alias_loaded() and head != remote:
        # A fresh Max session with nothing loaded yet: fast-forward before
        # trusting the on-disk companion, so the committed companion at
        # ``remote`` (not the stale one at ``head``) is what executes.  The
        # clean-tree and ancestry checks above already ran, so this cannot
        # discard local work.
        _pull_ff_only()
        after = _head()
        if after != remote:
            raise _GitFailure(
                "The checkout changed during the fast-forward pull. No retry "
                "was attempted.",
                "checkout_changed",
            )
        head = after
        exact, normalized = _companion_digests(head)
        return {"ok": True, "action": "clone", "head": head, "remote_sha": remote,
                "companion_sha256": exact,
                "companion_normalized_sha256": normalized,
                "message": "Updated checkout to commit %s." % head}
    exact, normalized = _companion_digests(head)
    return {"ok": True, "action": "existing", "head": head, "remote_sha": remote,
            "companion_sha256": exact, "companion_normalized_sha256": normalized,
            "message": ""}


def _clone_result():
    if not shutil.which("git"):
        raise _GitFailure(MISSING_GIT_MESSAGE, "missing_git")
    if CHECKOUT.exists() and not _is_empty_directory(CHECKOUT):
        raise _GitFailure(TARGET_EXISTS_MESSAGE, "permission")
    try:
        CHECKOUT.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise _GitFailure(TARGET_EXISTS_MESSAGE, "permission") from None
    code, stdout, stderr = _run_git(
        ["clone", "--branch", BRANCH, REPO_URL, str(CHECKOUT)],
        CHECKOUT.parent,
        NETWORK_GIT_TIMEOUT,
    )
    if code != 0:
        if CHECKOUT.exists() and not _is_empty_directory(CHECKOUT):
            raise _GitFailure(PARTIAL_CLONE_MESSAGE, "partial_clone")
        message, category = _classify(stderr, stdout)
        raise _GitFailure(message, category)
    try:
        head = _validate_checkout()
        remote = _remote_dev_sha()
        if remote != head:
            raise _GitFailure(PARTIAL_CLONE_MESSAGE, "partial_clone")
        exact, normalized = _companion_digests(head)
    except _GitFailure:
        raise _GitFailure(PARTIAL_CLONE_MESSAGE, "partial_clone") from None
    return {"ok": True, "action": "clone", "head": head, "remote_sha": remote,
            "companion_sha256": exact, "companion_normalized_sha256": normalized,
            "message": "Cloned commit %s." % head}


def _companion_path():
    return CHECKOUT / "tools" / "max_pull_and_load.py"


def _invoke_companion(blob_sha256, normalized_sha256=None):
    path = _companion_path()
    try:
        data = path.read_bytes()
    except OSError:
        raise RuntimeError(COMPANION_MESSAGE) from None
    if len(data) > _MAX_COMPANION_BYTES:
        raise _CompanionChanged(COMPANION_CHANGED_MESSAGE)
    digest = hashlib.sha256(data).hexdigest()
    normalized = hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()
    allowed = {
        value for value in (blob_sha256, normalized_sha256)
        if isinstance(value, str) and len(value) == 64
    }
    if not allowed or (digest not in allowed and normalized not in allowed):
        raise _CompanionChanged(COMPANION_CHANGED_MESSAGE)
    module_name = "_max2025_pull_and_load_companion"
    companion_mod = type(sys)(module_name)
    companion_mod.__file__ = str(path)
    sys.modules[module_name] = companion_mod
    try:
        exec(compile(data, str(path), "exec"), companion_mod.__dict__)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    starter = getattr(companion_mod, "start", None)
    if not callable(starter):
        raise RuntimeError("The launcher companion is incomplete. Restart 3ds Max and re-clone.")
    starter()


def _retire_bootstrap(session):
    module = session.bootstrap
    if module is None:
        return
    try:
        module.uninstall()
    except Exception:
        pass
    finally:
        session.bootstrap = None


def _validated_head(result):
    head = result.get("head")
    if not isinstance(head, str) or len(head) not in (40, 64):
        return None
    if any(ch not in _HEX for ch in head):
        return None
    return head.lower()


def _after_update(result):
    session = _session()
    session.in_flight = False
    session.worker = None
    session.bridge = None
    if not result.get("ok"):
        if result.get("unreaped") or result.get("category") == "restart_required":
            session.restart_required = True
            _retire_bootstrap(session)
        _emit(str(result.get("message") or GIT_FAILED_MESSAGE))
        return
    head = _validated_head(result)
    if head is None:
        session.restart_required = True
        _emit(RESTART_MESSAGE)
        return
    if result.get("action") == "clone":
        if session.loaded_head_sha is not None:
            session.restart_required = True
            _emit(RESTART_MESSAGE)
            return
        session.first_clone_head_sha = head
    elif session.loaded_head_sha is not None and head != session.loaded_head_sha:
        session.restart_required = True
        _retire_bootstrap(session)
        _emit(RESTART_MESSAGE)
        return
    if not _companion_path().is_file():
        _emit(COMPANION_MESSAGE)
        return
    try:
        _invoke_companion(
            result.get("companion_sha256"),
            result.get("companion_normalized_sha256"),
        )
    except _CompanionChanged:
        session.restart_required = True
        _retire_bootstrap(session)
        _emit(COMPANION_CHANGED_MESSAGE)
    except Exception as exc:
        _emit(_companion_failed_message(exc))


def _qt_bridge_factory(handler):
    from PySide6 import QtCore, QtWidgets
    application = QtWidgets.QApplication.instance()
    if application is None:
        raise RuntimeError(NO_QT_MESSAGE)

    class _Bridge(QtCore.QObject):
        done = QtCore.Signal(object)

        def __init__(self):
            super().__init__()
            self._handler = handler
            self.done.connect(self._deliver, QtCore.Qt.ConnectionType.QueuedConnection)

        @QtCore.Slot(object)
        def _deliver(self, result):
            self._handler(result)

    bridge = _Bridge()
    bridge.moveToThread(application.thread())
    return bridge


def _thread_worker_factory(target, bridge):
    class _Worker:
        def __init__(self):
            self._thread = threading.Thread(target=self._run, name="max2025-launcher", daemon=True)

        def start(self):
            self._thread.start()

        def _run(self):
            try:
                result = target()
            except Exception:
                result = {"ok": False, "message": GIT_FAILED_MESSAGE, "category": "internal"}
            bridge.done.emit(result)

    return _Worker()


def _schedule(*, bridge_factory=None, worker_factory=None):
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError(MAIN_THREAD_MESSAGE)
    if bridge_factory is None:
        from PySide6 import QtWidgets
        if QtWidgets.QApplication.instance() is None:
            raise RuntimeError(NO_QT_MESSAGE)
    session = _session()
    if session.restart_required:
        raise RuntimeError(RESTART_MESSAGE)
    if session.in_flight:
        _emit(DUPLICATE_MESSAGE)
        return None
    if ((not CHECKOUT.exists() or _is_empty_directory(CHECKOUT))
            and (session.loaded_head_sha is not None or _alias_loaded())):
        session.restart_required = True
        raise RuntimeError(RESTART_MESSAGE)
    session.in_flight = True
    try:
        bridge = (bridge_factory or _qt_bridge_factory)(_after_update)
        worker = (worker_factory or _thread_worker_factory)(_update_result, bridge)
    except Exception:
        session.in_flight = False
        raise
    session.bridge = bridge
    session.worker = worker
    _emit("Checking %s for updates ..." % CHECKOUT)
    worker.start()
    return None


if __name__ == "__main__":
    _install_macros()
    _schedule()
'''

def _embedded_python_source(*, checkout: Path = DEFAULT_CHECKOUT) -> str:
    macros = base64.b64encode(
        zlib.compress(render_macro_source(checkout=checkout).encode("utf-8"), 9)
    ).decode("ascii")
    startup = base64.b64encode(
        zlib.compress(render_startup_source(checkout=checkout).encode("utf-8"), 9)
    ).decode("ascii")
    return (
        _EMBEDDED_PYTHON_TEMPLATE.replace("__MACRO_SOURCE_B64__", macros)
        .replace("__STARTUP_SOURCE_B64__", startup)
    )


EMBEDDED_PYTHON = _embedded_python_source()
_EMBEDDED_BYTES = base64.b64encode(EMBEDDED_PYTHON.encode("utf-8")).decode("ascii")


def extract_embedded_python(ms_text: str) -> str:
    """Return the embedded Python from a generated ``.ms`` source.

    The extraction seam exists so tests can AST-parse and exercise the clone
    bootstrap without a MAXScript runtime.
    """

    lines = ms_text.splitlines()
    try:
        begin = next(
            index for index, line in enumerate(lines) if line.strip() == _EMBEDDED_BEGIN
        )
        end = next(
            index
            for index, line in enumerate(lines)
            if index > begin and line.strip() == _EMBEDDED_END
        )
    except StopIteration as exc:
        raise ValueError("embedded Python markers are missing") from exc
    body = "\n".join(lines[begin + 1 : end])
    first_quote = body.find('"')
    last_quote = body.rfind('"')
    if first_quote < 0 or last_quote <= first_quote:
        raise ValueError("embedded Python payload is missing")
    payload = base64.b64decode(body[first_quote + 1 : last_quote])
    try:
        payload = zlib.decompress(payload)
    except zlib.error:
        pass
    return payload.decode("utf-8")


def render_ms_source() -> str:
    """Render the standalone MAXScript launcher from this module's constants.

    The committed ``tools/max_pull_and_load.ms`` is generated by this function;
    a test compares the two so the embedded clone bootstrap cannot drift.
    """

    embedded_bytes = base64.b64encode(
        zlib.compress(
            _embedded_python_source(checkout=DEFAULT_CHECKOUT).encode("utf-8"), 9
        )
    ).decode("ascii")

    return (
        "-- 3dsModeler standalone launcher for 3ds Max 2025 (experimental).\n"
        "-- Save this single file locally and run Scripting > Run Script...\n"
        "-- It installs the persistent 3dsModeler MacroScripts and starts the\n"
        "-- asynchronous dev update flow. It needs no sibling Python file and\n"
        "-- no current-directory assumptions. The base64 block is a fixed\n"
        "-- Python installer/bootstrap; see tools/max_pull_and_load.py for the\n"
        "-- readable source and generator.\n"
        "(\n"
        '    local launcher_version = 1;\n'
        '    local package_name = "%s";\n'
        '    local repo_url = "%s";\n'
        '    local branch_name = "%s";\n'
        '    local checkout_path = "%s";\n'
        "%s\n"
        '    local embedded_python = "%s";\n'
        "%s\n"
        "    python.execute (\"import base64, zlib; exec(compile(zlib.decompress(base64.b64decode('\" + embedded_python + \"')), '<3dsModeler-embedded>', 'exec'), {'__name__': '__main__'})\");\n"
        ")\n"
    ) % (
        PACKAGE_NAME,
        REPO_URL,
        BRANCH,
        str(DEFAULT_CHECKOUT).replace("\\", "\\\\"),
        _EMBEDDED_BEGIN,
        embedded_bytes,
        _EMBEDDED_END,
    )

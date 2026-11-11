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
import types
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
_MAX_CAPTURED_BYTES = 65536
# Bounded grace period for terminating and reaping a timed-out Git tree.
_KILL_WAIT = 5.0
_HEX_SHA = re.compile(r"^[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?$")

REQUIRED_ROOT_ENTRIES = (
    "__init__.py",
    "core",
    "max_host",
    "qt_ui",
    "core/default_profile.json",
)

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


def _terminate_tree(process: subprocess.Popen) -> bool:
    """Terminate the whole Git process tree and confirm it was reaped.

    Killing only the immediate ``git`` process lets spawned Windows transport
    helpers (``git-remote-https``, ``git-credential-manager``, ...) survive and
    keep stdout/stderr open.  ``taskkill /T`` ends that tree on Windows and a
    signal to the process group does the same on POSIX.
    """

    pid = getattr(process, "pid", None)
    if pid is not None:
        if _is_windows():
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    shell=False,
                    creationflags=_creation_flags(),
                    timeout=_KILL_WAIT,
                    check=False,
                )
            except Exception:
                pass
        else:
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except Exception:
                pass
    try:
        process.kill()
    except Exception:
        pass
    return _reap(process)


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


def _head(checkout: Path) -> str:
    value = _git_stdout(["rev-parse", "HEAD"], cwd=checkout)
    return _normalize_sha(value)


def _remote_dev_sha(checkout: Path) -> str:
    value = _git_stdout(
        ["rev-parse", "--verify", "refs/remotes/origin/dev"], cwd=checkout
    )
    return _normalize_sha(value)


def _normalize_remote(url: str) -> str:
    text = (url or "").strip().rstrip("/")
    if text.lower().endswith(".git"):
        text = text[:-4]
    return text.rstrip("/")


_EXPECTED_REMOTE = _normalize_remote(REPO_URL)


def _git_operation_in_progress(checkout: Path) -> bool:
    try:
        git_dir = Path(
            _git_stdout(["rev-parse", "--absolute-git-dir"], cwd=checkout)
        )
    except LauncherError:
        return False
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


def _validate_checkout(checkout: Path) -> dict[str, Any]:
    """Validate an existing checkout with bounded local Git reads only."""

    root = _checkout_root(checkout)
    top_level = _git_stdout(["rev-parse", "--show-toplevel"], cwd=root)
    if not _same_path(top_level, root):
        raise LauncherError(TOP_LEVEL_MESSAGE, "wrong_top_level")
    bare = _git_stdout(["rev-parse", "--is-bare-repository"], cwd=root).lower()
    if bare == "true":
        raise LauncherError(LAYOUT_MESSAGE, "checkout_layout")
    branch_result = _run_git(
        ["symbolic-ref", "--quiet", "--short", "HEAD"], cwd=root, timeout=LOCAL_GIT_TIMEOUT
    )
    if branch_result.returncode != 0:
        raise LauncherError(BRANCH_MESSAGE, "detached_head")
    branch = branch_result.stdout.strip()
    if branch != BRANCH:
        raise LauncherError(BRANCH_MESSAGE, "wrong_branch")
    origin_result = _run_git(["remote", "get-url", "origin"], cwd=root, timeout=LOCAL_GIT_TIMEOUT)
    if origin_result.returncode != 0:
        raise LauncherError(ORIGIN_MESSAGE, "wrong_origin")
    if _normalize_remote(origin_result.stdout) != _EXPECTED_REMOTE:
        raise LauncherError(ORIGIN_MESSAGE, "wrong_origin")
    head = _head(root)
    if _git_operation_in_progress(root):
        raise LauncherError(GIT_OPERATION_MESSAGE, "git_operation")
    status = _run_git(
        ["status", "--porcelain=v1", "--untracked-files=all"],
        cwd=root,
        timeout=LOCAL_GIT_TIMEOUT,
    )
    if status.returncode != 0:
        raise _classify_git_failure(status)
    if status.stdout.strip():
        raise LauncherError(DIRTY_MESSAGE, "dirty_tree")
    return {
        "root": str(root),
        "head": head,
        "branch": branch,
    }


def _clone_checkout(checkout: Path) -> str:
    if checkout.exists():
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
        if checkout.exists():
            raise LauncherError(PARTIAL_CLONE_MESSAGE, "partial_clone")
        raise _classify_git_failure(result)
    try:
        return _validate_checkout(checkout)["head"]
    except LauncherError as exc:
        raise LauncherError(PARTIAL_CLONE_MESSAGE, "partial_clone") from exc


def _fetch_origin_dev(checkout: Path) -> None:
    result = _run_git(
        ["fetch", "--no-tags", "origin", BRANCH],
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


def _worker_update(
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

        if not checkout.exists():
            if modules_loaded or loaded_sha is not None:
                return _failure("stale_import", STALE_IMPORT_MESSAGE)
            head = _clone_checkout(checkout)
            return _result(ok=True, action="clone", after_sha=head)

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
            return _failure(exc.category, exc.message)
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

    class _Bridge(QtCore.QObject):
        completed = QtCore.Signal(object)

        def __init__(self) -> None:
            super().__init__()
            self._slots: list[Callable[[Any], None]] = []
            self.completed.connect(self._deliver, QtCore.Qt.ConnectionType.QueuedConnection)

        @QtCore.Slot(object)
        def _deliver(self, result: object) -> None:
            for slot in list(self._slots):
                slot(result)

        def connect(self, slot: Callable[[Any], None]) -> None:
            self._slots.append(slot)

        def emit(self, result: Any) -> None:
            self.completed.emit(result)

    bridge = _Bridge()
    bridge.moveToThread(application.thread())
    return bridge


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
        bootstrap.show_tools()
        _emit("Loaded commit %s." % sha[:12])
        return

    if action not in ("clone", "handoff", "import-only", "pull-import"):
        _emit(GIT_FAILED_MESSAGE)
        raise LauncherError(GIT_FAILED_MESSAGE, "git_failed")

    package = load_checkout_package(checkout, sha)
    bootstrap = getattr(session, "bootstrap", None) or sys.modules.get(
        PACKAGE_NAME + ".max_host.bootstrap"
    )
    if bootstrap is None:
        _enter_restart_required(session, STALE_IMPORT_MESSAGE)
        raise LauncherError(STALE_IMPORT_MESSAGE, "stale_import")
    bootstrap.show_tools()
    _emit("Loaded commit %s from %s." % (sha[:12], Path(package.__file__).parent))


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
# Embedded standalone .ms bootstrap (base64 to keep MAXScript quoting safe)
# ---------------------------------------------------------------------------

EMBEDDED_PYTHON = r'''
import base64
import os
import runpy
import shutil
import signal
import subprocess
import sys
import threading
from pathlib import Path

PACKAGE_NAME = "max2025_menu_prototype"
REPO_URL = "https://github.com/nekirk11/3dsModeler.git"
BRANCH = "dev"
CHECKOUT = Path(r"C:\dev\3dsModeler")
SESSION_MODULE_NAME = "_max2025_pull_and_load_session"
SESSION_SCHEMA_VERSION = 1
LOCAL_GIT_TIMEOUT = 15.0
NETWORK_GIT_TIMEOUT = 120.0
_KILL_WAIT = 5.0
_MAX_CAPTURED_BYTES = 65536
RESTART_MESSAGE = "Checkout updated; restart 3ds Max before loading this commit."
NO_QT_MESSAGE = "Start 3ds Max 2025 before running the launcher; its Qt application was not found."
MAIN_THREAD_MESSAGE = "The launcher must run on the 3ds Max main thread."
DUPLICATE_MESSAGE = "An update is already in progress."
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
COMPANION_MESSAGE = "The launcher companion is missing from the checkout. Repair it outside Max."
COMPANION_FAILED_MESSAGE = "The launcher companion failed to start. Restart 3ds Max and retry."

_REQUIRED = ("__init__.py", "core", "max_host", "qt_ui", "core/default_profile.json")
_HEX = "0123456789abcdefABCDEF"


def _emit(message):
    print(message, flush=True)


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


def _terminate_tree(process):
    pid = getattr(process, "pid", None)
    if pid is not None:
        if _is_windows():
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    shell=False,
                    creationflags=_creation_flags(),
                    timeout=_KILL_WAIT,
                    check=False,
                )
            except Exception:
                pass
        else:
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except Exception:
                pass
    try:
        process.kill()
    except Exception:
        pass
    return _reap(process)


def _run_git(args, cwd, timeout):
    executable = shutil.which("git")
    if not executable:
        raise _GitFailure(MISSING_GIT_MESSAGE, "missing_git")
    try:
        process = subprocess.Popen(
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
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        raise _GitTimeout(_terminate_tree(process)) from None
    except OSError:
        raise _GitFailure(GIT_FAILED_MESSAGE, "git_failed") from None
    return (process.returncode, _bounded(stdout), _bounded(stderr))


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
    return text.rstrip("/")


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
    try:
        git_dir = Path(_git_stdout(["rev-parse", "--absolute-git-dir"], root))
    except _GitFailure:
        return False
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


def _clone_result():
    if not shutil.which("git"):
        raise _GitFailure(MISSING_GIT_MESSAGE, "missing_git")
    if CHECKOUT.exists():
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
        if CHECKOUT.exists():
            raise _GitFailure(PARTIAL_CLONE_MESSAGE, "partial_clone")
        message, category = _classify(stderr, stdout)
        raise _GitFailure(message, category)
    try:
        head = _validate_checkout()
    except _GitFailure:
        raise _GitFailure(PARTIAL_CLONE_MESSAGE, "partial_clone") from None
    return {"ok": True, "action": "clone", "head": head,
            "message": "Cloned commit %s." % head[:12]}


def _existing_result():
    head = _validate_checkout()
    return {"ok": True, "action": "existing", "head": head, "message": ""}


def _update_result():
    try:
        if CHECKOUT.exists():
            return _existing_result()
        return _clone_result()
    except _GitTimeout as exc:
        return {"ok": False, "message": TIMEOUT_MESSAGE, "category": "timeout",
                "unreaped": not exc.reaped}
    except _GitFailure as exc:
        return {"ok": False, "message": exc.message, "category": exc.category}
    except Exception:
        return {"ok": False, "message": GIT_FAILED_MESSAGE, "category": "internal"}


def _companion_path():
    return CHECKOUT / "tools" / "max_pull_and_load.py"


def _invoke_companion():
    path = _companion_path()
    if not path.is_file():
        raise RuntimeError(COMPANION_MESSAGE)
    namespace = runpy.run_path(str(path), run_name="_max2025_pull_and_load_companion")
    starter = namespace.get("start")
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
        if result.get("unreaped"):
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
        # Validation ran in the worker; a changed checkout must never execute
        # the updated on-disk companion against the already loaded UI.
        session.restart_required = True
        _retire_bootstrap(session)
        _emit(RESTART_MESSAGE)
        return
    if not _companion_path().is_file():
        _emit(COMPANION_MESSAGE)
        return
    try:
        _invoke_companion()
    except Exception:
        _emit(COMPANION_FAILED_MESSAGE)


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
    if not CHECKOUT.exists() and (session.loaded_head_sha is not None or _alias_loaded()):
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
    _schedule()
'''

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
    return base64.b64decode(body[first_quote + 1 : last_quote]).decode("utf-8")


def render_ms_source() -> str:
    """Render the standalone MAXScript launcher from this module's constants.

    The committed ``tools/max_pull_and_load.ms`` is generated by this function;
    a test compares the two so the embedded clone bootstrap cannot drift.
    """

    return (
        "-- 3dsModeler standalone launcher for 3ds Max 2025 (experimental).\n"
        "-- Save this single file locally and run Scripting > Run Script...\n"
        "-- It needs no sibling Python file and no current-directory assumptions.\n"
        "-- The base64 block is a fixed Python clone bootstrap; see\n"
        "-- tools/max_pull_and_load.py for the readable source and generator.\n"
        "(\n"
        '    local launcher_version = 1;\n'
        '    local package_name = "%s";\n'
        '    local repo_url = "%s";\n'
        '    local branch_name = "%s";\n'
        '    local checkout_path = "%s";\n'
        "%s\n"
        '    local embedded_python = "%s";\n'
        "%s\n"
        "    python.execute (\"import base64; exec(compile(base64.b64decode('\" + embedded_python + \"'), '<3dsModeler-embedded>', 'exec'), {'__name__': '__main__'})\");\n"
        ")\n"
    ) % (
        PACKAGE_NAME,
        REPO_URL,
        BRANCH,
        str(DEFAULT_CHECKOUT).replace("\\", "\\\\"),
        _EMBEDDED_BEGIN,
        _EMBEDDED_BYTES,
        _EMBEDDED_END,
    )

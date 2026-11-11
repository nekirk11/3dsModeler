"""Build a deterministic, source-only ZIP of this prototype.

The builder is intentionally a strict allowlist, not an archive of whatever is
in the directory.  It copies only Python/JSON/Markdown source files plus the
four original sandbox static assets and the one standalone MAXScript launcher.
Git metadata, caches, bytecode, test
caches, snapshots, raw evidence, logs, screenshots, archives, local outputs,
editor files, symlinks, and special files never enter the archive.

The archive has one top-level directory, ``max2025_menu_prototype/``.  Member
order, timestamps, and permission bits are fixed so identical source bytes
produce identical ZIP bytes.  Two sidecars are written next to the archive:

* ``<archive>.sha256`` - standard ``sha256sum`` line for the ZIP;
* ``<archive>.manifest.json`` - schema version, package root, archive hash and
  size, then sorted member paths, byte sizes, and SHA-256 hashes.

Sidecars contain package-relative names only; no absolute machine paths are
recorded.  Existing outputs and sidecars are refused, never overwritten.

This module uses the Python standard library only and does not import Max,
``pymxs``, or Qt.  It performs no Git operations, host detection, or dependency
installation.

Usage::

    python3 tools/portable.py \\
        --output ../max2025-delivery/max2025_menu_prototype.zip
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Sequence

PACKAGE_NAME = "max2025_menu_prototype"
MANIFEST_SCHEMA_VERSION = 1

ALLOWED_SUFFIXES = frozenset({".py", ".json", ".md"})
STATIC_ALLOWLIST = frozenset(
    {
        "sandbox/static/app.js",
        "sandbox/static/index.html",
        "sandbox/static/style.css",
        "sandbox/static/favicon.svg",
        "tools/max_pull_and_load.ms",
    }
)
ALLOWED_TOP_LEVEL = frozenset(
    {
        "__init__.py",
        "README.md",
        "core",
        "max_host",
        "qt_ui",
        "sandbox",
        "tools",
        "tests",
        "docs",
    }
)
REQUIRED_MEMBERS = frozenset({"__init__.py", "README.md", "core/default_profile.json"})

_PRUNED_DIR_NAMES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        ".cache",
        "node_modules",
        "htmlcov",
        "build",
        "dist",
        "evidence",
        "snapshots",
        "logs",
        "tmp",
        "temp",
    }
)
_PRUNED_FILE_SUFFIXES = frozenset(
    {
        ".pyc",
        ".pyo",
        ".pyd",
        ".so",
        ".dll",
        ".dylib",
        ".zip",
        ".7z",
        ".rar",
        ".tar",
        ".gz",
        ".tgz",
        ".bz2",
        ".xz",
        ".exe",
        ".msi",
        ".log",
        ".trace",
        ".tmp",
        ".temp",
        ".bak",
        ".orig",
        ".rej",
        ".swp",
        ".swo",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".ico",
        ".pdf",
        ".db",
        ".ini",
    }
)
_PRUNED_FILE_NAMES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini"})
_FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)
_COMPRESS_LEVEL = 9


class PortableError(ValueError):
    """A source or destination is unsafe for packaging."""


@dataclass(frozen=True)
class Member:
    """One package-relative file selected for the archive."""

    path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class BuildResult:
    """Paths and hashes produced by :func:`build_portable_zip`."""

    output: Path
    sha256: str
    size: int
    sha256_path: Path
    manifest_path: Path
    members: Sequence[Member]


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_relative(path: PurePosixPath) -> None:
    if path.is_absolute():
        raise PortableError("absolute member path is not allowed")
    parts = path.parts
    if not parts:
        raise PortableError("empty member path is not allowed")
    for part in parts:
        if part in ("", ".", ".."):
            raise PortableError("traversal member path is not allowed")
        if "\\" in part or "\x00" in part:
            raise PortableError("unsafe member name is not allowed")
    text = path.as_posix()
    if not text or text.startswith("/") or text.startswith("../"):
        raise PortableError("traversal member path is not allowed")


def _is_pruned_dir(name: str) -> bool:
    return name.startswith(".") or name in _PRUNED_DIR_NAMES


def _is_pruned_file(name: str) -> bool:
    if name.startswith(".") or name in _PRUNED_FILE_NAMES or name.endswith("~"):
        return True
    return Path(name).suffix.lower() in _PRUNED_FILE_SUFFIXES


def _classify(path: Path, relative: PurePosixPath) -> bool:
    """Return True when *path* is an eligible member, False when safely pruned."""

    _validate_relative(relative)
    name = path.name
    if _is_pruned_file(name):
        return False
    if relative.as_posix() in STATIC_ALLOWLIST:
        return True
    if path.suffix.lower() in ALLOWED_SUFFIXES:
        return True
    raise PortableError(
        "unexpected file is not in the packaging allowlist: %s"
        % relative.as_posix()
    )


def collect_members(source: Path | str) -> list[Member]:
    """Select allowlisted source files without following symlinks.

    Raises :class:`PortableError` for an unexpected top-level entry, an
    ineligible file, a symlink, a special file, an unsafe name, or a
    case-insensitive duplicate.
    """

    root = Path(source)
    if root.is_symlink():
        raise PortableError("source root may not be a symlink")
    if not root.is_dir():
        raise PortableError("source is not a directory: %s" % root)
    root = root.resolve()

    seen: set[str] = set()
    selected: list[tuple[PurePosixPath, Path]] = []

    def add_file(path: Path, relative: PurePosixPath) -> None:
        if path.is_symlink():
            raise PortableError("symlinked file is not allowed: %s" % relative.as_posix())
        try:
            info = path.lstat()
        except OSError as exc:
            raise PortableError(
                "cannot stat source file %s" % relative.as_posix()
            ) from exc
        if not stat.S_ISREG(info.st_mode):
            raise PortableError(
                "special file is not allowed: %s" % relative.as_posix()
            )
        if not _classify(path, relative):
            return
        _validate_relative(relative)
        key = relative.as_posix().casefold()
        if key in seen:
            raise PortableError(
                "case-insensitive path collision: %s" % relative.as_posix()
            )
        seen.add(key)
        selected.append((relative, path))

    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        name = entry.name
        if _is_pruned_dir(name) or name in _PRUNED_FILE_NAMES:
            continue
        if name not in ALLOWED_TOP_LEVEL:
            raise PortableError("unexpected top-level entry: %s" % name)
        if entry.is_symlink():
            raise PortableError("symlinked source entry is not allowed: %s" % name)
        if entry.is_dir():
            for dirpath, dirnames, filenames in os.walk(entry, followlinks=False):
                directory = Path(dirpath)
                kept: list[str] = []
                for dirname in sorted(dirnames):
                    child = directory / dirname
                    if _is_pruned_dir(dirname):
                        continue
                    if child.is_symlink():
                        raise PortableError(
                            "symlinked source directory is not allowed: %s"
                            % child.relative_to(root).as_posix()
                        )
                    kept.append(dirname)
                dirnames[:] = kept
                for filename in sorted(filenames):
                    child = directory / filename
                    relative = PurePosixPath(child.relative_to(root).as_posix())
                    add_file(child, relative)
        elif entry.is_file():
            relative = PurePosixPath(name)
            add_file(entry, relative)
        else:
            raise PortableError("special top-level entry is not allowed: %s" % name)

    members = sorted(
        (
            Member(path.as_posix(), file_path.stat().st_size, _sha256_file(file_path))
            for path, file_path in selected
        ),
        key=lambda member: member.path,
    )
    paths = {member.path for member in members}
    missing = sorted(REQUIRED_MEMBERS - paths)
    if missing:
        raise PortableError(
            "required package members are missing: %s" % ", ".join(missing)
        )
    return members


def _sidecar_paths(output: Path) -> tuple[Path, Path]:
    return (
        output.with_name(output.name + ".sha256"),
        output.with_name(output.name + ".manifest.json"),
    )


def _check_destination(source: Path, output: Path) -> None:
    source_resolved = source.resolve()
    output_resolved = output.resolve()
    if output_resolved == source_resolved or output_resolved.is_relative_to(source_resolved):
        raise PortableError("output must be outside the source root")
    if output.suffix.lower() != ".zip":
        raise PortableError("output filename must end with .zip")
    if output.name in ("", ".", ".."):
        raise PortableError("invalid output filename")
    sha_path, manifest_path = _sidecar_paths(output)
    for candidate in (output, sha_path, manifest_path):
        if candidate.exists():
            raise PortableError("refusing to overwrite existing output: %s" % candidate.name)
    parent = output.parent
    if parent.exists() and not parent.is_dir():
        raise PortableError("output parent is not a directory")
    parent.mkdir(parents=True, exist_ok=True)


def _write_archive(
    source: Path,
    output: Path,
    members: Sequence[Member],
    temp_path: Path,
) -> None:
    with zipfile.ZipFile(
        temp_path,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=_COMPRESS_LEVEL,
    ) as archive:
        for member in members:
            info = zipfile.ZipInfo(
                "%s/%s" % (PACKAGE_NAME, member.path),
                date_time=_FIXED_DATE_TIME,
            )
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(
                info,
                (source / member.path).read_bytes(),
                compress_type=zipfile.ZIP_DEFLATED,
                compresslevel=_COMPRESS_LEVEL,
            )

    with zipfile.ZipFile(temp_path, "r") as archive:
        bad = archive.testzip()
        if bad is not None:
            raise PortableError("archive integrity check failed at %s" % bad)
        names = archive.namelist()
        expected = ["%s/%s" % (PACKAGE_NAME, member.path) for member in members]
        if names != expected:
            raise PortableError("archive member order or contents changed")
        for member in members:
            data = archive.read("%s/%s" % (PACKAGE_NAME, member.path))
            if _sha256_bytes(data) != member.sha256 or len(data) != member.size:
                raise PortableError(
                    "archive member does not match source: %s" % member.path
                )


def _manifest_payload(
    output: Path, archive_sha256: str, archive_size: int, members: Sequence[Member]
) -> dict[str, object]:
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "package_root": PACKAGE_NAME,
        "archive": output.name,
        "archive_sha256": archive_sha256,
        "archive_size": archive_size,
        "member_count": len(members),
        "members": [
            {"path": member.path, "size": member.size, "sha256": member.sha256}
            for member in members
        ],
    }


def build_portable_zip(source: Path | str, output: Path | str) -> BuildResult:
    """Build the deterministic ZIP and both sidecars.

    ``source`` is scanned for allowlisted files; ``output`` must name a new
    ``.zip`` outside the source tree.  Nothing is overwritten.
    """

    source_path = Path(source)
    output_path = Path(output)
    if source_path.is_symlink():
        raise PortableError("source root may not be a symlink")
    _check_destination(source_path, output_path)
    source_path = source_path.resolve()
    members = collect_members(source_path)
    if not members:
        raise PortableError("no package members were selected")

    sha_path, manifest_path = _sidecar_paths(output_path)
    temporary: list[Path] = []
    try:
        with tempfile.NamedTemporaryFile(
            prefix=".portable-", suffix=".zip.tmp", dir=output_path.parent, delete=False
        ) as handle:
            zip_temp = Path(handle.name)
        temporary.append(zip_temp)
        _write_archive(source_path, output_path, members, zip_temp)

        archive_sha256 = _sha256_file(zip_temp)
        archive_size = zip_temp.stat().st_size
        manifest_text = (
            json.dumps(
                _manifest_payload(output_path, archive_sha256, archive_size, members),
                indent=2,
                sort_keys=False,
            )
            + "\n"
        )
        with tempfile.NamedTemporaryFile(
            prefix=".portable-", suffix=".sha256.tmp", dir=output_path.parent,
            mode="w", encoding="utf-8", delete=False,
        ) as handle:
            handle.write("%s  %s\n" % (archive_sha256, output_path.name))
            sha_temp = Path(handle.name)
        temporary.append(sha_temp)
        with tempfile.NamedTemporaryFile(
            prefix=".portable-", suffix=".manifest.json.tmp", dir=output_path.parent,
            mode="w", encoding="utf-8", delete=False,
        ) as handle:
            handle.write(manifest_text)
            manifest_temp = Path(handle.name)
        temporary.append(manifest_temp)

        for candidate in (output_path, sha_path, manifest_path):
            if candidate.exists():
                raise PortableError(
                    "refusing to overwrite existing output: %s" % candidate.name
                )
        os.replace(zip_temp, output_path)
        temporary.remove(zip_temp)
        os.replace(sha_temp, sha_path)
        temporary.remove(sha_temp)
        os.replace(manifest_temp, manifest_path)
        temporary.remove(manifest_temp)
    finally:
        for path in temporary:
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    return BuildResult(
        output=output_path,
        sha256=archive_sha256,
        size=archive_size,
        sha256_path=sha_path,
        manifest_path=manifest_path,
        members=tuple(members),
    )


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m max2025_menu_prototype.tools.portable",
        description="Build the deterministic source-only prototype ZIP.",
    )
    parser.add_argument(
        "--source",
        default=str(Path(__file__).resolve().parents[1]),
        help="prototype package directory (default: this package)",
    )
    parser.add_argument("--output", required=True, help="new .zip path outside source")
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        result = build_portable_zip(args.source, args.output)
    except PortableError as exc:
        print("portable build refused: %s" % exc, file=sys.stderr)
        return 2

    print("Built %s" % result.output)
    print("SHA-256 %s" % result.sha256)
    print("Members %d" % len(result.members))
    print("Sidecars %s, %s" % (result.sha256_path.name, result.manifest_path.name))
    return 0


if __name__ == "__main__":
    sys.exit(main())

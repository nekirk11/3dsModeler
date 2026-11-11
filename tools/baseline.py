"""Capture and verify a read-only manifest of all pre-existing paths.

The manifest records root-relative, sorted entries with entry type, mode,
symlink target, size, and per-file SHA-256.  Verification reports changed,
missing, and added entries inside protected trees.  Symlinks are never
followed or hashed, and the new prototype subtree is excluded.

Usage::

    python3 -m max2025_menu_prototype.tools.baseline capture --output PATH
    python3 -m max2025_menu_prototype.tools.baseline verify --manifest PATH
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any, Iterable

MANIFEST_SCHEMA_VERSION = 1
EXCLUDED_TOP_LEVEL: tuple[str, ...] = ("max2025_menu_prototype",)
_CHUNK = 1 << 20


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _entry_for(path: Path, root: Path) -> dict[str, Any]:
    relative = path.relative_to(root).as_posix()
    info = path.lstat()
    mode = stat.S_IMODE(info.st_mode)
    if stat.S_ISLNK(info.st_mode):
        try:
            target = os.readlink(path)
        except OSError:
            target = None
        return {
            "path": relative,
            "type": "symlink",
            "mode": mode,
            "size": info.st_size,
            "sha256": None,
            "target": target,
        }
    if stat.S_ISDIR(info.st_mode):
        return {
            "path": relative,
            "type": "dir",
            "mode": mode,
            "size": 0,
            "sha256": None,
            "target": None,
        }
    if stat.S_ISREG(info.st_mode):
        return {
            "path": relative,
            "type": "file",
            "mode": mode,
            "size": info.st_size,
            "sha256": _sha256_file(path),
            "target": None,
        }
    return {
        "path": relative,
        "type": "other",
        "mode": mode,
        "size": 0,
        "sha256": None,
        "target": None,
    }


def scan(root: Path | str, excluded: Iterable[str] = EXCLUDED_TOP_LEVEL) -> list[dict[str, Any]]:
    """Return sorted manifest entries for ``root`` without following symlinks."""

    root = Path(root).resolve()
    excluded_set = {str(name) for name in excluded}
    entries: list[dict[str, Any]] = []

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        directory = Path(dirpath)
        relative_dir = directory.relative_to(root)
        if relative_dir == Path("."):
            dirnames[:] = sorted(d for d in dirnames if d not in excluded_set)
        else:
            dirnames[:] = sorted(dirnames)
        for name in sorted(dirnames + filenames):
            path = directory / name
            if path.relative_to(root).parts[0] in excluded_set:
                continue
            entries.append(_entry_for(path, root))

    entries.sort(key=lambda entry: entry["path"])
    return entries


def capture(root: Path | str, output: Path | str) -> dict[str, Any]:
    """Write a full protected-path manifest and return it."""

    root_path = Path(root).resolve()
    entries = scan(root_path, EXCLUDED_TOP_LEVEL)
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "root": str(root_path),
        "excluded_top_level": list(EXCLUDED_TOP_LEVEL),
        "entry_count": len(entries),
        "entries": entries,
    }
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )
    return manifest


def _load_manifest(manifest: Path | str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(manifest, dict):
        data = manifest
    else:
        with Path(manifest).open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        raise ValueError("Manifest is not a valid baseline manifest")
    if data.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported manifest schema_version: {data.get('schema_version')!r}"
        )
    return data


_COMPARED_FIELDS = ("type", "mode", "size", "sha256", "target")


def verify(
    root: Path | str | None,
    manifest: Path | str | dict[str, Any],
) -> dict[str, Any]:
    """Compare the tree against a manifest.

    ``root`` defaults to the manifest's recorded root.  Returns a report with
    ``ok`` plus ``changed``, ``missing``, and ``added`` lists.
    """

    data = _load_manifest(manifest)
    root_path = Path(root).resolve() if root is not None else Path(data["root"]).resolve()
    excluded = data.get("excluded_top_level") or list(EXCLUDED_TOP_LEVEL)
    expected = {entry["path"]: entry for entry in data["entries"]}
    actual_entries = scan(root_path, excluded)
    actual = {entry["path"]: entry for entry in actual_entries}

    changed: list[dict[str, Any]] = []
    for path in sorted(expected.keys() & actual.keys()):
        old = expected[path]
        new = actual[path]
        fields = [field for field in _COMPARED_FIELDS if old.get(field) != new.get(field)]
        if fields:
            changed.append({"path": path, "fields": fields})

    missing = sorted(expected.keys() - actual.keys())
    added = sorted(actual.keys() - expected.keys())

    return {
        "ok": not changed and not missing and not added,
        "root": str(root_path),
        "manifest_entries": len(expected),
        "actual_entries": len(actual),
        "changed": changed,
        "missing": missing,
        "added": added,
    }


def _print_report(report: dict[str, Any]) -> None:
    if report["ok"]:
        print(
            f"Baseline verified: {report['actual_entries']} entries unchanged "
            f"under {report['root']}"
        )
        return
    print(
        f"Baseline verification FAILED under {report['root']}: "
        f"{len(report['changed'])} changed, {len(report['missing'])} missing, "
        f"{len(report['added'])} added"
    )
    for item in report["changed"]:
        print(f"  changed: {item['path']} ({', '.join(item['fields'])})")
    for path in report["missing"]:
        print(f"  missing: {path}")
    for path in report["added"]:
        print(f"  added: {path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m max2025_menu_prototype.tools.baseline",
        description="Capture or verify the protected-path baseline manifest.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    capture_parser = subparsers.add_parser("capture", help="write a manifest")
    capture_parser.add_argument("--output", required=True, help="manifest path")
    capture_parser.add_argument(
        "--root", default=".", help="tree root (default: current directory)"
    )

    verify_parser = subparsers.add_parser("verify", help="verify a manifest")
    verify_parser.add_argument("--manifest", required=True, help="manifest path")
    verify_parser.add_argument(
        "--root",
        default=None,
        help="tree root (default: root recorded in the manifest)",
    )

    args = parser.parse_args(argv)

    if args.command == "capture":
        manifest = capture(Path(args.root), Path(args.output))
        print(
            f"Captured {manifest['entry_count']} protected entries from "
            f"{manifest['root']} -> {args.output}"
        )
        return 0

    report = verify(
        Path(args.root) if args.root is not None else None,
        Path(args.manifest),
    )
    _print_report(report)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

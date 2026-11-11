"""Portable source-only ZIP tests (stdlib only, no Max/Qt imports)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from max2025_menu_prototype.tools import portable


class PortableFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.source = self.root / "max2025_menu_prototype"
        self._write("__init__.py", 'MARKER = "fixture-package"\n')
        self._write("README.md", "# Fixture\n")
        self._write("core/__init__.py", "")
        self._write("core/default_profile.json", '{"schema_version": 1}\n')
        self._write("max_host/__init__.py", "")
        self._write("max_host/example.py", "VALUE = 1\n")
        self._write("qt_ui/__init__.py", "")
        self._write("qt_ui/example.py", "VALUE = 2\n")
        self._write("sandbox/__init__.py", "")
        self._write("sandbox/static/app.js", "console.log('fixture');\n")
        self._write("sandbox/static/index.html", "<!doctype html>\n")
        self._write("sandbox/static/style.css", "body {}\n")
        self._write("sandbox/static/favicon.svg", "<svg></svg>\n")
        self._write("tools/__init__.py", "")
        self._write("tools/example.py", "VALUE = 3\n")
        self._write(
            "tools/max_pull_and_load.ms",
            '-- fixture launcher\npython.execute "print(1)"\n',
        )
        self._write("tests/__init__.py", "")
        self._write("tests/test_example.py", "")
        self._write("docs/guide.md", "# Guide\n")

    def tearDown(self) -> None:
        self._temp.cleanup()

    def _write(self, relative: str, text: str) -> Path:
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def _build(self, name: str = "max2025_menu_prototype.zip") -> portable.BuildResult:
        return portable.build_portable_zip(self.source, self.root / "deliverables" / name)


class BuildTests(PortableFixture):
    def test_archive_has_one_root_and_only_allowlisted_members(self):
        result = self._build()
        with zipfile.ZipFile(result.output) as archive:
            self.assertIsNone(archive.testzip())
            names = archive.namelist()
        self.assertTrue(names)
        for name in names:
            self.assertTrue(name.startswith("max2025_menu_prototype/"), name)
            self.assertNotIn("..", Path(name).parts)
        relative = {name.split("/", 1)[1] for name in names}
        self.assertIn("__init__.py", relative)
        self.assertIn("core/default_profile.json", relative)
        self.assertIn("sandbox/static/app.js", relative)
        self.assertIn("sandbox/static/index.html", relative)
        self.assertIn("sandbox/static/style.css", relative)
        self.assertIn("sandbox/static/favicon.svg", relative)
        self.assertIn("tools/max_pull_and_load.ms", relative)
        self.assertIn("docs/guide.md", relative)
        self.assertEqual(
            list(names),
            ["max2025_menu_prototype/" + member.path for member in result.members],
        )

    def test_sidecars_match_archive_and_members(self):
        result = self._build()
        actual_sha = portable._sha256_file(result.output)
        self.assertEqual(result.sha256, actual_sha)
        self.assertEqual(result.size, result.output.stat().st_size)
        self.assertEqual(
            result.sha256_path.read_text(encoding="utf-8"),
            "%s  %s\n" % (actual_sha, result.output.name),
        )
        data = json.loads(result.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["package_root"], "max2025_menu_prototype")
        self.assertEqual(data["archive"], result.output.name)
        self.assertEqual(data["archive_sha256"], actual_sha)
        self.assertEqual(data["archive_size"], result.size)
        self.assertEqual(data["member_count"], len(result.members))
        self.assertEqual(
            [item["path"] for item in data["members"]],
            [member.path for member in result.members],
        )
        with zipfile.ZipFile(result.output) as archive:
            for item in data["members"]:
                payload = archive.read(
                    "max2025_menu_prototype/" + item["path"]
                )
                self.assertEqual(item["size"], len(payload))
                self.assertEqual(item["sha256"], portable._sha256_bytes(payload))
        text = result.manifest_path.read_text(encoding="utf-8")
        self.assertNotIn(str(self.root), text)
        self.assertNotIn(str(self.source), text)

    def test_launcher_ms_member_matches_source_exactly(self):
        result = self._build()
        relative = "tools/max_pull_and_load.ms"
        source_bytes = (self.source / relative).read_bytes()
        with zipfile.ZipFile(result.output) as archive:
            payload = archive.read("max2025_menu_prototype/" + relative)
        self.assertEqual(payload, source_bytes)
        member = next(item for item in result.members if item.path == relative)
        self.assertEqual(member.size, len(source_bytes))
        self.assertEqual(member.sha256, portable._sha256_bytes(source_bytes))

    def test_rebuild_is_byte_deterministic(self):
        first = self._build("first.zip")
        second = self._build("second.zip")
        self.assertEqual(first.output.read_bytes(), second.output.read_bytes())
        self.assertEqual(first.sha256, second.sha256)
        self.assertEqual(first.members, second.members)

    def test_extracted_package_imports_without_development_checkout(self):
        result = self._build()
        extract_root = self.root / "extracted"
        extract_root.mkdir()
        with zipfile.ZipFile(result.output) as archive:
            archive.extractall(extract_root)
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        completed = subprocess.run(
            [sys.executable, "-c", "import max2025_menu_prototype as m; print(m.MARKER)"],
            cwd=extract_root,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "fixture-package")

    def test_spaces_and_non_ascii_paths_work(self):
        source = self.root / "sp ace-é" / "max2025_menu_prototype"
        source.mkdir(parents=True)
        for path in self.source.rglob("*"):
            if path.is_file():
                target = source / path.relative_to(self.source)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(path.read_bytes())
        output = self.root / "deliver ables-é" / "package.zip"
        result = portable.build_portable_zip(source, output)
        self.assertTrue(result.output.is_file())
        self.assertTrue(result.sha256_path.is_file())
        self.assertTrue(result.manifest_path.is_file())


class ExclusionTests(PortableFixture):
    def test_caches_git_evidence_archives_and_editor_files_are_excluded(self):
        self._write(".git/config", "secret\n")
        self._write("__pycache__/module.cpython-314.pyc", "bytecode")
        self._write("docs/__pycache__/x.pyc", "bytecode")
        self._write("docs/.pytest_cache/state", "cache")
        self._write("docs/raw.log", "private log")
        self._write("docs/screenshot.png", "not a real png")
        self._write("docs/source.zip", "archive")
        self._write("docs/notes.txt~", "backup")
        self._write("docs/old.py.bak", "backup")
        self._write("docs/.DS_Store", "editor")
        result = self._build()
        with zipfile.ZipFile(result.output) as archive:
            names = archive.namelist()
        joined = "\n".join(names)
        for forbidden in (
            ".git/",
            "__pycache__",
            ".pytest_cache",
            ".pyc",
            "raw.log",
            "screenshot.png",
            "source.zip",
            "notes.txt~",
            "old.py.bak",
            ".DS_Store",
        ):
            self.assertNotIn(forbidden, joined)

    def test_hidden_suffix_files_are_pruned_and_visible_sources_kept(self):
        self._write("docs/.credentials.json", '{"token": "secret"}')
        self._write("docs/.hidden.py", "SECRET = 1\n")
        self._write("docs/.hidden.md", "# private\n")
        self._write("tests/.hidden_test.py", "")
        result = self._build()
        with zipfile.ZipFile(result.output) as archive:
            names = archive.namelist()
        joined = "\n".join(names)
        for forbidden in (
            "docs/.credentials.json",
            "docs/.hidden.py",
            "docs/.hidden.md",
            "tests/.hidden_test.py",
        ):
            self.assertNotIn(forbidden, joined)
        for preserved in (
            "core/default_profile.json",
            "docs/guide.md",
            "tests/test_example.py",
            "max_host/example.py",
            "qt_ui/example.py",
            "sandbox/static/app.js",
            "sandbox/static/index.html",
            "sandbox/static/style.css",
            "sandbox/static/favicon.svg",
        ):
            self.assertIn("max2025_menu_prototype/" + preserved, names)

    def test_unexpected_file_outside_the_allowlist_fails(self):
        self._write("docs/private.txt", "should not be packaged")
        with self.assertRaises(portable.PortableError):
            self._build()

    def test_unexpected_top_level_entry_fails(self):
        (self.source / "scratch").mkdir()
        with self.assertRaises(portable.PortableError):
            self._build()

    def test_other_maxscript_files_are_rejected(self):
        for name in (
            "tools/other.ms",
            "tools/max_pull_and_load_extra.ms",
            "tools/MAX_PULL_AND_LOAD.MS",
        ):
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('python.execute "print(1)"\n', encoding="utf-8")
            try:
                with self.assertRaises(portable.PortableError, msg=name):
                    self._build()
            finally:
                path.unlink()


class SafetyTests(PortableFixture):
    def test_symlinked_file_is_rejected(self):
        target = self.root / "outside.md"
        target.write_text("outside\n", encoding="utf-8")
        os.symlink(target, self.source / "docs" / "link.md")
        with self.assertRaises(portable.PortableError):
            self._build()

    def test_symlinked_directory_is_rejected(self):
        target = self.root / "outside-dir"
        target.mkdir()
        os.symlink(target, self.source / "docs" / "link-dir")
        with self.assertRaises(portable.PortableError):
            self._build()

    def test_symlinked_source_root_is_rejected(self):
        link = self.root / "linked-source"
        os.symlink(self.source, link)
        with self.assertRaises(portable.PortableError):
            portable.build_portable_zip(link, self.root / "out.zip")

    def test_case_insensitive_collision_is_rejected(self):
        self._write("docs/Guide.md", "# A\n")
        self._write("docs/guide.md", "# B\n")
        with self.assertRaises(portable.PortableError):
            self._build()

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires os.mkfifo")
    def test_special_file_is_rejected(self):
        os.mkfifo(self.source / "docs" / "pipe.md")
        with self.assertRaises(portable.PortableError):
            self._build()

    def test_backslash_name_is_rejected(self):
        (self.source / "docs" / "bad\\name.md").write_text("x\n", encoding="utf-8")
        with self.assertRaises(portable.PortableError):
            self._build()

    def test_traversal_path_validation_is_rejected(self):
        from pathlib import PurePosixPath

        for value in ("../escape.md", "/absolute.md", "a/../b.md", ""):
            with self.assertRaises(portable.PortableError):
                portable._validate_relative(PurePosixPath(value))


class DestinationTests(PortableFixture):
    def test_output_inside_source_is_refused(self):
        with self.assertRaises(portable.PortableError):
            portable.build_portable_zip(self.source, self.source / "out.zip")
        with self.assertRaises(portable.PortableError):
            portable.build_portable_zip(self.source, self.source / "core" / "out.zip")

    def test_existing_output_is_not_overwritten(self):
        output = self.root / "deliverables" / "existing.zip"
        output.parent.mkdir(parents=True)
        output.write_bytes(b"keep me")
        with self.assertRaises(portable.PortableError):
            portable.build_portable_zip(self.source, output)
        self.assertEqual(output.read_bytes(), b"keep me")

    def test_existing_sidecar_is_not_overwritten(self):
        output = self.root / "deliverables" / "sidecar.zip"
        output.parent.mkdir(parents=True)
        (output.parent / (output.name + ".sha256")).write_text("keep\n")
        with self.assertRaises(portable.PortableError):
            portable.build_portable_zip(self.source, output)
        self.assertFalse(output.exists())

    def test_non_zip_output_is_refused(self):
        with self.assertRaises(portable.PortableError):
            portable.build_portable_zip(self.source, self.root / "out.tar")

    def test_file_as_output_parent_is_refused(self):
        parent = self.root / "not-a-dir"
        parent.write_text("x")
        with self.assertRaises(portable.PortableError):
            portable.build_portable_zip(self.source, parent / "out.zip")


class CliTests(PortableFixture):
    def test_cli_builds_and_returns_success(self):
        output = self.root / "deliverables" / "cli.zip"
        self.assertEqual(
            portable.main(["--source", str(self.source), "--output", str(output)]),
            0,
        )
        self.assertTrue(output.is_file())
        self.assertTrue((output.parent / (output.name + ".sha256")).is_file())

    def test_cli_refuses_unsafe_output(self):
        self.assertEqual(
            portable.main(
                ["--source", str(self.source), "--output", str(self.source / "x.zip")]
            ),
            2,
        )


if __name__ == "__main__":
    unittest.main()

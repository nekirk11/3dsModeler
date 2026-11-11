"""AST and call-spy proof that V2 data can never choose executable code."""

from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from max2025_menu_prototype.core.atlas import (
    AtlasError,
    OverlayError,
    load_atlas,
    validate_atlas,
    validate_overlay,
)
from max2025_menu_prototype.core.maneuver_contracts import (
    REQUIRED_MANEUVER_IDS,
    TARGETS_BY_KIND,
    ExecutionDescriptor,
)
from max2025_menu_prototype.max_host.maneuver_dispatch import (
    contract_parity,
    dispatch,
    handler_for,
    missing_handlers,
)
from max2025_menu_prototype.max_host.preparation import PreparedTarget

CHECKOUT_ROOT = Path(__file__).resolve().parents[1]
ATLAS_PATH = CHECKOUT_ROOT / "core" / "maneuvers.json"

V2_MODULES = (
    "core/atlas.py",
    "core/search.py",
    "core/palette_controller.py",
    "core/maneuver_simulated.py",
    "core/maneuver_contracts.py",
    "max_host/maneuver_dispatch.py",
    "max_host/preparation.py",
    "max_host/adapter.py",
)

FORBIDDEN_CALLS = frozenset({"eval", "exec", "compile", "__import__"})
FORBIDDEN_IMPORTS = frozenset(
    {"importlib", "subprocess", "runpy", "pickle", "marshal", "ctypes"}
)


def raw_atlas() -> dict:
    return json.loads(ATLAS_PATH.read_text(encoding="utf-8"))


def prepared_target() -> PreparedTarget:
    return PreparedTarget(
        ok=True,
        status="prepared",
        tier="ready",
        node=object(),
        base=object(),
        ep=object(),
        context="object",
        selection_count=1,
    )


class AstSecurityTests(unittest.TestCase):
    def test_v2_data_modules_never_call_eval_exec_compile_or_import(self) -> None:
        for relative in V2_MODULES:
            source = CHECKOUT_ROOT / relative
            tree = ast.parse(source.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    with self.subTest(module=relative, line=node.lineno):
                        self.assertNotIn(node.func.id, FORBIDDEN_CALLS)
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = (
                        [alias.name.split(".")[0] for alias in node.names]
                        if isinstance(node, ast.Import)
                        else [(node.module or "").split(".")[0]]
                    )
                    for name in names:
                        with self.subTest(module=relative, imported=name):
                            self.assertNotIn(name, FORBIDDEN_IMPORTS)

    def test_dispatch_never_reads_api_or_doc_url(self) -> None:
        source = (CHECKOUT_ROOT / "max_host" / "maneuver_dispatch.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in ("api", "doc_url"):
                self.fail("dispatch reads display metadata at line %d" % node.lineno)

    def test_v2_modules_never_import_network_clients_at_runtime(self) -> None:
        for relative in V2_MODULES:
            tree = ast.parse((CHECKOUT_ROOT / relative).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = (
                        [alias.name for alias in node.names]
                        if isinstance(node, ast.Import)
                        else [node.module or ""]
                    )
                    for name in names:
                        root_name = name.split(".")[0]
                        if root_name == "urllib":
                            allowed = name == "urllib.parse"
                        else:
                            allowed = root_name not in {
                                "http",
                                "requests",
                                "socket",
                            }
                        with self.subTest(module=relative, imported=name):
                            self.assertTrue(allowed)


class RegistryParityTests(unittest.TestCase):
    def test_all_36_ids_and_both_registries_agree(self) -> None:
        atlas = load_atlas(CHECKOUT_ROOT).atlas
        self.assertEqual(len(REQUIRED_MANEUVER_IDS), 36)
        self.assertEqual(atlas.ids, REQUIRED_MANEUVER_IDS)
        self.assertEqual(set(atlas.ids), set(REQUIRED_MANEUVER_IDS))
        self.assertFalse(contract_parity())
        self.assertFalse(missing_handlers())
        for kind, targets in TARGETS_BY_KIND.items():
            for target in targets:
                with self.subTest(kind=kind, target=target):
                    self.assertIsNotNone(handler_for(kind, target))

    def test_every_atlas_descriptor_has_a_reviewed_handler(self) -> None:
        atlas = load_atlas(CHECKOUT_ROOT).atlas
        for spec in atlas.maneuvers:
            for entry in spec.contexts.values():
                for descriptor in (
                    entry.max2025.with_selection,
                    entry.max2025.empty_selection,
                ):
                    if descriptor is None:
                        continue
                    with self.subTest(maneuver=spec.id, target=descriptor.target):
                        self.assertIn(
                            descriptor.target, TARGETS_BY_KIND[descriptor.kind]
                        )
                        self.assertIsNotNone(
                            handler_for(descriptor.kind, descriptor.target)
                        )


class MaliciousDataTests(unittest.TestCase):
    def _atlas_with_descriptor(self, **overrides) -> dict:
        data = raw_atlas()
        descriptor = data["maneuvers"][0]["contexts"]["edge"]["max2025"][
            "with_selection"
        ]
        descriptor.update(overrides)
        return data

    def test_malicious_kind_and_target_are_rejected_by_the_loader(self) -> None:
        for overrides in (
            {"kind": "max_command; __import__('os').system('calc')"},
            {"kind": "shell"},
            {"kind": "max_command", "target": "os.system"},
            {"kind": "caddy", "target": "Chamfer; exec('x')"},
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(AtlasError):
                    validate_atlas(self._atlas_with_descriptor(**overrides))

    def test_malicious_api_text_is_bounded_display_metadata_only(self) -> None:
        hostile = "__import__('os').system('calc')"
        descriptor = ExecutionDescriptor(
            kind="max_command",
            target="move",
            api=hostile,
            doc_url=(
                "https://help.autodesk.com/cloudhelp/2025/ENU/MAXScript-Help/"
                "files/3ds-Max-Objects-and-Interfaces/Editable-Meshes-Splines-"
                "Patches/Editable_Poly/GUID-90843EC5-AE3A-43EB-9406-A3631DAEADEF.html"
            ),
        )
        runtime = SimpleNamespace(execute=mock.Mock())
        with mock.patch("builtins.exec", side_effect=AssertionError("exec")):
            outcome = dispatch(
                runtime,
                descriptor,
                prepared_target(),
                "object",
            )
        self.assertEqual(outcome.status, "launched")
        runtime.execute.assert_called_once_with("max move")

    def test_control_characters_and_oversize_api_are_rejected(self) -> None:
        for api in ("bad\x00api", "x" * 257):
            with self.subTest(api=api[:8]):
                with self.assertRaises(Exception):
                    ExecutionDescriptor(
                        kind="max_command",
                        target="move",
                        api=api,
                        doc_url=(
                            "https://help.autodesk.com/cloudhelp/2025/ENU/"
                            "MAXScript-Help/files/x.html"
                        ),
                    )

    def test_malicious_aliases_and_overlay_fields_are_rejected(self) -> None:
        data = raw_atlas()
        data["maneuvers"][0]["aliases"]["blender"] = ["ok", "bad\x00alias"]
        with self.assertRaises(AtlasError):
            validate_atlas(data)

        for overlay in (
            {"schema_version": 1, "aliases": {"bevel": ["bad\x00alias"]}},
            {"schema_version": 1, "aliases": {"not_a_real_id": ["x"]}},
            {"schema_version": 1, "hidden": ["bevel"], "targets": {"bevel": "x"}},
            {"schema_version": True, "aliases": {}},
            {"schema_version": 1, "aliases": {"bevel": [{"nested": "object"}]}},
        ):
            with self.subTest(overlay=overlay):
                with self.assertRaises(OverlayError):
                    validate_overlay(overlay, REQUIRED_MANEUVER_IDS)

    def test_unknown_dispatch_target_blocks_before_any_native_call(self) -> None:
        runtime = SimpleNamespace(execute=mock.Mock())
        for descriptor in (
            {"kind": "max_command", "target": "shell"},
            {"kind": "eval", "target": "move"},
            {"kind": None, "target": None},
        ):
            with self.subTest(descriptor=descriptor):
                outcome = dispatch(runtime, descriptor, prepared_target(), "object")
                self.assertEqual(outcome.status, "blocked")
        runtime.execute.assert_not_called()

    def test_urls_are_never_fetched_while_loading_or_dispatching(self) -> None:
        runtime = SimpleNamespace(execute=mock.Mock())
        with mock.patch(
            "urllib.request.urlopen", side_effect=AssertionError("network")
        ):
            atlas = load_atlas(CHECKOUT_ROOT).atlas
            dispatch(
                runtime,
                {
                    "kind": "max_command",
                    "target": "move",
                    "api": "display only",
                    "doc_url": "https://example.invalid/never",
                },
                prepared_target(),
                "object",
            )
        self.assertEqual(atlas.ids, REQUIRED_MANEUVER_IDS)
        runtime.execute.assert_called_once_with("max move")


if __name__ == "__main__":
    unittest.main()

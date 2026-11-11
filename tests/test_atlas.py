"""Atlas, overlay, descriptor-registry and V1-context translation tests."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from max2025_menu_prototype.core import atlas as atlas_module
from max2025_menu_prototype.core.atlas import (
    OVERLAY_WARNING,
    AtlasError,
    OverlayError,
    load_atlas,
    merge_overlay,
    validate_atlas,
    validate_overlay,
)
from max2025_menu_prototype.core.contracts import ContextLevel
from max2025_menu_prototype.core.maneuver_contracts import (
    MANEUVER_CONTEXTS,
    REQUIRED_MANEUVER_IDS,
    TARGETS_BY_KIND,
    DccReference,
    ExecutionDescriptor,
    ManeuverContext,
    from_legacy_context,
    to_legacy_context,
)

CHECKOUT_ROOT = Path(__file__).resolve().parents[1]
ATLAS_PATH = CHECKOUT_ROOT / "core" / "maneuvers.json"
EP_URL = (
    "https://help.autodesk.com/cloudhelp/2025/ENU/MAXScript-Help/files/"
    "3ds-Max-Objects-and-Interfaces/Editable-Meshes-Splines-Patches/"
    "Editable_Poly/GUID-90843EC5-AE3A-43EB-9406-A3631DAEADEF.html"
)


def raw_atlas() -> dict:
    return json.loads(ATLAS_PATH.read_text(encoding="utf-8"))


class BundledAtlasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.result = load_atlas(CHECKOUT_ROOT)
        cls.atlas = cls.result.atlas

    def test_all_36_frozen_ids_in_declaration_order(self):
        self.assertEqual(self.result.warnings, ())
        self.assertEqual(self.atlas.schema_version, 2)
        self.assertEqual(self.atlas.ids, REQUIRED_MANEUVER_IDS)
        self.assertEqual(len(self.atlas.ids), 36)
        self.assertEqual(len(set(self.atlas.ids)), 36)

    def test_every_context_and_cross_dcc_alias_source_exists(self):
        for maneuver_id, expected in MANEUVER_CONTEXTS.items():
            with self.subTest(maneuver=maneuver_id):
                spec = self.atlas.by_id(maneuver_id)
                self.assertIsNotNone(spec)
                self.assertEqual(set(spec.contexts), set(expected))
                self.assertEqual(
                    set(spec.aliases),
                    {"houdini_modeler", "blender", "maya", "max2025"},
                )
                for source in spec.aliases:
                    self.assertTrue(spec.aliases[source])

    def test_every_max_descriptor_uses_the_fixed_target_registry(self):
        for spec in self.atlas.maneuvers:
            for context, entry in spec.contexts.items():
                for descriptor in (
                    entry.max2025.with_selection,
                    entry.max2025.empty_selection,
                ):
                    if descriptor is None:
                        continue
                    with self.subTest(maneuver=spec.id, context=context.value):
                        self.assertIn(descriptor.kind, TARGETS_BY_KIND)
                        self.assertIn(
                            descriptor.target, TARGETS_BY_KIND[descriptor.kind]
                        )
                        self.assertTrue(descriptor.api)
                        self.assertTrue(
                            descriptor.doc_url.startswith(
                                "https://help.autodesk.com/cloudhelp/"
                            )
                        )
                        self.assertIsInstance(entry.houdini, DccReference)
                        self.assertIsInstance(entry.blender, DccReference)

    def test_atlas_and_nested_mappings_are_immutable(self):
        with self.assertRaises((TypeError, AttributeError)):
            self.atlas.maneuvers = ()
        spec = self.atlas.by_id("bevel")
        with self.assertRaises(TypeError):
            spec.aliases["blender"] = ("changed",)
        with self.assertRaises(TypeError):
            spec.contexts[ManeuverContext.OBJECT] = spec.contexts[
                ManeuverContext.EDGE
            ]
        with self.assertRaises(TypeError):
            self.atlas.user_aliases["bevel"] = ("changed",)


class ContextTranslationTests(unittest.TestCase):
    def test_face_and_polygon_are_the_only_translated_pair(self):
        for context in ManeuverContext:
            with self.subTest(context=context.value):
                legacy = to_legacy_context(context)
                self.assertIs(from_legacy_context(legacy), context)
        self.assertIs(to_legacy_context(ManeuverContext.FACE), ContextLevel.POLYGON)
        self.assertIs(from_legacy_context(ContextLevel.POLYGON), ManeuverContext.FACE)
        self.assertIs(to_legacy_context("edge"), ContextLevel.EDGE)
        self.assertIs(from_legacy_context("vertex"), ManeuverContext.VERTEX)
        for invalid in ("polygon", "objectt", ""):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    ManeuverContext(invalid)

    def test_v1_polygon_is_not_accepted_in_the_v2_json_vocabulary(self):
        with self.assertRaises(ValueError):
            validate_atlas(
                {
                    "schema_version": 2,
                    "maneuvers": [
                        {
                            "id": "bevel",
                            "label": "Bevel",
                            "group": "Modeling",
                            "aliases": {
                                "houdini_modeler": ["bevel"],
                                "blender": ["bevel"],
                                "maya": ["bevel"],
                                "max2025": ["bevel"],
                            },
                            "contexts": {"polygon": {}},
                        }
                    ],
                }
            )


class DescriptorTests(unittest.TestCase):
    def test_descriptor_rejects_unknown_kind_or_target(self):
        with self.assertRaises(ValueError):
            ExecutionDescriptor("teleport", "Chamfer", "x", EP_URL)
        with self.assertRaises(ValueError):
            ExecutionDescriptor("caddy", "NotARealCaddy", "x", EP_URL)
        with self.assertRaises(ValueError):
            ExecutionDescriptor("caddy", "Chamfer", "x", "http://example.com/doc")
        with self.assertRaises(ValueError):
            ExecutionDescriptor("caddy", "Chamfer", "x", "https://example.com/doc")
        with self.assertRaises(ValueError):
            DccReference("SOP polybevel", "http://www.sidefx.com/docs/x")

    def test_metadata_never_changes_dispatch_identity(self):
        first = ExecutionDescriptor(
            "caddy", "Chamfer", "EditablePoly.PopupDialog #Chamfer", EP_URL
        )
        second = ExecutionDescriptor(
            "caddy", "Chamfer", "__import__('os').system('echo owned')", EP_URL
        )
        self.assertEqual(first.kind, second.kind)
        self.assertEqual(first.target, second.target)
        self.assertNotEqual(first.api, second.api)

    def test_target_registry_has_exact_kind_keys(self):
        self.assertEqual(
            set(TARGETS_BY_KIND),
            {
                "caddy",
                "command_mode",
                "button_op",
                "polyop",
                "max_command",
                "composite",
                "context_switch",
            },
        )
        self.assertIn("Weld", TARGETS_BY_KIND["caddy"])
        self.assertIn("MSmooth", TARGETS_BY_KIND["caddy"])
        self.assertIn("SwiftLoop", TARGETS_BY_KIND["command_mode"])
        self.assertIn("clean_dissolve_edges", TARGETS_BY_KIND["composite"])


class AtlasValidationTests(unittest.TestCase):
    def _assert_invalid(self, payload: dict) -> None:
        with self.assertRaises(AtlasError):
            validate_atlas(payload)

    def test_rejects_malformed_roots_fields_and_versions(self):
        cases = []

        payload = raw_atlas()
        payload["schema_version"] = True
        cases.append(("bool version", payload))

        payload = raw_atlas()
        payload["schema_version"] = 3
        cases.append(("wrong version", payload))

        payload = raw_atlas()
        payload["extra"] = 1
        cases.append(("unknown root key", payload))

        payload = raw_atlas()
        payload["maneuvers"] = []
        cases.append(("empty maneuvers", payload))

        payload = raw_atlas()
        payload["maneuvers"][0]["id"] = "not_a_frozen_maneuver"
        cases.append(("unknown ID", payload))

        payload = raw_atlas()
        payload["maneuvers"].pop()
        cases.append(("missing ID", payload))

        payload = raw_atlas()
        payload["maneuvers"][0]["label"] = "bad\x00label"
        cases.append(("control character", payload))

        payload = raw_atlas()
        payload["maneuvers"][0]["group"] = "NotAGroup"
        cases.append(("bad group", payload))

        payload = raw_atlas()
        payload["maneuvers"][0]["aliases"]["python"] = ["x"]
        cases.append(("unknown alias source", payload))

        payload = raw_atlas()
        payload["maneuvers"][0]["contexts"]["polygon"] = payload["maneuvers"][0][
            "contexts"
        ]["vertex"]
        cases.append(("polygon synonym", payload))

        payload = raw_atlas()
        entry = payload["maneuvers"][0]["contexts"]["vertex"]["max2025"][
            "with_selection"
        ]
        entry["kind"] = "shell"
        cases.append(("unknown kind", payload))

        payload = raw_atlas()
        entry = payload["maneuvers"][0]["contexts"]["vertex"]["max2025"][
            "with_selection"
        ]
        entry["target"] = "NotATarget"
        cases.append(("unknown target", payload))

        payload = raw_atlas()
        entry = payload["maneuvers"][0]["contexts"]["vertex"]["max2025"][
            "with_selection"
        ]
        entry["doc_url"] = "https://example.com/not-autodesk"
        cases.append(("non-Autodesk URL", payload))

        payload = raw_atlas()
        payload["maneuvers"][0]["contexts"]["vertex"]["houdini"]["doc_url"] = (
            "https://docs.blender.org/api/current/bpy.ops.mesh.html"
        )
        cases.append(("wrong DCC host", payload))

        for label, payload in cases:
            with self.subTest(label):
                self._assert_invalid(payload)

    def test_rejects_nonfinite_json_and_duplicate_keys(self):
        with self.assertRaises(AtlasError):
            atlas_module._decode_json(
                b'{"schema_version": NaN}', limit=100, label="test"
            )
        with self.assertRaises(AtlasError):
            atlas_module._decode_json(
                b'{"schema_version": 2, "schema_version": 2}',
                limit=100,
                label="test",
            )

    def test_accepts_the_bundled_atlas_bytes(self):
        payload = json.loads(ATLAS_PATH.read_text(encoding="utf-8"))
        atlas = validate_atlas(payload)
        self.assertEqual(atlas.ids, REQUIRED_MANEUVER_IDS)


class OverlayValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.atlas = load_atlas(CHECKOUT_ROOT).atlas
        self.overlay = validate_overlay(
            {
                "schema_version": 1,
                "aliases": {"bevel": ["rounded edge", "my bevel"]},
                "pinned": ["bevel", "fill"],
                "hidden": ["fill", "quick_slice"],
            },
            self.atlas.ids,
        )

    def test_merge_extends_aliases_without_changing_routes_or_labels(self):
        merged = merge_overlay(self.atlas, self.overlay)
        self.assertIsNot(merged, self.atlas)
        self.assertEqual(merged.by_id("bevel").label, self.atlas.by_id("bevel").label)
        self.assertEqual(
            merged.by_id("bevel").contexts, self.atlas.by_id("bevel").contexts
        )
        self.assertEqual(
            merged.user_aliases["bevel"], ("rounded edge", "my bevel")
        )
        self.assertIn("rounded edge", merged.aliases_for("bevel"))
        # The original atlas is untouched.
        self.assertNotIn("bevel", self.atlas.user_aliases)

    def test_hidden_wins_over_pinned_and_dedupes_casefolded_aliases(self):
        overlay = validate_overlay(
            {
                "schema_version": 1,
                "aliases": {"bevel": ["BEVEL", "Chamfered Edge"]},
                "pinned": ["bevel"],
                "hidden": ["bevel"],
            },
            self.atlas.ids,
        )
        merged = merge_overlay(self.atlas, overlay)
        self.assertTrue(merged.is_hidden("bevel"))
        self.assertNotIn("bevel", merged.pinned)
        # "BEVEL" collapses against the shipped alias; the new text survives.
        self.assertEqual(merged.user_aliases["bevel"], ("Chamfered Edge",))

    def test_overlay_rejects_unknown_fields_ids_and_types(self):
        cases = [
            {"aliases": {}},
            {"schema_version": 2},
            {"schema_version": True},
            {"schema_version": 1, "unknown": []},
            {"schema_version": 1, "aliases": {"not_a_real_id": ["x"]}},
            {"schema_version": 1, "aliases": {"bevel": "not-a-list"}},
            {"schema_version": 1, "pinned": ["nope"]},
            {"schema_version": 1, "pinned": ["bevel", "bevel"]},
            {"schema_version": 1, "hidden": ["bevel", "bevel"]},
            {"schema_version": 1, "aliases": {"bevel": [""]}},
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                with self.assertRaises(OverlayError):
                    validate_overlay(payload, self.atlas.ids)

    def test_overlay_rejects_duplicate_normalized_aliases(self):
        with self.assertRaises(OverlayError):
            validate_overlay(
                {
                    "schema_version": 1,
                    "aliases": {"bevel": ["round edge", "ROUND EDGE"]},
                },
                self.atlas.ids,
            )


class OverlayFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.bundled = load_atlas(CHECKOUT_ROOT).atlas

    def tearDown(self) -> None:
        self._temp.cleanup()

    def _write(self, text: str) -> Path:
        path = self.root / "user_aliases.json"
        path.write_text(text, encoding="utf-8")
        return path

    def test_missing_overlay_is_normal(self):
        result = load_atlas(self.root)
        self.assertEqual(result.warnings, ())
        self.assertEqual(result.atlas.ids, REQUIRED_MANEUVER_IDS)

    def test_valid_overlay_is_applied(self):
        self._write(
            '{"schema_version": 1, "aliases": {"bevel": ["round edge"]},'
            ' "pinned": ["bevel"], "hidden": ["quick_slice"]}'
        )
        result = load_atlas(self.root)
        self.assertEqual(result.warnings, ())
        self.assertIn("round edge", result.atlas.aliases_for("bevel"))
        self.assertTrue(result.atlas.is_hidden("quick_slice"))

    def test_invalid_overlay_falls_back_to_the_entire_bundled_atlas(self):
        invalid_cases = (
            "not json",
            '{"schema_version": 2}',
            '{"schema_version": 1, "aliases": {"unknown": ["x"]}}',
            '{"schema_version": 1, "schema_version": 1}',
            '{"schema_version": 1, "aliases": {"bevel": [NaN]}}',
        )
        for text in invalid_cases:
            with self.subTest(text=text):
                self._write(text)
                result = load_atlas(self.root)
                self.assertEqual(result.warnings, (OVERLAY_WARNING,))
                self.assertEqual(result.atlas, self.bundled)
                self.assertEqual(result.atlas.user_aliases, {})

    def test_oversized_overlay_is_ignored_without_partial_merge(self):
        self._write("{" + (" " * (256 * 1024)) + "}")
        result = load_atlas(self.root)
        self.assertEqual(result.warnings, (OVERLAY_WARNING,))
        self.assertEqual(result.atlas, self.bundled)

    @unittest.skipUnless(hasattr(os, "symlink"), "requires os.symlink")
    def test_symlinked_overlay_is_ignored(self):
        target = self.root / "outside.json"
        target.write_text('{"schema_version": 1}', encoding="utf-8")
        os.symlink(target, self.root / "user_aliases.json")
        result = load_atlas(self.root)
        self.assertEqual(result.warnings, (OVERLAY_WARNING,))
        self.assertEqual(result.atlas, self.bundled)

    def test_overlay_path_is_the_resolved_checkout_root_not_cwd(self):
        self._write('{"schema_version": 1, "aliases": {"bevel": ["round edge"]}}')
        previous = Path.cwd()
        with tempfile.TemporaryDirectory() as neutral:
            try:
                os.chdir(neutral)
                result = load_atlas(self.root)
            finally:
                os.chdir(previous)
        self.assertEqual(result.warnings, ())
        self.assertIn("round edge", result.atlas.aliases_for("bevel"))


if __name__ == "__main__":
    unittest.main()

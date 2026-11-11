"""Focused tests for frozen parameter schemas and validation."""

from __future__ import annotations

import math
import unittest

from max2025_menu_prototype.core.catalog import ACTIONS_BY_ID
from max2025_menu_prototype.core.contracts import ContextLevel, ParameterSpec
from max2025_menu_prototype.core.parameters import (
    MAX_FLOAT_MAGNITUDE,
    ParameterError,
    parameter_schema,
    requires_parameters,
    validate_parameters,
)

POLYGON = ContextLevel.POLYGON
EDGE = ContextLevel.EDGE
VERTEX = ContextLevel.VERTEX
BORDER = ContextLevel.BORDER


class SchemaTests(unittest.TestCase):
    def spec(self, action_id: str, level: ContextLevel) -> dict[str, ParameterSpec]:
        return {item.name: item for item in parameter_schema(action_id, level)}

    def test_extrude_bevel_inset_schemas(self) -> None:
        extrude = self.spec("model.extrude", POLYGON)
        self.assertEqual(set(extrude), {"height", "extrusion_type"})
        self.assertEqual(extrude["height"].kind, "float")
        self.assertEqual(extrude["height"].default, 1.0)
        self.assertEqual(extrude["height"].unit, "system units")
        self.assertEqual(extrude["extrusion_type"].kind, "enum")
        self.assertEqual(
            extrude["extrusion_type"].choices,
            ("group", "local_normal", "by_polygon"),
        )

        bevel = self.spec("model.bevel", POLYGON)
        self.assertEqual(set(bevel), {"height", "outline", "extrusion_type"})
        self.assertEqual(bevel["outline"].default, -0.1)

        inset = self.spec("model.inset", POLYGON)
        self.assertEqual(set(inset), {"amount", "inset_type"})
        self.assertEqual(inset["inset_type"].choices, ("group", "by_polygon"))

    def test_chamfer_connect_bridge_weld_schemas(self) -> None:
        chamfer = self.spec("model.chamfer", EDGE)
        self.assertEqual(set(chamfer), {"amount", "open"})
        self.assertEqual(chamfer["open"].kind, "bool")
        self.assertFalse(chamfer["open"].default)

        edge_connect = self.spec("model.connect", EDGE)
        self.assertEqual(set(edge_connect), {"segments", "pinch", "slide"})
        self.assertEqual(edge_connect["segments"].minimum, 1)
        self.assertEqual(edge_connect["segments"].maximum, 100)
        self.assertEqual(self.spec("model.connect", VERTEX), {})

        bridge = self.spec("model.bridge", BORDER)
        self.assertEqual(set(bridge), {"segments", "taper", "bias", "twist"})
        self.assertEqual(bridge["segments"].kind, "int")
        self.assertEqual(bridge["taper"].kind, "float")
        self.assertEqual(bridge["bias"].kind, "float")
        # Autodesk documents bridgeTwist1/2 as Integer properties.
        self.assertEqual(bridge["twist"].kind, "int")
        self.assertEqual(bridge["twist"].minimum, -100)
        self.assertEqual(bridge["twist"].maximum, 100)

        weld = self.spec("model.weld", VERTEX)
        self.assertEqual(set(weld), {"threshold"})

    def test_destructive_schemas_require_confirmation(self) -> None:
        for action_id, level in (
            ("model.collapse", VERTEX),
            ("model.remove", EDGE),
            ("model.delete", POLYGON),
        ):
            with self.subTest(action_id=action_id):
                schema = self.spec(action_id, level)
                self.assertIn("confirm", schema)
                self.assertFalse(schema["confirm"].default)
        delete_vertex = self.spec("model.delete", VERTEX)
        self.assertEqual(set(delete_vertex), {"confirm", "cleanup"})
        self.assertTrue(delete_vertex["cleanup"].default)

    def test_parameter_free_actions_and_levels(self) -> None:
        for action_id, level in (
            ("selection.grow", VERTEX),
            ("selection.clear", EDGE),
            ("model.cap", BORDER),
            ("model.split_edges", EDGE),
            ("model.break_vertices", VERTEX),
            ("model.planar_x", POLYGON),
            ("model.flip_normals", POLYGON),
            ("context.vertex", POLYGON),
            ("model.cut", EDGE),
            ("model.target_weld", VERTEX),
        ):
            with self.subTest(action_id=action_id):
                self.assertEqual(parameter_schema(action_id, level), ())
                self.assertFalse(requires_parameters(action_id, level))

    def test_unknown_action_or_level_is_rejected(self) -> None:
        with self.assertRaises(ParameterError):
            parameter_schema("does.not.exist", POLYGON)
        with self.assertRaises(ParameterError):
            parameter_schema("model.extrude", EDGE)
        with self.assertRaises(ParameterError):
            parameter_schema("model.extrude", "not-a-level")

    def test_all_catalog_ids_have_a_schema_function(self) -> None:
        for spec in ACTIONS_BY_ID.values():
            for level in spec.allowed_levels:
                with self.subTest(action_id=spec.id, level=level.value):
                    schema = parameter_schema(spec.id, level)
                    self.assertIsInstance(schema, tuple)
                    for item in schema:
                        self.assertIsInstance(item, ParameterSpec)


class ValidationTests(unittest.TestCase):
    def test_valid_mappings_are_normalized(self) -> None:
        values = validate_parameters(
            "model.extrude",
            POLYGON,
            {"height": 2, "extrusion_type": "local_normal"},
        )
        self.assertEqual(values, {"height": 2.0, "extrusion_type": "local_normal"})
        self.assertIsInstance(values["height"], float)

        bevel = validate_parameters(
            "model.bevel",
            POLYGON,
            {"height": -1.5, "outline": 0.2, "extrusion_type": "group"},
        )
        self.assertEqual(bevel["height"], -1.5)

    def test_missing_extra_and_non_mapping_values_are_rejected(self) -> None:
        for values in (
            None,
            {},
            {"height": 1.0},
            {"height": 1.0, "extrusion_type": "group", "script": "x"},
            ["height", "type"],
        ):
            with self.subTest(values=values):
                with self.assertRaises(ParameterError):
                    validate_parameters("model.extrude", POLYGON, values)
        with self.assertRaises(ParameterError):
            validate_parameters("selection.grow", VERTEX, {"height": 1.0})

    def test_bool_int_and_float_type_rules(self) -> None:
        with self.assertRaises(ParameterError):
            validate_parameters(
                "model.extrude",
                POLYGON,
                {"height": True, "extrusion_type": "group"},
            )
        with self.assertRaises(ParameterError):
            validate_parameters(
                "model.connect", EDGE, {"segments": 1.0, "pinch": 0, "slide": 0}
            )
        with self.assertRaises(ParameterError):
            validate_parameters(
                "model.connect", EDGE, {"segments": True, "pinch": 0, "slide": 0}
            )
        for bad in (math.nan, math.inf, -math.inf, MAX_FLOAT_MAGNITUDE * 2):
            with self.subTest(bad=bad):
                with self.assertRaises(ParameterError):
                    validate_parameters(
                        "model.extrude",
                        POLYGON,
                        {"height": bad, "extrusion_type": "group"},
                    )

    def test_enum_members_are_exact_strings(self) -> None:
        for bad in ("Group", "local normal", "", 1):
            with self.subTest(bad=bad):
                with self.assertRaises(ParameterError):
                    validate_parameters(
                        "model.extrude",
                        POLYGON,
                        {"height": 1.0, "extrusion_type": bad},
                    )

    def test_ranges_zero_and_negative_rules(self) -> None:
        self.assertEqual(
            validate_parameters(
                "model.extrude",
                POLYGON,
                {"height": 0, "extrusion_type": "group"},
            )["height"],
            0.0,
        )
        for action, level, values in (
            ("model.inset", POLYGON, {"amount": 0, "inset_type": "group"}),
            ("model.inset", POLYGON, {"amount": -1, "inset_type": "group"}),
            ("model.chamfer", EDGE, {"amount": 0, "open": False}),
            ("model.chamfer", EDGE, {"amount": -1, "open": False}),
            ("model.weld", VERTEX, {"threshold": 0}),
            ("model.weld", VERTEX, {"threshold": -0.1}),
            ("model.connect", EDGE, {"segments": 0, "pinch": 0, "slide": 0}),
            ("model.connect", EDGE, {"segments": 101, "pinch": 0, "slide": 0}),
            ("model.connect", EDGE, {"segments": 1, "pinch": -101, "slide": 0}),
            ("model.connect", EDGE, {"segments": 1, "pinch": 0, "slide": 101}),
        ):
            with self.subTest(action=action, values=values):
                with self.assertRaises(ParameterError):
                    validate_parameters(action, level, values)

        self.assertEqual(
            validate_parameters(
                "model.bevel",
                POLYGON,
                {"height": -2.0, "outline": 0.5, "extrusion_type": "group"},
            )["height"],
            -2.0,
        )

    def test_bevel_rejects_only_both_zero(self) -> None:
        with self.assertRaises(ParameterError):
            validate_parameters(
                "model.bevel",
                POLYGON,
                {"height": 0, "outline": 0, "extrusion_type": "group"},
            )
        self.assertEqual(
            validate_parameters(
                "model.bevel",
                POLYGON,
                {"height": 0, "outline": 0.5, "extrusion_type": "group"},
            )["outline"],
            0.5,
        )

    def test_destructive_confirmation_is_enforced(self) -> None:
        for action, level in (
            ("model.collapse", VERTEX),
            ("model.delete", EDGE),
            ("model.remove", VERTEX),
        ):
            with self.subTest(action=action):
                with self.assertRaises(ParameterError):
                    validate_parameters(action, level, {"confirm": False})
                self.assertEqual(
                    validate_parameters(action, level, {"confirm": True}),
                    {"confirm": True},
                )
        delete_vertex = validate_parameters(
            "model.delete", VERTEX, {"confirm": True, "cleanup": False}
        )
        self.assertFalse(delete_vertex["cleanup"])
        with self.assertRaises(ParameterError):
            validate_parameters(
                "model.delete", VERTEX, {"confirm": True, "cleanup": 1}
            )

    def test_bridge_twist_is_an_integer_property(self) -> None:
        valid = validate_parameters(
            "model.bridge",
            BORDER,
            {"segments": 2, "taper": 0.0, "bias": 0.0, "twist": -7},
        )
        self.assertEqual(valid["twist"], -7)
        self.assertIsInstance(valid["twist"], int)
        for bad in (1.0, 1.5, True, False, "1", None):
            with self.subTest(bad=bad):
                with self.assertRaises(ParameterError):
                    validate_parameters(
                        "model.bridge",
                        BORDER,
                        {"segments": 1, "taper": 0.0, "bias": 0.0, "twist": bad},
                    )
        for bad in (-101, 101):
            with self.subTest(bad=bad):
                with self.assertRaises(ParameterError):
                    validate_parameters(
                        "model.bridge",
                        BORDER,
                        {"segments": 1, "taper": 0.0, "bias": 0.0, "twist": bad},
                    )

    def test_parameter_free_validation(self) -> None:
        self.assertEqual(validate_parameters("selection.grow", VERTEX, None), {})
        self.assertEqual(validate_parameters("selection.grow", VERTEX, {}), {})
        with self.assertRaises(ParameterError):
            validate_parameters("selection.grow", VERTEX, {"height": 1.0})


class ParameterSpecTests(unittest.TestCase):
    def test_spec_rejects_bad_declarations(self) -> None:
        with self.assertRaises(ValueError):
            ParameterSpec("x", "X", "text", 1)
        with self.assertRaises(ValueError):
            ParameterSpec("x", "X", "enum", "a", choices=())
        with self.assertRaises(ValueError):
            ParameterSpec("x", "X", "enum", "b", choices=("a",))
        with self.assertRaises(ValueError):
            ParameterSpec("x", "X", "bool", 0)
        with self.assertRaises(ValueError):
            ParameterSpec("x", "X", "int", True)
        with self.assertRaises(ValueError):
            ParameterSpec("x", "X", "float", 1.0, minimum=2, maximum=1)

    def test_spec_is_frozen(self) -> None:
        spec = ParameterSpec("x", "X", "float", 1.0)
        with self.assertRaises(Exception):
            spec.default = 2.0  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()

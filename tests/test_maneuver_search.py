"""Deterministic search, pivot, pin/hide and system-command tests."""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

from max2025_menu_prototype.core.atlas import load_atlas, merge_overlay, validate_overlay
from max2025_menu_prototype.core.contracts import Availability
from max2025_menu_prototype.core.maneuver_contracts import (
    REQUIRED_MANEUVER_IDS,
    ManeuverContext,
    SystemCommand,
)
from max2025_menu_prototype.core.search import search_maneuvers

CHECKOUT_ROOT = Path(__file__).resolve().parents[1]


def always_available(maneuver_id: str, pivot: ManeuverContext | None) -> Availability:
    return Availability(True)


class SearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.atlas = load_atlas(CHECKOUT_ROOT).atlas

    def search(self, query: str, context: ManeuverContext = ManeuverContext.EDGE, provider=always_available):
        return search_maneuvers(self.atlas, query, context, provider)

    def test_empty_query_returns_one_stable_row_per_maneuver(self):
        rows = self.search("")
        self.assertEqual(len(rows), 36)
        self.assertEqual({row.maneuver_id for row in rows}, set(REQUIRED_MANEUVER_IDS))
        self.assertEqual(len({row.row_id for row in rows}), len(rows))

    def test_active_context_rows_precede_every_pivot_row(self):
        rows = self.search("")
        active = [row for row in rows if row.pivot_context is None]
        pivots = [row for row in rows if row.pivot_context is not None]
        self.assertTrue(active)
        self.assertTrue(pivots)
        last_active = max(rows.index(row) for row in active)
        first_pivot = min(rows.index(row) for row in pivots)
        self.assertLess(last_active, first_pivot)
        for row in active:
            self.assertIn(ManeuverContext.EDGE, self.atlas.by_id(row.maneuver_id).contexts)

    def test_pinned_pivot_cannot_beat_an_active_row(self):
        overlay = validate_overlay(
            {"schema_version": 1, "pinned": ["inset"]}, self.atlas.ids
        )
        pinned_atlas = merge_overlay(self.atlas, overlay)
        rows = search_maneuvers(pinned_atlas, "", ManeuverContext.EDGE, always_available)
        positions = {row.maneuver_id: index for index, row in enumerate(rows)}
        self.assertEqual(rows[positions["inset"]].pivot_context, ManeuverContext.FACE)
        self.assertLess(positions["bevel"], positions["inset"])

    def test_pinned_rows_sort_before_unpinned_rows_inside_a_tier(self):
        overlay = validate_overlay(
            {"schema_version": 1, "pinned": ["frame_selection"]}, self.atlas.ids
        )
        pinned_atlas = merge_overlay(self.atlas, overlay)
        rows = search_maneuvers(
            pinned_atlas, "", ManeuverContext.OBJECT, always_available
        )
        positions = {row.maneuver_id: index for index, row in enumerate(rows)}
        self.assertLess(positions["frame_selection"], positions["move"])

    def test_hidden_rows_disappear_and_hidden_wins_over_pinned(self):
        overlay = validate_overlay(
            {
                "schema_version": 1,
                "pinned": ["bevel"],
                "hidden": ["bevel", "quick_slice"],
            },
            self.atlas.ids,
        )
        hidden_atlas = merge_overlay(self.atlas, overlay)
        self.assertNotIn("bevel", hidden_atlas.pinned)
        rows = search_maneuvers(hidden_atlas, "", ManeuverContext.EDGE, always_available)
        ids = {row.maneuver_id for row in rows}
        self.assertNotIn("bevel", ids)
        self.assertNotIn("quick_slice", ids)
        self.assertEqual(len(rows), 34)

    def test_fill_and_inset_pivots_use_documented_intent_preferences(self):
        rows = {row.maneuver_id: row for row in self.search("")}
        self.assertEqual(rows["fill"].pivot_context, ManeuverContext.BORDER)
        self.assertEqual(rows["inset"].pivot_context, ManeuverContext.FACE)
        self.assertIsNone(rows["bevel"].pivot_context)

    def test_other_pivots_use_fixed_context_order(self):
        rows = {row.maneuver_id: row for row in self.search("", ManeuverContext.OBJECT)}
        self.assertEqual(rows["bevel"].pivot_context, ManeuverContext.VERTEX)
        self.assertEqual(rows["delete"].pivot_context, ManeuverContext.VERTEX)
        self.assertEqual(rows["fill"].pivot_context, ManeuverContext.BORDER)

    def test_query_prefers_exact_label_and_alias(self):
        rows = self.search("bevel")
        self.assertEqual(rows[0].maneuver_id, "bevel")
        self.assertGreaterEqual(rows[0].score, rows[-1].score)
        alias_rows = self.search("round edge")
        self.assertEqual(alias_rows[0].maneuver_id, "bevel")

    def test_user_alias_extends_search(self):
        overlay = validate_overlay(
            {"schema_version": 1, "aliases": {"bevel": ["rounded edge"]}},
            self.atlas.ids,
        )
        merged = merge_overlay(self.atlas, overlay)
        rows = search_maneuvers(merged, "rounded", ManeuverContext.EDGE, always_available)
        self.assertEqual(rows[0].maneuver_id, "bevel")

    def test_unicode_casefold_and_whitespace_normalization(self):
        rows = self.search("  ＢＥＶＥＬ ")
        self.assertEqual(rows[0].maneuver_id, "bevel")
        self.assertEqual(rows[0].score, 100.0)

    def test_all_query_tokens_must_match(self):
        self.assertEqual(self.search("bevel extrude"), ())
        self.assertEqual(self.search("definitely-not-a-maneuver"), ())

    def test_unavailable_rows_remain_with_inline_reason(self):
        def provider(maneuver_id, pivot):
            if maneuver_id == "bevel":
                return Availability(False, "Select edges first.")
            return Availability(True)

        rows = self.search("", provider=provider)
        bevel = next(row for row in rows if row.maneuver_id == "bevel")
        self.assertFalse(bevel.enabled)
        self.assertEqual(bevel.reason, "Select edges first.")

    def test_availability_provider_failure_is_sanitized_and_deterministic(self):
        def broken(maneuver_id, pivot):
            raise RuntimeError("private host detail")

        first = self.search("", provider=broken)
        second = self.search("", provider=broken)
        self.assertEqual(first, second)
        self.assertTrue(all(not row.enabled for row in first))
        self.assertTrue(all(row.reason == "Availability is unavailable." for row in first))

    def test_system_prefix_lists_exactly_three_commands(self):
        rows = self.search(">")
        self.assertEqual(
            [row.label for row in rows],
            ["> Pull Updates (Git)", "> Diagnostics", "> Unload UI"],
        )
        self.assertEqual(
            [row.system_command for row in rows],
            [
                SystemCommand.PULL_UPDATES,
                SystemCommand.DIAGNOSTICS,
                SystemCommand.UNLOAD_UI,
            ],
        )
        self.assertTrue(all(row.maneuver_id is None for row in rows))
        self.assertTrue(all(row.row_id.startswith("system:") for row in rows))

    def test_system_prefix_filters_labels_and_aliases(self):
        self.assertEqual(
            [row.system_command for row in self.search("> diag")],
            [SystemCommand.DIAGNOSTICS],
        )
        self.assertEqual(
            [row.system_command for row in self.search("> git pull")],
            [SystemCommand.PULL_UPDATES],
        )
        self.assertEqual(self.search("> rm -rf /"), ())

    def test_normal_search_finds_systems_below_maneuvers(self):
        rows = self.search("pull")
        self.assertEqual([row.system_command for row in rows], [SystemCommand.PULL_UPDATES])
        self.assertIsNotNone(rows[0].system_command)

    def test_empty_normal_query_does_not_include_system_commands(self):
        rows = self.search("")
        self.assertTrue(all(row.system_command is None for row in rows))

    def test_pivot_marker_and_row_ids_are_stable(self):
        rows = {row.maneuver_id: row for row in self.search("")}
        self.assertIn("[→ Border]", rows["fill"].display_label)
        self.assertNotIn("[→", rows["bevel"].display_label)
        for row in self.search(""):
            if row.maneuver_id is not None:
                self.assertEqual(row.row_id, row.maneuver_id)
            self.assertTrue(math.isfinite(row.score))

    def test_search_is_deterministic(self):
        first = self.search("bev")
        second = self.search("bev")
        self.assertEqual(first, second)

    def test_search_does_not_import_host_or_qt_modules(self):
        for name in ("pymxs", "qtmax", "PySide6", "PySide2"):
            self.assertNotIn(name, sys.modules)


if __name__ == "__main__":
    unittest.main()

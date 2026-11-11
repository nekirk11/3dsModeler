"""Pure presentation/keyboard tests for the V2 search palette.

These tests import no Qt and no host modules, so they run on every machine.
"""

from __future__ import annotations

import unittest

from max2025_menu_prototype.qt_ui import palette_model as model


class _EnumLike:
    def __init__(self, value: str) -> None:
        self.value = value


class _Row:
    def __init__(
        self,
        row_id: str,
        label: str = "Bevel",
        group: str = "Modeling",
        enabled: bool = True,
        reason: str = "",
        pivot_context: object = None,
        system_command: object = None,
    ) -> None:
        self.row_id = row_id
        self.label = label
        self.group = group
        self.enabled = enabled
        self.reason = reason
        self.pivot_context = pivot_context
        self.system_command = system_command


class _Result:
    def __init__(self, status: str, message: str = "") -> None:
        self.status = status
        self.message = message


class _View:
    def __init__(self, **kwargs: object) -> None:
        self.session_id = kwargs.get("session_id", "session-1")
        self.generation = kwargs.get("generation", 1)
        self.context = kwargs.get("context", "edge")
        self.selection_count = kwargs.get("selection_count", 2)
        self.query = kwargs.get("query", "")
        self.rows = tuple(kwargs.get("rows", ()))  # type: ignore[arg-type]
        self.highlighted_row_id = kwargs.get("highlighted_row_id")
        self.status = kwargs.get("status", "")
        self.busy = kwargs.get("busy", False)
        self.last_result = kwargs.get("last_result")
        self.pending_confirmation = kwargs.get("pending_confirmation")


class ContextTests(unittest.TestCase):
    def test_frozen_context_order_and_pills(self) -> None:
        self.assertEqual(
            model.CONTEXT_ORDER,
            ("object", "vertex", "edge", "border", "face", "element"),
        )
        self.assertEqual(
            model.pill_labels(),
            (
                "[0 Obj]",
                "[1 Vert]",
                "[2 Edge]",
                "[3 Border]",
                "[4 Face]",
                "[5 Elem]",
            ),
        )
        for index, context in enumerate(model.CONTEXT_ORDER):
            self.assertEqual(model.context_for_index(index), context)
            self.assertEqual(model.context_index(context), index)
            self.assertEqual(model.pill_label(index), model.PILL_LABELS[index])
        self.assertIsNone(model.context_for_index(6))
        self.assertIsNone(model.context_for_index(-1))
        self.assertIsNone(model.context_for_index("edge"))
        self.assertEqual(model.pill_label(9), "")

    def test_context_value_accepts_enum_like_and_rejects_unknown(self) -> None:
        self.assertEqual(model.context_value(_EnumLike("face")), "face")
        self.assertEqual(model.context_value("border"), "border")
        self.assertEqual(model.context_value(_EnumLike("polygon")), "")
        self.assertEqual(model.context_value(None), "")
        self.assertEqual(model.context_title("element"), "Element")
        self.assertEqual(model.context_title("polygon"), "")

    def test_context_cycling_wraps_both_directions(self) -> None:
        self.assertEqual(model.cycle_context("face", 1), "element")
        self.assertEqual(model.cycle_context("element", 1), "object")
        self.assertEqual(model.cycle_context("object", -1), "element")
        self.assertEqual(model.cycle_context("edge", 0), "edge")
        self.assertEqual(model.cycle_context("", 1), "object")
        self.assertEqual(model.cycle_context("", -1), "element")
        self.assertEqual(model.cycle_context("polygon", 1), "object")


class KeyboardIntentTests(unittest.TestCase):
    def test_typing_letters_digits_and_space_never_switch_or_launch(self) -> None:
        cases = [
            ("W", "w"),
            ("E", "e"),
            ("R", "r"),
            ("X", "x"),
            ("T", "t"),
            ("C", "c"),
            ("0", "0"),
            ("1", "1"),
            ("2", "2"),
            ("3", "3"),
            ("4", "4"),
            ("5", "5"),
            ("Space", " "),
        ]
        for key, text in cases:
            with self.subTest(key=key):
                intent = model.keyboard_intent(key, text)
                self.assertEqual(intent.action, "insert_text")
                self.assertEqual(intent.text, text)
                self.assertIsNone(intent.context_index)
                self.assertEqual(intent.delta, 0)

    def test_editing_shortcuts_are_forwarded_to_the_line_edit(self) -> None:
        for key in ("A", "C", "V", "X"):
            with self.subTest(key=key):
                intent = model.keyboard_intent(key, "\x01", ctrl=True)
                self.assertEqual(intent.action, "edit")
        for key in ("Backspace", "Delete", "Left", "Right", "Home", "End"):
            with self.subTest(key=key):
                self.assertEqual(model.keyboard_intent(key).action, "edit")
        self.assertEqual(
            model.keyboard_intent("W", "\x17", ctrl=True).action, "ignore"
        )

    def test_alt_digits_switch_context_and_alt_six_is_ignored(self) -> None:
        for digit in range(6):
            intent = model.keyboard_intent(str(digit), "", alt=True)
            self.assertEqual(intent.action, "context_switch")
            self.assertEqual(intent.context_index, digit)
            self.assertFalse(intent.convert_selection)
        self.assertEqual(
            model.keyboard_intent("6", "", alt=True).action, "ignore"
        )
        self.assertEqual(model.keyboard_intent("1", "1", alt=True).action, "context_switch")

    def test_navigation_activation_cancel_and_cycles(self) -> None:
        self.assertEqual(model.keyboard_intent("Up").delta, -1)
        self.assertEqual(model.keyboard_intent("Down").delta, 1)
        self.assertEqual(model.keyboard_intent("Tab").action, "context_cycle")
        self.assertEqual(model.keyboard_intent("Tab").delta, 1)
        self.assertEqual(
            model.keyboard_intent("Tab", shift=True).delta, -1
        )
        self.assertEqual(model.keyboard_intent("Backtab").delta, -1)
        self.assertEqual(model.keyboard_intent("Return").action, "activate")
        self.assertEqual(model.keyboard_intent("Enter").action, "activate")
        self.assertEqual(model.keyboard_intent("Escape").action, "cancel")

    def test_auto_repeat_never_dispatches_or_closes(self) -> None:
        self.assertEqual(
            model.keyboard_intent("Return", auto_repeat=True).action, "ignore"
        )
        self.assertEqual(
            model.keyboard_intent("Escape", auto_repeat=True).action, "ignore"
        )

    def test_control_text_is_ignored_and_unicode_typing_is_kept(self) -> None:
        self.assertEqual(model.keyboard_intent("F1", "").action, "ignore")
        self.assertEqual(
            model.keyboard_intent("W", "\x17", ctrl=True).action, "ignore"
        )
        composed = model.keyboard_intent("A", "ä")
        self.assertEqual(composed.action, "insert_text")
        self.assertEqual(composed.text, "ä")


class BadgeTests(unittest.TestCase):
    def test_pivot_selection_route_and_context_badges(self) -> None:
        self.assertEqual(model.pivot_badge("border"), "[→ Border]")
        self.assertEqual(model.pivot_badge("face"), "[→ Face]")
        self.assertEqual(model.pivot_badge(None), "")
        self.assertEqual(model.pivot_badge("polygon"), "")
        self.assertEqual(model.selection_badge(0), "[no selection]")
        self.assertEqual(model.selection_badge(3), "[3 selected]")
        self.assertEqual(model.selection_badge("bad"), "[no selection]")
        self.assertEqual(model.route_badge(0), "EMPTY ROUTE")
        self.assertEqual(model.route_badge(1), "SELECTED ROUTE")
        self.assertEqual(model.context_badge("edge"), "EDGE CONTEXT")
        self.assertEqual(model.context_badge("polygon"), "CONTEXT UNKNOWN")

    def test_row_projection_includes_pivot_system_and_reason(self) -> None:
        plain = model.row_presentation(
            _Row("bevel", pivot_context="border")
        )
        self.assertEqual(plain.row_id, "bevel")
        self.assertEqual(plain.pivot_badge, "[→ Border]")
        self.assertTrue(plain.enabled)
        self.assertEqual(plain.meta, "MODELING")
        self.assertFalse(plain.system)

        blocked = model.row_presentation(
            _Row(
                "weld",
                label="Weld",
                enabled=False,
                reason="Select edges first",
                system_command=None,
            )
        )
        self.assertFalse(blocked.enabled)
        self.assertEqual(blocked.reason, "Select edges first")

        system = model.row_presentation(
            _Row("sys", label="Diagnostics", group="", system_command="diagnostics")
        )
        self.assertTrue(system.system)
        self.assertEqual(system.meta, "SYSTEM")

    def test_row_signature_and_highlight_use_stable_ids(self) -> None:
        first = [_Row("bevel"), _Row("extrude")]
        self.assertEqual(model.row_signature(first), model.row_signature(first))
        changed = [_Row("bevel", label="Chamfer"), _Row("extrude")]
        self.assertNotEqual(model.row_signature(first), model.row_signature(changed))
        self.assertIs(model.highlighted_row(first, "extrude"), first[1])
        self.assertIsNone(model.highlighted_row(first, "missing"))
        self.assertIsNone(model.highlighted_row(first, None))
        # A stale numeric index must never resolve a row.
        self.assertIsNone(model.highlighted_row(first, 0))


class StatusTests(unittest.TestCase):
    def test_result_labels_are_honest_about_launch_only(self) -> None:
        launched = model.result_line(_Result("launched", "Chamfer"))
        self.assertIn("LAUNCHED", launched)
        self.assertIn("no geometry result claimed", launched)
        self.assertIn("APPLIED", model.result_line(_Result("applied")))
        self.assertIn("BLOCKED", model.result_line(_Result("blocked", "no edges")))
        self.assertIn("SIMULATED", model.result_line(_Result("simulated")))
        self.assertEqual(model.result_line(None), "")
        self.assertEqual(model.result_line(_Result("")), "")

    def test_status_line_prefers_controller_status(self) -> None:
        view = _View(status="Overlay ignored", last_result=_Result("blocked", "x"))
        self.assertEqual(model.status_line(view), "Overlay ignored")
        view.status = ""
        self.assertIn("BLOCKED", model.status_line(view))

    def test_hint_mentions_confirmation_when_pending(self) -> None:
        self.assertIn("Destructive", model.hint_text(_View(pending_confirmation="t")))
        self.assertIn("Type to search", model.hint_text(_View()))
        self.assertIn("Enter runs", model.hint_text(_View(query="bev")))

    def test_view_is_closed_only_for_terminal_results(self) -> None:
        self.assertTrue(model.view_is_closed(None))
        self.assertTrue(
            model.view_is_closed(_View(session_id="", generation=1))
        )
        self.assertTrue(model.view_is_closed(_View(last_result=_Result("launched"))))
        self.assertTrue(model.view_is_closed(_View(last_result=_Result("applied"))))
        self.assertTrue(model.view_is_closed(_View(last_result=_Result("cancelled"))))
        self.assertFalse(model.view_is_closed(_View(last_result=_Result("blocked"))))
        self.assertFalse(model.view_is_closed(_View(last_result=_Result("error"))))
        self.assertFalse(model.view_is_closed(_View()))
        self.assertFalse(model.view_is_closed(_View(busy=True)))
        # A declined confirmation is terminal but keeps its rows: it stays open.
        self.assertFalse(
            model.view_is_closed(
                _View(
                    rows=(_Row("bevel"),),
                    last_result=_Result("cancelled"),
                )
            )
        )
        self.assertTrue(
            model.view_is_closed(
                _View(rows=(), last_result=_Result("launched"))
            )
        )
        closed_flag = _View()
        closed_flag.closed = True  # type: ignore[attr-defined]
        self.assertTrue(model.view_is_closed(closed_flag))


class ConfirmationTests(unittest.TestCase):
    def test_no_confirmation_without_a_token(self) -> None:
        self.assertIsNone(model.confirmation_model(_View()))
        self.assertIsNone(model.confirmation_model(_View(pending_confirmation=False)))
        self.assertEqual(model.confirmation_token(_View()), "")

    def test_confirmation_defaults_to_cancel_and_explains_scope(self) -> None:
        inline = model.confirmation_model(_View(pending_confirmation="token-1"))
        self.assertIsNotNone(inline)
        assert inline is not None
        self.assertEqual(inline.token, "token-1")
        self.assertEqual(inline.default_action, "cancel")
        self.assertTrue(inline.destructive)
        self.assertEqual(inline.accept_label, model.CONFIRM_ACCEPT_LABEL)
        self.assertEqual(inline.cancel_label, model.CONFIRM_CANCEL_LABEL)
        for phrase in ("modifier stack", "Instanced", "Only this explicit action"):
            self.assertIn(phrase, inline.detail)

    def test_confirmation_accepts_duck_typed_token_objects(self) -> None:
        from dataclasses import dataclass

        @dataclass
        class Pending:
            token: str
            message: str = "Collapse the selected node's modifier stack?"
            destructive_scope: str = "Only the selected node is targeted."

        inline = model.confirmation_model(_View(pending_confirmation=Pending("t-9")))
        assert inline is not None
        self.assertEqual(inline.token, "t-9")
        self.assertIn("modifier stack", inline.detail)
        self.assertIn("Only the selected node", inline.detail)


class StylesheetTests(unittest.TestCase):
    def _blocks(self) -> list[tuple[str, str]]:
        blocks: list[tuple[str, str]] = []
        for line in model.stylesheet().splitlines():
            line = line.strip()
            if not line or "{" not in line or not line.endswith("}"):
                continue
            selector, body = line.split("{", 1)
            blocks.append((selector.strip(), body.rsplit("}", 1)[0].strip()))
        return blocks

    def test_every_rule_declares_foreground_background_and_border(self) -> None:
        blocks = self._blocks()
        self.assertGreaterEqual(len(blocks), 40)
        for selector, body in blocks:
            with self.subTest(selector=selector):
                self.assertTrue(body.startswith("color: "), body)
                self.assertIn("background-color: ", body)
                self.assertIn("border: ", body)
                color = body.split("color: ", 1)[1].split(";", 1)[0].strip()
                self.assertTrue(color)
                background = (
                    body.split("background-color: ", 1)[1].split(";", 1)[0].strip()
                )
                self.assertTrue(background)
                # The actual Windows failure mode is white-on-light fallback:
                # no rule may leave a light/white background.
                self.assertNotIn(background.lower(), ("white", "#fff", "#ffffff"))

    def test_pills_and_buttons_style_every_interactive_state(self) -> None:
        selectors = {selector for selector, _ in self._blocks()}
        pill = "QPushButton#contextPill"
        for suffix in ("", ":hover", ":pressed", ":checked", ":checked:hover", ":disabled", ":focus"):
            with self.subTest(state=suffix):
                self.assertIn(pill + suffix, selectors)
        accept = "QPushButton#confirmAccept"
        cancel = "QPushButton#confirmCancel"
        for suffix in ("", ":hover", ":pressed", ":focus", ":disabled"):
            self.assertIn(accept + suffix, selectors)
            self.assertIn(cancel + suffix, selectors)
        self.assertIn('QPushButton#confirmCancel[default="true"]', selectors)
        input_selector = "QLineEdit#paletteInput"
        for suffix in ("", ":hover", ":focus", ":disabled"):
            self.assertIn(input_selector + suffix, selectors)
        for row_state in (
            "QFrame#paletteRow",
            'QFrame#paletteRow[highlighted="true"]',
            'QFrame#paletteRow[disabled="true"]',
        ):
            self.assertIn(row_state, selectors)

    def test_theme_tokens_match_the_agreed_dark_palette(self) -> None:
        self.assertEqual(model.DARK_THEME["background"], "#12151d")
        self.assertEqual(model.DARK_THEME["input"], "#0b0d11")
        self.assertEqual(model.DARK_THEME["text"], "#e8ebf2")
        self.assertEqual(model.DARK_THEME["button"], "#242b38")
        self.assertEqual(model.DARK_THEME["accent"], "#67b8ff")
        self.assertEqual(model.DARK_THEME["disabled"], "#6c7487")
        self.assertEqual(model.DARK_THEME["border"], "#333b4d")
        copy = model.style_tokens()
        copy["text"] = "#000000"
        self.assertEqual(model.DARK_THEME["text"], "#e8ebf2")
        self.assertIn("#12151d", model.STYLESHEET)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

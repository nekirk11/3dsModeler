"""Undo-wrapper tests, including the documented pymxs exception suppression.

``pymxs.undo`` rolls back and swallows exceptions.  These tests use a fake undo
context that can suppress exactly like the host so the unsafe
``try: with undo: edit(); return success`` pattern cannot pass unnoticed.
"""

from __future__ import annotations

import sys
import types
import unittest
from unittest import mock

from max2025_menu_prototype.max_host.operations import OperationOutcome
from max2025_menu_prototype.max_host.transactions import (
    MAX_LABEL_LENGTH,
    TransactionError,
    hold_reason,
    hold_state,
    run_undoable,
)


class FakeUndoContext:
    def __init__(self, factory: "FakeUndoFactory") -> None:
        self.factory = factory

    def __enter__(self):
        self.factory.entered.append(self.factory.label)
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.factory.exited.append((self.factory.label, exc_type))
        if exc_type is not None and self.factory.suppress:
            return True
        if self.factory.exit_raises:
            raise RuntimeError("undo context exit failed")
        return False


class FakeUndoFactory:
    def __init__(self, suppress: bool = False, exit_raises: bool = False) -> None:
        self.suppress = suppress
        self.exit_raises = exit_raises
        self.calls = []
        self.entered = []
        self.exited = []
        self._label = ""

    @property
    def label(self) -> str:
        return self._label

    def __call__(self, enabled, label):
        self.calls.append((enabled, label))
        self._label = label
        return FakeUndoContext(self)


class UndoTransactionTests(unittest.TestCase):
    def test_success_is_reported_only_after_context_exit(self):
        undo = FakeUndoFactory()
        order = []

        def edit():
            order.append("edit")
            return OperationOutcome("applied", "edit completed")

        outcome = run_undoable("Max2025 prototype: test", edit, undo)
        self.assertEqual(outcome.status, "applied")
        self.assertTrue(outcome.completed)
        self.assertEqual(outcome.message, "edit completed")
        self.assertEqual(undo.calls, [(True, "Max2025 prototype: test")])
        self.assertEqual(len(undo.entered), 1)
        self.assertEqual(len(undo.exited), 1)
        self.assertEqual(order, ["edit"])

    def test_raised_error_is_error_and_not_success(self):
        undo = FakeUndoFactory()
        calls = []

        def edit():
            calls.append("started")
            raise RuntimeError("native failure")

        outcome = run_undoable("Max2025 prototype: test", edit, undo)
        self.assertEqual(outcome.status, "error")
        self.assertFalse(outcome.completed)
        self.assertIn("RuntimeError", outcome.message)
        self.assertNotIn("applied", outcome.message)
        self.assertEqual(undo.exited[0][1], RuntimeError)

    def test_suppressed_exception_still_reports_error(self):
        undo = FakeUndoFactory(suppress=True)

        def edit():
            raise RuntimeError("host suppressed this")

        outcome = run_undoable("Max2025 prototype: suppress", edit, undo)
        self.assertEqual(outcome.status, "error")
        self.assertFalse(outcome.completed)
        self.assertIn("RuntimeError", outcome.message)
        self.assertIsNotNone(undo.exited)
        self.assertEqual(undo.exited[0][1], RuntimeError)

    def test_edit_that_never_completes_before_suppression_is_error(self):
        undo = FakeUndoFactory(suppress=True)
        state = {"finished": False}

        def edit():
            state["finished"] = False
            raise RuntimeError("still not finished")

        outcome = run_undoable("Max2025 prototype: incomplete", edit, undo)
        self.assertFalse(state["finished"])
        self.assertEqual(outcome.status, "error")
        self.assertFalse(outcome.completed)

    def test_context_exit_failure_is_error(self):
        undo = FakeUndoFactory(exit_raises=True)

        def edit():
            return OperationOutcome("applied", "edited")

        outcome = run_undoable("Max2025 prototype: exit", edit, undo)
        self.assertEqual(outcome.status, "error")
        self.assertFalse(outcome.completed)
        self.assertIn("undo context exit failed", outcome.message)

    def test_undo_factory_failure_does_not_run_the_edit(self):
        calls = []

        def factory(enabled, label):
            raise RuntimeError("cannot begin")

        def edit():
            calls.append("edit")
            return OperationOutcome("applied", "edited")

        outcome = run_undoable("Max2025 prototype: factory", edit, factory)
        self.assertEqual(outcome.status, "error")
        self.assertFalse(outcome.completed)
        self.assertIn("cannot begin", outcome.message)
        self.assertEqual(calls, [])

    def test_failure_message_does_not_assert_a_verified_rollback(self):
        for suppress in (False, True):
            with self.subTest(suppress=suppress):
                undo = FakeUndoFactory(suppress=suppress)

                def edit():
                    raise RuntimeError("native failure")

                outcome = run_undoable(
                    "Max2025 prototype: neutral", edit, undo
                )
                self.assertEqual(outcome.status, "error")
                self.assertFalse(outcome.completed)
                self.assertIn("RuntimeError", outcome.message)
                self.assertIn(
                    "rollback verification is required", outcome.message
                )
                self.assertNotIn("rolled it back", outcome.message)
                self.assertNotIn("restored", outcome.message.lower())

    def test_noop_outcome_is_preserved(self):
        undo = FakeUndoFactory()

        def edit():
            return OperationOutcome("noop", "nothing changed")

        outcome = run_undoable("Max2025 prototype: noop", edit, undo)
        self.assertEqual(outcome.status, "noop")
        self.assertTrue(outcome.completed)
        self.assertEqual(outcome.message, "nothing changed")

    def test_invalid_outcome_is_error_not_applied(self):
        undo = FakeUndoFactory()

        def edit():
            return types.SimpleNamespace(status="applied-ish", message="bad")

        outcome = run_undoable("Max2025 prototype: invalid", edit, undo)
        self.assertEqual(outcome.status, "error")
        self.assertFalse(outcome.completed)

    def test_invalid_labels_are_rejected_before_a_chunk(self):
        undo = FakeUndoFactory()
        edit = lambda: OperationOutcome("applied", "edited")
        for label in ("", "   ", "line\nbreak", "x" * (MAX_LABEL_LENGTH + 1)):
            with self.subTest(label=label[:12]):
                with self.assertRaises(TransactionError):
                    run_undoable(label, edit, undo)
        self.assertEqual(undo.calls, [])

    def test_each_call_creates_exactly_one_chunk(self):
        undo = FakeUndoFactory()

        def edit():
            return OperationOutcome("applied", "edited")

        for _ in range(2):
            outcome = run_undoable("Max2025 prototype: repeat", edit, undo)
            self.assertEqual(outcome.status, "applied")
        self.assertEqual(len(undo.calls), 2)
        self.assertEqual(undo.calls, [
            (True, "Max2025 prototype: repeat"),
            (True, "Max2025 prototype: repeat"),
        ])

    def test_hold_guard_blocks_chunk_and_edit_before_factory(self):
        undo = FakeUndoFactory()
        order = []

        def factory(enabled, label):
            order.append("factory")
            return undo(enabled, label)

        def edit():
            order.append("edit")
            return OperationOutcome("applied", "edited")

        outcome = run_undoable(
            "Max2025 prototype: hold",
            edit,
            factory,
            hold_guard=lambda: (order.append("guard") or "active hold"),
        )
        self.assertEqual(outcome.status, "error")
        self.assertFalse(outcome.completed)
        self.assertIn("active hold", outcome.message)
        self.assertEqual(order, ["guard"])
        self.assertEqual(undo.calls, [])

    def test_hold_guard_is_rechecked_immediately_before_factory(self):
        undo = FakeUndoFactory()
        order = []

        def factory(enabled, label):
            order.append("factory")
            return undo(enabled, label)

        def edit():
            order.append("edit")
            return OperationOutcome("applied", "edited")

        outcome = run_undoable(
            "Max2025 prototype: hold-ok",
            edit,
            factory,
            hold_guard=lambda: (order.append("guard") or None),
        )
        self.assertEqual(outcome.status, "applied")
        self.assertEqual(order, ["guard", "factory", "edit"])

    def test_hold_state_uses_documented_queries_and_fails_closed(self):
        class FakeHold:
            def __init__(self, holding=False, restore_or_redoing=False):
                self._holding = holding
                self._restore_or_redoing = restore_or_redoing
                self.calls = []

            def Holding(self):
                self.calls.append("Holding")
                return self._holding

            def RestoreOrRedoing(self):
                self.calls.append("RestoreOrRedoing")
                return self._restore_or_redoing

        idle_hold = FakeHold()
        idle = types.SimpleNamespace(theHold=idle_hold)
        self.assertEqual(hold_state(idle), (True, ""))
        self.assertIsNone(hold_reason(idle))
        self.assertIn("Holding", idle_hold.calls)
        self.assertIn("RestoreOrRedoing", idle_hold.calls)
        self.assertNotIn("Redoing", idle_hold.calls)

        unreadable_values = (None, "false", 0.0, object())
        for bad in unreadable_values:
            with self.subTest(holding_value=bad):
                runtime = types.SimpleNamespace(
                    theHold=types.SimpleNamespace(
                        Holding=lambda bad=bad: bad,
                        RestoreOrRedoing=lambda: False,
                    )
                )
                ok, reason = hold_state(runtime)
                self.assertFalse(ok)
                self.assertIn("unreadable", reason)
            with self.subTest(restore_value=bad):
                runtime = types.SimpleNamespace(
                    theHold=types.SimpleNamespace(
                        Holding=lambda: False,
                        RestoreOrRedoing=lambda bad=bad: bad,
                    )
                )
                ok, reason = hold_state(runtime)
                self.assertFalse(ok)
                self.assertIn("unreadable", reason)

        for runtime in (
            types.SimpleNamespace(theHold=FakeHold(holding=True)),
            types.SimpleNamespace(theHold=FakeHold(restore_or_redoing=True)),
            types.SimpleNamespace(),
            types.SimpleNamespace(theHold=types.SimpleNamespace()),
            types.SimpleNamespace(
                theHold=types.SimpleNamespace(
                    Holding=lambda: False, Redoing=lambda: False
                )
            ),
        ):
            with self.subTest(runtime=runtime):
                ok, reason = hold_state(runtime)
                self.assertFalse(ok)
                self.assertTrue(reason)
                self.assertIsNotNone(hold_reason(runtime))

    def test_integer_hold_flags_use_documented_nonzero_semantics(self):
        runtime = types.SimpleNamespace(
            theHold=types.SimpleNamespace(
                Holding=lambda: 0, RestoreOrRedoing=lambda: 0
            )
        )
        self.assertEqual(hold_state(runtime), (True, ""))
        runtime = types.SimpleNamespace(
            theHold=types.SimpleNamespace(
                Holding=lambda: 0, RestoreOrRedoing=lambda: 1
            )
        )
        ok, reason = hold_state(runtime)
        self.assertFalse(ok)
        self.assertIn("restoring/redoing", reason)

    def test_default_factory_uses_pymxs_undo_and_never_run_undo(self):
        undo = FakeUndoFactory()
        run_undo_calls = []

        fake_pymxs = types.SimpleNamespace(
            undo=undo,
            run_undo=lambda: run_undo_calls.append("run_undo"),
            run_redo=lambda: run_undo_calls.append("run_redo"),
        )

        def edit():
            return OperationOutcome("applied", "edited")

        with mock.patch.dict(sys.modules, {"pymxs": fake_pymxs}):
            outcome = run_undoable("Max2025 prototype: default", edit)
        self.assertEqual(outcome.status, "applied")
        self.assertEqual(undo.calls, [(True, "Max2025 prototype: default")])
        self.assertEqual(run_undo_calls, [])

    def test_missing_pymxs_reports_a_clear_lazy_error(self):
        def edit():
            return OperationOutcome("applied", "edited")

        with mock.patch.dict(sys.modules, {"pymxs": None}):
            outcome = run_undoable("Max2025 prototype: missing", edit)
        self.assertEqual(outcome.status, "error")
        self.assertFalse(outcome.completed)
        self.assertIn("pymxs.undo", outcome.message)


if __name__ == "__main__":
    unittest.main(verbosity=2)

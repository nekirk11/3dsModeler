"""Single-chunk undo wrapping for immediate Editable Poly edits.

``pymxs.undo(True, label)`` rolls an edited scene back **and suppresses the
exception** (Autodesk 2025 pymxs documentation).  Returning success from inside
a ``with undo:`` block is therefore unsafe: an edit can raise, be swallowed by
the context manager, and still look completed.  This module records the edit
result outside the context and only reports completion after the context has
exited normally.

The wrapper never calls ``run_undo()`` as rollback, never clears the undo
buffer, never disables undo globally, and never nests or adopts a hold owned by
another tool.  It creates exactly one synchronous chunk per call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

#: A trusted, short operation label.  The adapter/operations module supplies
#: only module constants; no node name, parameter text, or user string reaches
#: this label.
MAX_LABEL_LENGTH = 96


class TransactionError(RuntimeError):
    """Raised when the undo wrapper itself cannot be established."""


@dataclass(frozen=True)
class TransactionOutcome:
    """Outcome of one owned undo chunk.

    ``status`` is ``applied``, ``noop`` or ``error``.  ``completed`` is true
    only when the edit callable returned a result, its restores finished, and
    the undo context exited without an exception (including a suppressed one).
    """

    status: str
    message: str
    completed: bool = False
    exception: Optional[str] = None

    def __post_init__(self) -> None:
        if self.status not in ("applied", "noop", "error"):
            raise ValueError(f"Unsupported transaction status: {self.status!r}")


def _default_undo_factory(enabled: bool, label: str) -> Any:
    """Resolve ``pymxs.undo`` lazily; importing this module never imports pymxs."""

    try:
        import pymxs  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - only outside Max
        raise TransactionError(
            "pymxs.undo is unavailable; run the edit inside 3ds Max 2025 or "
            "inject an undo context factory."
        ) from exc
    undo = getattr(pymxs, "undo", None)
    if not callable(undo):
        raise TransactionError("pymxs.undo is not callable in this host.")
    return undo(enabled, label)


def _validate_label(label: str) -> str:
    if not isinstance(label, str) or not label.strip():
        raise TransactionError("An undo label is required.")
    if len(label) > MAX_LABEL_LENGTH or "\n" in label or "\r" in label:
        raise TransactionError("The undo label is not a trusted constant.")
    return label


def _is_undefined(value: Any) -> bool:
    if value is None:
        return True
    try:
        return str(value).strip().lower() in {"undefined", "none"}
    except Exception:
        return False


def _documented_flag(value: Any) -> Optional[bool]:
    """Interpret a documented MAXScript Boolean/C++ nonzero integer result.

    ``theHold.Holding()`` and ``theHold.RestoreOrRedoing()`` are documented as
    boolean (C++ returns nonzero), so only ``bool`` and integer values are
    trustworthy.  ``None``, strings, floats, and arbitrary truthy objects are
    unreadable and must fail closed rather than becoming ``False``.
    """

    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    return None


def hold_flags(runtime: Any) -> tuple[bool, bool, bool]:
    """Return ``(holding, restore_or_redoing, readable)`` without mutation.

    V2 snapshots keep the two documented flags separate so charging, rollback
    and redo states are not conflated.  Any unreadable/unavailable query yields
    ``readable=False``; callers must fail closed rather than assume "not held".
    """

    try:
        the_hold = getattr(runtime, "theHold", None)
    except Exception:
        return False, False, False
    if _is_undefined(the_hold):
        return False, False, False
    holding = getattr(the_hold, "Holding", None)
    restore_or_redoing = getattr(the_hold, "RestoreOrRedoing", None)
    if not callable(holding) or not callable(restore_or_redoing):
        return False, False, False
    try:
        holding_flag = _documented_flag(holding())
        restore_flag = _documented_flag(restore_or_redoing())
    except Exception:
        return False, False, False
    if holding_flag is None or restore_flag is None:
        return False, False, False
    return holding_flag, restore_flag, True


def hold_state(runtime: Any) -> tuple[bool, str]:
    """Read the documented MAXScript ``theHold`` state without mutation.

    Only ``theHold.Holding()`` and ``theHold.RestoreOrRedoing()`` are used
    (Autodesk MAXScript Help, ``theHold``).  Their absence, non-callability, or
    unreadable/non-Boolean/non-integer return fails closed because an active
    hold/restore/redo must never receive a new owned chunk.
    """

    try:
        the_hold = getattr(runtime, "theHold", None)
    except Exception as exc:
        return False, "theHold could not be read (%s)" % type(exc).__name__
    if _is_undefined(the_hold):
        return False, "theHold is unavailable; undo/redo state cannot be verified"
    holding = getattr(the_hold, "Holding", None)
    restore_or_redoing = getattr(the_hold, "RestoreOrRedoing", None)
    if not callable(holding) or not callable(restore_or_redoing):
        return False, "theHold.Holding()/RestoreOrRedoing() are not callable"
    try:
        holding_value = holding()
        restore_value = restore_or_redoing()
    except Exception as exc:
        return False, "theHold state query failed (%s)" % type(exc).__name__
    holding_flag = _documented_flag(holding_value)
    restore_flag = _documented_flag(restore_value)
    if holding_flag is None or restore_flag is None:
        return False, "theHold state query returned an unreadable value"
    if holding_flag or restore_flag:
        return (
            False,
            "an active theHold holding/restoring/redoing state owns the undo "
            "system",
        )
    return True, ""


def hold_reason(runtime: Any) -> Optional[str]:
    """Return ``None`` when a new owned chunk is safe, else a reason."""

    ok, reason = hold_state(runtime)
    return None if ok else reason


def run_undoable(
    label: str,
    edit: Callable[[], Any],
    undo_factory: Optional[Callable[[bool, str], Any]] = None,
    hold_guard: Optional[Callable[[], Optional[str]]] = None,
) -> TransactionOutcome:
    """Run ``edit`` in one owned undo chunk and report its real outcome.

    ``edit`` returns an internal outcome with a ``status`` attribute of
    ``applied`` or ``noop``.  Any exception it raises is recorded and re-raised
    so the undo context rolls the edit back; a suppressed exception is still
    detected because ``completed`` is only set after the callable returns.
    ``hold_guard`` is rechecked immediately before the undo factory so a new
    chunk cannot start while a host hold/redo is active.
    """

    if not callable(edit):
        raise TypeError("edit must be callable")
    label = _validate_label(label)
    factory = undo_factory or _default_undo_factory

    state: dict[str, Any] = {
        "completed": False,
        "outcome": None,
        "error": None,
    }

    if hold_guard is not None:
        try:
            hold_error = hold_guard()
        except Exception as exc:
            return TransactionOutcome(
                "error",
                "Could not verify host undo/redo state (%s): %s"
                % (type(exc).__name__, exc),
                completed=False,
                exception=type(exc).__name__,
            )
        if hold_error:
            return TransactionOutcome(
                "error",
                "Refusing a new undo chunk: %s" % hold_error,
                completed=False,
                exception="HostHoldActive",
            )

    try:
        context = factory(True, label)
    except BaseException as exc:  # noqa: BLE001 - reported, never swallowed
        return TransactionOutcome(
            "error",
            "Could not begin the undo chunk (%s): %s" % (type(exc).__name__, exc),
            completed=False,
            exception=type(exc).__name__,
        )

    try:
        with context:
            try:
                outcome = edit()
                status = getattr(outcome, "status", None)
                if status not in ("applied", "noop"):
                    raise TransactionError(
                        "Undo edit returned an unsupported outcome: %r" % (status,)
                    )
                state["outcome"] = outcome
                # This line is only reached after all native calls, property
                # restores, and postcondition checks inside ``edit`` succeed.
                state["completed"] = True
            except BaseException as exc:  # noqa: BLE001 - must trigger rollback
                state["error"] = exc
                raise
    except BaseException as exc:  # noqa: BLE001 - includes suppressed exits
        if state["error"] is None:
            state["error"] = exc
    # Deliberately inspect recorded state after the context has exited.  A
    # context that suppresses the exception still leaves completed == False.
    if state["error"] is not None:
        exc = state["error"]
        return TransactionOutcome(
            "error",
            "Native edit failed; rollback verification is required (%s): %s"
            % (type(exc).__name__, exc),
            completed=False,
            exception=type(exc).__name__,
        )
    if not state["completed"]:
        return TransactionOutcome(
            "error",
            "The undo chunk exited without confirming completion; no success "
            "is claimed.",
            completed=False,
            exception="IncompleteUndoContext",
        )

    outcome = state["outcome"]
    return TransactionOutcome(
        status=outcome.status,
        message=outcome.message,
        completed=True,
    )


__all__ = [
    "MAX_LABEL_LENGTH",
    "TransactionError",
    "TransactionOutcome",
    "hold_flags",
    "hold_reason",
    "hold_state",
    "run_undoable",
]

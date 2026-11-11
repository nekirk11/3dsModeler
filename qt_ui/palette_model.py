"""Pure presentation and keyboard-intent helpers for the V2 search palette.

This module imports no Qt, no ``pymxs``/``qtmax``, and no host modules.  It
therefore stays testable on a machine without PySide6 and keeps
``qt_ui/palette.py`` a thin renderer.  It mirrors the frozen V2 vocabulary from
the palette plan (``object``, ``vertex``, ``edge``, ``border``, ``face``,
``element``) as plain strings and duck-types every controller view/row it
receives, so it never imports the streaming core modules.

Nothing here executes a maneuver.  Rows, badges, confirmation text and key
intents are display data only; the controller remains the sole authority for
context switching, search, dispatch and destructive confirmation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

#: Frozen public V2 context order (maps to Max levels 0-5).
CONTEXT_ORDER: tuple[str, ...] = (
    "object",
    "vertex",
    "edge",
    "border",
    "face",
    "element",
)

CONTEXT_TITLES: dict[str, str] = {
    "object": "Object",
    "vertex": "Vertex",
    "edge": "Edge",
    "border": "Border",
    "face": "Face",
    "element": "Element",
}

CONTEXT_CODES: tuple[str, ...] = ("Obj", "Vert", "Edge", "Border", "Face", "Elem")

#: Exact header pills required by the plan: ``[0 Obj] [1 Vert] ...``.
PILL_LABELS: tuple[str, ...] = tuple(
    f"[{index} {code}]" for index, code in enumerate(CONTEXT_CODES)
)

PILL_TOOLTIP = "Click: switch context · Ctrl+Click: convert selection"
PILL_HINT = "Tab/Shift+Tab cycles context · Alt+0…5 selects a context"

WINDOW_TITLE = "3dsModeler Search"

CONFIRM_TITLE = "Collapse stack to Editable Poly?"
CONFIRM_ACCEPT_LABEL = "Collapse stack"
CONFIRM_CANCEL_LABEL = "Cancel"

#: Explicit dark-theme colors, including the plan's example palette.
DARK_THEME: dict[str, str] = {
    "background": "#12151d",
    "input": "#0b0d11",
    "text": "#e8ebf2",
    "button": "#242b38",
    "accent": "#67b8ff",
    "accent_hover": "#7cc6ff",
    "disabled": "#6c7487",
    "border": "#333b4d",
    "muted": "#9aa3b5",
    "reason": "#d7a27a",
    "row_hover": "#1a2029",
    "row_highlight": "#1d2735",
    "danger": "#ff8a9a",
    "danger_button": "#3a2328",
    "danger_border": "#7a3a44",
}

#: Object names shared by the stylesheet and the renderer.
PALETTE_OBJECT = "searchPalette"
INPUT_OBJECT = "paletteInput"
PILL_OBJECT = "contextPill"
ROW_OBJECT = "paletteRow"
STATUS_OBJECT = "paletteStatus"
HINT_OBJECT = "paletteHint"
CONFIRM_BAR_OBJECT = "confirmBar"
CONFIRM_ACCEPT_OBJECT = "confirmAccept"
CONFIRM_CANCEL_OBJECT = "confirmCancel"

_TERMINAL_RESULT_STATUSES = frozenset(
    {"launched", "applied", "noop", "cancelled", "simulated"}
)


@dataclass(frozen=True)
class KeyIntent:
    """One pure interpretation of a key event.

    ``action`` is one of ``insert_text``, ``edit``, ``navigate``,
    ``activate``, ``cancel``, ``context_switch``, ``context_cycle`` or
    ``ignore``.  Only ``insert_text``/``edit`` are forwarded to the real line
    edit; everything else is consumed by the palette.
    """

    action: str
    text: str = ""
    delta: int = 0
    context_index: int | None = None
    convert_selection: bool = False
    key: str = ""
    auto_repeat: bool = False


@dataclass(frozen=True)
class RowPresentation:
    """Render-only projection of one ``ManeuverRow``."""

    row_id: str
    label: str
    group: str
    enabled: bool
    reason: str
    pivot_badge: str
    meta: str
    system: bool


@dataclass(frozen=True)
class ConfirmationModel:
    """Inline destructive-confirmation content (never auto-accepted)."""

    token: str
    title: str
    detail: str
    accept_label: str = CONFIRM_ACCEPT_LABEL
    cancel_label: str = CONFIRM_CANCEL_LABEL
    destructive: bool = True
    default_action: str = "cancel"


# --------------------------------------------------------------------- context


def context_value(context: Any) -> str:
    """Return a canonical V2 context string for an enum or plain string."""

    if context is None:
        return ""
    value = getattr(context, "value", context)
    try:
        text = str(value)
    except Exception:
        return ""
    return text if text in CONTEXT_ORDER else ""


def context_title(context: Any) -> str:
    """Human-readable context name; empty for unknown values."""

    value = context_value(context)
    return CONTEXT_TITLES.get(value, "")


def context_for_index(index: Any) -> str | None:
    """Map a Max/pill level 0-5 to a context, or None when out of range."""

    try:
        number = int(index)
    except (TypeError, ValueError):
        return None
    if 0 <= number < len(CONTEXT_ORDER):
        return CONTEXT_ORDER[number]
    return None


def context_index(context: Any) -> int | None:
    """Return the 0-5 level for a context, or None when unknown."""

    value = context_value(context)
    if not value:
        return None
    return CONTEXT_ORDER.index(value)


def context_for_digit(digit: Any) -> str | None:
    """Alt+digit helper; only 0-5 are legal."""

    return context_for_index(digit)


def cycle_context(context: Any, delta: int) -> str:
    """Wrap Tab/Shift+Tab context cycling deterministically."""

    try:
        step = int(delta)
    except (TypeError, ValueError):
        step = 0
    current = context_value(context)
    if not current:
        return CONTEXT_ORDER[0 if step >= 0 else -1]
    if step == 0:
        return current
    return CONTEXT_ORDER[(CONTEXT_ORDER.index(current) + step) % len(CONTEXT_ORDER)]


def pill_label(index: Any) -> str:
    """Return the exact ``[N Code]`` pill label, or an empty string."""

    context = context_for_index(index)
    if context is None:
        return ""
    return PILL_LABELS[CONTEXT_ORDER.index(context)]


def pill_labels() -> tuple[str, ...]:
    return PILL_LABELS


# --------------------------------------------------------------------- badges


def pivot_badge(pivot_context: Any) -> str:
    """``[→ Border]``-style pivot marker; empty when there is no pivot."""

    title = context_title(pivot_context)
    return f"[→ {title}]" if title else ""


def selection_badge(selection_count: Any) -> str:
    """Compact selection count badge for the palette header."""

    try:
        count = int(selection_count or 0)
    except (TypeError, ValueError):
        count = 0
    if count <= 0:
        return "[no selection]"
    return f"[{count} selected]"


def route_badge(selection_count: Any) -> str:
    """Route label derived from the fresh selection count only."""

    try:
        count = int(selection_count or 0)
    except (TypeError, ValueError):
        count = 0
    return "SELECTED ROUTE" if count > 0 else "EMPTY ROUTE"


def context_badge(context: Any) -> str:
    title = context_title(context)
    return f"{title.upper()} CONTEXT" if title else "CONTEXT UNKNOWN"


# ----------------------------------------------------------------------- rows


def row_presentation(row: Any) -> RowPresentation:
    """Project a duck-typed ``ManeuverRow`` without touching the host."""

    row_id = str(getattr(row, "row_id", "") or "")
    label = str(getattr(row, "label", "") or "")
    group = str(getattr(row, "group", "") or "")
    enabled = bool(getattr(row, "enabled", False))
    reason = str(getattr(row, "reason", "") or "")
    pivot = pivot_badge(getattr(row, "pivot_context", None))
    system = getattr(row, "system_command", None) is not None
    meta = group.upper() if group else ""
    if system:
        meta = f"{meta} · SYSTEM" if meta else "SYSTEM"
    return RowPresentation(
        row_id=row_id,
        label=label,
        group=group,
        enabled=enabled,
        reason=reason,
        pivot_badge=pivot,
        meta=meta,
        system=system,
    )


def row_signature(rows: Iterable[Any]) -> tuple[tuple[Any, ...], ...]:
    """Cheap identity for deciding whether row widgets must be rebuilt."""

    signature: list[tuple[Any, ...]] = []
    for row in rows:
        projected = row_presentation(row)
        signature.append(
            (
                projected.row_id,
                projected.label,
                projected.group,
                projected.enabled,
                projected.reason,
                projected.pivot_badge,
                projected.meta,
            )
        )
    return tuple(signature)


def highlighted_row(rows: Sequence[Any], row_id: Any) -> Any | None:
    """Find the highlighted row by stable row ID, never by stale index."""

    wanted = str(row_id or "")
    if not wanted:
        return None
    for row in rows:
        if str(getattr(row, "row_id", "") or "") == wanted:
            return row
    return None


# --------------------------------------------------------------------- status


def result_status(result: Any) -> str:
    if result is None:
        return ""
    status = str(getattr(result, "status", "") or "")
    return status.strip().lower()


def result_line(result: Any) -> str:
    """Honest result label; never claims geometry work from a launch."""

    status = result_status(result)
    if not status:
        return ""
    labels = {
        "launched": "LAUNCHED — mode entry only; no geometry result claimed",
        "applied": "APPLIED",
        "blocked": "BLOCKED",
        "error": "ERROR",
        "cancelled": "CANCELLED",
        "noop": "NO CHANGES",
        "simulated": "SIMULATED — no mesh changes",
    }
    label = labels.get(status, status.upper())
    message = str(getattr(result, "message", "") or "").strip()
    return f"{label} — {message}" if message else label


def status_line(view: Any) -> str:
    """Single status line, preferring an explicit controller string."""

    status = str(getattr(view, "status", "") or "").strip()
    if status:
        return status
    return result_line(getattr(view, "last_result", None))


def hint_text(view: Any) -> str:
    if getattr(view, "pending_confirmation", None) is not None:
        return "Destructive action: choose Collapse or Cancel · Esc cancels"
    if str(getattr(view, "query", "") or ""):
        return "↑/↓ move · Enter runs · Tab switches context · Esc closes"
    return (
        "Type to search · ↑/↓ move · Enter runs · Tab switches context · Esc closes"
    )


def view_is_closed(view: Any) -> bool:
    """Renderer-side close detection without inventing a controller result.

    ``PaletteView`` has no explicit ``closed`` flag in the frozen contract, so
    a closed palette is recognized from an explicit flag when one is present,
    a falsy session id, or a terminal dispatch result whose row list was
    cleared.  A declined confirmation keeps its rows, so it stays open.  A
    blocked/error view is never treated as closed, because it must reopen with
    a fresh reason.
    """

    if view is None:
        return True
    if bool(getattr(view, "closed", False)):
        return True
    if getattr(view, "open", True) is False:
        return True
    if not getattr(view, "session_id", None):
        return True
    if bool(getattr(view, "busy", False)):
        return False
    if result_status(getattr(view, "last_result", None)) not in _TERMINAL_RESULT_STATUSES:
        return False
    # The controller keeps a declined confirmation open with its rows intact;
    # a completed dispatch clears the rows.  A terminal result alone is not a
    # close signal.
    rows = getattr(view, "rows", None)
    if rows is None:
        return True
    try:
        return len(tuple(rows)) == 0
    except TypeError:
        return True


# ------------------------------------------------------------- confirmation


def confirmation_scope_lines() -> tuple[str, ...]:
    """Fixed destructive-scope copy shown before ``collapse_to_poly``."""

    return (
        "Bakes the selected object's modifier stack into one Editable Poly base.",
        "Modifiers are removed by that bake; the collapse is a destructive edit.",
        "Instanced or shared objects are refused until you run Make Unique yourself.",
        "Only this explicit action collapses a stack; Esc and Cancel change nothing.",
    )


def confirmation_token(view: Any) -> str:
    pending = getattr(view, "pending_confirmation", None)
    if pending is None or pending is False:
        return ""
    if isinstance(pending, str):
        return pending
    token = getattr(pending, "token", "")
    return str(token or "")


def confirmation_model(view: Any) -> ConfirmationModel | None:
    """Build the inline confirmation model, defaulting to Cancel."""

    token = confirmation_token(view)
    if not token:
        return None
    pending = getattr(view, "pending_confirmation", None)
    message = str(getattr(pending, "message", "") or "").strip()
    scope = str(
        getattr(pending, "destructive_scope", "")
        or getattr(pending, "scope", "")
        or ""
    ).strip()
    detail_parts = [message] if message else []
    if scope:
        detail_parts.append(scope)
    detail_parts.extend(confirmation_scope_lines())
    return ConfirmationModel(
        token=token,
        title=CONFIRM_TITLE,
        detail="\n".join(part for part in detail_parts if part),
    )


# ------------------------------------------------------------------ keyboard


def _printable(text: Any) -> str:
    if not isinstance(text, str) or not text:
        return ""
    if len(text) > 64:
        return ""
    for character in text:
        if ord(character) < 32 or ord(character) == 127:
            return ""
    return text


def keyboard_intent(
    key: str,
    text: str = "",
    *,
    ctrl: bool = False,
    alt: bool = False,
    shift: bool = False,
    auto_repeat: bool = False,
) -> KeyIntent:
    """Map one Qt-level key token to a palette intent.

    Typing isolation is the critical property: letters (W/E/R/X/T/C), digits
    (0-5) and Space are ``insert_text`` unless Alt/Ctrl is held, so they always
    reach the real line edit instead of launching a modeling tool or switching
    context.  Ctrl+A/C/V/X and cursor/editing keys stay with the line edit.
    """

    token = str(key or "")
    printable = _printable(text)

    if token == "Escape":
        if auto_repeat:
            return KeyIntent("ignore", key=token, auto_repeat=True)
        return KeyIntent("cancel", key=token)
    if token in ("Return", "Enter"):
        if auto_repeat:
            return KeyIntent("ignore", key=token, auto_repeat=True)
        return KeyIntent("activate", key=token)
    if token == "Up":
        return KeyIntent("navigate", delta=-1, key=token)
    if token == "Down":
        return KeyIntent("navigate", delta=1, key=token)
    if token == "Tab":
        return KeyIntent("context_cycle", delta=-1 if shift else 1, key=token)
    if token == "Backtab":
        return KeyIntent("context_cycle", delta=-1, key=token)
    if token in ("Backspace", "Delete", "Left", "Right", "Home", "End"):
        return KeyIntent("edit", key=token)

    if alt:
        if len(token) == 1 and token.isdigit():
            context = context_for_digit(int(token))
            if context is not None:
                return KeyIntent("context_switch", context_index=int(token), key=token)
        return KeyIntent("ignore", key=token)

    if ctrl:
        if len(token) == 1 and token.upper() in ("A", "C", "V", "X"):
            return KeyIntent("edit", key=token)
        return KeyIntent("ignore", key=token)

    if token == "Space":
        return KeyIntent("insert_text", text=printable or " ", key=token)
    if printable:
        return KeyIntent("insert_text", text=printable, key=token)
    return KeyIntent("ignore", key=token)


# ------------------------------------------------------------------- styling


def _rule(
    selector: str,
    *,
    color: str,
    background: str,
    border: str,
    extra: str = "",
) -> str:
    body = (
        f"color: {color}; "
        f"background-color: {background}; "
        f"border: {border}; "
    )
    if extra:
        body += extra.strip().rstrip(";") + "; "
    return f"{selector} {{ {body}}}"


def _stylesheet_rules() -> tuple[str, ...]:
    """Every rule declares color, background-color and border explicitly.

    The explicit triple is what prevents Windows Qt from falling back to
    inherited/native light buttons (white-on-light) in any widget state.
    """

    theme = DARK_THEME
    border = f"1px solid {theme['border']}"
    no_border = "none"
    transparent = "transparent"
    rules = [
        _rule(
            f"QFrame#{PALETTE_OBJECT}",
            color=theme["text"],
            background=theme["background"],
            border=border,
            extra="border-radius: 10px;",
        ),
        _rule(
            "QLabel",
            color=theme["text"],
            background=transparent,
            border=no_border,
            extra="font-size: 11px;",
        ),
        _rule(
            "QPushButton",
            color=theme["text"],
            background=theme["button"],
            border=border,
            extra="border-radius: 4px; padding: 3px 8px;",
        ),
        _rule(
            "QLineEdit",
            color=theme["text"],
            background=theme["input"],
            border=border,
        ),
        _rule(
            "QFrame",
            color=theme["text"],
            background=transparent,
            border=no_border,
        ),
        _rule(
            "QLabel#paletteTitle",
            color=theme["text"],
            background=transparent,
            border=no_border,
            extra="font-size: 12px; font-weight: 700;",
        ),
        _rule(
            "QLabel#paletteContext",
            color=theme["accent"],
            background=transparent,
            border=no_border,
            extra="font-size: 10px; font-weight: 700; letter-spacing: 1px;",
        ),
        _rule(
            "QLabel#paletteSelection",
            color=theme["muted"],
            background=transparent,
            border=no_border,
            extra="font-size: 10px; font-family: monospace;",
        ),
        _rule(
            f"QLineEdit#{INPUT_OBJECT}",
            color=theme["text"],
            background=theme["input"],
            border=f"1px solid {theme['border']}",
            extra=(
                "border-radius: 5px; padding: 6px 8px; font-size: 13px; "
                "selection-color: #0b0d11; selection-background-color: #67b8ff; "
                "placeholder-text-color: #6c7487;"
            ),
        ),
        _rule(
            f"QLineEdit#{INPUT_OBJECT}:hover",
            color=theme["text"],
            background=theme["input"],
            border=f"1px solid {theme['accent']}",
        ),
        _rule(
            f"QLineEdit#{INPUT_OBJECT}:focus",
            color=theme["text"],
            background=theme["input"],
            border=f"1px solid {theme['accent']}",
            extra="border-radius: 5px; padding: 6px 8px;",
        ),
        _rule(
            f"QLineEdit#{INPUT_OBJECT}:disabled",
            color=theme["disabled"],
            background=theme["input"],
            border=f"1px solid {theme['border']}",
        ),
        _rule(
            f"QPushButton#{PILL_OBJECT}",
            color="#c7cedd",
            background=theme["button"],
            border=border,
            extra="border-radius: 4px; padding: 2px 7px; font-size: 10px;",
        ),
        _rule(
            f"QPushButton#{PILL_OBJECT}:hover",
            color="#ffffff",
            background="#2e3747",
            border=f"1px solid {theme['accent']}",
        ),
        _rule(
            f"QPushButton#{PILL_OBJECT}:pressed",
            color=theme["text"],
            background=theme["row_hover"],
            border=f"1px solid {theme['accent']}",
        ),
        _rule(
            f"QPushButton#{PILL_OBJECT}:focus",
            color=theme["text"],
            background=theme["button"],
            border=f"1px solid {theme['accent']}",
        ),
        _rule(
            f"QPushButton#{PILL_OBJECT}:checked",
            color=theme["input"],
            background=theme["accent"],
            border=f"1px solid {theme['accent']}",
        ),
        _rule(
            f"QPushButton#{PILL_OBJECT}:checked:hover",
            color=theme["input"],
            background=theme["accent_hover"],
            border=f"1px solid {theme['accent_hover']}",
        ),
        _rule(
            f"QPushButton#{PILL_OBJECT}:disabled",
            color=theme["disabled"],
            background="#1a1f29",
            border=f"1px solid {theme['border']}",
        ),
        _rule(
            f"QFrame#{ROW_OBJECT}",
            color=theme["text"],
            background=transparent,
            border=no_border,
            extra="border-radius: 5px;",
        ),
        _rule(
            f"QFrame#{ROW_OBJECT}:hover",
            color=theme["text"],
            background=theme["row_hover"],
            border=f"1px solid {theme['border']}",
        ),
        _rule(
            f'QFrame#{ROW_OBJECT}[highlighted="true"]',
            color="#ffffff",
            background=theme["row_highlight"],
            border=f"1px solid {theme['accent']}",
        ),
        _rule(
            f'QFrame#{ROW_OBJECT}[disabled="true"]',
            color=theme["disabled"],
            background=transparent,
            border=no_border,
        ),
        _rule(
            "QLabel#rowLabel",
            color=theme["text"],
            background=transparent,
            border=no_border,
            extra="font-size: 12px;",
        ),
        _rule(
            'QLabel#rowLabel[disabled="true"]',
            color=theme["disabled"],
            background=transparent,
            border=no_border,
        ),
        _rule(
            "QLabel#rowReason",
            color=theme["reason"],
            background=transparent,
            border=no_border,
            extra="font-size: 10px;",
        ),
        _rule(
            "QLabel#rowMeta",
            color="#5c6374",
            background=transparent,
            border=no_border,
            extra="font-size: 9px; font-family: monospace;",
        ),
        _rule(
            "QLabel#pivotBadge",
            color=theme["accent"],
            background=theme["input"],
            border=f"1px solid {theme['accent']}",
            extra="border-radius: 3px; padding: 1px 4px; font-size: 9px;",
        ),
        _rule(
            "QLabel#routeBadge",
            color=theme["muted"],
            background=theme["input"],
            border=f"1px solid {theme['border']}",
            extra="border-radius: 3px; padding: 1px 4px; font-size: 9px;",
        ),
        _rule(
            "QLabel#paletteEmpty",
            color=theme["muted"],
            background=transparent,
            border=no_border,
            extra="font-size: 11px; padding: 14px 8px;",
        ),
        _rule(
            "QLabel#paletteGroup",
            color=theme["disabled"],
            background=transparent,
            border=no_border,
            extra="font-size: 9px; font-weight: 700; letter-spacing: 1px; padding: 4px 2px 1px 2px;",
        ),
        _rule(
            f"QLabel#{STATUS_OBJECT}",
            color=theme["muted"],
            background=transparent,
            border=no_border,
            extra="font-size: 10px;",
        ),
        _rule(
            f"QLabel#{HINT_OBJECT}",
            color="#5c6374",
            background=transparent,
            border=no_border,
            extra="font-size: 10px;",
        ),
        _rule(
            f"QFrame#{CONFIRM_BAR_OBJECT}",
            color=theme["text"],
            background="#1a1216",
            border=f"1px solid {theme['danger_border']}",
            extra="border-radius: 6px;",
        ),
        _rule(
            "QLabel#confirmTitle",
            color=theme["danger"],
            background=transparent,
            border=no_border,
            extra="font-size: 11px; font-weight: 700;",
        ),
        _rule(
            "QLabel#confirmDetail",
            color="#e6c9cc",
            background=transparent,
            border=no_border,
            extra="font-size: 10px;",
        ),
        _rule(
            f"QPushButton#{CONFIRM_ACCEPT_OBJECT}",
            color="#ffd7d7",
            background=theme["danger_button"],
            border=f"1px solid {theme['danger_border']}",
            extra="border-radius: 4px; padding: 4px 10px; font-size: 11px;",
        ),
        _rule(
            f"QPushButton#{CONFIRM_ACCEPT_OBJECT}:hover",
            color="#ffffff",
            background="#4a2b31",
            border=f"1px solid {theme['danger']}",
        ),
        _rule(
            f"QPushButton#{CONFIRM_ACCEPT_OBJECT}:pressed",
            color="#ffffff",
            background="#2b1a1e",
            border=f"1px solid {theme['danger']}",
        ),
        _rule(
            f"QPushButton#{CONFIRM_ACCEPT_OBJECT}:focus",
            color="#ffd7d7",
            background=theme["danger_button"],
            border=f"2px solid {theme['danger']}",
        ),
        _rule(
            f"QPushButton#{CONFIRM_ACCEPT_OBJECT}:disabled",
            color=theme["disabled"],
            background="#231a1c",
            border=f"1px solid {theme['border']}",
        ),
        _rule(
            f"QPushButton#{CONFIRM_CANCEL_OBJECT}",
            color=theme["text"],
            background=theme["button"],
            border=border,
            extra="border-radius: 4px; padding: 4px 10px; font-size: 11px;",
        ),
        _rule(
            f"QPushButton#{CONFIRM_CANCEL_OBJECT}:hover",
            color="#ffffff",
            background="#2e3747",
            border=f"1px solid {theme['accent']}",
        ),
        _rule(
            f"QPushButton#{CONFIRM_CANCEL_OBJECT}:pressed",
            color=theme["text"],
            background=theme["row_hover"],
            border=f"1px solid {theme['accent']}",
        ),
        _rule(
            f"QPushButton#{CONFIRM_CANCEL_OBJECT}:focus",
            color=theme["text"],
            background=theme["button"],
            border=f"1px solid {theme['accent']}",
        ),
        _rule(
            f'QPushButton#{CONFIRM_CANCEL_OBJECT}[default="true"]',
            color="#ffffff",
            background="#2e3747",
            border=f"1px solid {theme['accent']}",
        ),
        _rule(
            f"QPushButton#{CONFIRM_CANCEL_OBJECT}:disabled",
            color=theme["disabled"],
            background="#1a1f29",
            border=f"1px solid {theme['border']}",
        ),
        _rule(
            "QScrollArea#paletteScroll",
            color=theme["text"],
            background=transparent,
            border=no_border,
        ),
        _rule(
            "QScrollArea#paletteScroll > QWidget",
            color=theme["text"],
            background=theme["background"],
            border=no_border,
        ),
        _rule(
            "QWidget#paletteRows",
            color=theme["text"],
            background=theme["background"],
            border=no_border,
        ),
        _rule(
            "QScrollBar:vertical",
            color=theme["muted"],
            background=theme["background"],
            border=no_border,
            extra="width: 8px;",
        ),
        _rule(
            "QScrollBar::handle:vertical",
            color=theme["muted"],
            background="#394154",
            border=no_border,
            extra="border-radius: 4px; min-height: 20px;",
        ),
        _rule(
            "QScrollBar::add-line:vertical",
            color=theme["disabled"],
            background=transparent,
            border=no_border,
            extra="height: 0;",
        ),
        _rule(
            "QScrollBar::sub-line:vertical",
            color=theme["disabled"],
            background=transparent,
            border=no_border,
            extra="height: 0;",
        ),
    ]
    return tuple(rules)


def stylesheet() -> str:
    """Return the complete dark stylesheet for the palette."""

    return "\n".join(_stylesheet_rules())


#: Convenience alias for callers that prefer a constant.
STYLESHEET = stylesheet()


def style_tokens() -> Mapping[str, str]:
    """Expose the color tokens for tests/diagnostics (read-only copy)."""

    return dict(DARK_THEME)


__all__ = [
    "CONFIRM_ACCEPT_LABEL",
    "CONFIRM_CANCEL_LABEL",
    "CONFIRM_TITLE",
    "CONTEXT_CODES",
    "CONTEXT_ORDER",
    "CONTEXT_TITLES",
    "ConfirmationModel",
    "DARK_THEME",
    "KeyIntent",
    "PILL_LABELS",
    "PILL_TOOLTIP",
    "RowPresentation",
    "STYLESHEET",
    "confirmation_model",
    "confirmation_scope_lines",
    "confirmation_token",
    "context_badge",
    "context_for_digit",
    "context_for_index",
    "context_index",
    "context_title",
    "context_value",
    "cycle_context",
    "hint_text",
    "highlighted_row",
    "keyboard_intent",
    "pill_label",
    "pill_labels",
    "pivot_badge",
    "result_line",
    "result_status",
    "route_badge",
    "row_presentation",
    "row_signature",
    "selection_badge",
    "status_line",
    "style_tokens",
    "stylesheet",
    "view_is_closed",
]

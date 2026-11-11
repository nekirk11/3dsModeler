"""Deterministic, host-free search over a validated maneuver atlas.

Ranking is deliberately small and local: every query token must match the
label, ID, any shipped DCC alias, or any user alias.  Match quality is exact
label/alias, prefix, token boundary, substring, then a bounded ordered
subsequence.  There is no usage telemetry, network access, or user-supplied
regular expression.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Callable, Iterable, Sequence

from .maneuver_contracts import (
    CONTEXT_ORDER,
    INTENT_PIVOTS,
    SYSTEM_COMMANDS,
    Availability,
    ManeuverAtlas,
    ManeuverContext,
    ManeuverRow,
    ManeuverSpec,
    SystemCommand,
)

AvailabilityProvider = Callable[[str, ManeuverContext | None], Availability]

_WHITESPACE = re.compile(r"\s+")

_EXACT_SCORE = 100
_PREFIX_SCORE = 80
_BOUNDARY_SCORE = 60
_SUBSTRING_SCORE = 40
_SUBSEQUENCE_SCORE = 20
_MAX_SUBSEQUENCE_SPAN = 8


def _normalize_text(value: str) -> str:
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", value).casefold()).strip()


def _tokens(query: str) -> tuple[str, ...]:
    normalized = _normalize_text(query)
    return tuple(token for token in normalized.split(" ") if token)


def _is_bounded_subsequence(needle: str, haystack: str) -> bool:
    if not needle:
        return False
    position = -1
    first = -1
    for character in needle:
        found = haystack.find(character, position + 1)
        if found < 0:
            return False
        if first < 0:
            first = found
        position = found
    return position - first + 1 <= max(len(needle) * _MAX_SUBSEQUENCE_SPAN, len(needle))


def _token_score(token: str, haystack: str) -> int | None:
    if not token or not haystack:
        return None
    if token == haystack:
        return _EXACT_SCORE
    if haystack.startswith(token):
        return _PREFIX_SCORE
    index = haystack.find(token)
    if index >= 0:
        if index == 0 or not haystack[index - 1].isalnum():
            return _BOUNDARY_SCORE
        return _SUBSTRING_SCORE
    if _is_bounded_subsequence(token, haystack):
        return _SUBSEQUENCE_SCORE
    return None


def _score_tokens(tokens: Sequence[str], haystacks: Iterable[str]) -> int | None:
    if not tokens:
        return 0
    normalized = [_normalize_text(value) for value in haystacks if value]
    total = 0
    for token in tokens:
        best: int | None = None
        for haystack in normalized:
            score = _token_score(token, haystack)
            if score is not None and (best is None or score > best):
                best = score
                if best == _EXACT_SCORE:
                    break
        if best is None:
            return None
        total += best
    return total


def _pivot_for(spec: ManeuverSpec, active: ManeuverContext) -> ManeuverContext | None:
    if active in spec.contexts:
        return None
    preferred = INTENT_PIVOTS.get(spec.id)
    if preferred is not None and preferred in spec.contexts:
        return preferred
    for candidate in CONTEXT_ORDER:
        if candidate is not active and candidate in spec.contexts:
            return candidate
    return None


def _ask_provider(
    provider: AvailabilityProvider,
    maneuver_id: str,
    pivot_context: ManeuverContext | None,
) -> Availability:
    if not callable(provider):
        return Availability(False, "Availability is unavailable.")
    try:
        value = provider(maneuver_id, pivot_context)
    except Exception:
        return Availability(False, "Availability is unavailable.")
    if not isinstance(value, Availability):
        return Availability(False, "Availability is unavailable.")
    return value


def _system_rows(text: str) -> tuple[ManeuverRow, ...]:
    tokens = _tokens(text)
    scored: list[tuple[int, int, SystemCommand]] = []
    for order, command in enumerate(SYSTEM_COMMANDS):
        label = command.label
        stripped = label[2:] if label.startswith("> ") else label
        score = _score_tokens(tokens, (stripped, *command.aliases))
        if score is None:
            continue
        scored.append((score, order, command))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return tuple(
        ManeuverRow(
            row_id="system:%s" % command.value,
            maneuver_id=None,
            label=command.label,
            group="System",
            enabled=True,
            reason="",
            pivot_context=None,
            system_command=command,
            score=float(score),
        )
        for score, _order, command in scored
    )


def search_maneuvers(
    atlas: ManeuverAtlas,
    query: str,
    active_context: ManeuverContext | str,
    availability_provider: AvailabilityProvider,
) -> tuple[ManeuverRow, ...]:
    """Return deterministic rows for ``query`` in ``active_context``.

    Literal ``>`` switches to system-command search.  A normal query includes
    matching maneuvers first; matching system commands follow below every
    maneuver tier.  Empty normal queries keep atlas/pinned order and do not
    show system commands.
    """

    if not isinstance(atlas, ManeuverAtlas):
        raise TypeError("atlas must be a ManeuverAtlas")
    active = (
        active_context
        if isinstance(active_context, ManeuverContext)
        else ManeuverContext(active_context)
    )
    text = "" if query is None else str(query)
    if text.startswith(">"):
        return _system_rows(text[1:].strip())

    tokens = _tokens(text)
    rows: list[tuple[int, int, int, int, str, ManeuverRow]] = []
    for spec in atlas.maneuvers:
        if atlas.is_hidden(spec.id):
            continue
        pivot = _pivot_for(spec, active)
        context = active if pivot is None else pivot
        if context not in spec.contexts:
            continue
        score = _score_tokens(tokens, (spec.label, spec.id, *atlas.aliases_for(spec.id)))
        if score is None:
            continue
        availability = _ask_provider(availability_provider, spec.id, pivot)
        row = ManeuverRow(
            row_id=spec.id,
            maneuver_id=spec.id,
            label=spec.label,
            group=spec.group,
            enabled=availability.enabled,
            reason=availability.reason,
            pivot_context=pivot,
            system_command=None,
            score=float(score),
        )
        rows.append(
            (
                0 if pivot is None else 1,
                0 if atlas.is_pinned(spec.id) else 1,
                -score,
                atlas.order_of(spec.id),
                row.row_id,
                row,
            )
        )

    if tokens:
        for order, row in enumerate(_system_rows(text)):
            rows.append((2, 1, -row.score, order, row.row_id, row))

    rows.sort(key=lambda item: item[:5])
    return tuple(item[5] for item in rows)


__all__ = ["search_maneuvers"]

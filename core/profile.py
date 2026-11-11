"""Versioned JSON menu profiles with strict, non-executable validation.

Profiles contain known action IDs, visibility flags, and gesture bounds only.
They never contain scripts, paths, callables, or action-ID discovery.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .catalog import ACTIONS_BY_ID
from .contracts import ContextLevel

SCHEMA_VERSION = 1
TAP_MS_MIN = 100
TAP_MS_MAX = 600
NEUTRAL_PX_MIN = 4
NEUTRAL_PX_MAX = 48
CONTEXT_KEYS = tuple(level.value for level in ContextLevel)

_ENTRY_KEYS = frozenset({"id", "visible"})
_PROFILE_KEYS = frozenset(
    {"schema_version", "name", "tap_ms", "neutral_px", "contexts"}
)
_DEFAULT_PATH = Path(__file__).with_name("default_profile.json")


class ProfileError(ValueError):
    """Raised when profile data is malformed.  The active profile is untouched."""


@dataclass
class ProfileEntry:
    """One profile action reference.  Hidden entries keep their position."""

    id: str
    visible: bool = True


@dataclass
class MenuProfile:
    """In-memory, easily cloned profile.  Persistence is explicit only."""

    schema_version: int
    name: str
    tap_ms: int
    neutral_px: int
    contexts: dict[str, list[ProfileEntry]] = field(default_factory=dict)

    def clone(self) -> "MenuProfile":
        return MenuProfile(
            schema_version=self.schema_version,
            name=self.name,
            tap_ms=self.tap_ms,
            neutral_px=self.neutral_px,
            contexts={
                context: [ProfileEntry(e.id, e.visible) for e in entries]
                for context, entries in self.contexts.items()
            },
        )

    def entries_for(self, context: ContextLevel | str) -> list[ProfileEntry]:
        key = context.value if isinstance(context, ContextLevel) else str(context)
        return self.contexts[key]

    def set_visible(
        self, context: ContextLevel | str, action_id: str, visible: bool
    ) -> None:
        if not isinstance(visible, bool):
            raise ProfileError("visible must be a boolean")
        for entry in self.entries_for(context):
            if entry.id == action_id:
                entry.visible = visible
                return
        raise ProfileError(f"Unknown action for this context: {action_id}")

    def move_action(
        self, context: ContextLevel | str, action_id: str, new_index: int
    ) -> None:
        entries = self.entries_for(context)
        if not isinstance(new_index, int) or isinstance(new_index, bool):
            raise ProfileError("new_index must be an integer")
        for old_index, entry in enumerate(entries):
            if entry.id == action_id:
                if not 0 <= new_index < len(entries):
                    raise ProfileError("new_index is out of range")
                if old_index != new_index:
                    entries.insert(new_index, entries.pop(old_index))
                return
        raise ProfileError(f"Unknown action for this context: {action_id}")

    def set_gesture(
        self, tap_ms: int | None = None, neutral_px: int | None = None
    ) -> None:
        if tap_ms is not None:
            _validate_int_bounds("tap_ms", tap_ms, TAP_MS_MIN, TAP_MS_MAX)
            self.tap_ms = tap_ms
        if neutral_px is not None:
            _validate_int_bounds(
                "neutral_px", neutral_px, NEUTRAL_PX_MIN, NEUTRAL_PX_MAX
            )
            self.neutral_px = neutral_px


def _validate_int_bounds(name: str, value: Any, low: int, high: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ProfileError(f"{name} must be an integer")
    if not low <= value <= high:
        raise ProfileError(f"{name} must be between {low} and {high}")
    return value


def _validate(data: Any) -> None:
    if not isinstance(data, dict):
        raise ProfileError("Profile must be a JSON object")
    unknown = set(data) - _PROFILE_KEYS
    if unknown:
        raise ProfileError(f"Unknown profile field(s): {', '.join(sorted(unknown))}")
    missing = _PROFILE_KEYS - set(data)
    if missing:
        raise ProfileError(f"Missing profile field(s): {', '.join(sorted(missing))}")

    if data["schema_version"] != SCHEMA_VERSION or isinstance(
        data["schema_version"], bool
    ):
        raise ProfileError(f"schema_version must be {SCHEMA_VERSION}")
    if not isinstance(data["name"], str) or not data["name"].strip():
        raise ProfileError("name must be a nonempty string")
    _validate_int_bounds("tap_ms", data["tap_ms"], TAP_MS_MIN, TAP_MS_MAX)
    _validate_int_bounds("neutral_px", data["neutral_px"], NEUTRAL_PX_MIN, NEUTRAL_PX_MAX)

    contexts = data["contexts"]
    if not isinstance(contexts, dict):
        raise ProfileError("contexts must be an object")
    keys = set(contexts)
    if keys != set(CONTEXT_KEYS):
        missing_contexts = sorted(set(CONTEXT_KEYS) - keys)
        extra_contexts = sorted(keys - set(CONTEXT_KEYS))
        details = []
        if missing_contexts:
            details.append("missing " + ", ".join(missing_contexts))
        if extra_contexts:
            details.append("unknown " + ", ".join(extra_contexts))
        raise ProfileError("contexts must contain exactly six contexts (" + "; ".join(details) + ")")

    for context in CONTEXT_KEYS:
        entries = contexts[context]
        if not isinstance(entries, list):
            raise ProfileError(f"contexts.{context} must be a list")
        seen: set[str] = set()
        for index, entry in enumerate(entries):
            where = f"contexts.{context}[{index}]"
            if not isinstance(entry, dict):
                raise ProfileError(f"{where} must be an object")
            if set(entry) != _ENTRY_KEYS:
                raise ProfileError(f"{where} must contain exactly id and visible")
            action_id = entry["id"]
            if not isinstance(action_id, str) or action_id not in ACTIONS_BY_ID:
                raise ProfileError(f"{where} has unknown action ID: {action_id!r}")
            if not isinstance(entry["visible"], bool):
                raise ProfileError(f"{where}.visible must be a boolean")
            if action_id in seen:
                raise ProfileError(f"{where} duplicates action ID {action_id}")
            seen.add(action_id)
            action = ACTIONS_BY_ID[action_id]
            if ContextLevel(context) not in action.allowed_levels:
                raise ProfileError(
                    f"{where} places {action_id} in an unsupported context: {context}"
                )


def load_profile(data: Any) -> MenuProfile:
    """Validate and deep-copy profile data into a :class:`MenuProfile`.

    Raises :class:`ProfileError` without mutating any caller-owned object.
    """

    _validate(data)
    copied = copy.deepcopy(data)
    return MenuProfile(
        schema_version=SCHEMA_VERSION,
        name=copied["name"],
        tap_ms=copied["tap_ms"],
        neutral_px=copied["neutral_px"],
        contexts={
            context: [
                ProfileEntry(id=entry["id"], visible=entry["visible"])
                for entry in copied["contexts"][context]
            ]
            for context in CONTEXT_KEYS
        },
    )


def dump_profile(profile: MenuProfile) -> dict[str, Any]:
    """Return a fresh JSON-compatible wire representation."""

    return {
        "schema_version": profile.schema_version,
        "name": profile.name,
        "tap_ms": profile.tap_ms,
        "neutral_px": profile.neutral_px,
        "contexts": {
            context: [
                {"id": entry.id, "visible": entry.visible}
                for entry in profile.contexts[context]
            ]
            for context in CONTEXT_KEYS
        },
    }


def default_profile() -> MenuProfile:
    """Load the bundled default profile relative to this module, never cwd."""

    with _DEFAULT_PATH.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return load_profile(data)

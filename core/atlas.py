"""Strict, host-free loading and validation of the V2 maneuver atlas.

The bundled atlas is resolved relative to this module; an optional checkout
``user_aliases.json`` is the only other input.  Both are data only.  This
module never imports Max, Qt, executes strings, fetches URLs, or performs I/O
at import time.
"""

from __future__ import annotations

import json
import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from .maneuver_contracts import (
    MANEUVER_CONTEXTS,
    MAX_ALIASES_PER_SOURCE,
    MAX_ALIAS_CHARS,
    MAX_JSON_DEPTH,
    MAX_MANEUVERS,
    MAX_TEXT_CHARS,
    REQUIRED_MANEUVER_IDS,
    ContractError,
    ContextSpec,
    DccReference,
    ExecutionDescriptor,
    ManeuverAtlas,
    ManeuverContext,
    ManeuverSpec,
    MaxRoutes,
    UserOverlay,
    validate_blender_url,
    validate_houdini_url,
    validate_maneuver_id,
)

ATLAS_FILENAME = "maneuvers.json"
OVERLAY_FILENAME = "user_aliases.json"
ATLAS_MAX_BYTES = 2 * 1024 * 1024
OVERLAY_MAX_BYTES = 256 * 1024
OVERLAY_WARNING = "User alias overlay ignored: the overlay file is invalid."

_ATLAS_ROOT_KEYS = frozenset({"schema_version", "maneuvers"})
_MANEUVER_KEYS = frozenset({"id", "label", "group", "aliases", "contexts"})
_ALIAS_SOURCES = ("houdini_modeler", "blender", "maya", "max2025")
_MAX_ROUTE_KEYS = frozenset({"with_selection", "empty_selection"})
_DESCRIPTOR_KEYS = frozenset({"kind", "target", "api", "doc_url"})
_DCC_KEYS = frozenset({"api", "doc_url", "note"})
_OVERLAY_KEYS = frozenset({"schema_version", "aliases", "pinned", "hidden"})


class AtlasError(ValueError):
    """The bundled atlas or a candidate atlas is malformed."""


class OverlayError(AtlasError):
    """A user overlay is malformed; the caller keeps the bundled atlas."""


@dataclass(frozen=True)
class AtlasLoadResult:
    """Validated atlas plus sanitized, category-only overlay warnings."""

    atlas: ManeuverAtlas
    warnings: tuple[str, ...] = ()

    @property
    def overlay_warning(self) -> str | None:
        return self.warnings[0] if self.warnings else None


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AtlasError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> Any:
    raise AtlasError("nonfinite JSON number is not allowed")


def _json_depth(value: Any, depth: int = 0) -> int:
    if depth > MAX_JSON_DEPTH:
        raise AtlasError("JSON document is nested too deeply")
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise AtlasError("JSON object keys must be strings")
            _json_depth(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            _json_depth(child, depth + 1)
    return depth


def _decode_json(data: bytes, *, limit: int, label: str) -> Any:
    if len(data) > limit:
        raise AtlasError("%s exceeds its size limit" % label)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AtlasError("%s is not valid UTF-8" % label) from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except AtlasError:
        raise
    except (ValueError, RecursionError) as exc:
        raise AtlasError("%s is not valid JSON" % label) from exc
    _json_depth(value)
    return value


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AtlasError("%s must be an object" % label)
    for key in value:
        if not isinstance(key, str):
            raise AtlasError("%s has a non-string key" % label)
    return value


def _exact_keys(value: Mapping[str, Any], expected: frozenset[str], label: str) -> None:
    if set(value) != set(expected):
        raise AtlasError(
            "%s must have exactly these keys: %s"
            % (label, ", ".join(sorted(expected)))
        )


def _subset_keys(
    value: Mapping[str, Any], allowed: frozenset[str], label: str
) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise AtlasError(
            "%s has unknown keys: %s" % (label, ", ".join(sorted(unknown)))
        )


def _sequence(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise AtlasError("%s must be an array" % label)
    return value


def _bounded_string(value: Any, limit: int, label: str) -> str:
    try:
        return _bounded(value, limit, label)
    except ContractError as exc:
        raise AtlasError(str(exc)) from exc


def _bounded(value: Any, limit: int, label: str) -> str:
    from .maneuver_contracts import bounded_text

    return bounded_text(value, limit, field_name=label)


# ---------------------------------------------------------------------------
# Atlas validation
# ---------------------------------------------------------------------------


def _validate_descriptor(value: Any, *, context: ManeuverContext, label: str) -> ExecutionDescriptor:
    data = _mapping(value, label)
    _exact_keys(data, _DESCRIPTOR_KEYS, label)
    kind = data["kind"]
    target = data["target"]
    if not isinstance(kind, str):
        raise AtlasError("%s kind must be a string" % label)
    if not isinstance(target, str):
        raise AtlasError("%s target must be a string" % label)
    api = _bounded_string(data["api"], MAX_TEXT_CHARS, "%s api" % label)
    doc_url = data["doc_url"]
    try:
        descriptor = ExecutionDescriptor(
            kind=kind, target=target, api=api, doc_url=doc_url
        )
    except ContractError as exc:
        raise AtlasError(str(exc)) from exc
    _validate_route_legality(descriptor, context, label)
    return descriptor


def _validate_route_legality(
    descriptor: ExecutionDescriptor, context: ManeuverContext, label: str
) -> None:
    if descriptor.kind in (
        "caddy",
        "command_mode",
        "button_op",
        "polyop",
    ) and context is ManeuverContext.OBJECT:
        raise AtlasError("%s: %s routes are not valid at object level" % (label, descriptor.kind))
    if descriptor.kind == "composite":
        if descriptor.target == "clean_dissolve_edges":
            if context is not ManeuverContext.EDGE:
                raise AtlasError("%s: clean dissolve is only valid at edge level" % label)
        elif context is not ManeuverContext.OBJECT:
            raise AtlasError(
                "%s: %s is only valid at object level"
                % (label, descriptor.target)
            )


def _validate_max_routes(value: Any, *, context: ManeuverContext, label: str) -> MaxRoutes:
    data = _mapping(value, label)
    _subset_keys(data, _MAX_ROUTE_KEYS, label)
    if not data:
        raise AtlasError("%s needs at least one selection route" % label)
    with_selection = (
        _validate_descriptor(
            data["with_selection"], context=context, label=label + ".with_selection"
        )
        if "with_selection" in data
        else None
    )
    empty_selection = (
        _validate_descriptor(
            data["empty_selection"], context=context, label=label + ".empty_selection"
        )
        if "empty_selection" in data
        else None
    )
    return MaxRoutes(with_selection=with_selection, empty_selection=empty_selection)


def _validate_reference(
    value: Any, *, backend: str, label: str
) -> DccReference:
    data = _mapping(value, label)
    _subset_keys(data, _DCC_KEYS, label)
    if "api" not in data or "doc_url" not in data:
        raise AtlasError("%s must contain api and doc_url" % label)
    api = _bounded_string(data["api"], MAX_TEXT_CHARS, "%s api" % label)
    doc_url = data["doc_url"]
    note = data.get("note", "")
    if not isinstance(note, str):
        raise AtlasError("%s note must be a string" % label)
    try:
        if backend == "houdini":
            validate_houdini_url(doc_url, field_name="%s doc_url" % label)
        else:
            validate_blender_url(doc_url, field_name="%s doc_url" % label)
        return DccReference(api=api, doc_url=doc_url, note=note)
    except ContractError as exc:
        raise AtlasError(str(exc)) from exc


def _validate_aliases(value: Any, label: str) -> Mapping[str, tuple[str, ...]]:
    data = _mapping(value, label)
    _exact_keys(data, frozenset(_ALIAS_SOURCES), label)
    result: dict[str, tuple[str, ...]] = {}
    for source in _ALIAS_SOURCES:
        entries = _sequence(data[source], "%s.%s" % (label, source))
        if not entries:
            raise AtlasError("%s.%s must not be empty" % (label, source))
        if len(entries) > MAX_ALIASES_PER_SOURCE:
            raise AtlasError("%s.%s has too many aliases" % (label, source))
        aliases: list[str] = []
        for index, alias in enumerate(entries):
            aliases.append(
                _bounded_string(
                    alias, MAX_ALIAS_CHARS, "%s.%s[%d]" % (label, source, index)
                )
            )
        result[source] = tuple(aliases)
    return result


def _validate_contexts(
    value: Any, *, maneuver_id: str, label: str
) -> Mapping[ManeuverContext, ContextSpec]:
    data = _mapping(value, label)
    if not data:
        raise AtlasError("%s must not be empty" % label)
    expected = set(MANEUVER_CONTEXTS[maneuver_id])
    parsed: dict[ManeuverContext, ContextSpec] = {}
    for raw_context, routes in data.items():
        if raw_context == "polygon":
            raise AtlasError("%s may not use the V1 polygon synonym" % label)
        try:
            context = ManeuverContext(raw_context)
        except ValueError as exc:
            raise AtlasError("%s has an unknown context: %s" % (label, raw_context)) from exc
        if context in parsed:
            raise AtlasError("%s has a duplicate context" % label)
        context_data = _mapping(routes, "%s.%s" % (label, raw_context))
        _exact_keys(
            context_data,
            frozenset({"max2025", "houdini", "blender"}),
            "%s.%s" % (label, raw_context),
        )
        parsed[context] = ContextSpec(
            max2025=_validate_max_routes(
                context_data["max2025"],
                context=context,
                label="%s.%s.max2025" % (label, raw_context),
            ),
            houdini=_validate_reference(
                context_data["houdini"],
                backend="houdini",
                label="%s.%s.houdini" % (label, raw_context),
            ),
            blender=_validate_reference(
                context_data["blender"],
                backend="blender",
                label="%s.%s.blender" % (label, raw_context),
            ),
        )
    if set(parsed) != expected:
        missing = sorted(item.value for item in expected - set(parsed))
        extra = sorted(item.value for item in set(parsed) - expected)
        raise AtlasError(
            "%s contexts do not match the frozen registry (missing: %s; extra: %s)"
            % (label, ", ".join(missing) or "none", ", ".join(extra) or "none")
        )
    return MappingProxyType(parsed)


def validate_atlas(data: Any) -> ManeuverAtlas:
    """Validate a complete bundled-atlas object and return an immutable atlas."""

    root = _mapping(data, "atlas")
    _exact_keys(root, _ATLAS_ROOT_KEYS, "atlas")
    version = root["schema_version"]
    if isinstance(version, bool) or not isinstance(version, int):
        raise AtlasError("atlas schema_version must be an integer")
    if version != 2:
        raise AtlasError("atlas schema_version must be 2")
    entries = _sequence(root["maneuvers"], "atlas.maneuvers")
    if not entries:
        raise AtlasError("atlas.maneuvers must not be empty")
    if len(entries) > MAX_MANEUVERS:
        raise AtlasError("atlas.maneuvers exceeds the maneuver ceiling")

    specs: list[ManeuverSpec] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        label = "atlas.maneuvers[%d]" % index
        item = _mapping(entry, label)
        _exact_keys(item, _MANEUVER_KEYS, label)
        try:
            maneuver_id = validate_maneuver_id(item["id"])
        except ContractError as exc:
            raise AtlasError(str(exc)) from exc
        if maneuver_id not in MANEUVER_CONTEXTS:
            raise AtlasError("%s has an unknown maneuver ID: %s" % (label, maneuver_id))
        if maneuver_id in seen:
            raise AtlasError("duplicate maneuver ID: %s" % maneuver_id)
        seen.add(maneuver_id)
        label_text = _bounded_string(item["label"], MAX_TEXT_CHARS, "%s.label" % label)
        group = item["group"]
        if not isinstance(group, str):
            raise AtlasError("%s.group must be a string" % label)
        aliases = _validate_aliases(item["aliases"], "%s.aliases" % label)
        contexts = _validate_contexts(
            item["contexts"], maneuver_id=maneuver_id, label="%s.contexts" % label
        )
        try:
            specs.append(
                ManeuverSpec(
                    id=maneuver_id,
                    label=label_text,
                    group=group,
                    aliases=aliases,
                    contexts=contexts,
                )
            )
        except ContractError as exc:
            raise AtlasError(str(exc)) from exc
    missing = set(REQUIRED_MANEUVER_IDS) - seen
    if missing:
        raise AtlasError(
            "atlas is missing required maneuvers: %s" % ", ".join(sorted(missing))
        )
    return ManeuverAtlas(schema_version=2, maneuvers=tuple(specs))


# ---------------------------------------------------------------------------
# Overlay validation and merge
# ---------------------------------------------------------------------------


def _normalize_alias_key(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold().strip()


def _id_sequence(value: Any, known_ids: set[str], label: str) -> tuple[str, ...]:
    entries = _sequence(value, label)
    result: list[str] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        try:
            maneuver_id = validate_maneuver_id(entry, field_name="%s[%d]" % (label, index))
        except ContractError as exc:
            raise OverlayError(str(exc)) from exc
        if maneuver_id not in known_ids:
            raise OverlayError("%s contains an unknown ID: %s" % (label, maneuver_id))
        if maneuver_id in seen:
            raise OverlayError("%s contains a duplicate ID: %s" % (label, maneuver_id))
        seen.add(maneuver_id)
        result.append(maneuver_id)
    return tuple(result)


def validate_overlay(data: Any, known_ids: Iterable[str]) -> UserOverlay:
    """Validate an overlay object without touching the shipped atlas."""

    try:
        return _validate_overlay_impl(data, known_ids)
    except OverlayError:
        raise
    except ContractError as exc:
        raise OverlayError(str(exc)) from exc
    except AtlasError as exc:
        raise OverlayError(str(exc)) from exc


def _validate_overlay_impl(data: Any, known_ids: Iterable[str]) -> UserOverlay:
    known = set(known_ids)
    root = _mapping(data, "overlay")
    _subset_keys(root, _OVERLAY_KEYS, "overlay")
    if "schema_version" not in root:
        raise OverlayError("overlay schema_version is required")
    version = root["schema_version"]
    if isinstance(version, bool) or not isinstance(version, int):
        raise OverlayError("overlay schema_version must be an integer")
    if version != 1:
        raise OverlayError("overlay schema_version must be 1")

    aliases_data = root.get("aliases", {})
    aliases = _mapping(aliases_data, "overlay.aliases")
    parsed_aliases: dict[str, tuple[str, ...]] = {}
    for raw_id, values in aliases.items():
        try:
            maneuver_id = validate_maneuver_id(raw_id, field_name="overlay alias ID")
        except ContractError as exc:
            raise OverlayError(str(exc)) from exc
        if maneuver_id not in known:
            raise OverlayError("overlay alias has an unknown ID: %s" % maneuver_id)
        entries = _sequence(values, "overlay.aliases.%s" % maneuver_id)
        if len(entries) > MAX_ALIASES_PER_SOURCE:
            raise OverlayError("overlay alias list is too long for %s" % maneuver_id)
        checked: list[str] = []
        seen_keys: set[str] = set()
        for index, alias in enumerate(entries):
            text = _bounded_string(
                alias, MAX_ALIAS_CHARS, "overlay.aliases.%s[%d]" % (maneuver_id, index)
            )
            key = _normalize_alias_key(text)
            if not key:
                raise OverlayError("overlay alias must not be blank")
            if key in seen_keys:
                raise OverlayError(
                    "overlay has a duplicate alias for %s" % maneuver_id
                )
            seen_keys.add(key)
            checked.append(text)
        parsed_aliases[maneuver_id] = tuple(checked)

    pinned = (
        _id_sequence(root["pinned"], known, "overlay.pinned")
        if "pinned" in root
        else ()
    )
    hidden = (
        _id_sequence(root["hidden"], known, "overlay.hidden")
        if "hidden" in root
        else ()
    )
    try:
        return UserOverlay(
            schema_version=1,
            aliases=MappingProxyType(parsed_aliases),
            pinned=pinned,
            hidden=hidden,
        )
    except ContractError as exc:
        raise OverlayError(str(exc)) from exc


def merge_overlay(atlas: ManeuverAtlas, overlay: UserOverlay) -> ManeuverAtlas:
    """Return a new atlas with extended user aliases and deterministic hints.

    Shipped aliases and routes are never changed or removed.  Hidden wins over
    pinned.  A normalized duplicate alias is collapsed.
    """

    if not isinstance(atlas, ManeuverAtlas):
        raise TypeError("atlas must be a ManeuverAtlas")
    if not isinstance(overlay, UserOverlay):
        raise TypeError("overlay must be a UserOverlay")
    merged: dict[str, tuple[str, ...]] = {
        maneuver_id: tuple(values)
        for maneuver_id, values in atlas.user_aliases.items()
    }
    for maneuver_id, values in overlay.aliases.items():
        spec = atlas.by_id(maneuver_id)
        if spec is None:
            raise OverlayError("overlay alias has an unknown ID: %s" % maneuver_id)
        existing = list(merged.get(maneuver_id, ()))
        seen = {
            _normalize_alias_key(alias)
            for source in _ALIAS_SOURCES
            for alias in spec.aliases.get(source, ())
        }
        seen.update(_normalize_alias_key(alias) for alias in existing)
        for alias in values:
            key = _normalize_alias_key(alias)
            if key and key not in seen:
                seen.add(key)
                existing.append(alias)
        merged[maneuver_id] = tuple(existing)
    hidden = tuple(overlay.hidden)
    pinned = tuple(
        maneuver_id for maneuver_id in overlay.pinned if maneuver_id not in hidden
    )
    return ManeuverAtlas(
        schema_version=2,
        maneuvers=atlas.maneuvers,
        pinned=pinned,
        hidden=hidden,
        user_aliases=merged,
    )


# ---------------------------------------------------------------------------
# File loading
# ---------------------------------------------------------------------------


def _bundled_atlas_path() -> Path:
    return Path(__file__).resolve().with_name(ATLAS_FILENAME)


def _default_checkout_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _read_bounded(path: Path, limit: int) -> bytes:
    with path.open("rb") as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise AtlasError("file exceeds its size limit")
    return data


def _load_bundled_atlas() -> ManeuverAtlas:
    path = _bundled_atlas_path()
    try:
        data = _read_bounded(path, ATLAS_MAX_BYTES)
    except OSError as exc:
        raise AtlasError("bundled maneuver atlas is unreadable") from exc
    try:
        payload = _decode_json(data, limit=ATLAS_MAX_BYTES, label="bundled maneuver atlas")
        return validate_atlas(payload)
    except AtlasError:
        raise
    except (ValueError, RecursionError) as exc:  # pragma: no cover - defensive
        raise AtlasError("bundled maneuver atlas is invalid") from exc


def _read_overlay(path: Path) -> UserOverlay:
    # Reject symlinks and non-regular files before opening them.
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        raise OverlayError("overlay must not be a symlink")
    if not stat.S_ISREG(info.st_mode):
        raise OverlayError("overlay must be a regular file")
    data = _read_bounded(path, OVERLAY_MAX_BYTES)
    payload = _decode_json(data, limit=OVERLAY_MAX_BYTES, label="user alias overlay")
    return validate_overlay(payload, REQUIRED_MANEUVER_IDS)


def load_atlas(checkout_root: Path | str | None = None) -> AtlasLoadResult:
    """Load the bundled atlas and optionally merge a checkout overlay.

    A missing overlay is normal.  Any overlay error (unreadable, invalid,
    malformed, symlinked, oversized) ignores the entire overlay and returns one
    sanitized warning category; a bundled-atlas failure raises
    :class:`AtlasError` and must block native dispatch.
    """

    atlas = _load_bundled_atlas()
    root = (
        Path(checkout_root) if checkout_root is not None else _default_checkout_root()
    )
    try:
        root = root.resolve()
    except OSError:
        return AtlasLoadResult(atlas=atlas, warnings=(OVERLAY_WARNING,))
    overlay_path = root / OVERLAY_FILENAME
    try:
        overlay_path.lstat()
    except FileNotFoundError:
        return AtlasLoadResult(atlas=atlas)
    except OSError:
        return AtlasLoadResult(atlas=atlas, warnings=(OVERLAY_WARNING,))
    try:
        overlay = _read_overlay(overlay_path)
        return AtlasLoadResult(atlas=merge_overlay(atlas, overlay))
    except (OSError, UnicodeError, ValueError, AtlasError):
        # One finite category; never expose file contents or a private path.
        return AtlasLoadResult(atlas=atlas, warnings=(OVERLAY_WARNING,))


def atlas_ids(atlas: ManeuverAtlas) -> tuple[str, ...]:
    """Return maneuver IDs in declaration order."""

    return atlas.ids


def overlay_known_ids() -> tuple[str, ...]:
    """IDs accepted by :func:`validate_overlay` for the bundled schema."""

    return REQUIRED_MANEUVER_IDS


# Keep imports explicit for callers/tests that inspect module surface.
__all__ = [
    "ATLAS_FILENAME",
    "ATLAS_MAX_BYTES",
    "AtlasError",
    "AtlasLoadResult",
    "OVERLAY_FILENAME",
    "OVERLAY_MAX_BYTES",
    "OVERLAY_WARNING",
    "OverlayError",
    "atlas_ids",
    "load_atlas",
    "merge_overlay",
    "overlay_known_ids",
    "validate_atlas",
    "validate_overlay",
]

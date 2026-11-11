"""Frozen V2 maneuver contracts shared by the atlas, search, UI and hosts.

This module is intentionally stdlib-only and host-free.  It defines the public
context vocabulary, the fixed descriptor target registry, the immutable atlas
types, the opaque host snapshot, renderer rows/views, and the host seam.  JSON
data may choose only a kind/target pair from :data:`TARGETS_BY_KIND`; it can
never add handlers, execute strings, or point at files.

The V1 enum in :mod:`core.contracts` keeps ``polygon``; V2 calls the same Max
sub-object level 4 ``face``.  Only :func:`to_legacy_context` and
:func:`from_legacy_context` translate between the two vocabularies.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

from .contracts import Availability, ContextLevel, LaunchResult

# ---------------------------------------------------------------------------
# Public V2 context vocabulary and fixed registries
# ---------------------------------------------------------------------------


class ManeuverContext(str, Enum):
    """The six V2 contexts, in the frozen display/preference order."""

    OBJECT = "object"
    VERTEX = "vertex"
    EDGE = "edge"
    BORDER = "border"
    FACE = "face"
    ELEMENT = "element"


CONTEXT_ORDER: tuple[ManeuverContext, ...] = (
    ManeuverContext.OBJECT,
    ManeuverContext.VERTEX,
    ManeuverContext.EDGE,
    ManeuverContext.BORDER,
    ManeuverContext.FACE,
    ManeuverContext.ELEMENT,
)

CONTEXT_INDEX: dict[ManeuverContext, int] = {
    context: index for index, context in enumerate(CONTEXT_ORDER)
}

MAX_ALIAS_SOURCES: tuple[str, ...] = (
    "houdini_modeler",
    "blender",
    "maya",
    "max2025",
)

MANEUVER_GROUPS: tuple[str, ...] = (
    "Modeling",
    "Selection",
    "Transform",
    "View",
    "Object",
)

EXECUTION_KINDS: tuple[str, ...] = (
    "caddy",
    "command_mode",
    "button_op",
    "polyop",
    "max_command",
    "composite",
    "context_switch",
)

PREP_TIERS: tuple[str, ...] = (
    "ready",
    "convert",
    "base_with_modifiers",
    "blocked",
)

#: Exact semantic target registry.  The native dispatch registry in stream B
#: must expose the same key sets; a descriptor cannot introduce a new target.
TARGETS_BY_KIND: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "caddy": frozenset(
            {
                "Chamfer",
                "Extrude",
                "Bevel",
                "Inset",
                "ConnectEdges",
                "BridgeEdge",
                "BridgeBorder",
                "BridgePolygon",
                "Weld",
                "Tessellate",
                "MSmooth",
            }
        ),
        "command_mode": frozenset(
            {
                "ChamferVertex",
                "ChamferEdge",
                "ExtrudeVertex",
                "ExtrudeEdge",
                "ExtrudeFace",
                "Bevel",
                "InsetFace",
                "BridgeEdge",
                "BridgeBorder",
                "BridgePolygon",
                "Weld",
                "CutVertex",
                "CutEdge",
                "CutFace",
                "QuickSlice",
                "SwiftLoop",
            }
        ),
        "button_op": frozenset(
            {
                "Delete",
                "Remove",
                "Cap",
                "Collapse",
                "SplitEdges",
                "BreakVertex",
                "SelectEdgeLoop",
                "SelectEdgeRing",
                "GrowSelection",
                "ShrinkSelection",
                "FlipNormals",
                "MakePlanarInX",
                "MakePlanarInY",
                "MakePlanarInZ",
            }
        ),
        "polyop": frozenset({"clear_selection"}),
        "max_command": frozenset(
            {"move", "rotate", "scale", "frame_selection"}
        ),
        "composite": frozenset(
            {"clean_dissolve_edges", "convert_to_poly", "collapse_to_poly"}
        ),
        "context_switch": frozenset({"vertex", "edge", "border", "face"}),
    }
)

#: `>`-prefixed system commands.  Callbacks are injected by bootstrap, never
#: named in JSON.
class SystemCommand(str, Enum):
    PULL_UPDATES = "pull_updates"
    DIAGNOSTICS = "diagnostics"
    UNLOAD_UI = "unload_ui"

    @property
    def label(self) -> str:
        return SYSTEM_COMMAND_LABELS[self]

    @property
    def aliases(self) -> tuple[str, ...]:
        return SYSTEM_COMMAND_ALIASES[self]


SYSTEM_COMMANDS: tuple[SystemCommand, ...] = (
    SystemCommand.PULL_UPDATES,
    SystemCommand.DIAGNOSTICS,
    SystemCommand.UNLOAD_UI,
)

SYSTEM_COMMAND_LABELS: Mapping[SystemCommand, str] = MappingProxyType(
    {
        SystemCommand.PULL_UPDATES: "> Pull Updates (Git)",
        SystemCommand.DIAGNOSTICS: "> Diagnostics",
        SystemCommand.UNLOAD_UI: "> Unload UI",
    }
)

SYSTEM_COMMAND_ALIASES: Mapping[SystemCommand, tuple[str, ...]] = MappingProxyType(
    {
        SystemCommand.PULL_UPDATES: ("update", "git pull", "pull"),
        SystemCommand.DIAGNOSTICS: ("diagnose", "status", "report"),
        SystemCommand.UNLOAD_UI: ("unload", "close ui", "dispose"),
    }
)

#: Frozen table from the plan (§4).  Contexts are exact; adding a context to
#: the bundled atlas requires a documented host route and tests first.
MANEUVER_CONTEXTS: Mapping[str, tuple[ManeuverContext, ...]] = MappingProxyType(
    {
        "bevel": (ManeuverContext.VERTEX, ManeuverContext.EDGE, ManeuverContext.FACE),
        "extrude": (ManeuverContext.VERTEX, ManeuverContext.EDGE, ManeuverContext.FACE),
        "inset": (ManeuverContext.FACE,),
        "dissolve": (ManeuverContext.VERTEX, ManeuverContext.EDGE),
        "delete": (
            ManeuverContext.VERTEX,
            ManeuverContext.EDGE,
            ManeuverContext.BORDER,
            ManeuverContext.FACE,
            ManeuverContext.ELEMENT,
        ),
        "loop_cut": (ManeuverContext.EDGE, ManeuverContext.FACE),
        "cut": (ManeuverContext.VERTEX, ManeuverContext.EDGE, ManeuverContext.FACE),
        "quick_slice": (
            ManeuverContext.VERTEX,
            ManeuverContext.EDGE,
            ManeuverContext.FACE,
        ),
        "bridge": (
            ManeuverContext.EDGE,
            ManeuverContext.BORDER,
            ManeuverContext.FACE,
        ),
        "fill": (ManeuverContext.BORDER,),
        "weld": (ManeuverContext.VERTEX, ManeuverContext.EDGE),
        "target_weld": (ManeuverContext.VERTEX, ManeuverContext.EDGE),
        "collapse": (
            ManeuverContext.VERTEX,
            ManeuverContext.EDGE,
            ManeuverContext.FACE,
        ),
        "split_edges": (ManeuverContext.EDGE,),
        "break_vertices": (ManeuverContext.VERTEX,),
        "select_loop": (ManeuverContext.EDGE,),
        "select_ring": (ManeuverContext.EDGE,),
        "grow_selection": (
            ManeuverContext.VERTEX,
            ManeuverContext.EDGE,
            ManeuverContext.BORDER,
            ManeuverContext.FACE,
            ManeuverContext.ELEMENT,
        ),
        "shrink_selection": (
            ManeuverContext.VERTEX,
            ManeuverContext.EDGE,
            ManeuverContext.BORDER,
            ManeuverContext.FACE,
            ManeuverContext.ELEMENT,
        ),
        "clear_selection": (
            ManeuverContext.VERTEX,
            ManeuverContext.EDGE,
            ManeuverContext.BORDER,
            ManeuverContext.FACE,
            ManeuverContext.ELEMENT,
        ),
        "convert_to_verts": CONTEXT_ORDER,
        "convert_to_edges": CONTEXT_ORDER,
        "convert_to_borders": CONTEXT_ORDER,
        "convert_to_faces": CONTEXT_ORDER,
        "subdivide": (
            ManeuverContext.VERTEX,
            ManeuverContext.EDGE,
            ManeuverContext.FACE,
        ),
        "smooth": (
            ManeuverContext.VERTEX,
            ManeuverContext.EDGE,
            ManeuverContext.FACE,
        ),
        "flip_normals": (ManeuverContext.FACE, ManeuverContext.ELEMENT),
        "planar_x": (
            ManeuverContext.VERTEX,
            ManeuverContext.EDGE,
            ManeuverContext.BORDER,
            ManeuverContext.FACE,
            ManeuverContext.ELEMENT,
        ),
        "planar_y": (
            ManeuverContext.VERTEX,
            ManeuverContext.EDGE,
            ManeuverContext.BORDER,
            ManeuverContext.FACE,
            ManeuverContext.ELEMENT,
        ),
        "planar_z": (
            ManeuverContext.VERTEX,
            ManeuverContext.EDGE,
            ManeuverContext.BORDER,
            ManeuverContext.FACE,
            ManeuverContext.ELEMENT,
        ),
        "move": CONTEXT_ORDER,
        "rotate": CONTEXT_ORDER,
        "scale": CONTEXT_ORDER,
        "frame_selection": CONTEXT_ORDER,
        "convert_to_poly": (ManeuverContext.OBJECT,),
        "collapse_to_poly": (ManeuverContext.OBJECT,),
    }
)

REQUIRED_MANEUVER_IDS: tuple[str, ...] = tuple(MANEUVER_CONTEXTS)

#: Narrow, documented intent preferences for cross-context pivots.  Everything
#: else uses fixed :data:`CONTEXT_ORDER`.
INTENT_PIVOTS: Mapping[str, ManeuverContext] = MappingProxyType(
    {
        "fill": ManeuverContext.BORDER,
        "inset": ManeuverContext.FACE,
    }
)

#: Bounds shared by the loader and overlay validator.
MAX_ID_CHARS = 128
MAX_ALIAS_CHARS = 128
MAX_TEXT_CHARS = 256
MAX_URL_CHARS = 2048
MAX_ALIASES_PER_SOURCE = 64
MAX_MANEUVERS = 256
MAX_JSON_DEPTH = 12

_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")

_AUTODESK_URL_HOSTS = frozenset({"help.autodesk.com"})
_AUTODESK_URL_PREFIX = "/cloudhelp/"
_HOUDINI_URL_HOST = "www.sidefx.com"
_HOUDINI_URL_PREFIX = "/docs/"
_BLENDER_URL_HOST = "docs.blender.org"
_BLENDER_URL_PREFIXES = ("/api/", "/manual/")


class ContractError(ValueError):
    """Raised when a frozen contract value is malformed."""


def _is_control_character(character: str) -> bool:
    category = unicodedata.category(character)
    return category in ("Cc", "Cf", "Cs")


def bounded_text(value: Any, limit: int, *, field_name: str) -> str:
    """Return a bounded, single-line, nonempty text value or raise."""

    if not isinstance(value, str):
        raise ContractError("%s must be a string" % field_name)
    if not value.strip():
        raise ContractError("%s must not be empty" % field_name)
    if len(value) > limit:
        raise ContractError("%s exceeds %d characters" % (field_name, limit))
    for character in value:
        if _is_control_character(character):
            raise ContractError("%s contains control characters" % field_name)
    return value


def validate_maneuver_id(value: Any, *, field_name: str = "maneuver id") -> str:
    text = bounded_text(value, MAX_ID_CHARS, field_name=field_name)
    if not _ID_PATTERN.fullmatch(text):
        raise ContractError("%s must be lowercase snake_case" % field_name)
    return text


def _official_https_url(
    value: Any,
    *,
    hosts: frozenset[str] | str,
    prefixes: tuple[str, ...],
    field_name: str,
) -> str:
    from urllib.parse import urlsplit

    text = bounded_text(value, MAX_URL_CHARS, field_name=field_name)
    if not text.startswith("https://"):
        raise ContractError("%s must use https" % field_name)
    try:
        parts = urlsplit(text)
    except ValueError as exc:  # pragma: no cover - urlsplit is forgiving
        raise ContractError("%s is not a valid URL" % field_name) from exc
    host_set = {hosts} if isinstance(hosts, str) else set(hosts)
    if parts.scheme != "https":
        raise ContractError("%s must use https" % field_name)
    if parts.hostname not in host_set:
        raise ContractError("%s is not an official documentation host" % field_name)
    if parts.username or parts.password or parts.port is not None:
        raise ContractError("%s must not include credentials or a port" % field_name)
    path = parts.path or "/"
    if not any(path.startswith(prefix) for prefix in prefixes):
        raise ContractError("%s is not an official documentation path" % field_name)
    if not parts.path and not parts.fragment:
        raise ContractError("%s must name a documentation page" % field_name)
    return text


def validate_autodesk_url(value: Any, *, field_name: str = "doc_url") -> str:
    return _official_https_url(
        value,
        hosts=_AUTODESK_URL_HOSTS,
        prefixes=(_AUTODESK_URL_PREFIX,),
        field_name=field_name,
    )


def validate_houdini_url(value: Any, *, field_name: str = "doc_url") -> str:
    return _official_https_url(
        value,
        hosts=_HOUDINI_URL_HOST,
        prefixes=(_HOUDINI_URL_PREFIX,),
        field_name=field_name,
    )


def validate_blender_url(value: Any, *, field_name: str = "doc_url") -> str:
    return _official_https_url(
        value,
        hosts=_BLENDER_URL_HOST,
        prefixes=_BLENDER_URL_PREFIXES,
        field_name=field_name,
    )


# ---------------------------------------------------------------------------
# Descriptors and immutable data records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExecutionDescriptor:
    """One non-executable Max route declaration.

    ``api`` and ``doc_url`` are display/search metadata.  Dispatch uses only
    ``kind`` and ``target`` through the fixed native registry.
    """

    kind: str
    target: str
    api: str
    doc_url: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or self.kind not in TARGETS_BY_KIND:
            raise ContractError("Unsupported execution kind: %r" % (self.kind,))
        targets = TARGETS_BY_KIND[self.kind]
        if not isinstance(self.target, str) or self.target not in targets:
            raise ContractError(
                "Target %r is not allowed for kind %r" % (self.target, self.kind)
            )
        bounded_text(self.api, MAX_TEXT_CHARS, field_name="api")
        validate_autodesk_url(self.doc_url)


@dataclass(frozen=True)
class DccReference:
    """Display-only Houdini or Blender reference metadata."""

    api: str
    doc_url: str
    note: str = ""

    def __post_init__(self) -> None:
        bounded_text(self.api, MAX_TEXT_CHARS, field_name="api")
        bounded_text(self.doc_url, MAX_URL_CHARS, field_name="doc_url")
        if not self.doc_url.startswith("https://"):
            raise ContractError("doc_url must use https")
        if not isinstance(self.note, str):
            raise ContractError("note must be a string")
        if self.note:
            bounded_text(self.note, MAX_TEXT_CHARS, field_name="note")


@dataclass(frozen=True)
class MaxRoutes:
    """The optional selected/empty routes for one context and maneuver."""

    with_selection: ExecutionDescriptor | None = None
    empty_selection: ExecutionDescriptor | None = None

    def __post_init__(self) -> None:
        for name in ("with_selection", "empty_selection"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, ExecutionDescriptor):
                raise ContractError("%s must be an ExecutionDescriptor or None" % name)
        if self.with_selection is None and self.empty_selection is None:
            raise ContractError("A context route needs at least one selection route")

    def route_for(self, has_selection: bool) -> ExecutionDescriptor | None:
        return self.with_selection if has_selection else self.empty_selection


@dataclass(frozen=True)
class ContextSpec:
    """One maneuver/context bundle across the three DCC vocabularies."""

    max2025: MaxRoutes
    houdini: DccReference
    blender: DccReference

    def __post_init__(self) -> None:
        if not isinstance(self.max2025, MaxRoutes):
            raise ContractError("max2025 must be MaxRoutes")
        if not isinstance(self.houdini, DccReference):
            raise ContractError("houdini must be a DccReference")
        if not isinstance(self.blender, DccReference):
            raise ContractError("blender must be a DccReference")

    def route_for(self, has_selection: bool) -> ExecutionDescriptor | None:
        return self.max2025.route_for(has_selection)


def _freeze_aliases(
    aliases: Mapping[str, tuple[str, ...]],
) -> Mapping[str, tuple[str, ...]]:
    frozen: dict[str, tuple[str, ...]] = {}
    for source in MAX_ALIAS_SOURCES:
        values = aliases.get(source, ())
        frozen[source] = tuple(values)
    return MappingProxyType(frozen)


@dataclass(frozen=True)
class ManeuverSpec:
    """One validated maneuver.  Declaration order lives in the atlas."""

    id: str
    label: str
    group: str
    aliases: Mapping[str, tuple[str, ...]]
    contexts: Mapping[ManeuverContext, ContextSpec]

    def __post_init__(self) -> None:
        validate_maneuver_id(self.id, field_name="maneuver id")
        bounded_text(self.label, MAX_TEXT_CHARS, field_name="label")
        if self.group not in MANEUVER_GROUPS:
            raise ContractError("Unsupported maneuver group: %r" % (self.group,))
        if not isinstance(self.aliases, Mapping):
            raise ContractError("aliases must be a mapping")
        if set(self.aliases) != set(MAX_ALIAS_SOURCES):
            raise ContractError(
                "aliases must have exactly: %s" % ", ".join(MAX_ALIAS_SOURCES)
            )
        for source, values in self.aliases.items():
            if not isinstance(values, (tuple, list)):
                raise ContractError("aliases[%s] must be a sequence" % source)
            if not values:
                raise ContractError("aliases[%s] must not be empty" % source)
            if len(values) > MAX_ALIASES_PER_SOURCE:
                raise ContractError("aliases[%s] has too many entries" % source)
            for alias in values:
                bounded_text(
                    alias, MAX_ALIAS_CHARS, field_name="alias for %s" % source
                )
        frozen_contexts: dict[ManeuverContext, ContextSpec] = {}
        if not isinstance(self.contexts, Mapping) or not self.contexts:
            raise ContractError("contexts must be a nonempty mapping")
        for raw_context, spec in self.contexts.items():
            context = (
                raw_context
                if isinstance(raw_context, ManeuverContext)
                else ManeuverContext(raw_context)
            )
            if not isinstance(spec, ContextSpec):
                raise ContractError("context spec must be a ContextSpec")
            if context in frozen_contexts:
                raise ContractError("duplicate context: %s" % context.value)
            frozen_contexts[context] = spec
        object.__setattr__(self, "aliases", _freeze_aliases(self.aliases))
        object.__setattr__(self, "contexts", MappingProxyType(frozen_contexts))

    def supports(self, context: ManeuverContext) -> bool:
        return context in self.contexts

    def route_for(
        self, context: ManeuverContext, has_selection: bool
    ) -> ExecutionDescriptor | None:
        spec = self.contexts.get(context)
        return spec.route_for(has_selection) if spec is not None else None


@dataclass(frozen=True)
class ManeuverAtlas:
    """An immutable, ordered atlas plus optional overlay hints."""

    schema_version: int
    maneuvers: tuple[ManeuverSpec, ...]
    pinned: tuple[str, ...] = ()
    hidden: tuple[str, ...] = ()
    user_aliases: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    _by_id: Mapping[str, ManeuverSpec] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )
    _order: Mapping[str, int] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if isinstance(self.schema_version, bool) or not isinstance(
            self.schema_version, int
        ):
            raise ContractError("schema_version must be an integer")
        if self.schema_version != 2:
            raise ContractError("schema_version must be 2")
        if not isinstance(self.maneuvers, tuple):
            raise ContractError("maneuvers must be a tuple")
        if not self.maneuvers:
            raise ContractError("atlas must contain at least one maneuver")
        if len(self.maneuvers) > MAX_MANEUVERS:
            raise ContractError("atlas exceeds the maneuver ceiling")
        by_id: dict[str, ManeuverSpec] = {}
        order: dict[str, int] = {}
        for index, spec in enumerate(self.maneuvers):
            if not isinstance(spec, ManeuverSpec):
                raise ContractError("maneuvers must contain ManeuverSpec values")
            if spec.id in by_id:
                raise ContractError("duplicate maneuver ID: %s" % spec.id)
            by_id[spec.id] = spec
            order[spec.id] = index
        for name, values in (("pinned", self.pinned), ("hidden", self.hidden)):
            if not isinstance(values, tuple):
                raise ContractError("%s must be a tuple" % name)
            seen: set[str] = set()
            for value in values:
                if value not in by_id:
                    raise ContractError("%s contains unknown ID: %s" % (name, value))
                if value in seen:
                    raise ContractError("%s contains a duplicate ID: %s" % (name, value))
                seen.add(value)
        if not isinstance(self.user_aliases, Mapping):
            raise ContractError("user_aliases must be a mapping")
        frozen_aliases: dict[str, tuple[str, ...]] = {}
        for maneuver_id, values in self.user_aliases.items():
            if maneuver_id not in by_id:
                raise ContractError(
                    "user_aliases contains unknown ID: %s" % maneuver_id
                )
            if not isinstance(values, (tuple, list)):
                raise ContractError("user_aliases values must be sequences")
            checked: list[str] = []
            for value in values:
                bounded_text(value, MAX_ALIAS_CHARS, field_name="user alias")
                checked.append(value)
            frozen_aliases[maneuver_id] = tuple(checked)
        object.__setattr__(self, "_by_id", MappingProxyType(by_id))
        object.__setattr__(self, "_order", MappingProxyType(order))
        object.__setattr__(
            self, "user_aliases", MappingProxyType(frozen_aliases)
        )

    def by_id(self, maneuver_id: str) -> ManeuverSpec | None:
        return self._by_id.get(maneuver_id)

    get = by_id

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(spec.id for spec in self.maneuvers)

    def order_of(self, maneuver_id: str) -> int:
        return self._order[maneuver_id]

    def is_hidden(self, maneuver_id: str) -> bool:
        return maneuver_id in self.hidden

    def is_pinned(self, maneuver_id: str) -> bool:
        return maneuver_id in self.pinned

    def aliases_for(self, maneuver_id: str) -> tuple[str, ...]:
        """Shipped DCC aliases followed by deduplicated user aliases."""

        spec = self._by_id.get(maneuver_id)
        if spec is None:
            return ()
        shipped: list[str] = []
        for source in MAX_ALIAS_SOURCES:
            shipped.extend(spec.aliases.get(source, ()))
        shipped.extend(self.user_aliases.get(maneuver_id, ()))
        return tuple(shipped)

    def maneuvers_for_context(
        self, context: ManeuverContext | str
    ) -> tuple[ManeuverSpec, ...]:
        target = (
            context if isinstance(context, ManeuverContext) else ManeuverContext(context)
        )
        return tuple(spec for spec in self.maneuvers if spec.supports(target))


@dataclass(frozen=True)
class UserOverlay:
    """Validated optional alias/pin/hide overlay.  It is never executable."""

    schema_version: int
    aliases: Mapping[str, tuple[str, ...]]
    pinned: tuple[str, ...] = ()
    hidden: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.schema_version, bool) or not isinstance(
            self.schema_version, int
        ):
            raise ContractError("overlay schema_version must be an integer")
        if self.schema_version != 1:
            raise ContractError("overlay schema_version must be 1")
        if not isinstance(self.aliases, Mapping):
            raise ContractError("overlay aliases must be a mapping")
        frozen: dict[str, tuple[str, ...]] = {}
        for maneuver_id, values in self.aliases.items():
            validate_maneuver_id(maneuver_id, field_name="overlay maneuver id")
            if not isinstance(values, (tuple, list)):
                raise ContractError("overlay aliases must be sequences")
            checked: list[str] = []
            for alias in values:
                bounded_text(alias, MAX_ALIAS_CHARS, field_name="overlay alias")
                checked.append(alias)
            frozen[maneuver_id] = tuple(checked)
        for name, values in (("pinned", self.pinned), ("hidden", self.hidden)):
            if not isinstance(values, tuple):
                raise ContractError("overlay %s must be a tuple" % name)
            for value in values:
                validate_maneuver_id(value, field_name="overlay %s id" % name)
        object.__setattr__(self, "aliases", MappingProxyType(frozen))


# ---------------------------------------------------------------------------
# Opaque host snapshot, renderer rows/views, and requests
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ManeuverSnapshot:
    """Opaque, mutation-relevant host state captured by a V2 host.

    The tokens are host-defined identities (hashes), never live wrappers,
    names, paths, geometry arrays or callables.  ``prep_tier`` is one of
    :data:`PREP_TIERS`.
    """

    context: ManeuverContext
    target_kind: str
    target_token: str
    selection_count: int
    selection_token: str
    revision: int
    prep_tier: str
    blocked_reason: str
    hold_active: bool
    restore_or_redoing: bool
    mode_token: str
    caddy_active: bool
    safety_token: str

    def __post_init__(self) -> None:
        if isinstance(self.context, str) and not isinstance(
            self.context, ManeuverContext
        ):
            object.__setattr__(self, "context", ManeuverContext(self.context))
        if not isinstance(self.context, ManeuverContext):
            raise ContractError("snapshot context must be a ManeuverContext")
        for name in (
            "target_kind",
            "target_token",
            "selection_token",
            "blocked_reason",
            "mode_token",
            "safety_token",
        ):
            value = getattr(self, name)
            if not isinstance(value, str):
                raise ContractError("snapshot %s must be a string" % name)
        if isinstance(self.selection_count, bool) or not isinstance(
            self.selection_count, int
        ):
            raise ContractError("snapshot selection_count must be an integer")
        if self.selection_count < 0:
            raise ContractError("snapshot selection_count must be non-negative")
        if isinstance(self.revision, bool) or not isinstance(self.revision, int):
            raise ContractError("snapshot revision must be an integer")
        if self.revision < 0:
            raise ContractError("snapshot revision must be non-negative")
        if self.prep_tier not in PREP_TIERS:
            raise ContractError("Unsupported prep tier: %r" % (self.prep_tier,))
        for name in ("hold_active", "restore_or_redoing", "caddy_active"):
            if not isinstance(getattr(self, name), bool):
                raise ContractError("snapshot %s must be a bool" % name)

    def identity(self) -> tuple[Any, ...]:
        """Exact state that must still match immediately before dispatch."""

        return (
            self.context.value,
            self.target_kind,
            self.target_token,
            self.selection_count,
            self.selection_token,
            self.revision,
            self.prep_tier,
            self.blocked_reason,
            self.hold_active,
            self.restore_or_redoing,
            self.mode_token,
            self.caddy_active,
            self.safety_token,
        )


@dataclass(frozen=True)
class ManeuverRow:
    """One stable renderable row.  ``row_id`` never depends on rank or label."""

    row_id: str
    maneuver_id: str | None
    label: str
    group: str
    enabled: bool
    reason: str
    pivot_context: ManeuverContext | None
    system_command: SystemCommand | None
    score: float

    def __post_init__(self) -> None:
        bounded_text(self.row_id, MAX_ID_CHARS * 2, field_name="row_id")
        if self.maneuver_id is not None:
            validate_maneuver_id(self.maneuver_id, field_name="row maneuver id")
        if not isinstance(self.label, str) or not self.label.strip():
            raise ContractError("row label must be nonempty")
        if not isinstance(self.group, str) or not self.group.strip():
            raise ContractError("row group must be nonempty")
        if not isinstance(self.enabled, bool):
            raise ContractError("row enabled must be a bool")
        if not isinstance(self.reason, str):
            raise ContractError("row reason must be a string")
        if self.pivot_context is not None and not isinstance(
            self.pivot_context, ManeuverContext
        ):
            raise ContractError("row pivot_context must be a ManeuverContext or None")
        if self.system_command is not None and not isinstance(
            self.system_command, SystemCommand
        ):
            raise ContractError("row system_command must be a SystemCommand or None")
        if (
            isinstance(self.score, bool)
            or not isinstance(self.score, (int, float))
            or not math.isfinite(float(self.score))
        ):
            raise ContractError("row score must be a finite number")
        if self.maneuver_id is None and self.system_command is None:
            raise ContractError("a row needs a maneuver or a system command")
        if self.maneuver_id is not None and self.system_command is not None:
            raise ContractError("a row cannot be both a maneuver and a system row")

    def to_dict(self) -> dict[str, Any]:
        return {
            "row_id": self.row_id,
            "maneuver_id": self.maneuver_id,
            "label": self.label,
            "display_label": self.display_label,
            "group": self.group,
            "enabled": self.enabled,
            "reason": self.reason,
            "pivot_context": (
                self.pivot_context.value if self.pivot_context else None
            ),
            "system_command": (
                self.system_command.value if self.system_command else None
            ),
            "score": float(self.score),
        }

    @property
    def display_label(self) -> str:
        if self.pivot_context is None:
            return self.label
        return "[→ %s] %s" % (self.pivot_context.value.title(), self.label)


@dataclass(frozen=True)
class ConfirmationPrompt:
    """One-use destructive confirmation carried by an inline palette view."""

    token: str
    row_id: str
    maneuver_id: str
    message: str
    destructive_scope: str

    def __post_init__(self) -> None:
        bounded_text(self.token, MAX_TEXT_CHARS, field_name="confirmation token")
        bounded_text(self.row_id, MAX_ID_CHARS * 2, field_name="confirmation row_id")
        validate_maneuver_id(
            self.maneuver_id, field_name="confirmation maneuver id"
        )
        bounded_text(self.message, MAX_TEXT_CHARS * 2, field_name="confirmation message")
        bounded_text(
            self.destructive_scope,
            MAX_TEXT_CHARS * 2,
            field_name="confirmation scope",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "row_id": self.row_id,
            "maneuver_id": self.maneuver_id,
            "message": self.message,
            "destructive_scope": self.destructive_scope,
        }


@dataclass(frozen=True)
class PaletteView:
    """The sole renderer input for the V2 palette."""

    session_id: str
    generation: int
    context: ManeuverContext | None
    selection_count: int
    query: str
    rows: tuple[ManeuverRow, ...]
    highlighted_row_id: str | None
    status: str
    busy: bool
    last_result: LaunchResult | None
    pending_confirmation: ConfirmationPrompt | None

    def __post_init__(self) -> None:
        if not isinstance(self.session_id, str):
            raise ContractError("session_id must be a string")
        if isinstance(self.generation, bool) or not isinstance(self.generation, int):
            raise ContractError("generation must be an integer")
        if self.generation < 0:
            raise ContractError("generation must be non-negative")
        if self.context is not None and not isinstance(
            self.context, ManeuverContext
        ):
            raise ContractError("view context must be a ManeuverContext or None")
        if isinstance(self.selection_count, bool) or not isinstance(
            self.selection_count, int
        ):
            raise ContractError("selection_count must be an integer")
        if self.selection_count < 0:
            raise ContractError("selection_count must be non-negative")
        if not isinstance(self.query, str):
            raise ContractError("query must be a string")
        if not isinstance(self.rows, tuple) or not all(
            isinstance(row, ManeuverRow) for row in self.rows
        ):
            raise ContractError("rows must be a tuple of ManeuverRow")
        if self.highlighted_row_id is not None and not isinstance(
            self.highlighted_row_id, str
        ):
            raise ContractError("highlighted_row_id must be a string or None")
        if not isinstance(self.status, str):
            raise ContractError("status must be a string")
        if not isinstance(self.busy, bool):
            raise ContractError("busy must be a bool")
        if self.last_result is not None and not isinstance(
            self.last_result, LaunchResult
        ):
            raise ContractError("last_result must be a LaunchResult or None")
        if self.pending_confirmation is not None and not isinstance(
            self.pending_confirmation, ConfirmationPrompt
        ):
            raise ContractError(
                "pending_confirmation must be a ConfirmationPrompt or None"
            )

    def row(self, row_id: str | None) -> ManeuverRow | None:
        if not row_id:
            return None
        for row in self.rows:
            if row.row_id == row_id:
                return row
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "generation": self.generation,
            "context": self.context.value if self.context else None,
            "selection_count": self.selection_count,
            "query": self.query,
            "rows": [row.to_dict() for row in self.rows],
            "highlighted_row_id": self.highlighted_row_id,
            "status": self.status,
            "busy": self.busy,
            "last_result": (
                {
                    "status": self.last_result.status,
                    "action_id": self.last_result.action_id,
                    "message": self.last_result.message,
                }
                if self.last_result
                else None
            ),
            "pending_confirmation": (
                self.pending_confirmation.to_dict()
                if self.pending_confirmation
                else None
            ),
        }


@dataclass(frozen=True)
class ManeuverRequest:
    """One explicit dispatch request.  Only the controller may create these."""

    maneuver_id: str
    expected_snapshot: ManeuverSnapshot
    pivot_context: ManeuverContext | None = None
    convert_selection: bool = False
    confirmed: bool = False

    def __post_init__(self) -> None:
        validate_maneuver_id(self.maneuver_id)
        if not isinstance(self.expected_snapshot, ManeuverSnapshot):
            raise ContractError("expected_snapshot must be a ManeuverSnapshot")
        if self.pivot_context is not None and not isinstance(
            self.pivot_context, ManeuverContext
        ):
            raise ContractError("pivot_context must be a ManeuverContext or None")
        if not isinstance(self.convert_selection, bool):
            raise ContractError("convert_selection must be a bool")
        if not isinstance(self.confirmed, bool):
            raise ContractError("confirmed must be a bool")


@runtime_checkable
class ManeuverHost(Protocol):
    """The only interface through which V2 UI reaches a host."""

    def snapshot(self) -> ManeuverSnapshot:
        ...

    def availability(
        self,
        maneuver_id: str,
        snapshot: ManeuverSnapshot,
        *,
        pivot_context: ManeuverContext | None = None,
    ) -> Availability:
        ...

    def execute(self, request: ManeuverRequest) -> LaunchResult:
        ...

    def switch_context(
        self,
        context: ManeuverContext,
        *,
        expected_snapshot: ManeuverSnapshot,
        convert_selection: bool = False,
    ) -> LaunchResult:
        ...


AvailabilityProvider = Callable[[str, ManeuverContext | None], Availability]


# ---------------------------------------------------------------------------
# V1 <-> V2 context translation
# ---------------------------------------------------------------------------


def to_legacy_context(context: ManeuverContext | str) -> ContextLevel:
    """Map V2 ``face`` to V1 ``polygon``; all other values are identical."""

    value = (
        context if isinstance(context, ManeuverContext) else ManeuverContext(context)
    )
    if value is ManeuverContext.FACE:
        return ContextLevel.POLYGON
    return ContextLevel(value.value)


def from_legacy_context(context: ContextLevel | str) -> ManeuverContext:
    """Map V1 ``polygon`` to V2 ``face``; all other values are identical."""

    value = context if isinstance(context, ContextLevel) else ContextLevel(context)
    if value is ContextLevel.POLYGON:
        return ManeuverContext.FACE
    return ManeuverContext(value.value)


def legacy_level_index(context: ManeuverContext | str) -> int:
    """Return the Max sub-object level (0-5) for a V2 context."""

    return CONTEXT_INDEX[
        context if isinstance(context, ManeuverContext) else ManeuverContext(context)
    ]

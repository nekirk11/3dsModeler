"""Original curated catalog and stable row construction.

Row order always comes from the profile.  Availability only toggles enabled
state and explanatory text; filtering only removes rows for the current
query.  There is no usage-based or adaptive sorting.
"""

from __future__ import annotations

from typing import Iterable, Mapping

from .contracts import (
    ActionSpec,
    Availability,
    ContextLevel,
    LEVEL_ORDER,
    MenuRow,
)

ALL_LEVELS = LEVEL_ORDER
SUBOBJECT_LEVELS = (
    ContextLevel.VERTEX,
    ContextLevel.EDGE,
    ContextLevel.BORDER,
    ContextLevel.POLYGON,
    ContextLevel.ELEMENT,
)
OBJECT_ONLY = (ContextLevel.OBJECT,)

ACTIONS: tuple[ActionSpec, ...] = (
    ActionSpec(
        id="transform.move",
        label="Move",
        aliases=("translate", "position"),
        group="Transform",
        allowed_levels=ALL_LEVELS,
        repeatable=True,
        execution_kind="interactive",
    ),
    ActionSpec(
        id="transform.rotate",
        label="Rotate",
        aliases=("turn",),
        group="Transform",
        allowed_levels=ALL_LEVELS,
        repeatable=True,
        execution_kind="interactive",
    ),
    ActionSpec(
        id="transform.scale",
        label="Scale",
        aliases=("resize", "size"),
        group="Transform",
        allowed_levels=ALL_LEVELS,
        repeatable=True,
        execution_kind="interactive",
    ),
    ActionSpec(
        id="selection.grow",
        label="Grow selection",
        aliases=("expand selection", "grow"),
        group="Selection",
        allowed_levels=SUBOBJECT_LEVELS,
        repeatable=True,
        execution_kind="selection",
    ),
    ActionSpec(
        id="selection.shrink",
        label="Shrink selection",
        aliases=("contract selection", "shrink"),
        group="Selection",
        allowed_levels=SUBOBJECT_LEVELS,
        repeatable=True,
        execution_kind="selection",
    ),
    ActionSpec(
        id="selection.loop",
        label="Select loop",
        aliases=("edge loop", "loop"),
        group="Selection",
        allowed_levels=(ContextLevel.EDGE,),
        repeatable=True,
        execution_kind="selection",
    ),
    ActionSpec(
        id="selection.ring",
        label="Select ring",
        aliases=("edge ring", "ring"),
        group="Selection",
        allowed_levels=(ContextLevel.EDGE,),
        repeatable=True,
        execution_kind="selection",
    ),
    ActionSpec(
        id="selection.clear",
        label="Clear selection",
        aliases=("deselect", "clear"),
        group="Selection",
        allowed_levels=SUBOBJECT_LEVELS,
        repeatable=False,
        execution_kind="selection",
    ),
    ActionSpec(
        id="selection.to_border",
        label="Select border",
        aliases=("border", "select border"),
        group="Selection",
        allowed_levels=(ContextLevel.EDGE,),
        repeatable=False,
        execution_kind="selection",
    ),
    ActionSpec(
        id="selection.to_element",
        label="Select element",
        aliases=("element", "select element"),
        group="Selection",
        allowed_levels=(ContextLevel.POLYGON,),
        repeatable=False,
        execution_kind="selection",
    ),
    ActionSpec(
        id="context.object",
        label="Object level",
        aliases=("object level", "level 0"),
        group="Context",
        allowed_levels=ALL_LEVELS,
        repeatable=False,
        execution_kind="selection",
    ),
    ActionSpec(
        id="context.vertex",
        label="Vertex level",
        aliases=("vertex level", "level 1"),
        group="Context",
        allowed_levels=ALL_LEVELS,
        repeatable=False,
        execution_kind="selection",
    ),
    ActionSpec(
        id="context.edge",
        label="Edge level",
        aliases=("edge level", "level 2"),
        group="Context",
        allowed_levels=ALL_LEVELS,
        repeatable=False,
        execution_kind="selection",
    ),
    ActionSpec(
        id="context.border",
        label="Border level",
        aliases=("border level", "level 3"),
        group="Context",
        allowed_levels=ALL_LEVELS,
        repeatable=False,
        execution_kind="selection",
    ),
    ActionSpec(
        id="context.polygon",
        label="Polygon level",
        aliases=("polygon level", "level 4"),
        group="Context",
        allowed_levels=ALL_LEVELS,
        repeatable=False,
        execution_kind="selection",
    ),
    ActionSpec(
        id="context.element",
        label="Element level",
        aliases=("element level", "level 5"),
        group="Context",
        allowed_levels=ALL_LEVELS,
        repeatable=False,
        execution_kind="selection",
    ),
    ActionSpec(
        id="model.extrude",
        label="Extrude",
        aliases=("extrude polygon", "extrude"),
        group="Modeling",
        allowed_levels=(ContextLevel.POLYGON,),
        repeatable=True,
        execution_kind="immediate",
    ),
    ActionSpec(
        id="model.bevel",
        label="Bevel",
        aliases=("bevel polygon", "bevel"),
        group="Modeling",
        allowed_levels=(ContextLevel.POLYGON,),
        repeatable=True,
        execution_kind="immediate",
    ),
    ActionSpec(
        id="model.inset",
        label="Inset",
        aliases=("inset polygon", "inset"),
        group="Modeling",
        allowed_levels=(ContextLevel.POLYGON,),
        repeatable=True,
        execution_kind="immediate",
    ),
    ActionSpec(
        id="model.chamfer",
        label="Chamfer",
        aliases=("edge chamfer", "chamfer edges", "chamfer"),
        group="Modeling",
        allowed_levels=(ContextLevel.VERTEX, ContextLevel.EDGE),
        repeatable=True,
        execution_kind="immediate",
    ),
    ActionSpec(
        id="model.cap",
        label="Cap",
        aliases=("close border", "cap border", "cap"),
        group="Modeling",
        allowed_levels=(ContextLevel.BORDER,),
        repeatable=False,
        execution_kind="immediate",
    ),
    ActionSpec(
        id="model.connect",
        label="Connect",
        aliases=("connect edges", "connect vertices"),
        group="Modeling",
        allowed_levels=(ContextLevel.VERTEX, ContextLevel.EDGE),
        repeatable=True,
        execution_kind="immediate",
    ),
    ActionSpec(
        id="model.bridge",
        label="Bridge",
        aliases=("bridge edges", "bridge border", "bridge polygon"),
        group="Modeling",
        allowed_levels=(
            ContextLevel.EDGE,
            ContextLevel.BORDER,
            ContextLevel.POLYGON,
        ),
        repeatable=True,
        execution_kind="immediate",
    ),
    ActionSpec(
        id="model.weld",
        label="Threshold weld (border vertices)",
        aliases=("weld vertices", "threshold weld"),
        group="Modeling",
        allowed_levels=(ContextLevel.VERTEX,),
        repeatable=True,
        execution_kind="immediate",
    ),
    ActionSpec(
        id="model.collapse",
        label="Collapse selected components",
        aliases=("collapse components", "collapse selection"),
        group="Modeling",
        allowed_levels=(
            ContextLevel.VERTEX,
            ContextLevel.EDGE,
            ContextLevel.POLYGON,
        ),
        repeatable=False,
        execution_kind="immediate",
    ),
    ActionSpec(
        id="model.delete",
        label="Delete selected components",
        aliases=("delete components", "delete selection"),
        group="Modeling",
        allowed_levels=(
            ContextLevel.VERTEX,
            ContextLevel.EDGE,
            ContextLevel.BORDER,
            ContextLevel.POLYGON,
            ContextLevel.ELEMENT,
        ),
        repeatable=False,
        execution_kind="immediate",
    ),
    ActionSpec(
        id="model.remove",
        label="Remove selected components",
        aliases=("remove components", "remove selection"),
        group="Modeling",
        allowed_levels=(ContextLevel.VERTEX, ContextLevel.EDGE),
        repeatable=False,
        execution_kind="immediate",
    ),
    ActionSpec(
        id="model.split_edges",
        label="Split edges",
        aliases=("split edges",),
        group="Modeling",
        allowed_levels=(ContextLevel.EDGE,),
        repeatable=True,
        execution_kind="immediate",
    ),
    ActionSpec(
        id="model.break_vertices",
        label="Break vertices",
        aliases=("break vertices",),
        group="Modeling",
        allowed_levels=(ContextLevel.VERTEX,),
        repeatable=True,
        execution_kind="immediate",
    ),
    ActionSpec(
        id="model.planar_x",
        label="Make planar X",
        aliases=("planar x",),
        group="Modeling",
        allowed_levels=SUBOBJECT_LEVELS,
        repeatable=True,
        execution_kind="immediate",
    ),
    ActionSpec(
        id="model.planar_y",
        label="Make planar Y",
        aliases=("planar y",),
        group="Modeling",
        allowed_levels=SUBOBJECT_LEVELS,
        repeatable=True,
        execution_kind="immediate",
    ),
    ActionSpec(
        id="model.planar_z",
        label="Make planar Z",
        aliases=("planar z",),
        group="Modeling",
        allowed_levels=SUBOBJECT_LEVELS,
        repeatable=True,
        execution_kind="immediate",
    ),
    ActionSpec(
        id="model.flip_normals",
        label="Flip normals",
        aliases=("reverse normals",),
        group="Modeling",
        allowed_levels=(ContextLevel.POLYGON, ContextLevel.ELEMENT),
        repeatable=True,
        execution_kind="immediate",
    ),
    ActionSpec(
        id="model.cut",
        label="Cut",
        aliases=("cut tool",),
        group="Modeling",
        allowed_levels=(
            ContextLevel.VERTEX,
            ContextLevel.EDGE,
            ContextLevel.POLYGON,
        ),
        repeatable=False,
        execution_kind="interactive",
    ),
    ActionSpec(
        id="model.target_weld",
        label="Target weld",
        aliases=("weld target",),
        group="Modeling",
        allowed_levels=(ContextLevel.VERTEX,),
        repeatable=False,
        execution_kind="interactive",
    ),
    ActionSpec(
        id="model.exit_tool",
        label="Exit tool",
        aliases=("exit command mode",),
        group="Modeling",
        allowed_levels=SUBOBJECT_LEVELS,
        repeatable=False,
        execution_kind="interactive",
    ),
    ActionSpec(
        id="view.frame_selection",
        label="Frame selection",
        aliases=("frame", "zoom selection"),
        group="View",
        allowed_levels=ALL_LEVELS,
        repeatable=False,
        execution_kind="view",
    ),
    ActionSpec(
        id="menu.repeat",
        label="Repeat",
        aliases=("again", "repeat last"),
        group="Repeat",
        allowed_levels=ALL_LEVELS,
        repeatable=False,
        execution_kind="interactive",
    ),
)

ACTIONS_BY_ID: dict[str, ActionSpec] = {action.id: action for action in ACTIONS}

if len(ACTIONS_BY_ID) != len(ACTIONS):  # pragma: no cover - import guard
    raise RuntimeError("Duplicate action ID in catalog")

REPEAT_ACTION_ID = "menu.repeat"


def _matches(action: ActionSpec, tokens: Iterable[str]) -> bool:
    """Case-insensitive token containment over the label and original aliases."""

    haystacks = [action.label.lower()]
    haystacks.extend(alias.lower() for alias in action.aliases)
    for token in tokens:
        if not any(token in haystack for haystack in haystacks):
            return False
    return True


def _repeat_availability(
    context: ContextLevel,
    availability: Mapping[str, Availability],
    last_repeat_id: str | None,
) -> tuple[bool, str]:
    if not last_repeat_id:
        return False, "No repeatable action has launched yet."
    spec = ACTIONS_BY_ID.get(last_repeat_id)
    if spec is None or not spec.repeatable:
        return False, "The last launch is not repeatable."
    if context not in spec.allowed_levels:
        return False, "The last action is not available in this context."
    available = availability.get(spec.id)
    if available is None:
        return False, "Host binding not verified."
    if not available.enabled:
        return False, available.reason
    return True, ""


def build_rows(
    profile,
    context: ContextLevel | str,
    availability: Mapping[str, Availability],
    last_repeat_id: str | None,
    query: str,
) -> list[MenuRow]:
    """Build the ordered row list for ``context`` and ``query``.

    Hidden actions are omitted; unavailable actions remain with a reason.
    Search never reorders rows; clearing ``query`` restores profile order.
    """

    level = context if isinstance(context, ContextLevel) else ContextLevel(context)
    tokens = str(query or "").lower().split()
    rows: list[MenuRow] = []

    for entry in profile.contexts[level.value]:
        if not entry.visible:
            continue
        spec = ACTIONS_BY_ID.get(entry.id)
        if spec is None:  # A validated profile cannot reach this.
            continue
        if level not in spec.allowed_levels:
            continue
        if tokens and not _matches(spec, tokens):
            continue

        if spec.id == REPEAT_ACTION_ID:
            enabled, reason = _repeat_availability(level, availability, last_repeat_id)
            label = spec.label
            if last_repeat_id:
                last_spec = ACTIONS_BY_ID.get(last_repeat_id)
                if last_spec is not None:
                    label = f"Repeat: relaunch {last_spec.label}"
        else:
            label = spec.label
            available = availability.get(spec.id)
            if available is None:
                enabled, reason = False, "Host binding not verified."
            else:
                enabled, reason = available.enabled, available.reason

        rows.append(
            MenuRow(
                action_id=spec.id,
                label=label,
                group=spec.group,
                enabled=enabled,
                reason=reason,
                execution_kind=spec.execution_kind,
            )
        )
    return rows

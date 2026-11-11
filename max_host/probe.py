"""Read-only diagnostics for 3ds Max 2025.

``build_report()``/``report()`` read the current host without creating a
controller, converting/creating geometry, entering a mode, changing
selection/level/settings, or starting an undo chunk.  A *fresh* observation
uses a temporary :class:`~max_host.adapter.MaxHostAdapter`; it therefore has no
knowledge of a live adapter's historical rollback latch.  A supplied
``ControllerSummary`` is shown separately as cached UI state.

The default report is privacy-conscious: node/scene/user names, paths,
handles/revisions, component lists, parameter values, raw exception text and
arbitrary result text are omitted.  Disabled reasons are reduced to a finite
category.  ``include_details=True`` appends bounded original last-result and
blocked reasons after an explicit local-private warning.

A report is documentation/diagnostics only.  It never changes native
``runtime_status='not_run'`` metadata and is not evidence that any operation
works.
"""

from __future__ import annotations

import importlib.util
import platform
import re
import threading
from typing import Any, Dict, List, Optional, Tuple

from ..core.contracts import ContextLevel, ContextSnapshot, LaunchResult
from .adapter import MaxHostAdapter
from .bindings import (
    CATALOG_ACTION_IDS,
    EDITABLEPOLY_PROPERTIES_REFERENCE,
    EDITABLEPOLY_REFERENCE,
    NATIVE_BINDINGS,
)
from .operations import _property_owner

_REPORT_SCHEMA = "max2025-diagnostics/1"
_MAX_DETAIL_ITEM = 500
_MAX_DETAIL_TOTAL = 32 * 1024

#: Read-only informational properties owned by the node/base object.  These
#: mirror the documented soft-selection/constraint and temporary operation
#: properties in ``operations``.  They are not all required by a binding.
_INFORMATIONAL_BASE_PROPERTIES = (
    "useSoftSel",
    "ssUseEdgeDist",
    "ssEdgeDist",
    "affectBackfacing",
    "falloff",
    "pinch",
    "bubble",
    "lockSoftSel",
    "constrainType",
    "preserveUVs",
    "extrusionType",
    "insetAmount",
    "insetType",
    "connectEdgeSegments",
    "connectEdgePinch",
    "connectEdgeSlide",
    "bridgeSelected",
    "bridgeSegments",
    "bridgeTaper",
    "bridgeBias",
    "bridgeTwist1",
    "bridgeTwist2",
    "weldThreshold",
)

#: Informational methods queried only for presence/callability, never invoked.
_INFORMATIONAL_INTERFACE_METHODS = (
    "buttonOp",
    "chamferVertices",
    "chamferEdges",
    "ConnectVertices",
    "ConnectEdges",
    "ReadyToBridgeFlagged",
    "Remove",
    "splitEdges",
    "breakVerts",
    "MakePlanarIn",
    "flipNormals",
    "SelectBorder",
    "SelectElement",
    "SelectEdgeLoop",
    "SelectEdgeRing",
    "getCommandMode",
    "toggleCommandMode",
    "exitCommandModes",
)

#: Actions in the first manual validation gate.  A missing member here is
#: reported as P0-relevant; informational members never are.
_P0_ACTIONS = (
    "model.extrude",
    "model.inset",
    "model.bevel",
    "model.chamfer",
    "selection.grow",
    "selection.shrink",
    "selection.loop",
    "selection.ring",
    "selection.clear",
    "transform.move",
    "transform.rotate",
    "transform.scale",
    "view.frame_selection",
)

_TASK_MODES = frozenset(
    {"create", "modify", "hierarchy", "motion", "display", "utility"}
)
_TOOL_MODES = frozenset(
    {"select", "move", "rotate", "scale", "uscale", "nuscale", "squash", "none"}
)
_EP_MODES = frozenset(
    {
        "none",
        "weld",
        "cutvertex",
        "cutedge",
        "cutface",
        "chamfervertex",
        "chamferedge",
    }
)
_CATALOG_IDS = frozenset(CATALOG_ACTION_IDS)
_STATUSES = frozenset(LaunchResult.STATUSES)
_TARGET_KINDS = frozenset(ContextSnapshot.TARGET_KINDS)
_PHASES = frozenset({"closed", "held", "latched", "search"})


def _assert_main_thread() -> None:
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError(
            "Capability diagnostics must run on the 3ds Max main thread."
        )


def _sanitize(value: Any, limit: int = 256) -> str:
    """Return bounded text with control/escape characters stripped."""

    try:
        text = str(value)
    except Exception:
        return "unreadable"
    # ANSI/escape sequences, then remaining C0/C1 controls.
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    text = "".join(
        " " if (ord(char) < 32 or 0x7F <= ord(char) <= 0x9F) else char
        for char in text
    )
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: max(0, limit - 3)] + "..."
    return text


def _is_unset(value: Any) -> bool:
    if value is None:
        return True
    try:
        return str(value).strip().lower() in {"undefined", "none"}
    except Exception:
        return False


def _normalize_mode(value: Any) -> str:
    if value is None or _is_unset(value):
        return "none"
    try:
        text = str(value).strip().lower()
    except Exception:
        return "unknown"
    text = text.lstrip("#").strip("\"'")
    if text in ("-1", "none"):
        return "none"
    return text or "none"


def _known_mode(value: Any, allowed: frozenset) -> str:
    text = _normalize_mode(value)
    return text if text in allowed else "unknown"


def _known_context(value: Any) -> str:
    if value is None:
        return "none"
    if isinstance(value, ContextLevel):
        return value.value
    try:
        text = str(value).strip().lower()
    except Exception:
        return "unknown"
    return text if text in {
        "object",
        "vertex",
        "edge",
        "border",
        "polygon",
        "face",
        "element",
    } else "unknown"


def _known_phase(value: Any) -> str:
    try:
        text = str(value).strip().lower()
    except Exception:
        return "unknown"
    return text if text in _PHASES else "unknown"


def _known_catalog_id(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    return value if value in _CATALOG_IDS else None


def _known_status(value: Any) -> str:
    try:
        text = str(value)
    except Exception:
        return "unknown"
    return text if text in _STATUSES else "unknown"


def _count(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return "unknown"
    return str(value)


def _category(reason: Any) -> str:
    """Reduce a trusted or arbitrary reason to one finite category."""

    try:
        text = str(reason).strip().lower()
    except Exception:
        return "details withheld"
    if not text:
        return "details withheld"
    if any(
        key in text
        for key in ("rollback", "latched", "blind undo", "pre-edit state")
    ):
        return "rollback lockout"
    if "hold" in text or "undo/redo" in text:
        return "hold"
    if (
        "command mode" in text
        or "toolmode" in text
        or "togglecommandmode" in text
    ):
        return "command mode"
    if (
        "required native member" in text
        or "not callable" in text
        or ("member" in text and "unavailable" in text)
    ):
        return "member unavailable"
    if (
        "time" in text
        or "currenttime" in text
        or "unreadable" in text
        or "could not be read" in text
    ):
        return "unreadable state"
    if any(
        key in text
        for key in (
            "target",
            "editable poly",
            "instance",
            "modify panel",
            "geometry",
            "supported object",
        )
    ):
        return "target"
    if any(
        key in text
        for key in (
            "select",
            "context",
            "component",
            "sub-object",
            "subobject",
            "level",
        )
    ):
        return "context/selection"
    return "details withheld"


def _qt_version() -> str:
    try:
        spec = importlib.util.find_spec("PySide6")
    except Exception:
        spec = None
    if spec is None:
        return "PySide6 unavailable"
    try:
        import PySide6  # type: ignore[import-not-found]

        version = getattr(PySide6, "__version__", "unknown")
    except Exception:
        # Never expose an arbitrary import error/path in the default report.
        return "PySide6 import failed"
    return "PySide6 %s" % _sanitize(version, 64)


def _import_pymxs_runtime() -> Any:
    """Lazy pymxs import kept separate so tests can simulate absence."""

    from pymxs import runtime as max_runtime  # type: ignore[import-not-found]

    return max_runtime


def _load_runtime(runtime: Any) -> Tuple[Any, str]:
    if runtime is not None:
        return runtime, ""
    try:
        return _import_pymxs_runtime(), ""
    except ImportError:
        return None, "host runtime unavailable"
    except Exception:
        return None, "host runtime unavailable"


def _max_build(runtime: Any, busy: bool) -> str:
    if busy:
        return "not read (prototype busy)"
    if runtime is None:
        return "unavailable (no host runtime)"
    try:
        value = runtime.maxVersion()
    except Exception:
        return "unavailable (unreadable)"
    if _is_unset(value):
        return "unavailable (unreadable)"
    return _sanitize(value, 120)


def _time_state(runtime: Any) -> str:
    """Readable/unreadable classification without exposing the value."""

    try:
        raw = runtime.currentTime
    except Exception:
        return "unreadable"
    if isinstance(raw, bool) or raw is None:
        return "unreadable"
    try:
        text = str(raw).strip()
    except Exception:
        return "unreadable"
    if not text or text.lower() in {"undefined", "none", "time:unreadable"}:
        return "unreadable"
    return "readable"


def _documented_flag(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    return None


def _hold_flag(hold: Any, name: str) -> str:
    try:
        function = getattr(hold, name, None)
    except Exception:
        return "unreadable"
    if not callable(function):
        return "unavailable"
    try:
        value = function()
    except Exception:
        return "unreadable"
    flag = _documented_flag(value)
    if flag is None:
        return "unreadable"
    return "true" if flag else "false"


def _hold_flags(runtime: Any) -> Tuple[str, str]:
    try:
        hold = getattr(runtime, "theHold", None)
    except Exception:
        hold = None
    if hold is None or _is_unset(hold):
        return "unavailable", "unavailable"
    return (
        _hold_flag(hold, "Holding"),
        _hold_flag(hold, "RestoreOrRedoing"),
    )


def _selected_node(runtime: Optional[Any]) -> Optional[Any]:
    if runtime is None:
        return None
    try:
        selection = list(runtime.selection)
    except Exception:
        return None
    return selection[0] if len(selection) == 1 else None


def _selected_object_count(runtime: Optional[Any]) -> str:
    if runtime is None:
        return "unavailable"
    try:
        selection = runtime.selection
    except Exception:
        return "unreadable"
    if _is_unset(selection):
        return "0"
    try:
        return str(len(list(selection)))
    except TypeError:
        return "1"
    except Exception:
        return "unreadable"


def _cached_availability(summary: Any, action_id: str) -> Any:
    getter = getattr(summary, "availability_for", None)
    if callable(getter):
        try:
            return getter(action_id)
        except Exception:
            return None
    try:
        pairs = getattr(summary, "availability", ()) or ()
    except Exception:
        return None
    try:
        for known_id, value in pairs:
            if known_id == action_id:
                return value
    except Exception:
        return None
    return None


def _cached_actions(summary: Any) -> List[str]:
    """Return cached action IDs in catalog order, only known catalog IDs."""

    return list(CATALOG_ACTION_IDS)


def _fresh_observation(runtime: Any) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "ok": False,
        "adapter": None,
        "snapshot": None,
        "inspection": None,
        "availability": {},
    }
    try:
        adapter = MaxHostAdapter(runtime=runtime)
        snapshot = adapter.snapshot()
        availability = adapter.availability(snapshot)
    except Exception:
        return result
    result["ok"] = True
    result["adapter"] = adapter
    result["snapshot"] = snapshot
    result["inspection"] = getattr(adapter, "_last_inspection", None)
    result["availability"] = availability
    return result


def _required_members() -> Dict[str, Dict[str, List[str]]]:
    groups: Dict[str, Dict[str, List[str]]] = {}
    for action_id in CATALOG_ACTION_IDS:
        binding = NATIVE_BINDINGS.get(action_id)
        if binding is None:
            continue
        for owner, member in binding.required_members:
            groups.setdefault(owner, {}).setdefault(member, []).append(action_id)
    return groups


def _owner_object(
    owner: str,
    runtime: Optional[Any],
    node: Optional[Any],
    property_owner: Any,
    interface: Any,
) -> Any:
    if owner == "runtime":
        return runtime
    if owner == "polyop":
        if runtime is None:
            return None
        try:
            return getattr(runtime, "polyop", None)
        except Exception:
            return None
    if owner == "toolMode":
        if runtime is None:
            return None
        try:
            return getattr(runtime, "toolMode", None)
        except Exception:
            return None
    if owner == "editable_poly":
        return interface
    if owner == "base_object":
        return property_owner
    return None


def _member_status(owner: Any, member: str) -> str:
    if owner is None:
        return "owner unavailable"
    try:
        value = getattr(owner, member)
    except AttributeError:
        return "absent"
    except Exception:
        return "unreadable"
    if _is_unset(value):
        return "absent"
    if callable(value):
        return "callable"
    return "present"


def _owner_label(owner: str) -> str:
    return {
        "runtime": "runtime",
        "polyop": "polyop",
        "toolMode": "toolMode",
        "editable_poly": "EditablePoly interface",
        "base_object": "base object (node/base-object seam)",
    }.get(owner, owner)


def _action_list(actions: List[str]) -> str:
    unique = sorted(set(actions))
    if len(unique) <= 3:
        return ", ".join(unique)
    return "%s, ..." % ", ".join(unique[:3])


def _member_report(
    runtime: Optional[Any],
    inspection: Any,
    skip_note: str = "no host runtime",
) -> Tuple[List[str], List[str], List[Tuple[str, str]]]:
    """Return (default lines, private details, P0 problems)."""

    lines: List[str] = []
    details: List[Tuple[str, str]] = []
    node = getattr(inspection, "node", None) if inspection is not None else None
    if node is None:
        node = _selected_node(runtime)

    property_owner = None
    interface = None
    if node is not None:
        try:
            property_owner = _property_owner(node)
        except Exception:
            property_owner = None
        try:
            interface = getattr(node, "EditablePoly", None)
        except Exception:
            interface = None

    groups = _required_members()
    statuses: Dict[Tuple[str, str], str] = {}
    lines.append("Member presence (read-only; callability is not execution):")
    if runtime is None:
        lines.append("  owner unavailable: %s" % skip_note)
    elif node is None:
        lines.append("  owner unavailable: no single target node")

    for owner in ("runtime", "polyop", "toolMode", "editable_poly", "base_object"):
        members = groups.get(owner)
        if not members:
            continue
        target = _owner_object(owner, runtime, node, property_owner, interface)
        lines.append("  %s required members:" % _owner_label(owner))
        if target is None:
            lines.append("    owner unavailable")
            for member in sorted(members):
                statuses[(owner, member)] = "owner unavailable"
            continue
        for member in sorted(members):
            status = _member_status(target, member)
            statuses[(owner, member)] = status
            lines.append(
                "    %s: %s (required by %s)"
                % (member, status, _action_list(members[member]))
            )

    required_base = set(groups.get("base_object", {}))
    required_interface = set(groups.get("editable_poly", {}))
    informational_base = [
        name for name in _INFORMATIONAL_BASE_PROPERTIES if name not in required_base
    ]
    informational_interface = [
        name
        for name in _INFORMATIONAL_INTERFACE_METHODS
        if name not in required_interface
    ]

    if informational_base:
        lines.append("  informational base-object properties (not P0 requirements):")
        if property_owner is None:
            lines.append("    owner unavailable")
        else:
            for name in informational_base:
                lines.append("    %s: %s" % (name, _member_status(property_owner, name)))
    if informational_interface:
        lines.append("  informational interface methods (not P0 requirements):")
        if interface is None:
            lines.append("    owner unavailable")
        else:
            for name in informational_interface:
                lines.append(
                    "    %s: %s" % (name, _member_status(interface, name))
                )

    problems: List[Tuple[str, str]] = []
    if runtime is None or node is None:
        lines.append(
            "  P0 member health: not evaluated (%s)"
            % (skip_note if runtime is None else "no single target node")
        )
    else:
        for action_id in _P0_ACTIONS:
            binding = NATIVE_BINDINGS.get(action_id)
            if binding is None:
                continue
            for owner, member in binding.required_members:
                status = statuses.get((owner, member), "owner unavailable")
                if status in ("absent", "unreadable", "owner unavailable"):
                    problems.append(
                        (
                            action_id,
                            "%s.%s is %s" % (_owner_label(owner), member, status),
                        )
                    )
        if problems:
            lines.append("  P0 member health: missing/unreadable required members")
            for action_id, problem in problems:
                lines.append("    %s: %s" % (action_id, problem))
                details.append(("%s member problem" % action_id, problem))
        else:
            lines.append(
                "  P0 member health: required members present "
                "(presence does not validate runtime behavior)"
            )
    return lines, details, problems


def _cached_lines(
    summary: Any, details: List[Tuple[str, str]]
) -> Tuple[List[str], Dict[str, Any]]:
    lines = ["Cached UI state (may be stale; read from an existing controller only):"]
    state: Dict[str, Any] = {
        "available": summary is not None,
        "lockout": False,
        "categories": set(),
    }
    if summary is None:
        lines.append(
            "  controller summary: not available "
            "(diagnostics did not initialize a controller)"
        )
        return lines, state

    lines.append("  controller summary: available (cached; may be stale)")
    phase = getattr(summary, "phase", "unknown")
    lines.append("  phase: %s" % _known_phase(phase))
    context = getattr(summary, "context", None)
    lines.append("  context: %s" % _known_context(context))
    target_kind = getattr(summary, "target_kind", "unknown")
    if not isinstance(target_kind, str) or target_kind not in _TARGET_KINDS:
        target_kind = "unknown"
    lines.append("  target kind: %s" % target_kind)
    lines.append("  selection count: %s" % _count(getattr(summary, "selection_count", None)))
    lines.append("  panel ready: %s" % bool(getattr(summary, "panel_ready", False)))
    lines.append(
        "  conflicting mode: %s" % bool(getattr(summary, "conflicting_mode", False))
    )
    lines.append("  busy: %s" % bool(getattr(summary, "busy", False)))

    last_result = getattr(summary, "last_result", None)
    if last_result is None:
        lines.append("  last result: none recorded")
    else:
        action_id = _known_catalog_id(getattr(last_result, "action_id", None))
        status = _known_status(getattr(last_result, "status", None))
        message = getattr(last_result, "message", "")
        category = _category(message)
        state["categories"].add(category)
        lines.append(
            "  last result: action=%s status=%s reason category=%s"
            % (action_id or "unknown", status, category)
        )
        details.append(("last result message", message))
        if category == "rollback lockout":
            state["lockout"] = True

    repeat_id = _known_catalog_id(getattr(summary, "last_repeat_id", None))
    lines.append("  last repeat: %s" % (repeat_id or "none"))

    lines.append("  cached per-action availability (catalog order; may be stale):")
    for action_id in _cached_actions(summary):
        value = _cached_availability(summary, action_id)
        if value is None:
            lines.append("    %s: not cached" % action_id)
            continue
        enabled = bool(getattr(value, "enabled", False))
        reason = getattr(value, "reason", "")
        if enabled:
            lines.append("    %s: enabled (cached)" % action_id)
            continue
        category = _category(reason)
        state["categories"].add(category)
        lines.append("    %s: disabled - %s (cached)" % (action_id, category))
        details.append(("%s cached reason" % action_id, reason))
        if category == "rollback lockout":
            state["lockout"] = True

    if state["lockout"]:
        lines.append(
            "  cached rollback lockout: present; fresh temporary-adapter "
            "eligibility below does not clear or override this cached lockout."
        )
    return lines, state


def _fresh_lines(
    runtime: Optional[Any],
    unavailable: str,
    busy: bool,
    details: List[Tuple[str, str]],
) -> Tuple[List[str], Dict[str, Any]]:
    lines = ["Fresh read-only capability observation:"]
    state: Dict[str, Any] = {
        "probed": False,
        "inspection": None,
        "categories": set(),
        "target_kind": None,
        "panel_ready": None,
        "conflicting": None,
        "selection_count": None,
        "level": None,
        "time": None,
        "hold": None,
        "task_mode": None,
    }
    if busy:
        lines.append(
            "  skipped: a prototype invocation/dialog is busy; close the pending "
            "dialog before retrying"
        )
        lines.append("  fresh probing did not access the host or the dialog.")
        return lines, state
    if runtime is None:
        lines.append("  skipped: %s" % unavailable)
        lines.append(
            "  no temporary probe adapter was created; live latch state is not "
            "certified."
        )
        return lines, state

    observation = _fresh_observation(runtime)
    if not observation["ok"]:
        lines.append("  unavailable: host read failed (category: unreadable state)")
        state["categories"].add("unreadable state")
        return lines, state

    snapshot = observation["snapshot"]
    inspection = observation["inspection"]
    availability = observation["availability"]
    state["probed"] = True
    state["inspection"] = inspection
    lines.append("  temporary read-only adapter; no controller/panel was created.")
    lines.append("  selected objects: %s" % _selected_object_count(runtime))
    lines.append("  selection count at level: %s" % _count(snapshot.selection_count))
    lines.append("  level: %s" % snapshot.level.value)
    lines.append("  target kind: %s" % snapshot.target_kind)
    lines.append("  panel ready: %s" % bool(snapshot.panel_ready))
    lines.append("  conflicting mode: %s" % bool(snapshot.conflicting_mode))
    state["target_kind"] = snapshot.target_kind
    state["panel_ready"] = bool(snapshot.panel_ready)
    state["conflicting"] = bool(snapshot.conflicting_mode)
    state["selection_count"] = snapshot.selection_count
    state["level"] = snapshot.level.value

    task_mode = "unknown"
    if inspection is not None and getattr(inspection, "task_mode_known", False):
        task_mode = _known_mode(getattr(inspection, "task_mode", None), _TASK_MODES)
    lines.append("  task mode: %s" % task_mode)
    state["task_mode"] = task_mode

    time_state = _time_state(runtime)
    lines.append("  current time: %s" % time_state)
    state["time"] = time_state

    holding, restoring = _hold_flags(runtime)
    lines.append("  theHold.Holding(): %s" % holding)
    lines.append("  theHold.RestoreOrRedoing(): %s" % restoring)
    state["hold"] = (holding, restoring)
    if holding == "true" or restoring == "true":
        state["categories"].add("hold")
    elif holding in ("unreadable", "unavailable") or restoring in (
        "unreadable",
        "unavailable",
    ):
        state["categories"].add("hold")

    tool_mode = "unknown"
    ep_mode = "unknown"
    if inspection is not None:
        tool_mode = _known_mode(getattr(inspection, "tool_mode", None), _TOOL_MODES)
        ep_mode = _known_mode(getattr(inspection, "ep_mode", None), _EP_MODES)
    lines.append("  tool command mode: %s" % tool_mode)
    lines.append("  EditablePoly command mode: %s" % ep_mode)
    if tool_mode == "unknown" or ep_mode == "unknown":
        state["categories"].add("command mode")
    elif tool_mode != "none" or ep_mode != "none":
        # Documented idle modes are not a conflict; real mode names are.
        if ep_mode not in _EP_MODES or tool_mode not in _TOOL_MODES:
            state["categories"].add("command mode")

    lines.append(
        "  Note: this temporary adapter has no knowledge of the live adapter's "
        "historical latch or last failure; it does not certify live latch state."
    )

    reason = ""
    if inspection is not None:
        reason = getattr(inspection, "target_reason", "") or ""
        if reason:
            state["categories"].add(_category(reason))
            details.append(("fresh target reason", reason))
        geometry_reason = getattr(inspection, "geometry_reason", "") or ""
        if geometry_reason:
            state["categories"].add(_category(geometry_reason))
            details.append(("fresh geometry reason", geometry_reason))
        hold_reason = getattr(inspection, "hold_reason", "") or ""
        if hold_reason:
            state["categories"].add(_category(hold_reason))
            details.append(("fresh hold reason", hold_reason))
        command_reason = getattr(inspection, "command_reason", "") or ""
        if command_reason:
            state["categories"].add(_category(command_reason))
            details.append(("fresh command reason", command_reason))

    lines.append(
        "  Action availability in catalog order "
        "(disabled eligibility is not runtime validation; NOT RUN):"
    )
    for action_id in CATALOG_ACTION_IDS:
        value = availability.get(action_id)
        if value is None:
            lines.append("    %s: unavailable - details withheld" % action_id)
            continue
        if value.enabled:
            lines.append("    %s: enabled (runtime not validated)" % action_id)
            continue
        category = _category(value.reason)
        state["categories"].add(category)
        lines.append("    %s: disabled - %s" % (action_id, category))
        details.append(("%s fresh reason" % action_id, value.reason))
    return lines, state


def _steps(
    runtime: Optional[Any],
    unavailable: str,
    busy: bool,
    cached: Dict[str, Any],
    fresh: Dict[str, Any],
    problems: List[Tuple[str, str]],
) -> List[str]:
    lines = ["Safe next steps (read-only; never bypass a guard):"]
    categories = set(cached.get("categories", set())) | set(
        fresh.get("categories", set())
    )
    steps: List[Tuple[str, str]] = []

    lockout = bool(cached.get("lockout")) or "rollback lockout" in categories
    if lockout:
        steps.append(
            (
                "rollback lockout",
                "STOP. Inspect the scene and history manually; do not blind Undo, "
                "retry, or reinitialize to bypass the guard.",
            )
        )
    if problems:
        steps.append(
            (
                "member unavailable",
                "Capture this report and stop that action; a required native "
                "member is missing or unreadable.",
            )
        )
    if busy:
        steps.append(
            (
                "busy",
                "Close the pending prototype parameter dialog, then retry the "
                "read-only probe.",
            )
        )
    if runtime is None and not busy:
        steps.append(
            (
                "target",
                "Run inside 3ds Max 2025 (or pass a fake runtime) for fresh host "
                "observation; cached state may still be shown.",
            )
        )
    if "hold" in categories:
        steps.append(
            (
                "hold",
                "Close any pending hold/undo/redo operation and retry; do not "
                "start a new undo chunk.",
            )
        )

    target_kind = fresh.get("target_kind")
    if target_kind == "none":
        steps.append(
            (
                "target",
                "Select one disposable, unmodified, non-instanced Editable Poly "
                "object.",
            )
        )
    elif target_kind == "multiple":
        steps.append(
            (
                "target",
                "Select exactly one disposable, unmodified, non-instanced "
                "Editable Poly object.",
            )
        )
    elif target_kind in ("other", "edit_poly"):
        steps.append(
            (
                "target",
                "Open the Modify panel on an unmodified Editable Poly base "
                "object; conversion and Edit Poly targets are not supported.",
            )
        )
    if fresh.get("panel_ready") is False:
        steps.append(
            (
                "target",
                "Open the Modify panel/base object on the selected Editable Poly.",
            )
        )
    if fresh.get("task_mode") == "unknown":
        steps.append(
            (
                "target",
                "Switch the Command Panel to the Modify task manually; the probe "
                "will not switch panels.",
            )
        )
    if fresh.get("conflicting") or "command mode" in categories:
        steps.append(
            (
                "command mode",
                "Return from the foreign native mode manually; the prototype "
                "will not exit a tool it does not own.",
            )
        )
    if fresh.get("time") == "unreadable" or "unreadable state" in categories:
        steps.append(
            (
                "unreadable state",
                "Current time or host state cannot be read; stop mutation and "
                "inspect the scene.",
            )
        )
    if (
        fresh.get("level") not in (None, "object")
        and fresh.get("selection_count") == 0
    ):
        steps.append(
            (
                "context/selection",
                "Select the required components at the current sub-object level.",
            )
        )
    if "context/selection" in categories and not any(
        category == "context/selection" for category, _ in steps
    ):
        steps.append(
            (
                "context/selection",
                "Choose the required context and component selection, then retry.",
            )
        )

    if not steps:
        steps.append(
            (
                "none",
                "No specific read-only blocker detected; native operation "
                "validation remains NOT RUN.",
            )
        )

    seen = set()
    for category, text in steps:
        if (category, text) in seen:
            continue
        seen.add((category, text))
        lines.append("  [%s] %s" % (category, text))
    if unavailable and runtime is None and not busy:
        lines.append("  [runtime] %s" % unavailable)
    return lines


def _details_lines(details: List[Tuple[str, str]]) -> List[str]:
    lines = [
        "LOCAL PRIVATE DETAILS \u2014 review before sharing",
        "This opt-in section may contain names, paths, component data, and host "
        "error text.  It is not fully redacted; do not paste it publicly without "
        "review.",
    ]
    total = 0
    for label, value in details:
        text = _sanitize(value, _MAX_DETAIL_ITEM)
        if not text:
            continue
        line = "  %s: %s" % (label, text)
        if total + len(line) + 1 > _MAX_DETAIL_TOTAL:
            lines.append("  ... details truncated at %d bytes." % _MAX_DETAIL_TOTAL)
            break
        lines.append(line)
        total += len(line) + 1
    return lines


def _v2_warning_category(value: Any) -> str:
    """Reduce an overlay warning to one finite, non-path category."""

    if not value:
        return "none"
    try:
        text = str(value).strip().lower()
    except Exception:
        return "overlay warning (details withheld)"
    if text in ("none", "no overlay", "not present"):
        return "none"
    if "symlink" in text:
        return "symlink overlay ignored"
    if "invalid" in text or "malformed" in text:
        return "invalid overlay ignored"
    if "unreadable" in text or "oserror" in text or "utf-8" in text:
        return "unreadable overlay ignored"
    if "overlay" in text and "ignore" in text:
        return "overlay ignored"
    return "overlay warning (details withheld)"


def _v2_sha(value: Any) -> str:
    if not value:
        return "unknown"
    try:
        text = str(value).strip().lower()
    except Exception:
        return "unknown"
    if re.fullmatch(r"[0-9a-f]{7,64}", text):
        return text
    return "unknown"


def _v2_state(v2: Any, busy: bool) -> Optional[Dict[str, Any]]:
    if v2 is None:
        return None
    getter = getattr(v2, "diagnostic_state", None)
    if callable(getter):
        try:
            state = getter(observe=not busy)
        except TypeError:
            try:
                state = getter()
            except Exception:
                return None
        except Exception:
            return None
        return state if isinstance(state, dict) else None
    if isinstance(v2, dict):
        return dict(v2)
    state = {}
    for name in (
        "atlas_schema",
        "atlas_entries",
        "overlay_warning",
        "context",
        "prep_tier",
        "blocked_category",
        "handler_coverage",
        "idle_mode",
        "caddy_active",
        "ambiguous_mode",
        "rollback_lockout",
        "loaded_sha",
    ):
        if hasattr(v2, name):
            state[name] = getattr(v2, name)
    return state or None


def _v2_lines(
    v2: Any, busy: bool, loaded_sha: Any
) -> List[str]:
    lines = ["V2 maneuver host (read-only; no preparation or dispatch is started):"]
    state = _v2_state(v2, busy)
    if state is None:
        lines.append("  V2 host: not attached to this report")
        lines.append("  native operation validation: NOT RUN")
        return lines
    schema = state.get("atlas_schema", "unknown")
    entries = state.get("atlas_entries", "unknown")
    lines.append("  atlas schema: %s" % _sanitize(schema, 16))
    lines.append(
        "  atlas entries: %s"
        % (entries if isinstance(entries, int) and not isinstance(entries, bool) else "unknown")
    )
    lines.append(
        "  overlay warning: %s"
        % _v2_warning_category(state.get("overlay_warning"))
    )
    context = _known_context(state.get("context"))
    lines.append("  active context: %s" % context)
    tier = str(state.get("prep_tier", "blocked")).strip().lower()
    if tier not in ("ready", "convert", "base_with_modifiers", "blocked"):
        tier = "blocked"
    category = _sanitize(state.get("blocked_category", "unavailable"), 48)
    if not category:
        category = "unavailable"
    lines.append("  preparation tier: %s (blocked category: %s)" % (tier, category))
    coverage = _sanitize(state.get("handler_coverage", "unknown"), 64)
    lines.append("  fixed handler coverage: %s" % (coverage or "unknown"))
    lines.append(
        "  idle EditablePoly mode: %s"
        % _sanitize(state.get("idle_mode", "unavailable"), 32)
    )
    lines.append(
        "  native caddy active: %s" % bool(state.get("caddy_active", False))
    )
    if state.get("ambiguous_mode"):
        lines.append(
            "  ambiguous Graphite mode: present (chaining blocked until live validation)"
        )
    lines.append(
        "  rollback lockout: %s (persistent; unload does not clear it)"
        % ("present" if state.get("rollback_lockout") else "none")
    )
    lines.append(
        "  loaded SHA: %s" % _v2_sha(loaded_sha if loaded_sha is not None else state.get("loaded_sha"))
    )
    if busy:
        lines.append(
            "  cached state used: a prototype invocation/dialog is busy; no host "
            "preparation or dispatch was attempted."
        )
    lines.append("  native operation validation: NOT RUN")
    return lines


def build_report(
    runtime: Any = None,
    *,
    summary: Any = None,
    include_details: bool = False,
    v2: Any = None,
    loaded_sha: Any = None,
) -> str:
    """Build read-only diagnostic text without mutating scene or controller state.

    ``summary`` is an optional cached :class:`~core.contracts.ControllerSummary`.
    It is reported separately from a fresh temporary-adapter observation.
    A busy summary intentionally skips all fresh host access.
    """

    _assert_main_thread()

    busy = summary is not None and bool(getattr(summary, "busy", False))
    if busy:
        # Do not touch the runtime or the pending dialog while busy.
        active_runtime, unavailable = None, "prototype busy"
    else:
        active_runtime, unavailable = _load_runtime(runtime)

    lines: List[str] = [
        "Max 2025 native modeling diagnostics (read-only)",
        "Report schema: %s" % _REPORT_SCHEMA,
        "Python: %s" % platform.python_version(),
        "Qt: %s" % _qt_version(),
        "Max build: %s" % _max_build(active_runtime, busy),
        "Native operation validation: NOT RUN "
        "(binding runtime_status=not_run; capability presence does not change this)",
        "Local only: no file, clipboard, network, or telemetry output.",
        "",
    ]

    details: List[Tuple[str, str]] = []
    cached_lines, cached_state = _cached_lines(summary, details)
    lines.extend(cached_lines)
    lines.append("")

    fresh_lines, fresh_state = _fresh_lines(
        active_runtime, unavailable, busy, details
    )
    lines.extend(fresh_lines)
    lines.append("")

    member_lines, member_details, problems = _member_report(
        active_runtime,
        fresh_state.get("inspection"),
        "fresh probing skipped (prototype busy)" if busy else "no host runtime",
    )
    details.extend(member_details)
    lines.extend(member_lines)
    lines.append("")

    lines.extend(_v2_lines(v2, busy, loaded_sha))
    lines.append("")

    lines.extend(
        _steps(
            active_runtime,
            unavailable,
            busy,
            cached_state,
            fresh_state,
            problems,
        )
    )
    lines.append("")
    lines.append(
        "Evidence is documentation-only; no operation was test-run here. "
        "Native bindings remain runtime_status=not_run."
    )
    lines.append(EDITABLEPOLY_REFERENCE)
    lines.append(EDITABLEPOLY_PROPERTIES_REFERENCE)

    if include_details:
        lines.append("")
        lines.extend(_details_lines(details))
    else:
        lines.append(
            "Default privacy mode: arbitrary names, paths, values, and error "
            "text are omitted; use include_details=True only for local review."
        )
    return "\n".join(lines)


def report(
    runtime: Any = None,
    *,
    summary: Any = None,
    include_details: bool = False,
    v2: Any = None,
    loaded_sha: Any = None,
) -> str:
    """Print and return :func:`build_report` output."""

    text = build_report(
        runtime,
        summary=summary,
        include_details=include_details,
        v2=v2,
        loaded_sha=loaded_sha,
    )
    print(text)
    return text


__all__ = ["build_report", "report"]

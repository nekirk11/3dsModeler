"""Conservative, safety-first pymxs adapter for 3ds Max 2025.

Design rules:

* ``pymxs.runtime`` is imported lazily.  Importing this module outside 3ds Max
  never touches the host, creates widgets, writes files, or imports Qt.
* Exactly one valid, unmodified, non-instanced Editable Poly base object is a
  supported sub-object mutation target.  Active Edit Poly targets, primitives,
  meshes, nonempty modifier stacks, closed Modify panels, shared/instanced
  base geometry, motion-panel state, and unknown identities fail closed.
* Snapshots are read-only.  A mutating dispatch revalidates the exact target,
  component indices, bounded geometry fingerprint, soft-selection/constraint
  state, and command-mode state before any native call.
* Opaque tokens cross the core boundary; live pymxs values never do.
* One immediate operation runs in one owned undo chunk.  Because
  ``pymxs.undo`` suppresses exceptions, completion is recorded outside the
  context and inspected only after it exits.  A failed rollback latches native
  topology disabled rather than attempting a blind undo.

No native behavior in this module was exercised against a real 3ds Max 2025
install.  All bindings are ``runtime_status='not_run'``.
"""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..core.contracts import (
    Availability,
    ContextLevel,
    ContextSnapshot,
    LaunchResult,
)
from ..core.parameters import ParameterError, parameter_schema, validate_parameters
from .bindings import (
    CATALOG_ACTION_IDS,
    NATIVE_BINDINGS,
    UNVERIFIED_SUBOBJECT_PREFIX,
    launch_native,
    unverified_reason,
)
from .operations import (
    GeometryInfo,
    GeometryReadError,
    MutableTarget,
    OperationError,
    bitarray_count,
    read_geometry,
    read_selection_indices,
    read_settings,
    target_fingerprint,
)
from .transactions import hold_state

_LEVEL_BY_SUBOBJECT = {
    0: ContextLevel.OBJECT,
    1: ContextLevel.VERTEX,
    2: ContextLevel.EDGE,
    3: ContextLevel.BORDER,
    4: ContextLevel.POLYGON,
    5: ContextLevel.ELEMENT,
}

#: Documented ``toolMode.commandMode`` idle transform modes.  The three scale
#: button modes are distinct values (#uscale/#nuscale/#squash), not just
#: ``scale``; an idle scale tool must not be mistaken for a foreign mode.
_IDLE_TOOL_MODES = frozenset(
    {
        "select",
        "move",
        "rotate",
        "scale",
        "uscale",
        "nuscale",
        "squash",
        "none",
        "-1",
        "",
    }
)
_IDLE_EP_MODES = frozenset({"none", "-1", ""})

_LEVEL_NAMES = {
    ContextLevel.OBJECT: "object",
    ContextLevel.VERTEX: "vertex",
    ContextLevel.EDGE: "edge",
    ContextLevel.BORDER: "border",
    ContextLevel.POLYGON: "polygon",
    ContextLevel.ELEMENT: "element",
}


def _digest(*parts: object) -> str:
    hasher = hashlib.sha256()
    for part in parts:
        hasher.update(str(part).encode("utf-8", "backslashreplace"))
        hasher.update(b"\x00")
    return hasher.hexdigest()


def _is_undefined(value: Any) -> bool:
    if value is None:
        return True
    try:
        return str(value).strip().lower() in {"undefined", "none"}
    except Exception:
        return False


def _is_unset(value: Any) -> bool:
    """True only for None/undefined; the MAXScript name ``none`` is a value."""

    if value is None:
        return True
    try:
        return str(value).strip().lower() == "undefined"
    except Exception:
        return False


def _normalize_mode(value: Any) -> str:
    if value is None or _is_undefined(value):
        return "none"
    try:
        text = str(value).strip().lower()
    except Exception:
        return "unknown"
    text = text.lstrip("#").strip("\"'")
    if text in ("-1", "none"):
        return "none"
    return text or "none"


def _class_name(runtime: Any, value: Any) -> str:
    try:
        cls = runtime.classOf(value)
    except Exception:
        return "unknown"
    name = getattr(cls, "__name__", None)
    if isinstance(name, str) and name:
        return name
    return str(cls)


def _class_is(runtime: Any, value: Any, expected: str) -> bool:
    if value is None or _is_undefined(value):
        return False
    expected_cls = getattr(runtime, expected, None)
    if expected_cls is not None:
        try:
            if runtime.isKindOf(value, expected_cls):
                return True
        except Exception:
            pass
        try:
            if runtime.classOf(value) == expected_cls:
                return True
        except Exception:
            pass
    name = _class_name(runtime, value).lower()
    normalized = (
        name.replace("<", " ")
        .replace(">", " ")
        .replace("'", " ")
        .replace("#", " ")
        .replace(".", " ")
    )
    return normalized.strip() == expected.lower() or expected.lower() in normalized.split()


def _read_subobject_level(runtime: Any) -> Optional[int]:
    try:
        raw = getattr(runtime, "subObjectLevel", None)
    except Exception:
        return None
    if raw is None or _is_undefined(raw):
        return None
    if isinstance(raw, bool):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _selected_nodes(runtime: Any) -> List[Any]:
    try:
        selection = runtime.selection
    except Exception:
        return []
    if selection is None or _is_undefined(selection):
        return []
    try:
        return list(selection)
    except TypeError:
        try:
            return [selection]
        except Exception:
            return []
    except Exception:
        return []


def _current_object(runtime: Any) -> Optional[Any]:
    try:
        current = runtime.modPanel.getCurrentObject()
    except Exception:
        return None
    if current is None or _is_undefined(current):
        return None
    return current


def _modifiers(node: Any) -> List[Any]:
    try:
        modifiers = node.modifiers
    except Exception:
        return []
    if modifiers is None or _is_undefined(modifiers):
        return []
    try:
        return list(modifiers)
    except TypeError:
        return []
    except Exception:
        return []


def _stable_handle(runtime: Any, value: Any) -> Optional[str]:
    """Return a stable anim handle; never fall back to a name."""

    for getter in (
        lambda: runtime.getHandleByAnim(value),
        lambda: getattr(value, "handle"),
    ):
        try:
            handle = getter()
        except Exception:
            continue
        if handle is None or _is_undefined(handle):
            continue
        text = str(handle).strip()
        if text and text.lower() != "undefined":
            return "h:%s" % text
    return None


def _base_object(node: Any) -> Optional[Any]:
    try:
        base = node.baseObject
    except Exception:
        return None
    if base is None or _is_undefined(base):
        return None
    return base


def _pymxs_equal(left: Any, right: Any) -> bool:
    try:
        return bool(left == right)
    except Exception:
        return False


def _dependent_nodes(runtime: Any, base: Any) -> Optional[List[Any]]:
    refs = getattr(runtime, "refs", None)
    if refs is None or _is_undefined(refs):
        return None
    function = getattr(refs, "dependentNodes", None)
    if not callable(function):
        return None
    try:
        result = function(base, firstOnly=False, baseObjectOnly=True)
    except Exception:
        return None
    if result is None or _is_undefined(result):
        return None
    try:
        return list(result)
    except TypeError:
        return [result]
    except Exception:
        return None


def _stack_signature(runtime: Any, node: Optional[Any], current: Optional[Any]) -> str:
    if node is None:
        return "none"
    parts: List[str] = []
    for modifier in _modifiers(node):
        entry = [_class_name(runtime, modifier)]
        try:
            handle = modifier.handle
            if not _is_undefined(handle):
                entry.append("h:%s" % handle)
        except Exception:
            pass
        try:
            entry.append("enabled:%s" % bool(modifier.enabled))
        except Exception:
            pass
        parts.append("|".join(entry))
    if current is not None:
        parts.append("current:%s" % _class_name(runtime, current))
    return " ; ".join(parts) if parts else "empty"


def _object_selection(nodes: Sequence[Any], runtime: Any) -> Tuple[int, str]:
    identities = []
    for node in nodes:
        identities.append(_stable_handle(runtime, node) or "unknown")
    return len(nodes), _digest("objects", *sorted(identities))


def _component_bitarray(runtime: Any, level: ContextLevel, node: Any) -> Any:
    polyop = getattr(runtime, "polyop", None)
    if polyop is None or _is_undefined(polyop):
        return None
    try:
        if level == ContextLevel.VERTEX:
            return polyop.getVertSelection(node)
        if level in (ContextLevel.EDGE, ContextLevel.BORDER):
            return polyop.getEdgeSelection(node)
        if level in (ContextLevel.POLYGON, ContextLevel.ELEMENT):
            return polyop.getFaceSelection(node)
    except Exception:
        return None
    return None


def _read_command_panel_task_mode(runtime: Any) -> Tuple[str, bool]:
    """Read ``getCommandPanelTaskMode()`` without switching panels.

    Autodesk documents the return as a name value (``#create``, ``#modify``,
    ``#hierarchy``, ``#motion``, ``#display``, ``#utility``).  An absent,
    throwing, or unreadable result is reported as unknown so the target fails
    closed instead of assuming the Modify panel.
    """

    function = getattr(runtime, "getCommandPanelTaskMode", None)
    if not callable(function):
        return "unknown", False
    try:
        value = function()
    except Exception:
        return "unknown", False
    if _is_unset(value):
        return "unknown", False
    return _normalize_mode(value), True


def _read_command_modes(runtime: Any, ep: Any) -> Tuple[str, bool, str, bool]:
    tool_name = "unknown"
    tool_known = False
    try:
        tool_mode = getattr(runtime, "toolMode", None)
        if tool_mode is not None and not _is_undefined(tool_mode):
            value = getattr(tool_mode, "commandMode", None)
            if not _is_unset(value):
                tool_name = _normalize_mode(value)
                tool_known = True
    except Exception:
        pass

    ep_name = "unknown"
    ep_known = False
    try:
        value = getattr(ep, "getCommandMode")
        if callable(value):
            value = value()
        if not _is_unset(value):
            ep_name = _normalize_mode(value)
            ep_known = True
    except Exception:
        pass
    return tool_name, tool_known, ep_name, ep_known


def _safe_str(value: Any, fallback: str) -> str:
    try:
        return str(value)
    except Exception:
        return fallback


def _current_time(runtime: Any) -> str:
    try:
        return str(runtime.currentTime)
    except Exception:
        return "time:unreadable"


def _editable_poly_interface(node: Any) -> Any:
    try:
        return getattr(node, "EditablePoly", None)
    except Exception:
        return None


@dataclass
class _Inspection:
    target_kind: str = "none"
    node: Any = None
    base: Any = None
    ep: Any = None
    node_token: Optional[str] = None
    base_token: Optional[str] = None
    level: ContextLevel = ContextLevel.OBJECT
    raw_level: Optional[int] = None
    panel_ready: bool = False
    task_mode: str = "unknown"
    task_mode_known: bool = False
    stack_signature: str = "none"
    target_ok: bool = False
    target_reason: str = "Select one supported object"
    indices: Optional[Tuple[int, ...]] = None
    selection_count: int = 0
    selection_token: str = ""
    geometry: Optional[GeometryInfo] = None
    geometry_reason: str = ""
    settings_token: str = ""
    settings_readable: bool = False
    hold_ok: bool = False
    hold_reason: str = ""
    tool_mode: str = "unknown"
    ep_mode: str = "unknown"
    command_state_ok: bool = False
    command_reason: str = ""
    topology_ok: bool = False
    topology_reason: str = ""
    unsupported_subobject: bool = False
    target_token: str = ""
    revision: int = 0

    def target_identity(self) -> str:
        return self.target_token or _digest(
            "target", self.node_token, self.base_token, self.target_kind
        )


class MaxHostAdapter:
    """``HostAdapter`` implementation for the native 3ds Max 2025 bridge."""

    def __init__(self, runtime: Any = None, undo_factory: Any = None) -> None:
        # The runtime is resolved on first host read, never at construction.
        self._runtime = runtime
        self._injected_runtime = runtime is not None
        self._undo_factory = undo_factory
        self._topology_disabled_reason: Optional[str] = None
        self._owned_mode: Optional[Tuple[str, str]] = None
        self._last_snapshot: Optional[ContextSnapshot] = None
        self._last_inspection: Optional[_Inspection] = None
        self._last_snapshot_seconds = 0.0

    # -- runtime resolution and thread safety ---------------------------

    def _assert_main_thread(self) -> None:
        if self._injected_runtime:
            # An injected fake is not a real Max install and may be exercised
            # from any test thread.  Real pymxs access is asserted below.
            return
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError(
                "pymxs.runtime must be used on the 3ds Max main thread; worker "
                "threads are not supported."
            )

    def _resolve_runtime(self) -> Any:
        self._assert_main_thread()
        if self._runtime is not None:
            return self._runtime
        try:
            from pymxs import runtime as max_runtime  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "pymxs.runtime is unavailable. Create MaxHostAdapter with an "
                "injected runtime, or run it inside 3ds Max 2025."
            ) from exc
        self._runtime = max_runtime
        return max_runtime

    # -- public host contract --------------------------------------------

    def detect_context(self) -> ContextLevel:
        """Return the current six-level context without mutating the scene."""

        return self.snapshot().level

    def snapshot(self) -> ContextSnapshot:
        """Read a read-only context snapshot with opaque identity tokens."""

        runtime = self._resolve_runtime()
        self._assert_main_thread()
        started = time.perf_counter()
        inspection = self._inspect(runtime)
        snapshot = self._build_snapshot(runtime, inspection)
        self._last_inspection = inspection
        self._last_snapshot = snapshot
        self._last_snapshot_seconds = time.perf_counter() - started
        return snapshot

    def availability(self, context: ContextSnapshot) -> Dict[str, Availability]:
        """Return enabled/disabled decisions without mutating scene state."""

        if self._runtime is not None:
            self._assert_main_thread()

        inspection = self._matching_inspection(context)
        result: Dict[str, Availability] = {}
        for action_id in CATALOG_ACTION_IDS:
            binding = NATIVE_BINDINGS.get(action_id)
            if binding is None:
                result[action_id] = Availability(
                    False, unverified_reason(action_id, context)
                )
                continue
            if context.level not in binding.allowed_levels:
                result[action_id] = Availability(
                    False,
                    "Available only in the %s context."
                    % ", ".join(
                        sorted(level.value for level in binding.allowed_levels)
                    ),
                )
                continue

            if (
                binding.handler == "level"
                and inspection is not None
                and not inspection.target_ok
            ):
                result[action_id] = Availability(
                    False, inspection.target_reason or "Target is not supported."
                )
                continue

            base = binding.check(context)
            if not base.enabled:
                result[action_id] = base
                continue

            override = self._availability_override(action_id, context, inspection)
            result[action_id] = override or Availability(True, "")
        return result

    def launch(
        self,
        action_id: str,
        expected_context: ContextSnapshot,
        parameters: Optional[dict] = None,
    ) -> LaunchResult:
        """Revalidate the exact live state and dispatch one fixed binding."""

        if not isinstance(action_id, str) or action_id not in CATALOG_ACTION_IDS:
            return LaunchResult(
                status="blocked",
                action_id=str(action_id),
                message="Action is not part of the prototype catalog.",
            )
        if not isinstance(expected_context, ContextSnapshot):
            return LaunchResult(
                status="blocked",
                action_id=action_id,
                message="Expected context is missing or invalid",
            )

        try:
            schema = parameter_schema(action_id, expected_context.level)
        except ParameterError as exc:
            return LaunchResult(status="blocked", action_id=action_id, message=str(exc))
        if schema:
            if parameters is None:
                return LaunchResult(
                    status="blocked",
                    action_id=action_id,
                    message=(
                        "Validated parameters are required; no default amounts "
                        "are applied invisibly."
                    ),
                )
            try:
                validated = validate_parameters(
                    action_id, expected_context.level, parameters
                )
            except ParameterError as exc:
                return LaunchResult(
                    status="blocked",
                    action_id=action_id,
                    message="Invalid parameters: %s" % exc,
                )
        else:
            if parameters not in (None, {}):
                return LaunchResult(
                    status="blocked",
                    action_id=action_id,
                    message="This action takes no parameters.",
                )
            validated = {}

        try:
            runtime = self._resolve_runtime()
            self._assert_main_thread()
            inspection = self._inspect(runtime)
            fresh = self._build_snapshot(runtime, inspection)
            self._last_inspection = inspection
            self._last_snapshot = fresh
        except Exception as exc:
            return LaunchResult(
                status="error",
                action_id=action_id,
                message="Host snapshot failed (%s): %s"
                % (type(exc).__name__, exc),
            )

        mismatch = _context_mismatch(expected_context, fresh)
        if mismatch:
            return LaunchResult(
                status="blocked",
                action_id=action_id,
                message="%s; reopen the context." % mismatch,
            )

        availability = self.availability(fresh).get(action_id)
        if availability is None or not availability.enabled:
            reason = (
                availability.reason
                if availability is not None
                else "Host binding not implemented for this action."
            )
            return LaunchResult(status="blocked", action_id=action_id, message=reason)

        binding = NATIVE_BINDINGS[action_id]
        if binding.handler in ("mode", "exit"):
            return self._launch_mode_action(
                runtime, action_id, fresh, inspection, validated
            )

        if binding.handler == "operation":
            if not inspection.topology_ok:
                return LaunchResult(
                    status="blocked",
                    action_id=action_id,
                    message=inspection.topology_reason or "Target is not mutable.",
                )
            try:
                target = self._make_target(runtime, inspection)
                pre_fingerprint = target_fingerprint(runtime, target)
            except Exception as exc:
                return LaunchResult(
                    status="error",
                    action_id=action_id,
                    message="Could not establish pre-edit state (%s): %s"
                    % (type(exc).__name__, exc),
                )
            result = launch_native(
                runtime,
                action_id,
                fresh,
                validated,
                undo_factory=self._undo_factory,
                target=target,
            )
            if result.status == "error":
                return self._verify_rollback(
                    runtime, action_id, target, pre_fingerprint, result
                )
            if result.status == "applied":
                result = self._refresh_presentation(runtime, result)
            return result

        result = launch_native(
            runtime,
            action_id,
            fresh,
            validated,
            undo_factory=self._undo_factory,
        )
        return result

    # -- inspection -------------------------------------------------------

    def _inspect(self, runtime: Any) -> _Inspection:
        nodes = _selected_nodes(runtime)
        current = _current_object(runtime)
        raw_level = _read_subobject_level(runtime)

        if not nodes:
            return self._finish_inspection(
                _Inspection(
                    target_kind="none",
                    raw_level=raw_level,
                    panel_ready=current is not None,
                    target_reason="Select one supported object",
                    selection_count=0,
                    selection_token=_digest("objects"),
                    stack_signature="none",
                ),
                runtime,
                current,
            )

        if len(nodes) > 1:
            count, token = _object_selection(nodes, runtime)
            return self._finish_inspection(
                _Inspection(
                    target_kind="multiple",
                    raw_level=raw_level,
                    panel_ready=current is not None,
                    target_reason="Select a single Editable Poly object",
                    selection_count=count,
                    selection_token=token,
                    stack_signature="multiple",
                ),
                runtime,
                current,
            )

        node = nodes[0]
        node_token = _stable_handle(runtime, node)
        modifiers = tuple(_modifiers(node))
        base = _base_object(node)
        stack = _stack_signature(runtime, node, current)
        count, token = _object_selection(nodes, runtime)

        if node_token is None:
            return self._finish_inspection(
                _Inspection(
                    target_kind="other",
                    node=node,
                    base=base,
                    raw_level=raw_level,
                    panel_ready=current is not None,
                    target_reason="Target has no stable node handle",
                    selection_count=count,
                    selection_token=token,
                    stack_signature=stack,
                ),
                runtime,
                current,
            )

        if current is not None and _class_is(runtime, current, "Edit_Poly"):
            return self._finish_inspection(
                _Inspection(
                    target_kind="edit_poly",
                    node=node,
                    base=base,
                    node_token=node_token,
                    raw_level=raw_level,
                    panel_ready=True,
                    target_reason=(
                        "Active Edit Poly target not supported in this experiment"
                    ),
                    selection_count=count,
                    selection_token=token,
                    stack_signature=stack,
                ),
                runtime,
                current,
            )

        if modifiers:
            return self._finish_inspection(
                _Inspection(
                    target_kind="other",
                    node=node,
                    base=base,
                    node_token=node_token,
                    raw_level=raw_level,
                    panel_ready=current is not None,
                    target_reason=(
                        "Only an unmodified Editable Poly base object is "
                        "supported (no conversion)"
                    ),
                    selection_count=count,
                    selection_token=token,
                    stack_signature=stack,
                ),
                runtime,
                current,
            )

        if base is None or not _class_is(runtime, base, "Editable_Poly"):
            return self._finish_inspection(
                _Inspection(
                    target_kind="other",
                    node=node,
                    base=base,
                    node_token=node_token,
                    raw_level=raw_level,
                    panel_ready=current is not None,
                    target_reason=(
                        "Only an unmodified Editable Poly base object is "
                        "supported (no conversion)"
                    ),
                    selection_count=count,
                    selection_token=token,
                    stack_signature=stack,
                ),
                runtime,
                current,
            )

        # Supported Editable Poly base object.  Base equality is pymxs equality,
        # not Python identity, and no name fallback is used for mutation.  The
        # Modify panel must be the actual command-panel task mode, not merely
        # ``modPanel.getCurrentObject()`` matching a stale/cached value.
        task_mode, task_mode_known = _read_command_panel_task_mode(runtime)
        modify_task = task_mode_known and task_mode == "modify"
        panel_ready = (
            current is not None and _pymxs_equal(current, base) and modify_task
        )
        level = ContextLevel.OBJECT
        if panel_ready and raw_level in _LEVEL_BY_SUBOBJECT:
            level = _LEVEL_BY_SUBOBJECT[raw_level]

        unsupported_subobject = (
            raw_level is not None and raw_level != 0 and level == ContextLevel.OBJECT
        )
        inspection = _Inspection(
            target_kind="editable_poly",
            node=node,
            base=base,
            ep=_editable_poly_interface(node),
            node_token=node_token,
            base_token=_stable_handle(runtime, base),
            level=level,
            raw_level=raw_level,
            panel_ready=panel_ready,
            task_mode=task_mode,
            task_mode_known=task_mode_known,
            stack_signature=stack,
            unsupported_subobject=unsupported_subobject,
        )

        if not panel_ready:
            if not task_mode_known:
                inspection.target_reason = (
                    "getCommandPanelTaskMode() could not be read; the Modify "
                    "panel cannot be verified"
                )
            elif not modify_task:
                inspection.target_reason = (
                    "Switch the Command Panel to the Modify task for the "
                    "Editable Poly"
                )
            else:
                inspection.target_reason = (
                    "Open the Modify panel on the Editable Poly"
                )
        elif raw_level is None:
            inspection.target_reason = "subObjectLevel could not be read"
        elif raw_level not in _LEVEL_BY_SUBOBJECT:
            inspection.target_reason = (
                "Unsupported sub-object level %r" % (raw_level,)
            )
        elif inspection.base_token is None:
            inspection.target_reason = "Target base object has no stable handle"
        else:
            dependents = _dependent_nodes(runtime, base)
            if dependents is None:
                inspection.target_reason = (
                    "Could not verify shared/instanced geometry "
                    "(refs.dependentNodes unavailable)"
                )
            elif len(dependents) != 1 or not _pymxs_equal(dependents[0], node):
                inspection.target_reason = (
                    "Instanced or shared Editable Poly geometry is not supported"
                )
            else:
                inspection.target_ok = True
                inspection.target_reason = ""

        # Component selection and object selection.
        if level == ContextLevel.OBJECT:
            inspection.selection_count = count
            inspection.selection_token = token
            inspection.indices = ()
        else:
            try:
                indices = read_selection_indices(runtime, level, node)
                inspection.indices = indices
                inspection.selection_count = len(indices or ())
                inspection.selection_token = (
                    _digest("components", level.value, *indices)
                    if indices
                    else _digest("empty", level.value)
                )
            except OperationError as exc:
                inspection.indices = None
                bitarray = _component_bitarray(runtime, level, node)
                fallback = bitarray_count(bitarray) if bitarray is not None else None
                inspection.selection_count = fallback or 0
                inspection.selection_token = _digest(
                    "unreadable-selection", level.value, str(exc)
                )

        # Bounded geometry fingerprint.  It is required for topology and is
        # also included in the revision so equal counts cannot hide an edit.
        if inspection.target_ok:
            try:
                inspection.geometry = read_geometry(runtime, node)
            except GeometryReadError as exc:
                inspection.geometry_reason = exc.reason
            except Exception as exc:
                inspection.geometry_reason = (
                    "Mesh geometry could not be read (%s): %s"
                    % (type(exc).__name__, exc)
                )
        else:
            inspection.geometry_reason = inspection.target_reason

        # Documented soft-selection and constraint state.
        inspection.settings_token, inspection.settings_readable = read_settings(
            inspection.node
        )
        inspection.hold_ok, inspection.hold_reason = hold_state(runtime)

        (
            inspection.tool_mode,
            tool_known,
            inspection.ep_mode,
            ep_known,
        ) = _read_command_modes(runtime, inspection.ep)

        if not tool_known:
            inspection.command_reason = "toolMode.commandMode could not be read"
        elif inspection.tool_mode not in _IDLE_TOOL_MODES:
            inspection.command_reason = (
                "An incompatible command mode (%s) is active"
                % inspection.tool_mode
            )
        elif not ep_known:
            inspection.command_reason = "EditablePoly.getCommandMode could not be read"
        elif inspection.ep_mode not in _IDLE_EP_MODES:
            inspection.command_reason = (
                "An active Editable Poly command mode (%s) owns the viewport"
                % inspection.ep_mode
            )
        else:
            inspection.command_state_ok = True

        # Target eligibility for level/transform entry does not require a full
        # geometry read; topology additionally requires a complete fingerprint.
        topology_reasons = []
        if not inspection.target_ok:
            topology_reasons.append(inspection.target_reason)
        if inspection.geometry is None:
            topology_reasons.append(
                inspection.geometry_reason or "Mesh geometry is unavailable"
            )
        if not inspection.settings_readable:
            topology_reasons.append(
                "Soft-selection/constraint/operation settings could not be read"
            )
        if not inspection.hold_ok:
            topology_reasons.append(
                inspection.hold_reason or "Undo/redo hold state could not be verified"
            )
        if not inspection.command_state_ok:
            topology_reasons.append(
                inspection.command_reason or "Command mode could not be verified"
            )
        if level != ContextLevel.OBJECT and inspection.indices is None:
            topology_reasons.append(
                "Component selection indices could not be read"
            )
        inspection.topology_ok = not topology_reasons
        inspection.topology_reason = (
            "; ".join(dict.fromkeys(topology_reasons)) if topology_reasons else ""
        )

        return self._finish_inspection(inspection, runtime, current)

    def _finish_inspection(
        self, inspection: _Inspection, runtime: Any, current: Optional[Any]
    ) -> _Inspection:
        if not inspection.target_token:
            if inspection.target_kind == "none":
                inspection.target_token = _digest("none", inspection.target_kind)
            else:
                inspection.target_token = inspection.target_identity()
        if inspection.target_kind in ("editable_poly", "edit_poly"):
            inspection.target_token = _digest(
                "target",
                inspection.target_kind,
                inspection.node_token,
                inspection.base_token,
            )
        # An active sub-object level on any target we could not verify must
        # never look like an ordinary object context.  The opaque prefix keeps
        # transforms disabled, matching the frozen contract.
        if (
            inspection.raw_level is not None
            and inspection.raw_level != 0
            and inspection.level == ContextLevel.OBJECT
        ):
            inspection.unsupported_subobject = True
            if not inspection.selection_token.startswith(UNVERIFIED_SUBOBJECT_PREFIX):
                inspection.selection_token = (
                    UNVERIFIED_SUBOBJECT_PREFIX
                    + _digest(
                        "unverified-subobject",
                        inspection.target_kind,
                        inspection.raw_level,
                        inspection.selection_token,
                    )
                )
        return inspection

    def _build_snapshot(
        self, runtime: Any, inspection: _Inspection
    ) -> ContextSnapshot:
        try:
            transform = _safe_str(inspection.node.transform, "transform:unreadable")
        except Exception:
            transform = "transform:unreadable"
        geometry_part = (
            (inspection.geometry.counts, inspection.geometry.digest)
            if inspection.geometry is not None
            else ("geometry-unavailable", inspection.geometry_reason)
        )
        revision = int(
            _digest(
                inspection.target_token,
                inspection.stack_signature,
                inspection.level.value,
                "raw-subobject:%s" % inspection.raw_level,
                str(inspection.panel_ready),
                "task-mode:%s:%s"
                % (inspection.task_mode, inspection.task_mode_known),
                inspection.selection_token,
                inspection.tool_mode,
                inspection.ep_mode,
                str(inspection.command_state_ok),
                str(inspection.hold_ok),
                geometry_part,
                inspection.settings_token,
                _current_time(runtime),
                transform,
            )[:16],
            16,
        )
        inspection.revision = revision
        return ContextSnapshot(
            level=inspection.level,
            target_kind=inspection.target_kind,
            target_token=inspection.target_token,
            selection_count=max(0, inspection.selection_count),
            selection_token=inspection.selection_token,
            panel_ready=inspection.panel_ready,
            conflicting_mode=not inspection.command_state_ok,
            revision=revision,
        )

    # -- availability helpers --------------------------------------------

    def _matching_inspection(self, context: ContextSnapshot) -> Optional[_Inspection]:
        if self._last_snapshot is None or self._last_inspection is None:
            return None
        try:
            if context.identity() == self._last_snapshot.identity():
                return self._last_inspection
        except Exception:
            return None
        return None

    def _availability_override(
        self,
        action_id: str,
        context: ContextSnapshot,
        inspection: Optional[_Inspection],
    ) -> Optional[Availability]:
        binding = NATIVE_BINDINGS[action_id]

        if self._runtime is None:
            return Availability(
                False,
                "Host runtime is unavailable; the native binding cannot be "
                "verified.",
            )

        if binding.handler == "exit":
            return self._exit_tool_availability(context, inspection)

        if self._topology_disabled_reason and binding.is_mutation:
            return Availability(False, self._topology_disabled_reason)

        missing = self._missing_member_reason(action_id, inspection)
        if missing:
            return Availability(False, missing)

        if binding.handler in ("operation", "mode") and inspection is not None:
            if not inspection.topology_ok:
                return Availability(
                    False,
                    inspection.topology_reason or "Target is not mutable.",
                )
            if binding.handler == "mode" and inspection.ep_mode not in _IDLE_EP_MODES:
                return Availability(
                    False,
                    "A native Editable Poly command mode is already active.",
                )
        if binding.handler == "level" and inspection is not None:
            if not inspection.target_ok:
                return Availability(
                    False, inspection.target_reason or "Target is not supported."
                )
        if binding.handler == "command" and inspection is not None:
            if context.level != ContextLevel.OBJECT and not inspection.target_ok:
                return Availability(
                    False, inspection.target_reason or "Target is not supported."
                )
        return None

    def _missing_member_reason(
        self, action_id: str, inspection: Optional[_Inspection]
    ) -> str:
        runtime = self._runtime
        if runtime is None:
            return ""
        binding = NATIVE_BINDINGS[action_id]
        for owner, member in binding.required_members:
            if owner == "runtime":
                target: Any = runtime
            elif owner == "polyop":
                target = getattr(runtime, "polyop", None)
            elif owner == "toolMode":
                target = getattr(runtime, "toolMode", None)
            elif owner == "editable_poly":
                target = inspection.ep if inspection is not None else None
                if target is None:
                    continue
            elif owner == "base_object":
                target = None
                if inspection is not None:
                    target = inspection.base or inspection.node
                    if target is not None:
                        try:
                            target = getattr(target, "baseObject", target)
                        except Exception:
                            target = None
                if target is None:
                    continue
            else:
                continue
            if target is None or _is_undefined(target):
                return "Required native member %s.%s is unavailable." % (
                    owner,
                    member,
                )
            try:
                value = getattr(target, member)
            except Exception:
                value = None
            if value is None or _is_undefined(value):
                return "Required native member %s.%s is unavailable." % (
                    owner,
                    member,
                )
        return ""

    def _exit_tool_availability(
        self, context: ContextSnapshot, inspection: Optional[_Inspection]
    ) -> Availability:
        if inspection is None:
            return Availability(
                False,
                "Exit tool requires a freshly inspected Editable Poly target.",
            )
        token = inspection.target_identity()
        if self._owned_mode is None or self._owned_mode[0] != token:
            return Availability(
                False, "No prototype-owned native tool is active on this target."
            )
        current = inspection.ep_mode
        if current != self._owned_mode[1]:
            return Availability(
                False,
                "The prototype-owned mode is no longer active; another tool will "
                "not be exited.",
            )
        return Availability(True, "")

    # -- mutation helpers -------------------------------------------------

    def _make_target(self, runtime: Any, inspection: _Inspection) -> MutableTarget:
        if inspection.geometry is None:
            raise OperationError(
                inspection.geometry_reason or "Mesh geometry is unavailable."
            )
        if inspection.ep is None or _is_undefined(inspection.ep):
            raise OperationError("The target EditablePoly interface is unavailable.")
        return MutableTarget(
            node=inspection.node,
            ep=inspection.ep,
            level=inspection.level,
            raw_level=inspection.raw_level or 0,
            indices=tuple(inspection.indices or ()),
            geometry=inspection.geometry,
            settings_token=inspection.settings_token,
            target_token=inspection.target_identity(),
            node_token=inspection.node_token or "",
            base_token=inspection.base_token or "",
        )

    def _verify_rollback(
        self,
        runtime: Any,
        action_id: str,
        target: MutableTarget,
        pre_fingerprint: str,
        result: LaunchResult,
    ) -> LaunchResult:
        try:
            post_fingerprint = target_fingerprint(runtime, target)
        except Exception as exc:
            self._topology_disabled_reason = (
                "An edit failed and the pre-edit state could not be re-read "
                "(%s). Native topology is latched disabled until the adapter is "
                "reinitialized; do not run another blind undo." % type(exc).__name__
            )
            return LaunchResult(
                status="error",
                action_id=action_id,
                message=result.message + " " + self._topology_disabled_reason,
            )
        if post_fingerprint == pre_fingerprint:
            return LaunchResult(
                status="error",
                action_id=action_id,
                message=result.message
                + " The undo chunk restored the pre-edit geometry, selection, "
                "and operation settings.",
            )
        self._topology_disabled_reason = (
            "An edit failed and rollback could not be verified: geometry, "
            "selection, or operation settings differ from the pre-edit state. "
            "Native topology is latched disabled until the adapter is "
            "reinitialized; inspect the scene and use Max Undo manually."
        )
        return LaunchResult(
            status="error",
            action_id=action_id,
            message=result.message + " " + self._topology_disabled_reason,
        )

    def _refresh_presentation(
        self, runtime: Any, result: LaunchResult
    ) -> LaunchResult:
        for name in ("redrawViews", "completeRedraw"):
            function = getattr(runtime, name, None)
            if callable(function):
                try:
                    function()
                    return result
                except Exception:
                    break
        return LaunchResult(
            status=result.status,
            action_id=result.action_id,
            message=result.message
            + " The edit committed, but the viewport refresh failed; geometry "
            "was not rolled back.",
        )

    # -- interactive mode ownership --------------------------------------

    def _mode_name(self, action_id: str, level: ContextLevel) -> Optional[str]:
        if action_id == "model.target_weld":
            return "Weld"
        if action_id == "model.cut":
            return {
                ContextLevel.VERTEX: "CutVertex",
                ContextLevel.EDGE: "CutEdge",
                ContextLevel.POLYGON: "CutFace",
            }.get(level)
        return None

    def _launch_mode_action(
        self,
        runtime: Any,
        action_id: str,
        context: ContextSnapshot,
        inspection: _Inspection,
        parameters: dict,
    ) -> LaunchResult:
        if parameters:
            return LaunchResult(
                status="blocked",
                action_id=action_id,
                message="This interactive mode takes no parameters.",
            )
        if not inspection.target_ok:
            return LaunchResult(
                status="blocked",
                action_id=action_id,
                message=inspection.target_reason or "Target is not supported.",
            )
        if inspection.ep is None or _is_undefined(inspection.ep):
            return LaunchResult(
                status="blocked",
                action_id=action_id,
                message="The target EditablePoly interface is unavailable.",
            )

        token = inspection.target_identity()

        if action_id == "model.exit_tool":
            return self._exit_owned_mode(runtime, token, inspection)

        desired = self._mode_name(action_id, context.level)
        if desired is None:
            return LaunchResult(
                status="blocked",
                action_id=action_id,
                message="This mode is not available at the current level.",
            )
        desired_normalized = _normalize_mode(desired)

        current = inspection.ep_mode
        if current not in _IDLE_EP_MODES:
            if self._owned_mode == (token, current) and current == desired_normalized:
                return LaunchResult(
                    status="launched",
                    action_id=action_id,
                    message=(
                        "The prototype-owned native mode is already active; it was "
                        "not toggled off."
                    ),
                )
            return LaunchResult(
                status="blocked",
                action_id=action_id,
                message=(
                    "A different native Editable Poly command mode is active; "
                    "another tool will not be toggled or cancelled."
                ),
            )

        try:
            inspection.ep.toggleCommandMode(runtime.Name(desired))
        except Exception as exc:
            return LaunchResult(
                status="error",
                action_id=action_id,
                message="Native mode entry failed (%s): %s"
                % (type(exc).__name__, exc),
            )
        try:
            value = getattr(inspection.ep, "getCommandMode")
            if callable(value):
                value = value()
            observed = _normalize_mode(value)
        except Exception as exc:
            return LaunchResult(
                status="error",
                action_id=action_id,
                message="Native mode entry could not be read back: %s" % exc,
            )
        if observed != desired_normalized:
            return LaunchResult(
                status="error",
                action_id=action_id,
                message=(
                    "Native mode readback was %r, expected %r; ownership was not "
                    "recorded." % (observed, desired_normalized)
                ),
            )
        self._owned_mode = (token, observed)
        return LaunchResult(
            status="launched",
            action_id=action_id,
            message=(
                "Entered the native %s command mode. Viewport clicks and native "
                "cancellation/undo are Max-owned." % desired
            ),
        )

    def _exit_owned_mode(
        self, runtime: Any, token: str, inspection: _Inspection
    ) -> LaunchResult:
        if self._owned_mode is None or self._owned_mode[0] != token:
            return LaunchResult(
                status="blocked",
                action_id="model.exit_tool",
                message=(
                    "No prototype-owned native tool is active on this exact "
                    "target; another tool will not be exited."
                ),
            )
        owned_mode = self._owned_mode[1]
        if inspection.ep_mode != owned_mode:
            self._owned_mode = None
            return LaunchResult(
                status="blocked",
                action_id="model.exit_tool",
                message=(
                    "The prototype-owned mode is no longer active; no other "
                    "tool was exited."
                ),
            )
        if inspection.ep is None or _is_undefined(inspection.ep):
            return LaunchResult(
                status="blocked",
                action_id="model.exit_tool",
                message="The target EditablePoly interface is unavailable.",
            )
        try:
            inspection.ep.exitCommandModes()
        except Exception as exc:
            return LaunchResult(
                status="error",
                action_id="model.exit_tool",
                message="Exit Tool failed (%s): %s" % (type(exc).__name__, exc),
            )
        try:
            value = getattr(inspection.ep, "getCommandMode")
            if callable(value):
                value = value()
            observed = _normalize_mode(value)
        except Exception:
            observed = "unknown"
        self._owned_mode = None
        if observed not in _IDLE_EP_MODES:
            return LaunchResult(
                status="error",
                action_id="model.exit_tool",
                message=(
                    "Exit Tool was requested but the command mode is still %r; "
                    "no success is claimed." % observed
                ),
            )
        return LaunchResult(
            status="launched",
            action_id="model.exit_tool",
            message="Exited the prototype-owned native command mode.",
        )

    def release_owned_mode(self) -> LaunchResult:
        """Exit only the exact native mode this adapter entered, if still active.

        This is the public uninstall seam.  It never reads private UI state,
        never toggles a foreign tool, and returns ``blocked`` when ownership is
        absent or cannot be proven against the current target.  Callers must
        treat an exception/error as unknown cleanup, not as proof of exit.
        """

        if self._owned_mode is None:
            return LaunchResult(
                status="blocked",
                action_id="model.exit_tool",
                message="No prototype-owned native tool is active.",
            )
        try:
            runtime = self._resolve_runtime()
            inspection = self._inspect(runtime)
        except Exception as exc:
            return LaunchResult(
                status="error",
                action_id="model.exit_tool",
                message="Owned-mode release could not read the host (%s): %s"
                % (type(exc).__name__, exc),
            )
        token = inspection.target_identity()
        return self._exit_owned_mode(runtime, token, inspection)


def _context_mismatch(
    expected_context: ContextSnapshot, fresh: ContextSnapshot
) -> Optional[str]:
    if not isinstance(expected_context, ContextSnapshot):
        return "Expected context is missing or invalid"
    comparisons = (
        ("context level", expected_context.level, fresh.level),
        ("editing target", expected_context.target_kind, fresh.target_kind),
        ("target token", expected_context.target_token, fresh.target_token),
        ("selection", expected_context.selection_token, fresh.selection_token),
        ("selection count", expected_context.selection_count, fresh.selection_count),
        ("Modify panel state", expected_context.panel_ready, fresh.panel_ready),
        ("conflicting mode", expected_context.conflicting_mode, fresh.conflicting_mode),
        ("revision", expected_context.revision, fresh.revision),
    )
    for label, expected, actual in comparisons:
        if expected != actual:
            return "Context changed before launch (%s)" % label
    return None


__all__ = ["MaxHostAdapter"]

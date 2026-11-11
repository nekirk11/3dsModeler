# Native modeling expansion — V2 contracts and historical V1 gate

**Status (2026-11-11):** V2 integration source-complete on `dev`; every native
behavior remains **NOT RUN** because no 3ds Max 2025 runtime was available. The
browser sandbox is **SIMULATED** and is not native evidence. The V1 contract
below is frozen and retained for legacy tests/renderers, but it is no longer the
normal native launch path.

## V2 architecture

```text
core/maneuvers.json + optional checkout/user_aliases.json
    -> core/atlas.py (strict validation, immutable atlas, overlay merge)
    -> core/search.py + core/palette_controller.py
    -> qt_ui/palette.py (render/input/focus only)
                    |
      core/maneuver_contracts.py:ManeuverHost seam
          |                           |
max_host/adapter.py:          core/maneuver_simulated.py:
ManeuverHostAdapter           SimulatedManeuverHost
    -> preparation.py + maneuver_dispatch.py
    -> bindings.py / operations.py / transactions.py

bootstrap.py composes widgets, controller, callbacks, diagnostics, teardown
tools/max_pull_and_load.py/.ms owns persistent macros and local/update loaders
```

### Atlas and overlay

`core/maneuvers.json` is UTF-8 JSON with exactly `schema_version: 2` and a
nonempty ordered `maneuvers` list. Every maneuver has exactly `id`, `label`,
`group`, `aliases`, `contexts`. IDs are lowercase snake_case from the frozen
36-ID set. Declaration order is the final deterministic search tie-breaker.
No descriptor contains Python, MAXScript, paths, or executable argument strings.

Every context has exactly `max2025`, `houdini`, `blender`. A Max route has
`with_selection` and/or `empty_selection`; each route has exactly `kind`,
`target`, `api`, `doc_url`. `kind` is one of `caddy`, `command_mode`,
`button_op`, `polyop`, `max_command`, `composite`, `context_switch`, and
`target` must belong to that kind's fixed set in `TARGETS_BY_KIND`. `api` and
`doc_url` are display/search metadata only; they are never interpreted, split,
looked up, fetched at runtime, or executed. Houdini/Blender records are
non-executable references with official HTTPS documentation links.

Optional checkout-root `user_aliases.json` has `schema_version: 1` and optional
`aliases`, `pinned`, `hidden`. It may extend aliases and ordering hints only; it
cannot change targets, contexts, labels, URLs, or behavior. Invalid UTF-8/JSON,
unknown fields/IDs, duplicates, symlinks, or nonregular files cause the whole
overlay to be ignored with one sanitized warning. `hidden` wins over `pinned`;
hidden applies only to maneuvers, not the three system commands. The file is
git-ignored and excluded from portable archives.

### Host seam and fixed dispatch

`core/maneuver_contracts.py` freezes `ManeuverContext`, `ExecutionDescriptor`,
`DccReference`, `ManeuverSpec`, `ManeuverAtlas`, `UserOverlay`,
`ManeuverSnapshot`, `ManeuverRow`, `PaletteView`, `ManeuverRequest`,
`Availability`/`LaunchResult` reuse, and the four-method `ManeuverHost` protocol:
`snapshot`, `availability`, `execute`, `switch_context`.

`max_host/maneuver_dispatch.py` exposes the host half of the registry. Its key
sets must equal `TARGETS_BY_KIND` exactly (`contract_parity()` returns `()`);
any mismatch is a configuration error that blocks dispatch. Handlers are
reviewed Python functions. `Weld` maps to native `#WeldSelected` and `MSmooth`
to `#MeshSmooth`; the JSON-visible target stays stable while `api` records the
real call. Empty-selection bridge command modes have no verified Editable Poly
2025 launcher, so those routes stay explicitly blocked. SwiftLoop uses the
documented Graphite `PolyToolsPolyDraw.SwiftLoop()` handler and records mode
provenance; it is not claimed as a published EP enum.

### Three preparation tiers

| Tier | Target | Behavior |
| --- | --- | --- |
| 1 `convert` | one non-instanced primitive or Editable Mesh, zero modifiers, single dependent base | Switch Modify, undoably convert to Editable Poly, reacquire base/interface, verify |
| 2 `base_with_modifiers` | Editable Poly base with a stack, **no Edit_Poly anywhere** | Preserve stack byte-for-byte, step to base, set/retain `showEndResult=True`, edit base topology |
| 3 `blocked` | instanced/shared base, unreadable sharing/stack, non-poly + modifiers, any Edit_Poly | Refuse before panel/base/geometry writes; name *Collapse to Poly* or manual Make Unique |

`collapse_to_poly` is the only stack-collapse permission; it requires inline
destructive confirmation and explains modifier loss and instancing impact.
`convert_to_poly` uses Tier-1 restrictions and is not a collapse synonym.

### Count routing, caddies, and undo

After approved preparation and any pivot/context conversion, the host takes a
fresh destination snapshot. Selection count routes the descriptor:
`>0 -> with_selection`, `0 -> empty_selection`. A missing route reports why and
never launches a different tool. Caddy/mode entry is `launched`; an immediate
handler is `applied` only after its postcondition. Native caddies own their
interactive transaction: do not hold `pymxs.undo`/`theHold` across a Qt/native
loop, and never auto-accept Preview/OK. The completed auto-prep conversion is a
separate undo record from the caddy's later edit. `theHold.Holding()` and
`RestoreOrRedoing()` are checked immediately before any mutation; rollback
failure latches native mutation disabled until a fresh Max session.

### Clean Edge Dissolve

`max_host/operations.py:clean_dissolve_edges` records selected edge IDs, endpoint
vertex candidates, context, selection sets, and a slot identity guard before any
removal. It removes the selected edges, then removes only surviving candidate
vertices that are incident to exactly two collinear, opposite live edges.
Corners, kinks, zero-length/ambiguous edges, branches, non-candidates, and
unsupported nonmanifold cases are preserved. If Remove renumbers or identity is
unprovable, the whole chunk rolls back; failed rollback latches. Both passes are
one Max undo chunk.

### Bootstrap and persistent macros

`bootstrap.show_palette(controller=None)` composes the V2 controller/host and
shows/raises/refocuses the one palette. `show_tools`, `show_menu`, and
`install_launcher` forward to it. `switch_context` works without UI for the
macros. `uninstall()` disposes only in-memory UI and owned hooks; it never
deletes `3dsModeler.mcr`, removes hotkeys, clears lockout, or exits native
modes. The launcher writes/verifies the owned MacroScript file and loads it into
the live session via `rt.fileIn` (fallback `rt.macros.load`) so the
`"3dsModeler"` category is available in **Customize > Hotkey Editor** without a
restart. Hotkeys are assigned manually; no W/E/R/Space binding is changed.

---

## Historical V1 frozen contract

This section records the frozen V1 interfaces consumers still exercise. It does
not claim any Editable Poly operation works in Max.

## Frozen core symbols

| Module | Frozen symbol | Purpose |
| --- | --- | --- |
| `core.contracts` | `ContextSnapshot`, `Availability`, `ActionSpec`, `MenuEvent`, `MenuRow`, `MenuView` | Existing immutable wire/types; field names and identity semantics unchanged. |
| `core.contracts` | `LaunchResult.STATUSES` | Added `applied`, `cancelled`, `noop`; retained `launched`, `simulated`, `blocked`, `error`. `applied` means one undoable edit completed with an operation-specific postcondition check, not a Max environment validation. |
| `core.contracts` | `ParameterSpec(name, label, kind, default, minimum=None, maximum=None, choices=(), unit='')` | Frozen parameter declaration. `kind` is `float`, `int`, `bool`, or `enum`. No host wrapper, callable, property name, or script text. |
| `core.contracts` | `HostAdapter.launch(action_id, expected_context, parameters=None)` | Optional third argument. Two-argument calls remain valid for parameter-free actions/adapters. Parameterized actions must not fall back to invisible defaults. |
| `core.contracts` | `ControllerSummary` | Frozen read-only panel snapshot. |
| `core.parameters` | `parameter_schema(action_id, level)`, `validate_parameters(action_id, level, values)`, `ParameterError`, `requires_parameters(...)`, `MAX_FLOAT_MAGNITUDE` | Single allowlist for names, kinds, ranges, enum members, finite checks, and destructive confirmation. |
| `core.catalog` | `ACTIONS`, `ACTIONS_BY_ID` | Expanded native catalog; profile order still decides row order. |
| `core.controller` | `MenuController.set_parameter_prompt(callback)`, `clear_parameter_prompt(callback=None)` | Registers the synchronous renderer parameter collector. |
| `core.controller` | `MenuController.invoke(action_id)` | Panel button entry through the same revalidation/prompt/dispatch path; no profile-visibility filter for explicitly exposed buttons. |
| `core.controller` | `MenuController.summary()` | **Panel symbol name.** Returns `ControllerSummary`; panels must use this instead of private controller/host attributes. |
| `core.controller` | `MenuController.dispose()` | Invalidates pending input and closes the controller view. |
| `core.controller` | `MenuController.release_owned_native_mode()` | Optional public teardown seam; delegates only to a host `release_owned_mode()` hook. Returns `LaunchResult`/`None`, never launches. |
| `core.simulated` | `SimulatedHost.launch(..., parameters=None)` | Remains simulated and has no mesh. Parameterized modeling rows are disabled in the browser with `Native parameter dialog is unavailable in this renderer.` |

## Parameter schemas (frozen)

| Action | Contexts | Fields |
| --- | --- | --- |
| `model.extrude` | polygon | `height` float (default 1.0, system units), `extrusion_type` enum `group`/`local_normal`/`by_polygon` |
| `model.bevel` | polygon | `height` float (1.0), `outline` float (-0.1), same type enum; both zero rejected |
| `model.inset` | polygon | `amount` float (0.1, > 0), `inset_type` enum `group`/`by_polygon` |
| `model.chamfer` | vertex, edge | `amount` float (0.1, > 0), `open` bool (false) |
| `model.connect` | vertex | no fields (parameter-free commit) |
| `model.connect` | edge | `segments` int 1–100, `pinch` int -100–100, `slide` int -100–100 |
| `model.bridge` | edge, border, polygon | `segments` int 1–100, `taper`/`bias` float -100–100, `twist` **int** -100–100 (Autodesk documents `bridgeTwist1`/`bridgeTwist2` as Integer) |
| `model.weld` | vertex | `threshold` float (0.1, > 0) |
| `model.collapse` | vertex, edge, polygon | `confirm` bool (false, must be true) |
| `model.delete` | vertex | `confirm` bool (false, must be true), `cleanup` bool (true) |
| `model.delete` | edge, border, polygon, element | `confirm` bool (false, must be true) |
| `model.remove` | vertex, edge | `confirm` bool (false, must be true) |
| All other catalog IDs | declared contexts | no parameter prompt |

Validation rejects missing/extra keys, booleans as integers, non-finite floats,
values above 1,000,000 system units, out-of-range enums, negative inset/chamfer/
weld values, and unconfirmed destructive edits. `extrude` height `0` is accepted
so the adapter can report `noop`; it is not a topology success.

## Controller lifecycle (frozen ordering)

For a parameterized commit or `invoke`:

1. Resolve the catalog action, level, capability, and snapshot.
2. Close/disarm the menu and set an internal busy token **before** the renderer
   callback or modal event loop.
3. Run the existing synchronous pre-dispatch hide/focus hook.
4. Call the registered prompt with `(ActionSpec, ContextSnapshot,
   tuple[ParameterSpec, ...])`; it returns a plain `dict` or `None` for cancel.
   It may only collect values and must never launch a host action.
5. Validate parameters with `core.parameters.validate_parameters`.
6. Take a **fresh** snapshot and capability map and compare them with the
   pre-prompt identity; any context, selection, target, level, revision,
   panel, mode, or capability change blocks dispatch.
7. Call `host.launch(action_id, fresh_context, validated_parameters)` once.
8. Update result/repeat/log and always clear busy in `finally`.

Cancel, invalid parameters, prompt exceptions, reentrant open/event/profile
replacement, or a changed context never launch and never replace the repeat
record. Parameterized repeat reopens the dialog with schema defaults against the
current target/selection; no amounts are stored in repeat memory. Parameterless
actions keep their previous revalidation/pre-dispatch/two-argument dispatch
behavior. Parameterized rows without a registered prompt are disabled with the
exact reason above, and direct `invoke` is blocked.

## Read-only diagnostics (frozen additions)

```python
probe.build_report(runtime=None, *, summary=None, include_details=False) -> str
probe.report(runtime=None, *, summary=None, include_details=False) -> str
bootstrap.report_diagnostics(*, include_details=False) -> str
```

Existing positional `build_report(runtime)` / `report(runtime)` calls remain
valid. `summary` is an existing immutable `core.contracts.ControllerSummary` or
`None`, never an adapter/live-host wrapper. `bootstrap.report_diagnostics` reads
`_controller.summary()` only when a controller is already present; it never
creates one, refreshes/cancels a menu, or reads private host fields. Cached UI
state is labeled stale-capable and separate from a temporary fresh adapter
observation, which cannot certify the live adapter's historical rollback latch.
The main-thread check runs before host or summary access; a busy summary skips
fresh probing and does not interact with the pending dialog.

Default output omits names, paths, handles/revisions, component data, parameter
values, arbitrary exception text, and native-validation claims. Reasons use
finite bounded categories. `include_details=True` is opt-in, warns **LOCAL
PRIVATE DETAILS — review before sharing**, strips control/escape characters, and
bounds each item and the total section (500 characters / 32 KiB). Reports are
returned/printed locally only; they do not write files, clipboard, network,
telemetry, screenshots, or scene state, and they never turn `runtime_status`
from `not_run` into a host result.

## Catalog ownership handoff

- **W2** completed and released `max_host/adapter.py`, `bindings.py`,
  `operations.py`, `transactions.py`, `probe.py`, and its tests. Coverage is
  derived from `core.catalog`; every catalog ID except `menu.repeat` has a fixed
  binding with required members, allowed levels, and documentation. The frozen
  core parameter function is the second validation pass. All bindings remain
  `runtime_status="not_run"`; catalog/panel presence is not a Max success claim.
- **W3** completed and released its paths. Its public implementation is:
  `bootstrap.show_tools()` is the primary Max-parented `ModelingPanel`;
  `show_menu()` is the optional shared latched popup; `install_launcher()` is a
  **click-to-open alias** for `show_tools` and the retired hold launcher is gone;
  one shared controller/QApplication and one bootstrap-lifetime parameter prompt
  are reused; the panel calls `controller.invoke` and reads
  `controller.summary`; `qt_ui.parameters.collect_parameters` is the modal
  one-shot dialog; **Refresh context** uses public `open` + immediate `cancel`
  because no separate refresh contract exists; `uninstall()` closes pending
  dialogs, clears only its own prompt hook, and disposes only prototype widgets.
- **Owned-mode release seam (connected):** uninstall now reaches the public
  `MenuController.release_owned_native_mode()` → adapter `release_owned_mode()`
  path. The adapter exits only when its exact recorded target token and command
  mode still match; a foreign/stale/unknown mode is blocked and retains no
  private access. Real-host ownership/exit behavior remains **NOT RUN**.
- Neither consumer edited the other's files. W1 owns `core/**`, focused core
  tests, README/docs, and only snapshot/diff tooling under `tools/**`.

## Cycle 1 review repairs (2026-11-11)

These are bounded corrections to the already-implemented native path, not a new
feature wave. All remain fake-runtime evidence; Max/PySide execution is still
**NOT RUN**.

- **Settings fingerprint owner:** `max_host/operations.py:target_fingerprint`
  now reads documented operation/soft-selection properties from
  `target.node` → `baseObject` instead of the `EditablePoly` method interface.
  Transform, handles, settings, selection, and geometry are required to be
  readable; an unreadable component raises instead of adding an
  `unreadable:*` token that could match pre/post and falsely verify rollback.
  Node and base-object identity use the adapter's documented
  `runtime.getHandleByAnim()` query; fingerprinting never relies on a
  base-object `.handle` property or falls back to it. `_verify_rollback` latches
  native topology when the post-failure fingerprint cannot be re-read.
- **Current-time readability correction:** the earlier blanket claim that every
  unreadable component, including time, was rejected was too broad. The prior
  code stringified raw `currentTime` before checking, so MAXScript
  undefined/`None`, the text `none`, booleans, and blank/whitespace could become
  matching text and falsely support a verified-rollback claim. The guard now
  reads raw `currentTime`, rejects `None`/undefined (`_is_undefined`), booleans,
  failed reads or stringification, empty/whitespace text, and the legacy
  `time:unreadable` sentinel case-insensitively, while preserving valid zero and
  readable native-like time text. This is a bounded missing-value guard, not a
  claim to validate every host `TimeValue` representation. An unreadable
  pre-edit time fails before an undo chunk or native edit; an unreadable
  post-failure time latches topology. The adapter latch message names the
  `OperationError`/re-read failure, not specifically current time. Real
  `TimeValue` behavior remains **NOT RUN**.
- **Undo hold guard:** `max_host/transactions.py:hold_state` uses the documented
  `theHold.Holding()` and `theHold.RestoreOrRedoing()` queries. Only MAXScript
  Boolean / C++ nonzero-integer results are accepted; absent, throwing, or
  unreadable returns fail closed, as do active hold/restore/redo states. The
  guard is evaluated before the undo factory, so an active hold cannot receive
  a new chunk.
- **Idle tool modes:** `max_host/adapter.py` accepts the documented scale tool
  values `#uscale`, `#nuscale`, and `#squash` in addition to `#scale`; they are
  idle transform modes, not foreign command modes.
- **Modify task mode:** supported native targets require
  `getCommandPanelTaskMode() == #modify` in addition to exact
  `modPanel.getCurrentObject() == node.baseObject`. The task mode is part of
  the snapshot revision, so changing panels while a dialog/menu is pending
  blocks dispatch. Object-level transforms stay available at raw level 0 when
  no unverifiable sub-object state needs protection.
- **Integer bridge twist:** `twist` is an `int` schema field, the native write
  converts to `int`, and the Qt model consumes it through integer spin-box
  bounds. Fractional values and booleans are rejected by the frozen validator.
- **Test fixture:** the native-array fake now supports indexing (used by
  `capHolesByEdge`); production native-array conversion was not weakened.

## Manual acceptance cases (all NOT RUN)

These cases are for a real Max 2025/2025.1 operator after W2 reports native
completion and the W2/W3 composition is integrated. A fake runtime, AST pass, or
browser simulation cannot satisfy any row. Record the exact Max build, Python/Qt versions, operator, and disposable
fixture for every run. Use only user-created Editable Poly boxes/planes; never
convert, repair, or reuse an existing production object.

**Global preconditions:** exactly one valid, non-instanced Editable Poly base
object; zero modifiers; actual Modify panel task mode with
`modPanel.getCurrentObject() == node.baseObject`; no unsupported/unknown command
mode; component selection is nonempty only where the case requires it. Level
buttons may be used with zero components and must not change geometry.

### P0 acceptance (must pass before any P1 claim)

| ID | Case | Pass condition |
| --- | --- | --- |
| P0-LEVEL | Object → vertex → edge → border → polygon → element buttons | `subObjectLevel` changes to 0–5; no selection/geometry conversion; border and element stay distinct; opening/refreshing the panel changes nothing. |
| P0-LAUNCH | `show_tools()` primary, `install_launcher()` click-to-open alias, optional `show_menu()`, `uninstall()` | Repeated `show_tools` raises one shared panel/controller; `install_launcher` returns that same panel with no hold widget; one bootstrap-lifetime parameter prompt; uninstall cancels pending dialog/input, disposes only prototype widgets, does not exit a foreign tool or undo committed geometry. |
| P0-REFRESH | Panel **Refresh context** | Read-only public `open` + immediate `cancel`; skipped while a menu/parameter request is active; never edits geometry, changes levels, or reaches private adapter state. |
| P0-EXTRUDE | Polygon extrude, positive and negative height; group/local/by-polygon type | One `applied` result, documented `polyop.extrudeFaces` call, expected face/position change, one labeled undo restores geometry/selection/settings; Cancel leaves no undo entry. |
| P0-BEVEL | Polygon bevel height/outline; both-zero rejection | One undoable edit with restored `extrusionType`; both-zero produces no topology success; Cancel/zero-only is `noop` or blocked, never `applied`. |
| P0-INSET | Polygon inset amount > 0; group/by polygon | `EP.buttonOp(#Inset)` is actually invoked; one undo unit; Cancel/zero/negative changes nothing and claims no success. |
| P0-CHAMFER | Vertex and edge chamfer amount > 0; open false/true | Correct `chamferVertices`/`chamferEdges` path, one undo unit, expected topology; unsupported segmented controls stay hidden, not guessed. |
| P0-SELECT | Grow/shrink on nonempty sub-object selection; loop/ring on edges | Selection changes only (no geometry), result compares resulting selection, loop/ring refuse arbitrary borders. |
| P0-DIALOG | Parameter dialog for a parameterized action | Shows native target/context/component count, units, semantics, and no-preview notice; Escape/close cancels; `Apply once` runs once; double-click/Enter auto-repeat does not apply twice. |
| P0-STALE | Change selection (equal count), level, target, stack, panel, time, transform, mode, or soft-selection during the dialog | Apply is blocked after a fresh snapshot; no geometry, no undo entry, no repeat replacement. |
| P0-REPEAT | Parameterized repeat `/ Repeat: relaunch ...` | Reopens the dialog with safe defaults against the current target/selection; no stored amounts, no native RepeatLastOperation; destructive actions never repeat. |
| P0-UNDO | Every applied P0 edit with existing scene history | Exactly one labeled Undo restores full geometry/selection/settings; Redo restores the result; Cancel adds no undo entry; no blind rollback. |

### P1 and P2 acceptance (implemented in source; runtime pending)

W2 now has fixed bindings for cap, connect, bridge, threshold weld, collapse,
delete, remove, split/break, planar X/Y/Z, flip normals, and the P2 cut/target
weld/exit-tool modes. Their fake-runtime and undo tests pass, but **no Max
runtime validation is claimed**. Accept them only after the real-host checklist
records the documented call, prerequisites, no-op/failure behavior, property
save/restore, postcondition, and single undo unit. P2 additionally requires exact
owned-mode ownership, native cancel/undo behavior, and teardown proof; a missing
or ambiguous behavior is reported as blocked/deferred, not fake success.

For every case record before/after component counts, a geometry signature,
settings before/after, and the undo/redo result. Blocked and `noop` paths must be
tested with history already present. A P1/P2 absence is reported as deferred or
blocked with a reason, never as success.

## Task-start evidence

The full task-start snapshot (prototype manifest including caches, byte copies
of every pre-existing non-cache file, root/reference manifest, plan copy, test
and syntax output, and aggregate hash comparison) is retained privately outside
the prototype and is not published.

Key files there: `prototype-full-manifest.json`, `prototype-copies/`,
`root-reference-manifest.json`, `prototype-reference-scan.json`,
`plan-hash-comparison.json`, `baseline-tests.json`, `syntax-check.json`, and
`pre-existing-gaps.md`. The task-start snapshot is not edited by the delta tool.

A reproducible full-tree delta report is retained privately outside both trees.
It contains `delta_report.py`, `delta-all.json`, `delta-all.md`,
`delta-all.patch`, and `run-all.json`, plus the earlier W1-scoped files. The
full-tree report records **15 added, 19 modified, 0 deleted, 13 unchanged**;
all 32 task-start copies verify and the patch has no skips. `__pycache__` is
excluded from the patch and cache-only changes are recorded separately.

## Test evidence

- `tests/test_parameters.py` — schema/range/type/confirmation tests.
- `tests/test_native_integration.py` — prompt lifecycle, cancel, post-prompt
  revalidation, reentrancy, repeat, invoke, summary, launch-signature, and
  public owned-mode release tests.
- `tests/test_core.py` — existing gesture/revalidation/repeat/profile tests.
- W2/W3 tests — adapters, operations, transactions, mode ownership, bootstrap,
  panel/dialog composition, and the optional Qt skips.

After the cycle-1 review repairs: **257 tests OK, 12 skipped** (11 Qt-gated
`test_native_ui` skips + 1 optional `qt_ui` module skip); 37-file 3.11
AST/compile, host-free imports, Node syntax, `git diff --check`, and the
293-entry protected baseline all pass. See [`VERIFICATION.md`](VERIFICATION.md)
for exact commands and results. Native runtime status for every operation
remains **NOT RUN**.

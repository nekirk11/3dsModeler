# Max 2025 native validation ledger

**Status: NOT RUN — no 3ds Max 2025 installation was available for this
prototype.**

Everything below is documentation evidence plus fake-runtime contract tests.
No native action, viewport gesture, DPI case, focus case, undo case, or host
state was exercised in 3ds Max. The sandbox is simulated and is not evidence
either. Do not promote this adapter based on this document.

## V2 maneuver/route ledger

`core/maneuvers.json` is the authoritative machine-readable atlas for all 36
IDs, every legal context, both selection routes, the actual `api` call text, and
official `doc_url` links. `max_host/maneuver_dispatch.py` is the authoritative
fixed handler registry. This table summarizes runtime status; **every row is
`NOT RUN`** in Max 2025.

| ID | V2 contexts | `with_selection` route | `empty_selection` route | Blocker / live requirement |
| --- | --- | --- | --- | --- |
| `bevel` | vertex, edge, face | caddy `Chamfer` (V/E), `Bevel` (F) | mode `ChamferVertex`/`ChamferEdge`/`Bevel` | none known |
| `extrude` | vertex, edge, face | caddy `Extrude` | mode `ExtrudeVertex`/`ExtrudeEdge`/`ExtrudeFace` | none known |
| `inset` | face | caddy `Inset` | mode `InsetFace` | none known |
| `dissolve` | vertex, edge | button `Remove` (V); composite `clean_dissolve_edges` (E) | — | vertex-slot identity under `Remove` needs live proof |
| `delete` | vertex, edge, border, face, element | button `Delete` | — | component-only semantics need live proof |
| `loop_cut` | edge, face | caddy `ConnectEdges` (E); Graphite SwiftLoop (F) | mode `SwiftLoop` (E/F) | SwiftLoop provenance/idle-exit needs live proof |
| `cut` | vertex, edge, face | mode `CutVertex`/`CutEdge`/`CutFace` | same mode | none known |
| `quick_slice` | vertex, edge, face | mode `QuickSlice` | same mode | none known |
| `bridge` | edge, border, face | caddy `BridgeEdge`/`BridgeBorder`/`BridgePolygon` | mode bridge targets | **BLOCKED** empty routes: no verified EP 2025 launcher |
| `fill` | border | button `Cap` | — | open-edge pivot needs live proof |
| `weld` | vertex, edge | caddy `Weld` → `#WeldSelected` | mode `Weld` | enum mapping needs live proof |
| `target_weld` | vertex, edge | mode `Weld` | same mode | distinct from threshold weld; live proof |
| `collapse` | vertex, edge, face | button `Collapse` | — | none known |
| `split_edges` | edge | button `SplitEdges` | — | none known |
| `break_vertices` | vertex | button `BreakVertex` | — | none known |
| `select_loop` | edge | button `SelectEdgeLoop` | — | none known |
| `select_ring` | edge | button `SelectEdgeRing` | — | none known |
| `grow_selection` | vertex, edge, border, face, element | button `GrowSelection` | — | none known |
| `shrink_selection` | vertex, edge, border, face, element | button `ShrinkSelection` | — | none known |
| `clear_selection` | vertex, edge, border, face, element | polyop `clear_selection` | same noop | none known |
| `convert_to_verts` | all six | context switch `vertex` | same | none known |
| `convert_to_edges` | all six | context switch `edge` | same | none known |
| `convert_to_borders` | all six | context switch `border` | same | closed-edge-only must report no open border |
| `convert_to_faces` | all six | context switch `face` | same | none known |
| `subdivide` | vertex, edge, face | caddy `Tessellate` | — | tessellation ≠ smooth subdivision; live proof |
| `smooth` | vertex, edge, face | caddy `MSmooth` → `#MeshSmooth` | — | enum mapping needs live proof |
| `flip_normals` | face, element | button `FlipNormals` | — | none known |
| `planar_x` | vertex, edge, border, face, element | button `MakePlanarInX` | — | none known |
| `planar_y` | vertex, edge, border, face, element | button `MakePlanarInY` | — | none known |
| `planar_z` | vertex, edge, border, face, element | button `MakePlanarInZ` | — | none known |
| `move` | all six | fixed `max move` | blocked without selection | entering a tool is `launched`, not applied |
| `rotate` | all six | fixed `max rotate` | blocked without selection | same |
| `scale` | all six | fixed `max scale` | blocked without selection | same |
| `frame_selection` | all six | fixed `max zoomext sel` | blocked without selection | same |
| `convert_to_poly` | object | composite `convert_to_poly` | blocked without node | Tier-1 restrictions; live proof |
| `collapse_to_poly` | object | composite `collapse_to_poly` | blocked without node | destructive confirmation; instancing live proof |

`with_selection` requires a fresh component count `> 0` after preparation and
pivot conversion; `empty_selection` requires exactly `0`. A missing route is a
finite blocked reason, never an inferred alternative operation. All descriptors
are validated against `TARGETS_BY_KIND`; malicious kinds/targets are rejected
before dispatch. `api`/`doc_url` are never inputs to dispatch.

### Outstanding live gates

- **Bridge empty selection.** Published Editable Poly 2025 material does not
  list the requested bridge command-mode launchers. The atlas keeps the
  semantic targets, but the host registry returns an explicit blocked reason.
  Do not add an Edit_Poly modifier or dispatch a default edit as a substitute.
- **SwiftLoop.** It is a Graphite `PolyToolsPolyDraw` handler, not a proven
  EditablePoly enum. Mode ownership and safe idle-exit need live validation
  before chained launches are claimed.
- **Weld/MSmooth.** Stable semantic names map to `#WeldSelected`/`#MeshSmooth`;
  verify the popup enums in 2025.
- **Clean dissolve slot identity.** Verify that `Remove` preserves recorded
  vertex slot/flag identity; otherwise the operation must roll back rather than
  use nearest-position matching.
- **Safe base editing under modifiers.** Validate Symmetry/TurboSmooth with
  `showEndResult=True`, and confirm any Edit Poly modifier blocks.

All live checks in [`TOMORROW-MAX2025.md`](TOMORROW-MAX2025.md) remain **NOT RUN**.

---

## Historical V1 adapter ledger

The remainder documents the conservative V1 target model and P0/P1/P2 catalog
mappings. It is retained for legacy tests; V2 preparation intentionally lifts
the V1 "no modifiers" restriction only under the documented three tiers.

## Environment and documentation evidence

| Topic | Evidence used | What it establishes |
| --- | --- | --- |
| Python/Qt in Max | [What's New in 3ds Max Python](https://help.autodesk.com/cloudhelp/2025/ENU/MAXDEV-Python/files/MAXDEV_Python_what_s_new_in_3ds_max_python_api_html.html): 2025 = Python 3.11.4 / PySide 6.5.3; 2025.1 = Python 3.11.8 | Target Python 3.11 syntax and Qt 6.5 APIs; use Max's own PySide6 |
| pymxs | [Using pymxs](https://help.autodesk.com/cloudhelp/2025/ENU/MAXDEV-Python/files/MAXDEV_Python_using_pymxs_html.html): wraps the MAXScript engine and exposes its interfaces, globals, and classes; members are case-insensitive; samples use `from pymxs import runtime as rt` and `rt.execute(...)` | Lazy `pymxs.runtime` access; opaque values stay local |
| Parent widget | [`qtmax` module](https://help.autodesk.com/cloudhelp/2025/ENU/MAXDEV-Python/files/MAXDEV_Python_qtmax_module_html.html): `GetQMaxMainWindow()` returns the Max main `QMainWindow` for parenting | Bootstrap parents prototype widgets to Max's main window when available |
| Transform modes | [3ds MAX Commands (archived MAXScript Help)](https://help.autodesk.com/cloudhelp/2017/ENU/MAXScript-Help/files/GUID-A96857E7-73FE-4F42-BE71-E8185356F4C9.htm): `max move` “Activates Select and Move mode”; `max rotate`; `max scale`; `max zoomext sel` “Activates Zoom Extents Selected” | Only fixed, documented command literals are dispatched |
| Modify panel/sub-object state | [Modify Panel (archived MAXScript Help)](https://help.autodesk.com/cloudhelp/2016/ENU/MAXScript-Help/files/GUID-BAC3CC23-9D1D-44CE-B4AF-C0CEBDD7099E.htm): `subObjectLevel` system global is 0 when off and `undefined` when the Modify panel is not open; `modPanel.getCurrentObject()` returns `undefined` outside the Modify panel | Level/target detection is read-only and conservative |
| Command panel task mode | [Command Panels (archived MAXScript Help)](https://help.autodesk.com/cloudhelp/2017/ENU/MAXScript-Help/files/GUID-D2A6D67D-60D6-4068-80E2-7C16496213CF.htm): `getCommandPanelTaskMode()` returns the name value `#create`/`#modify`/`#hierarchy`/`#motion`/`#display`/`#utility` | Native targets require `#modify`; the mode is read only, never changed, and is part of the stale-context revision |
| Undo hold state | [theHold (archived MAXScript Help)](https://help.autodesk.com/cloudhelp/2017/ENU/MAXScript-Help/files/GUID-17BFEAAF-3395-4FFB-9F98-54767481AB08.htm): `Holding()` returns a Boolean, `RestoreOrRedoing()` returns a Boolean indicating restoring or redoing | The wrapper refuses a new chunk while holding/restoring/redoing; only documented Boolean/C++ nonzero-integer results are trusted |
| Transform tool modes | [Main Toolbar (archived MAXScript Help)](https://help.autodesk.com/cloudhelp/2016/ENU/MAXScript-Help/files/GUID-408F43DA-6C99-4CF5-96D5-78836A5DCB02.htm): `toolmode.commandmode` names include `#select`, `#move`, `#rotate`, `#uscale`, `#nuscale`, `#squash` | All documented idle transform modes are allowlisted; unknown/active modeling modes stay fail-closed |
| Editable Poly queries | [Editable_Poly Methods (archived MAXScript Help)](https://help.autodesk.com/cloudhelp/2017/ENU/MAXScript-Help/files/GUID-B40F84EA-5F19-45B6-AA7C-0ADC77708BBE.htm): `polyop.getVertSelection`, `getEdgeSelection`, `getFaceSelection` return selection bitarrays; `polyop.capHolesByEdge` is an immediate call; `polyop.extrudeFaces`/`bevelFaces`/`chamferEdges` take immediate amount arguments | Selection counts/tokens are readable; immediate default-amount edits are deliberately not used |

The 2025 MAXScript Help pages are served through a JavaScript help viewer and
were not machine-retrievable in this environment; the archived MAXScript pages
above are cited as API evidence, not as 2025 runtime proof. The 2025 Python/Qt
pages were retrieved directly.

## Conservative target model

The adapter reads the current selection, Modify panel target, modifier stack,
and sub-object level. It never writes them.

- Supported sub-object target: **one active, unmodified Editable Poly base
  object** with `getCommandPanelTaskMode() == #modify`, exact
  `modPanel.getCurrentObject() == node.baseObject`, matching level, and a
  nonempty component selection. A readable non-Modify task mode is reported as
  unsupported; an unreadable task-mode query disables topology.
- The command-panel task mode is included in snapshot `revision`; a task-mode
  change while a menu/dialog is pending fails revalidation before dispatch.
  Object-level transforms remain eligible at raw level 0 when no sub-object
  state is unverifiable.
- Visible but disabled: primitives, meshes, unknown providers, multiple-object
  sub-object editing, nonempty modifier stacks, and **active Edit Poly
  modifiers**.
- Sub-object levels map 0→object, 1→vertex, 2→edge, 3→border, 4→polygon,
  5→element **only for a supported active target**. Unsupported targets report
  the safe `object` context, and the target kind/reason carries the limitation.
  Border and element are never merged.
- No conversion (`convertToPoly`/`convertToMesh`), no modifier creation, no
  collapse, no stack-current-object write, no scene reset, and no implicit
  selection repair.
- An active sub-object level on a target that cannot be verified (for example
  an active Edit Poly modifier or a nonempty stack) keeps object transforms
  disabled; the raw sub-object level is part of the snapshot revision.
- Opening, hovering, snapshotting, and availability checks do not create undo
  chunks or modify geometry.

## Native binding evidence table

**Integrated W2 state (2026-11-11):** every catalog ID except core-owned
`menu.repeat` has a real fixed binding in
`max_host/bindings.py`, with `runtime_status="not_run"`. The panel/dialog are
renderers; they do not make a runtime claim. Citation keys map to the URLs below.

| Action ID | Handler | Contexts | Required native members | Citations |
| --- | --- | --- | --- | --- |
| `transform.move` | command | all six | `runtime.execute` (`max move`) | C |
| `transform.rotate` | command | all six | `runtime.execute` (`max rotate`) | C |
| `transform.scale` | command | all six | `runtime.execute` (`max scale`) | C |
| `view.frame_selection` | command/view | all six | `runtime.execute` (`max zoomext sel`) | C |
| `context.object` | level | all six | `runtime.subObjectLevel` | EP, MP |
| `context.vertex` | level | all six | `runtime.subObjectLevel` | EP, MP |
| `context.edge` | level | all six | `runtime.subObjectLevel` | EP, MP |
| `context.border` | level | all six | `runtime.subObjectLevel` | EP, MP |
| `context.polygon` | level | all six | `runtime.subObjectLevel` | EP, MP |
| `context.element` | level | all six | `runtime.subObjectLevel` | EP, MP |
| `selection.grow` | operation | vertex…element | `editable_poly.buttonOp` | EP |
| `selection.shrink` | operation | vertex…element | `editable_poly.buttonOp` | EP |
| `selection.loop` | operation | edge | `editable_poly.SelectEdgeLoop` | EP |
| `selection.ring` | operation | edge | `editable_poly.SelectEdgeRing` | EP |
| `selection.clear` | operation | vertex…element | `polyop.setVertSelection/setEdgeSelection/setFaceSelection` | PS |
| `selection.to_border` | operation | edge | `editable_poly.SelectBorder` | EP |
| `selection.to_element` | operation | polygon | `editable_poly.SelectElement` | EP |
| `model.extrude` | operation | polygon | `polyop.extrudeFaces`, `editable_poly.extrusionType` | PX, EPP |
| `model.bevel` | operation | polygon | `polyop.bevelFaces`, `editable_poly.extrusionType` | PX, EPP |
| `model.inset` | operation | polygon | `editable_poly.buttonOp(#Inset)`, `insetAmount`, `insetType` | EP, EPP |
| `model.chamfer` | operation | vertex, edge | `editable_poly.chamferVertices`, `chamferEdges` | EP, EPP |
| `model.cap` | operation | border | `polyop.capHolesByEdge`, `polyop.getOpenEdges` | PO, EP |
| `model.connect` | operation | vertex, edge | `ConnectVertices`, `ConnectEdges`, `connectEdgeSegments/Pinch/Slide` | EP, EPP |
| `model.bridge` | operation | edge, border, polygon | `ReadyToBridgeFlagged`, `buttonOp`, `bridgeSelected/Segments/Taper/Bias/Twist1/Twist2` | EP, EPP, PO |
| `model.weld` | operation | vertex | `polyop.weldVertsByThreshold`, `getOpenEdges`, `getVertsUsingEdge`, `weldThreshold` | PW, PO, EPP |
| `model.collapse` | operation | vertex, edge, polygon | `polyop.collapseVerts/collapseEdges/collapseFaces` | PC |
| `model.delete` | operation | vertex…element | `polyop.deleteVerts/deleteEdges/deleteFaces/deleteIsoVerts` | PD |
| `model.remove` | operation | vertex, edge | `editable_poly.Remove` | EP, PD |
| `model.split_edges` | operation | edge | `editable_poly.splitEdges` | EP |
| `model.break_vertices` | operation | vertex | `editable_poly.breakVerts` | EP |
| `model.planar_x` | operation | vertex…element | `editable_poly.MakePlanarIn(#X)` | EP |
| `model.planar_y` | operation | vertex…element | `editable_poly.MakePlanarIn(#Y)` | EP |
| `model.planar_z` | operation | vertex…element | `editable_poly.MakePlanarIn(#Z)` | EP |
| `model.flip_normals` | operation | polygon, element | `editable_poly.flipNormals` | EP |
| `model.cut` | mode | vertex, edge, polygon | `toggleCommandMode`, `getCommandMode`, `exitCommandModes` | EP, P |
| `model.target_weld` | mode | vertex | `toggleCommandMode`, `getCommandMode` | EP, P |
| `model.exit_tool` | exit | vertex…element | `exitCommandModes` | EP |
| `menu.repeat` | core | all six | controller repeat record; no native binding | — |

Citation keys:

- **C** — 3ds Max MAXScript Help, “3ds MAX Commands” (archived ENU): <https://help.autodesk.com/cloudhelp/2017/ENU/MAXScript-Help/files/GUID-A96857E7-73FE-4F42-BE71-E8185356F4C9.htm>
- **EP** — 3ds Max 2025 MAXScript Help, Interface: EditablePoly: <https://help.autodesk.com/cloudhelp/2025/ENU/MAXScript-Help/files/3ds-Max-Objects-and-Interfaces/Editable-Meshes-Splines-Patches/Editable_Poly/GUID-90843EC5-AE3A-43EB-9406-A3631DAEADEF.html>
- **EPP** — 3ds Max 2025 MAXScript Help, Editable_Poly Properties: <https://help.autodesk.com/cloudhelp/2025/ENU/MAXScript-Help/files/3ds-Max-Objects-and-Interfaces/Editable-Meshes-Splines-Patches/Editable_Poly/GUID-0196A023-116F-47F8-99E9-AF3CB52F302C.html>
- **PX** — 3ds Max 2025 MAXScript Help, Extrude/Bevel/Chamfer Methods: <https://help.autodesk.com/cloudhelp/2025/ENU/MAXScript-Help/files/3ds-Max-Objects-and-Interfaces/Editable-Meshes-Splines-Patches/Editable_Poly/Editable_Poly-Methods/GUID-942EDBDB-D13B-4D37-8098-868EFA68BA6E.html>
- **PS** — 3ds Max 2025 MAXScript Help, Get/Set Selection Methods: <https://help.autodesk.com/cloudhelp/2025/ENU/MAXScript-Help/files/3ds-Max-Objects-and-Interfaces/Editable-Meshes-Splines-Patches/Editable_Poly/Editable_Poly-Methods/GUID-37771642-5CC2-49F3-B77F-4F390E7ABD9E.html>
- **PO** — 3ds Max 2025 MAXScript Help, Open Edges Methods: <https://help.autodesk.com/cloudhelp/2025/ENU/MAXScript-Help/files/3ds-Max-Objects-and-Interfaces/Editable-Meshes-Splines-Patches/Editable_Poly/Editable_Poly-Methods/GUID-77A69BBB-6E1C-488B-8973-CDBF54FF2486.html>
- **PC** — 3ds Max 2025 MAXScript Help, Collapse Methods: <https://help.autodesk.com/cloudhelp/2025/ENU/MAXScript-Help/files/3ds-Max-Objects-and-Interfaces/Editable-Meshes-Splines-Patches/Editable_Poly/Editable_Poly-Methods/GUID-DCD5813F-1956-4E9D-BC57-DD2ED6C02293.html>
- **PW** — 3ds Max 2025 MAXScript Help, Weld Methods: <https://help.autodesk.com/cloudhelp/2025/ENU/MAXScript-Help/files/3ds-Max-Objects-and-Interfaces/Editable-Meshes-Splines-Patches/Editable_Poly/Editable_Poly-Methods/GUID-B801F6E2-FCA6-43CF-A736-B7C157135A65.html>
- **PD** — 3ds Max 2025 MAXScript Help, Delete Methods: <https://help.autodesk.com/cloudhelp/2025/ENU/MAXScript-Help/files/3ds-Max-Objects-and-Interfaces/Editable-Meshes-Splines-Patches/Editable_Poly/Editable_Poly-Methods/GUID-12ABA4CC-0600-49E7-A543-069BA1952A98.html>
- **P** — 3ds Max 2025 Python Developer Help, Using pymxs (enum names, 1-based indices, equality not identity): <https://help.autodesk.com/cloudhelp/2025/ENU/MAXDEV-Python/files/MAXDEV_Python_using_pymxs_html.html>
- **MP** — 3ds Max 2025 MAXScript Help, Modify panel: <https://help.autodesk.com/cloudhelp/2025/ENU/MAXScript-Help/files/MAXScript-Tools-and-Interaction/Interacting-with-the-3ds-Max/Command-Panels/GUID-BAC3CC23-9D1D-44CE-B4AF-C0CEBDD7099E.html>

Additional implementation references used by the adapter/probe and tests:
BitArray `numberSet`/coercion (BA), pymxs undo rollback/suppression (PU),
`refs.dependentNodes` (DN), and Python threading (TH) are listed in the existing
environment table and `max_host/bindings.py` constants. None is runtime proof.

Only these fixed bindings are dispatched. `launched` means a mode was entered,
not that geometry changed; `applied` means a postcondition passed after one
undoable edit. Every row remains **NOT RUN** in Max.

## Supported/disabled matrix by context

Legend: **B\*** = binding exists, availability-gated by target/level/selection;
**B0** = level binding usable with zero components; **C** = core-owned. This
matrix is source coverage, not a runtime-enabled claim.

| Action | object | vertex | edge | border | polygon | element |
| --- | --- | --- | --- | --- | --- | --- |
| `transform.move/rotate/scale` | B* | B* | B* | B* | B* | B* |
| `view.frame_selection` | B* | B* | B* | B* | B* | B* |
| `context.object…element` | B0 | B0 | B0 | B0 | B0 | B0 |
| `selection.grow/shrink` | — | B* | B* | B* | B* | B* |
| `selection.loop/ring` | — | — | B* | — | — | — |
| `selection.clear` | — | B* | B* | B* | B* | B* |
| `selection.to_border` | — | — | B* | — | — | — |
| `selection.to_element` | — | — | — | — | B* | — |
| `model.extrude/bevel/inset` | — | — | — | — | B* | — |
| `model.chamfer` | — | B* | B* | — | — | — |
| `model.cap` | — | — | — | B* | — | — |
| `model.connect` | — | B* | B* | — | — | — |
| `model.bridge` | — | — | B* | B* | B* | — |
| `model.weld` | — | B* | — | — | — | — |
| `model.collapse` | — | B* | B* | — | B* | — |
| `model.delete` | — | B* | B* | B* | B* | B* |
| `model.remove` | — | B* | B* | — | — | — |
| `model.split_edges` | — | — | B* | — | — | — |
| `model.break_vertices` | — | B* | — | — | — | — |
| `model.planar_x/y/z` | — | B* | B* | B* | B* | B* |
| `model.flip_normals` | — | — | — | — | B* | B* |
| `model.cut` | — | B* | B* | — | B* | — |
| `model.target_weld` | — | B* | — | — | — | — |
| `model.exit_tool` | — | B* | B* | B* | B* | B* |
| `menu.repeat` | C | C | C | C | C | C |

`B*` includes the supported unmodified Editable Poly target, open Modify panel,
matching level, and operation-specific selection/readiness checks. `menu.repeat`
reopens the current eligible action/dialog; destructive actions are non-repeatable.

## Fake-runtime tests run here

`tests/test_max_host.py`, `tests/test_native_operations.py`, and
`tests/test_transactions.py` cover the final source mappings on local Python
3.14 (exact counts are in the final integration evidence in
[`VERIFICATION.md`](VERIFICATION.md)):

- importing the package/bindings/bootstrap without importing `pymxs` or PySide6;
- lazy-runtime failure with a clear error;
- 0–5 level mapping for a supported target, unknown level fallback;
- none/primitive/multiple/Edit Poly/stack/panel-closed target classification;
- equal-size but different selections and equal-count geometry/position changes
  produce different tokens and block stale dispatch;
- changed target identity, deletion, level, and stack/revision block before
  dispatch;
- fixed action allowlist; untrusted action strings and unknown IDs never reach
  `runtime.execute`;
- command-mode ownership is never guessed as clear; same-mode relaunch does not
  toggle off, foreign tools are not cancelled, and the stack item is never
  written;
- every `native_bindings` row has a fixed handler, allowed levels, required
  members, and documentation; missing members yield a specific blocked reason;
- P0/P1/P2 operation calls use the documented members/properties and restore
  temporary settings inside one undo chunk;
- only the four fixed command literals can be executed;
- host exceptions become `error`, not `launched`; false/void/no-change results
  never become `applied`; rollback failure latches topology disabled;
- unreadable pre/post settings, transform, and handles are rejected rather than
  fingerprinted as matching tokens; an unreadable post-failure fingerprint
  latches topology instead of claiming a verified rollback;
- raw `currentTime` is guarded before stringification: `None`/undefined/`none`,
  booleans, failed reads or stringification, blank/whitespace, and the legacy
  `time:unreadable` sentinel (case-insensitive) are rejected, while valid zero
  and readable native-like time text remain accepted. An unreadable pre-edit
  time fails before undo/native edit; an unreadable post-failure time latches
  topology. The earlier blanket “time is rejected” wording was too broad for
  the previous stringify-first code. This is a bounded missing-value guard, not
  full `TimeValue` validation, and the adapter latch message may name only the
  `OperationError`/re-read failure, not current time. Real `TimeValue` remains
  **NOT RUN**;
- fingerprint node/base identity uses the documented
  `runtime.getHandleByAnim()` query, including a base object with no `.handle`
  property; unavailable, non-integer, throwing, or missing queries fail closed
  before the undo chunk instead of using a `.handle` fallback;
- `Holding()`/`RestoreOrRedoing()` must return documented Boolean/integer values;
  `None`, strings, floats, missing methods, and active hold/restore/redo fail
  closed before the undo factory; `Redoing()`-only fixtures are no longer used;
- non-Modify and unreadable command-panel task modes disable native topology;
  the task mode changes the snapshot revision, while raw-level-0 node transforms
  remain available;
- documented scale tool modes `#uscale`, `#nuscale`, and `#squash` are idle, not
  foreign command modes;
- bridge `twist` is an integer in the frozen schema, native `bridgeTwist1/2`
  writes, and the Qt integer bounds; fractional/boolean twists are rejected;
- repeat relaunch is fresh and uses the current context; destructive actions are
  non-repeatable;
- snapshots/availability do not mutate selection/modifiers or call conversion,
  modifier-add, collapse, scene-reset, or stack-current-object APIs;
- the real shared controller opens/parametrizes/applies through the adapter,
  blocks a stale context after the dialog, and can release an exact owned mode
  through the public controller/adapter hook.

These tests do **not** verify Autodesk behavior, real selection bitarrays,
viewport focus, undo grouping/rollback, DPI, or command-mode toggling. There is
no PySide6 or Max install in the test environment; Qt tests are separately
skipped when absent.

## Runtime checklist (all NOT RUN)

Perform on 3ds Max 2025 and 2025.1 where practical, recording the exact build,
Python version, and operator. Do not infer any item from the sandbox or mocks.

### Loading and teardown

- [ ] Import `max2025_menu_prototype.max_host` in Max Python; confirm no widgets, hooks, scene changes, or startup entries are created.
- [ ] `show_tools()` creates/raises the one Max-parented tools panel; repeated calls reuse and raise the same panel and shared controller.
- [ ] `show_menu()` opens the optional shared latched menu; Escape, outside click, and focus loss close it.
- [ ] `install_launcher()` returns the same shared panel as a click-to-open alias; confirm no hold-to-open widget or trigger-release claim remains.
- [ ] `uninstall()` closes pending parameter dialogs, clears only the prototype prompt hook, disposes only prototype widgets, invalidates pending input, and is safe to repeat.
- [ ] With a foreign viewport tool active, `uninstall()` never cancels or exits that tool; exact owned-mode cleanup uses `MenuController.release_owned_native_mode()` → adapter `release_owned_mode()` and blocks foreign/stale ownership.
- [ ] After uninstall and Max restart, confirm no permanent hotkey, startup script, registry, or right-click change remains.

### Context and revalidation

- [ ] Editable Poly at levels 0–5 reports object/vertex/edge/border/polygon/element respectively, with border ≠ edge and element ≠ polygon.
- [ ] No selection, primitive, mesh, multiple objects, nonempty modifier stack, closed Modify panel, a non-Modify task mode (e.g. Create/Utility), an unreadable task-mode query, and active Edit Poly each report a safe context and readable disabled reasons.
- [ ] Active Edit Poly never receives a base-object topology operation and is never converted.
- [ ] Different selections with the same component count produce different selection tokens; committing after the change is blocked.
- [ ] Renaming/deleting the target, changing levels, or changing the active stack item while open blocks commit with a revalidation message.
- [ ] An active command mode (move/rotate/scale or a modeling mode) is never misinterpreted as a verified-clear state; unsupported rows remain disabled.

### Dispatch and undo

- [ ] `transform.move`/`rotate`/`scale` enter the documented Max mode and the result message does not claim completion.
- [ ] Repeating a transform launch is a fresh mode entry, not a duplicated amount or macro replay.
- [ ] `view.frame_selection` frames the current selection in the active viewport.
- [ ] Opening, hovering, snapshotting, and availability checks create no undo chunk and no geometry/stack change.
- [ ] `menu.repeat` relaunches the last successful repeatable action against the current context; failed/cancelled/disabled attempts do not replace it; Cap and Frame selection never become repeat targets.
- [ ] Cap uses selected open borders only, invokes `polyop.capHolesByEdge`, and creates one explicit undo unit; reject/cancel leaves no edit.

### Input, focus, and display

- [ ] Manual latched popup entry commits only by explicit click/Enter; trigger release never commits.
- [ ] Focus returns to the surviving origin widget; no synthetic keystrokes or unrelated window activation.
- [ ] Escape is consumed by the open popup/dialog and does not fall through to a viewport tool; keys do nothing while closed.
- [ ] Second invocation cancels the open menu without launching; a later explicit invocation gets a new session and ignores late old events.
- [ ] DPI scaling at 100/150/200% and multiple monitors: popup/panel clamp to available screen geometry in logical pixels; placement never arms a row.
- [ ] `install_launcher()` click opens/raises the panel; there is no hold threshold, short-click inactivity, or viewport trigger-release behavior to test.
- [ ] Panel level buttons change `subObjectLevel` through the shared controller; **Refresh context** is read-only and skips while a menu or parameter dialog is pending.

### Honest-result audit

- [ ] Every disabled row has a nonempty reason; no row silently disappears or is reordered by availability.
- [ ] No `launched` result is treated as proof of a completed modeling operation.
- [ ] Browser simulation is visibly separate from native dispatch in logs and UI.

## Result ledger

| Date | Environment | Check group | Result | Evidence/notes |
| --- | --- | --- | --- | --- |
| — | No 3ds Max 2025 install | Loading/teardown | **NOT RUN** | Documentation and fake-runtime tests only |
| — | No 3ds Max 2025 install | Context/revalidation | **NOT RUN** | — |
| — | No 3ds Max 2025 install | Dispatch/undo | **NOT RUN** | — |
| — | No 3ds Max 2025 install | Input/focus/DPI | **NOT RUN** | — |
| — | No 3ds Max 2025 install | Honest-result audit | **NOT RUN** | — |

## Known gaps

- Command-mode and command-panel-task-mode query/readback exposure and behavior
  remain host-unverified; unsupported/unknown modes and task modes fail closed.
- The revised `Holding()`/`RestoreOrRedoing()` guard and unreadable-fingerprint
  lockout have fake-runtime coverage only; real Max return types and undo/redo
  timing remain NOT RUN.
- pymxs bitArray iteration/`numberSet`/`as array` coercion must be rechecked in
  Max; failed coercion keeps topology disabled rather than inventing indices.
- The single undo chunk and rollback/lockout logic is fake-runtime tested only;
  real Max undo grouping, Ctrl+Z/Redo, and exception-suppression behavior are
  NOT RUN.
- No live preview/caddies or numeric typed transforms; immediate documented
  operations are used where the plan specifies them.
- No Edit Poly modifier support in native v1.
- No viewport right-click replacement, global hotkey, or startup install.
- `install_launcher()` is a click-to-open alias for the shared tools panel; the
  retired hold widget and trigger-release routing are not claimed. The optional
  popup remains latched-only.
- Uninstall's owned-mode cleanup now uses the public
  `MenuController.release_owned_native_mode()` → adapter `release_owned_mode()`
  seam; exact ownership, foreign-tool safety, and real-host behavior remain
  NOT RUN.

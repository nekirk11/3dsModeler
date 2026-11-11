# Tomorrow's first Max 2025 test — short checklist

**Status: NOT RUN.** This is the ordered first-test checklist, not a validation
result. Native operation metadata stays `runtime_status="not_run"` until an
independently reviewed real-host run says otherwise.

The preferred delivery is the Git checkout at `C:\dev\3dsModeler` (branch
`dev`), launched with `tools/max_pull_and_load.ms`. The ZIP checklist is a
separate fallback layout; do not mix the two copies in one Max process. The
full operator plan is [`plan-FirstMaxTest.md`](plan-FirstMaxTest.md).

| Field | Value |
| --- | --- |
| Date prepared | 2026-11-11 |
| Delivery mode | Git checkout `C:\dev\3dsModeler`, branch `dev` (ZIP fallback only if Git is unavailable) |
| Commit SHA loaded | _record the full SHA printed by the launcher; for ZIP record `n/a`_ |
| Max build | _record from Max; not run here_ |
| Python / Qt inside Max | _record from Max; target bundled Python 3.11 / PySide6 6.5_ |
| ZIP SHA-256 (fallback only) | _paste from the delivered `.zip.sha256`; verify before extracting_ |
| Extraction parent (fallback only) | `C:\MaxTools\TestReadiness\` (contains `max2025_menu_prototype\`) |

Use disposable, manually prepared geometry only. Keep reports local until names,
paths, and parameter values have been reviewed. Never weaken a guard to make a
step pass.

| Step | Expected | Outcome + notes |
| --- | --- | --- |
| 1. Load and diagnose | Save and inspect `tools/max_pull_and_load.ms` from the published `dev` branch; fresh Max 2025 session; **Scripting > Run Script...**; Git clone/update completes without a prompt; Listener shows the checkout root and full commit SHA; one tools panel. Default diagnostics open no controller and make no edit. Select exactly one manually prepared unmodified, non-instanced Editable Poly, open Modify/base object, choose the required level/components. | **NOT RUN** |
| 2. P0 geometry first | On selected polygons: extrude → inset → bevel. On selected edges and vertices: chamfer. Use small safe amounts; record actual geometry/selection/settings before and after, not just APPLIED text. Inspect native Undo labeling after each edit instead of stacking edits on an unknown failed state. Grow/shrink, edge loop/ring/clear, and transform/frame checks belong in this P0 gate where eligible. | **NOT RUN** |
| 3. P0 interaction safety | Parameter Cancel, Escape, and window close leave geometry, settings, selection, and history unchanged. Repeat reopens fresh default parameters on the current selection and never replays previous amounts. One Undo restores each committed P0 edit; Redo restores its result with pre-existing history intact. If a pending dialog blocks a selection/target change, commit must block. | **NOT RUN** |
| 4. Stop gate | Unexpected mutation, false success, unavailable P0 member, crash, failed Undo, or rollback lockout ⇒ stop that operation. Run read-only diagnostics only when no dialog is busy; retain local result/error/build/steps (private details only if needed); inspect scene/history. No automatic retries, blind Undo, or adapter reset to bypass lockout. Record failures as FAILED/BLOCKED, distinctly from NOT RUN. | **NOT RUN** |
| 5. Secondary only after P0 passes | Cap/connect/bridge and border-vertex weld; component remove versus delete/collapse; split/break/planar/flip; P2 cut/target-weld/owned-exit and foreign-mode cancellation; then repeat show/uninstall and focus/DPI/multi-monitor checks. Use the detailed matrix in [`MAX2025-VALIDATION.md`](MAX2025-VALIDATION.md) for fixture/operation semantics. | **NOT RUN** |
| 6. Update drill (Git only) | Re-running the `.ms` at the same commit reuses the panel. A dirty checkout refuses without changes. A pull that advances `dev` retires the old UI and requires a fresh Max session. Authentication failure ends with a finite message and no popup. | **NOT RUN** |

## Diagnostics

- Run `print(bootstrap.report_diagnostics())` for the default local report.
- `include_details=True` is opt-in and prints **LOCAL PRIVATE DETAILS**; review
  before sharing.
- The report is read-only. It does not prove an operation works, clear a latch,
  or replace native Undo/Redo evidence.
- On rollback lockout: **STOP** and inspect the scene/history. Do not blindly
  Undo, retry, or reinitialize the adapter.

## Do not

- Do not run the batch native smoke script as the first test; it mutates selected
  geometry and is not a diagnostic.
- Do not use a production scene or real work.
- Do not install Python/Qt packages or replace Max's bundled PySide6.
- Do not mark fake-runtime, source, AST, or Linux launcher results as host passes.

# Tomorrow's first Max 2025 test — V2 live checklist

**Status: NOT RUN.** Every row below is a live-host check with no evidence yet.
Native metadata stays `runtime_status="not_run"` until an independently
reviewed real-Max run records per-route results. Source, fake-runtime, and
optional offscreen Qt tests are not host evidence.

Preferred delivery is the Git checkout at `C:\dev\3dsModeler` (branch `dev`)
launched with `tools/max_pull_and_load.ms`. The ZIP is a fallback layout only;
never mix the two copies in one Max process. Deeper detail:
[`MAX2025-VALIDATION.md`](MAX2025-VALIDATION.md),
[`INTERACTION.md`](INTERACTION.md), [`NATIVE-MODELING.md`](NATIVE-MODELING.md).

| Field | Value |
| --- | --- |
| Date prepared | 2026-11-11 |
| Delivery mode | Git checkout `C:\dev\3dsModeler`, branch `dev` (ZIP fallback only without Git) |
| Commit SHA loaded | _record the full SHA printed by the launcher_ |
| Max build / Python / Qt | _record from Max; target bundled Python 3.11 / PySide6 6.5_ |
| ZIP SHA-256 (fallback only) | _paste from the delivered `.zip.sha256`; verify before extracting_ |

Use disposable, manually prepared geometry only. Never weaken a guard to make a
step pass. Record actual geometry/selection/settings/history, not just status
text; mark unsupported routes BLOCKED, distinct from NOT RUN and FAILED.

## Live checklist (all NOT RUN)

| # | Check | Expected | Outcome |
| --- | --- | --- | --- |
| 1 | Fresh load | Save and inspect `max_pull_and_load.ms`; **Scripting > Run Script...** in a fresh Max 2025. Git clone/update is responsive and asynchronous; Listener shows checkout root and full SHA; the V2 Spotlight palette opens once; no V1 panel/dialog appears. | **NOT RUN** |
| 2 | Spotlight typing isolation | The real search line owns the caret. Typing `W`, `E`, `R`, `X`, `1`–`5`, and Space inserts text and never fires Max tools or context switches. Dark pills/buttons stay legible under Windows theme/DPI/scaling. | **NOT RUN** |
| 3 | Hotkey Editor binding | Macro category `3dsModeler` (with `ThreeDsModeler_Search` and context macros) is present in **Customize > Hotkey Editor** after launch. Assign Search manually, restart Max, trigger it before any package import: local `C:\dev\3dsModeler` loads immediately, cold/warm latency is bounded, and context macros work before the first palette launch. | **NOT RUN** |
| 4 | Contexts | Plain click switches; Ctrl+Click converts selection; Tab/Shift+Tab and Alt+0..5 switch; typed `1`–`5` stays text. Ordinary switches do not convert geometry. Esc/outside focus restores a usable viewport; reopening never duplicates widgets or actions. | **NOT RUN** |
| 5 | 3-tier preparation | Tier 1: safe primitive/Editable Mesh auto-converts to Editable Poly in one undo. Tier 2: poly base with Symmetry/TurboSmooth steps to base, preserves the stack, shows end result. Tier 3: instanced/shared base, non-poly + modifiers, unreadable state, or **any** Edit_Poly modifier blocks before writes; confirmed *Collapse to Poly* / manual Make Unique remedies work. | **NOT RUN** |
| 6 | Live caddies vs 0-sel modes | Selected components route to the matching native caddy (Chamfer, Extrude, Bevel, Inset, ConnectEdges, Weld→`#WeldSelected`, Tessellate, MSmooth→`#MeshSmooth`, bridge targets) and report `launched`. Zero components route to the matching fixed command mode, never a different tool. Caddy Cancel leaves no caddy edit; the separate auto-prep conversion is its own undo record; no Qt/undo hold spans the caddy. | **NOT RUN** |
| 7 | Clean Dissolve | Edge `dissolve` removes the selected edges and, in one undo chunk, only recorded inline valence-2 endpoints. Corners, bends, branches, unrelated/coincident vertices, and unsupported nonmanifold cases are preserved; ambiguous identity rolls back with no partial topology. One Undo restores both passes. | **NOT RUN** |
| 8 | `>` commands | `> Diagnostics` is read-only/redacted, works with unsupported or no selection. `> Pull Updates (Git)` is asynchronous, dev-only, finite on failure; unchanged SHA reopens the palette, advanced loaded SHA retires the UI and requires restart. `> Unload UI` removes only in-memory UI/hooks; macros, hotkeys, native modes, lockout, and scene persist. | **NOT RUN** |
| 9 | Search/pivots | Cross-DCC/user aliases, fuzzy partials, active-context-first ranking, pins/hidden IDs, and cross-context Enter behave as documented. `cap` from selected open edges pivots to Border and caps only intended holes; closed/mixed/empty yields the safe documented result. Malformed overlay warns but still launches. | **NOT RUN** |
| 10 | Stop gate | Unexpected mutation, false success, unavailable member, crash, failed Undo, or rollback lockout stops that route. Record local result/error/build/steps (private details reviewed), inspect scene/history, and do not auto-retry, blindly Undo, or reset the adapter to bypass lockout. | **NOT RUN** |

After every mandatory row has recorded evidence, the owner reviews the `dev`
diff and signs off the tested dev SHA. Main promotion is a separate,
user-authorized release step.

## Diagnostics

- `print(bootstrap.report_diagnostics())` is the default read-only local report.
- `include_details=True` prints **LOCAL PRIVATE DETAILS**; review before sharing.
- On lockout: **STOP**; inspect scene/history. Do not blind Undo, retry, or
  reinitialize the adapter.

## Do not

- Do not use a production scene, install packages, or replace Max's PySide6.
- Do not run the mutating native smoke script as the first test.
- Do not mark source, fake-runtime, AST, Linux launcher, or offscreen Qt results
  as host passes.

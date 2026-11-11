# Integration verification evidence

**Date:** 2026-11-11
**Scope:** integration writer run for the whole `max2025_menu_prototype/`
tree. The planning document and the Houdini reference tree were not modified.
No Git operations were performed.

This page records what was actually executed after the integration changes:
core tap-latch semantics, Qt pre-dispatch close/focus ordering, and the full
aggregate checks. It does not promote the Max adapter: native runtime checks
remain **NOT RUN**.

## Integration changes covered here

- A quick tap (release before the profile threshold) now **latches** in place
  with no armed or highlighted row. It does not close and does not launch.
  An explicit click or Enter is still required. Hold-neutral cancellation and
  post-threshold hold-release commit are unchanged.
- `MenuController.set_pre_dispatch` / `clear_pre_dispatch` add a synchronous
  renderer hook. It runs after revalidation succeeds and immediately before
  `HostAdapter.launch`. The Qt popup uses it to hide and restore focus before
  native dispatch. If the hook raises, dispatch is refused with an `error`.
- The Qt popup is still latched-only in practice: it has no trigger-release
  input. Its `held` phase is a renderer demo state, not a Max hold-support
  claim.

## Cycle 1 review fix (active Edit Poly sub-object levels)

**Finding:** an active Edit Poly target at sub-object levels 2–3 reported the
safe `object` context, but the snapshot revision did not include the raw
sub-object level. Changing level 2 → 3 therefore left the snapshot identity
unchanged and object transforms stayed enabled, so a stale menu could dispatch
`max move`.

**Fix:** the adapter now keeps the raw sub-object level in the revision and in
the opaque selection-token fingerprint for unverifiable active sub-object
targets. Those snapshots carry an explicit unverified-sub-object marker.
`bindings.object_transform_check` rejects `transform.move/rotate/scale` whenever
that marker is present, while ordinary object-level Edit Poly (raw level 0)
still allows node transforms. No conversion, selection repair, or stack write
was added.

Regression coverage in `tests/test_max_host.py`: blocked transforms for active
Edit Poly levels 2 and 3, changed 2 → 3 revalidation blocked before dispatch,
direct binding refusal, and object-level Edit Poly transform still allowed.

## Commands and results

| Check | Command | Result |
| --- | --- | --- |
| Aggregate stdlib tests | `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s max2025_menu_prototype/tests -p 'test_*.py' -v` | **101 tests, OK, 1 skipped** (`test_qt_ui`: PySide6 not installed) |
| JavaScript syntax | `node --check max2025_menu_prototype/sandbox/static/app.js` | **OK** |
| Python 3.11 syntax | AST `feature_version=(3, 11)` + `compile()` over all 23 `*.py` files | **OK**, no bytecode written |
| Baseline preservation | `PYTHONDONTWRITEBYTECODE=1 python3 -m max2025_menu_prototype.tools.baseline verify --manifest "$BASELINE_MANIFEST"` | **293 protected entries unchanged** |
| Live sandbox matrix | HTTP driver against `http://127.0.0.1:8771/` using the same endpoints/controller as the browser | **42/42 checks passed** |

Aggregate test composition: core/state-machine tests, sandbox HTTP/security
tests, fake-runtime Max adapter tests, and one optional Qt module skip. The Qt
tests (including the new pre-dispatch visibility/focus ordering test) were
**not run** on this host because PySide6 is absent.

## Sandbox interaction matrix

The browser tool reported `[browser.disconnected]`; no local Chromium/Firefox
or `DISPLAY` exists on this host. The matrix was therefore exercised through
the live sandbox HTTP API, which drives the same `SandboxServer` and shared
`MenuController` as `app.js`. DOM rendering/hit-testing, visual clamping,
browser focus restoration, browser console/network, and screenshots are
**SKIPPED — no connected browser**.

| Area | Result |
| --- | --- |
| Six contexts (`object`…`element`) | PASS — distinct rows; border keeps Cap, element has no polygon tools |
| Zero selection / zero components | PASS — disabled rows stay with readable reasons (`Select one supported object`, `Select vertices`) |
| Primitive / active Edit Poly | PASS — primitive transforms enabled without conversion; Edit Poly reports safe object context and no topology rows |
| Tap latch | PASS — release at 70 ms latches, no armed/highlighted row, no launch; explicit click then launches |
| Hold row release | PASS — pointer move onto `transform.move`, release at 400 ms → simulated launch |
| Hold-neutral cancellation | PASS — release inside 12 px neutral zone closes with no launch |
| Focus loss while held | PASS — closes with `Cancelled by focus loss` |
| Escape | PASS — closes with `Cancelled with Escape` |
| Search / clear / no results | PASS — `grow` filters in place, clearing restores profile order, no-results Enter explains and stays open |
| Keyboard traversal / disabled row | PASS — ArrowDown highlights the disabled row and shows its reason; Enter does not launch |
| Repeated invocation | PASS — second invocation closes without reopening; next gets a fresh session |
| Stale context while open | PASS — scenario change blocks commit with a revalidation message |
| Repeat | PASS — label names the last action, relaunches it, rejects a stale context, disables in an ineligible context, failed attempts do not replace the record |
| Profiles | PASS — reorder/hide/bounds applied, hidden action omitted, export/import round-trip, malformed import rejected with active profile intact, reset restores the default |

Evidence: local HTTP matrix detail retained privately (not published). The
sandbox was running current code on `127.0.0.1:8771`; it was restarted before
this run so the static cache and Python modules matched the edited tree.

## Historical cycle-1 native scope (superseded)

This section records the earlier partial bridge and is **superseded** by the
final integration evidence at the end of this page. At that earlier point:

- Only **four** native bindings existed: `transform.move`, `transform.rotate`,
  `transform.scale`, and `view.frame_selection`.
- Selection and modeling rows were visible but **disabled** with reasons; no
  conversion, stack edit, immediate default-amount edit, or guessed command mode
  was added then.
- `menu.repeat` was core-owned and only relaunched an available repeatable
  action; the four native bindings did not prove completion of a transform.
- Max runtime validation was **NOT RUN — no 3ds Max 2025 installation**.

The final integration now has fixed source bindings for the P0/P1/P2 catalog
IDs; every native item is still **NOT RUN** in Max.

## Outstanding browser limitations (documented, not fixed in cycle 1)

- Focus loss during a pending open request can miss cancellation because the
  blur handler only sends when a view is already open.
- Queued browser events read the session when they execute, so a delayed event
  can bind to a later session instead of the one that produced it.
- A Space-held menu ignores pointer movement for row arming; hold-release via
  the keyboard cannot select a row with the pointer.
- An outside click sends cancellation but does not prevent the underlying
  control from activating.
- Timestamps above 10,000,000 ms are rejected, so a very long-lived tab can
  have a gesture request refused.
- Copy/Download reuse existing non-empty export textarea content, which can be
  stale after later in-memory edits.

## Known skipped checks

- Qt popup tests (PySide6 absent), including the pre-dispatch close/focus test.
- Browser DOM interaction, visual clamped placement, browser focus restore,
  console/network inspection, and screenshots (`[browser.disconnected]`, no
  local browser, no display).
- Every Max 2025 runtime checklist item in
  [`MAX2025-VALIDATION.md`](MAX2025-VALIDATION.md).

## W1 native-expansion contract gate (2026-11-11)

This section is additive. The cycle-1 results above remain historical evidence
for the 101-test menu prototype; they are not relabelled as native modeling
coverage.

**Task-start snapshot (outside the prototype, retained privately):** the
snapshot contains the full prototype manifest including existing caches, byte
copies of every pre-existing non-cache file, the plan copy/hash,
root/reference manifest, separate prototype scan, old-baseline verification,
runtime availability, baseline test output, syntax output, and the
pre-existing-gaps inventory. Planned new paths did not exist at task start.

| Check | Command | Result |
| --- | --- | --- |
| Task-start aggregate tests | `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s max2025_menu_prototype/tests -p 'test_*.py' -v` | **101 tests, OK, 1 skipped** (recorded before any W1 source edit) |
| Old baseline preservation | `PYTHONDONTWRITEBYTECODE=1 python3 -m max2025_menu_prototype.tools.baseline verify --manifest "$BASELINE_MANIFEST"` | **293 protected entries unchanged** |
| W1 aggregate tests | same aggregate command after W1 | **140 tests, OK, 1 skipped** (`test_qt_ui`: PySide6 absent) |
| W1 parameter schema tests | `... -p 'test_parameters.py' -v` | **16 tests, OK** |
| W1 controller lifecycle tests | `... -p 'test_native_integration.py' -v` | **23 tests, OK** |
| W1 core regression tests | `... -p 'test_core.py' -v` | **45 tests, OK** |
| Python 3.11 syntax | AST `feature_version=(3, 11)` + `compile()` over all prototype `*.py` | **OK**, no bytecode written |
| Host-free imports | import `max2025_menu_prototype.max_host.adapter/bindings/bootstrap` outside Max | **OK** (not a Max runtime test) |
| JavaScript syntax | `node --check max2025_menu_prototype/sandbox/static/app.js` | **OK** |

**Scoped W1 re-check while W2/W3 were active:** no aggregate run was performed
against their partial paths. The W1-only checks were 16 parameter-schema tests,
23 controller-lifecycle tests, and 45 core-regression tests — all **OK**; 3.11
AST + compile over the 10 W1 Python files **OK**; old protected baseline **293
entries unchanged**. This was an intermediate check only; the final combined
suite and full-tree delta are recorded at the end of this page.

Scope of this gate: `core/**`, `tests/test_core.py`, new
`tests/test_parameters.py`, new `tests/test_native_integration.py`, README, and
docs. No `max_host/**`, `qt_ui/**`, `sandbox/**`, root package file, reference
tree, Git operation, or dependency installation was changed. The frozen
contracts and the exact panel symbol (`MenuController.summary()` →
`ControllerSummary`) are documented in
[`NATIVE-MODELING.md`](NATIVE-MODELING.md).

**Still NOT RUN:** all native Max 2025 behavior, the future Qt panel/dialog,
undo/postcondition behavior, and viewport focus/DPI cases. The browser remains
simulated and parameterized modeling rows there are intentionally unavailable
without a native dialog.

## W3 launcher/UI handoff and final integration

W3 completed and released `qt_ui/**`, `max_host/bootstrap.py`, and its tests.
The reconciled public implementation is `bootstrap.show_tools()` as the primary
Max-parented tools panel, `show_menu()` as the optional shared latched popup,
`install_launcher()` as a **click-to-open alias** for the panel (the hold
launcher is retired), one shared controller/QApplication, and one
bootstrap-lifetime parameter prompt. The panel uses `controller.invoke` and
`controller.summary`; Refresh context uses the public `open` + immediate `cancel`
contract because no separate refresh contract exists. Uninstall closes pending
dialogs and disposes prototype widgets.

The owned-mode seam is now connected: uninstall calls the public
`MenuController.release_owned_native_mode()`, which delegates only to an adapter
`release_owned_mode()` public hook. The adapter exits only its exact recorded
target token and command mode; foreign/stale/unknown modes are blocked. No
private adapter access or guessed mode exit is used.

## Final integration evidence (2026-11-11)

Integration repair: two fake meshes created invalid two-vertex face loops after
mutation (and cap created a two-vertex face), which the hardened geometry
fingerprint correctly rejected. The fixtures now produce ≥3-vertex face loops;
the real `read_geometry` guards were not weakened. No implementation defect was
found in the operations/adapter path by these 7 errors.

| Check | Command | Result |
| --- | --- | --- |
| Combined stdlib suite | `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s max2025_menu_prototype/tests -p 'test_*.py' -v` | **237 tests, OK, 12 skipped** (11 `test_native_ui` Qt skips + 1 optional `qt_ui` module skip) |
| JavaScript syntax | `node --check max2025_menu_prototype/sandbox/static/app.js` | **OK** |
| Python 3.11 syntax/compile | AST `feature_version=(3, 11)` + `compile()` over all **37** prototype `*.py` files | **OK**, no bytecode written |
| Host-free imports | import `max_host.adapter`, `bindings`, `bootstrap` outside Max; assert no `pymxs`/`PySide6` | **OK** |
| Protected baseline | `PYTHONDONTWRITEBYTECODE=1 python3 -m max2025_menu_prototype.tools.baseline verify --manifest "$BASELINE_MANIFEST"` | **293 entries unchanged** |
| Full task delta | `delta_report.py --scope all --label all` against the task-start snapshot | **15 added, 19 modified, 0 deleted, 13 unchanged**; all 32 snapshot copies verified; patch has no skips |

Review paths (retained privately, not published):

- Task-start snapshot directory (prototype manifest, byte copies, and baseline
  logs).
- Delta tool/report/patch directory (`delta_report.py`, `delta-all.json`,
  `delta-all.md`, `delta-all.patch`, `run-all.json`).

**Still NOT RUN:** all native Max 2025 behavior, real undo grouping/rollback,
viewport focus/DPI, and actual Qt panel/dialog execution. The browser remains
simulated and parameterized modeling rows there are intentionally unavailable
without a native dialog. Every native binding remains `runtime_status="not_run"`.

## Cycle 1 review remediation evidence (2026-11-11)

This is the first implementation cycle's review-repair pass, not a new feature
wave. The corrections are limited to the native safety path and its tests:

- `target_fingerprint` now reads settings from `target.node`/`baseObject` (not
  the `EditablePoly` interface) and raises on unreadable settings, transform, or
  handles; `_verify_rollback` latches topology when the post-failure fingerprint
  cannot be re-read. An `unreadable:*` token can no longer match on both sides
  and falsely verify rollback.
- **Time-specific correction (test-readiness pass):** the earlier blanket claim
  that unreadable time was rejected was too broad. The prior code stringified
  raw `currentTime` before checking, so undefined/`None`, `none`, booleans, and
  blank/whitespace could look readable. The guard now reads raw `currentTime`
  and rejects those values, failed reads/stringification, and the legacy
  `time:unreadable` sentinel case-insensitively, while preserving valid zero and
  readable native-like time text. Pre-edit unreadability fails before undo/native
  edit; post-failure unreadability latches topology. The adapter latch message
  names the `OperationError`/re-read failure, not necessarily current time. This
  is a bounded missing-value guard; real `TimeValue` behavior remains
  **NOT RUN**.
- `hold_state` now uses `theHold.Holding()` plus
  `theHold.RestoreOrRedoing()` and accepts only documented Boolean/integer
  results. Absent, unreadable, holding, restoring, and redoing states all fail
  closed before the undo factory.
- `toolMode.commandMode` allowlists the documented `#uscale`, `#nuscale`, and
  `#squash` scale modes as idle transforms.
- Supported native targets require `getCommandPanelTaskMode() == #modify`; the
  task mode is part of the snapshot revision, and a task-mode change blocks a
  pending launch. Raw-level-0 object transforms remain eligible.
- Bridge `twist` is an integer in the frozen schema, the native
  `bridgeTwist1`/`bridgeTwist2` writes, and the Qt integer bounds; fractional
  and boolean values are rejected.
- Second/final repair: `target_fingerprint` node/base identity now uses the same
  documented `runtime.getHandleByAnim()` query as the adapter instead of a
  `.handle`-only helper. A base object without `.handle` fingerprints and edits
  successfully when the anim query works; unavailable/invalid/throwing/missing
  anim queries fail closed before the undo factory. The test fake is not given
  a synthetic base `.handle`, and no unrelated field or feature changed.
- The test-only native-array fake now supports indexing; production native
  array conversion was not weakened.

| Check | Command | Result |
| --- | --- | --- |
| Final aggregate stdlib tests | `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s max2025_menu_prototype/tests -p 'test_*.py' -v` | **257 tests, OK, 12 skipped** (11 `test_native_ui` Qt skips + 1 optional `qt_ui` module skip) |
| JavaScript syntax | `node --check max2025_menu_prototype/sandbox/static/app.js` | **OK** |
| Python 3.11 syntax/compile | AST `feature_version=(3, 11)` + `compile()` over all **37** prototype `*.py` files | **OK**, no bytecode written |
| Host-free imports | import `max_host.adapter`, `bindings`, `bootstrap` outside Max; assert no `pymxs`/`PySide6` | **OK** (`False False`) |
| Protected baseline | `PYTHONDONTWRITEBYTECODE=1 python3 -m max2025_menu_prototype.tools.baseline verify --manifest "$BASELINE_MANIFEST"` | **293 entries unchanged** |
| Whitespace check | `git -C max2025_menu_prototype diff --check` | **OK** |

**Still NOT RUN:** all native Max 2025 behavior, real `theHold` return/timing,
actual `getCommandPanelTaskMode()` behavior, real undo grouping/rollback,
viewport focus/DPI, and actual Qt panel/dialog execution. The browser remains
simulated. Every native binding remains `runtime_status="not_run"`.

## Test-readiness pass — scoped evidence (2026-11-11)

This readiness pass is not a new feature wave and is not a native validation.
The task-start baseline was **257 tests, OK, 12 skipped**. A, B, and C completed
their exclusive paths and released them before this integration run; the full
aggregate was then run once over the settled tree. The final source-only ZIP is
built only after this document and all source evidence are settled; its hash and
member inventory are reported in the external handoff, not rewritten here.

A (native safety) released its owned paths and reported these **scoped** results:

| Check | Command | Result |
| --- | --- | --- |
| Operation fingerprint tests | `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s max2025_menu_prototype/tests -p 'test_native_operations.py' -v` | **43 tests, OK** (reported by A) |
| Adapter/gateway tests | `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s max2025_menu_prototype/tests -p 'test_max_host.py' -v` | **65 tests, OK** (reported by A) |
| Transaction tests | `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s max2025_menu_prototype/tests -p 'test_transactions.py' -v` | **17 tests, OK** (reported by A) |
| New regression count | compared with the 257-test task-start baseline | **7 new tests** (A's scope) |

A's raw time guard rejects `None`/undefined/`none`, booleans, blank/whitespace,
the case/spacing variant of the legacy sentinel, and throwing getter/`str`;
valid zero and readable native-like time text still fingerprint. A pre-edit
unreadable time does not create an undo chunk or native edit; an unreadable
post-failure time latches topology with both suppressing and propagating fake
undo contexts, while a readable restored state still verifies rollback. The
transaction failure wording is now neutral. The adapter latch message names the
`OperationError`/re-read failure, not specifically current time. All of this is
fake-runtime evidence; real `TimeValue` behavior remains **NOT RUN**.

B (read-only diagnostics) released its owned paths with the frozen signatures
`probe.build_report(runtime=None, *, summary=None, include_details=False)`,
`probe.report(...)`, and `bootstrap.report_diagnostics(*, include_details=False)`:

| Check | Command | Result |
| --- | --- | --- |
| Probe tests | `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s max2025_menu_prototype/tests -p 'test_probe.py' -v` | **16 tests, OK** |
| Bootstrap tests | `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s max2025_menu_prototype/tests -p 'test_bootstrap.py' -v` | **19 tests, OK** |
| Host-free imports | fresh process imports `max_host.adapter`, `bindings`, `bootstrap`, `probe`, and `tools.portable`; no `pymxs`/PySide6/PySide2 | **OK (all False)** |
| Python 3.11 AST/compile | `feature_version=(3, 11)` + `compile()` over all **40** prototype `*.py` files (37 prior + 3 added) | **OK**, no bytecode written |

B's report keeps cached `ControllerSummary` state separate from a temporary
fresh adapter, labels the live latch as unknown to that adapter, skips fresh
probing while busy, checks the main thread before host/summary access, uses the
node/base-object `_property_owner` seam, and defaults to finite categories plus
bounded opt-in private details. No controller creation, latch clearing, scene
mutation, file/network/clipboard write, or native-validation promotion is
claimed. Max/PySide execution remains **NOT RUN**.

C (portability/docs) scoped result:

| Check | Command | Result |
| --- | --- | --- |
| Portable builder tests | `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s max2025_menu_prototype/tests -p 'test_portable.py' -v` | **23 tests, OK** |
| JavaScript syntax | `node --check max2025_menu_prototype/sandbox/static/app.js` | **OK** |
| Protected baseline | `python3 -m max2025_menu_prototype.tools.baseline verify --manifest "$BASELINE_MANIFEST"` | **293 entries unchanged** |
| Whitespace | `git -C max2025_menu_prototype diff --check` | **OK** |
| Aggregate stdlib tests | `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s max2025_menu_prototype/tests -p 'test_*.py' -v` | **310 tests, OK, 12 skipped** (same 11 Qt + 1 optional Qt skip; no host run) |

The portable tests cover deterministic rebuild bytes, one package root, member
and sidecar hashes, cache/Git/evidence/archive/editor exclusion, hidden
`.json`/`.py`/`.md` pruning with visible source/resource preservation,
allowlist rejection, symlink/special/traversal/case-collision refusal,
output-inside-source and overwrite refusal, spaces/non-ASCII paths, extraction,
and host-free import. The README launcher layout and all five README Python
fences were inspectively checked; the archive extraction/import proof is
recorded in the external handoff. Native Max/PySide behavior remains **NOT RUN**.

### Packaging review remediation 1 (High finding)

The first reviewed candidate was safe (51 members), but its builder classified
allowed suffixes before hidden-file pruning. A hidden `.json`/`.py`/`.md` under
an allowed directory (for example `docs/.credentials.json`) could therefore
enter a future archive. The fix applies `_is_pruned_file` before any suffix or
static allowlist acceptance. A regression fixture now covers hidden
`.credentials.json`, `.hidden.py`, and `.hidden.md`, plus preserved visible
`core/default_profile.json`, docs, tests, and all four static assets. Portable
tests are **23 OK**; the aggregate is **310 OK, 12 skipped**. This remains a
source test, not a Max or Windows extraction test.

Two review Medium findings are intentionally **not** fixed in this narrow
change and remain builder limitations:

- **Publication race/clobber:** the builder checks that output/sidecars are
  absent and then uses `os.replace`; a concurrent creator between check and
  replace can still be clobbered. The publication is not an exclusive-create
  transaction.
- **Windows member-name normalization:** the builder rejects absolute paths,
  `..`, backslashes, control characters, and case-insensitive collisions, but
  does not reject every Windows-unsafe normalized name (for example reserved
  device names, trailing dots/spaces, or alternate-data-stream colons) beyond
  its existing checks.

The reviewed candidate and regenerated candidate contain no such member names,
no hidden credentials, and no unexpected content; member inventory and source
hash proof are in the external review evidence.

## Git delivery and publication integration (2026-11-11)

This additive section records the final source gates for the Git-first delivery.
It does not relabel any earlier count and does not promote native runtime state.

The reconciled delivery is:

- preferred: clone `https://github.com/nekirk11/3dsModeler.git` branch `dev`
  to `C:\dev\3dsModeler` and run `tools/max_pull_and_load.ms` from
  **Scripting > Run Script...**;
- fallback: the source-only ZIP with its unchanged `max2025_menu_prototype/`
  root and the separate extraction/import layout.

The launcher is stdlib-importable and keeps Qt/`pymxs` imports lazy. It owns the
Git worker, queued main-thread completion, import alias, commit guard, and
restart-required state. The standalone `.ms` embeds a clone-only bootstrap and
delegates to the Python companion through the frozen seam. Tests use fake
process, worker, bridge, and checkout fixtures.

| Check | Command | Result |
| --- | --- | --- |
| Aggregate stdlib suite | `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s max2025_menu_prototype/tests -p 'test_*.py' -v` | **357 tests, OK, 12 skipped** (11 `test_native_ui` Qt skips + 1 optional `qt_ui` module skip) |
| Python 3.11 AST + compile | `ast.parse(..., feature_version=(3, 11))` and `compile()` over all **43** prototype `*.py` files | **OK**, no bytecode written |
| Host-free lazy imports | fresh process imports `max_host.adapter/bindings/bootstrap/probe`, `tools.portable`, and `tools.baseline`; asserts `pymxs`/`qtmax`/`PySide6`/`PySide2` absent | **OK (all absent)** |
| JavaScript syntax | `node --check sandbox/static/app.js` | **OK** |
| Protected baseline | `PYTHONDONTWRITEBYTECODE=1 python3 -B -m max2025_menu_prototype.tools.baseline verify --manifest "$BASELINE_MANIFEST"` | **293 protected entries unchanged** |

The final real-source ZIP is built after this document settles. Its member
inventory, deterministic repeat hash, extraction import, and publication-history
audit are reported in the external maintainer handoff, not rewritten here.

**Still NOT RUN:** MAXScript parsing/execution in Max, Max's Python bridge,
Windows Git transport, live GitHub clone/update from Max, Qt thread affinity in
Max, native geometry, undo grouping, viewport focus, and DPI. The ZIP is a
source archive, not a host or Windows delivery test.

## Launcher Cycle 1 fixes: checkout trust before companion and Git process-tree timeout (2026-11-11)

This additive section covers only two High findings raised against commit
`3f24a0b`. It does not relabel any earlier count and does not promote native
runtime state.

**Finding 1 — an existing companion ran before checkout validation.** The
standalone `.ms` bootstrap called `runpy.run_path` on
`tools/max_pull_and_load.py` on the main thread whenever that file existed, so
changed on-disk companion code could execute before the checkout's trust was
established. The embedded bootstrap now schedules a worker that performs the
same bounded local checks as the Python companion — Git top-level equals the
checkout root, non-bare repository, `origin` equals `REPO_URL`, branch `dev`,
no in-progress merge/rebase/cherry-pick/sequencer, empty
`--porcelain=v1 --untracked-files=all` status, and a full hexadecimal HEAD —
and only the queued main-thread callback invokes the companion. A dirty,
untrusted, or in-progress checkout is refused before the companion file is
read. If the session already loaded a different SHA, the old UI/bootstrap is
retired (`uninstall()` once) and the run stops with the restart message
instead of executing the updated companion against the loaded code.

**Finding 2 — a timed-out Git child tree could survive.** Killing only the
immediate `git` process let spawned helpers (`git-remote-https`,
`git-credential-manager`, ...) survive and keep the captured stdout/stderr
pipes open, and a suppressed second `communicate` timeout could report
completion while a child still mutated the checkout. Both the Python companion
and the embedded bootstrap now terminate the whole tree on timeout — bounded
`taskkill /F /T /PID <pid>` with `CREATE_NO_WINDOW` and `stdin=DEVNULL` on
Windows, `start_new_session=True` plus `os.killpg(pid, SIGKILL)` on POSIX —
then close pipes and reap with a bounded `wait`. Only a confirmed reap returns
the ordinary `timeout` result; otherwise the timeout message is still returned
but the session is marked restart-required (and the trusted bootstrap retired)
so no retry can race a surviving helper. `tools/max_pull_and_load.ms` was
regenerated from `render_ms_source()`.

Regression coverage in `tests/test_pull_and_load.py` grew from 45 to **63**
cases (18 added). New cases cover: worker validation before the companion for
existing checkouts, dirty/top-level/bare/detached/wrong-branch/wrong-origin/
in-progress/full-SHA refusals with a companion stub that records execution,
changed-SHA retirement without running the updated companion, matching-SHA
reuse, and unreaped-timeout session locking in the embedded bootstrap; plus
Windows `taskkill` and POSIX `killpg` tree termination, pipe closure, bounded
reap, `start_new_session`/`CREATE_NO_WINDOW` process options, and unreaped
`_run_git` propagation in the companion.

| Check | Command | Result |
| --- | --- | --- |
| Launcher regression suite | `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest max2025_menu_prototype.tests.test_pull_and_load -v` | **63 tests, OK** |
| Portable suite | `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest max2025_menu_prototype.tests.test_portable -v` | **25 tests, OK** |
| Aggregate stdlib suite | `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s max2025_menu_prototype/tests -p 'test_*.py' -v` | **375 tests, OK, 12 skipped** (11 `test_native_ui` Qt skips + 1 optional `qt_ui` module skip) |
| Copied `3dsModeler`-named checkout suite | same discovery inside a copied `3dsModeler/` directory with a `max2025_menu_prototype` package alias | **375 tests, OK, 12 skipped** |
| Extracted-ZIP suite | same discovery inside the built archive's extracted `max2025_menu_prototype/` root | **375 tests, OK, 12 skipped** |
| Python 3.11 AST + compile | `ast.parse(..., feature_version=(3, 11))` and `compile()` over all **43** prototype `*.py` files | **OK**, no bytecode written |
| Deterministic ZIP | `python3 -m max2025_menu_prototype.tools.portable --source <copy> --output <artifact>/max2025_menu_prototype.zip` twice | **59 members; identical SHA-256 on repeat** |
| Whitespace | `git diff --check` | **OK** |
| Protected baseline | `PYTHONDONTWRITEBYTECODE=1 python3 -B -m max2025_menu_prototype.tools.baseline verify --manifest "$BASELINE_MANIFEST"` | **293 protected entries unchanged** |

Archive member inventory, both ZIP hashes, extraction import, and the remote
push audit stay in the external maintainer handoff rather than in this file.

**Still NOT RUN for these fixes:** MAXScript parsing/execution in Max, Max's
Python bridge, real Windows `taskkill`/Git transport behaviour, live
clone/update from Max, and Qt queued delivery inside Max. The Windows and POSIX
process paths are simulated in tests only.

## Launcher Cycle 2 fixes: remote ancestry, companion byte trust, and tree-kill proof (2026-11-11)

This additive section covers the three High findings from the re-review of
`53269c3`. It does not relabel any earlier count and does not promote native
runtime state.

**Finding 1 — local trust alone still let an unpushed commit execute its own
companion.** `origin` + `dev` + a clean tree proved nothing about remote
ancestry, and `runpy.run_path` re-read the file from disk after validation.
The embedded bootstrap now, in its worker: (a) refuses a checkout whose HEAD
differs from `_session().loaded_head_sha` immediately, before any network call;
(b) otherwise runs `git fetch --no-tags origin dev`, resolves
`refs/remotes/origin/dev`, and requires `git merge-base --is-ancestor <head>
<remote>` so local-ahead, diverged, or unrelated commits are refused before the
companion file is ever read — and refuses with `restart_required` when a loaded
session's SHA no longer matches the remote; (c) derives the trusted companion
digest from the commit itself with `git cat-file -p <head>:tools/max_pull_and_load.py`
(plus a `git cat-file --filters` digest so a CRLF/filter checkout still matches
its committed bytes). The main thread reads the file bytes once, checks
SHA-256 against that commit-derived digest, and only then runs
`exec(compile(data, path, "exec"), namespace)`; a mismatch retires the old UI
and latches restart-required. `runpy.run_path` is no longer used for the
checkout companion.

**Finding 2 — a failed tree kill could still be reported as a successful
reap.** Reaping the immediate child was treated as proof the whole tree had
stopped. Timeout handling now requires positive evidence: on Windows,
`taskkill /F /T /PID <pid>` must complete inside the deadline with
`returncode == 0` (any error, timeout, or non-zero result means the tree is not
confirmed); on POSIX, the child is a session leader (`start_new_session=True`),
`os.killpg(pgid, SIGKILL)` is only issued when `pgid == pid`, and the group is
polled with `os.killpg(pgid, 0)` until `ProcessLookupError` within the deadline.
`tree_reaped` is true only when both the immediate child is reaped and the tree
is proven gone.

**Finding 3 — timeout paths discarded `tree_reaped=False`.** The
`--absolute-git-dir` probe no longer swallows `LauncherError`, partial-clone and
pre-pull revalidation paths preserve `exc.tree_reaped`, and both workers track a
sticky `unreaped` flag: a `tree_reaped=False` from any subprocess call in the
run forces `unreaped=True` in the worker result even if an inner handler
converted the error, so the main thread latches `restart_required`. The
actionable timeout message is still returned unchanged.

Regression coverage in `tests/test_pull_and_load.py` grew from 63 to **79**
cases (16 added): local-ahead/unrelated-commit refusal and no-network drift
refusal in the embedded worker, loaded-SHA remote move retires without running
the companion, verified-byte execution (asserting `cat-file -p
<head>:tools/max_pull_and_load.py`), commit-digest mismatch and post-validation
disk swap both blocking execution, CRLF filter digest acceptance, POSIX
surviving-group and `killpg`-failure non-confirmation, foreign-group refusal,
Windows `taskkill` non-zero/error non-confirmation, sticky unreaped after an
inner handler swallowed the error, and operation-probe/partial-clone
`tree_reaped` preservation.

| Check | Command | Result |
| --- | --- | --- |
| Launcher regression suite | `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest max2025_menu_prototype.tests.test_pull_and_load -v` | **79 tests, OK** |
| Portable suite | `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest max2025_menu_prototype.tests.test_portable -v` | **25 tests, OK** |
| Aggregate stdlib suite | `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s max2025_menu_prototype/tests -p 'test_*.py' -v` | **391 tests, OK, 12 skipped** (11 `test_native_ui` Qt skips + 1 optional `qt_ui` module skip) |
| Copied `3dsModeler`-named checkout suite | same discovery inside a copied `3dsModeler/` directory with a `max2025_menu_prototype` package alias | **391 tests, OK, 12 skipped** |
| Extracted-ZIP suite | same discovery inside the built archive's extracted `max2025_menu_prototype/` root | **391 tests, OK, 12 skipped** |
| Python 3.11 AST + compile | `ast.parse(..., feature_version=(3, 11))` and `compile()` over all **43** prototype `*.py` files | **OK**, no bytecode written |
| Deterministic ZIP | `python3 -m max2025_menu_prototype.tools.portable --source <copy> --output <artifact>/max2025_menu_prototype.zip` twice | **59 members; identical SHA-256 on repeat** |
| Whitespace | `git diff --check` | **OK** |
| Protected baseline | `PYTHONDONTWRITEBYTECODE=1 python3 -B -m max2025_menu_prototype.tools.baseline verify --manifest "$BASELINE_MANIFEST"` | **293 protected entries unchanged** |

Archive member inventory, both ZIP hashes, extraction import, and the remote
push audit stay in the external maintainer handoff rather than in this file.

**Still NOT RUN for these fixes:** MAXScript parsing/execution in Max, Max's
Python bridge, real Windows `taskkill`/Git transport and CRLF checkout
behaviour, live clone/update from Max, and Qt queued delivery inside Max. The
Windows and POSIX process paths are simulated in tests only.

## Launcher Cycle 3 fixes: empty-target clone, full SHA, and deterministic refs (2026-11-11)

This additive section covers the live Listener report from a Windows 3ds Max
2025 session. It does not relabel any earlier count and does not promote native
runtime state.

**Finding 1 — a pre-existing `C:\dev\3dsModeler` defeated the first clone.**
Previously any existing path was sent to checkout validation, so an empty
directory (or one holding the saved `.ms`) failed with the vague
`The checkout layout is not a complete prototype package. Repair it outside Max.`
message. Both launchers now treat a missing **or empty** checkout directory as a
clone target and run the same `git clone --branch dev ... <checkout>` directly
into it. A non-empty directory without `.git` stops with a finite, actionable
message naming at most three sanitized entry basenames (40 characters each,
control characters removed, sorted): it says the path already exists and is not
a Git checkout, tells the operator to save `max_pull_and_load.ms` **outside**
the checkout (for example `C:\dev\max_pull_and_load.ms`), and to leave the
directory empty or absent before retrying. Detection happens before any Git
process, so no local repository state is touched.

**Finding 2 — completion printed only 12 SHA characters and gave no
diagnostics hint.** Successful loads now print the full 40-character commit,
together with the checkout root, and a one-line Listener tip after the panel is
shown: `Diagnostics (Python tab, or in MAXScript tab run): python.Execute
"from max2025_menu_prototype.max_host import bootstrap;
print(bootstrap.report_diagnostics())"`. The standalone `.ms` bootstrap prints
the same entry, because the operator may still be in the MAXScript Listener
tab.

**Finding 3 — reviewer items closed while editing the same paths.**
The fetch is now an explicit refspec
`git fetch --no-tags origin +refs/heads/dev:refs/remotes/origin/dev`, so
`refs/remotes/origin/dev` is updated deterministically instead of relying on
default refspec/configuration. Companion-byte verification no longer runs
`git cat-file --filters` (which could execute local clean/smudge filters); it
reads the committed blob with `git cat-file -p <head>:tools/max_pull_and_load.py`
and accepts either the exact `sha256(disk_bytes) == sha256(blob)` match or an
exact CRLF-normalized match
(`disk_bytes.replace(b"\r\n", b"\n") == blob.replace(b"\r\n", b"\n")`). On
POSIX, `start_new_session=True` guarantees `pgid == pid`, so the tree kill uses
`pgid = proc.pid` directly for `os.killpg(pgid, SIGKILL)` and for the
`os.killpg(pgid, 0)` poll, including when the group leader has already exited
and `getpgid` would raise.

Regression coverage in `tests/test_pull_and_load.py` grew from 79 to **86**
cases (7 added): empty-directory clone routing for the companion and the
embedded bootstrap, the non-repository message sample/bounds/sanitization,
full-SHA and Listener-tip output on load and no-op completion, the exact fetch
refspec, CRLF-normalized blob acceptance without `--filters`, and the POSIX
leader-exit group kill.

| Check | Command | Result |
| --- | --- | --- |
| Launcher regression suite | `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest max2025_menu_prototype.tests.test_pull_and_load -v` | **86 tests, OK** |
| Portable suite | `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest max2025_menu_prototype.tests.test_portable -v` | **25 tests, OK** |
| Aggregate stdlib suite | `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s max2025_menu_prototype/tests -p 'test_*.py' -v` | **398 tests, OK, 12 skipped** |
| Copied `3dsModeler`-named checkout suite | same discovery inside a copied `3dsModeler/` directory with the frozen alias seam | **398 tests, OK, 12 skipped** |
| Python 3.11 AST + compile | `ast.parse(..., feature_version=(3, 11))` and `compile()` over all **43** prototype `*.py` files | **OK**, no bytecode written |
| Deterministic ZIP | `python3 -m max2025_menu_prototype.tools.portable` twice | **59 members; identical SHA-256 on repeat** |
| Whitespace | `git diff --check` | **OK** |
| Protected baseline | `PYTHONDONTWRITEBYTECODE=1 python3 -B -m max2025_menu_prototype.tools.baseline verify --manifest <external manifest>` | **293 protected entries unchanged** |

Archive member inventory, both ZIP hashes, extraction import, and the remote
push audit stay in the external maintainer handoff rather than in this file.

**Still NOT RUN for these fixes:** MAXScript parsing/execution in Max, Max's
Python bridge, real Windows Git transport and the empty-directory clone path
against live GitHub, the Listener tip rendering in Max, live clone/update from
Max, and Qt queued delivery inside Max. The Windows and POSIX process paths are
simulated in tests only.

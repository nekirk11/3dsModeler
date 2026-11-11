# Plan: first 3ds Max 2025 test of the Git delivery

**Status: NOT RUN.** This is an operator execution plan, not a result ledger and
not a claim that any native behavior works. Every result field below starts as
**NOT RUN**. Update a field only from a real Windows/Max session, and record
the exact evidence beside it.

This plan covers the **Git delivery** on branch `dev` into
`C:\dev\3dsModeler`. The ZIP fallback remains a separate delivery layout; see
[`TOMORROW-MAX2025.md`](TOMORROW-MAX2025.md) for the short ZIP checklist and
[`MAX2025-VALIDATION.md`](MAX2025-VALIDATION.md) for the API and runtime
matrices.

## 1. Frozen names this plan assumes

The launcher is delivered in this repository. Treat these names as fixed;
compare the delivered files against this table before running geometry tests.
If a delivered file disagrees, stop and report the drift.

| Item | Fixed value |
| --- | --- |
| Public repository | `nekirk11/3dsModeler` |
| HTTPS remote | `https://github.com/nekirk11/3dsModeler.git` |
| Update branch | `dev` |
| Windows checkout target | `C:\dev\3dsModeler` |
| Python package alias | `max2025_menu_prototype` |
| Real package initializer | `C:\dev\3dsModeler\__init__.py` |
| Launch files | `tools/max_pull_and_load.ms`, `tools/max_pull_and_load.py` |
| Primary UI call | `max2025_menu_prototype.max_host.bootstrap.show_tools()` |
| Cleanup and diagnostics | `bootstrap.uninstall()`, `bootstrap.report_diagnostics()` |

The companion script exposes the frozen launcher surface
`PACKAGE_NAME`, `REPO_URL`, `BRANCH`, `DEFAULT_CHECKOUT`, `LauncherError`,
`start(*, checkout=DEFAULT_CHECKOUT)` and
`load_checkout_package(checkout, head_sha)`. `start()` is asynchronous; it
returns promptly and reports progress through the Listener. Do not call it from
a worker thread, and do not create a second Qt application.

## 2. Preconditions

Record each item before the first launch.

| Precondition | Requirement |
| --- | --- |
| Operating system | 64-bit Windows 10 or Windows 11 |
| 3ds Max | 2025 or 2025.1, fresh session, no production scene |
| Python inside Max | Max's bundled Python 3.11; do not install or replace it |
| Qt inside Max | Max's bundled PySide6 6.5; do not add another Qt |
| Git | `git` on `PATH`; version recorded; no administrator shell required |
| Network | Public HTTPS access to the remote initially; DNS/TLS/proxy known |
| Script | Locally saved `.ms` obtained from the published `dev` branch and inspected |
| Geometry | Disposable, manually created Editable Poly only |
| Scene history | Small baseline history is allowed; record it before editing |

Explicitly out of scope for this test:

- no `pip` install, virtual environment, or replacement Python/Qt;
- no administrator shell, service, registry change, startup script, or permanent
  hotkey;
- no proprietary archive, plugin, or host replacement;
- no automatic remote download or auto-executed script;
- no conversion, repair, or adapter reset used to make a step pass.

The launcher itself must not modify startup state. It may create only the
checkout, Git metadata, and its owned runtime UI.

## 3. Delivery flows

Run the flows in order. Flow A establishes the checkout; Flow B proves the
same-commit path; Flow C proves the restart guard. Do not skip Flow B by
restarting Max between every launch.

### Flow A — first use, no checkout

1. Obtain `tools/max_pull_and_load.ms` from the published `dev` branch. A raw
   `dev` URL is acceptable for a manual download, for example
   `https://github.com/nekirk11/3dsModeler/raw/dev/tools/max_pull_and_load.ms`.
   Treat that as untrusted input until the source has been reviewed. It is not
   an auto-executed remote download.
2. Save the `.ms` locally, outside the future checkout (for example
   `C:\dev\max_pull_and_load.ms`). Do not save it inside `C:\dev\3dsModeler`.
   Leave `C:\dev\3dsModeler` empty or absent so the launcher can clone into it.
   Do not rely on the script's current directory or on a sibling `.py` file.
3. In Max 2025, choose **Scripting > Run Script...** and select the saved
   `.ms`.
4. Watch the Listener for finite progress categories: prerequisite check,
   clone start, clone completion, checkout validation, package load, and panel
   result. A hang with no deadline is a failure.
5. Confirm the clone created `C:\dev\3dsModeler` with `__init__.py`, `core/`,
   `max_host/`, `qt_ui/`, and `tools/max_pull_and_load.py` at that root. The
   Git top level must be the checkout root, not its parent.
6. Confirm the local branch is `dev` and the full HEAD is a 40-character
   hexadecimal commit:

   ```text
   git -C C:\dev\3dsModeler rev-parse --abbrev-ref HEAD
   git -C C:\dev\3dsModeler rev-parse HEAD
   ```

7. Confirm the module actually loaded from the checkout, not from an older ZIP
   copy or a parent directory. Record both paths:

   ```text
   max2025_menu_prototype.__file__   -> C:\dev\3dsModeler\__init__.py
   bootstrap.__file__                -> C:\dev\3dsModeler\max_host\bootstrap.py
   ```

8. Confirm exactly one tools panel appears and no menu, diagnostics report, or
   uninstall was called automatically.

If the checkout directory already exists as a non-repository directory, a
repository on another branch, a detached HEAD, or a checkout with a different
origin, stop. Do not delete it, move it, or initialize Git inside it. Report the
collision for manual repair. An existing **empty** `C:\dev\3dsModeler` is not a
collision: the launcher clones directly into it. A non-empty directory without
`.git` stops with a finite message that names a bounded sample of entries; move
the saved `.ms` outside that directory and leave the directory empty or absent,
then retry.

Diagnostics are Python, not MAXScript. Switch the Listener to the Python tab, or
in the MAXScript tab run:

```text
python.Execute "from max2025_menu_prototype.max_host import bootstrap; print(bootstrap.report_diagnostics())"
```

### Flow B — subsequent launch at the same commit

1. Leave the panel open in the same Max session.
2. Run the saved `.ms` again through **Scripting > Run Script...**.
3. The launcher may fetch `origin/dev`. If the tip still equals the loaded
   commit, it must reuse the already-loaded package, raise the existing panel,
   and not uninstall or recreate the adapter.
4. Record that no restart message appeared, the commit did not change, and the
   panel is the same instance.
5. Do not expect hot reload. External edits or pulls during a live session are
   unsupported.

### Flow C — updated commit, restart required

1. Have the maintainer advance `dev` while Max is closed, or use a disposable
   checkout for the drill. Do not hand-edit tracked files.
2. Start Max and run the saved `.ms`.
3. If the launcher detects an advanced commit, it must retire the trusted old
   UI, retain a restart-required state, and print exactly:

   ```text
   Checkout updated; restart 3ds Max before loading this commit.
   ```

4. Confirm no new package code was imported and no updated panel appeared in
   that session.
5. Restart Max fully. Run the saved `.ms` again. Confirm the new full SHA loads
   and the panel is created once from `C:\dev\3dsModeler`.
6. If the update fails after the old UI was retired, the restart requirement
   still stands. Do not resurrect the old panel by reloading modules.

## 4. Evidence fields

Every field begins as **NOT RUN** or "record at run". Use these result words
consistently:

| Word | Meaning |
| --- | --- |
| **NOT RUN** | No real-host attempt has been made |
| **PASSED** | Observed in the recorded Max session and supported by evidence |
| **FAILED** | Observed behavior contradicted the expected behavior |
| **BLOCKED** | Could not run because a documented prerequisite failed |
| **SKIPPED** | Deliberately not attempted, with a reason |

### 4.1 Run identity

| Field | Value |
| --- | --- |
| Run ID | NOT RUN |
| Date and local time | NOT RUN |
| Operator | NOT RUN |
| Windows build | NOT RUN |
| Max build (2025 or 2025.1) | NOT RUN |
| Bundled Python version | NOT RUN |
| PySide6 / Qt version | NOT RUN |
| Delivery mode (Git or ZIP) | NOT RUN |
| Commit SHA (Git) | NOT RUN |
| ZIP SHA-256 (if ZIP) | NOT RUN |
| Checkout root / package root | NOT RUN |
| `bootstrap.__file__` | NOT RUN |
| Local report location | NOT RUN — keep private, do not commit |
| Launcher transcript | NOT RUN |

### 4.2 Gate results

| Gate | Expected | Result |
| --- | --- | --- |
| G0 Git transport | Git found; clone/fetch completes within the documented deadlines | **NOT RUN** |
| G1 Import and alias | Package and bootstrap load from `C:\dev\3dsModeler`; full SHA recorded | **NOT RUN** |
| G2 Panel and diagnostics | One panel; read-only diagnostics create no controller or edit | **NOT RUN** |
| G3 P0 geometry | Extrude, inset, bevel, chamfer meet the P0 matrix | **NOT RUN** |
| G4 P0 interaction and Undo | Cancel is inert; one Undo/Redo per applied edit | **NOT RUN** |
| G5 Update drills | Dirty tree refuses; advanced commit requires restart | **NOT RUN** |
| G6 Stop gate | Any failure stops cleanly with no false success | **NOT RUN** |
| G7 ZIP fallback | Separate layout works only after a fresh Max if switching copies | **NOT RUN** |

### 4.3 Evidence boundaries

Do not promote one row into another.

| Evidence type | What it can establish | What it cannot establish |
| --- | --- | --- |
| Linux stdlib unit tests | Source contract and fake-runtime behavior | Max, Windows Git, MAXScript, viewport, or native host behavior |
| Python 3.11 AST/compile checks | Source compatibility with the target syntax | Python 3.11 execution inside Max |
| Windows Git transport test | Clone/fetch/pull mechanics on that machine | Package import, Qt threading, native modeling, or Undo |
| **Scripting > Run Script...** | The `.ms` was accepted and ran in Max | That any modeling action works |
| Real host behavior | The specific recorded operation and context | General parity, other builds, or unrecorded cases |

A source pass, a fake-runtime pass, a browser simulation, or a MAXScript parse
is never a native success claim.

## 5. Native test sequence

Use only a disposable scene. Prepare fixtures manually. The prototype must not
convert, repair, or create the target for you.

### Phase 0 — preflight

1. Confirm the preconditions in section 2.
2. Confirm the delivered files match the frozen paths and symbols.
3. Read [`MAX2025-VALIDATION.md`](MAX2025-VALIDATION.md) and
   [`NATIVE-MODELING.md`](NATIVE-MODELING.md) before touching geometry.
4. Open **Scripting > MAXScript Listener** so progress and errors are visible.
5. Record the baseline scene, history, and target.

### Phase 1 — delivery and load

Run Flow A. Then Flow B in the same session. Do not call `show_menu()`,
`report_diagnostics()`, or `uninstall()` as part of the first panel launch.

### Phase 2 — panel, levels, and diagnostics

1. Confirm one shared panel and one shared controller. Repeated
   `show_tools()` calls must raise the same panel, not create a second one.
2. Use the level buttons and confirm only `subObjectLevel` changes. Opening the
   panel, hovering, or refreshing must not change geometry, selection, or the
   modifier stack.
3. Run the default read-only diagnostic report only while no menu or parameter
   dialog is pending. Confirm it creates no controller, edits nothing, and makes
   no native-success claim.
4. Opt in to `include_details=True` only if needed, and treat the output as
   **LOCAL PRIVATE DETAILS** to be reviewed before sharing. Do not paste it into
   a public issue or commit.

Detailed lifecycle and ownership rules are in
[`INTERACTION.md`](INTERACTION.md) and
[`NATIVE-MODELING.md`](NATIVE-MODELING.md).

### Phase 3 — target preparation

1. In an empty scene, create one Box or Plane manually.
2. Convert it to Editable Poly through the Max UI, collapse the stack, and
   confirm zero modifiers.
3. Do not instance or reference the object. Do not use a primitive, a mesh, an
   Edit Poly modifier, or a multi-object sub-object selection.
4. Open the Modify panel and confirm the active object is the base object.
5. Choose one sub-object level and make the component selection required by the
   case. Level buttons may be used with zero components.
6. Record object name, level, component count, units, and a geometry signature
   before editing.

Unsupported targets must show a readable disabled reason and must never be
silently converted.

### Phase 4 — P0 geometry

Run the P0 cases from [`NATIVE-MODELING.md`](NATIVE-MODELING.md) in this order.
Use small safe amounts. Record actual geometry and settings, not only result
text.

| Order | Case | Required observation |
| --- | --- | --- |
| 1 | Polygon extrude, positive and negative | New geometry, correct type, one undo unit |
| 2 | Polygon inset | Inset actually occurs; amount greater than zero |
| 3 | Polygon bevel | Height/outline both honored; no false success |
| 4 | Edge chamfer | Expected topology; correct edge path |
| 5 | Vertex chamfer | Expected topology; correct vertex path |
| 6 | Grow and shrink | Selection changes only; no geometry edit |
| 7 | Edge loop and ring | Correct selection; arbitrary borders refuse |
| 8 | Selection clear | Selection empties; no geometry edit |
| 9 | Transform move/rotate/scale | Enters the documented Max mode; no completion claim |
| 10 | Frame selection | Frames the current selection in the active viewport |

The supported/disabled matrix, per-action native members, and citations are in
[`MAX2025-VALIDATION.md`](MAX2025-VALIDATION.md). P1/P2 cases are secondary and
must not be attempted before P0 passes.

### Phase 5 — P0 interaction, stale state, and Undo

| Check | Expected |
| --- | --- |
| Dialog **Cancel** | No geometry, settings, selection, or history change |
| Escape | Same as Cancel; event is consumed by the dialog |
| Window close | Same as Cancel; no commit |
| **Apply once** | Runs once; double-click or Enter does not apply twice |
| **Repeat** | Reopens fresh schema defaults; never replays previous amounts |
| Stale selection with equal count | Fresh snapshot blocks the commit |
| Changed level, target, stack, panel, time, transform, mode, or soft selection | Commit blocks with a revalidation message |
| One native Undo per applied edit | Geometry, selection, and settings restore |
| Redo | Restores the applied result |
| Pre-existing history | Remains intact before and after the edit |
| Panel and dialog lifetime | One shared panel; no orphan window; focus returns |
| `uninstall()` | Removes only prototype UI and pending input; committed geometry stays |

Do not stack edits on an unknown failed state. If one check fails, stop that
operation and run the stop gate.

### Phase 6 — secondary cases

Only after P0 passes, use the P1/P2 rows and the detailed matrix in
[`MAX2025-VALIDATION.md`](MAX2025-VALIDATION.md). These remain **NOT RUN** until
actually executed. A P1/P2 absence is reported as blocked or deferred, never as
success.

### Phase 7 — update drills

Run these on a disposable checkout or with maintainer coordination. None of
them authorizes a reset, stash, clean, branch switch, or force-push.

| Drill | Setup | Expected |
| --- | --- | --- |
| D1 dirty tree | Add one untracked file in the checkout | Launcher refuses; no changes, deletion, reset, or stash; the file remains |
| D2 advanced `dev` | Maintainer pushes a new commit | Old UI is retired; restart message appears; no new code loads |
| D3 missing network | Disconnect or block HTTPS | Finite timeout; actionable category; no indefinite hang |
| D4 authentication or access failure | Use a private copy or revoked credentials | Finite failure; no credential popup; no token in logs |
| D5 missing Git | Remove `git` from `PATH` for a disposable shell | Clear "Git not found" style failure; no partial launch |
| D6 ZIP fallback | Use the package-named ZIP layout | Separate layout; fresh Max session required when switching copies |

For D3 and D4, do not weaken TLS or embed a token to make the test pass. A
private GitHub repository may report "repository not found" to an
unauthenticated client; treat that as an access/authentication category, not as
proof that the repository does not exist.

### Phase 8 — teardown

1. Call `bootstrap.uninstall()` or close Max.
2. Confirm no startup script, registry entry, permanent hotkey, scene object, or
   modifier was installed.
3. Confirm the checkout and Git metadata are the only persistent files.
4. Restart Max and confirm no prototype UI returns by itself.

## 6. Stop conditions

Stop the current operation immediately when any of these occurs. Keep the
session open long enough to capture readable evidence, unless the host is
unstable.

| Trigger | Required action | Prohibited |
| --- | --- | --- |
| Mutation on Cancel, Escape, close, or preview | Stop; inspect scene and history | Blind Undo, retry, or conversion |
| Result claims success without a visible change | Stop; record as false success | Editing the report to match expectations |
| Rollback latch or topology-disabled state | Stop; run read-only diagnostics only if no dialog is busy; inspect scene/history | Adapter reset, reinitialize, or guard weakening |
| Host crash or freeze | Restart Max; record build and steps | Reusing the crashed session as evidence |
| Missing native member, marshalling failure, or unreadable return | Stop that action; record blocked reason | Guessing a private member, fallback, or script workaround |
| Broken Undo or Redo | Stop; do not continue the matrix | Manual geometry repair presented as a pass |
| Stale selection or target accepted | Stop; report a guard failure | Re-selecting to hide the defect |
| Foreign viewport tool cancelled by uninstall | Stop; report ownership failure | Exiting or resetting the foreign tool |

No automatic retry. No blind Undo. No conversion, adapter reset, or weakened
guard. Diagnostics are read-only and must not interact with a pending dialog.
Record failures as **FAILED** or **BLOCKED**, distinctly from **NOT RUN**.

## 7. Public-to-private operator notes

The repository starts public. A later visibility change is an owner action
performed outside Max, after reading GitHub's visibility-change consequences.
Making a repository private does not recall existing clones, forks, caches, or
search results. Never publish proprietary material expecting a later privacy
flip to erase it.

The repository [`README.md`](../README.md) is the single source for the exact
GitHub commands and maintainer credential setup. This plan deliberately does
not duplicate those commands, to avoid conflicting instructions. In summary:

- configure Windows Git credentials interactively **outside Max**, for example
  with Git Credential Manager, or `gh auth login` followed by
  `gh auth setup-git` if GitHub CLI is installed;
- fine-grained, read-only **Contents** access is sufficient for pulling;
- never place a token in the remote URL, script, Listener output, or a log;
- the launcher must fail promptly and must not prompt for credentials;
- GitHub CLI is optional on the Windows machine and is not a launcher
  dependency;
- after a privacy change, unauthenticated requests may say "repository not
  found"; recover credentials outside Max, then retry deliberately.

## 8. Handoff and record keeping

Report to the integration owner:

1. run ID, date, operator, Windows/Max/Python/Qt builds;
2. delivery mode, full commit SHA, checkout and bootstrap paths;
3. each gate result using the defined words;
4. every stop event with the exact step and visible symptom;
5. the location of the local report, kept outside the repository.

Do not edit [`VERIFICATION.md`](VERIFICATION.md); the integration owner owns
that ledger. Do not commit local reports, screenshots, scene files, or private
details. Keep every unexecuted native row as **NOT RUN** until a real-host
record supports it.

## 9. Related plans and matrices

- [`TOMORROW-MAX2025.md`](TOMORROW-MAX2025.md) — short ordered first-test
  checklist and ZIP layout.
- [`MAX2025-VALIDATION.md`](MAX2025-VALIDATION.md) — API evidence,
  supported/disabled matrix, and runtime checklist.
- [`NATIVE-MODELING.md`](NATIVE-MODELING.md) — frozen contracts, parameter
  schemas, and P0/P1/P2 acceptance cases.
- [`INTERACTION.md`](INTERACTION.md) — gesture table, panel workflow, and
  sandbox walkthroughs.
- [`plan-PolyPenPort.md`](plan-PolyPenPort.md) — future, independent
  polygon-drawing plan. It is not part of this delivery and must not be tested
  as if it already exists.

# Native modeling tools prototype (experimental)

An original, deliberately experimental prototype for a native 3ds Max 2025
modeling loop. The primary entry is the V2 **3dsModeler Spotlight palette**:
one search field over the 36-maneuver atlas, six context pills, cross-context
pivots, and fixed native caddy/command-mode dispatch. The browser sandbox
remains a secondary **simulation** and cannot control Max. No 3ds Max 2025
installation was available here, so every native runtime check in
[`docs/MAX2025-VALIDATION.md`](docs/MAX2025-VALIDATION.md) remains **NOT RUN**.
Nothing here is production-ready, host-validated, or a replacement for a
modeling toolset.

The source is published at `https://github.com/nekirk11/3dsModeler` on branch
`dev`. The preferred delivery is a Git checkout at `C:\dev\3dsModeler`; the
source-only ZIP remains a separate fallback layout.

V2 adds the host-free atlas/search/controller path beside the frozen V1
catalog/parameter controller. The V1 panel, popup, and one-shot parameter dialog
remain in the tree for legacy tests and the sandbox, but no V1 widget is
composed on a normal V2 launch. All native bindings still carry
`runtime_status="not_run"`; source/fake-runtime coverage is not a Max success
claim.

## V2 at a glance

- **Atlas and overlays.** `core/maneuvers.json` is bundled, validated JSON with
  exactly 36 stable maneuver IDs and non-executable reference metadata.
  `core/atlas.py` loads it without touching the network; an optional
  checkout-root `user_aliases.json` may add aliases/pins/hides only. A malformed
  or foreign overlay is ignored as a whole with one sanitized warning. The
  overlay is excluded from packaging and is never required.
- **Search-first workflow.** Open the palette, type across labels, IDs, Houdini
  Modeler/Blender/Maya/Max aliases, and user aliases. An exact/prefix/token match
  ranks first; active-context rows precede pivot rows; a single deterministic
  pivot row is shown with `[→ Border]`, `[→ Face]`, etc. `>` searches the three
  recovery commands only.
- **Six contexts, one seam.** Public contexts are `object`, `vertex`, `edge`,
  `border`, `face`, `element` (Max levels 0–5). V1's `polygon` is mapped only by
  the explicit legacy helpers; JSON never accepts `polygon`.
- **Safe target preparation.** Tier 1 converts one non-instanced primitive or
  mesh to Editable Poly under undo; Tier 2 steps down to an existing poly base
  while preserving a Symmetry/TurboSmooth stack and showing the end result;
  Tier 3 blocks instanced/shared bases, unreadable state, or non-poly + modifier
  targets. Any Edit Poly modifier blocks topology editing unless *Collapse to
  Poly* is explicitly confirmed.
- **Live caddies versus modes.** Selected components route to native
  `PopupDialog` caddies (Chamfer, Extrude, Bevel, Inset, ConnectEdges, Weld,
  Tessellate, MSmooth, and the bridge targets); zero components route to fixed
  command modes. A caddy/mode result is `launched`, never a geometry claim.
  Cancel leaves no caddy edit; the separate auto-prep conversion remains its own
  undo record.
- **Clean Edge Dissolve.** `dissolve` in Edge context removes selected edges,
  then removes only recorded inline valence-2 endpoint candidates in the same
  undo chunk, restoring context/selection and rolling back on identity
  ambiguity.
- **Persistent macros, local hotkeys.** The launcher writes the owned
  `3dsModeler.mcr` and registers its `"3dsModeler"` category in the live
  session. Hotkeys are assigned manually in **Customize > Hotkey Editor**.
  Hotkey execution is a synchronous local-checkout load with no network.

## First Max 2025 test: Git delivery (preferred)

Requirements: 64-bit Windows, 3ds Max 2025/2025.1 with its bundled Python (3.11)
and PySide6 6.5, and `git` on `PATH`. Public HTTPS access is enough for the
first clone. Do not install another Python or Qt into Max, and do not add a
startup script or registry entry. The launcher intentionally installs the
persistent `3dsModeler` MacroScripts; it never assigns or overwrites a hotkey
automatically.

1. Obtain [`tools/max_pull_and_load.ms`](tools/max_pull_and_load.ms) from the
   published `dev` branch and review it before running. In a fresh Max 2025
   session with a disposable scene, choose **Scripting > Run Script...** and
   select the saved `.ms`.
2. The script checks Max's main thread and existing `QApplication`, then either
   clones `https://github.com/nekirk11/3dsModeler.git` (branch `dev`) to
   `C:\dev\3dsModeler` or validates the existing checkout. Git runs in a worker;
   the Listener shows finite progress lines and the Spotlight palette opens on
   the main thread when the import succeeds. Save the `.ms` **outside**
   `C:\dev\3dsModeler` (for example `C:\dev\max_pull_and_load.ms`) and leave
   that directory empty or absent; a non-empty directory that is not a Git
   checkout stops with a finite repair message.
3. The installer writes/verifies `3dsModeler.mcr` and, when `pymxs` is
   available, loads it immediately so the `"3dsModeler"` category appears in
   **Customize > Hotkey Editor** without a restart. Assign
   `ThreeDsModeler_Search` (and any `ThreeDsModeler_Context*` /
   `...Convert` variants you want) manually. A restart is not required for
   the category, but re-run the `.ms` after an update to refresh definitions.
4. Record the printed checkout root and full 40-character commit SHA in
   [`docs/TOMORROW-MAX2025.md`](docs/TOMORROW-MAX2025.md). Diagnostics are
   Python: switch the Listener to the Python tab, or in the MAXScript tab run
   `python.Execute "from max2025_menu_prototype.max_host import bootstrap; print(bootstrap.report_diagnostics())"`.
   Do not run updates, diagnostics, or `uninstall()` automatically after the
   first palette launch.

The Python companion `tools/max_pull_and_load.py` owns the Git update,
import-alias, commit-guard, and restart policy. The standalone `.ms` works even
when `C:\dev\3dsModeler` and the companion do not yet exist, because it embeds a
small clone-only bootstrap and then runs the companion from the checkout.

### Updating an existing checkout

Running the same `.ms` again fetches `origin/dev` and reuses the loaded panel
when the commit is unchanged. If the commit advanced, the launcher retires the
trusted old UI, refuses further imports, and stops with “Checkout updated;
restart 3ds Max before loading this commit.” It does not hot-reload and it never
imports changed files just to clean up. A dirty checkout, wrong origin or
branch, divergent history, timeout, or authentication/access failure stops with
a finite message. Repair the checkout outside Max and start a fresh session; the
launcher never prompts for credentials and never resets or cleans your tree.

### Layout rules and limits

- The checkout directory is literally `3dsModeler`, while the Python package is
  `max2025_menu_prototype`. The launcher installs the checkout root by alias.
  Do not rename the package, add a nested package directory, or add
  `C:\dev\3dsModeler` to `sys.path`.
- Do not run concurrent Max processes against one checkout, and do not edit or
  pull external changes while the prototype is loaded.
- A palette opening is not a native geometry success. Every Max runtime check
  remains **NOT RUN** until recorded from a real session.

## ZIP fallback (separate layout)

The ZIP is a source-only fallback and is **not** the Git checkout layout. Its
one top-level directory is still `max2025_menu_prototype/`, and switching between
the ZIP copy and the Git checkout requires a fresh Max session.

Build a deterministic ZIP from the repository root (maintainer shell, outside
Max):

```sh
python3 tools/portable.py \
  --output ../max2025-delivery/max2025_menu_prototype.zip
```

The builder refuses to overwrite an existing archive or sidecar, includes only
prototype `.py`/`.json`/`.md` sources plus the four original sandbox static
assets and the one exact `tools/max_pull_and_load.ms`, and writes
`max2025_menu_prototype.zip`, `.zip.sha256`, and `.zip.manifest.json`. Verify the
SHA-256 before extracting.

Extract the ZIP to `C:\MaxTools\TestReadiness\` so the required layout is
exactly:

```text
C:\MaxTools\TestReadiness\
└── max2025_menu_prototype\
    └── __init__.py
```

Add the **parent** `C:\MaxTools\TestReadiness` to `sys.path`, never the package
directory itself. Run this copy-paste block on Max's main thread. It checks the
expected path, refuses a stale already-loaded copy instead of hot-reloading,
and prints the actual package/bootstrap files:

```python
import os
import sys
from pathlib import Path

PACKAGE_PARENT = Path(r"C:\MaxTools\TestReadiness")
PACKAGE_DIR = PACKAGE_PARENT / "max2025_menu_prototype"
EXPECTED_INIT = PACKAGE_DIR / "__init__.py"


def _same_path(left, right):
    try:
        return os.path.normcase(str(Path(left).resolve())) == os.path.normcase(
            str(Path(right).resolve())
        )
    except OSError:
        return False


if not EXPECTED_INIT.is_file():
    raise RuntimeError(
        "Expected package not found at %s. Extract the ZIP to %s and retry."
        % (EXPECTED_INIT, PACKAGE_PARENT)
    )

existing = sys.modules.get("max2025_menu_prototype")
if existing is not None:
    existing_file = getattr(existing, "__file__", None)
    if not _same_path(existing_file or "", EXPECTED_INIT):
        loaded_from = str(Path(existing_file).resolve()) if existing_file else "unknown"
        raise RuntimeError(
            "A different max2025_menu_prototype is already loaded from %s. "
            "Start a fresh Max session; do not hot-reload or remove modules."
            % loaded_from
        )

parent_text = str(PACKAGE_PARENT.resolve())
if not any(entry and _same_path(entry, PACKAGE_PARENT) for entry in sys.path):
    sys.path.insert(0, parent_text)

import max2025_menu_prototype
from max2025_menu_prototype.max_host import bootstrap

loaded_init = Path(max2025_menu_prototype.__file__).resolve()
if not _same_path(loaded_init, EXPECTED_INIT):
    raise RuntimeError(
        "Stale import: max2025_menu_prototype resolved to %s, expected %s. "
        "Start a fresh Max session." % (loaded_init, EXPECTED_INIT)
    )

print("max2025_menu_prototype:", loaded_init)
print("bootstrap:", Path(bootstrap.__file__).resolve())
bootstrap.show_palette()  # primary V2 entry: one Spotlight palette
```

The palette stays alive through bootstrap's existing references. Optional
diagnostics and cleanup are **separate** calls, not one consecutive script:

```python
# print(bootstrap.report_diagnostics())  # read-only default report
# bootstrap.uninstall()                 # remove prototype UI; not committed geometry
```

### Read-only diagnostics

`bootstrap.report_diagnostics()` returns a local, read-only report; it does not
edit geometry, clear a latch, create a controller, or probe with a dialog open.
The default omits node/scene/user names, paths, handles, component data,
parameter values, and arbitrary exception text. Only if needed, opt in to
bounded private details and review them before sharing:

```python
print(bootstrap.report_diagnostics(include_details=True))
# LOCAL PRIVATE DETAILS — review before sharing
```

On rollback lockout, **stop**: inspect the scene/history. Do not blindly Undo,
retry, or reinitialize to bypass the guard. Native operation validation remains
**NOT RUN**; a report is not proof that an operation works. See
[`docs/TOMORROW-MAX2025.md`](docs/TOMORROW-MAX2025.md) for the ordered first-test
checklist.

## Development from a source checkout

Requirements: 3ds Max 2025/2025.1 with its bundled Python (3.11.4 or 3.11.8)
and PySide6 6.5.3. Do not install another Qt into Max. Keep the prototype
package tree intact. The published checkout directory is named `3dsModeler`, so
a shell session must install the package alias explicitly instead of relying on
the directory name.

From the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -B - <<'PY'
import importlib.util
import sys
from pathlib import Path

root = Path.cwd()
spec = importlib.util.spec_from_file_location(
    "max2025_menu_prototype",
    root / "__init__.py",
    submodule_search_locations=[str(root)],
)
package = importlib.util.module_from_spec(spec)
sys.modules["max2025_menu_prototype"] = package
spec.loader.exec_module(package)

from max2025_menu_prototype.max_host import bootstrap

print("max2025_menu_prototype:", package.__file__)
print("bootstrap:", bootstrap.__file__)
bootstrap.show_palette()    # primary: V2 Spotlight palette
PY
```

Call these helpers on Max's main thread; the prototype creates no worker threads
for UI. Optional diagnostics and cleanup are **separate** calls:

```python
print(bootstrap.report_diagnostics())  # read-only default report
bootstrap.uninstall()                  # remove prototype UI; not committed geometry
```

- `show_palette()` composes one `PaletteController` + `ManeuverHostAdapter` +
  `SearchPalette` and raises/refocuses the single existing palette.
- `show_tools()`, `show_menu()`, and `install_launcher()` forward to
  `show_palette()` for native compatibility; they no longer compose V1 widgets.
- `switch_context(context, convert_selection=False)` works without opening the
  palette, for the persistent context macros. `polygon` is rejected in the V2
  API; use `face`.
- `uninstall()` closes pending V2 UI, invalidates timers/signals, and drops
  strong UI references. It does not delete `3dsModeler.mcr`, remove hotkeys,
  clear the rollback lockout, undo edits, or exit a foreign/native tool.
- Persistent macros and manually assigned hotkeys are intentionally out-of-memory
  Max configuration; see **Clean removal** below.

## Native V2 workflow

1. Select one non-instanced node and choose the working context. Primitives and
   Editable Meshes are converted to Editable Poly under one undo record when
   they are otherwise eligible; an existing poly with Symmetry/TurboSmooth is
   edited at its base while the stack stays intact and the end result is shown.
   Instanced/shared bases, non-poly bases with modifiers, unreadable state, and
   any Edit Poly modifier block topology editing; the block explains whether
   *Convert to Poly* or explicit *Collapse to Poly* is the intended remedy.
2. Open the palette with `bootstrap.show_palette()`, the `ThreeDsModeler_Search`
   macro, or an assigned hotkey. The real search line owns focus and shows a
   caret; typing `w/e/r/x/t/c`, digits `1`–`5`, or Space stays text.
3. A plain pill click switches context; Ctrl+Click converts the selection with
   `EditablePoly.ConvertSelection`. Tab/Shift+Tab and Alt+0..5 also switch.
   Ordinary switches never convert geometry or selection.
4. Search by label, ID, or cross-DCC/user alias. Enter dispatches only the
   highlighted, currently valid row. A pivot row such as `[→ Border]` switches
   first; `fill` from selected open edges is the one deliberate open-edge
   conversion exception.
5. Selected components route to a live native caddy (or a fixed immediate
   handler); zero components route to a matching command mode. Count routing
   uses the fresh destination selection, never the object-node count.
6. Results distinguish **LAUNCHED** (caddy/mode entry only), **APPLIED**,
   **BLOCKED**, **ERROR**, **CANCELLED**, and **NOOP**/SIMULATED. A caddy is
   interactive: its preview/OK/Cancel and later undo are native. The separate
   auto-prep conversion completed *before* the caddy and remains its own undo
   record; no Qt loop or undo hold is kept across caddy lifetime.
7. `dissolve` in Edge context removes the selected edges and, in the same undo
   chunk, only the recorded inline valence-2 endpoints. Corners, branches,
   non-candidate vertices, coincident slots, and unsupported nonmanifold cases
   are preserved; ambiguous identity rolls the whole chunk back.
8. `collapse_to_poly` is the only stack-collapse action and requires an inline
   destructive confirmation. `> Diagnostics` is read-only and works with no
   selection; `> Unload UI` disposes only in-memory UI.

V1's tools panel, latched popup, and typed one-shot parameter dialog are
historical. Their frozen catalog/parameter contracts remain in the tree and are
exercised by legacy tests and the browser sandbox, but a normal V2 launch never
composes them and no blind numeric default can be dispatched from the palette.

The atlas defines all 36 maneuver IDs. `max_host/maneuver_dispatch.py` has a
fixed reviewed handler for every descriptor `(kind, target)` pair or an explicit
blocked reason. A row or a launched caddy still proves nothing about native
success; the per-route matrix is in
[`docs/MAX2025-VALIDATION.md`](docs/MAX2025-VALIDATION.md). All native rows
remain **NOT RUN** in Max.

## What is real, simulated, or unvalidated

| Part | Status here |
| --- | --- |
| Python V2 atlas/search/controller/simulator (`core/`) | Real stdlib code, exercised by stdlib tests; no Qt/Max imports |
| Spotlight palette (`qt_ui/palette.py`, `palette_model.py`) | Implemented renderer; fake-Qt/offscreen tests only, **NOT RUN in Windows Max** |
| V2 host adapter/preparation/dispatch (`max_host/`) | Fixed registry plus 3-tier preparation, caddies/modes, clean dissolve; fake-runtime tests only, **NOT RUN in Max** |
| V1 catalog/controller/panel/popup/parameter dialog | Frozen legacy path; retained for legacy tests and the sandbox, not a normal V2 launch |
| Persistent MacroScripts (`3dsModeler.mcr`) | Deterministic generator, ownership/conflict guard, `fileIn` activation; **NOT RUN in Max** |
| Browser sandbox | Real stdlib server with a **simulated** host; cannot control Max |
| Git launcher (`tools/max_pull_and_load.ms`, `tools/max_pull_and_load.py`) | Source-complete and stdlib/alias tested; **NOT RUN** in Max or against live GitHub from Max |
| Native runtime validation | **NOT RUN — no 3ds Max 2025 installation** |

## Browser sandbox (secondary simulation)

From the repository root, using the same package-alias setup:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -B - <<'PY'
import importlib.util
import runpy
import sys
from pathlib import Path

root = Path.cwd()
spec = importlib.util.spec_from_file_location(
    "max2025_menu_prototype",
    root / "__init__.py",
    submodule_search_locations=[str(root)],
)
package = importlib.util.module_from_spec(spec)
sys.modules["max2025_menu_prototype"] = package
spec.loader.exec_module(package)
runpy.run_module("max2025_menu_prototype.sandbox", run_name="__main__")
PY
```

The server binds loopback only (`127.0.0.1`), chooses a free port unless you pass
`--port`, prints a line like `Open: http://127.0.0.1:<port>/`, and does **not**
open a browser. Open that URL yourself. Stop it with Ctrl+C.

- No third-party Python packages are required for the sandbox.
- The sandbox uses the same Python controller, profiles, ordered rows, and
  eligibility decisions as the native renderers. It is a thin renderer, not a
  second state machine.
- Every host result in the sandbox is **SIMULATED**. Simulation is never evidence
  that an operation works in Max.
- Parameterized modeling rows are disabled in the browser with
  `Native parameter dialog is unavailable in this renderer.` There is no browser
  parameter implementation and no browser-to-Max route.
- The sandbox does not import the Max adapter and cannot control a native host.

First things to try: click the simulated viewport, press **Space** to open a held
menu, release quickly to latch it (nothing launches), hold and move onto a row,
release there, or hold and return to the neutral zone. Use the context and
scenario controls to visit all six contexts, including empty selections and
unsupported primitive/Edit Poly scenarios. See
[`docs/INTERACTION.md`](docs/INTERACTION.md) for the gesture table, native panel
workflow, and sandbox walkthroughs.

## Optional Qt desktop simulation

Only on a machine with a display and compatible PySide6. From the repository
root, use the same alias block, replacing the final `runpy` call with
`runpy.run_module("max2025_menu_prototype.qt_ui", run_name="__main__")`.

This proves the optional popup factory against the simulated host. It is not a
Max focus, DPI, viewport-input, panel, or native-binding test. The popup is
opened with **latched** entry only: it has no trigger-release input, so
hold-release commit is not claimed here or in Max. If PySide6 is unavailable,
skip it; do not install an old PySide6 into an unrelated Python.

## Profiles

The default profile is versioned JSON shipped with the core. In the sandbox you
can:

1. choose a context;
2. reorder actions and hide/show them;
3. reset to the default;
4. adjust the tap threshold (100–600 ms) and neutral radius (4–48 logical px);
5. export the profile to JSON, or import a previously exported version.

Edits are in memory by default. Persistence happens only through an explicit
export/save, never on hover or window leave. Imports validate the schema version,
all six contexts, known action IDs, uniqueness, and value bounds. A malformed
import leaves the active profile untouched and shows an error. Profiles cannot
contain scripts, paths, callables, or arbitrary action discovery.

## Known sandbox limitations

These browser-side issues are documented but intentionally **not fixed in cycle
1**:

- Focus loss during a pending open request can miss cancellation.
- Queued browser events can bind to a later session instead of their origin.
- A Space-held menu does not arm rows from pointer movement.
- An outside click can also activate the underlying control.
- Gesture timestamps above 10,000,000 ms are rejected.
- Copy/Download may reuse stale export textarea content after later edits.

The shared controller and Max adapter are unaffected by these renderer issues.

## Tests

Run the combined suite from the repository root. The alias block makes the
checkout directory itself the `max2025_menu_prototype` package, so the commands
work for both the original package-named directory and a published checkout
named `3dsModeler`:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -B - <<'PY'
import importlib.util
import sys
import unittest
from pathlib import Path

root = Path.cwd()
spec = importlib.util.spec_from_file_location(
    "max2025_menu_prototype",
    root / "__init__.py",
    submodule_search_locations=[str(root)],
)
package = importlib.util.module_from_spec(spec)
sys.modules["max2025_menu_prototype"] = package
spec.loader.exec_module(package)
suite = unittest.defaultTestLoader.discover(str(root / "tests"), pattern="test_*.py")
raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
PY
node --check sandbox/static/app.js
python3 tools/portable.py --help
```

The Max tests use a fake runtime and are contract/safety tests only. They do not
prove anything about Autodesk's host. The portable tests cover the deterministic
ZIP allowlist, sidecar hashes, `core/maneuvers.json` inclusion, overlay exclusion
without reading its bytes, extraction, and host-free import. `test_macros.py`
covers the generated `.ms`, all 13 macro names/category, `.mcr` ownership and
`fileIn` activation, and the network-free local hotkey path. `test_v2_security.py`
is the AST/call-spy gate against data-chosen execution. `test_v2_checkout.py`
runs the whole suite from a checkout literally named `3dsModeler` (with a child
sentinel to avoid recursion). They are source tests, not a Max or Windows
delivery test. The watcher/launcher tests exercise fake process/bridge seams
only; MAXScript execution and live GitHub transport remain **NOT RUN**. Final
integration counts and skip reasons belong in
[`docs/VERIFICATION.md`](docs/VERIFICATION.md); do not run the aggregate suite
against concurrently edited paths.

## Clean removal

```python
bootstrap.uninstall()
```

Unload UI first; it disposes only in-memory widgets/hooks and leaves the scene,
active native tools, the rollback lockout, and the persistent macros intact.

Then delete the `C:\dev\3dsModeler` checkout (or the extracted
`max2025_menu_prototype/` directory for the ZIP fallback). The launcher creates
no startup script or registry entry, but it **does** create one owned file,
`3dsModeler.mcr`, in Max's user MacroScripts directory. It does not delete that
file, remove hotkey assignments, or clear loaded-SHA/restart state
automatically. To remove macros and hotkeys, do it explicitly and separately:

1. Close Max (or at least unload the UI), then delete `3dsModeler.mcr` from
   Max's user MacroScripts folder (`getDir #userMacros`).
2. In **Customize > Hotkey Editor**, remove only the `"3dsModeler"` category
   assignments you created. No assignment was made for you and no W/E/R/Space
   binding was overwritten.
3. Restart Max and confirm the category is gone.

Committed native modeling edits remain normal Max undoable geometry; uninstall
removes prototype UI and pending input, **not** already committed scene edits.
It does not cancel foreign viewport tools. The prototype does not replace
right-click.

## Maintainers: GitHub publication and private conversion

These commands are documentation for the repository owner; this delivery does
not run the visibility change. Use the GitHub CLI.

Initial public creation (already performed for this publication) and branch
pushes:

```sh
gh repo create nekirk11/3dsModeler --public --source=. --remote=origin
git push -u origin main
git push -u origin dev
gh repo edit nekirk11/3dsModeler --default-branch main
```

Read GitHub's visibility-change consequences before converting the repository
to private, then run:

```sh
gh repo edit nekirk11/3dsModeler --visibility private --accept-visibility-change-consequences
gh repo view nekirk11/3dsModeler --json visibility
```

Making a repository private does not recall existing public clones, forks, or
caches. Never publish proprietary material expecting a later visibility change
to erase it. The repository URL, package root, and branch names stay the same.

After a private conversion, unauthenticated Git requests can fail as if the
repository did not exist. Configure noninteractive Windows Git credentials
**outside Max** first, for example Git Credential Manager or `gh auth login`
followed by `gh auth setup-git`. Fine-grained read-only `Contents` access is
enough for pulling. The launcher then fails promptly with an
authentication/access category instead of prompting; recover credentials
outside Max. GitHub CLI on the Windows machine is optional and is not a launcher
dependency.

## More detail

- [`docs/TOMORROW-MAX2025.md`](docs/TOMORROW-MAX2025.md) — short ordered first Max test and stop gate.
- [`docs/INTERACTION.md`](docs/INTERACTION.md) — gestures, native panel workflow, sandbox walkthroughs.
- [`docs/NATIVE-MODELING.md`](docs/NATIVE-MODELING.md) — W1 frozen contracts, schemas, consumer handoff, manual acceptance cases.
- [`docs/VERIFICATION.md`](docs/VERIFICATION.md) — integration commands/results and the sandbox matrix.
- [`docs/MAX2025-VALIDATION.md`](docs/MAX2025-VALIDATION.md) — API evidence, supported/disabled matrix, NOT RUN runtime checklist.
- [`docs/plan-FirstMaxTest.md`](docs/plan-FirstMaxTest.md) — operator plan for the Git delivery's clone, update, and restart flows.
- [`docs/plan-PolyPenPort.md`](docs/plan-PolyPenPort.md) — proposed future polygon-drawing design; no polygon tool is implemented.
- [`docs/LEARNING-ARCHITECTURE.md`](docs/LEARNING-ARCHITECTURE.md) — source-verified architecture map and guided read order.
- [`docs/LEARNING-HOUDINI-TO-3DSMAX.md`](docs/LEARNING-HOUDINI-TO-3DSMAX.md) — conceptual Houdini-to-Max comparison from public documentation.

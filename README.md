# Native modeling tools prototype (experimental)

An original, deliberately experimental prototype for a native 3ds Max 2025
modeling loop. The primary entry is the Max-parented tools panel; the browser
sandbox is a secondary **simulation** and cannot control Max. No 3ds Max 2025
installation was available here, so every native runtime check in
[`docs/MAX2025-VALIDATION.md`](docs/MAX2025-VALIDATION.md) remains **NOT RUN**.
Nothing here is production-ready, host-validated, or a replacement for a
modeling toolset.

The source is published at `https://github.com/nekirk11/3dsModeler` on branch
`dev`. The preferred delivery is a Git checkout at `C:\dev\3dsModeler`; the
source-only ZIP remains a separate fallback layout.

W1 froze the catalog, parameter schemas, result statuses, and controller
lifecycle. W3 implemented the Qt tools panel, one-shot parameter dialog, and
bootstrap composition. W2 implemented the native adapter/operation mappings for
the catalog's P0/P1/P2 IDs, including mode ownership and undo transactions. All
bindings still carry `runtime_status="not_run"`; source/fake-runtime coverage is
not a Max success claim.

## First Max 2025 test: Git delivery (preferred)

Requirements: 64-bit Windows, 3ds Max 2025/2025.1 with its bundled Python (3.11)
and PySide6 6.5, and `git` on `PATH`. Public HTTPS access is enough for the
first clone. Do not install another Python or Qt into Max, and do not add a
startup script, registry entry, or permanent hotkey.

1. Obtain [`tools/max_pull_and_load.ms`](tools/max_pull_and_load.ms) from the
   published `dev` branch and review it before running. In a fresh Max 2025
   session with a disposable scene, choose **Scripting > Run Script...** and
   select the saved `.ms`.
2. The script checks Max's main thread and existing `QApplication`, then either
   clones `https://github.com/nekirk11/3dsModeler.git` (branch `dev`) to
   `C:\dev\3dsModeler` or validates the existing checkout. Git runs in a worker;
   the Listener shows finite progress lines and the tools panel opens on the
   main thread when the import succeeds. Save the `.ms` **outside**
   `C:\dev\3dsModeler` (for example `C:\dev\max_pull_and_load.ms`) and leave
   that directory empty or absent; a non-empty directory that is not a Git
   checkout stops with a finite repair message.
3. Record the printed checkout root and full 40-character commit SHA in
   [`docs/TOMORROW-MAX2025.md`](docs/TOMORROW-MAX2025.md). Diagnostics are
   Python: switch the Listener to the Python tab, or in the MAXScript tab run
   `python.Execute "from max2025_menu_prototype.max_host import bootstrap; print(bootstrap.report_diagnostics())"`.
   Do not run the optional menu, diagnostics, or `uninstall()` automatically
   after the first panel launch.

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
- A panel opening is not a native geometry success. Every Max runtime check
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
bootstrap.show_tools()  # first block: only the shared tools panel
```

The panel stays alive through bootstrap's existing references. Optional menu,
diagnostics, and cleanup are **separate** calls, not one consecutive script:

```python
# bootstrap.show_menu()                 # optional latched context menu
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
bootstrap.show_tools()      # primary: Max-parented native modeling panel
PY
```

Call these helpers on Max's main thread; the prototype creates no worker threads
for UI. Optional menu, diagnostics, and cleanup are **separate** calls:

```python
bootstrap.show_menu()                  # optional: shared latched context menu
print(bootstrap.report_diagnostics())  # read-only default report
bootstrap.uninstall()                  # remove prototype UI; not committed geometry
```

- `show_tools()` creates or raises the one shared panel. The panel, popup, and
  modal parameter dialog share one controller and one existing `QApplication`.
- `install_launcher()` is now a **click-to-open alias** for `show_tools()`. The
  old hold-to-open launcher is retired; this prototype makes no viewport
  hold-release, trigger-release, or shortcut claim.
- `show_menu()` opens the optional shared menu in latched mode. Its popup has
  no trigger-release input; explicit click/Enter commits.
- `uninstall()` closes pending prototype dialogs, clears the hook this bootstrap
  registered, disposes only prototype widgets, and invalidates pending input.
  It does not undo already committed scene edits. It never exits a user-owned
  viewport tool. Exact owned-mode cleanup uses only the public
  `MenuController.release_owned_native_mode()` → adapter `release_owned_mode()`
  seam; a foreign/stale mode is blocked and no private adapter field is read.
- Nothing is installed at startup; no registry change, permanent hotkey, global
  OS hook, or right-click replacement is made.

## Native workflow

1. Select exactly one valid, non-instanced **Editable Poly** base object. Open
   the Modify panel so `modPanel.getCurrentObject() == node.baseObject`; zero
   modifiers. Primitives, meshes, multiple objects, shared/instanced geometry,
   nonempty stacks, and active Edit Poly modifiers stay unsupported.
2. Run `bootstrap.show_tools()` on Max's main thread.
3. Use the panel's sub-object level buttons. They call the shared
   `MenuController.invoke("context.*")` path, change `subObjectLevel` only, and
   may be used with zero components. They never convert geometry, repair a
   selection, or change the stack.
4. Select vertices/edges/borders/polygons/elements in the native Max viewport.
5. Choose **Open context menu** or press the panel action path. Parameterized
   rows open one modal, typed, no-preview dialog showing the native target,
   context, component count, units, semantics, and destructive-confirmation
   text. **Apply once** collects values; **Cancel**, Escape, or window close
   returns nothing and makes no edit.
6. The controller takes a fresh snapshot and capability map after the dialog.
   A changed selection (even with equal count), level, target, stack, panel,
   time, transform, mode, or soft-selection blocks the commit. No scene repair
   is attempted.
7. Results distinguish **APPLIED ONCE**, **LAUNCHED (mode entry only)**,
   **BLOCKED**, **ERROR**, **CANCELLED**, and **NO CHANGES**. Max Undo remains
   native; `uninstall()` never undoes a committed edit.
8. **Refresh context** is read-only. Because the frozen controller has no
   separate refresh entry, the panel uses the public `open` + immediate `cancel`
   contract and skips while a menu or parameter request is pending. It never
   edits geometry or reaches through private host state.

The catalog now defines level, selection, modeling, and mode action IDs, and
`max_host/bindings.py` has a fixed native binding for every ID except the
core-owned Repeat row. A catalog row or panel button still proves nothing about
native success. The panel shows the host adapter's enabled/disabled decisions;
the detailed implemented/blocked/deferred matrix and per-binding citations are
in [`docs/MAX2025-VALIDATION.md`](docs/MAX2025-VALIDATION.md). All native rows
remain **NOT RUN** in Max.

## What is real, simulated, or unvalidated

| Part | Status here |
| --- | --- |
| Python core controller, catalog, profile validation, parameter schemas, simulator | Real shared code, exercised by stdlib tests |
| Max tools panel, parameter dialog, bootstrap composition (`qt_ui/`, `max_host/bootstrap.py`) | Implemented W3 renderers; scoped fake/fixture tests only, **NOT RUN in Max** |
| Max adapter and native operations (`max_host/`) | W2 implemented source mappings for catalog P0/P1/P2 IDs; fake-runtime/undo tests only, **NOT RUN in Max** |
| Browser sandbox | Real stdlib server and shared controller with a **simulated** host; cannot control Max |
| Optional Qt popup/desktop simulation | Shared-controller renderer using `SimulatedHost`; not a native-success demonstration |
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
ZIP allowlist, sidecar hashes, exclusion/security rules, extraction, and
host-free import. They are source tests, not a Max or Windows delivery test.
The watcher/launcher tests exercise fake process/bridge seams only; MAXScript
execution and live GitHub transport remain **NOT RUN**. Final integration counts
and skip reasons belong in [`docs/VERIFICATION.md`](docs/VERIFICATION.md); do not
run the aggregate suite against concurrently edited paths.

## Clean removal

```python
bootstrap.uninstall()
```

Then delete the `C:\dev\3dsModeler` checkout (or the extracted
`max2025_menu_prototype/` directory for the ZIP fallback). There are no startup
scripts, registry entries, permanent hotkeys, scene objects, or modifiers.
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

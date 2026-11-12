# Interaction contract and sandbox walkthroughs

> **Current native entry (V2): the 3dsModeler Spotlight palette.** The
> controller/panel/gesture contract below is V1, retained for legacy tests and
> the browser sandbox. It is not the normal native launch path. See the V2
> section first, then treat the rest of this page as historical reference.

## V2 Spotlight palette workflow

The V2 palette is a single compact, Max-parented popup/tool window driven by
`core/palette_controller.py` and rendered by `qt_ui/palette.py`. It uses Max's
existing `QApplication`; it never creates a second one. Opening, raising, or
refocusing reuses the same widget instance.

### Focus and typing

- A real `QLineEdit` receives focus after show/raise and owns the platform
  blinking caret. Typing is never faked in a label.
- A scoped `ShortcutOverride` filter accepts editing/navigation shortcuts while
  the search line or a palette descendant owns focus. It then lets the actual
  text `KeyPress` reach the line edit. `W`, `E`, `R`, `X`, `T`, `C`, digits
  `1`–`5`, and Space therefore type normally instead of firing Max tools.
- `Ctrl+A/C/V/X`, cursor movement, selection, IME/composed Unicode,
  Backspace/Delete, and Home/End remain available. No permanent global OS hook
  is installed.
- Focus-out/window deactivate cancels exactly once. Child pill clicks and inline
  confirmation focus movement are not external blur. The controller uses a
  guarded deferred check with session/generation validation. No scene action
  runs on blur.

### Opening the palette

- **Tab in the viewport opens the palette by default** when the transfer is
  installed. The launcher writes the owned `3dsModeler_startup.ms` into Max's
  user startup folder; on the next session it calls
  `invoke_local(action="install_hotkey")`, which installs one session-local Qt
  event filter. No Customize hotkey or OS hook is created, and a foreign file
  with that name is never overwritten.
- The opening Tab is consumed, so it never advances a context or reaches the
  fresh search field. Modified Tab (Ctrl/Alt/Shift) and auto-repeat are ignored.
- Tab is left alone while a foreign `QLineEdit` (or text editor/spin box)
  owns focus, when any modal or popup widget is active, or while the palette is
  already open.
- Escape and pre-dispatch close through the same lifecycle hook; the host
  restores `rt.enableAccelerators` to the value captured on open, once.

### Context pills and keys

| Input | Result |
| --- | --- |
| Header pills `[0 Obj] [1 Vert] [2 Edge] [3 Border] [4 Face] [5 Elem]` | plain click = standard context switch; Ctrl+Click = switch with selection conversion |
| Tab / Shift+Tab (including Backtab) | while open, cycles contexts, not widget focus traversal; when closed, Tab in the viewport opens the palette |
| Alt+0..Alt+5 | chooses a context |
| Typed `1`–`5` | text only; never a context switch |
| Up/Down | moves the highlighted row, including disabled rows |
| Enter | dispatches only the one highlighted, currently valid row; auto-repeat and stale signals cannot dispatch twice |
| Esc | closes and cancels any pending inline confirmation |
| Typed `>` | system-only search: `> Pull Updates (Git)`, `> Diagnostics`, `> Unload UI` |

A palette pill switch keeps palette/text focus after success. A context macro
invoked outside the palette switches without opening UI. A new context always
starts a new generation, so no stale selected row survives.

### Search, aliases, and pivots

Search normalizes Unicode (NFKC/casefold/whitespace) and fuzzy-matches the
label, ID, every shipped Houdini Modeler/Blender/Maya/Max alias, and optional
checkout-root `user_aliases.json` entries. Deterministic ranking is active
context first, then pinned, match score, atlas declaration order, and ID.

A maneuver valid in another context appears once, with a deterministic pivot
label (`[→ Border]`, `[→ Face]`, …). Enter switches context and then executes.
`fill` from selected open edges is the narrow converting exception: only open
edges are converted/expanded to a Border selection before Cap; closed-only or
empty input blocks. Other pivots switch level without converting selection.

### Dispatch, caddies, and teardown

1. Activation re-snapshots the host and compares the exact safety/selection/
   target/mode identity. An equal-count but different selection is stale.
2. `collapse_to_poly` first produces an inline confirmation with destructive
   scope and a fresh one-use token. Cancel/blur/expiry changes nothing.
3. One dispatch is marked in flight; the synchronous pre-dispatch hook hides the
   palette, removes transient input interception, and restores viewport focus.
   A throwing hook blocks dispatch.
4. The host adapter applies approved preparation, revalidates, and dispatches
   exactly one fixed handler. Count routing uses the fresh destination
   selection: `>0` → caddy/handler, `0` → command mode.
5. A caddy/mode result is `launched`, not a geometry result. Caddy Cancel
   cancels only its own preview; the earlier auto-prep conversion is separately
   undoable. No undo hold spans the interactive caddy.
6. `Esc`/outside focus restores the saved viewport focus only when Max remains
   the active application. Teardown disposes only owned UI/timers/filters and
   never deletes macros, removes hotkeys, clears the rollback lockout, or exits a
   foreign tool.

### Styling

Every state has an explicit color: background `#12151d`, input `#0b0d11`, text
`#e8ebf2`, buttons `#242b38`, accent `#67b8ff`, disabled `#6c7487`, border
`#333b4d`, plus hover/pressed/checked/selected/focus states. No native white
Windows button or inheritance-dependent style is relied on. Windows DPI and
theming remain a manual test.

### Sandbox

The browser sandbox remains a separate simulated renderer over the V1 shared
controller. Every host result there is **SIMULATED**; it cannot control Max and
is not evidence for any V2 native route.

---

## Historical V1 interaction contract

The following describes the frozen V1 shared controller used by the legacy
tools panel, optional popup, and browser sandbox. It is retained for
compatibility tests and is not a normal V2 native launcher.

Initial experiment settings: **tap threshold 220 ms**, **neutral radius 12
logical pixels**. Both are adjustable inside the profile bounds (100–600 ms,
4–48 px) and are visibly editable in the sandbox.

## Inputs

All renderers send normalized events to the controller:

| Event | Meaning |
| --- | --- |
| `trigger_down` | the original trigger was pressed; opens the menu |
| `trigger_up` | the original trigger was released; only this release can commit a held menu |
| `pointer_move` | logical-pixel offset from the invocation origin plus the hit-tested row ID, if any |
| `click_row` | explicit row click with the hit-tested row ID |
| `key_down` | key name or printable text, with an auto-repeat flag |
| `query_changed` | the editor supplied search text |
| `outside_click`, `focus_lost`, `cancel` | cancellations |

Coordinates are offsets from the invocation origin, not screen positions.
Renderer row IDs are hit-test results, not permission to execute: the controller
rechecks the row, eligibility, and the live host context before dispatch.

Every session gets a fresh ID. Events with another session ID or a
non-increasing sequence are ignored. The browser measures gesture duration from
producer timestamps, never from HTTP arrival time.

## States

| Phase | How it is entered | What it allows |
| --- | --- | --- |
| `closed` | start, commit, cancel, teardown, profile/context replacement | no input routing; unrelated keys pass through |
| `held` | `trigger_down` / a held invocation | release after threshold can commit a deliberately hovered enabled row |
| `latched` | explicit manual entry, clearing search, or a tap release before the threshold | click/Enter commits; arrows traverse; typing starts search |
| `search` | printable text while open, or `query_changed` with text | filtered rows in profile order; release never executes |

## Gesture table

| Gesture | Result |
| --- | --- |
| Tap: release before 220 ms | menu stays open **latched**, nothing is armed or launched; click or Enter commits |
| Tap release after hovering a row | latches and disarms the row; explicit click/Enter is still required |
| Release at or after 220 ms, no hovered row | menu closes, nothing launches |
| Press, move outside the neutral zone onto an enabled row, release | that row is committed after revalidation |
| Press, hover a row, return inside the 12 px neutral zone, release | cancels; no launch |
| Press, move over a disabled row, release | cancels with the row's reason; no launch |
| Menu appears under the pointer without a deliberate move | no row is armed; release cancels |
| Click an enabled row in a latched menu | commits after revalidation |
| Enter with a highlighted enabled row | commits after revalidation |
| Enter on a disabled row / no row / no search results | explains why; no launch |
| Arrow Up/Down | moves the highlight, including disabled rows, skipping group headings |
| Printable typing | starts/replaces search and disarms trigger-release execution |
| Backspace with search text | edits the query; clearing it restores latched profile order |
| Escape / outside click / focus loss / invalidated context / second invocation / teardown | closes with no launch |
| Browser tab loses focus while held | sends cancellation, not a guessed key release |
| Trigger auto-repeat | ignored; only the original trigger's release counts |

A successful commit re-snapshots the host immediately before dispatch. A changed
target, level, selection (including a different selection of equal size), panel
state, stack, or deletion blocks the launch and explains why. Nothing is
converted, collapsed, reselected, or repaired automatically. In Qt, the popup
hides and focus returns to the origin widget **before** the host adapter is
called, so a native command mode never starts under the popup.

## Renderer differences

The shared controller supports held gestures, tap latch, and latched entry. The
browser renderer exposes all of them. The optional Qt popup uses explicit
**latched** entry only: the frozen popup factory has no trigger-release input, so
its `held` phase is a demo state and is not a claim of hold-release support.

The native `ModelingPanel` does not use a trigger at all. Its level buttons call
`MenuController.invoke("context.*")`; **Open context menu** asks bootstrap to
show the shared latched popup; **Refresh context** uses the public
`open` + immediate `cancel` contract because no separate refresh contract
exists. `install_launcher()` is a click-to-open alias for the panel. Max native
input routing, viewport hold-release, and all runtime behavior remain
**NOT RUN**.

## Native tools panel workflow

Primary launch inside Max 2025's Python on the main thread:

```python
from max2025_menu_prototype.max_host import bootstrap
bootstrap.show_tools()
```

1. Select one unmodified Editable Poly base object, open the Modify panel, and
   keep the stack empty. Primitives, meshes, multiple/instanced objects,
   nonempty stacks, and active Edit Poly modifiers are unsupported.
2. Choose a sub-object level in the panel. The button changes `subObjectLevel`
   only; it does not convert geometry or repair selection, and zero components
   are allowed.
3. Select components in the native Max viewport. The panel's **Refresh context**
   is a read-only public-controller probe and is skipped while another menu or
   parameter request is active.
4. Choose **Open context menu** (or a fixed panel action) to reach the shared
   controller. Parameterized actions open one modal, typed, no-preview dialog
   with target/context/count, system-unit labels, semantics, and destructive
   confirmation. Commit is **Apply once**; Cancel/Escape/close cancels.
5. After the dialog, the controller takes a fresh snapshot and capability map.
   Any changed selection, level, target, stack, panel, time, transform, mode, or
   soft-selection change blocks the launch. No automatic repair occurs.
6. Results distinguish applied/launched/blocked/error/cancelled/no-op; Max Undo
   remains native. `bootstrap.uninstall()` cancels pending prototype input and
   removes prototype widgets, not committed scene edits.

`bootstrap.show_menu()` remains an optional latched popup at the cursor.
`bootstrap.install_launcher()` returns the same shared panel; it is not a
hold-to-open widget. `bootstrap.uninstall()` probes only public controller
hooks (currently optional owned-native-mode release) and never private adapter
fields or foreign tools.

## Repeat

One shared **Repeat** row appears last in every context. It relaunches the last
successfully accepted repeatable native command against the **current** eligible
context. The label reads `Repeat: relaunch …`.

- Only `applied` or `launched` results — or an explicit **simulated** result in
  the sandbox — update the repeat record; blocked, error, cancelled, and no-op
  attempts do not.
- Parameterized repeat reopens the one-shot dialog against the current
  target/selection with schema defaults; it never stores amounts or calls a
  native RepeatLastOperation.
- Cap and Frame selection are non-repeatable, so they never become the repeat
  target.
- If the last action is unavailable in the current context, the row explains
  that and does nothing.

## Sandbox walkthroughs

Start the sandbox from the session root and open the printed URL:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m max2025_menu_prototype.sandbox
```

The simulated viewport, context/scenario controls, gesture instructions, state
display, and bounded action log are all simulated. Scenario changes are safe to
make while a menu is open.

### 1. Six contexts

Use the context controls to visit `object`, `vertex`, `edge`, `border`,
`polygon`, and `element`. Each keeps its own profile order. Border and element
remain distinct; they are never silently treated as edge or polygon contexts.
Disabled rows remain in place with a reason.

### 2. Tap, click, and hold

1. Focus the simulated viewport. Tap Space briefly: the menu stays open in
   latched mode with nothing launched. Click a row or press Enter to run one.
2. Press Space and hold at least 220 ms without hovering a row, then release:
   nothing launches.
3. Press again, hold, move onto an enabled row, and release: that row launches.
4. Return to the origin within the neutral radius before releasing: it cancels.
5. Open again and click a row (or press Enter): it commits explicitly.

### 3. Search and keyboard

Type `chamfer` or `grow`. Rows filter in fixed profile order; clearing the text
restores that order. Arrow through a disabled row to read its reason. Enter on
an unavailable row or a no-results search does nothing and says why. Typing
during a hold switches to latched search, so releasing the trigger cannot
execute.

### 4. Cancellation

Use Escape, click outside, change the simulated context while open, or open a
second invocation. Each closes or replaces the menu without launching. The log
records `cancel`, `cancelled`, or `blocked` as appropriate.

### 5. Unsupported scenarios

Choose a zero-selection scenario, a primitive, multiple objects, a modifier
stack, a conflicting mode, or the active Edit Poly scenario. Rows stay visible
but disabled, with reasons such as `Select one supported object`,
`Select edges`, `Active Edit Poly target not supported in this experiment`, or
`Host binding not verified`. The simulated viewport is never repaired.

### 6. Stale context and repeat

Open the menu, then change the scenario before committing. Commit is blocked
with a revalidation message. Relaunch, launch a repeatable action, change the
context, and use Repeat: it targets the current context only when eligible.
Failed or cancelled attempts do not replace the repeat record.

### 7. Profiles

Choose a context, reorder actions, hide/show one, reset, change the threshold or
neutral radius, then export JSON. Import it again; malformed JSON is rejected
with a visible error and leaves the active profile intact. In-memory edits are
not persisted automatically.

### 8. Clamped placement

Open the menu near each viewport edge. The popup stays on the available screen.
Placement never arms a row by itself.

## Max scope

`bootstrap.show_tools()` is the primary manual entry; `show_menu()` is an
optional latched popup; `install_launcher()` is a click-to-open alias, not the
retired hold widget. The bootstrap does not intercept viewport right-click,
install a global shortcut, or claim trigger-release input. Uninstall cancels
prototype input/widgets and never cancels foreign tools. The optional
owned-native-mode release remains a public-hook integration seam with W2; no
private adapter access or guessed mode exit is used. See
[`MAX2025-VALIDATION.md`](MAX2025-VALIDATION.md) for what remains unverified.

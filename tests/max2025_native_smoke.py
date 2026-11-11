"""Opt-in native smoke test for a disposable Editable Poly in Max 2025.

This file is intentionally **not** named ``test_*.py`` and is never run by
unittest discovery.  It is authored for a human operator with a real 3ds Max
2025 install; it has **NOT RUN** in this environment.

Safety rules:

* Set ``MAX2025_NATIVE_SMOKE=1`` explicitly.
* Select exactly one disposable Editable Poly, open the Modify panel, enter
  Polygon level, and select one or more faces first.
* The script never creates, converts, resets, deletes, or make-unique's anything.
* It performs fixed P0 edits (extrude, inset, bevel) plus a safe selection grow
  and shrink.  Undo is left to the operator so the native undo unit can be
  inspected.
"""

from __future__ import annotations

import os
import sys
import threading

_ACTIONS = (
    ("selection.grow", {}, "grow the current polygon selection"),
    ("selection.shrink", {}, "shrink the current polygon selection"),
    (
        "model.extrude",
        {"height": 1.0, "extrusion_type": "group"},
        "extrude selected polygons",
    ),
    (
        "model.inset",
        {"amount": 0.1, "inset_type": "group"},
        "inset selected polygons",
    ),
    (
        "model.bevel",
        {"height": 1.0, "outline": -0.1, "extrusion_type": "group"},
        "bevel selected polygons",
    ),
)


def main() -> int:
    if os.environ.get("MAX2025_NATIVE_SMOKE") != "1":
        print(
            "NOT RUN. This opt-in smoke test mutates geometry.\n"
            "Select one disposable Editable Poly, open Modify, enter Polygon "
            "level, select faces, then set MAX2025_NATIVE_SMOKE=1 and rerun."
        )
        return 0
    if threading.current_thread() is not threading.main_thread():
        print("Run this script on the 3ds Max main thread.")
        return 2

    from pymxs import runtime as rt  # type: ignore[import-not-found]

    from max2025_menu_prototype.core.contracts import ContextLevel
    from max2025_menu_prototype.max_host.adapter import MaxHostAdapter
    from max2025_menu_prototype.max_host.operations import read_geometry

    adapter = MaxHostAdapter(runtime=rt)
    snapshot = adapter.snapshot()
    if (
        snapshot.target_kind != "editable_poly"
        or snapshot.level != ContextLevel.POLYGON
        or not snapshot.panel_ready
    ):
        print(
            "Prerequisite failed: select one unmodified Editable Poly, open "
            "Modify, enter Polygon level, and select faces. "
            "target=%s level=%s panel_ready=%s"
            % (
                snapshot.target_kind,
                snapshot.level.value,
                snapshot.panel_ready,
            )
        )
        return 3

    node = rt.selection[0]
    before = read_geometry(rt, node)
    print(
        "Before: %d vertices / %d edges / %d faces"
        % (before.verts, before.edges, before.faces)
    )
    print("This run leaves native Undo entries for manual inspection.")

    failed = 0
    for action_id, parameters, description in _ACTIONS:
        fresh = adapter.snapshot()
        result = adapter.launch(action_id, fresh, parameters)
        print(
            "%s (%s): %s - %s"
            % (action_id, description, result.status, result.message)
        )
        if result.status not in ("applied", "noop"):
            failed += 1

    after = read_geometry(rt, node)
    print(
        "After: %d vertices / %d edges / %d faces"
        % (after.verts, after.edges, after.faces)
    )
    print(
        "Record this output and the Max build/Python version in "
        "docs/MAX2025-VALIDATION.md; this script does not write documentation."
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

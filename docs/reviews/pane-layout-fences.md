# Pane layout review and browser fences

Reviewed 2026-10-03 at runtime baseline `2a712e70`. Shell production stays
unchanged; E2 is skipped because no frame-lifetime defect or blocked local
layout task was demonstrated. This closes a coverage gap, not a shell rewrite.

The representative close task stays within `app_shell.js`:
`closeSplitColumn` → `discardSplitSubtree` → `collapseSplitGroupIfNeeded` →
`remumberSplitColumns` / `persistSplitLayout`, with the existing frame mount,
listeners and column maps on survivor promotion. Pure stored-root helpers
serve several call sites; moving them alone would leave this live path coupled
and has no demonstrated navigation benefit. `CuttleSpaces` decisions remain
single-owned. Untested in-place root mutation is not established harmless.

`src/tests/e2e/test_app_shell_layout.py` uses actual shell/chat assets,
isolated browser storage and the existing static/exact-origin guard fixture.
Six cases cover nested horizontal/vertical restore, leaf identity/order and
flex, real nested-pane close/collapse, pointer focus, Spaces switching and
reload, and v1 saved-layout compatibility. Pane API publication checks await
a new POST after the operation. Reload must not resurrect the closed pane.

The Spaces case simulates only the existing titlebar DOM presentation; it
runs no Electron process or native API. Plain browser titlebar tabs are
intentionally hidden. Focus follows pointer presses and must index a live
pane; an absolute post-reload reset or focus persistence is not claimed,
because iframe activity can update focus during startup. Assertions normalize
only the asynchronous `new` URL flag and preserve chat/session parameters.
Root flex is empty in the observed live/saved root; child/group flex survives.

Manager verification: **6 browser cases passed, zero skips** on the final
candidate, plus **48 pane/workspace/CLI/Spaces checks passed**. Chromium 154,
Linux, short in-project TMPDIR; no live Flask, database or provider involved.
Required browser prerequisites must be available; a skip is not coverage.

```bash
python -m pytest src/tests/e2e/test_app_shell_layout.py
python -m pytest src/tests/test_shell_panes.py src/tests/test_shell_workspaces.py \
  src/tests/test_panes_cli.py src/tests/test_pane_space_drag.py \
  src/tests/test_spaces_state.py src/tests/test_spaces_drop.py \
  src/tests/test_spaces_order_activity.py src/tests/test_spaces_groups.py \
  src/tests/test_space_activity_dots.py
```

Runtime delta: **zero lines**; one focused browser test file added. No restart
is needed. Remaining large shell regions retain their existing ownership;
future extraction still requires evidence of a concrete blocked task or defect.

---
name: cuttle-computer-use
description: >-
  Use an existing signed-in browser, interact with native Linux or Windows
  apps and system file pickers, or take task-specific desktop screenshots in
  Cuttle. Select the local driver and verify the result across agent harnesses.
---

# Computer use in Cuttle

Read [the computer-use runbook](../../docs/computer-use.md). Resolve an
install-local addendum if present.

1. Use the requested browser and profile for signed-in browser work. In Codex
   with Chrome, start with the installed Chrome plugin. Use a native driver
   for operating-system dialogs.
2. Check the target host and desktop session. On Linux Wayland, use
   `wayland-mcp doctor`. On Windows, use `cua-driver doctor`. If the driver
   is missing, use the runbook's optional install for that OS after the user
   authorizes installation.
3. Scope desktop access to the requested task. Inspect the target before
   acting. Use an accessibility control or a fresh screenshot and its
   coordinate mapping. Check the result in the app and close the session
   when finished.

---
name: pipeline-authoring
description: >-
  Visual pipeline graphs were removed. Do not create Node Editor JSON or
  executor wiring. Chat and Discord use slash agents and the agent router.
---

# Pipeline authoring — removed

Visual pipeline JSON, the trigger executor, and the Node Editor are **gone**.
Do not add graphs under `src/pipelines/`, Node Editor UI, or
`pipeline_trigger_executor.py`.

Chat and Discord: starred slash agents (`/cursor`, `/codex`, …) and the
agent router. Recover old graphs from git/Gitea history. See
`docs/PIPELINES_REMOVED.md`.

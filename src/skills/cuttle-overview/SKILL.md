---
name: cuttle-overview
description: >-
  What Cuttle is, how the daemon and Flask API fit together, and where to change
  behavior. Use when onboarding, debugging routing, or explaining the project.
---

# Cuttle overview

## What Cuttle is

Cuttle is a persistent AI agent control-plane: a Windows tray daemon runs Flask
(default port **8080**), optional Discord, Electron Host/Client UI, and mesh
workers. **Chat and Discord** use starred slash agents (`/cursor`, …) and the
agent router — **not** a default pipeline graph.

## Main code paths

| Area | Location |
|------|----------|
| Daemon (spawns API, bot, cron, tray) | `src/scripts/cuttle_daemon.py` |
| HTTP API, web UI, chat | `src/api/web_chat_api.py` |
| Agent harness / slash agents | `src/api/agent_harness/` |
| Context Compiler (Brain) | `src/api/cuttle_brain/` |
| Discord → API bridge | `src/bots/bot_mcp.py` |
| Hub/project config | `.cuttle/` (+ `.cuttle/personal/` overlay) |

## Graphs — removed

Visual pipelines, the trigger executor, and the Node Editor were deleted.
Old JSON: git/Gitea history. Discord still uses the URL
`/api/pipeline-trigger-discord` as chat ingress only.

## Configuration

- Secrets: `src/.env` (or repo-root `.env`).
- Routing and defaults: `src/settings.json` via `src/managers/settings_manager.py`.

## Conventions

- Flask listens on **8080**, not 5000.
- Prefer `.cuttle/` docs/commands/actions over inventing parallel paths.

When editing behavior, prefer the smallest layer that owns the feature and match
existing patterns in those files.

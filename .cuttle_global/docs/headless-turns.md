# Headless agent turns

Cuttle harness agents (Cursor `agent -p`, etc.) are **one-shot** CLI runs per turn.

## Rules

- Finish thinking and tool use **this turn**. Ending the turn exits the process.
- Nothing wakes you except a new user message or a watch-card **Continue**.
- Do **not** launch Cursor/Codex **CLI background** subagents or defer with “I'll dig into…”.
- Independent parallel work belongs in **Cuttle child chats**:
  `python -m api.subagents` (see [subagents.md](subagents.md)). Those sessions
  outlive this turn. Use `--wait` when the parent reply needs their results.
- If a subagent fails, do the work yourself before answering.
- Do **not** sit in a wait loop for long OS jobs.

## Long OS jobs

Builds, uploads, model downloads, ComfyUI, etc.:

1. Prefer a project command with `execute: shell` + `watch:` (Flask attaches the progress card).
2. Otherwise follow [action-forms.md](action-forms.md) (kick job + watch card, then **stop**).

Short work (Q&A, quick edits, seconds-long commands) needs no watch card.

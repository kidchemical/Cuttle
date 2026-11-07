# Headless agent turns

Cuttle harness agents (Cursor `agent -p`, etc.) are **one-shot** CLI runs per turn.

## Rules

- Finish thinking and tool use **this turn**. Ending the turn exits the process.
- Nothing wakes you except a new user message or a watch-card **Continue**.
  Exception — **Claude Code**: while your own background tasks (`Bash` with
  `run_in_background`, `Monitor`) are still running, Cuttle keeps the turn open
  (the chat shows "waiting on background task: …") and their completion
  notification continues the **same reply**. The wait is capped (default 50 min,
  `CLAUDE_BACKGROUND_WAIT_SEC`; `0` disables). At the cap the turn stops waiting, but
  background shells that are still running keep going: Cuttle tells the chat when each
  one ends (a Cuttle notice turn with its output tail), so you pick the result up then.
  In-CLI tasks with no process of their own (Claude `Task` agents) end with the turn.
  Do not start background work you do not intend to wait for (dev servers).
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

### Detached work: unit + `watch-unit` (never the harness's own background watch)

Work you deliberately put outside Flask's lifetime — a build chain started as
`systemd-run --user --unit=<name> …` so a Flask restart cannot kill it — is **not**
covered by your CLI's own background watch. Claude Code's background task ends
with its CLI, and a watch card's `resume_message` only fires while a browser is
polling the status file. An orchestrator lost four of these (idle 1 h, 1 h, 21 h,
12 h) before this existed.

Start the unit, then register the resume in the same turn:

```bash
systemd-run --user --unit=ep-build-linux --remain-after-exit   --working-directory="$PWD" -- ./build.sh          # detached: survives a Flask restart

PYTHONPATH=src .venv/bin/python -m api.job_watch watch-unit   --unit ep-build-linux   --resume "The Linux build finished. Read its log, then continue P6."
```

- Prints the watch card — put it in your reply, then **end the turn**. The card shows
  progress and can Stop the job; it deliberately has no client-side Continue, so the
  chat is resumed exactly once, by the host.
- Cuttle polls `systemctl --user` (or `--pid N` for a bare process) every 15 s — from
  Flask's chat-ops pump **and** from the daemon, so whichever is running catches the
  exit — writes the terminal watch status, and queues your resume message on this chat
  through the chat-ops outbox, **exactly once** (a pass runs under a lock and claims
  the record before it fires). It survives Flask restarts and needs no open browser.
- While it runs, the chat shows it in its background card (*Detached job running* —
  `Running: <unit> · <SubState> · <elapsed>`), and `chat_cli status` lists it under
  `live.background.watches` (`kind: "unit"`) and reports the chat as
  `waiting_background`, so supervision does not nudge it. The row clears when the
  watch fires or its record is removed.
- Pass `--remain-after-exit` to `systemd-run` so the unit keeps its exit status;
  without it the resume turn has to say "exit status not available".
- `--chat CH-…` names the chat to resume (default `CUTTLE_CHAT_SESSION_ID`).
- Register **after** the job is running: a watch on a dead unit is refused
  (`--allow-inactive` overrides).
- Inspect with `python -m api.job_watch units`; `poll-units` forces one pass.
- Rule of thumb: if the work outlives your turn on purpose, it needs `watch-unit`.
  Never finish a turn with "I'll check back later" for detached work.

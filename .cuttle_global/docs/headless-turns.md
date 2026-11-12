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

PYTHONPATH=src .venv/bin/python -m api.job_watch watch-unit --unit ep-build-linux --id linux-checks \
  --label "Linux build and smoke tests" --resume "Read the build and test logs, then continue P6." \
  --activity-json '{"schema_version":1,"steps":[{"id":"build","label":"Build Linux player","state":"running"},{"id":"smoke","label":"Run smoke tests","state":"pending"}]}'
```

- Prints the watch card — put it in your reply, then **end the turn**. The card shows
  progress and can Stop the job; it deliberately has no client-side Continue, so the
  chat is resumed exactly once, by the host.
- Cuttle polls `systemctl --user` (or `--pid N` for a bare process) every 15 s — from
  Flask's chat-ops pump **and** from the daemon, so whichever is running catches the
  exit — writes the terminal watch status, and queues your resume message on this chat
  through the chat-ops outbox, **exactly once** (a pass runs under a lock and claims
  the record before it fires). It survives Flask restarts and needs no open browser.
- While it runs, the chat shows a compact *Background job running* card with
  readable steps and an explicit completion handoff. `chat_cli status` lists it under
  `live.background.watches` (`kind: "unit"`) and reports the chat as
  `waiting_background`, so supervision does not nudge it. Finished jobs retain up to
  three receipts per chat for seven days, including follow-up delivery status.
- **Stop means stop.** Stop on the card (or `python -m api.job_watch cancel --id <id>`)
  stops the unit and cancels its follow-up: the chat is not resumed for a job that
  was ended on purpose. The receipt shows the handoff as `cancelled`. A unit that is
  stopped any other way (`systemctl --user stop`, a crash, a reboot) still counts as
  an exit and resumes the chat, so stop watched jobs through the card or `cancel`.
- Pass `--remain-after-exit` to `systemd-run` so the unit keeps its exit status;
  without it the resume turn has to say "exit status not available".
- `--chat CH-…` names the chat to resume (default `CUTTLE_CHAT_SESSION_ID`).
- Register **after** the job is running: a watch on a dead unit is refused
  (`--allow-inactive` overrides).
- Inspect with `python -m api.job_watch units`; `poll-units` forces one pass.
- **Starting later** (after a reset, overnight): let systemd own the schedule —
  `systemd-run --user --unit=<name> --on-calendar='2026-11-09 15:52' --remain-after-exit -- ./job.sh`,
  then `watch-unit --unit <name>` as usual. While the timer waits the card says
  *Background job scheduled* with the start time and a **Start now** button
  (`python -m api.job_watch start-now --id <id>`); Stop disarms the timer. Never
  hide a wait inside the job (`sleep N` in a script): the card can only call that
  "running", and nobody can start it early.
- **Before deferring for a usage limit**, check live usage
  (`python -m api.gizmos usage <agent>`). A spawn's `out of usage until … (re-probe
  after an earlier failed run; the reset was not named)` is a guess, not the
  vendor's reset; if the agent you are running on *is* that agent, it is wrong.
- Rule of thumb: if the work outlives your turn on purpose, it needs `watch-unit`.
  Never finish a turn with "I'll check back later" for detached work.

Use a human-readable `--label`, not a unit name. For multi-step jobs, have the
runner publish a full activity snapshot at each step transition and periodically
while waiting (the reporting CLI must run on the Host with the same Cuttle home):

```bash
PYTHONPATH=src .venv/bin/python -m api.job_watch report --id linux-checks \
  --activity-json '{"schema_version":1,"summary":"Smoke tests running","steps":[{"id":"build","label":"Build Linux player","state":"done"},{"id":"smoke","label":"Run smoke tests","state":"running","detail":"Case 2 of 4"}],"output":"Build succeeded. Starting smoke tests.","stale_after_sec":600}'
```

Activity v1 is optional: at most 32 steps (`pending`, `running`, `done`, `failed`,
`skipped`, `cancelled`), a 240-character summary, and the last 4,000 characters
of explicitly supplied recent output. Supply only non-secret output. Optional
`deadline_ts` is an expected deadline in epoch seconds, not an automatic kill.
`updated_ts` is stamped by the Host on reporting; no update for `stale_after_sec`
(default 600) shows a warning, not a claim that the job is hung. Activity is kept
separate from lifecycle status, so a late heartbeat cannot turn a finished job
back into a running one. Library producers call `job_watch.report_activity(id, snapshot)`;
the pure normalizer is `api.job_activity.normalize`, with no host dependencies.
Unknown versions/absent snapshots fall back to process status; remove reporting
to return to that behavior without migrating watch records. Cuttle never guesses
step progress or tails arbitrary log paths. Retained receipts keep the latest
snapshot available after refresh/restart.

Pausing a **supervision schedule** does not disable the job's independent completion
follow-up. The card states this in Details and shows whether the follow-up is
queued, sending, being verified, delivered, failed or cancelled. “Delivered” means
the turn was accepted; it does not promise that the agent has finished its reply.

# Cuttle chat history

Transcripts live in a **gitignored SQLite DB**, not in the working tree. Empty
`rg`/`grep` over the repo is **not** evidence that Cuttle has no chats — never
answer “no traces of Cuttle chats found” on that basis alone.

Open this file before reading or summarizing chat history.
Prefer `<home>/personal/docs/chat-history.md` when it exists (install-local DB path).
`<home>` is the per-user Cuttle home (`~/.local/share/cuttle`, `%LOCALAPPDATA%\Cuttle`, or `$CUTTLE_HOME`; `core.runtime_paths.cuttle_home()`).

## Agent ops CLI (preferred)

Do **not** hand-roll SQL against `cuttle_auth.db`. Use the CLI (wraps the same
library the UI uses for share indices):

```bash
# From Cuttle repo root — always use the project venv (bcrypt + api on path)
PYTHONPATH=src .venv/bin/python -m api.chat_cli get CH-000430-99 --json
PYTHONPATH=src .venv/bin/python -m api.chat_cli get CH-000430 --json
PYTHONPATH=src .venv/bin/python -m api.chat_cli session CH-000430 --limit 40 --json
PYTHONPATH=src .venv/bin/python -m api.chat_cli session CH-000430 --all --json
PYTHONPATH=src .venv/bin/python -m api.chat_cli search "flask restart" --from-chat CH-000465 --json
PYTHONPATH=src .venv/bin/python -m api.chat_cli parse CH-000430-99 --json
```

Do **not** call a bare `python` / system interpreter — missing venv deps used to
look like “chat_cli is broken” (e.g. `ModuleNotFoundError: bcrypt`) even though
reads no longer import bcrypt at module load.

Put flags **after** the verb (`get … --json`, not `--json get …`).

`get` / `session` / `search` hide other agents' `<think>` narration (it trips
provider reasoning-extraction safeguards for the agent reading it); a
post-processed reply is shown as the agent's own answer followed by the
processed text. Read through `chat_cli`, not raw SQL; `--raw`
shows stored text unchanged, but only for debugging storage, never to bring
context into your own turn.

| Verb | Use |
|---|---|
| `get CH-…` | Session summary, or one bubble when `CH-…-N` |
| `session CH-…` | List bubbles with UI share indices |
| `search "…"` | Title/body search (pass `--from-chat` on multi-user DBs) |
| `parse CH-…` | Parse handle only (no DB) |
| `status` / `snapshot` / `send` / `stop` / `supervise` / `context` / `compact` | Operate on another chat. See [Talk to, inspect, or supervise an existing chat](#talk-to-inspect-or-supervise-an-existing-chat) |

Exit codes: `0` ok, `2` not found / bad handle, `3` other error.

Library (same semantics): `resolve_chat_handle_message`, 
`AuthDatabase.get_message_by_share_index`. Design pattern: `.cuttle_global/docs/agent-ops-cli.md`.

## Talk to, inspect, or supervise an existing chat

Same CLI, one call each. Never mint auth tokens, curl `/api/chat`, or read
Flask source for this. `api.subagents message` only reaches child chats it
spawned. Use these verbs for any other chat (including a parent or sibling).
The acting chat defaults to `CUTTLE_CHAT_SESSION_ID` (already set inside a
Cuttle turn); pass `--as CH-…` otherwise. Both chats must have the same owner.

```bash
PYTHONPATH=src .venv/bin/python -m api.chat_cli status   CH-001548 --json
PYTHONPATH=src .venv/bin/python -m api.chat_cli snapshot CH-001548 --usage codex --json
PYTHONPATH=src .venv/bin/python -m api.chat_cli send     CH-001548 --text "Continue with the next open task." --json
PYTHONPATH=src .venv/bin/python -m api.chat_cli stop     CH-001548 --json
PYTHONPATH=src .venv/bin/python -m api.chat_cli supervise CH-001548 --every 900 --agent cursor \
  --involve blocked --require-branch dev/multiplayer --forbid-branch dev/core --forbid-branch master \
  --model-tiers '{"worker":["cursor auto","codex terra medium"],"expert":["claude opus"]}' --json
PYTHONPATH=src .venv/bin/python -m api.chat_cli operations --json
PYTHONPATH=src .venv/bin/python -m api.chat_cli cancel-operation <id> --json
```

**A queued send is not a delivered one.** `send` returns `delivery: queued`
when the target is mid-turn; it is delivered when that turn ends. `status`
lists what is still in the outbox for a chat under `undelivered` (from, to,
`waiting_sec`, `waiting_on`), and a supervision check-in reports any send from
or to its target that has waited 15 minutes ("Not delivered"), even while the
target is busy. Before assuming a lane got its brief, read `undelivered`.

**Settings, not prose.** Every check-in states the supervisor's job and each
setting (Tasks lists, branch rules, `--involve`, checkpoints, `--nudge`) from the
schedule itself (`api.chat_ops.brief`); settings win. `--instructions` is the
optional **Focus**: the user's job-specific notes (e.g. "animation first;
verify runtime IK exceptions"), shown under Advanced in the Supervise dialog.
Don't put models, branches, intervals, when to involve the user, milestone
reviews or per-check-in procedures there — use the flags (or a rule/runbook);
the CLI prints why on stderr and the result carries `warnings`. Only set or
change Focus when the user asked for that guidance. `--involve`: `blocked`
(default: logins, payments, irreversible steps), `never`, `questions`.
`--nudge template`: an idle chat with open Tasks items gets its next item from
the host with no supervisor turn (twice without Tasks progress, then the
supervisor is woken); errors, questions, stalls and violations always wake it.

| Verb | Use |
|---|---|
| `status CH-…` | `state`: `busy`, `idle`, `idle_with_work` (open Tasks items), `waiting_background` (turn ended but its CLI is still waiting on its own background task, or a detached job registered with `job_watch watch-unit` is still running, and the chat will be resumed by itself; `live.background_wait` lists it, so don't nudge), `awaiting_input` (form waiting), `error`, `cancelled`; plus Tasks progress, follow-up queue, latest reply (`latest_reply.text` is the readable answer: post-processing wrapper and `<think>` removed, cards as `[card: …]`; `content` is the raw stored row) |
| `snapshot CH-…` | `status` plus worktree binding, live branch, execution path, the chat's jobs and watch bars, and `context` (its agent's window fill); `--usage <agent>` (repeatable) adds plan-usage windows |
| `context CH-…` | Window fill of the chat's agent session: `agent`, `percent`, `used_tokens`/`limit_tokens`, `compact_available` (the agent is the one that last ran there; `--agent ID` overrides) |
| `compact CH-…` | Compact that session **between turns** (refused while the target is busy or its CLI is still live). Default `--mode native`: the CLI summarizes its own session (Cursor, Claude, Codex, OpenCode, Hermes, Muse). `--mode fresh`: discard the session, so the next turn starts a new one with the full Cuttle briefing, Tasks/orchestration brief and the recent transcript (works for any resumable harness, e.g. Antigravity). Exit 1 with `error` when refused. Supervision check-ins suggest it once fill reaches 75% |
| `send CH-… --text` | A live turn is steered (`delivery: steered`). Otherwise it is queued in a host-owned outbox (`delivery: queued`) and sent when the chat goes idle. The browser does not need to be open. `--mode queue` never steers. `target_busy: true` (with `steer_reason`) means the turn is live but took nothing: the send waits for that whole turn to end. Let it wait: stopping the target throws away its running work. Run `stop` only when the user asked to pause or stop that chat |
| `stop CH-…` | Same as the Stop button, and it also cancels sends still queued for that chat (`cancelled_sends` in the result), so the chat stays stopped instead of restarting on the next queued message |
| `supervise CH-…` | Durable check-ins that survive your turn and Flask restarts. Each interval the host checks the target. It wakes **your** chat with a compact report only when the target is not busy and something changed, or when a branch/path rule is violated. A healthy, working target costs no tokens. A busy or background-waiting target that shows no progress for 45 min (no commit, Tasks change, reply of its own or of a sub-agent, or new background wait) wakes you once; repeats without progress back off to 90 then 180 min. When the target **ends a turn** on a question card (`awaiting_input`), with open Tasks items (`idle_with_work`) or on an `error`, the next check is pulled to now, so that check-in arrives within seconds rather than up to one interval later (at most one early wake per schedule per 2 min; never for `busy` or `waiting_background`; the interval stays the backstop) |
| `supervise … --require-branch / --require-path / --forbid-branch` | Guard rules, checked on every interval **and** whenever the target or one of its sub-agent children finishes a tool call or rebinds its worktree. A new violation wakes you within seconds, even mid-turn. A commit landing on a forbidden branch from any checkout of the repo is reported once (the new tip becomes the baseline); wrong branch/checkout repeats each interval until fixed |
| `supervise … --checkpoints JSON` | Event wakes on top of the interval (`api.chat_ops.checkpoints`), from the target **or its sub-agents**: `milestone` (an item in a followed Tasks list is checked off, or the list completes), `tests` (a test command finishes; `when`: `pass` / `fail` / `any`), `repeat_failures` (the same command fails `n` times in a row, default 3), `watched_paths` (files matching `regex` change in the target's checkout, committed or not; today's state is the baseline). Each has its own `do` list, same entries as `--on-violation`: `wake`, `stop` (halts the target until someone sends it on — the approval gate), `action:<name>`, `supervisor:<profile>` (a reviewer chat on that profile, briefed with the checkpoint). One undelivered wake per checkpoint kind; every firing is in the check history as `checkpoint`. The dialog starts new schedules with `milestone` + `repeat_failures`. Change later with `edit-operation <id> --checkpoints JSON` (`{}` = none). The `failed` flag comes from each harness adapter's tool result |
| `supervise … --agent ID` | Prefix each check-in with `/<agent>` so that harness runs it (validated against the agent catalog) |
| `operations` / `operation <id>` / `cancel-operation <id>` | Inspect or cancel your queued sends and supervision schedules |
| Unattended targets | A chat under a **live** (not paused) schedule, and every sub-agent child, gets an "Unattended chat" section after the rules (`context_compiler.unattended_reason` = `git_hook.supervised_reason`): no Discord posts or person-only side-effect cards (`discord.post`, `git.push`, `flask.restart`), even when a project rule asks for one at the end of a turn; it names the need in its reply and the supervisor or parent decides. A supervised chat still asks with Q&A cards (the supervisor answers them); a child asks in plain text |
| `pause-operation <id>` / `resume-operation <id>` | Pause a schedule; resume checks right away. **A pause is timed:** no flag = 4 h, `--hours N` / `--until <epoch>` set your own; the host resumes it and checks immediately. `--indefinite` pauses with no expiry — nothing but a resume brings it back, so only use it when you mean to turn supervision off. Paused schedules are listed in both chats' context envelope ("Supervision schedules on this chat") with since-when, and an indefinite pause left over `supervision_paused_alert_hours` (default 6) while the target is idle with open Tasks items raises one UI/tray warning |
| `edit-operation <id>` | **This is how the check-in model changes** (`--model` / `--effort` / `--agent`); the supervisor's envelope names its schedule id, current pin and the exact command, so never tell the user the CLI can't do it. Change a supervision schedule in place; only the flags you pass move: `--every`, `--agent` / `--model` / `--effort` (re-pins the supervisor chat and its composer chip), `--instructions` (Focus; see above), `--involve`, `--nudge`, `--require-branch`, `--require-path`, `--forbid-branch` (replaces the list, re-baselines tips), `--model-tiers`, `--tasks a,b` (Tasks lists to follow; the chat's own lists made later still count) / `--all-tasks`. A paused schedule stays paused. The edit adds a timeline notice to the supervisor chat and rides the next check-in ("Settings changed since your last check-in"; a live schedule checks within ~20 s), never a turn of its own. UI: Edit… in the supervision chip popover |

Send states: `pending` → `delivered` (host started the turn) / `done` /
`failed`. If the connection drops mid-send (for example, Flask restarts), the
send goes to `verifying`. Once the chat is idle, the host looks in its history
for the send's id (`metadata.chat_op_id` on the user row). If the id is there,
the send is `delivered`. If not, it was never received and is resent
automatically. A message is never sent twice. Sends, steers and supervision
check-ins render in the receiving chat as **Cuttle** (like a parent's message
to a sub-agent), not as the user. This happens only when the chat-ops store
holds that id for that chat. `unknown` now means three
attempts never reached the chat; run `status` before resending by hand.

**Supervising another chat:** run `status` once to orient, then `supervise`,
then end your turn. Do not sleep-loop inside a turn. Each check-in arrives as
a new turn in your chat. Act on the target with `send` (steer or queue). Do not
message its sub-agent children unless asked.

### Change the target chat's agent, model, or effort

`chat_cli send` can deliver the target harness's own slash commands; it does
not change the supervisor chat or its schedule. Use the target's harness prefix
and a model/effort supported by that harness, for example:

```bash
PYTHONPATH=src .venv/bin/python -m api.chat_cli send CH-001548 \
  --text "/claude model claude-opus-5-5" --json
PYTHONPATH=src .venv/bin/python -m api.chat_cli send CH-001548 \
  --text "/claude effort medium" --json
PYTHONPATH=src .venv/bin/python -m api.chat_cli send CH-001548 \
  --text "/claude Continue with the next open task." --json
```

First check `status`. When the target is busy, sends may steer the live turn;
if a send reports `target_busy` / queued, let it deliver after that turn rather
than stopping the chat. A model change does not switch the model of a turn
already running. Queue model and effort commands in order, then send a
continuation only if the target needs a prompt to resume its work. Afterward,
check `status` or `snapshot` and the target's agent chip / confirmation before
reporting the new setting. Do not contact the target's child chats.

Sends and supervision need the host up (Flask on loopback); read-only `get` /
`session` / `search` do not. Library: `api.chat_ops` (service, store, worker, rules, triggers).

## This chat vs other chats

- Display id `CH-<zero-padded number>` → numeric `chat_sessions.id` (strip `CH-` and leading zeros).
- Message ref `CH-<session>-<N>` = **same** session, 1-based bubble index `<N>` among
  **user+assistant rows only** (`role IN ('user', 'assistant')`; exclude `role='system'`),
  ordered by `chat_messages.id` (not the row primary key). System notices — especially
  **Stop generating** (`⏹ Stopped generating.`) — are stored in the DB but **do not**
  consume a share index. Matching the UI: `.message:not(.system)` / message-nav.
- A running turn's reply row exists from the moment the turn starts, so the live
  bubble already has its `CH-<session>-<N>` and query-log link. Until it finishes
  the row is empty and marked in progress: `get_messages`, `status`/`snapshot` and
  `get` skip it, and `get CH-…-N` on it returns the empty row. The reply lands in
  that same row. A stopped, failed or crashed turn turns it into the "Agent
  stopped/failed/interrupted before completion" card, so no empty row stays behind
  and later indices never shift.
- Every assistant row records the agent turn it belongs to: `metadata.turn_id`
  (the turn's query id; the inspector is `/query_log.html?id=<query_id>`). Rows
  that no agent turn produced (launch-gate and control replies, Flask-restart
  notices, supervised coordinator bubbles, README demo seeds) have none.
- Never invent a different meaning for `CH-<session>-<N>` (it is not a second chat).
- Never reuse a session id from an example or from this doc’s sample numbers — that
  reads a stranger’s conversation. Use the id named in your turn briefing, or the
  `CH-` handle the user gave you.
- **In replies:** cite as bare `CH-000182` (Cuttle linkifies the badge). Do not wrap
  in `[CH-…](file://…)` — that becomes a file chip. Rule: `.cuttle_global/rules/01-chat-handles.md`.

## Database

| | |
|---|---|
| Default path | `<home>/db/cuttle_auth.db` (resolve via runtime / `api.auth_db.DB_PATH`) |

Tables: `chat_sessions(id, user_id, session_name, last_activity, is_active)`,
`chat_messages(chat_session_id, role, content, timestamp)`.

## Live API (host)

Prefer the panes CLI over hand-rolled curl:

```bash
PYTHONPATH=src .venv/bin/python -m api.panes_cli list --json
PYTHONPATH=src .venv/bin/python -m api.panes_cli messages 1 --limit 40 --json
```

(Flask must be up — HTTPS on `127.0.0.1:8080`, self-signed.)

Raw endpoints (fallback): `GET /api/shell/panes`, `GET /api/shell/panes/<n>/messages?limit=40`.

From **WSL**, `127.0.0.1` is often the WSL VM — the host API may be firewalled;
use `python -m api.chat_cli` / `api.panes_cli` on the Windows host instead.

## Related

- Handle recognition (always-on rule): `.cuttle_global/rules/01-chat-handles.md`
- Agent ops CLI pattern: `.cuttle_global/docs/agent-ops-cli.md` / `.cuttle_global/rules/02-agent-ops-cli.md`

## Shell environment

Examples run from the Cuttle repository root with the project venv. POSIX
examples set `PYTHONPATH=src` per invocation. For Windows PowerShell, set
the path and use the Windows interpreter; for example:

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m api.chat_cli get CH-000430-99 --json
```

Use PowerShell backticks for multiline continuation and single-quoted JSON
arguments; POSIX examples use backslashes for continuation.

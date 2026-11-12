# Sub-agents (child chats)

Any Cuttle chat can spawn **real child chats**. Each child is a normal
`chat_sessions` row (`origin=subagent`, `parent_session_id` set) that you can
open from history. This is **not** `/coordinate` (synthetic worker ids) and
**not** a subagent facility built into your own CLI (those run inside your turn
and die with it, so they never become openable child chats).

Library: `api.subagents`. CLI: `python -m api.subagents`.

Claude chat launches enforce this boundary with `--disallowedTools` for
`Agent`, its legacy name `Task`, and `TeamCreate` (alongside the native question
picker). This applies on fresh sessions, resumes and API-credit retries; it
does not depend on Brain repeating an unchanged rule. Background `Bash`,
`Monitor`, `TaskStop` and `TaskOutput` remain available for OS jobs and Cuttle
`spawn --wait` commands. The trajectory's `harness_policy` event records the
launch policy and deny list. Already running Claude processes retain their
original tools until they finish; a Flask restart does not retrofit a live run.

To message, check on, or supervise a chat this CLI did not spawn (a parent,
sibling, or any existing chat), use `python -m api.chat_cli send|status|snapshot|supervise`
(`chat-history.md`).

## Spawn

```bash
PYTHONPATH=src .venv/bin/python -m api.subagents spawn --parent CH-000535 --wait --json \
  --collect all \
  --lifetime one_shot \
  --child "{\"title\":\"Chef A\",\"agent\":\"cursor\",\"message\":\"What should I cook with eggs and spinach?\"}" \
  --child "{\"title\":\"Chef B\",\"agent\":\"cursor\",\"model\":\"grok-4.6\",\"effort\":\"low\",\"message\":\"Same ingredients, spicier.\"}" \
  --child "{\"title\":\"Chef C\",\"agent\":\"codex\",\"model\":\"gpt-5.6\",\"effort\":\"low\",\"message\":\"Same ingredients, fastest.\"}"
```

`--child` is repeatable JSON. Keys: `title`, `message` (required), `agent`
(harness id: `cursor`, `codex`, `muse`, …), `model`, `effort`, `route` (bool),
`profile` (catalog id or inline `{name,avatar,agent}`), `avatar`, `tier`
(orchestrator chats: a playbook tier; Cuttle runs its first agent with budget
left — `features/orchestrator/docs/orchestrator.md`).

A pinned child whose account is out of usage (a recent quota failure, or the
cached `/usage` plan windows at their limit) is refused before any chat is
created; a child that fails on quota marks its account for later spawns and
routed turns. Only a *failed* run can mark an account (a successful reply that
mentions "budget" or a number like `1.402` never does). A mark whose error named
the vendor's reset holds until then; one with a guessed reset ("the reset was not
named") yields to a newer live `/usage` snapshot that shows the account usable,
and any later successful turn on that agent clears it. Don't park work for hours
on a budget block without checking `python -m api.gizmos usage <agent>`.

`model` must be an id the named harness's own CLI accepts — pass it exactly as
that harness spells it (some qualify with a provider prefix, some are bare); do
not translate an id from another harness.

`--children-json '[...]'` also works.

Always pass `--json` when a parent agent will parse the result.

### Collect (how the parent waits)

| `--collect` | Behavior |
|---|---|
| `all` (default) | Run in parallel; wait for every child |
| `first` | Parallel; first completed reply wins; cancel the rest |
| `serial` | Run one after another in `--child` order; cancelling the running step cancels the steps after it |

### Lifetime

| `--lifetime` | Behavior |
|---|---|
| `one_shot` (default) | One turn each; batch completes when the collect rule is met |
| `conversational` | Children stay open. `message` more turns; `close` when done |

`--wait` is **required** for `spawn` and `message`: child turns run on
daemon threads of the CLI process, so without `--wait` the process would
exit immediately, its workers would die with it, and the batch would keep
pending/running rows no live owner can finish. The CLI rejects a missing
`--wait` before creating any row. Use `status` to inspect, `wait` to
re-poll a batch a previous `--wait` already supervised (e.g. after a
timeout), and `cancel` to terminally stop stuck work.

`--timeout` seconds (default 900) bounds supervision polling: if the
round is still unfinished, `spawn --wait` terminally cancels the unfinished
work it owns and exits nonzero (1) instead of abandoning active rows.
Serial children run synchronously one at a time, so `--timeout` does not
deadline-interrupt an individual synchronous turn. Cancel (`cancel`, chat
Stop, `/api/chat-cancel`, the agent feed's End) flips the row and records a
stop request on it; the process that runs the child turn polls that request
and kills the child's CLI within about a second, including a CLI a finished
Claude turn left in a detached background wait. Only the owning process can
kill it: Cuttle signals only its own descendants. `message --wait` runs the follow-up turn to
completion — its `--timeout` is accepted but not enforced on the turn.
`wait` is observation-only: it never starts pending work and never cancels
another owner's batch. Crash/SIGKILL recovery (rows left active with no
process at all) is explicitly deferred: inspect with `status`, terminally
stop with `cancel`.

**Parent stopped listening.** A `--wait` CLI notes the process that launched it
(the parent's agent CLI). If that process is gone when the round ends (your
turn hit its background-wait limit, timed out, or Flask restarted), the CLI queues
a Cuttle notice turn on the parent chat with each child's handle, status and
reply, and the parent continues from there. Running `spawn --wait` as a
background command is therefore safe for long children: you are told when it
finishes either way. While the launcher is still alive nothing extra is posted.

Prefer that ordinary background command over a separate systemd service for
child chats. Use a service only when the spawn supervisor must survive its
launching process (for example, a planned Flask restart). `systemd-run` does
not inherit Cuttle's environment. Append `--turn "$CUTTLE_TURN_ID"` to the
`spawn` / `message` command in the **launching shell**, expanding it before
crossing the service boundary; alternatively pass
`--setenv=CUTTLE_TURN_ID="$CUTTLE_TURN_ID"` and
`--setenv=CUTTLE_CHAT_SESSION_ID="$CUTTLE_CHAT_SESSION_ID"` to `systemd-run`.
Preserve the other ownership variables too (`CUTTLE_AGENT_RUN_ID`,
`CUTTLE_AGENT_ID`, `CUTTLE_AGENT_PROJECT_PATH`, and `CUTTLE_HOME` if set).
Register completion with `watch-unit` ([headless-turns.md](headless-turns.md)).
Do not start a second spawn to fix card placement.

`--route` asks the existing Cuttle router to pick `agent`+`model` for children
that omitted them (or set `"route": true`).

`--watch` writes `/output/subagents-<id>-status.json` with an overall bar plus
one worker bar per child (emit a watch card from the parent reply if the job is
long). `--tasks` pins a parent Tasks gizmo with one item per child.

## Other verbs

```bash
PYTHONPATH=src .venv/bin/python -m api.subagents status --parent CH-000535 --json
PYTHONPATH=src .venv/bin/python -m api.subagents status --batch "<batch-id>" --json
PYTHONPATH=src .venv/bin/python -m api.subagents status --parent CH-000535 --context --json
PYTHONPATH=src .venv/bin/python -m api.subagents wait "<batch-id>" --json
PYTHONPATH=src .venv/bin/python -m api.subagents message --session CH-000540 --text "follow up" --wait --json
PYTHONPATH=src .venv/bin/python -m api.subagents cancel --batch "<batch-id>" --json
PYTHONPATH=src .venv/bin/python -m api.subagents cancel --session CH-000540 --json
PYTHONPATH=src .venv/bin/python -m api.subagents list --parent CH-000535 --json
PYTHONPATH=src .venv/bin/python -m api.subagents close "<batch-id>" --json
PYTHONPATH=src .venv/bin/python -m api.subagents profiles list --json
```

`--session` / `--parent` accept `CH-000535` or a numeric id. Tests may pass `--db`.

`status --context` adds each child's agent window fill (`context.percent`,
`compact_available`). A conversational child you keep reusing that runs above
~75% can be compacted between its turns with
`python -m api.chat_cli compact CH-… [--mode fresh]` (see `chat-history.md`);
never while it is working.

## Caps

- 8 children per batch
- nesting depth 3 (a child may spawn children, not forever)

## UI

The parent typing indicator **keeps its own orbit** (and dancing meters) and
adds a **separate sibling orbit per sub-agent**, same size, with a name under
it. Those extra orbs stay up through parent synthesis (until the parent reply
binds chips). Click an orbit to open that child chat.

After the parent reply is saved, launcher chips under **that assistant bubble**
open the children. In a **child** chat, inbound turns are from **Cuttle** (not
the human), and a parent-chat chip sits under **that sender bubble**. Reusing
the same child from another parent tags each inbound bubble with *that* parent.

**Fleet cards (experimental, `subagent_fleet_cards`):** the launcher chips
become one card per child — name/avatar, harness · model · effort, outcome
(`queued` / `running` / `done` / `failed` / `cancelled` / `lost` = host process
died) and a one-line plain-text result (full text in the tooltip). Outcome comes
from the child row, never inferred; history reload refreshes cards from it.
Click opens the child chat. A follow-up round (`python -m api.subagents message`)
gets its own card on the bubble of the turn that sent it (same rules as below),
and the earlier bubble's card is frozen at the previous round's outcome
(`frozen: true`, never re-hydrated).

Cards show **the moment `spawn` creates the batch** and attach to the **turn that
launched it**, the same for every agent (Claude Code, Codex, Cursor, OpenCode, …).
Every agent turn Cuttle runs exports its identity to the agent's process:
`CUTTLE_TURN_ID` (the turn's query id), plus `CUTTLE_TURN_MESSAGE` (the user
bubble that started it, `CH-<chat>-<N>`) and `CUTTLE_TURN_MESSAGE_ID` where there
is one. `spawn` and `message` record that turn on the batch / follow-up round, and
every assistant row records the turn it belongs to (`metadata.turn_id`, stamped
on every write path). So:

- the card is live on that turn's in-progress bubble as soon as `spawn` runs (and
  in the pinned background card), and stays on the reply that turn saves,
  matched by turn id;
- a reply from a different turn never takes another turn's card;
- nothing depends on timing or on which agent ran the turn.

If that turn's reply was already saved when `spawn` ran (an agent still working in
the background after its reply), the card goes on the first reply of that turn
saved after the spawn, never back onto the earlier one.

Sub-agents are not background jobs: the parent's pinned **background card**
does not list them; their transcript cards are their only place. While children
are live, the card also leaves out the run that waits on them — the agent's own
background task or detached unit that runs the `api.subagents` command — so the
same work is never shown twice. If that run returns after its turn ended, the
result arrives as a Cuttle notice turn (one line: each child and its status).

A follow-up to a child — `subagents message` or `chat_cli send CH-<child>` —
starts a new card round the same way, bound to the turn that sent it, and the
round ends when the child's turn ends.

A manual run from a plain terminal has no turn env: pass `--anchor CH-<chat>-<N>`
(spawn and message) to pin the cards to that parent bubble, else they go on the
parent's latest bubble. If the parent has no bubble at all yet, the background
card shows them until one exists.

Without turn identity, a newer outstanding user request or in-progress reply
blocks the old-bubble fallback: cards stay on the live strip instead of being
filed on an earlier answer. Cuttle does not guess ownership from whichever turn
is active. After verifying a detached batch's originating turn in the query log,
repair it with `python -m api.subagents repair-cards --batch <batch-id> --turn <parent-turn-id> --json`.
This moves the original cards without restarting children, preserves unrelated
cards, and refuses conflicting known identities or follow-up card rounds.

Running fleet cards show the harness's latest status (reading files, running
commands/tests, writing, etc.) rather than a fixed "Working…". The child CLI
persists a bounded snapshot in SQLite, coalescing updates to at most four writes
per second; existing parent live-status/history polls refresh cards, including
cards on in-progress bubbles. Child panes use the same status. Tooltips carry
the snapshot's UTC update time. There is no fabricated percentage or partial
answer presented as a result. Terminal outcomes clear running status; turn ids
prevent late callbacks from overwriting a subsequent child turn. Harnesses
without status events retain "Working…" until a real outcome arrives.

History nests children under the parent (Subagent badge), **collapsed by
default**. The parent row shows a pivoting chevron and a count badge; click
either (or **Show sub-agents** in that row’s menu) to expand. Opening a child
always reveals that child, its siblings, and the parent. Child assistant
bubbles use the sub-agent's profile name and avatar.

## Agent profiles

Profiles name the sub-agent (speaker), set an avatar (emoji, image URL, or
`cuttle`), and optionally pin a harness (`none` = no preference).

Built-ins: `cuttle`, `scout`, `critic`, `builder`, `chef`, `muse`, `codex`,
`hermes`.

```bash
PYTHONPATH=src .venv/bin/python -m api.subagents profiles list --json
PYTHONPATH=src .venv/bin/python -m api.subagents profiles get scout --json
PYTHONPATH=src .venv/bin/python -m api.subagents profiles save --id dinner-judge --name "Dinner Judge" --avatar "🍽️" --agent cursor --model grok-4.6 --effort low --json
PYTHONPATH=src .venv/bin/python -m api.subagents profiles delete --id dinner-judge --json
```

On `--child` JSON:

- `"profile": "scout"` — catalog id (built-in or saved)
- `"profile": {"name":"Judge","avatar":"⚖️","agent":"codex"}` — ephemeral (this spawn only)
- `"title"` / `"name"` / `"avatar"` — override identity without a catalog row
- Top-level `"agent"` always beats the profile harness

`--agent none` on `profiles save` means no harness preference.

`profiles save` checks the harness/model/effort pin against that CLI's catalog
and rejects a pin that cannot start. `spawn` runs the same check on every
child before creating any chat; a rejection names the child and the pin — fix
the spec or pick another profile rather than resending it unchanged.

## Parent reply

After `--wait`, synthesize **one** answer in the parent chat. Cite children with
bare `CH-000540` tokens (never markdown-wrapped). Do not dump every child
transcript unless the user asks.

## Git worktrees for children

Create worktrees with `python -m api.worktrees create`, not raw `git worktree
add`: registered checkouts can be bound to a chat, so its chip and turns follow
them ([agent-ops-cli.md → Worktrees](agent-ops-cli.md#worktrees)). Bind a child
with `bind CHECKOUT_UID --chat <child CH->` (no warning: it is your child).
If you give children isolated checkouts, the parent that creates a worktree owns
removing it. Nothing else cleans them up, so forgotten ones pile up by the
gigabyte.

- Before your final reply, remove each worktree you created whose work is
  committed (or that the user discarded) with `python -m api.worktrees remove
  CHECKOUT_UID`. It refuses dirty checkouts and deletes the worktree's branch,
  so only remove after the branch is merged or discarded.
- Never `--force`-remove a worktree with uncommitted changes. Keep it, and list
  its path and why in the parent reply so the user can decide.
- `python -m api.worktrees list --project-id N` is the audit. Register a
  leftover plain-git worktree with `adopt` before cleaning it up.

## Shell environment

Examples run from the Cuttle repository root with the project venv. POSIX
examples set `PYTHONPATH=src` per invocation. For Windows PowerShell, set
the path and use the Windows interpreter; for example:

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m api.subagents --help
```

Use PowerShell backticks for multiline continuation and single-quoted JSON
arguments; POSIX examples use backslashes for continuation.

Windows PowerShell spawn (substitute the actual parent chat):

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m api.subagents spawn --parent CH-000535 --wait --json --collect all --child '{"title":"Scout","agent":"cursor","message":"Inspect the requested change."}'
```

# Sub-agents (child chats)

Any Cuttle chat can spawn **real child chats**. Each child is a normal
`chat_sessions` row (`origin=subagent`, `parent_session_id` set) that you can
open from history. This is **not** `/coordinate` (synthetic worker ids) and
**not** a Cursor CLI background subagent (those die when the parent turn ends).

Library: `api.subagents`. CLI: `python -m api.subagents`.

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
`profile` (catalog id or inline `{name,avatar,agent}`), `avatar`.

`--children-json '[...]'` also works.

Always pass `--json` when a parent agent will parse the result.

### Collect (how the parent waits)

| `--collect` | Behavior |
|---|---|
| `all` (default) | Run in parallel; wait for every child |
| `first` | Parallel; first completed reply wins; cancel the rest |
| `serial` | Run one after another in `--child` order |

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
deadline-interrupt an individual synchronous turn; cancellation is
cooperative (rows flip terminal, runners that ignore cancellation run until
the supervising process exits). `message --wait` runs the follow-up turn to
completion — its `--timeout` is accepted but not enforced on the turn.
`wait` is observation-only: it never starts pending work and never cancels
another owner's batch. Crash/SIGKILL recovery (rows left active with no
process at all) is explicitly deferred: inspect with `status`, terminally
stop with `cancel`.

`--route` asks the existing Cuttle router to pick `agent`+`model` for children
that omitted them (or set `"route": true`).

`--watch` writes `/output/subagents-<id>-status.json` with an overall bar plus
one worker bar per child (emit a watch card from the parent reply if the job is
long). `--tasks` pins a parent Tasks gizmo with one item per child.

## Other verbs

```bash
PYTHONPATH=src .venv/bin/python -m api.subagents status --parent CH-000535 --json
PYTHONPATH=src .venv/bin/python -m api.subagents status --batch "<batch-id>" --json
PYTHONPATH=src .venv/bin/python -m api.subagents wait "<batch-id>" --json
PYTHONPATH=src .venv/bin/python -m api.subagents message --session CH-000540 --text "follow up" --wait --json
PYTHONPATH=src .venv/bin/python -m api.subagents cancel --batch "<batch-id>" --json
PYTHONPATH=src .venv/bin/python -m api.subagents cancel --session CH-000540 --json
PYTHONPATH=src .venv/bin/python -m api.subagents list --parent CH-000535 --json
PYTHONPATH=src .venv/bin/python -m api.subagents close "<batch-id>" --json
PYTHONPATH=src .venv/bin/python -m api.subagents profiles list --json
```

`--session` / `--parent` accept `CH-000535` or a numeric id. Tests may pass `--db`.

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
Click opens the child chat.

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

## Parent reply

After `--wait`, synthesize **one** answer in the parent chat. Cite children with
bare `CH-000540` tokens (never markdown-wrapped). Do not dump every child
transcript unless the user asks.

## Git worktrees for children

Cuttle does not create worktrees. If you give children isolated checkouts, the
parent that creates a worktree owns removing it. Nothing else cleans them up, so
forgotten ones pile up by the gigabyte.

- Create under `{project}/temp/worktrees/<parent CH-handle>-<slug>`, never
  elsewhere, so they are findable and gitignored.
- Before your final reply, remove each worktree you created whose work is
  committed (or that the user discarded): check `git -C <path> status
  --porcelain` is empty, then `git worktree remove <path>`, then
  `git worktree prune`. Delete a branch you created for it once it is merged.
- Never `--force`-remove a worktree with uncommitted changes. Keep it, and list
  its path and why in the parent reply so the user can decide.
- `git worktree list` is the audit; anything under `temp/worktrees/` whose
  parent chat is finished is a leftover.

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

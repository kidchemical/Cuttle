# Sub-agents (child chats)

Any Cuttle chat can spawn **real child chats**. Each child is a normal
`chat_sessions` row (`origin=subagent`, `parent_session_id` set) that you can
open from history. This is **not** `/coordinate` (synthetic worker ids) and
**not** a Cursor CLI background subagent (those die when the parent turn ends).

Library: `api.subagents`. CLI: `python -m api.subagents`.

## Spawn

```bash
.venv\Scripts\python.exe -m api.subagents spawn --parent CH-000535 --wait --json \
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

`--wait` blocks the CLI until this round finishes (use it for one-shot votes).
Without `--wait`, spawn returns handles immediately; poll with `status` / `wait`.

`--timeout` seconds (default 900).

`--route` asks the existing Cuttle router to pick `agent`+`model` for children
that omitted them (or set `"route": true`).

`--watch` writes `/output/subagents-<id>-status.json` with an overall bar plus
one worker bar per child (emit a watch card from the parent reply if the job is
long). `--tasks` pins a parent Tasks widget with one item per child.

## Other verbs

```bash
python -m api.subagents status --parent CH-000535 --json
python -m api.subagents status --batch <batch-id> --json
python -m api.subagents wait <batch-id> --json
python -m api.subagents message --session CH-000540 --text "follow up" --wait --json
python -m api.subagents cancel --batch <batch-id> --json
python -m api.subagents cancel --session CH-000540 --json
python -m api.subagents list --parent CH-000535 --json
python -m api.subagents close <batch-id> --json
python -m api.subagents profiles list --json
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
python -m api.subagents profiles list --json
python -m api.subagents profiles get scout --json
python -m api.subagents profiles save --id dinner-judge --name "Dinner Judge" --avatar "🍽️" --agent cursor --model grok-4.6 --effort low --json
python -m api.subagents profiles delete --id dinner-judge --json
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

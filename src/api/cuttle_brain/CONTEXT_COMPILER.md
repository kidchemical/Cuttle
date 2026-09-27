# Context Compiler (Cuttle Brain)

## Name

**Context Compiler** — precise about the job (assemble an envelope for the next
agent turn). It is the first solid component under **Cuttle Brain**, the umbrella
for agent-agnostic cognition sockets (context today; memory retrieval later).
Do not treat "Brain" as a single CLI product or a second chat persona. Layers
should become registered plugs the same way tentacles are folders — see
[`MODULARITY.md`](../../docs/guides/MODULARITY.md). There is **no** product
Cuttle CLI; `python -m api.cuttle_brain compile` is a debug aid only.

## What it is / is not

| Is | Is not |
|---|---|
| A library the harness kernel calls every turn | A replacement for Cursor/Codex/OpenCode |
| Cuttle-owned source of truth for shared context | A mirror of each vendor's skill registry |
| Optional debug compile helper | A product CLI, or something agents must shell out to on every turn |

Agents **may** call `python -m api.cuttle_brain compile …` when they need to
inspect the envelope. Day-to-day, Flask injects it — no extra round-trip.

## Canonical layers (order)

1. **Core contract** — versioned Cuttle UI / headless-turn rules (`cuttle_ui_capabilities`).
2. **Cuttle hub rules** — `{Cuttle}/.cuttle/rules/*.md` (global guidelines for every registered project).
3. **Project rules** — `{project}/.cuttle/rules/*.md` (always-on guidelines for that project).
4. **Profile** — `standard` today; room for coordination / supervision later.
5. **Runtime** — inventory of commands/docs/actions (hub + project); optional handoff delta; chat-store hint.
6. **Ranked context (optional)** — Jev may inject 1–3 extra skill/doc snippets for this prompt (`python -m api.jev rank`). Always-on rules still win on conflict.
7. **User request** — the actual turn prompt (never truncated by the compiler).

## How this relates to `.cuttle/`

| Path | Role vs compiler |
|---|---|
| `{Cuttle}/.cuttle/rules/` | **Always-on global** guidelines — compiled for every registered project. |
| `{Cuttle}/.cuttle/personal/` | **Install-local overlay** (gitignored) — same layout; same-relative-path files win. |
| `{project}/.cuttle/rules/` | **Always-on project** guidelines — compiled after hub rules. |
| `commands/*.md` | **On-demand** via `/name` expand (`project_commands`). Listed in inventory; not dumped every turn. |
| `docs/` | Runbooks — inventory only; agents open when relevant (hub `discord.md` is canonical for Discord). |
| `actions/` | Allowlisted side effects — inventory of action ids. |
| `agents/` | Drop-in harness connectors — discovery, not context text. |
| `memory/` (future) | Retrieved snippets via Brain; not a full dump. |

Native vendor files (Cursor skills, etc.) stay optional compatibility —
never the Cuttle source of truth. Repo-level agent brief is `AGENTS.md`.

## Hot-swap / resume

Cuttle owns the **neutral chat transcript** (SQLite). Each agent keeps its own
native resume id for this chat.

- **Fresh session** → full Context Compiler envelope; snapshot recorded.
- **Same agent + resume** → bare user prompt. If hub/project rules or inventory
  changed since that snapshot, prepend a **context delta** (changed rules + new
  docs/actions only — not the full briefing).
- **Agent switch** → target agent's resume when available; handoff transcript delta;
  plus context delta when rules/inventory drifted.
- **Session reset** (`new` / `clear`) → clears native resume and stored snapshot;
  next turn is a fresh full envelope.

See `context_delta.py`.

## Transport

Adapters declare how they accept compiled context (today: lossless prompt prefix /
stdin). Prefer native system/developer channels when a CLI supports them; never
silently truncate.

When the CLI has no separate system channel, the compiler still demarcates:

* envelope in `<cuttle_context>…</cuttle_context>` with an explicit “not the user
  speaking / do not acknowledge” contract
* user text under `## User request`

Live smoke `test_live_no_envelope_narration` (CH-000150-8) fails replies that
meta-talk the briefing instead of answering the short user ask.

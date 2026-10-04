# Context Compiler (Cuttle Brain)

## Name

**Context Compiler** — precise about the job (assemble an envelope for the next
agent turn). It is the first solid component under **Cuttle Brain**, the umbrella
for agent-agnostic cognition sockets (context today; memory retrieval later).
Do not treat "Brain" as a single CLI product or a second chat persona. Layers
should become registered plugs the same way tentacles are folders — see
[`MODULARITY.md`](../../../docs/guides/MODULARITY.md). There is **no** product
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
2. **Cuttle global rules** — `{Cuttle}/.cuttle_global/rules/*.md` (global guidelines for every registered project).
3. **Project rules** — `{project}/.cuttle/rules/*.md` (always-on guidelines for that project; for the Cuttle repo itself this is Cuttle-only rules, never guest etiquette).
4. **Profile** — `standard` today; room for coordination / supervision later.
5. **Runtime** — inventory of commands/docs/actions (global + project); optional handoff delta; chat-store hint.
6. **Ranked context (optional)** — Jev may inject 1–3 extra skill/doc snippets for this prompt (`python -m api.jev rank`). Always-on rules still win on conflict.
7. **User request** — the actual turn prompt (never truncated by the compiler).

## How this relates to `.cuttle/`

| Path | Role vs compiler |
|---|---|
| `{Cuttle}/.cuttle_global/rules/` | **Always-on global** guidelines — compiled for every registered project. |
| `{Cuttle}/.cuttle_global/personal/` | **Install-local global overlay** (gitignored) — same layout; personal markdown appends as a delta, other files win by basename. |
| `{project}/.cuttle/rules/` | **Always-on project** guidelines — compiled after global rules. |
| `{Cuttle}/.cuttle/rules/` | **Cuttle-repo-only** rules — compiled only when the chat targets Cuttle itself. |
| `commands/*.md` | **On-demand** via `/name` expand (`project_commands`). Listed in inventory; not dumped every turn. |
| `docs/` | Runbooks — inventory only; agents open when relevant (global `discord.md` is canonical for Discord). |
| `actions/` | Allowlisted side effects — inventory of action ids. |
| `agents/` | Drop-in harness connectors — discovery, not context text. |
| `memory/` (future) | Retrieved snippets via Brain; not a full dump. |

Native vendor files (Cursor skills, etc.) stay optional compatibility —
never the Cuttle source of truth. Repo-level agent brief is `AGENTS.md`.

## Hot-swap / resume

Cuttle owns the **neutral chat transcript** (SQLite). Each agent keeps its own
native resume id for this chat.

- **Fresh session** → full Context Compiler envelope; snapshot recorded.
- **Same agent + resume** → bare user prompt. If global/project rules or inventory
  changed since that snapshot, prepend a **context delta** (changed rules + new
  docs/actions only — not the full briefing).
- **Agent switch / missed turns** → target agent's resume when available, plus every
  chat message after that agent's *seen cursor* (the newest message it saw on its
  last completed turn) minus its own reply and the prompt being sent now. This
  covers other agents, plain LLM / router replies, and failed turns. Newest rows
  win a char budget; older ones are counted as omitted with a `chat_cli` pointer.
  Settings/meta commands never move a cursor. Plus a context delta when
  rules/inventory drifted. See `handoff.py`.
- **No native resume** (`resume: false`, e.g. DeepSeek) → full envelope plus the
  recent conversation every turn ("Conversation so far").
- **Compaction** → when an adapter reports `context_compacted` (Codex
  `contextCompaction`, Claude transcript `compact_boundary`) or the user compacts
  via Cuttle, the stored snapshot is dropped and the next turn re-sends the full
  briefing.
- **Session reset** (`new` / `clear`) → clears native resume and stored snapshot;
  next turn is a fresh full envelope. Chat delete forgets all Brain state
  (`state.forget_chat`); `python -m api.cuttle_brain prune` sweeps orphans.

See `context_delta.py`.

## Metrics

Every harness turn appends a row to `src/data/brain/context_metrics.db`
(`metrics.py`): briefing mode, size per layer, handoff size, agent-reported
window fill and limit, compaction. The **Context** dashboard reads it
(`python -m api.cuttle_brain metrics list|backfill`).

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

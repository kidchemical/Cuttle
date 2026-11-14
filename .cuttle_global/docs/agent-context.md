# Agent context gauge + compact

Composer radial for **Cursor**, **Muse**, **OpenCode**, **Codex**, **Antigravity**,
**Hermes**, **Claude Code**, and **DeepSeek** sticky agents. Shows estimated context
fill and a **Compact** action when the CLI supports one.

## What it shows

Fill % ≈ **peak / live occupancy** ÷ model **context limit**.

Billing totals with huge `cache_read` are **never** painted as fill (Cursor,
Codex, Claude, Hermes, Antigravity, Muse). When only an aggregate is available the
gauge shows 0% with a hint instead of a false 100%.

| Agent | Fill source | Compact |
|---|---|---|
| Cursor | Stream peak `context_tokens` (distrusts billing aggregates) | `agent -p --resume … "/summarize"` |
| Muse | On-disk MSP view | Low compaction thresholds on `muse exec` |
| OpenCode | Last-step `context_tokens` | `opencode run --session … --command compact` |
| Codex | App-server `tokenUsage.last` + `modelContextWindow` | `thread/compact/start` |
| Claude | JSON `usage` / snapshot (distrusts aggregates) | `claude -p --resume … "/compact"` |
| Hermes | Active non-compacted transcript in `state.db` | `hermes chat -Q -q "/compact" --resume …` |
| Antigravity | Last-turn JSON usage | *(none — auto-compacts)* |
| DeepSeek | Last model-call input, including cache | *(none — headless is one-shot)* |

**Codex:** prefers live app-server occupancy after each turn / on gauge open.
Turn-completed `input_tokens` with huge `cached_input_tokens` is billing, not fill.

**Hermes:** quiet mode does not print tokens; occupancy is estimated from the
active transcript (`compacted=0` rows). Session `input_tokens` is cumulative billing.

**DeepSeek:** official headless profile creates a fresh session every call — no
`--resume` in `dsh --profile headless`. Gauge is last-turn only; Compact hidden.

**Antigravity:** interactive `/context` exists; there is no documented headless
`/compact`. Auto-compaction is internal.

Limits come from (in order):

1. Model id / label hints (`[context=1m]`, `1M Thinking`)
2. models.dev `limit.context` (pricing cache)
3. Claude only: the full model id from the CLI transcript (the chat stores the alias, e.g. `fable`)
4. Agent defaults (Cursor/Muse/Antigravity **1M**, Codex **272k**,
   OpenCode/Hermes/Claude **200k**, DeepSeek **128k**)

## UI

- Radial circle in the composer actions (left of Enhance)
- Visible when a supported agent badge is sticky
- Click → details + **Compact** (when `compact_available`)
- Colors: green &lt;60%, amber 60–85%, red ≥85%
- **Compact** while a reply is generating (or while the agent CLI is still alive, e.g. a Claude
  background wait) queues it: a removable row in the queued panel, run when the agent is idle and
  before queued messages. Client-side, one per chat, not kept across a page reload.

## API

```http
GET  /api/agent-context?agent=cursor|muse|opencode|codex|antigravity|hermes|claude|deepseek&session=<id>&path=<cwd>
GET  /api/agent-context?…&live=1   # Codex: force app-server occupancy refresh
POST /api/agent-context/compact
{"agent":"claude","session":"<id>","path":"<cwd>","mode":"native|fresh"}
```

## Compact paths

| Agent | Method |
|---|---|
| Cursor | `agent -p --resume … "/summarize"` (aliases `/compact`, `/compress`) |
| Muse | `muse exec` with low `--context-compaction-*-threshold` (no headless `/compact`) |
| OpenCode | `opencode run --session … --command compact` (best-effort; core `/compact` has known flakiness — auto-compact still helps) |
| Codex | `codex app-server` JSON-RPC: `thread/resume` → `thread/compact/start` |
| Hermes | `hermes chat -Q -q "/compact" --resume …` (`/compact` aliases `/compress`) |
| Claude Code | `claude -p --resume … "/compact"` |
| Antigravity | *(none)* — gauge only |
| DeepSeek | *(none)* — headless one-shot |

**Not used for Codex:** `codex exec resume … "/compact"` — slash commands are
unreliable in exec mode (often treated as ordinary prompt text).

**OpenCode gauge:** each `step_finish` overwrites `context_tokens` with
`tokens.input + tokens.cache.read + tokens.cache.write`, including fully cached
steps. Summed `prompt_tokens` stay for billing only. DeepSeek uses the same
disjoint input/cache convention and stamps its last `step_end` occupancy.

**Muse gauge:** MSP view on disk (`tokenUsage` / `context_anchor`). Pure
headless sessions that never wrote MSP snapshots may show 0% until the next
turn that materializes a view.
The current MSP view replaces older readings even when smaller after compaction.

## Compaction and the Cuttle briefing

The Context Compiler sends the full briefing once per native session, then
change-deltas against an acknowledged receipt (`cuttle_brain.context_delta`).
A compaction can summarize that briefing away, so every compaction drops the
receipt and the next turn re-sends the full briefing:

- manual Compact (gauge, `/cursor /compact`, `chat_cli compact`);
- `context_compacted` reported by the adapter (Claude transcript boundaries,
  Codex app-server);
- fallback for CLIs that auto-compact silently (Cursor, OpenCode, Hermes,
  Muse): on the **same** native session, a window fill at or below 60% of the
  previous turn's (previous ≥ 20k tokens, `cuttle_brain.metrics.fill_dropped`)
  counts as a compaction.

The handoff seen-cursor is kept: the conversation lives in the native summary.

**Managed (fresh) compact** — `POST /api/agent-context/compact` with
`"mode": "fresh"` / `chat_cli compact CH-… --mode fresh`: no summary; the chat's
native session is discarded (`clear_resume`), the receipt dropped and the
agent's seen-cursor replaced by a `fresh` mark. The next turn starts a new
session with the full briefing (rules, Tasks digest, orchestration brief) and
the recent transcript plus earlier user messages under a "managed compact"
notice. Same chat, handle, links and supervision. Works for any harness with
resume (also Antigravity); refused while a CLI is live in the chat.

**Agents compacting other chats** (sub-agents, supervised chats):
`python -m api.chat_cli context|compact CH-…` (`chat-history.md`), fill in
`subagents status --context` and in supervision snapshots/check-ins.

## Auto-compaction (experimental flag `auto_compact`)

Cuttle compacts an agent's session **between turns**, before its CLI would
compact mid-task near the window edge. Owner: `src/api/auto_compact/`.

- **When:** after a turn ends and a quiet period passes (default 20 s; a
  message sent meanwhile just starts the next turn), if the fill is at or over
  the threshold **or** the token cap, whichever comes first. Defaults:
  ordinary chats 70% / 300k, agent-driven chats 60% / 250k.
- **Agent-driven ("managed") chats:** supervised targets, supervisor chats,
  sub-agent children, orchestrator-bound chats. Sub-agent children compact
  just before their next turn (`api.subagents.turns` preflight), because their
  turns can run in the parent's CLI process where Flask sees no turn end.
- **Tasks milestone:** an item of the chat's own Tasks list checked off since
  its last turn lowers the threshold for that turn end (default 40% / 150k).
- **Never:** mid-turn, while a CLI still runs in the chat (Claude background
  wait: retried every minute), right after a compact with no new turn, or on
  an unreliable reading (billing aggregate / no usage yet).
- **How:** `auto` uses the CLI's own compact; when the next turn still reads
  over the threshold it starts a fresh session instead. CLIs without a
  headless compact (Antigravity) are left to manage themselves unless the mode
  is `fresh`. Failures back off (1, 2, 4, 8 turns) and stop after 4 in a row
  until a setting changes. Each compact holds the chat busy with a live
  status and leaves a `compact` timeline notice.
- **Settings:** Settings → Agents → Auto-compaction (`/api/settings/auto-compact`,
  key `auto_compact`). Per chat: the composer gauge popover (Default / On /
  Off, threshold, milestones), stored in the Cuttle home. Supervision
  check-ins tell the supervisor the target is covered.
- **CLI:** `python -m api.auto_compact status CH-…` (policy + dry-run
  decision), `set CH-… --enabled on|off|inherit --percent N|inherit
  --milestone on|off|inherit`, `history CH-…`, `settings [--set JSON]`.
  `CUTTLE_AUTO_COMPACT=0` stops the Flask trigger.

## Turn usage footers

Footers label total input and its cached portion as “cached (included)”. Muse
and Codex native input includes cache; Cursor, Claude, OpenCode and DeepSeek
report disjoint input/cache buckets, which the footer adds once. Each adapter
stamps `cache_inclusive` for pricing and display. Unknown conventions retain
“reported input” and “cache read” labels. Saved older footers use the harness
badge where its convention is known; historical billing records are not rewritten.
Estimated costs retain `cost_estimated` through the kernel and show `~$`.

**Claude Code:** native `claude -p --output-format stream-json --verbose
--include-partial-messages` against the project
cwd (no sandbox mirror). Resume IDs live in `claude_cli_session_map.json`.
Compact is refused while the chat still has a live Claude CLI (running turn or
detached background wait): that process holds the full window in memory, so a
second `--resume` would only compact the file. After a compact the gauge reads
the boundary's `postTokens` (snapshot flagged `compacted`) until the next turn.

Slash: `/cursor /compact` (also `/summarize`, `/compress`).

## Other harnesses

**Removed:** Gemini CLI connector — Google deprecated individual Gemini CLI in
favor of Antigravity CLI (`agy`) as of 2026-06-18. Use `/antigravity`.

## Cursor ACP?

Cursor ACP (`agent acp`) is a full JSON-RPC client protocol. It would not improve
compact today (no documented compact RPC; `/summarize` via `-p --resume` is the
official path) and would mean rewriting Cuttle’s Cursor harness. Keep `-p` +
stream-json for turns; revisit ACP only if we need structured session control
beyond what print mode already gives.

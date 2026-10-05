# Agent context gauge + compact

Composer radial for **Cursor**, **Muse**, **OpenCode**, **Codex**, **Antigravity**,
**Hermes**, **Claude Code**, and **DeepSeek** sticky agents. Shows estimated context
fill and a **Compact** action when the CLI supports one.

## What it shows

Fill % ≈ **peak / live occupancy** ÷ model **context limit**.

Billing totals with huge `cache_read` are **never** painted as fill (Cursor,
Codex, Claude, Hermes, Antigravity). When only an aggregate is available the
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
| DeepSeek | Last-turn usage only | *(none — headless is one-shot)* |

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
3. Agent defaults (Cursor/Muse/Antigravity **1M**, Codex **272k**,
   OpenCode/Hermes/Claude **200k**, DeepSeek **128k**)

## UI

- Radial circle in the composer actions (left of Enhance)
- Visible when a supported agent badge is sticky
- Click → details + **Compact** (when `compact_available`)
- Colors: green &lt;60%, amber 60–85%, red ≥85%

## API

```http
GET  /api/agent-context?agent=cursor|muse|opencode|codex|antigravity|hermes|claude|deepseek&session=<id>&path=<cwd>
GET  /api/agent-context?…&live=1   # Codex: force app-server occupancy refresh
POST /api/agent-context/compact
{"agent":"claude","session":"<id>","path":"<cwd>"}
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

**OpenCode gauge:** each `step_finish.tokens.input` overwrites `context_tokens`
(last step = fill). Summed `prompt_tokens` stay for billing only.

**Muse gauge:** MSP view on disk (`tokenUsage` / `context_anchor`). Pure
headless sessions that never wrote MSP snapshots may show 0% until the next
turn that materializes a view.

**Claude Code:** native `claude -p --output-format stream-json --verbose
--include-partial-messages` against the project
cwd (no sandbox mirror). Resume IDs live in `claude_cli_session_map.json`.

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

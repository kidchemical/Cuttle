# Agent Router

Cuttle’s **agent router** sits above sticky-slash agent selection. When a chat has no selected agent/model, the router picks which agent CLI and model should run the task.

The router is a **socket**: the plug can be a cheap cloud LLM, a local LLM, a preference table, or another harness used only as the routing brain. Product thesis: [`MODULARITY.md`](./MODULARITY.md). Work items: [`TODO_AGENT_ROUTER.md`](../../../TODO_AGENT_ROUTER.md).

This is **not** the pipeline `tool-router` node (that chooses LLM backends inside a pipeline). The agent router chooses **execution harnesses** such as Cursor Agent CLI, Codex, or DeepSeek Harness.

## Router provider vs execution target

| Concept | Meaning |
|---|---|
| **Router provider** | The model/brain that *decides* where to send the task (`api` / `local` / `agent` / `off`) |
| **Execution target** | The agent CLI + model that *does* the work (e.g. Cursor Auto, Cursor Grok 4.6, Codex) |
| **Session selection** | A sticky agent already chosen for the chat — manually or via a starred command. **Bypasses routing.** |
| **Fallback chain** | Ordered alternatives when a target cannot run (quota, auth, missing CLI, …) |
| **Escalation** | Retry once with a stronger model after an observable *task* failure |

## Modes

- `off` — routing disabled; existing Cuttle behavior (pipelines / starred sticky agents)
- `api` — cheap routing brain (default: OpenAI `gpt-4o-mini`). Set `/router api model jev` to use TypeSafe Jev instead.
- `local` — schema-ready; configure endpoint/model (not fully implemented yet)
- `agent` — schema-ready; agent CLI as brain (not fully implemented; recursion-guarded)

Default mode is **`api`**. If the routing API fails, Cuttle does **not** fail the user task — it uses the deterministic default: **Cursor Agent / Auto**.

## Slash commands

```
/router status
/router mode off|api|local|agent
/router api model <model>
/router local model <model>
/router local endpoint <url>
/router agent <agent>
/router agent model <model>
/router default <agent> <model>
/router fallback add <agent> <model>
/router fallback remove <agent> <model>
/router fallback list
/router metrics summary [days]
/router feedback good|bad [decision_id]
/router reset

/route <agent> <model> <prompt>   # one-task override
/retry frontier                  # retry last failure with escalation target
/retry fallback                  # retry last failure with first fallback
```

`/router status` shows mode, provider/model, default + escalation targets, fallbacks, and whether **this session** will invoke the router (or what selected agent caused a bypass).

## Session / starred behavior

Starred sticky commands (e.g. Cursor Agent) are executed in new chats as normal commands. That selects an agent for the session, so the router is **bypassed** — same as typing `/cursor` yourself.

To use the router on clean sessions: unstar sticky agent chips and leave the composer without an agent badge. `/router mode off` restores prior clean-session/default pipeline behavior.

The router never overwrites an already-selected session agent/model unless you explicitly `/route` or clear that selection.

## Default policy (initial)

1. Prefer **Cursor Agent CLI — Auto** for ordinary low/medium coding work (currently the preferred low-cost path).
2. Escalate once to **Cursor Agent — Grok 4.6** on observable Auto *task* failures (or when Auto cannot start and Grok is next).
3. On Grok **transport** failures (budget/quota/auth/unavailable), walk the configured **Codex** fallback (and further fallbacks).
4. If every target fails, stop and report each attempt — no unbounded loops.

Escalation is only triggered by observable failures (CLI exit/`[FAIL]`, timeouts, explicit errors) or `/retry` — not by guessing that an answer was “wrong.”

## Configuration

Stored in `src/settings.json` under `agent_router` (via `SettingsManager`), same persistence path as starred slash commands and inference preferences.

## Use cases — the declared routing table

Before any learning, the router honors **declared preferences**: a list of
*use-case blocks* mapping a kind of work to the agent/model you want for it.
When a turn matches the first enabled block, its targets **override the
routing brain's choice** (authority order draft: explicit selection → session
pin → table → brain/learning → defaults).

```jsonc
// settings.json → agent_router.use_cases
{
  "id": "frontier-coding",
  "name": "Frontier coding model",
  "description": "Hard coding / debugging / architecture work",
  "enabled": true,
  "priority": 30,                       // lower = matched first (simple → complex)
  "criteria": {
    "task_types": ["coding", "debugging", "architecture"],  // [] = any
    "difficulties": ["high"],           // [] = any
    "code_changes": null,               // null = any, true/false = filter
    "keywords": []                      // any substring match; [] = any
  },
  "routing": {
    "targets": [                        // ordered chain — no fixed tiers
      {"agent": "cursor", "model": "grok-4.6"},   // 1st runs the task
      {"agent": "codex",  "model": ""}            // next on failure; add as many as you want
    ],
    "never_use":  [{"agent": "hermes", "model": ""}]
  }
}
```

The first target in the chain runs the task; later entries are tried in order
when earlier ones fail (task failure escalates to the next hop, transport
failure walks the remaining chain). The legacy
`preferred` / `escalation` / `fallbacks` fields are still read and flatten
into the chain on load.

A fresh install is seeded with three tested blocks: *General chat / simple
requests*, *Frontier coding model*, and *Coding model*. You (or any agent) can
edit `agent_router.use_cases` directly in `settings.json` — the router and the
Router page always re-read the file from disk.

**Editing surfaces:**

- **Agents in chat** — edit the JSON above (preferred path; it is the same
  source of truth), or call `PUT /api/router/config` with `{"use_cases": [...]}`
- **Router page** — `/router_editor.html` (the old pipeline Node Editor page
  was rebuilt into this): core routing, use-case cards, and target health.
  `/node_editor.html` redirects there.

## Quality-regression signals ("is it sucking?") — basic

Per target `(agent, model)`, the router compares the recent fail rate
(transport + task, 6h window) against its own baseline (14 days). A sudden,
significant rise (spike rule or two-proportion z ≥ 2) is a **quality
regression signal** — never an accusation:

- the target is **demoted** for 30 minutes (`agent_router.demotions` in
  settings.json) and the engine, escalation, and fallback chains route around it
- a demotion requires **new** failure evidence to extend — a target recovers
  by simply not failing again (or by TTL expiry)
- `cancelled` turns are never counted as quality evidence
- events: `[AGENT-ROUTER] event=quality_drift` / `event=quality_recovered`

Manual control: `POST /api/router/health/refresh` re-evaluates;
`POST /api/router/demotion/clear` re-promotes a target; the Router page shows
7-day outcomes, drift flags, and a **Recheck** button.

### What triggers a reroute — and what does not

| Signal | Example | Router reaction |
|---|---|---|
| Transport failure | quota, auth, missing CLI, 429 | walks the target chain |
| Observable task failure | nonzero exit, `[FAIL]`, explicit errors | escalates one hop |
| User cancellation | you pressed Stop | terminal — never rerouted, never counted |
| Tool call failing *inside* the agent's run | agent retries a test | **no** router involvement — that's the agent's own loop |
| **Silent failure** (claims success, work is wrong) | "fix this bug" → "done" → it isn't | see below |

### Silent-failure escalation (repeated prompts)

A "completed but actually broken" turn produces no failure signal — exit 0, no
`[FAIL]`. The only evidence is **you re-sending the same request**. The router
treats a near-identical re-prompt in the same session (24h window, ≥0.90
similarity or containment) as the prior attempt failing:

- the next attempt **escalates one hop** up the use case's target chain per repeat
- the prior "successful" attempt is rated `bad` in the outcomes store — real
  signal for later analysis, zero tokens, no LLM judge
- the escalated run receives a note explaining the prior attempt is unverified
- a re-ask after you **cancelled** is fresh evidence, not a complaint

Purely local string matching on real prompts — no scheduled job, no tokens.

### Frustration detection ("still not fixed")

Rephrased complaints don't match repeat-detection, so the router also watches
for explicit frustration phrases: *"still not fixed"*, *"still broken"*, *"you
said you fixed it"*, *"for the Nth time"*, and similar. On detection:

- **Local correlation (free, instant):** the rage phrase is stripped from the
  complaint and the remainder is string-correlated against the session's prior
  asks — only attempts actually about that issue are flagged `bad`. When the
  complaint carries no issue description, exactly the single most recent prior
  attempt is flagged (never a blind "last N" window).
- **Background investigation (parallel, configurable):** a deep pass is
  handed the full evidence packet — prior asks + outcome metadata — and decides
  exactly which attempts failed, writing good/bad verdicts back to the
  outcomes store. It runs in a background thread, so the escalated task never
  waits for it; one investigation per session at a time. Default judge:
  **Jev** (falls back to Cursor Auto if `TYPESAFE_API_KEY` is missing). Configure
  on the Router page (Frustration analysis) or
  `settings.json → agent_router.rage.investigator`
  (`{"enabled", "agent", "model", "timeout_s"}`).
- the chain escalates the same way repeats do, and compounds across rephrased
  complaints ("still broken" → "it still fails" = repeat #2)
- the escalated run is told the user is frustrated and prior conclusions
  should not be trusted: *re-diagnose from scratch, verify end-to-end*
- fresh bug reports are **not** frustration ("the login is not working" has no
  "still" / "you said" context and does not trigger)
- the phrase list itself is configurable via `agent_router.rage.phrases`;
  `agent_router.rage.enabled: false` disables the whole feature. The local
  correlation pass costs nothing; the investigator agent does spend its own
  tokens — that's what the toggle and agent picker are for.

## Evaluation

Decision-only routing eval (never runs Cursor / Codex / Hermes):

```
/router evaluate <prompt>
/router evaluate batch                 # preview (requires --yes to spend API)
/router evaluate batch baseline --yes
/router evaluate batch baseline --concurrency 3 --yes
/router evaluate batch baseline --repeat 5 --concurrency 3 --yes
```

`--repeat N` re-runs each case N times on the production decision path (still no
executors), keeps every raw decision, and reports per-case stability for task
type / difficulty / target (including Auto↔Grok oscillation and confidence
min/mean/max).

Suites live in `src/api/agent_router/suites/*.json` (tracked in git). Reports are
written under `src/web/logs/router_eval_<suite>_<timestamp>.json` (and a matching
`.md`; those logs stay gitignored).

## Supervised coordination

See [SUPERVISED_COORDINATOR.md](./SUPERVISED_COORDINATOR.md). Strategy type
`supervised` (Codex Sol low → Cursor Auto diet-frontier) is available via
`/coordinate` / `/coordinator`; production auto-routing policy is unchanged.

## Limitations (v1)

- `local` and `agent` router modes are configurable but not fully wired.
- Routed attempts are stored locally in `src/data/router_outcomes.db`; `/router metrics summary`
  shows current totals.
- Drift detection is the **basic** version: fail-rate windows + temporary demotion.
  EWMA/CUSUM tuning, soft signals, and learned weights are not implemented yet.
- Command-based good/bad feedback is available; chat thumbs and automatic retry signals are not yet wired.
- Parallel multi-agent execution is intentionally not supported.
- Semantic quality of successful replies is not auto-scored.

## Future hooks

Routing decisions include a `decision_id` and `source` (`router` / `default` / `escalation` / `fallback` / …) so later telemetry can correlate outcomes without changing the schema.

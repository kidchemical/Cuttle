# Agent Router

Cuttle’s **agent router** sits above sticky-slash agent selection. When a chat has no selected agent/model, the router picks which agent CLI and model should run the task.

The router is a **socket**: the plug can be a cheap cloud LLM, a local LLM, a preference table, or another harness used only as the routing brain. Product thesis: [`MODULARITY.md`](./MODULARITY.md). Work items: [`.cuttle/docs/agent-router-todo.md`](../../.cuttle/docs/agent-router-todo.md).

The agent router chooses **execution harnesses** such as Cursor Agent CLI, Codex, or DeepSeek Harness.

## Router provider vs execution target

| Concept | Meaning |
|---|---|
| **Router provider** | The model/brain that *decides* where to send the task (`api` / `local` / `agent` / `off`) |
| **Execution target** | The agent CLI + model that *does* the work (e.g. Cursor Auto, Cursor Grok 4.6, Codex) |
| **Session selection** | A sticky agent already chosen for the chat — manually or via a starred command. **Bypasses routing.** |
| **Fallback chain** | Ordered alternatives when a target cannot run (quota, auth, missing CLI, …) |
| **Escalation** | A stronger model, used only when you ask (`/retry frontier`) or re-send the same prompt (silent-failure escalation). The router never automatically reruns a failed task. |

## How a turn is routed

The router answers two questions, in this order, and keeps them separate:

1. **What kind of work is this, and how much of it?** (classification)
2. **Which harness is best at that kind of work right now?** (your use-case table)

**Kind of work** (`task_type`) is what decides which harness is good at a turn:

| Lane | Meaning | Example |
|---|---|---|
| `basic_ask` (chat) | conversation, quick general question | "hello", "what's 2+2?" |
| `explain` | question about this codebase / chats / logs; read, don't change | "how does the router pick fallbacks?", "look at CH-000989" |
| `coding` | implement or change code | "add a clear-cache button to settings" |
| `debugging` | something is broken; find the cause and fix it | "why did this hang for 13s?", "the host keeps crashing" |
| `architecture` | design, plans, cross-cutting restructure | "re-imagine the router classification" |
| `research` | external lookup, comparisons | "compare opencode vs claude code" |
| `writing` | prose deliverable | "write the release notes", "summarize yesterday's commits" |
| `ops` | git, restarts, deploys, workers, installs, chores | "push it", "restart flask" |

**Scope** (`difficulty`) is how much work, not how hard the question sounds:
`low` = one quick step, `medium` = normal multi-step task, `high` = broad,
ambiguous, or cross-cutting. Architecture is `high` unless explicitly small.

**Classifier fast path** (`src/api/agent_router/classify.py`): most turns are
obvious from their wording and classify locally in microseconds. At confidence
≥ `agent_router.classifier.fast_path_confidence` (default 0.75) the routing
brain is skipped entirely. Ambiguous turns ("make it pop more") go to the
brain. If the brain is down, the classifier's lane is still used, so the
table can pick a harness. Settings:

```json
"classifier": {"fast_path": true, "fast_path_confidence": 0.75}
```

**Out-of-usage memory** (`src/api/agent_router/quota.py`): when an account
reports quota/usage exhaustion, routing remembers it for the reset time named
in the error (or 3 hours) and puts those targets at the back of the chain. A
Cursor *premium* cooldown leaves Cursor Auto in use. A later success on that
tier clears the cooldown, and so does a Flask restart. `/router status`
lists active cooldowns.

**Budget awareness** (`src/api/agent_router/budget.py`, Router page → Budget
tickbox, `agent_router.budget`). The router reads the same live plan windows
as `/usage` *before* picking: Codex 5-hour/weekly, Claude 5-hour/weekly, and
Cursor included API usage (Auto is not metered by that pool). Accounts at their
limit go to the back of the chain, accounts below `low_headroom` (default 15%)
go behind healthy ones, and the declared order holds otherwise. Fetches run on
a background thread every `refresh_s` (default 300s), so routing never waits
on a vendor API. Muse, OpenCode and Hermes have no usage API and keep their
position. Off by default, because it calls vendor usage endpoints with your
CLI credentials.

**Per-target effort.** Any chain entry, default, escalation or fallback can
carry `"effort"` (e.g. `{"agent": "codex", "model": "gpt-6.1-sol", "effort":
"xhigh"}`). It is validated against the harness manifest's levels and passed to
the CLI as its reasoning effort. Cursor has no separate effort setting; pick
an effort-specific model id such as `cursor-grok-4.6-high`.

**Which brain runs.** Mode `api` ("hosted brain") runs exactly one brain,
picked by the model id. `jev`/`jev-latest` runs Jev (TypeSafe System One via
your TypeSafe or OpenRouter key) and OpenAI is not called. An OpenAI id
(`gpt-4o-mini`, …) runs OpenAI and needs `OPENAI_API_KEY`. With the classifier
fast path on, most turns call neither.

**The routing chip** shows one readable line, e.g.
`writing · normal → Writing & docs (matched “Write the release notes”)`. The
brain's raw scores stay in the decision's `raw` details.

## Modes

- `off` — routing disabled; starred sticky agents and explicit composer badges still apply
- `api` — cheap routing brain (default: OpenAI `gpt-4o-mini`). Set `/router api model jev` to use TypeSafe Jev instead.
- `local` — schema-ready; the local-model brain is not implemented yet, so turns route on the classifier alone
- `agent` — schema-ready; the agent-CLI brain is not implemented yet (recursion-guarded), so turns route on the classifier alone

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

To use the router on clean sessions: unstar sticky agent chips and leave the composer without an agent badge. `/router mode off` turns routing off for those clean sessions.

The router never overwrites an already-selected session agent/model unless you explicitly `/route` or clear that selection.

## Default policy (initial)

1. Prefer **Cursor Agent CLI — Auto** for ordinary low/medium coding work (currently the preferred low-cost path).
2. A **task** failure (the agent ran and reported failure: nonzero exit, `[FAIL]`, tests failed) is **terminal**. You get the agent's own output plus `/retry frontier`, `/retry fallback`, `/route …` hints. Rerunning the same prompt on another model would repeat side effects and hide what happened.
3. A **transport** failure (the target never ran: quota/usage, auth, missing CLI, connection) walks the fallback chain. When Auto cannot start, that chain begins with Grok.
4. **Quota is account-wide.** After an out-of-usage/quota error, the rest of the turn skips that agent's other premium models. For Cursor it tries `cursor`/`auto` first, because Auto draws on its own usage pool and often still runs when premium (API) usage is exhausted.
5. If every target fails, stop and report each attempt with its real error line, with no unbounded loops. Each step ("Router → cursor / grok-4.6…", "… unavailable (…) — trying cursor / auto…") appears in the chat activity bubble.

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
when an earlier one cannot run (transport failure walks the remaining chain;
a task failure stops the turn). The legacy
`preferred` / `escalation` / `fallbacks` fields are still read and flatten
into the chain on load.

A fresh install is seeded with one block per kind of work: *Quick chat*,
*Ops & chores*, *Writing & docs* (Claude first), *Explain the codebase*,
*Research*, *Everyday coding*, *Architecture & design*, *Deep work*
(high-scope coding/debugging/explain), and an *Anything else* catch-all, so
every turn lands in a declared lane. Seeds use agent defaults (empty model),
except Cursor. You (or any agent) can
edit `agent_router.use_cases` directly in `settings.json` — the router and the
Router page always re-read the file from disk.

**Editing surfaces:**

- **Agents in chat** — edit the JSON above (preferred path; it is the same
  source of truth), or call `PUT /api/router/config` with `{"use_cases": [...]}`
- **Router page** — `/router_editor.html`: compact lane table, prompt preview,
  recent decisions, and expandable core routing / budget / target health settings.
  Click a lane to edit its criteria, ordered targets, exclusions and effort.
  Typing in **Try a prompt** uses the local classifier and the production table,
  demotion and availability layers, against saved settings. Ambiguous results are
  marked provisional; **Ask brain** invokes the full decision-only path and may
  spend routing API tokens. Neither path executes a task or records an outcome.
  Unsaved edits are labelled; save them to preview their effect.
  **Recent decisions** polls the latest recorded attempt per routed decision every
  10 seconds while visible (six cards). It excludes sticky/manual telemetry and
  preview decisions; active runs appear once an attempt records its outcome.
  This is an enhancement to the established router editor and ships directly,
  without a separate experimental flag.

Editor-only HTTP endpoints are owner-authorized: `POST /api/router/preview`
(`{"prompt": "…", "consult_brain": false}`; explicit `true` asks the brain)
and read-only `GET /api/router/decisions`. Decision evaluation in chat remains
`/router evaluate <prompt>`.

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
| Observable task failure | nonzero exit, `[FAIL]`, explicit errors | terminal: shows the agent output; `/retry` to escalate |
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
- Routed attempts are stored locally in `src/data/db/router_outcomes.db`; `/router metrics summary`
  shows current totals.
- Drift detection is the **basic** version: fail-rate windows + temporary demotion.
  EWMA/CUSUM tuning, soft signals, and learned weights are not implemented yet.
- Command-based good/bad feedback and chat thumbs are recorded. Repeated prompts and frustration signals inform routing; observable failures and explicit `/retry` drive escalation. These signals do not prove semantic correctness.
- Automatic routing selects attempts and escalation targets sequentially; explicit child chats support parallel multi-agent work.
- Optional dashboard/Jev model judging can score successful turns. This is separate from the router’s failure/escalation policy; routing does not automatically treat every judged answer as a failure.

## Future hooks

Routing decisions include a `decision_id` and `source` (`router` / `default` / `escalation` / `fallback` / …) so later telemetry can correlate outcomes without changing the schema.

### Reply selection chip (experimental)

Enable **Settings → Experimental → Router selection chip**, or use
`PYTHONPATH=src .venv/bin/python -m api.experimental set router_selection_chip on`.
New routed replies replace the existing `🔀` prose headline with a compact chip.
It uses the agent badge’s segmented styling: icon | status | message. The
message shows the routing reason with CSS ellipsis; the tooltip reveals the full
message. A separate normal agent chip shows the final harness, model, and effort
reported by the runner. Unspecified effort stays hidden. Status names are Router,
Reroute (escalation), and Fallback.
Default-agent execution when the routing brain never ran uses **Warning** with the recorded
default-agent recovery message, rather than presenting a routing success. Explicit/manual and sticky choices do
not get an automatic-routing chip. Missing reason metadata produces no invented
explanation. The flag defaults off and respects `CUTTLE_EXPERIMENTAL=0`.

The router dispatch annotates results with `routing_badge`, shaped by
`api.chat_metadata.routing_badge_from_router`. The shared saver persists that
snapshot; sync/SSE and pending/history recovery carry it to `chat_messages`' pure
renderer. The same status renderer presents **Error** for existing failed-reply
classification; regular agent chips retain their existing error/Stop styling.
Previously saved prose headlines remain unchanged. Disabling the flag
restores prose for new turns; recorded chips remain visible in history.

Teardown: remove the registry row and dispatch gate, badge shaper/persistence and
transport field, frontend metadata forwarding/renderer and chip CSS. No separate
route or background polling service is introduced.

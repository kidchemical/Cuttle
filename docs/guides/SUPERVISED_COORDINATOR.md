# Supervised Coordinator

Cuttle’s **supervised-task mode** is a harness-neutral coordination layer above workers.
It is a **routing strategy**, not a second router and not a fake agent name.

**Naming:** here “worker” means a **harness tentacle** (e.g. Cursor Auto) on the same machine — not a **device worker** in the multi-PC mesh. For LAN compute workers see [`CUTTLE_WORKERS.md`](CUTTLE_WORKERS.md).

Initial path:

```text
User ↔ Codex CLI (GPT-5.6 Sol, reasoning low) ↔ Cursor Agent CLI (Auto)
```

## Architecture findings (where this sits)

| Layer | Role | Reuse |
|---|---|---|
| `agent_router` | Chooses *strategy* + execution target | `RoutingDecision.strategy`, dispatch, bypass rules |
| `supervised/` | Durable task state, packets, review loop | New package under router |
| `web_chat_api._run_codex_web_command` / `_run_cursor_web_command` | Actual harness runners | Adapters call these |
| `codex_cli_tool` / resume maps | CLI argv + thread resume | Extended with `model_reasoning_effort` via `-c` |
| `chat_run_registry` | Cancel in-flight worker procs | Used on `/coordinate cancel` |
| Slash palette `controlCommand` | Native chips that never get sticky-prefixed | `/coordinator`, `/coordinate` |

Direct Auto → Grok → Codex fallback remains valid. Supervised mode is composable
(config hooks exist) but **not** enabled in production policy by this change.

## Responsibility boundaries

- **Router** — who / which strategy (direct, supervised, frontier, fallback, ask_user).
- **Coordinator** — plan, synthesize a **delegation packet**, review evidence, choose typed actions.
- **Worker** — implement the packet; return a structured report (+ raw output retained).
- **Cuttle** — durable state, budgets, cancellation, economic gates, telemetry events.

The initial diet-frontier profile sets `may_edit_repo=false` on the coordinator: it
plans/delegates/reviews/communicates; it does not edit the repo itself.

## Strategy typing

`RoutingDecision.strategy` is one of:

- `direct` (default — unchanged behavior)
- `supervised`
- `frontier`
- `fallback`
- `ask_user`

Worker identity remains a normal `ExecutionTarget` (e.g. `cursor`/`auto`). Supervised
is **never** encoded as agent name `supervised`.

## Initial profile: `diet-frontier`

| Role | Harness | Model | Reasoning | Economic source (recorded) |
|---|---|---|---|---|
| Coordinator | Codex CLI | `gpt-5.6-sol` (configurable) | Cuttle `low` → Codex `model_reasoning_effort=low` | `codex_chatgpt_allocation` |
| Worker | Cursor Agent CLI | `auto` | — | `cursor_auto_promo` |
| Reviewer | same as coordinator | — | — | — |
| Max follow-ups | 1 (configurable) | | | |

Codex discovery notes (installed CLI **0.147.0**):

- Model: `-m` / `--model`
- Reasoning: config override `-c model_reasoning_effort="low"` (values: `minimal|low|medium|high|xhigh`)
- Cuttle stores normalized reasoning (`light` aliases to `low`) and translates only in the Codex adapter
- Soft-known Codex model ids are configurable; live model enumeration is not assumed

## Slash commands

```
/coordinator status
/coordinator mode off|supervised
/coordinator profile diet-frontier
/coordinator worker <agent> <model>
/coordinator review-loops <n>
/coordinator reset

/coordinate <prompt>          # manual supervised run (allowed even if mode off)
/coordinate status
/coordinate cancel
/coordinate followup <text>
```

These are **native control commands** (`controlCommand: true`): sticky agent chips
must not wrap them into `/cursor …` prompts.

Settings live under `settings.json` → `supervised_coordinator` (via SettingsManager).

## State machine

```text
created → coordinating → delegated → worker_running → reviewing
    ├─ approve → approved
    ├─ follow_up → follow_up → worker_running → …
    ├─ ask_user → awaiting_user
    ├─ escalate → escalated
    ├─ fail → failed
    ├─ cancel → cancelled
    └─ budget → budget_exhausted
```

Durable JSON: `src/data/workspace/supervised_tasks/<task_id>.json` (+ `_index.json`).
Raw worker stdout: `supervised_tasks/<task_id>/runs/<run_id>.raw.txt` (full text; chat previews may truncate separately).

**Control lane (exactly-once):** parse → authorize (`_resolve_auth_chat_session`) → mutate once → optional `control_request_id` idempotency → terminal return. Recognized control commands never fall through into router/agent dispatch. Transport retries with the same `control_request_id` must not append a second chat pair (`skip_history_persist` / `reconcile_only`).

**Background UX:** One `/coordinate` request owns **one ordinary Cuttle assistant
bubble**. Live status updates that bubble in place (Thinking → Planning → Working →
Reviewing → final) using the **same animated activity indicator as any normal
reply** (`.typing-indicator` + `.typing-status`), driven by `status_text` in the
`<cuttle_supervised_activity>` payload — in-flight phases never write status as
plain body text. Cursor Auto is an internal worker — not a separate chat
participant. Expanded **Activity** disclosure holds status / add-instruction /
cancel (typed `/api/supervised/tasks/<id>/control`). A tiny off-screen chip may
appear only when the canonical bubble is outside the viewport. Terminal completion
shows a non-message “Jump to result” notification (deduped by delivery event id).

**Review packet:** when `parse_ok=true`, the coordinator receives the full structured report (including every finding). Chat previews may truncate; raw artifacts stay complete at `/api/supervised/tasks/<task_id>/runs/<run_id>/raw`.

**Terminal bubble:** the default assistant answer is concise — decision, synthesized
result, important verified evidence, and remaining uncertainty / next action.
Detailed worker output, worktree diagnostics, model/task IDs, timeline, and the
evidence ledger stay inside collapsed **Activity** (Copy details / View worker
report). Copy Text copies the human answer only (strips `<cuttle_supervised_activity>`
transport metadata; user-authored XML/code is preserved).

**Verification modes:** `mutation` | `read_only_code_review` | `research` | `architecture` | `general`. Explicit read-only / inspect / review / identify language selects `read_only_code_review` (or architecture for design reviews). Ambiguous tasks without modification intent stay `general` — not mutation. Read-only reviews use path/symbol spot-checks — do not escalate solely because `git status` cannot prove a static-analysis claim.

**Evidence:** task-start worktree baseline vs end snapshot (`worktree_task_delta`); pre-existing dirty paths are not attributed as worker-caused. Concurrent attribution is always `not_provable`.
Survives Flask restart/reconnect. In-memory thread table is best-effort only.

Correlated IDs: `task_id`, `decision_id`, `coordinator_session_id`, `worker_session_id`,
`run_id`, attempt / review-loop counters.

## Async behavior

1. Coordinator plan runs (Codex).
2. Worker starts in a **background thread** with a dedicated session id (Cursor resume-safe).
3. Kickoff reply returns to the user — Flask request is **not** held open to poll the worker.
4. On worker completion (or failure/cancel), the coordinator is invoked **once** for review.
5. No model-based “are you done?” polling.

### Control lane (must not queue behind the worker)

While a worker runs, these commands execute immediately (frontend bypasses the
pending-prompt queue; backend skips chat busy / router):

- `/coordinate status` — model-free durable (+ live process) snapshot
- `/coordinate cancel` — owned worker process only; idempotent; blocks late approval
- `/coordinate followup <instruction>` — accepted immediately; live Cursor injection
  is **not** supported → persisted as `pending` while `worker_running` / `reviewing` /
  `delegated` until the next safe boundary (never starts a parallel worker during review)
- `/coordinator status` (and other `/coordinator` config) — control lane

Parent chat live status during worker progress uses `active=False` so the UI does
not mark the coordinator conversation as generating.

### Coordinator conversation during worker

Free-form messages (no leading `/`) while a supervised task is active route to the
**coordinator** conversation thread (serialized per `coordinator_session_id`), not
the worker and not the ordinary router. They consume Codex allocation; control
commands do not.

### Independent evidence

Review packets and final UI separate `worker_claimed` from `cuttle_verified` /
`not_verified` programmatic checks (git status/diff, path existence, process
exit). Worker-supplied commands are never executed.

### Test isolation

`CUTTLE_TEST_MODE` / pytest activates fail-closed guards on default Cursor/Codex
runners. Spoofed allow env vars cannot bypass; only `allow_external_runners(...)`.

## Cost controls

- Programmatic status/cancel/index ops use **no** model calls.
- Coordinator and worker invocations are logged separately (`[SUPERVISED]` events).
- Economic source is recorded separately from model identity.
- `require_paid_approval` (default true): profiles with `openai_api` /
  `paid_requires_approval` sources are blocked unless approval is granted.
- Default diet-frontier does **not** silently switch to billable OpenAI API.
- Worker JSON cannot inject permissions, budgets, or settings
  (`sanitize_untrusted_dict` strips forbidden keys; packet permits are force-cleared).

## Policy hooks (inactive by default)

```json
"supervised_as_frontier_fallback": false,
"prefer_supervised_for": []
```

When `supervised_as_frontier_fallback` is later enabled, exhausted frontier/transport
paths may select strategy=`supervised` instead of failing open — still without silent paid.

## Adding another coordinator or worker

1. Implement an adapter with `invoke(prompt, session_id=…, project_path=…, status_queue=…)`.
2. Register a profile under `supervised_coordinator.profiles`.
3. Keep economic_source honest; map reasoning through the adapter only.
4. Do not teach the router a fake agent name — set `RoutingDecision.strategy`.

## Telemetry schema (future dashboard; events already emitted)

```text
[SUPERVISED] event=task_created|delegated|worker_started|worker_completed|
                  review_decision|cancelled|coordinator_invoke|worker_invoke
fields: task_id, decision_id, run_id, attempt, economic_source, model, reasoning, …
```

## Manual smoke test (requires explicit approval)

Do **not** run until approved — consumes Codex allocation + Cursor Auto capacity.

1. Hard-refresh chat UI; confirm `/coordinator` and `/coordinate` palette chips.
2. `/coordinator status` → mode off, profile diet-frontier.
3. `/coordinator mode supervised`
4. `/coordinate Add a one-line comment to README explaining supervised mode`  
   (or another tiny, reversible edit)
5. Confirm kickoff message with task id; `/coordinate status` while worker runs.
6. Wait for a concise terminal answer; open Activity for worker/evidence details.
7. `/coordinate cancel` on a second run to verify cancellation.
8. Optional: `/restart graceful` after a mid-run kill of Flask (daemon-owned) and confirm
   task JSON under `src/data/workspace/supervised_tasks/` still loads via `/coordinate status`.

## Restart

Python changes require a **Flask restart** (daemon-owned `/restart graceful` or
`when-idle`). Do not `taskkill` Flask from an agent chat. Daemon restart not required
unless the daemon itself is wedged.

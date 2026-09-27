# TODO — Agent Router (adaptive quality routing)

Living backlog for making Cuttle’s agent router **complete, measurable, adaptive, and OOB-useful**.  
Canonical product roadmap remains [`docs/ROADMAP.md`](./docs/ROADMAP.md). Product / socket thesis: [`src/docs/guides/MODULARITY.md`](./src/docs/guides/MODULARITY.md). Day-to-day guide: [`src/docs/guides/AGENT_ROUTER.md`](./src/docs/guides/AGENT_ROUTER.md).

**Doc strategy (decision):** keep **one** living file (`.cuttle/docs/agent-router-todo.md`) until the extractable package boundary is real. Do **not** spin up `ROADMAP_AGENT_ROUTER.md` + `DESIGNDOC_AGENT_ROUTER.md` yet — that triples maintenance for the same content. When (if) we cut a standalone repo, *then* promote:

| Need | Where (now) | Later (standalone) |
|------|-------------|--------------------|
| Work items + phases | this file | GitHub Issues + CHANGELOG |
| Architecture / contracts | § Design sketch below | `DESIGN.md` in the package |
| Multi-month product priority | one line under [`docs/ROADMAP.md`](./docs/ROADMAP.md) + [`MODULARITY.md`](./src/docs/guides/MODULARITY.md) | package README “vision” |

---

## Thesis

Cuttle’s router is not “pick the cheapest chat model.” It is an **execution-harness router** that:

1. Sees which agents/models the user actually has (Cursor Auto, Grok, Codex, Claude, Gemini, local, promos, free windows).
2. Routes by **task class + cost + availability**.
3. Records **outcomes**, not just decisions.
4. Detects **silent quality regression** (“it used to crush this; now it’s careless”) — the “**Is it sucking?**” signal — and adapts routing / flags the target.
5. Works **out of the box** on a fresh install, then personalizes from *this user’s* telemetry.
6. Optionally (opt-in) contributes anonymized health signals so the network can spot **provider-wide** dogfooding / capacity demotion vs **local** bad luck.

That last point is the wedge vs gateways that only track uptime/latency/cost.

---

## Modularity and swapping routers

The name is **CuttleRouter**. The router is a **socket**, not a hardcoded brain.

The plug can be:

- a **cheap cloud LLM** (default today: OpenAI `gpt-4o-mini`)
- a **local LLM**
- a **programmatic / preference-table** policy
- **another agent harness** used only as the routing brain (experimental; recursion-guarded)

CuttleRouter is the default plug, not the only one. Same socket idea applies to Brain, knowledge, and reasoning — see [`MODULARITY.md`](./src/docs/guides/MODULARITY.md).

The goal is not to compete with every router project. The goal is a clean plug:

- Cuttle can use **CuttleRouter**, **Switchyard**, **OpenRouter**, or a future experiment without rewriting chat or agent code.
- Another framework can use **CuttleRouter** without installing all of Cuttle.
- Every router receives a consistent description of the task and available agents.
- Every router returns a simple choice: which agent/model should run, plus a short reason.
- Cuttle then runs that choice through the same agent harness and records the result.

This should build on Cuttle’s **agent harness path** (`agent_harness` catalog + shared kernel). OpenCode, Gemini, Antigravity, Cursor, Codex, Muse, Claude, Hermes, and DeepSeek all use it. Chat slash dispatch no longer has per-agent elif forks — only `_match_harness_slash` → kernel. Thin `_run_*_web_command` shims remain for tests / supervised adapters. Pipeline `tool-remote-agent` and router runner wiring still need to stop importing Flask — that is part of closing the tentacle socket.

Switchyard and OpenRouter are useful comparison points and possible plug-ins, not things CuttleRouter must replace.

---

## Current state (honest)

### Done (v1 spine)

- [x] Router provider vs execution target split (`api` brain → Cursor/Codex/… target)
- [x] Sticky / starred session bypass; `sticky_agent: "none"` clears
- [x] Deterministic default on brain failure (Cursor Auto)
- [x] Failure kinds: `transport` / `task` / `cancelled` (cancel is terminal)
- [x] Escalation (Auto → Grok) + ordered fallbacks (e.g. Codex)
- [x] Turn guard / stale-turn discard; no unbounded loops
- [x] Structured `[AGENT-ROUTER]` log events + `decision_id`
- [x] Decision-only eval suites (`/router evaluate…`, baseline suite, stability repeats)
- [x] Supervised coordinator (separate strategy; production policy unchanged)
- [x] Slash control surface (`/router`, `/route`, `/retry`)

### Missing for “complete”

- [x] Durable local outcome store (decisions ↔ attempts ↔ feedback ↔ available cost/latency)
- [x] User-declared routing table (preferences per task kind) — `agent_router.use_cases`; seeded defaults; table overrides the brain; editable by agents in settings.json, via REST, or the Router page (`/router_editor.html`)
- [ ] CuttleRouter packaged as a harness agent (invokable, pinnable, starrable like Cursor)
- [ ] Quality / regret signals beyond hard `[FAIL]` / transport
- [ ] Adaptive policy that **changes** weights from local history
- [ ] OOB catalog of targets discovered from installed CLIs + keys (not only settings defaults) — include DeepSeek Flash when `dsh` + `DEEPSEEK_API_KEY` are present
- [ ] UI: router health / “is it sucking?” views (Vega in chat + Settings page)
- [ ] `local` / `agent` brain modes fully wired
- [ ] Clean package boundary (no `web_chat_api` imports inside core decide/dispatch)
- [ ] Opt-in federated health (privacy, schema, aggregation) — deferred until local loop works

---

## Design sketch (enough to avoid a separate DESIGNDOC)

### Simple component boundary

```
┌─────────────────────────────────────────────────────────────┐
│ Host (Cuttle): chat, harness adapters, UI, settings, auth   │
├─────────────────────────────────────────────────────────────┤
│ CuttleRouter (replaceable component)                        │
│  • receives the task + list of available agents             │
│  • returns the chosen agent/model + reason                  │
│  • learns from recorded results                             │
├─────────────────────────────────────────────────────────────┤
│ Alternative router adapter: Switchyard / OpenRouter / other │
└─────────────────────────────────────────────────────────────┘
```

**Rule:** the router must not depend on Flask, Discord, or the chat UI. Cuttle supplies available agents through the new harness catalog and runs the returned choice through the shared harness kernel.

### Signal model (“Is it sucking?”)

Per `(agent, model)` [and optionally `(task_type, difficulty)`]:

| Signal | Source | Notes |
|--------|--------|-------|
| Transport fail rate | CLI/API errors, 401/402/429, missing binary | Immediate demotion / fallback |
| Task fail rate | `[FAIL]`, nonzero exit, explicit errors | Escalation candidate |
| Soft quality | thumbs, retry-same-prompt, edit-after, user `/retry frontier`, judge samples | Sparse; high value |
| Latency / TTFT | wall clock | Secondary; not the main story |
| Cost / budget remaining | provider usage if available | Triggers tier fallback |
| Drift score | CUSUM / EWMA on a quality proxy vs baseline window | Core “was good → got dumb” detector |
| Cohort flag (opt-in) | anonymized rollup | Distinguishes me-only vs widespread |

**Hard constraint:** never treat `CANCELLED` as quality evidence.

**Privacy default:** local-only store; no prompts in federated payloads (hashes/buckets only). Full prompt retention stays host policy.

### OOB policy (fresh install)

1. Discover available targets (installed CLIs + configured API keys).
2. Seed with **benchmark-backed priors** (our eval suites + published RouterBench-style difficulty bands) — not empty weights.
3. Prefer cheapest capable tier for `low`/`basic_ask`; escalate on task fail; fallback on transport/budget.
4. After N local outcomes (e.g. 20+ per target), priors yield to **personal** EWMA.
5. Surface a quiet health chip: green / yellow (“degraded — routing around”) / red (“unavailable”).

---

## Awaiting your approval — who decides what

Draft authority order (not locked — finalize before Phase 0 is coded):

1. **This message** — `/cursor …`, `/route …`. Always wins.
2. **Pinned / starred agent** — the chat already has an agent. Router is bypassed.
3. **Your routing table (Phase 0)** — your declared preference per kind of task.
4. **Learned adjustments (Phases B–C)** — only inside the limits you allow, and only when adaptation is on.
5. **Built-in defaults** — used only where you haven't expressed a preference.

Also draft (same approval gate):

- Adaptation is a switch (`agent_router.adaptive`). Proposed default: **off** (telemetry reports only until you turn learning on).
- Learned changes never silently rewrite the Phase 0 table — they stay suggestions until you accept them.
- Exact edge cases to settle with you: starring CuttleRouter itself vs starring Cursor; whether the table may demote a target that was preferred but is currently failing; whether `/route` updates the table or is one-shot only.

**Do not treat this section as decided.** When you approve (or rewrite) it, move the final version into Decisions and implement Phase 0 against that.

## Phased TODO

### Phase 0 — Your routing table

Before any telemetry exists, CuttleRouter should already know **your** preferences, because you told it.

A routing table maps a kind of work to the agent/model you want for it, for example: quick questions → cheap fast model; ordinary coding → Cursor Auto; hard debugging → a frontier model; research → whatever you prefer reading. You own this table and can change it any time. How strictly CuttleRouter must obey it vs the brain/learning layers is the authority-order question above — code against the approved order, not this draft.

- [ ] **0.0.** Finalize and approve the authority order + adaptation switch defaults (see section above)
- [x] **0.1.** Preference table stored in settings (`agent_router.use_cases`): use-case blocks with criteria (task types / difficulties / code-changes / keywords) → preferred, escalation, fallbacks, never-use. *(Phase 0 "preferences" naming → implemented as use-case blocks)*
- [x] **0.2.** Seed the table from tested defaults on a fresh install, so it's useful before you edit anything (General chat / Frontier coding / Coding model)
- [ ] **0.3.** Optional short setup interview (action-form cards, skippable) that fills the table by asking a few questions instead of making you learn the config
- [ ] **0.4.** Edit any time: `/router prefer <task-kind> <agent> <model>`, `/router prefer list`, `/router prefer clear <task-kind>`, plus a Settings panel
- [ ] **0.5.** Enforce the **approved** decision order in code (table / brain / learning / defaults as you finalize them)
- [ ] **0.6.** `/router status` explains which rule produced the choice (table line, brain, learned patch, or default)
- [ ] **0.7.** Adaptation switch wired to the approved default and semantics
- [ ] **0.8.** Table rewrite policy matches approval (silent rewrite forbidden unless you decide otherwise)

**Exit:** a new user can say what they want per task kind in under a minute, and CuttleRouter follows the approved authority rules with no learning required.

### Phase A — Save what happened

Every routed attempt is saved locally: what Cuttle chose, whether it worked, how long it took, and any token/cost numbers available. This gives later phases real evidence to learn from.

- [x] **A1.** SQLite `router_outcomes` keyed by `decision_id` + attempt number
  - fields: ts, session_id, project_id, task_type, difficulty, target, source, strategy, failure_kind, latency_ms, token/cost if known, attempt_index, user_feedback nullable
- [x] **A2.** Persist from dispatch (success + each attempt), not only stdout logs
- [x] **A3.** Optional soft signals
  - [x] `/router feedback good|bad [decision_id]`
  - [ ] Chat thumbs
  - [x] “retry same prompt” detection — repeated prompts escalate the target chain one hop per repeat and rate the prior attempt bad (`api/agent_router/repeats.py`); no tokens, no LLM judge
  - [x] Frustration (“rage”) detection — “still not fixed” / “you said you fixed it” phrases flag the session's recent routed attempts bad and escalate (`api/agent_router/frustration.py`); configurable phrase list at `agent_router.rage`; no tokens
- [x] **A4.** Redaction policy: prompts and responses are not stored; only routing/result metadata
- [x] **A5.** `/router metrics summary [days]` returns readable totals + chart-ready JSON

**Exit:** one week of dogfood produces charts without grepping logs.

### Phase B — Notice when a model gets worse

Compare recent results with that model’s normal results. If failures or negative feedback suddenly rise, flag it and temporarily send less work there.

- [x] **B1.** Per-target fail-rate tracking: transport_fail, task_fail (latency/soft-neg pending)
- [x] **B2.** Baseline window (14d, excludes rolling window) vs 6h rolling window; spike rule + two-proportion z-score (`api/agent_router/drift.py`)
- [x] **B3.** Actions on drift: temporary demotion (30 min TTL, settings-backed) routed around by engine + dispatch; `[AGENT-ROUTER] event=quality_drift` events; Router page health view with Recheck. *(chat-visible flag pending; re-flagging requires fresh failure evidence so routed-around targets auto-recover)*
- [ ] **B4.** Canary / shadow: sample % of turns with next-tier model offline score (decision-only or cheap judge) — cost-gated
- [ ] **B5.** Explicit non-goals: no auto-accusing providers of malice; wording = “quality regression signal”

**Exit:** injecting synthetic fail spike demotes a target in tests; recovering clears demotion.

### Phase C — Learn what works for this user

Use the saved history to prefer the agents that work best for this user’s tasks, while still respecting availability, budget, and pinned choices.

- [ ] **C1.** Policy patch layer: multiplicative weights on catalog entries; hard demotion list; budget-aware skip
- [ ] **C2.** Wire patches into `decide_with_outcome` after brain suggestion (brain proposes; policy validates/reranks)
- [ ] **C3.** Expand baseline suites; add “regret” eval: would escalation have been cheaper/better?
- [ ] **C4.** Cold-start priors checked into git (`suites/` + `priors/*.json`) derived from eval runs
- [ ] **C5.** Finish `local` brain mode; keep harness-as-brain experimental but supported; recursion guard only blocks CuttleRouter selecting *itself* as the worker

**Exit:** `/router status` shows live weights + demotions; eval suite does not regress.

### Phase D — Make it work on a fresh install

Detect installed agents and configured services, start with tested default choices, and route automatically when no agent/model is pinned.

- [ ] **D1.** First-run: discover harnesses; write sensible `agent_router` defaults; don’t require starring Cursor
- [ ] **D2.** Clean sessions invoke router by default; star remains opt-in sticky
- [ ] **D3.** Settings / chat: “Router health” panel (targets, budgets, drift flags)
- [ ] **D4.** Copy: short “how routing works” on first router decision (once)
- [ ] **D5.** Document promo/free-window targets as first-class catalog entries with expiry hints if known

**Exit:** fresh clone + keys → useful routing without reading this TODO.

### Phase E — Make CuttleRouter easy to swap and share

Keep the router behind a small, stable interface. Cuttle can swap in another router, and another project can use CuttleRouter, without either side rewriting its agent execution code.

- [ ] **E1.** Define one small router input/output contract (task + available targets in; chosen target + reason out)
- [x] **E2.** Use `agent_harness` catalog/kernel as Cuttle’s only chat/Discord slash execution adapter (Cursor/Codex/Muse/Claude/Hermes ported; per-agent `web_chat_api` elif forks deleted; thin shims kept for tests/supervised)
- [ ] **E3.** Add router adapters so CuttleRouter, Switchyard, or another experiment can fill the same slot
- [ ] **E4.** Move pure router modules into a package with no Flask/chat imports
- [ ] **E5.** Minimal outside-Cuttle example that calls CuttleRouter and runs the returned target in another framework
- [ ] **E6.** Publish private → public GitHub when A–C stable

**Exit:** external demo routes + records outcomes without Cuttle installed.

### Phase F — Optional shared health signals

With clear opt-in, share only anonymous summary numbers. This could show whether one user is having trouble or many users are seeing the same model decline. Prompts stay private.

- [ ] **F1.** Privacy design review (buckets only; k-anonymity; kill switch)
- [ ] **F2.** Schema: `{target_fingerprint, task_bucket, drift_bit, region_day, schema_ver}` — no prompts
- [ ] **F3.** Aggregation service (could be community-hosted); client pulls “global health”
- [ ] **F4.** Merge rule: global yellow + local green → soft warn; global red → stronger demotion unless local overrides
- [ ] **F5.** Legal/ToS + clear UX consent

**Exit:** two dogfood installs can agree a synthetic global spike is visible; default remains off.

---

## Visualization (in-product)

Prefer chat Vega + a Settings section over a heavy dashboard:

- Fail rate by target (7d)
- Drift flags timeline
- Escalation / fallback counts
- Cost proxy if available
- “Routes around X” callout when demoted

Reuse `<vega>` / fenced vega-lite already supported in Cuttle chat.

---

## Research / benchmark backlog

- [ ] Map our `baseline.json` cases onto difficulty bands comparable to RouterBench / xRouteBench (even if we only cite methodology)
- [ ] Read Switchyard stage-router + escalation docs; list algorithms we should *not* reimplement
- [ ] Prototype CUSUM on historical `[AGENT-ROUTER]` logs if any exist
- [ ] Define a **harness-aware** mini-bench: same prompts across Auto / Grok / Codex with human or judge labels (expensive — sample, don’t boil ocean)

---

## Explicit non-goals (for now)

- Parallel multi-agent fan-out as default routing
- Replacing the pipeline `tool-router` node
- Training a heavy neural router before EWMA/CUSUM + eval priors work
- Mandatory telemetry / phoning home
- Claiming we can prove provider dogfooding — only surface **signals**
- A Cuttle CLI (deferred; see `MODULARITY.md` goal 7)

---

## Decisions

1. **Name:** CuttleRouter.
2. **Quality checks:** support both human feedback and an optional judge model. This install will use Cursor for judging.
3. **Default behavior:** no starred/pinned agent or model means use CuttleRouter. A star/pin is an explicit bypass.
4. **Router contract:** task + available targets go in; chosen target + reason come out. Cuttle’s shared agent harness runs it. A network proxy API is not required for the first version.
5. **CuttleRouter is a harness agent** (a **router-harness**). It ships as a normal agent under the new harness pattern (`manifest.yaml` + `adapter.py`), so it can be invoked, pinned, and starred exactly like Cursor or OpenCode. It decides a target and delegates through the shared kernel.
6. **Preferences exist as a first-class table (Phase 0).** How that table ranks against the brain, learning, pins, and defaults is **not decided yet** — see “Awaiting your approval.”
7. **Pluggable routing brain.** CuttleRouter can think in more than one way; modularity is required:
   - **Programmatic** — regex / rules, and later CuttleBrain algorithms or specs you define
   - **LLM API** — e.g. OpenAI `gpt-4o-mini` (planned default for this install)
   - **Local LLM** — future experiment
   - **Harness agent as brain (experimental)** — e.g. Cursor Agent Auto while usage is free; another harness agent may be wired as the routing brain
8. **Nested query reports are intentional.** When CuttleRouter delegates to Cursor (or any agent), two query reports / run registrations for one user turn are a feature: one for the routing decision, one for the delegated work. Do not collapse them away.

### CuttleRouter as a harness agent — shape

| Manifest field | Value | Why |
|---|---|---|
| `sticky` | `true` | Lets you star CuttleRouter, so "route my chats" is a normal pinned agent instead of a special empty state |
| `resume` | `false` | The delegated agent owns its own native resume; the router must not claim one |
| `capabilities_inject` | `never` | The delegate's kernel pass already injects; avoids a double briefing |
| `requires_cloud` | depends on brain | `false` for pure programmatic; may be `true` when the selected brain needs cloud |
| `models` | **both** policy modes **and** selectable brains | See below — not profiles-only |
| install fields | unset | No CLI to install; availability is always true for the router shell |

**`models` is both, not either/or.** The harness `models` list (and related config) exposes:

| Kind | Examples | Role |
|---|---|---|
| Policy / autonomy modes | `table`, `auto`, `adaptive` | How much the router may deviate from your Phase 0 table (exact semantics after authority approval) |
| LLM brains | `openai/gpt-4o-mini`, future local model ids | Cheap/fast routing LLMs |
| Harness brains (experimental) | `cursor/auto`, later other agents | Use another agent harness as CuttleRouter’s thinking step |

Concrete planned configs for this install: OpenAI gpt-mini as the primary routing LLM; Cursor Agent Auto as an experimental free-window brain. Local LLMs later. Programmatic / CuttleBrain paths stay first-class so routing never *requires* an LLM.

Implementation notes:

- The adapter must refuse to select itself as the *execution* target (reuse the recursion guard). Selecting another harness as the *brain* is allowed and experimental.
- Delegation **should** go through the shared kernel so the router turn and the worker turn each get their own query report.
- Slash naming for the agent vs `/router` config is still open — see Still open #3. Do not invent a final name in code until you pick one.

## Still open

1. Where should shared, opt-in health aggregation live if Phase F ships?
2. Which exact Cursor model and sampling rate should judge completed work without wasting budget?
3. **Slash name for the CuttleRouter agent** — still thinking. `/router` is already the config command. Options include `/cuttle`, `/auto`, or move config under `/router config …` and give the agent `/router`. No pick yet.
4. Which task kinds should the Phase 0 table ship with? Current router vocabulary is `basic_ask`, `coding`, `debugging`, `architecture`, `research`, `other` — good enough to start, but the table is what you'll actually read, so the names should make sense to you.
5. **Authority order + adaptation defaults** — draft lives under “Awaiting your approval.” Needs your finalize/approve before Phase 0 coding.
6. How should policy modes and brain ids share the harness `models` list without looking confusing in the palette (namespacing? two fields?)?

---

## Suggested near-term order (next concrete slices)

0. **Phase 0** routing table + adaptation switch (do before B/C so learning has a baseline to respect)  
1. **A1–A2** durable outcomes  
2. **B1–B3** EWMA + demotion  
3. **D1–D2** OOB discovery + default clean-session routing  
4. **C1–C2** policy patches from store  
5. **E1** extract boundary (refactor while adding metrics, don’t wait for perfection)  
6. Defer **F** until local dogfood is boringly reliable  

---

## Changelog

| Date | Note |
|------|------|
| 2026-08-29 | Node Editor page rebuilt as the **Router page** (`/router_editor.html`; old editor moved to `src/web/legacy/`). Phase 0.1/0.2 done (use-case table, seeded defaults, table overrides brain). Phase B basic done (fail-rate drift + temporary demotion, routed around; recovery = no new failures). |
| 2026-08-17 | Router framed as a **socket** (cloud LLM / local LLM / table / other harness as brain); pointer to `MODULARITY.md`; DeepSeek Flash as a future OOB tentacle |
| 2026-08-17 | Authority order moved to “Awaiting approval”; router-harness = pluggable brain (programmatic / LLM / local / experimental harness); nested query reports intentional; models = policy modes + brains |
| 2026-08-17 | Added Phase 0 (user routing table + adaptation switch) and decided CuttleRouter ships as a harness agent |
| 2026-08-17 | Simplified phases; named CuttleRouter; set harness-based swap contract; completed A1/A2/A4/A5 and command-based A3 feedback |
| 2026-08-17 | Initial TODO from router review: telemetry → adapt → OOB → extract → optional federation |

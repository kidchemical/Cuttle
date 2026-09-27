# Cuttle Router Research Notes

**Status:** Living design document  
**Started:** 2026-08-16  
**Purpose:** Preserve the reasoning, economic assumptions, research findings, experiments, and design direction behind Cuttle's agent-routing system.

**Product / socket thesis (home lab, cozy, DeepSeek as tentacle, no Cuttle CLI for now):** [`guides/MODULARITY.md`](guides/MODULARITY.md). Work items: [`.cuttle/docs/agent-router-todo.md`](../.cuttle/docs/agent-router-todo.md).

## 1. Project Thesis

Cuttle is an **AI harness harness**: a control layer above agent harnesses such as Cursor CLI, Codex CLI, Hermes with local models, DeepSeek Harness (`dsh`), and future providers. Its mascot is a cuttlefish because each harness is conceptually a tentacle: independently capable, but selected and coordinated by a central system. The default habitat is a **home station / lab**; business-level use is optional, not the default design.

The router should not merely select the nominally cheapest model. It should select the workflow most likely to produce an acceptable result at the lowest **total** cost, accounting for money, latency, retries, intervention, and user satisfaction.

The long-term advantage is project-specific evidence. Generic routers know model names and advertised prices. Cuttle can learn which complete backends work well for Ian's actual projects and tasks.

## 2. Core Operating Assumption: Backends Are Mutable Black Boxes

Do not treat a visible model label as a stable product identity. The effective backend is:

```text
agent harness
+ advertised model
+ effort/reasoning configuration
+ harness system prompt and tools
+ context retrieval and compaction
+ provider serving behavior
+ CLI/harness version
+ current infrastructure conditions
```

For example, `Cursor CLI / Grok 4.6` is a distinct backend from direct xAI Grok 4.6. Quality can change even when the displayed label does not. The cause may be provider serving, Cursor behavior, context management, configuration, infrastructure, or an ordinary model update. Cuttle does not need to prove which party caused a regression; it needs to detect the observed change and adapt.

Backend identity should therefore include, when available:

- Harness and harness version
- Model identifier
- Effort/reasoning setting
- Relevant configuration version
- Project/repository
- Date or evaluation window

Maintain both long-term and recent performance windows. A backend can have a strong historical record while degrading sharply in its last several comparable tasks.

## 3. Current Router State

The initial router layer has been implemented above Cuttle's existing agent selection.

### Selection semantics

- If a session already has an agent/model selected, routing is bypassed.
- A starred agent command is equivalent to manually entering that command in a fresh session and therefore also bypasses routing.
- A clean session with no selected execution target invokes the enabled router.
- Router failure must not block execution; the deterministic default is Cursor Auto.

### Current default policy

- Routing brain: OpenAI API, currently `gpt-4o-mini`
- Preferred ordinary executor: `Cursor CLI / Auto`
- Frontier escalation: `Cursor CLI / Grok 4.6`
- Equivalent-provider fallback: configurable Codex target
- Local and agent-based router modes: schema-ready for later work

### Failure behavior

- Router API failure: use deterministic Cursor Auto.
- Observable Cursor Auto task failure: escalate once to Cursor Grok 4.6.
- Grok transport, quota, or availability failure: move through the configured Codex fallback chain.
- Recursion guards and tried-target tracking prevent loops.
- Semantically incorrect but process-successful work is not currently detected.

Current failure classification includes output-text heuristics. Over time, prefer evidence in this order:

1. Process exit status
2. Structured CLI event or terminal status
3. Build, test, lint, or type-check result
4. Explicit agent failure marker
5. Text-pattern inference as a last resort

## 4. Router, Coordinator, and Executor Are Separate Roles

### Router

Answers: **Who should handle this, and at what tier?**

Its output should include:

- Task type and difficulty
- Selected harness and model
- Coordination level
- Confidence or uncertainty signal
- Escalation target
- Fallback policy

The router should be cheap, fast, and supplied with only enough context to make a routing decision. It should not solve the entire task.

### Coordinator

Answers: **Who should do which part, with what context, constraints, acceptance criteria, and validation?**

Prompt enhancement belongs here, but the useful output is a **task packet**, not merely a longer prompt. A task packet may contain:

- Original request, preserved verbatim
- Interpreted objective
- Relevant files and existing patterns
- Scope inclusions and exclusions
- Constraints and assumptions
- Acceptance criteria
- Validation commands
- Ordered or independent subtasks
- Escalation instructions

Potential coordination levels:

```text
none       Pass the original request directly.
normalize  Clarify structure without repository investigation.
prepare    Inspect context and produce one task packet.
decompose  Produce multiple staged or independent packets.
supervise  Remain involved through validation and escalation.
ownership  The strong agent should perform the task directly.
```

### Executor

Performs the actual task. It may be Cursor Auto, Grok, Codex, Hermes/local Qwen, or another future harness/model pair.

### Important coordination rule

Do not automatically use a strong coordinator before every executor. A strong coordinator followed by a weak executor can cost more than direct strong-model execution once review and repair are included. Coordination should earn its cost through better success, reusable context, decomposition across several cheap workers, or substantially reduced executor exploration.

## 5. Economic Objective

The primary metric is **expected total workflow cost**, not the price of the first call.

```text
total workflow cost
= router
+ coordinator
+ executor
+ review
+ retries
+ rescue/escalation
+ local electricity
+ user intervention and annoyance
```

A useful simplified expression is:

```text
expected workflow cost
= initial workflow cost
+ P(initial failure) * expected rescue cost
```

The system should ultimately optimize for an acceptable outcome subject to budget and latency constraints:

```text
utility
= outcome quality
- monetary cost
- latency penalty
- intervention cost
- annoyance penalty
```

User satisfaction is a legitimate metric. A free local model that consumes thirty minutes of repair time is not economically free.

## 6. Real-World Cost Factors

### Cursor CLI / Grok 4.6

- Preferred frontier-like coding option when working well.
- Cheaper than the previously used Opus-heavy workflow.
- Has shown a perceived, unexplained quality regression after initially strong performance.
- Treat recent observed performance separately from historical performance.
- Treat Cursor/Grok as a backend distinct from direct Grok.

### Cursor CLI / Auto

- Current daily driver for low-to-medium coding work.
- Temporarily free/included during the promotional period, so its near-term marginal cost is approximately zero.
- After renewal it will have a limited included allocation and possibly on-demand pricing.
- Routing policy should be time- and quota-aware rather than assuming a permanent fixed price.
- Promotional capacity is use-it-or-lose-it, so aggressively using Auto during the promotion is rational.

### Codex CLI through ChatGPT Plus

- Appears partially included with the existing Plus subscription.
- Exact usable allocation, reset behavior, model options, and practical quality are not yet characterized.
- Until measured, represent it as zero immediate cash cost with uncertain quota and availability.
- Test on a controlled sample of real tasks before assigning it a major routing role.

### Local inference on RTX 3080

- The 3080 is already owned, so its purchase price is a sunk cost for current routing decisions.
- Marginal local costs include electricity, heat/cooling, latency, failed attempts, rescue calls, and user time.
- Raw electricity is likely much smaller than the expected cost of failed work and frontier rescue.
- Local models are most attractive for bounded, well-validated tasks, repository research, mechanical edits, context preparation, and other work where failure is cheap.

### Future local hardware

A Mac Mini, Spark, Halo, or similar device should be evaluated using:

```text
effective cost per useful task
= amortized hardware purchase
+ electricity
+ maintenance
+ expected rescue cost
```

If the hardware is partly purchased as an enjoyable experimental toy, separate that personal value from the strict productivity investment rather than demanding that the entire purchase price pay for itself through avoided API usage.

## 7. Subscription and Quota Economics

Most routing research assumes fixed per-token prices. Cuttle must support nonlinear budget buckets:

```text
included allowance remaining -> marginal cash cost near zero
allowance nearly exhausted    -> rising opportunity cost
allowance exhausted           -> on-demand rate or unavailable
promotion expiring            -> use-it-or-lose-it capacity
opaque subscription quota     -> uncertain availability risk
```

An included token is not always economically free because spending it now may leave less capacity for a harder task later. Represent this with an effective or shadow price that can rise as the billing period progresses and remaining capacity falls.

Candidate budget state:

```json
{
  "backend": "cursor/auto",
  "billing_type": "included_allowance",
  "remaining_fraction_estimate": 0.72,
  "reset_at": "unknown-or-timestamp",
  "marginal_cash_cost": 0,
  "availability_confidence": 0.8
}
```

Exact pricing is time-sensitive and should not be copied permanently into policy code. Store prices and allowances as configuration or refreshed metadata.

## 8. Research Takeaways

### Agent-as-a-Router / ACRouter

The closest research match treats coding model selection as a continuous routing problem. Its strongest practical finding is that router performance improved substantially when given prior model-performance statistics by task dimension. Merely adding task-dimension descriptions did not produce the same benefit.

**Cuttle takeaway:** Better evidence may matter more than a smarter router model. Feed the router historical, per-category backend outcomes rather than asking it to guess from the prompt alone.

Reference: <https://arxiv.org/html/2606.22902v1>

### RouteLLM

RouteLLM emphasizes choosing one suitable model per request to control cost and latency.

**Cuttle takeaway:** Direct routing should remain the common path. Multi-call coordination is justified only when it demonstrably improves the complete workflow.

Reference: <https://arxiv.org/html/2406.18665v4>

### FrugalGPT

FrugalGPT demonstrates that cheap-first cascades can preserve quality while greatly reducing cost on evaluated workloads.

**Cuttle takeaway:** Cheap-first escalation is most attractive when success can be verified objectively. Coding tasks without strong tests require more caution than benchmark question answering.

Reference: <https://arxiv.org/abs/2305.05176>

### TwinRouterBench

TwinRouterBench evaluates routing within multi-step agent trajectories rather than only at the initial prompt.

**Cuttle takeaway:** Task-level routing is the correct first step. Later, step-level routing could assign repository search, architecture, editing, and diagnosis to different tiers—but only if deeper harness integration justifies the complexity.

Reference: <https://arxiv.org/html/2605.18859v1>

### Triage

Triage uses code-health and maintainability signals to help select the cheapest model tier likely to pass verification.

**Cuttle takeaway:** Prompt wording is insufficient. Repository structure, test coverage, module size, dependency fan-out, existing examples, and baseline build health can help predict whether a cheaper model is suitable.

Reference: <https://arxiv.org/html/2604.07494v1>

### Mixture-of-Agents

Layering and aggregating several model outputs can improve quality, but increases calls and delays the final result.

**Cuttle takeaway:** Mixture-of-agents is a maximum-quality or parallel-research strategy, not the default budget strategy. Several free local workers followed by one strong aggregator may be useful; several paid workers followed by paid synthesis probably is not.

Reference: <https://arxiv.org/abs/2406.04692>

### Confidence calibration

Model-reported confidence is not automatically calibrated. A reported `0.8` does not imply an 80% chance of a good decision.

**Cuttle takeaway:** Measure actual accuracy within confidence bands, or derive uncertainty from historical similarity, model agreement, target-score margins, task novelty, and validation strength.

Reference: <https://arxiv.org/abs/2605.18796>

## 9. Initial Router Evaluation Results

### Single baseline

- Suite: `baseline` v1
- Router: API / `gpt-4o-mini`
- Cases: 25
- Passed: 23, including preference warnings and manual-review cases
- Expectation failures: 2
- Invalid decisions: 0
- API errors/fallbacks: 0
- Elapsed time: approximately 13.1 seconds at concurrency 3
- Target distribution: Cursor Auto 21; Cursor Grok 4.6 4

Persistent disagreements:

1. A self-adjusting routing-policy implementation was classified as medium coding and sent to Auto instead of high architecture/Grok.
2. “Improve performance everywhere” was classified as a basic ask rather than ambiguous architecture/coding work.

### Repeated baseline

- 25 cases x 5 repeats = 125 decisions
- Approximately 55 seconds
- Zero API errors and zero fallbacks
- Only one Auto/Grok target oscillation
- Most other variation affected classification labels without changing the target
- The same two expectation failures were deterministic across all five runs

Interpretation:

- `gpt-4o-mini` is sufficiently stable for the initial routing role.
- The evaluation proves decision consistency, not real-world routing effectiveness.
- Persistent misses are policy-understanding disagreements, not random noise.
- Do not upgrade the router model merely to chase two synthetic-suite disagreements.

## 10. Required Telemetry

Every real task should eventually correlate a routing decision with the complete workflow outcome.

### Decision record

- Decision ID
- Timestamp
- Original request or privacy-safe fingerprint
- Project/repository
- Task category and dimensions
- Router provider/model/configuration version
- Selected backend
- Coordination level
- Escalation and fallback targets
- Reported confidence
- Short reason

### Execution record

- Backend and model actually invoked
- Harness/model versions and effort level, where available
- Start/end time and latency
- Token/API cost or best available estimate
- Included-quota consumption estimate
- Files changed and diff size
- Build/test/lint/type-check results
- Observable failure kind
- Retry and escalation count
- Final backend
- Whether the initial patch survived

### Outcome record

- Accepted without intervention
- Accepted after repair
- Rejected or abandoned
- User rating: good / acceptable / annoying, or a small numeric scale
- Total wall-clock time
- Total monetary cost
- Manual intervention time
- Notes/tags describing failure or dissatisfaction

### Derived statistics

- Success rate by backend, project, and task dimension
- First-pass success rate
- Rescue/escalation rate
- Expected total workflow cost
- Recent versus long-term performance
- Median and tail latency
- User satisfaction by route
- Confidence calibration
- Model/harness quality-change alerts

## 11. Adaptive Routing Direction

The router should eventually receive compact empirical priors such as:

```json
{
  "task": {
    "category": "debugging",
    "scope": "multi_file",
    "project": "cuttle",
    "validation_strength": "medium"
  },
  "recent_performance": {
    "cursor/auto": {
      "success_rate": 0.76,
      "average_total_cost": 0.18,
      "rescue_rate": 0.12
    },
    "hermes/qwen-local": {
      "success_rate": 0.41,
      "average_total_cost": 0.07,
      "rescue_rate": 0.52
    }
  }
}
```

Use task-specific rolling windows and retain a modest exploration rate so a backend can recover from a bad period or demonstrate improvement after an update.

Do not compare raw success rates without controlling for task difficulty. A frontier model may look worse simply because it receives all difficult tasks.

Potential selection objective:

```text
choose route minimizing expected total workflow cost
subject to predicted success >= required quality threshold
and latency <= user/task tolerance
```

## 12. Experiment Design

Maintain control groups instead of evaluating only routed workflows.

Candidate strategies:

| Strategy | Workflow |
| --- | --- |
| Direct baseline | Cursor Auto directly |
| Local direct | Router -> Hermes/local model |
| Coordinated local | Router -> strong bounded coordinator -> local executor |
| Frontier direct | Router -> Grok or Codex owns task |
| Escalating local | Router -> local attempt -> frontier only on observable failure |

Compare similar task categories on:

- First-pass success
- Final success
- Total workflow cost
- Wall-clock time
- Paid rescue rate
- Manual intervention
- User satisfaction

Keep an explicit **exploration budget** separate from the expected **production budget**. Early experiments may legitimately cost more while discovering what works.

## 13. Guardrails Against Orchestration Waste

- No coordinator for trivial tasks.
- At most one coordinator call initially.
- No automatic LLM review after objective validation succeeds.
- One local attempt maximum before escalation, unless configured otherwise.
- Do not use a frontier coordinator before a frontier executor unless decomposition or reuse justifies it.
- Preserve a direct-route override.
- Never recurse through the router.
- Bound turns, tool calls, time, and estimated cost.
- Do not allow simultaneous writers in one worktree.
- Parallelize only isolated, independently testable work.
- Preserve the original request in every task packet.

## 14. Near-Term Roadmap

### Now

1. Keep `gpt-4o-mini` as the inexpensive routing brain.
2. Use Cursor Auto heavily during the free promotional period.
3. Begin routing real, noncritical work.
4. Preserve direct Cursor Auto as a control group.
5. Record minimal decision/outcome correlation.

### Next

1. Characterize Codex CLI models, allocation, latency, and task quality.
2. Benchmark the existing local Qwen coding model on historical Cuttle tasks.
3. Add project and task dimensions to telemetry.
4. Track complete workflow cost including rescue attempts.
5. Add recent versus long-term backend performance views.

### Later

1. Feed empirical performance priors into routing decisions.
2. Add ambiguity as an action (`clarify`) rather than treating it solely as difficulty.
3. Add optional bounded coordinator/task-packet preparation.
4. Test local-first and coordinated-local workflows against direct Auto.
5. Add quota-aware shadow pricing and reset schedules.
6. Explore change-point detection for sudden backend degradation.
7. Consider step-level routing only after task-level routing proves useful.

## 15. Open Questions

- How much Cursor Auto usage will the renewed subscription realistically support?
- What exactly is included with Codex through ChatGPT Plus, and how does its allocation reset?
- Which local Qwen tasks succeed without paid rescue?
- Does a strong coordinator materially improve local-model success enough to pay for itself?
- Which Cuttle and game-development tasks have strong enough validation for cheap-first cascades?
- How should Cuttle estimate user-time and annoyance cost without making telemetry burdensome?
- How quickly should recent performance override long-term history?
- How should the system distinguish actual model regression from a run of unusually difficult tasks?
- Should ambiguous tasks produce clarification before any executor is selected?
- At what point would new local hardware provide practical value beyond experimentation and enjoyment?

## 16. Durable Principles

1. Optimize the whole workflow, not the first call.
2. Treat backends as observed systems, not trustworthy labels.
3. Prefer evidence over router intuition.
4. Separate routing, coordination, execution, validation, and escalation.
5. Make every additional model call earn its cost.
6. Use local inference where tasks are bounded and verifiable.
7. Preserve control groups and comparable task categories.
8. Account for subscriptions, quotas, promotions, and reset dates.
9. React to recent degradation without forgetting long-term history.
10. Optimize for making games sustainably, not for building the fanciest orchestration diagram.

## 17. Architectural Decision: Harness-Neutral Coordination Plane (2026-08-16)

**Decision:** Cuttle is a **harness-neutral coordination/control plane** above agent
CLIs (Cursor, Codex, Hermes, …) and API/local brains. Supervised coordination is a
**routing strategy** (`RoutingDecision.strategy = supervised`), not a second router
and not a fake agent name.

**Initial implementation:** `diet-frontier` profile —

```text
Codex CLI / gpt-5.6-sol / reasoning low  →  Cursor Agent CLI / Auto
```

with durable supervised-task state, bounded delegation packets, event-driven review
(no model polling), max one follow-up by default, and paid-tier gates.

**Economic rule:** Supervised coordination must demonstrate **positive cost-adjusted
value**. It must not be applied to every task. A strong coordinator plus a weak
worker can cost more than direct frontier execution once review and repair are
included. Prefer supervised mode when it:

- Improves success enough to offset coordinator cost, or
- Substitutes for a frontier worker ("diet frontier"), or
- Serves as an explicit fallback when frontier capacity is unavailable — without
  silently consuming billable API quota.

Production Auto → Grok → Codex fallback remains valid and unchanged until a future
explicit policy change. Config hooks (`supervised_as_frontier_fallback`,
`prefer_supervised_for`) exist but default **off**.

See `docs/guides/SUPERVISED_COORDINATOR.md`.


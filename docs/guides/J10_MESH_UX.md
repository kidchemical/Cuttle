# J10: mesh UX and distributed scheduling — implementation plan

Status: design signed off (prototype `src/web/prototypes/j10/mesh_ux.html`); implementation starts after the
Job Manager deploy is smoke-tested. Requirements and the reasons behind them: [JOB_MANAGER.md](JOB_MANAGER.md#next-milestone-j10-mesh-ux-and-scheduling).

**Acceptance in one sentence:** the user asks for work and Cuttle picks and uses the right machines without
being told, with enough visibility that the user rarely intervenes. Scheduling that works reliably ships before
any visual polish.

## 1. Architecture and data model

One model, four views. The Mesh dashboard, the chat job card, the Jobs page and the approval form render the
same two sources; none keeps its own state.

| Source | Owner | Adds in J10 |
|---|---|---|
| Machine snapshot: worker row + heartbeat `load` | `capabilities.py` → `store.upsert_worker` | `load_at` (sample time), CPU %, disk and network rates, GPU temperature and power, a `null` value for every metric not reported (never 0), focused interactive app |
| Job rows: status, `reserved`, `held_by`, `progress`, `wait_reason`, `stopped` | `store.py` | `stages` (names and current index), `plan` (the chosen target, strategy and score table as shown on the card), `pinned` (target chosen by the user) |
| Job history | `metrics.py` (`job_metrics`) | p90 next to the median, a per-sample time series (peaks are already stored) |
| Scheduling preferences | settings `device_workers.scheduling` | new block, see §4 |

Rules carried over from the design review:
- **Queued capacity is not a reservation.** A job waiting for a busy machine has `target_worker_id` and a
  `wait_reason` only. `reserved_json`/`held_by` are written at claim, inside the existing
  `BEGIN IMMEDIATE` admission (`claim_jobs`), as today. The dashboard hatches only real holds.
- **Counted once:** a shared service (ComfyUI server, its loaded models) is machine load under `services`; a
  job's reservation covers what its own prompt adds.
- **A user's machine choice is never overridden.** `pinned` jobs never move; auto-routing and failover apply
  only to jobs the policy routed.

## 2. Milestones and dependencies

| # | Milestone | Depends on | Ships |
|---|---|---|---|
| T | Telemetry and freshness | — | new `load` fields; laptop GPU name via its `nvidia-smi`; `not reported` vs 0; stale greys after 30 s, meters hidden after 2 min |
| S | Scheduler and policy | T | `scheduler.rank()`, capacity classes, totals, confidence, strategies, `device_workers.scheduling`; `runs.plan` uses it; agents route through it by default |
| C | Job card | S | stages, three bars, reservation vs use, process groups, cancel checklist, resource history, notice coalescing |
| D | Mesh dashboard | T | machine cards, summary row, routing hint |
| A | Approval form | S, C | comparison choices, "why / what would change it", remembered preference vs auto-run |
| P | Settings → Mesh | S | strategy, interactive protection, host floor, per-kind ask/auto table |
| X | Process and notice hygiene | C | work-tree classes (workload / supporting / watcher / machine service / stale / orphaned), duplicate-submit attach, one notice per job |

Order: T → S → C → D → A → P → X. S is usable from the CLI and the existing J5 card before any new UI lands;
that is the reliability gate.

## 3. Existing components to reuse

| Need | Reuse | Do not add |
|---|---|---|
| Hard gate | `admission.admit`, `reserved_totals`, `HOST_RESERVE_MB`, `INTERACTIVE_BURST` | a second admission path |
| Estimates | `metrics.estimate` (median, peaks, OOM growth, basis tiers) | a separate history store |
| Plan / submit | `runs.plan`, `runs.submit`, `runs.decide`, `ask_if_blocked`, pause flag | a second run registry |
| Progress | `run_watch.summarize` → watch card (`cuttle-action-form`, `progress-bar-item--primary/worker/secondary`, `watch-previews`, `watch-resources`) | a second card system |
| Forms | `chat_action_forms.js` radio/check/field markup, J5 `workers.run` actions | native question tools |
| Processes | `api.jobs.work_tree`, `end-process --create-time` | pattern kills, agent wait loops |
| Notices | `background_handoff`, chat-ops `job_notice` dedupe | a second notifier |
| Styling | `design_tokens.css`; mesh-page status pills move to a shared stylesheet | per-page palettes |

## 4. Scheduling and admission

`scheduler.rank(job, snapshot, strategy, policy)` returns one option per worker (plus a split when the job is
shardable), each with:

- **Capacity class** from `admission.admit` against the snapshot and its projected releases: *now* ·
  *after job X* (that job's ETA) · *after an interactive app releases* · *cannot fit* (never offered).
- **Done-in = wait + transfer + run**, p50 and p90. Run time comes from `metrics.estimate` for that worker,
  falling back to "same settings, any machine" scaled by relative speed. Transfer is input bytes divided by
  measured mesh throughput (probe RTT plus the first transfer); zero when the inputs are local.
- **Confidence:** high with 10 or more samples on that worker, medium with 3–9, low below that or for
  splits without history.
- **Interactive impact:** which focused app would share CPU/GPU, for how long, and its expected slowdown.

Strategy weights (fastest / balanced / least disruption) combine done-in, interactive impact and host use into
the score shown under "Why". Balanced accepts up to `max(5 min, 1.5 × fastest)` extra to avoid interactive
impact. The option also states what would change the pick (an app going idle, a strategy change, an overrun).

Policy (`device_workers.scheduling`): `strategy`, `protect_interactive`, `host_floor_ram_mb`,
`host_floor_vram_mb`, `kinds: {comfyui: {ask, strategy}, …}`. It always asks for paid API jobs, a job kind seen
for the first time, and a job needing over 80 % of a machine's free VRAM, RAM or CPU, each resource checked
separately. Otherwise a covered job is submitted with the top option and only the job card appears, with an
undo.

The plan carries a snapshot id. Submit re-checks it: if the snapshot changed in a way that moves the pick, the
card re-plans instead of submitting. Admission stays atomic at claim.

## 5. Telemetry and freshness

- The worker heartbeat (`capabilities.load`) adds the fields in §1. Every field is either a measured value or
  `null` (shown as "not reported").
- Freshness comes from `load_at`, not the time the row was written. Under 30 s the meters are live; from 30 s
  to 2 min they grey out as "delayed"; after 2 min they are hidden. Admission already refuses telemetry older
  than `TELEMETRY_STALE_SEC` (60 s).
- Each job records a VRAM/RAM/GPU time series (one sample per heartbeat, capped) into `job_metrics` at the
  end. It feeds the card's resource history and the next estimate.
- Focused-app detection: Linux X11 `_NET_ACTIVE_WINDOW`; Windows `GetForegroundWindow`. Where neither is
  available, fall back to "running" with no focus signal.

## 6. UI

| Surface | File(s) | From the prototype |
|---|---|---|
| Mesh dashboard | `mesh_page.js`, new `css/mesh.css` | summary row, machine cards, used / held / free meters, grouped load, freshness, health |
| Job card (chat) | `chat_action_cards.js`, `chat_action_forms.js` (watch rendering), `run_watch.summarize` | stages, three bars, reservation vs use, processes, history, cancel checklist, merged notices |
| Jobs page | `jobs_page.js`, `jobs_ledger.js` | the same renderer as the chat card, grouped machine → chat → job |
| Approval form | `runs.plan` options → J5 form | comparison choices, strategy radio, why / change table, remember vs auto-run, snapshot line |
| Settings → Mesh | settings page section | strategy, interactive protection, host floor, per-kind table |

The job card renderer is a single function shared by the chat card and the Jobs page. The prototype CSS moves
into `css/mesh.css`, and the status pills move out of `mesh_page.html`.

## 7. Failure handling and recovery

| Failure | Behavior |
|---|---|
| Worker disconnects mid-job | the lease expires, then the job is requeued (existing); a routed job may move to another worker, a pinned one waits; the hold is released when the worker confirms or after `HOLD_OFFLINE_RELEASE_SEC` |
| One side of a split fails | its unfinished shards go to the other worker if they fit, otherwise they wait with a `wait_reason`; finished shards are kept |
| Snapshot changed before submit | re-plan on the card; never over-commit |
| Telemetry stale | admission refuses that worker; the card shows "delayed", never "available" |
| Cancel | the card checklist follows the store: requested → acknowledged → workload stopped (`stopped`) → hold released (`held_by` cleared); "cancelled" is shown only at the end |
| OOM | already handled: `metrics` grows the next reservation 1.5×; the card shows the OOM reason |
| Host upgrade | pause / drain / restart (JOB_MANAGER.md); a rollback starts only from a drained state |
| Mixed versions | older workers get only jobs that declare no resources (existing) |

## 8. Tests and acceptance

Automated:
- `scheduler.rank`, table-driven from the prototype snapshot fixture (Unity focused on the host, laptop busy,
  tower offline). Expected picks per strategy; capacity classes; done-in arithmetic; confidence thresholds;
  a split refused when one side cannot fit.
- Policy: always-ask cases (paid, new kind, over 80 % per resource); a pinned job is never moved; the
  remember-preference and auto-run settings stay separate.
- Store: a queued-capacity job holds nothing until claim; a snapshot mismatch re-plans; holds are counted
  once; cancel states in order.
- Telemetry: `null` vs 0; freshness bands; the time series is capped.
- UI: the existing card render e2e tests (`test_chat_action_card_render.py`) extended for stages, cancel
  checklist and processes; the dashboard renders `not reported` and stale states.
- The budget test stays green. New always-on rule text has to fit the envelope (see ROADMAP technical debt).

Real machines (desktop RTX 3090 and the laptop):
1. With Unity open on the host, ask an agent for a ComfyUI job. It runs on the laptop without a question
   (policy covers it), and the card says why.
2. With the laptop busy: Balanced queues it there; Fastest runs it on the host. The card totals match reality
   within 25 %.
3. Cancel a running job: the checklist completes and the reservation is released within 10 s of the worker's
   stop.
4. Disconnect the laptop mid-job: a routed job requeues or moves, a pinned one waits, and no phantom holds
   remain.
5. Dashboard meters match `nvidia-smi` and `free` on each machine within 5 %; an unplugged machine is never
   shown as idle.
6. Ten consecutive agent jobs produce no duplicate runs, no agent wait loops, and one notice per job.
7. Over a week, at least 80 % of heavy jobs submitted while the host is interactive run elsewhere (from
   `job_metrics`).

# Job Manager (mesh runs)

Agents decide *what* to run; Cuttle runs it. A run is queued across the mesh, admitted only when a worker has
the RAM/VRAM for it, isolated so an OOM kills only that job, shown live in the chat with no model turns, and the
chat is resumed exactly once when it ends. It is a layer on `api.device_workers`, not a second queue. Origin:
the ComfyUI memory audit (CH-002819).

Agent runbook: [`cuttle-workers.md` → Runs](../../.cuttle_global/docs/cuttle-workers.md#runs-job-manager).
Mesh basics: [CUTTLE_WORKERS.md](CUTTLE_WORKERS.md).

## Status

| Phase | State | What exists |
|---|---|---|
| J0 Telemetry v2 | **Done** | Heartbeat `load`: per-GPU VRAM used/total + util (`nvidia-smi`), RAM available, swap, PSI memory/io avg10 (Linux), interactive apps (Unity Editor with a project, Blender UI; headless runs excluded), adapter services (ComfyUI: pids, RSS, VRAM per GPU), and `heavy`: every process with ≥ 1 GB RAM or real VRAM tagged `interactive` / `service:<name>` / `cuttle-job` / `headless` / `external`. Refreshed on the job pulse while a job runs. `capabilities.py` |
| J1 Reservations | **Done** | `requirements.resources` (`ram_mb`, `vram_mb`, `gpu_slots`, `service`); `admission.admit` inside `claim_jobs`'s `BEGIN IMMEDIATE` transaction. A reservation counts while the worker **holds** it (`held_by`), not just while claimed: it is released only when the worker reports the workload ended or confirms it stopped. Open interactive apps keep burst room (Unity 3 GB RAM / 2 GB VRAM, Blender 2/2). Refusals name what uses the memory. Tested with 8 processes claiming at once |
| J2 Isolation | **Done (Linux)** | Jobs that declare `ram_mb` run in `cuttle-job-<id>.scope` under `cuttle-jobs.slice` (`MemoryMax` = reservation + 25 % / ≥1 GB, swap 512 MB, `OOMPolicy=kill`, `choom 1000`); OOM is reported as `oom: …`; peak RAM/VRAM measured from the scope. Shell recipes and Blender renders. A scope left running by a dead worker is stopped before the retry starts. Windows Job Objects: planned |
| J3 Runs | **Done (flat batches)** | `runs.plan` / `runs.submit`, run watch on the unit-watch records (`mesh_jobs`): status, Stop, exactly-once resume through the chat-ops outbox, polled by the daemon and Flask, refreshed on every host-side heartbeat. A run is finished only when no worker still holds one of its jobs ("stopping" until then). Multi-stage DAGs: planned |
| J4 ComfyUI adapter | **Done, validated live** | `comfyui_prompt` job type: WebSocket node events → stage, nodes done/total, sampler steps, previews (host worker), per-node seconds, service peak RAM/VRAM; deterministic prompt id → reattach on retry; cancel via `/api/jobs/<id>/cancel`, counted only once ComfyUI no longer lists the prompt; optional `start_recipe` |
| J5 Approval | **Done (no spend)** | `run-plan` returns per-worker fit (named processes, offline workers), estimates with their history basis, reservation raises, and a ready form; `workers.run` submits only the approved spec (SHA-256 check, once). Spend ledger and per-wallet caps: planned with J6 |
| J5b Nothing fits | **Done** | The plan says so up front; a run with no progress for `ask_after_s` (default 10 min) resumes the chat once with a Cuttle-built form: move the waiting jobs to a worker that fits now, keep waiting (asks again in 30 min), or cancel (no resume). `workers.run-decide` applies it |
| J6 Tripo adapter | Planned (follow-up milestone) | Developer-API stages, dry-run cost, idempotency key = job id; a spend ledger with credit and dollar budgets per wallet, checked in the approval form. Studio (MCP) stays agent-driven |
| J7 History | **Done** | `job_metrics` table: per finished job its machine, workload (`template`, else type + recipe), config digest (ComfyUI: node classes + numeric inputs, not prompts/seeds/files), wall time from first claim, queue wait, measured peaks, reservation, OOM, per-node seconds. `metrics.estimate` answers from this machine + these settings, then these settings anywhere, then the workload; `metrics.suggest` raises reservations to peak + 15 % (×1.5 after an OOM). Stage-weighted percent: planned |
| J8 Card v2 | **Done** | Bars, grid, label, elapsed, ETA, latest previews, per-worker RAM/VRAM/GPU/swap with reservations and the processes using the most, job errors (OOM marked), open decision. Rendered in Chromium tests |
| J9 Distribution | **Partial** | Auto (any worker that fits) or pinned target, chosen in the approval form or a decision; pinned requeue on lease loss. Staging inputs to other machines, laptop rollout (needs this branch on it): planned |
| Multi-stage workflows | Planned (follow-up milestone) | A run as a DAG of stages (hero image → Tripo shape → texture), each stage its own job type |
| Artifact transfer | Planned (follow-up milestone) | Inputs staged to, and outputs collected from, the worker a job lands on: content hashes checked after copy, a file the target already holds (same hash) not sent again |
| Adaptive estimates | Planned (follow-up milestone) | Reservations from a high percentile of measured peaks plus a safety margin instead of the max; an outlier (one anomalous peak, or an OOM under unrelated memory pressure) ages out or is dismissed from the card, so one bad run cannot pin a reservation high |
| Remote previews, Windows isolation | Planned (follow-up milestone) | Previews shipped from remote workers to the host; Windows Job Objects for the memory scope |

Generic adapters: Unity builds and other scripts run as allowlisted `shell` recipes, Blender as `blender_render`,
file staging as `file_copy`; all get resources, scopes, cancel and run watches. New providers are modules in
`api/device_workers/adapters/`.

## Pieces and owners

| Concern | Owner |
|---|---|
| Queue, claim/lease, retries, Jobs page, cancel | `api.device_workers.store` (+ `api.jobs` ledger) |
| Telemetry | `api.device_workers.capabilities` |
| Admission (pure) | `api.device_workers.admission` |
| Memory scopes | `api.device_workers.isolation` |
| Provider adapters | `api.device_workers.adapters` (`comfyui`) |
| Plan / approve / submit | `api.device_workers.runs`, action `workers.run` |
| Run status + resume | `api.device_workers.run_watch` on `api.job_watch` unit-watch records |
| Card | `chat_action_cards.js` (`watchEtaText` in `chat_action_forms.js`) |

## Run flow

```text
agent: run-plan --spec run.json ──► form (fit per worker, ETA, reservations) ──► user picks a worker
                                                                                   │ workers.run (run_id, spec_sha256, target)
host: runs.submit → jobs (params.watch_id) + watch record (mesh_jobs) + watch card
worker: claim (admission + reservation) → executor/adapter in a memory scope → heartbeats with progress
host: heartbeat → run_watch.touch → status JSON (≤2 s);  poll (15 s) → all jobs ended → terminal status + 1 resume
user: Stop on the card → jobs cancelled → next heartbeat refused → worker cancels (ComfyUI prompt, process tree)
```

### Resources and admission

```text
headroom_ram  = min(total − 4 GB host reserve − Σ reserved, available − 1 GB)   (+ service RSS for service jobs)
headroom_vram = per GPU: min(total − 512 MB − Σ reserved, total − used − 512 MB) (+ service VRAM for service jobs)
one gpu_slot per GPU; telemetry older than 60 s admits nothing that declares resources
```

Memory a long-lived service already holds (ComfyUI's cached models) counts as free for jobs that run inside it.
Workers still run one non-agent job at a time; reservations already guard against apps the mesh does not own,
and make more slots per worker safe later.

### ComfyUI

`params.prompt` is the API-format graph (Save (API)); `stages` maps node ids to labels; `server` must be
loopback. The ComfyUI server keeps its own memory scope (`comfyui.slice` in the ComfyUI project). Declare
`requirements.resources.service: "comfyui"` with the job's measured peaks (Hunyuan3D 2.1 shape at octree 256:
≈7.6 GB RAM load, 4.2 GB VRAM decode, 5.7 GB multiview; CH-002819).

## Reliability

A workload may outlive the process that started it (ComfyUI runs the prompt, a scope keeps running), so a
job's reservation follows the **workload**, not the claim:

| Event | What happens | Reservation |
|---|---|---|
| Host (daemon/Flask) restart | Jobs, holds and watch records are on disk; whichever poller runs picks up | unchanged |
| Worker process restarts | On its first register (new `worker_run`) its claimed jobs requeue at once, pinned to it; it re-claims them: ComfyUI reattaches to the same prompt id or collects the finished result; a shell/Blender scope still running is stopped before the retry | held throughout |
| Worker unreachable past its lease | Job requeued but pinned (`held_by`): no other worker may start it. When the worker comes back, its next heartbeat picks the job up where it is | held |
| Worker gone for 30 min | Its holds are released; pinned jobs may run elsewhere | released |
| Stop (card, Jobs page, decision) | Job cancelled; the worker's next heartbeat is refused and it stops the workload | held until the worker confirms it stopped |
| Stop not confirmed (ComfyUI hung, scope will not stop) | The job ends `failed`/`cancelled` but stays held; every worker tick retries the stop (`executor.stop_workload`) and releases only once it succeeds | held |
| Agent session gone | Nothing to reconnect; the host resumes the chat when the run ends | — |

A run is not finished while one of its jobs is still held, so the chat is never resumed early.

What the chat hears when a run ends:

| Ending | Card | Resume turn |
|---|---|---|
| Every job succeeded or failed | Done / Failed | one, with the counts |
| Every job stopped on purpose (card Stop, decision "cancel", or each job from Jobs / `workers cancel`) | Stopped | none: the user ended it, and a resume would make the agent start it again |
| Some jobs stopped on purpose, the rest finished | Done / Failed | one; the counts say how many were stopped and not to resubmit them |
| Jobs cancelled by the system (queue TTL, retries exhausted) | Failed | one: an outcome the agent must hear about |

The chat's own Stop ends the agent's turn, not the run: a run outlives turns by design, and its card's Stop ends it.

Validated live (CH-002819, Flux 2 Klein on ComfyUI): kill -9 of worker and host mid-prompt, a frozen worker
past its lease with a second worker trying to claim, a frozen ComfyUI during Stop, an orphaned scope.

## Upgrading and mixed versions

The host never runs old and new code side by side; change the live checkout only with the mesh stopped:

1. `python -m api.device_workers.cli pause-submissions`: new mesh jobs and run approvals are refused with a
   "paused for a host upgrade" error (agent turns unaffected); queued and running jobs carry on.
2. Wait for `python -m api.device_workers.cli drain-check` to pass: submissions paused, no mesh job queued,
   running, or still holding a reservation (a cancelled job counts until its worker confirms it stopped).
3. Stop the host worker and the daemon, then Flask, through their owners (`/restart`, the Flask restart form;
   never by signal from an agent they host).
4. Merge or roll back the live checkout; start the daemon and Flask; `drain-check` once more (nothing queued),
   then `resume-submissions`. The flag is a file beside `device_workers.db`, so it survives the restarts.
5. Update remote workers with `workers.self-update` (it restarts their client daemon, so a worker is never
   half-updated). Until then a worker on older code still runs jobs that declare no resources; it has no RAM
   telemetry, so admission refuses resource-declaring jobs on it ("update Cuttle on it"): reservations only
   ever exist on updated workers.

A rollback drops reservations and run watches the old code does not know, which is why step 2 comes first.

## Non-goals

- A second queue, a second progress-card system, or agent-side polling loops.
- Free-form remote shell (unchanged mesh rule).
- Running Tripo Studio MCP calls without an agent.

## Limits

- Previews are written on the host only (its `/output` is served); a remote worker's previews are not shipped.
- Percent counts graph nodes (one sampler node is most of the time); the ETA uses measured history first.
- A recipe without `ram_mb` runs unscoped: a dead worker cannot leave it running in a scope, but its child
  processes may outlive the worker on some platforms.

## Open questions

- Laptop model storage for ComfyUI: mirror the desktop's models, or per-template download with approval?
- Studio-wallet polling without an agent session: is there a supported token?
- Should several jobs run at once on one worker (per-GPU slots) once telemetry has proven itself?

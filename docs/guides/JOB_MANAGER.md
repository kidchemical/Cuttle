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
| J0 Telemetry v2 | **Done** | Heartbeat `load`: per-GPU VRAM used/total + util (`nvidia-smi`), RAM available, swap, PSI memory/io avg10 (Linux), interactive apps (Unity Editor with a project, Blender UI; headless runs excluded), adapter services (ComfyUI: pids, RSS, VRAM per GPU). `capabilities.py` |
| J1 Reservations | **Done** | `requirements.resources` (`ram_mb`, `vram_mb`, `gpu_slots`, `service`); `admission.admit` inside `claim_jobs`'s `BEGIN IMMEDIATE` transaction; reservation stored on the job (`reserved`), released by any end state; `wait_reason` on queued jobs. Tested with 8 processes claiming at once |
| J2 Isolation | **Done (Linux)** | Jobs that declare `ram_mb` run in `cuttle-job-<id>.scope` under `cuttle-jobs.slice` (`MemoryMax` = reservation + 25 % / ≥1 GB, swap 512 MB, `OOMPolicy=kill`, `choom 1000`); OOM is reported as `oom: …`. Shell recipes and Blender. Windows Job Objects: planned |
| J3 Runs | **Done (flat batches)** | `runs.plan` / `runs.submit`, run watch on the unit-watch records (`mesh_jobs`): status, Stop, exactly-once resume through the chat-ops outbox, polled by the daemon and Flask, refreshed on every host-side heartbeat. Multi-stage DAGs: planned |
| J4 ComfyUI adapter | **Done** | `comfyui_prompt` job type: WebSocket node events → stage, nodes done/total, sampler steps, previews (host worker), per-node seconds + peak VRAM; deterministic prompt id → reattach on retry; cancel via `/api/jobs/<id>/cancel`; optional `start_recipe`. Tested against recorded sessions; not yet run against a live ComfyUI |
| J5 Approval | **Done (no spend)** | `run-plan` returns per-worker fit, estimates and a ready form; `workers.run` action submits only the approved spec (SHA-256 check, once). Spend ledger and per-wallet caps: planned with J6 |
| J6 Tripo adapter | Planned | Developer-API stages, dry-run cost, idempotency key = job id, spend ledger. Studio (MCP) stays agent-driven |
| J7 Estimates | **Partial** | ETA from median duration of past jobs with the same `template` (or type + recipe), corrected by observed progress. `stage_profiles` table and resource predictions from history: planned |
| J8 Card v2 | **Partial** | Watch card shows bars, grid, label, elapsed and ETA; status also carries `errors`, `previews`, `resources`, activity steps (background-work bubble). Preview/resource renderers on the card: planned |
| J9 Distribution | **Partial** | Auto (any worker that fits) or pinned target, chosen in the approval form; requeue on lease loss. Policies `ask`/`stop` when nothing fits, staging inputs to other machines, laptop rollout (needs this branch on it): planned |

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

- Host restart: jobs, reservations and watch records are on disk; the daemon keeps polling while Flask is down.
- Worker restart or lease loss: the job requeues (attempt budget per type); the ComfyUI adapter reattaches to
  the same prompt id or collects its finished result instead of running it twice.
- Agent session gone: nothing to reconnect; the host resumes the chat when the run ends.
- Cancel: Jobs page or the card's Stop; cancellation reaches the worker on its next heartbeat (≤15 s).

## Non-goals

- A second queue, a second progress-card system, or agent-side polling loops.
- Free-form remote shell (unchanged mesh rule).
- Running Tripo Studio MCP calls without an agent.

## Open questions

- Laptop model storage for ComfyUI: mirror the desktop's models, or per-template download with approval?
- Studio-wallet polling without an agent session: is there a supported token?
- Should several jobs run at once on one worker (per-GPU slots) once telemetry has proven itself?

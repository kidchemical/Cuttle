# Cuttle Workers — multi-device compute mesh

**Status:** W1–W2 live; **W2.5 platform verbs** shipped (actions + CLI); **W2b** `blender_render` + `workers.blender-shard` shipped; **W2b+** work-steal chunks + per-frame analytics + render EWMA profiles (default `distribution=work_steal`); **W3** heuristics via `workers.plan` / `POST /api/workers/plan` (auto-enqueue off by default).  
**Related:** [`.cuttle/learnings/FEATURE_REQUESTS.md`](../../.cuttle/learnings/FEATURE_REQUESTS.md) `[FEAT-20260917-001]`, [`docs/ROADMAP.md`](../ROADMAP.md), [`.cuttle_global/docs/cuttle-workers.md`](../../.cuttle_global/docs/cuttle-workers.md) (Brain how-to), [`.cuttle_global/docs/cuttle-jobs.md`](../../.cuttle_global/docs/cuttle-jobs.md), [`SUPERVISED_COORDINATOR.md`](SUPERVISED_COORDINATOR.md), [`MODULARITY.md`](MODULARITY.md).

## Product intent (read this first)

The user talks to **Cuttle**. Cuttle (orchestrator + Brain) decides when work should run on the LAN mesh. Sticky harnesses (`/cursor`, `/codex`, Muse, Hermes, local LLM, …) are **brains plugged into Cuttle** — they must not each learn a private “how to curl `/api/workers`” tutorial.

| Who | Responsibility |
|---|---|
| **User** | Chat intent (“render this blend”, “copy from the laptop”, or nothing special) |
| **Cuttle host** | Intent policy + job queue + claim/lease; optional auto-shard when mesh-worthy |
| **Platform verbs** | Agent-agnostic list / submit / status / cancel (MCP and/or `.cuttle_global/actions`) — same contract for every harness |
| **Context Compiler** | Short rule → `.cuttle_global/docs/cuttle-workers.md` (when to mesh; never invent remote shell) |
| **Jobs → Devices** | **Observability only** (online workers, role you/host, mesh job history). Not the day-to-day control surface |
| **Sticky agent** | Irrelevant to the mesh API — just another tentacle |

**Do not:** teach `/cursor` (or any one CLI) a special workers path.  
**Do:** teach Cuttle once (docs + actions/tools + host scheduler).

## Runtime (what exists now)

| Piece | Location |
|---|---|
| SQLite store (workers + jobs) | `src/api/device_workers/store.py` → `src/data/db/device_workers.db` |
| Coordinator HTTP API | `/api/workers/*` (`routes.py`), registered from Flask |
| Local host worker loop | Daemon thread `run_device_workers_loop` in `cuttle_daemon.py` |
| Client sidecar | `electron/device-worker/cuttle_device_worker.py` (stdlib; shipped in Client asar) + Electron `startWorkerSidecar()` |
| Sidecar log (Client) | `%APPDATA%/cuttle-desktop/device-worker.log` (Electron console on exit) |
| Job types | `ping`, `file_copy`, `blender_render`, `shell`, `execute_shell_*`, `cuttle_self_update` |
| Platform verbs | `.cuttle_global/actions/workers-*.yaml` + `python -m api.device_workers.cli` |
| Host intent | `workers.plan` / `POST /api/workers/plan` (heuristics); `auto_mesh` default off |
| Blender farm | Default **work_steal** chunks (untargeted queue); `distribution=pinned` for legacy equal splits; per-frame timing in job results; EWMA `render_profile` on worker meta |
| Jobs UI | **Devices** tab — observability only |

### Quick test (host)

1. Restart Flask/daemon so routes + local worker start.
2. Open Jobs → **Devices** — host worker should show online within ~poll seconds.
3. Click **Ping local worker** (or `POST /api/workers/jobs` with `{"type":"ping"}`).
4. On the laptop: Electron **Client** → **Update** if offered (`cuttle-desktop@0.2.2+`), then connect to the PC. Worker **auto-enrolls** — no token to type. Needs a real Python 3.11+ (python.org; **not** the Windows Store stub). It should appear under Devices / green titlebar badge.
5. If the sidecar dies, open the sidecar log on the laptop — that file has the real traceback.
6. File copy (paths allowlisted on the **target** worker) — same HTTP envelope; prefer platform verbs once they exist:

```json
POST /api/workers/jobs
{
  "type": "file_copy",
  "target_worker_id": "yoga",
  "submitted_by": "ui-or-agent-id",
  "params": {
    "source": "C:\\Users\\You\\Desktop\\foo.txt",
    "dest": "\\\\server\\share\\staging\\foo.txt"
  }
}
```

### Env / settings

| Key | Meaning |
|---|---|
| `device_workers.enabled` / `CUTTLE_DEVICE_WORKERS_ENABLED` | Master gate |
| *(no manual token)* | Electron **Client** auto-calls `POST /api/workers/enroll` on connect; host stores a per-device bearer and Electron saves it in `desktop-config.json`. Same trust boundary as LAN Client UI (`discovery.lan_access_enabled`). |
| `CUTTLE_DEVICE_WORKERS_TOKEN` | Optional **override only** (legacy); not required for Client workers |
| `CUTTLE_DEVICE_WORKERS_COORDINATOR_URL` | Sidecar → host Flask (Electron sets this from the Client host you already chose) |
| `device_workers.allowed_path_prefixes` | Extra UNC/local roots for `file_copy` |
| Electron `desktop-config.json` `workerMode` | Client sidecar on/off (default on) |

## Thesis

Cuttle should become an **intent-aware compute orchestrator** over several home-lab machines — not “one Windows PC made of two,” and not a thin UI that only *views* a remote Flask.

**One workspace, several computers.** The user talks to Cuttle on the host. Cuttle classifies the job, picks a device (or shards across devices), stages inputs, runs work, and brings results back. Machines advertise capabilities and load; the user stops naming IPs for routine work.

Commercial analogies (Incredibuild, Flamenco) solve pieces of this. Cuttle’s differentiator is **semantic scheduling** (“don’t use the desktop GPU while Unity is interactive”; “render on anything except the interactive host”) plus **backends** (generic worker, optional Flamenco, builds, agents) under one orchestrator — **agent-agnostic**, same as Flask restart actions and project commands.

## Naming (do not confuse)

| Term | What it is | Multi-device workers? |
|---|---|---|
| Electron **Host / Client** | Host runs local daemon + UI; Client is a thin UI pointed at remote Flask | UI mode only |
| **Device worker** (this doc) | Process on a machine that registers, heartbeats, claims jobs | Yes — Client defaults **workerMode** on (orthogonal to UI) |
| `cuttle_jobs` **worker** | Daemon poll loop claiming Gitea `@cuttle` jobs from the jobs host | Same *pattern* (claim/lease/heartbeat); different job types |
| Supervised **worker** | Cursor Auto (or similar) under a coordinator on **one** machine | No — see [`SUPERVISED_COORDINATOR.md`](SUPERVISED_COORDINATOR.md) |
| Sessions API “agent-to-agent” | Same-host session messaging | Not multi-PC yet |

## Client daemon + self-update benchmark

**Problem:** From host chat, “update the laptop checkout and bring Client+worker back” cannot be done safely by Electron alone (killing yourself mid-pull). Same reason the **host daemon** owns Flask restart.

**Answer (not SSH for lifecycle):** a **Client daemon** (`src/scripts/cuttle_client_daemon.py`) that:

1. Owns the remote worker claim loop (Electron skips sidecar when daemon heartbeat is fresh).
2. Accepts allowlisted `shell` recipes (`git_pull`, …) and `cuttle_self_update`.
3. On `cuttle_self_update`, schedules a **detached** updater (`.cuttle_global/scripts/client-self-update.ps1` on Windows or `client-self-update.sh` on POSIX): check the checkout → `git fetch --all --prune` → `git merge --ff-only --no-overwrite-ignore` the configured upstream → stop Client processes → restart Client daemon + Electron Client.

The updater refuses tracked edits, untracked files, detached HEAD, missing upstream,
and local commits/divergence, and rechecks cleanliness after fetch. It neither
stashes nor hard-resets local work. Preserve or reconcile your changes before
retrying. Earlier updater revisions hard-reset the checkout; refresh those
scripts before relying on this contract. See the [release runbook](../../.cuttle/docs/cuttle-release.md).

Platform verb: `workers.self-update` (`target` = laptop `worker_id`). This is the mesh dogfood for “host commands Client lifecycle” without opening inbound SSH for updates.

**Version signal:** `cuttle_version` comes from `electron/package.json`. Bump SemVer only at release time using `.cuttle/scripts/bump-cuttle-version.py`; see the [release runbook](../../.cuttle/docs/cuttle-release.md). Git revision mismatch and stale boot revision also flag workers after ordinary pushes, without a version bump. Host `workers.list` exposes `host_cuttle_version`, `outdated_workers`, and per-worker `needs_update` so Jobs UI / agents can offer self-update.

### Shell transports (three tiers)

| Job type | Transport | Default | Use when |
|---|---|---|---|
| `shell` | Pull-based recipes on the worker | **on** | Safe named ops (`git_status`, `git_pull`, …) |
| `execute_shell_unsafe` | Local cmdline **on the worker** (worker already claimed the job) | **off** (`execute_shell_unsafe_enabled`) | Need real shell; accept risk; optional `execute_shell_prefixes`. Legacy type name `execute_shell` still accepted. |
| `execute_shell_ssh` | Worker runs `ssh user@host command` (OpenSSH client) | **off** (`execute_shell_ssh_enabled`) | SSH is already how you admin that box; still not the enroll/claim plane |
| `cuttle_self_update` | Detached script (daemon-owned) | **on** (job allowlisted) | Update Client checkout + restart UI — **prefer this over shell/SSH for pull+restart** |

SSH is a **shell transport**, not the control plane. Enroll/claim/self-update stay pull-based through the client daemon.

## Platform surface (agent-agnostic)

Wire the mesh the same way other Cuttle capabilities work (see [`MODULARITY.md`](MODULARITY.md), action-forms, project commands):

1. **Stable verbs** — `workers.list` / `submit` / `status` / `cancel` / `wait` / `plan` / `blender-shard` / `self-update` / `shell` as `.cuttle_global/actions/workers-*.yaml` + `python -m api.device_workers.cli`. Global runbook: [`.cuttle_global/docs/cuttle-workers.md`](../../.cuttle_global/docs/cuttle-workers.md).
2. **Host intent** — `workers.plan` / `POST /api/workers/plan` heuristics; `device_workers.auto_mesh` reserved (default off).
3. **Observability** — Jobs Devices + titlebar badge; optional progress via action-form watch later.

SSH is **not** the control plane (workers dial out / claim). Optional later transfer backend only.

## What already exists

| Piece | Location | Reuse |
|---|---|---|
| Gitea job claim/lease/heartbeat | `src/api/cuttle_jobs/` | Protocol shape for device workers |
| mDNS advertise | `src/api/discovery_mdns.py` | Optional discovery of coordinator / workers on LAN |
| Host vs Client Electron | `electron/main.js` | Client UI; skips sidecar when **client daemon** owns worker |
| Client daemon | `src/scripts/cuttle_client_daemon.py` | Worker loop + self-update ownership |
| Detached Client updater | `.cuttle_global/scripts/client-self-update.ps1` | pull + restart Electron/daemon |
| Jobs UI cockpit | `/jobs_page.html` Devices tab | Observability (workers + mesh jobs); not primary submit UX |
| Action forms / project commands | `.cuttle_global/actions`, `.cuttle/commands` | Pattern for agent-agnostic workers verbs |

## Target topology (hybrid)

```text
User (chat on the host)
        │
        ▼
┌───────────────────────────────┐
│  Cuttle Orchestrator (host)   │  intent classify → schedule → backends
│  Job queue (host-first)       │  claim / lease / heartbeat
│  Platform verbs (any brain)   │  actions / agent ops CLIs — not per-CLI tips
└───────────────┬───────────────┘
                │
     ┌──────────┼──────────┐
     ▼          ▼          ▼
  Laptop      Desktop    jobs-host
  worker      worker     (optional later:
  (GPU)     (interactive   durable queue +
             priority)     always-on worker)
```

**Hybrid rules**

1. **Host Flask/daemon is coordinator** for chat-driven work (“copy X from the laptop”, “render this”).
2. **Job store starts on the host** (SQLite or equivalent). Same claim/lease/heartbeat ideas as `cuttle_jobs`.
3. **Later:** durable queue may move to an always-on LAN host so the interactive desktop can also be a pure claim worker and Gitea jobs share infrastructure — without rewriting backends.
4. Electron **Client** stays a UI mode; **worker mode** is orthogonal (default-on). A laptop can be Client UI + device worker at once.
5. **Host does not auto-discover or enroll to another Host.** Two Host-mode machines are two separate coordinators. Extra workers appear only when a device **Client-connects** (or otherwise enrolls) to *this* host. The green titlebar badge counts **other** online workers only — not this PC’s own local worker loop.

## Capability advertisement

Workers periodically register / heartbeat with a blob like:

```yaml
worker_id: worker-a
hostname: WORKER-A
os: windows
interactive_priority: low   # high on the machine the human is using
ac_power: true
cpu_pct: 12
gpu_pct: 0
ram_used_gb: 8
ram_total_gb: 32
capabilities:
  shell: true
  filesystem: true
  python: true
  ffmpeg: true
  blender: true
  blender_gpu: optix      # or cuda / none
  unity: true
  ollama: false
  agent_cursor: false
storage:
  shared_jambit: true    # can see the LAN share
```

Scheduler inputs: required capabilities, estimated cost, “avoid interactive hosts,” AC-only for heavy GPU, user constraints (“except the host”).

## Job lifecycle

```text
submit → queued → claimed → running → (heartbeat) → succeeded | failed | cancelled
                                                      ├─ lease expiry → requeue (safe types, attempts left)
                                                      ├─ lease expiry → cancel (unsafe shell / max attempts)
                                                      └─ queued past expires_at → cancel (TTL)
```

Job envelope (illustrative):

```json
{
  "id": "...",
  "type": "file_copy | shell | blender_render | agent_turn | …",
  "submitted_by": "kcslaptop|kcstower|agent:…",
  "requirements": { "blender_gpu": "optix", "storage": ["shared_jambit"] },
  "inputs": { "paths": [], "content_hashes": [] },
  "params": {},
  "outputs": { "expected": [] },
  "shard": { "of": 1, "index": 0 },
  "priority": 50
}
```

Allowlist job types and paths early — remote shell without policy is a security footgun (see ROADMAP Phase 1).

## Staging and transfer

**Phase W2 (simple):** shared network drive as intermediary (already how the home lab often moves Blender/Unity assets). Jobs pass UNC/mapped paths both sides can see.

**Later:** content-addressed staging — hash dependency trees, transfer only missing blobs, return only outputs. Makes large `.blend` projects practical without full recopies.

Do not assume arbitrary live GUI app state can migrate (Unity editor GPU context, licenses, plugins). **Jobs** are distributable; **running interactive apps** generally are not.

## Backends (orchestrator plugs)

```text
                 CUTTLE ORCHESTRATOR
                        │
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
   Generic worker   Blender path    Builds / other
   (shell, files,   (CLI batches;   (Unity CLI,
    python, ffmpeg,  Flamenco plug   Incredibuild-like
    agent turns)     optional)       later)
```

Cuttle does **not** need to reimplement Blender’s renderer or Unity’s build pipeline. It schedules and stages; backends execute.

## Flagship mesh benchmark: Blender without Flamenco

**Goal:** prove the generic worker can batch Blender work across devices **without** Flamenco — driven by **Cuttle intent**, not a manual Jobs form.

Example job type `blender_render`:

```text
input:   staged .blend + deps (share or content-addressed)
params:  frame_start, frame_end, engine, output_dir, blender_bin
         batch_id, chunk_index, distribution (work_steal|pinned)
shard:   default work_steal — small untargeted chunks; workers claim next when free
         optional pinned — equal frame ranges targeted per worker
run:     blender -b file.blend --python frame_timer.py -o //share/.../frame_ -s N -e M -a
collect: frames on share (or pull-back); batch_status(batch_id) summarizes
fail:    soft idle / hard timeout → shrink to missing durable units + requeue (attempt budget);
         batch-watch / gap-fill enqueues stealable jobs for remaining holes
result:  elapsed_seconds, sec_per_frame, frame_times[], frames_written (range-local); progress on heartbeat
profile: host merges EWMA sec/frame into worker meta.render_profile (observability; not yet a scheduler input)
```

Success criteria for the benchmark: correct frames, sensible scheduling (idle GPU preferred; interactive desktop avoided when possible), recoverable shard failure, chat/Jobs status.

**Flamenco** remains an **optional later plug** if we want Blender Studio’s manager UX or their worker protocol. It is **not** a prerequisite for the farm story and is **not** a hard dependency of W1–W3.

## Intent examples (north star)

- “Copy `Desktop\foo` from the laptop to this PC’s Desktop.”
- “Render sample intro frames 1–120 on whatever is free; don’t touch the host GPU.”
- “Use workers to render this Blender file.”
- “I’m working — move noninteractive jobs elsewhere.”
- “Run this local-model subagent on the laptop.”

## Security (non-negotiable before broad remote exec)

- Worker ↔ coordinator auth (auto-enroll on LAN + per-device bearer); TLS on LAN where practical.
- Allowlisted job types and path roots; no open-ended remote shell for untrusted sessions.
- Phase 1 items (CORS, owner checks, rate limits) remain higher priority than exposing a mesh.
- Client devices: worker opt-in or clearly labeled default; easy disable (`workerMode: false`).

## Implementation phases

| Phase | Name | Outcome |
|---|---|---|
| **W0** | Docs | This guide + ROADMAP + FEAT — **done** |
| **W1** | Worker sidecar + register | Capability ads + heartbeats; Devices observability — **done** |
| **W2** | Generic jobs | `file_copy` + `ping` on host queue — **done** for those types; shell/python still later |
| **W2.5** | Platform verbs | Actions/CLI including self-update + shell recipes — **done** |
| **W2b** | Blender CLI benchmark | `blender_render` + shard — **done**; verified with a sample-scene mesh render |
| **W2b+** | Work-steal + analytics | Default chunk queue; per-frame timings; EWMA profiles — **code done** |
| **W2c** | Client daemon + self-update | Host→laptop pull/restart without SSH — **code done**; dogfood with client daemon on laptop |
| **W3** | Host intent routing | `workers.plan` heuristics — **partial**; auto-enqueue off |
| **W4** | Backends + queue migrate | Optional Flamenco plug; optional always-on durable queue; agent/LLM-on-worker |
| **W5** *(someday)* | Mobile sensor / HITL edge | Optional — **low priority**; see below |

**Next dogfood:** (1) run `cuttle_client_daemon` on laptop, (2) `workers.self-update` from host after a push, (3) Blender shard when both advertise blender.

### Later — mobile (Capacitor) as edge, not farm node

`apps/mobile` today is **LAN UI + notifications only** (no `/api/workers` enroll/claim). That is correct for now.

**Someday (low value vs PC mesh):** a thin native/JS edge — camera/`capture_photo` → upload, richer HITL on the phone for unsafe-shell approvals, maybe `ping` + battery/GPS ads — **not** Python sidecar, Blender, UNC `file_copy`, or shell recipes. Keep mobile out of the PC worker executor path unless a real pinch use-case shows up.

## Non-goals

- Transparent migration of arbitrary running GUI apps (Unity/Blender editor sessions) across PCs.
- Rebuilding Flamenco’s full manager product inside Cuttle (optional *plug* only).
- Collapsing Electron Host/Client into “worker” (UI mode ≠ compute role).
- Public internet mesh without Tailscale/VPN + auth.
- Replacing `cuttle_jobs` Gitea flow in W1 — keep it; converge protocols over time.
- **Per-harness workers tutorials** (Cursor-only, Codex-only, …) — platform contract only.
- **Manual Jobs submit as the product path** — observability/debug only.
- **SSH as required control plane** — enroll/claim/self-update stay pull-based; SSH is optional for `execute_shell_ssh` only.
- **Ungated arbitrary remote shell** — `execute_shell_unsafe` / `execute_shell_ssh` default **off**; prefer recipes + `cuttle_self_update`. When enabled, first use in a worker process is HITL-gated (Approve once / session / Deny); one session grant covers both kinds. Queued jobs expire by type TTL; unsafe kinds never requeue after lease death.

## Open questions (defer to implementation)

- Future extensions to the existing workers actions and agent ops CLI contract.
- How much W3 classification is rule/heuristic vs cheap router LLM.
- How Jobs UI merges Gitea jobs vs device-worker jobs in one cockpit (still observability).

## Verification

1. A laptop worker registers on the host; appears online with capabilities; **you** / **host** role tags correct on each machine.
2. File copy job via platform verb (not a Cursor-only recipe): tower ↔ laptop paths via share or allowlist.
3. Blender CLI: two shards on two machines from a chat intent; frames land in shared output; one shard kill recovers.
4. Interactive host skipped when `interactive_priority=high` and alternatives exist.
5. Same workers verbs work from at least two different sticky agents without harness-specific docs.
6. Host enqueues `cuttle_self_update` → laptop client daemon pulls + restarts Electron → worker online again.

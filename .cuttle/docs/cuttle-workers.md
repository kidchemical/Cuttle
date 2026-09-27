# Cuttle Workers (hub — agent-agnostic)

LAN **device worker** mesh: host coordinates; machines claim allowlisted jobs.

Canonical design: `src/docs/guides/CUTTLE_WORKERS.md` (under the Cuttle install).
Install-local LAN / SSH / hostname notes: `.cuttle/personal/docs/cuttle-workers.md`.

## When to use (intent)

Match this doc when the user (or a good batch candidate) involves:

- Multi-device / LAN workers / “use workers”
- File copy / staging across PCs
- Blender / render farm / frame sharding
- **Remote Client update** (git pull + restart Cuttle on laptop from host)
- Shell on a worker (recipes, local execute_shell_unsafe, or execute_shell_ssh)
- Mesh compute, capability ads, claim/lease jobs

**Not** this doc: Gitea `@cuttle` jobs → `cuttle-jobs.md`; same-machine supervised Cursor → `SUPERVISED_COORDINATOR.md`.

## Product rule

Teach **Cuttle**, not a sticky harness. Prefer platform verbs — not inventing per-CLI curl.

## Mesh self-serve (hard)

When device workers can do the work, **agents execute on the mesh** — do not hand the user
scripts, “open Documents and run X”, or Desktop scavenger hunts.

| Do on the mesh | Only ask the user when |
|---|---|
| `file_copy`, shell recipes, `workers.self-update`, blender jobs | No capable worker online |
| Place/repair Desktop `.lnk` (icon + target), fix shell folders | HITL modal only they can approve |
| Pull logs, git_pull, registry/desktop repairs via worker | Truly interactive UI (password prompt, etc.) |

Dumping `.cmd` / `.ps1` / `.vbs` onto the user’s Desktop as the primary deliverable is a
last resort. Prefer a real shortcut matching Host style (`python.exe` + args + `cuttle_logo.ico`).

## Client daemon (required for self-update)

Electron alone cannot safely pull+restart itself (same class of problem as killing Flask from inside Flask).

| Process | Role |
|---|---|
| **Host** `cuttle_daemon` | Flask + local worker; owns Flask restart |
| **Client** `cuttle_client_daemon` | Owns device-worker loop + **cuttle_self_update**; outlives Electron |
| Electron Client | UI only when client daemon is running (skips sidecar if daemon heartbeat is fresh) |

```powershell
cd <CuttleInstall>
.venv\Scripts\python.exe src\scripts\cuttle_client_daemon.py
```

Connect Electron Client to the host at least once first (writes `desktop-config.json`).

## Shell: three tiers

| Type | What | Default |
|---|---|---|
| `shell` | Named recipes only (`git_pull`, `git_status`, …) | available |
| `execute_shell_unsafe` | Free-form **local** cmdline on the worker after claim | **off** — `device_workers.execute_shell_unsafe_enabled` |
| `execute_shell_ssh` | Worker runs OpenSSH `ssh user@host …` | **off** — `execute_shell_ssh_enabled` + `ssh_host` |
| `cuttle_self_update` | Detached pull + restart Electron/daemon | prefer for Client updates |

Use **SSH for ad-hoc shell** when you already trust SSH into that machine. Do **not** use SSH for enroll/claim or as a substitute for `cuttle_self_update`.

Optional: `device_workers.execute_shell_prefixes` (list of allowed command prefixes) when either unsafe/ssh execute is on.

### LAN-only SSH

Configure keys / `authorized_keys` / `ssh_host` in `src/settings.json` for **this** install.
Document your LAN CIDR, key paths, and host aliases under
`.cuttle/personal/docs/cuttle-workers.md` — do not commit them to the tracked hub doc.

Helper script (when present): `.cuttle/scripts/install-cuttle-mesh-lan-key.ps1`.

**HITL:** first `execute_shell_unsafe` or `execute_shell_ssh` in a worker process pops a Cuttle modal.
**Queue TTL:** jobs get `expires_at` (defaults vary by type). Override with `ttl_seconds` / `expires_at` on submit.
**No requeue for unsafe:** lease death on `execute_shell_*` cancels; safer types requeue until `max_attempts`.

**Version bump (required for mesh-visible updates):** Workers advertise `electron/package.json` `version` as `cuttle_version`. Before pushing Client/worker runtime changes, bump it:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .cuttle\scripts\bump-cuttle-version.ps1
# or -Minor / -Major / -Set 0.3.0
```

Bump when any of these change: device_workers executor/store/claim/HITL, client daemon, Electron sidecar/`main.js` worker spawn, self-update script. Jobs → Devices shows host version, flags mismatches (⚠), and **Update** queues `cuttle_self_update`. Clients pull from the **git remote** (`git fetch` + `reset --hard @{u}`), not the Host working tree — unpushed Host commits never appear in Update.

**After bump → commit → push (reload ladder, least disruptive first):**

| Order | When | What |
|---|---|---|
| 1 | Only HTML/JS/CSS / static web UI | Hard-refresh the Cuttle shell (no process kill) |
| 2 | Host titlebar / `CUTTLE_PACKAGE_VERSION` still stale after bump | `electron.host-restart` or `workers.self-update` `target=<host worker_id>` with `no_daemon` (UI only; Flask stays up) |
| 3 | Outdated Client workers | `workers.self-update` `target=<client>` (mesh; do it yourself) |
| 4 | Python that Flask imports changed | Emit the **`flask.restart` action form** (prefer graceful). Do **not** autonomously `/restart when-idle` or force — the user may be mid-chat and needs to see/choose |

Do **not** skip Host Electron when you bumped `electron/package.json` — Clients will show the new version while Host Electron still advertises the old badge until step 2.

## Platform verbs

| Action | Purpose |
|---|---|
| `workers.list` / `plan` / `submit` / `status` / `wait` / `cancel` | Core mesh |
| `workers.shell` | Submit `type=shell` + `recipe` |
| `workers.self-update` | `cuttle_self_update` on `target` worker_id |
| `workers.blender-shard` | Frame shards — default **work_steal** chunks; `distribution=pinned` for equal pinned ranges; optional `chunk_size` / `batch_id` |
| `gap-fill` (CLI) | Scan batch output for missing durable frames → enqueue stealable jobs |
| `batch-watch` (CLI) | Write job_watch status with **overall + per-worker `bars`**; auto gap-fills when idle+incomplete |

CLI: `.cuttle\scripts\workers-cli.ps1 <verb>` or `python -m api.device_workers.cli <verb>`.

### Long-job reliability (generic)

Mesh jobs no longer use a single wall-clock `subprocess.run(timeout=)`. Every long
type runs under **soft idle + hard absolute** timeouts:

| | Soft (idle) | Hard (absolute) | Progress mode |
|---|---|---|---|
| `blender_render` | 45m without a new durable frame | 24h safety net | `units` (frames on disk) |
| `shell` / `execute_shell_*` | off (0) | 4h | `alive` (process still running) |
| Override | `params.idle_timeout_seconds` | `params.hard_timeout_seconds` or raise via `timeout_seconds` | `params.progress_mode` |

Heartbeats may carry `progress` (`units_done` / `last_unit_id`). Lease renew still
happens on a timer so Cycles/Unity builds outlive the 10m claim lease.

**Partial durable progress:** if Blender is killed mid-chunk after some `frame_*`
files exist, the worker **shrinks** `frame_start`/`frame_end` to the first missing
contiguous span and **requeues** (attempt budget applies). `fail(retry=True)` now
honors `max_attempts` (same as lease reclaim) — no infinite loops.

**Gap-fill:** `python -m api.device_workers.cli gap-fill --batch-id …` (and
`batch-watch` when a batch finishes failed/incomplete) inventories missing frames
and enqueues stealable size-1 chunks. Idempotent `frame_NNNN` outputs make overlap safe.

**Chunk defaults:** `auto_chunk_size` targets ~8 chunks/worker, clamp **1–8** (not 4–24).
Explicit `--chunk-size` never silently re-expands. Soft max_chunks raised so long
ranges stay stealable.

Do **not** hardcode Cycles vs Eevee SPF tables — optional generic
`params.secs_per_unit_hint` can raise the hard cap when known. EWMA
`render_profile` remains observational until a later weighted planner.

**Devices list order:** host is always slot `#1`; peers get stable `#2+` by
`registered_at` (not `last_seen`). Heartbeats must not reshuffle the table.

**Stale process (version matches, code does not):** each worker advertises
`boot_git_rev` (rev loaded into the live claim loop) alongside disk
`cuttle_git_rev`. If they differ → `stale_process` + `needs_update` (⚠ / Update)
even when package version matches. The claim loop hot-reloads
`device_workers` modules when it detects disk moved ahead — do not ask the user
to restart; if reload is not enough, `workers.self-update` yourself.

### Work distribution (agent best practice)

**What the mesh already does (native):**

| Capability | Today |
|---|---|
| Default `distribution=work_steal` | Untargeted chunk queue; idle workers claim next |
| `distribution=pinned` | Equal frame ranges pinned per worker (legacy) |
| `auto_chunk_size(total, workers)` | ~8 chunks/worker, clamp **1–8** — not SPF-/engine-aware (pass `chunk_size` / `secs_per_unit_hint` when known) |
| Soft idle + hard timeout | Progress/alive based; see Long-job reliability |
| Partial shrink + gap-fill | Timeout/fail keeps durable frames; requeues missing / `gap-fill` |
| Worker pick order | Prefer remote + non-interactive + lower GPU load |
| EWMA `render_profile` | Recorded on success (sec/frame by engine) — **not yet** used to size chunks |
| `workers.plan` | Intent classification / advice — does **not** pick chunk size |
| Smart mid-job rebalance / throttle | **Not** implemented — claimed work stays sticky until done/fail/lease reclaim |

Version/git mismatch does **not** mark a worker offline (online = recent heartbeat only).

**Generic rule (any mesh job type):** size the stealable unit so a slow or sleeping worker cannot strand a large fraction of remaining work. Prefer many small queue items over few large ones when:

- peers differ a lot in throughput, or
- machines may lid-close / Modern Standby / drop heartbeat, or
- you may want to adapt who does what without canceling in-flight claims

Large sticky units are fine when units are short, peers are matched, and reclaim cost is high.

**Do not** try to “reshape” asymmetrical in-flight chunks mid-batch — cancel + requeue (or finish) is the only clean path. Design the initial unit size so steal handles imbalance.

**Blender / Cycles specifically:**

- Prefer `work_steal` over `pinned` for multi-GPU farms.
- Auto chunks are already steal-friendly (≤8). For very heavy frames, `--chunk-size 1` is still best.
- Fast Eevee / Workbench: auto 1–8 is usually fine.
- Pass `--chunk-size` / `secs_per_unit_hint` when unit cost is known; do not assume SPF profiles yet.
- Idempotent `frame_NNNN` outputs make small chunks + occasional overlap safe.
- Use `batch-watch` (auto gap-fill) or `gap-fill` after shard failures — do not babysit missing frames by hand.

```powershell
# Cycles farm — steal-friendly
python -m api.device_workers.cli blender-shard ... --distribution work_steal --chunk-size 1

# Fast preview — auto chunk OK
python -m api.device_workers.cli blender-shard ... --distribution work_steal
```

### Mesh progress bars (hard)

When emitting a watch card for a multi-worker mesh bake:

1. Use one overall `kind:primary` bar and one `kind:worker` bar per worker (distinct styling).
2. Prefer `batch-watch` so status JSON always carries `bars` (do not write overall-only):

```powershell
python -m api.device_workers.cli batch-watch --batch-id v16-cycles-1080p24 --id v16-cycles-1080p24
# optional loop: --loop --interval 60
```

3. Card `watch.url` must point at that status file (`/output/<id>-status.json`).
Details / schema → `action-forms.md` (Multi-bar progress).

### Benchmark: update a Client from host

1. Client: client daemon + Electron Client → online in `workers.list`.
2. Host (after push to git remote): `workers.self-update` `target=<client_worker_id>`.
3. Detached updater: `git fetch` + `git reset --hard @{u}` + `git clean -fd` → stop Electron → restart daemon + Client.
4. Confirm with `workers.list` / `shell`+`git_rev_parse`.

Log: `%LOCALAPPDATA%\cuttle-desktop\client-self-update.log`

## Do not

- Make SSH the enroll/claim control plane
- Enable `execute_shell_unsafe` / `execute_shell_ssh` casually on untrusted sessions
- Expect Electron-only sidecar to self-update (use **client daemon**)
- Treat Capacitor mobile as a PC mesh worker (no Python claim loop / Blender / shell on phone). **Someday / low priority:** thin sensor+HITL edge only — see `CUTTLE_WORKERS.md` § W5.

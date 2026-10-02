# FEATURE REQUESTS

User-requested capabilities. Format: `[FEAT-YYYYMMDD-XXX] capability_name`

| Situation | Action |
| --- | --- |
| User wants missing feature | Log Requested Capability, User Context, Complexity |
| Related to existing | Add **Related Features** in Metadata |

---
<!-- Add entries below -->

## [FEAT-20260927-001] web_chat_api_dependency_map

- **Priority:** Low · **Status:** Done (map) / Backlog (extraction) · **Area:** architecture / flask
- **Requested capability:** Permanent map of `web_chat_api.py` so agents do not duplicate routes or add reverse imports. Extraction of settings/workers then the chat-turn coordinator only when the monolith slows product work.
- **User context:** CH-000743 — close security-hardening milestone; do not refactor 15k lines as the next product step.
- **Notes:** Canonical doc `docs/guides/WEB_CHAT_API.md`. Do not extract in the same effort as feature work.
- **Complexity:** XL (extraction) / S (map)
- **Related:** `docs/ROADMAP.md`, `docs/guides/MODULARITY.md`, https://github.com/kidchemical/Cuttle/issues/6

## [FEAT-20260926-003] github_app_forge_parity

- **Priority:** Medium · **Status:** Pending · **Area:** forge / cuttle-jobs / git
- **Requested capability:** GitHub parity with the Gitea `cuttle` user. Cuttle acts on GitHub through a per-install **GitHub App** (owner's is `Cuttle Harness` → `cuttle-harness[bot]`): create/label/assign/close issues, comment, push `cuttle/issue-N` branches, open PRs, and author commits as the bot so its avatar shows in Contributors. `@cuttle` triggers via App webhook → cuttle-jobs (public endpoint needed; Gitea host is LAN-only) or polling. Also add the bot as a `Co-authored-by` trailer option for commits Cuttle makes in GitHub repos (replaces Cursor's own trailer, which is now disabled in `~/.cursor/cli-config.json`).
- **User context:** CH-000722 — moving Cuttle to github.com/kidchemical as a portfolio; wants "Cuttle" credited like on Gitea, and the same auto-create / auto-resolve issue flow.
- **Notes:** Each self-hosted Cuttle creates its own App ("Only on this account"): a shared public App would mean distributing its private key. Auth = App ID + private key (.pem, outside the repo) → short-lived installation token. Bot commit email: `<bot-user-id>+cuttle-harness[bot]@users.noreply.github.com`.
- **Progress:** App created and installed (issues / PRs / contents write); `GITHUB_APP_*` keys in `src/.env` (not read by code yet). Profile README commit co-authored by the bot.
- **Complexity:** L
- **Related:** `.cuttle_global/docs/gitea.md`, `.cuttle_global/docs/cuttle-jobs.md`, `src/api/gitea/`, `src/api/cuttle_jobs/`, `src/scripts/utilities/git_pending_changes.py`, `src/api/edit_attribution/`

## [FEAT-20260926-002] query_log_inspector

- **Priority:** Medium · **Status:** In Progress · **Area:** chat / observability
- **Requested capability:** Replace static per-turn HTML query reports (`web/logs/query_report_*.html`, `target=_blank`) with a structured turn inspector: event timeline (harness, tools, thinking, compiled context / “what was sent”), searchable index, open as an in-pane overlay. Storage: JSON/event rows (SQLite or sibling store), not HTML as source of truth. One viewer app; keep a rail index for search.
- **User context:** CH-000715 — modernize prompt query logs for harness-of-harnesses (CLI adapters, Brain inject, thinking blocks); popup per viewport vs new window.
- **Notes:** Slice 1: event stream + `GET /api/query-log/<id>` + in-pane overlay / pop-out. HTML dumps removed; JSON sidecars are source of truth.
- **Complexity:** L
- **Related:** `src/api/query_events.py`, `src/api/query_tracker.py`, `src/web/query_log.html`, `src/api/agent_harness/kernel.py`, `[FEAT-20260926-001]` (local telemetry / dashboards)

## [FEAT-20260926-001] dashboards_model_benchmarks

- **Priority:** Medium · **Status:** In Progress · **Area:** web / dashboards
- **Requested capability:** Dashboards hub in the shell rail for ongoing tracked landscapes. First live dashboard is **Model Benchmarks** (DeepSWE cost × score × time scatter, effort as separate points, NEW badges, BenchmarkList discovery). Later: more tiles + **My Cuttle Performance** overlay from local turn telemetry.
- **User context:** CH-000584 — public JSON feeds (DeepSWE, BenchmarkList, SWE-bench, OpenRouter) to pick models; Cuttle-local outcomes as a second dataset.
- **Complexity:** L
- **Progress:** Hub + DeepSWE v1.1 live JSON + BenchmarkList discovery panel + rail item. Not yet: SWE-bench dump, OpenRouter, Artificial Analysis, local Cuttle telemetry.
- **Related:** `.cuttle_global/docs/dashboards.md`, `src/api/dashboards/`

## [FEAT-20260917-001] cuttle_device_workers

- **Priority:** High · **Status:** In Progress · **Area:** workers / multi-device / orchestrator
- **Requested capability:** LAN device workers (orthogonal to Electron Host/Client UI): register capabilities + heartbeats; host-first job queue (hybrid — durable queue may move to an always-on host later); **agent-agnostic** intent-aware scheduling (Context Compiler + platform verbs / host classifier — not per-harness `/cursor` tips); generic jobs (file copy, shell); Blender CLI frame sharding as mesh benchmark **without** requiring Flamenco; optional Flamenco/build backends later; agent-to-agent / local-LLM-on-worker later. Jobs Devices = observability only.
- **User context:** CH-000391 — run Cuttle on laptop + tower; Client mode should also run (or default) worker mode so tower can e.g. copy files from Yoga Desktop or farm Blender frames across GPUs. ChatGPT framing: one workspace / several computers; orchestrator + backends. Clarified: teach **Cuttle**, not sticky agents; smart batching when workers available.
- **Complexity:** XL
- **Progress:** W0–W2.5 done; W2b blender job/shard code done + JamBit v11 dogfood; **W2b+ work-steal chunks (default), per-frame result analytics, EWMA `render_profile`** code done. W2c Client daemon + `cuttle_self_update` / allowlisted `shell` recipes code done. Remaining: content-addressed staging, optional auto_mesh, weighted static plans using profiles, W4.
- **Related:** `docs/guides/CUTTLE_WORKERS.md`, `.cuttle_global/docs/cuttle-workers.md`, `docs/ROADMAP.md`, `src/api/cuttle_jobs/`, `src/api/device_workers/`, `src/api/discovery_mdns.py`, `.cuttle_global/docs/cuttle-jobs.md`

## [FEAT-20260917-002] mobile_worker_sensor_edge

- **Priority:** Low · **Status:** Backlog · **Area:** workers / mobile
- **Requested capability:** Optional thin Capacitor edge (not PC worker): camera/`capture_photo` → upload, HITL approvals on phone, maybe `ping` + battery/GPS ads. Explicitly **not** Python sidecar, Blender, UNC file_copy, or shell recipes.
- **User context:** CH — Android/iOS Cuttle is LAN UI today; ask whether phones could squeeze limited mesh value in a pinch. Agreed: note as someday, low value vs PC mesh for now.
- **Complexity:** M
- **Related:** `apps/mobile/`, `CUTTLE_WORKERS.md` W5, `[FEAT-20260917-001]`

## [FEAT-20260820-001] markdown_pipe_tables_in_chat

- **Priority:** Medium · **Status:** Done · **Area:** chat / markdown
- **Requested capability:** Render GitHub-flavored markdown pipe tables as real HTML `<table>` in assistant bubbles (not raw `|` text). Agents should prefer markdown tables for tabular data; Vega remains for charts.
- **User context:** CH-000219 enemy damage/health tables showed as raw pipes; Vega text grids looked poor.
- **Complexity:** S
- **Related:** `src/web/js/chat_page.js` `formatMessage`, `src/web/css/chat_page.css` `.message-table`, `src/api/cuttle_ui_capabilities.py`

## [FEAT-20260817-001] deepseek_as_routed_tentacle

- **Priority:** Medium · **Status:** Requested · **Area:** agent_router / agent_harness
- **Requested capability:** Treat bundled `/deepseek` (Flash) as a first-class cheap tentacle in CuttleRouter OOB discovery and the Phase 0 preference table — not only as a manual slash agent. Resume stays off until dsh headless supports it.
- **User context:** Home-lab harness-of-harnesses; DeepSeek is a tentacle to host, not a rewrite of Cuttle (CH-000162). Adapter already exists; routing catalog does not list it yet.
- **Complexity:** M
- **Related:** `docs/guides/MODULARITY.md`, `.cuttle/docs/agent-router-todo.md` Phase D, `src/api/agent_harness/agents/deepseek/`

## [FEAT-20260819-002] programmatic_commands_with_watch

- **Priority:** Medium · **Status:** Done · **Area:** chat / project commands
- **Requested capability:** Long jobs (`/build`, `/deploy`, …) run as `execute: shell`; Flask attaches the native watch card. LLM only on Continue / failure explanation. Same UX; no per-agent re-teach (ADDING_AN_AGENT §7).
- **User context:** CH-000190 Escape Purgatory after hybrid prompt-orchestrated ship.
- **Complexity:** S
- **Related:** `src/api/project_commands.py` `watch:`, `format_project_command_shell_reply`

## [FEAT-20260819-001] job_watch_native_policy

- **Priority:** Medium · **Status:** Done · **Area:** chat / Context Compiler
- **Requested capability:** Treat the action-form progress bar as native Cuttle UI (any project / any agent CLI), with WHEN/WHEN NOT in `cuttle_ui_capabilities`, plus `python -m api.job_watch` for a standard `/output/<id>-status.json` file. Agents should emit the card without per-project copy-paste.
- **User context:** Escape Purgatory `/build`+`/deploy` (CH-000190) after TRELLIS download watch (FEAT-20260818-002).
- **Complexity:** S
- **Related:** `src/api/cuttle_ui_capabilities.py`, `src/api/job_watch.py`, `src/web/js/chat_page.js` `watch`

## [FEAT-20260818-002] action_form_job_watch

- **Priority:** Medium · **Status:** Done · **Area:** chat / action forms
- **Requested capability:** Action-form progress bar that polls a job status file, plus Continue-when-finished (auto-resume this chat) vs I'll-reply (lock only).
- **User context:** Long Hugging Face TRELLIS.2 download should not block the agent turn.
- **Complexity:** M
- **Related:** `src/web/js/chat_page.js` `watch` + `__watch_resume__` / `__watch_park__`

## [FEAT-20260818-001] comfyui_trellis2_local_3d

- **Priority:** Medium · **Status:** In progress · **Area:** tools / MCP
- **Requested capability:** Local ComfyUI + Microsoft TRELLIS.2 for textured 3D assets from prompts and reference images, callable from Cuttle and other agents via MCP.
- **User context:** Game/Unity asset pipeline (CH-000184). RTX 3080 10GB — low-VRAM workflows. Engine installed at `F:\AI\ComfyUI-Trellis` (Python 3.11), not in the Cuttle git tree.
- **Notes:** TRELLIS.2-4B weights complete 2026-08-19 (~16.2 GB, 9 safetensors). DINOv3 still gated; generation blocked until HF license + login.
- **Complexity:** L
- **Related:** `.cuttle_global/docs/comfyui-trellis2.md`, `src/tools/comfyui/`

## [FEAT-20260817-002] cuttle_cli (deferred — product shell)

- **Priority:** Low · **Status:** Deferred · **Area:** product
- **Requested capability:** A user-facing `cuttle` command-line client / shell.
- **User context:** Nice-to-have only; explicitly not a current interest. Daemon + web/Electron chat + Discord remain the product.
- **Notes (2026-09-20):** Distinct from **agent ops** CLIs (`python -m api.chat_cli`, workers, brain). Those are the preferred pattern for agent-facing verbs — see `.cuttle/docs/agent-ops-cli.md`. This FEAT stays deferred for a product shell only.
- **Complexity:** L
- **Related:** `docs/guides/MODULARITY.md` goal 7, `docs/ROADMAP.md` “Not on the table”

## [FEAT-20260928-001] oobe_first_owner

- **Priority:** Medium · **Status:** Proposed · **Area:** auth / onboarding
- **Requested capability:** First account created during first-run OOBE becomes Owner (persisted `role` on the users row). `OWNER_USER_EMAIL` drops to lockout-recovery override only. Eliminates ownerless deployments where the env names an identity that never signs in.
- **User context:** CH-000764 — operator locked out of commit/restart by env pointing at an unused Google address; required manual `.env` edit + Flask restart to restore.
- **Notes:** Guests never owners (unchanged). Distinct Owner vs Admin roles deferred until two admins exist.
- **Complexity:** S
- **Related:** `src/api/http_authz.py`, `src/.env.example`, `docs/ROADMAP.md` Auth bullet

## [FEAT-20260928-002] per_user_project_scoping

- **Priority:** Low · **Status:** Proposed · **Area:** auth / projects / git
- **Requested capability:** Projects owned per user (`user_projects` membership; owner sees all). All project reads/writes and git endpoints scoped to membership; path containment per user root. Trigger: first genuinely shared host — not before.
- **User context:** CH-000764 — solo + own-hosts model needs none of this; household/shared-box scenario would.
- **Notes:** Auth decorator is ~5%; per-user path sandboxing, quotas, and commit authorship are the real work. No quotas/audit until requested twice.
- **Complexity:** XL
- **Related:** `[FEAT-20260928-001]`, `src/data/db/projects.db` (no user column today), `src/scripts/utilities/git_pending_changes.py`

## [FEAT-20260928-003] scoped_project_grants

- **Priority:** Low · **Status:** Proposed · **Area:** auth / projects
- **Requested capability:** Share exactly one owned project with exactly one user (roommate/contractor pattern). Thin slice of per-user scoping: single grant row, same enforcement points.
- **User context:** CH-000764 — "grant friend roommate access to a project that I own, and just that."
- **Notes:** Depends on `[FEAT-20260928-002]` machinery; shippable first as the minimal version of it.
- **Complexity:** M
- **Related:** `[FEAT-20260928-002]`

## [FEAT-20260928-004] supervisor_parental_controls

- **Priority:** Medium · **Status:** Proposed · **Area:** auth / observability / chat
- **Requested capability:** Supervisor levers per account: spend caps, model allowlists, time windows, read-only activity view. Raw material already exists (per-user transcripts, per-session accounting, query-log spend). No competitor does supervised kid access with real logs.
- **User context:** CH-000764 — home-lab dad pattern: kid uses AI on their phone, parent monitors and caps usage.
- **Notes:** Read-only supervisor must be enforced server-side, not just hidden in UI.
- **Complexity:** L
- **Related:** `src/api/query_tracker.py`, `src/api/cuttle_brain.py`, `[FEAT-20260928-001]`

## [FEAT-20260928-005] shared_farm_queue_multihost

- **Priority:** Low · **Status:** Proposed · **Area:** workers / cuttle-jobs / mesh
- **Requested capability:** Share one render/compute farm across many per-teammate Hosts. Decided direction: shared `cuttle-jobs` queue (Gitea-backed) as the commons — hosts submit, farm polls — not mesh multihoming. Worker loops stay single-homed (one coordinator URL + token per process); mesh membership remains per-host for personal devices.
- **User context:** CH-000764 — JamBit HQ model: every employee runs a Host, one farm serves all. Explicitly rejected: N worker registrations per machine (static split, uncoordinated contention) and manual coordinator re-pointing.
- **Notes:** Claim protocol already has capability ads, leases, heartbeats; multihoming would still need cross-coordinator capacity accounting to avoid double-booking a GPU. Queue beats mesh gossip on fairness, persistence, debuggability. Do not build until a second Host needs the farm.
- **Complexity:** M (shared queue posture) / XL (true multihoming — not recommended)
- **Related:** `.cuttle_global/docs/cuttle-workers.md`, `src/api/cuttle_jobs/`, `src/api/device_workers/worker_loop.py`, `[FEAT-20260928-002]`

## [FEAT-20261001-001] development_shadow_instance_and_daemon_rollback

- **Priority:** High · **Status:** Proposed · **Area:** dev lifecycle
- **Requested capability:** Ephemeral real-app validation (shadow Flask on an isolated port/DB, no daemon) plus daemon fallback: known-good immutable snapshot, fault-injection fallback proofs, and one-production-writer rollback before any automatic recovery. Phased S1 shadow bootstrap → S2 B1 journey → S3 daemon shadow → S4 rollback plane (S5 blue/green only on need).
- **User context:** CH-000856 — safe self-test path while the manager is hosted by Flask; no safe activation without independent recovery.
- **Notes:** Design proposed, not implemented: `docs/architecture/development-instance-safety.md` (baseline `4845233c`). Reuses existing owners (`runtime_paths`, settings/auth/project/persist, execution seams); no new product shell/MCP; no `src/api` module importing `web_chat_api`. Multi-daemon blocked on process-ownership fixes first. S1 assigned CH859 B1 worktree, S2 assigned CH860 (neither done yet); S3/S4 deferred followup, not B1 prerequisites — existing external independent recovery stays valid for final activation.
- **Complexity:** L
- **Related:** `docs/architecture/development-instance-safety.md`, `src/tests/test_action_form_process_restart.py`, `src/scripts/cuttle_daemon.py`, `src/api/agent_router/supervised/test_isolation.py`

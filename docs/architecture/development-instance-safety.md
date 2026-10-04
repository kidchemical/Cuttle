# Development-instance safety: shadow app first, daemon ownership before multi-daemon

Status: **B1 and S1/S2 integrated and live; Stop/resend acceptance completed**,
including the user's successful real vendor test (independently verified).
Historical gate runs below (S1 37 pass; S2 real-HTTP 34 pass / 66 executions /
zero guard denials; combined real-browser 3 pass no skips; shadow/HTTP/boundary
71 pass; broad 2255 pass / 61 skipped) remain historical evidence, not a current
activation block. Baseline: source snapshot `4845233c`; live `ss`/PID evidence is
transient runtime state, not source.

Scope: S1/S2 only before B1; S3/S4 are deferred followup, not B1 prerequisites.
This doc supports one loopback HTTP server running the real app — no daemon, no
main bootstrap.

## 1. Listener audit

`web_chat_api.py.__main__` starts the primary listener plus same-`app`
companions; `lan_access` owns the port constants (`LAN_PHONE_HTTPS_PORT`,
`LAN_HTTP_FALLBACK_PORT`) and LAN bind/CORS/firewall; the daemon owns the
Flask process and its :8080 health check (`FLASK_PORT` in `cuttle_daemon`)
— it does not own socket setup, and there is one app served through multiple
listener/server objects, plus no separate daemon-control listener.

| Listener | Bind | Owner | Current uses | Necessary |
|---|---|---|---|---|
| Primary HTTPS `:8080` | `lan_access.resolve_bind_host` → `127.0.0.1` LAN-off, `0.0.0.0` LAN-on; served by `web_chat_api.__main__` via `app.run` | `web_chat_api.__main__` socket setup | All chat/API traffic; daemon health + internal-base default (`CUTTLE_INTERNAL_API_BASE` → `https://127.0.0.1:8080` in `api/internal_http`) | Yes — primary |
| Companion HTTP `:8000`, same Flask `app` (**not** a tombstone/redirect) | `0.0.0.0` only when LAN is enabled and a LAN IP is found; otherwise the `127.0.0.1` branch, in `web_chat_api.__main__` | `lan_access` (port constant) + `web_chat_api.__main__` (socket) | CURRENT preferred Electron HTTP origin (`resolveUiBaseUrl` in `electron/main.js`) + mobile cleartext fallback (`httpFallbackBase` in `MainActivity.java`); rationale comment in `electron/main.js` (Chromium TLS/SSE pool wedge) | Not intrinsically — Electron falls back to HTTPS `:8080` when HTTP is down, so the API does not require it. Preserve until the client-compat/TLS-SSE rationale is tested; configure/deprecate only after |
| (Optional) Phone TLS `:8888`, same `app` | `0.0.0.0`, only when LAN enabled with a LAN IP (phone-server branch in `web_chat_api.__main__`) | Same as above | Phone portal HTTPS (`lan_phone_portal_url`) | Only with LAN |

No distinct domain, routes, or executor per listener. The daemon's raw TCP
connect to 127.0.0.1:8080 (`cuttle_daemon._flask_port_open` readiness check) succeeds
against any live listener there, so a second instance can mistake the
production listener for its own readiness signal. `CUTTLE_INTERNAL_API_BASE`
overrides internal clients only; there is no listener-port knob — listener
ports are not configurable today. Future policy, unimplemented: CLI > env >
config > defaults, loopback default with explicit LAN, ephemeral shadow ports
with bound-port reporting, explicit instance identity.

## 2. Shadow runbook

Agent-ops only — no generic `cuttle dev` command; existing owners unchanged.

```bash
PYTHONPATH=src .venv/bin/python -m api.dev_instance prepare --candidate .
PYTHONPATH=src .venv/bin/python -m api.dev_instance up --candidate . --port 0
```

Run from the project root. Optional arguments, explained separately:
`--candidate DIR` (default: cwd) selects the candidate checkout;
`--shadow-id ID` names the instance; `--port N` fixes the dev port
(`0` = ephemeral); `--scenario NAME` picks the child executor scenario
(default `blocked`); `--timeout S` bounds readiness. `--candidate` follows
the subcommand. Port precedence: explicit CLI (even `0`) >
`CUTTLE_SHADOW_PORT` > default `0` (ephemeral). `8080/8000/8888` refused at
CLI validation, child boot, and post-bind check. `up` runs in the
foreground; Ctrl+C stops only its owned child (that `Popen`). No second
daemon; no S3/S4 gates.

Snapshot: `git ls-files` working bytes + ONLY 2 extras
(`src/scripts/cuttle_shadow_app.py`, `src/api/dev_instance.py`); all other
untracked code omitted. Required application modules must be tracked before
launching; the runner does not silently include unrelated untracked files.
Denied basenames anywhere: `.env`, `settings.json`,
`GLOBAL.ini`, `bot_config.json`, `runtime_config.json`; suffixes `.db/.sqlite*/.pem/.key/.p12`; dirs
`.git/.venv/__pycache__/node_modules/temp`; `personal` at any depth.
Fingerprint sha256 + 4 anchors (`web_chat_api`, `chat_turn_workflow`,
`chat_turn_persist`, `chat_coordinator` by `__file__` + bytes, re-verified
child-side). No immutability claim: the snapshot is mutable-private.

Runtime: allowlist child env, never inherited. Every guard flag verified read:
`CUTTLE_MOBILE_AUTO_REBUILD=0`, `CUTTLE_JEV_WATCH=0`, `CUTTLE_AGENT_STEER=0`,
`CUTTLE_DEVICE_WORKERS_ENABLED=0`, `CUTTLE_LAN_ACCESS=0`,
`CHAT_TITLE_DISABLED=1`, `CUTTLE_TEST_MODE=1`; private `MUSE_MODEL` /
`HERMES_HOME` (actually consumed), never `HOME` / `CODEX_HOME`. Writes span
the entire private instance (app+data); config/DBs resolve snapshot-relative
via `__file__` paths. Guards install before app import: subprocess deny,
loopback single-bind sockets, audit-hook file/sqlite policy, first-position
route gate, executor fakes at 4 owner seams + routing-decision stub (default
`blocked`). Real coordinator/workflow/saver/persist/routes process every fake
result. Readiness: manifest nonce/pid/codehash + private `/__shadow/ready` and
`/__shadow/control` (nonce on read and write) — never bare TCP, never
`/api/health`. Real auth with separate browser contexts (cookies are
host-wide, not port-scoped).

Core chat, auth, history, cancellation, assets, and the narrow system-notice
endpoint pass through to the real owners; other routes get a side-effect-free
403. The complete allowlist lives in code (`cuttle_shadow_app`: `ALLOW_EXACT`,
`ALLOW_GET_PREFIXES`, numeric session/message regexes) — this doc does not
enumerate it. Eight optional probes stay real 403s: `GET /api/agents`,
`/api/cursor-agent/models`, `/api/muse/models`, `/api/doctor`,
`/api/terminal/status`, `/api/agent-context`, `/api/project-commands`, and
`/api/git/pending-changes`. POST session messages (`/api/auth/sessions/\d+/messages`)
permit production SYSTEM-only Stop feedback — never assistant insert (enforced
by production code). Deny-listed chat payloads: `/restart`, `/cmd`, `[button`,
`[action-form`. Five pinned optional-CDN URLs plus fonts stay browser-aborted
(expected aborts, pinned in test). State: `blocked_attempts` FULL log +
`blocked_counts`.

## 3. Not claimed / followup

- Python/application guards scope trusted-code effects; NOT an OS sandbox
  against malicious code. Linux validated only (a win32 PATH branch exists in
  code with no Windows proof).
- S3 (multi-daemon needs ownership fixes — launch no second daemon) and S4 are
  deferred; existing external independent recovery stays valid for final
  activation. No permanent staging waits on rollback.

# Security hardening review — 2026-09-27

Status of this document: **code inspection + targeted pytest + one interpreter proof**.  
It is **not** a claim that the hardening is fully verified in production. Flask was not restarted against this working tree during the review. No live agent turn, Electron window, mesh job, or daemon restart was executed.

Reviewer working tree: branch `main`, not committed, `origin/main` at same commit as HEAD. Inspection date: 2026-09-27.

---

## 1. Change inventory

### Git summary

```
12 files changed, 287 insertions(+), 794 deletions(-)
```

`git diff --numstat` (tracked files):

| Insertions | Deletions | Path |
|---|---|---|
| 0 | 1 | `.cuttle/scripts/host-electron-restart.sh` |
| 2 | 2 | `.cuttle/scripts/launch-cuttle-client.sh` |
| 2 | 2 | `.cuttle/scripts/launch-cuttle-host.sh` |
| 1 | 0 | `.gitignore` |
| 9 | 0 | `README.md` |
| 17 | 0 | `src/api/action_forms.py` |
| 17 | 0 | `src/api/agent_harness/catalog.py` |
| 2 | 2 | `src/api/device_workers/auth.py` |
| 64 | 5 | `src/api/device_workers/routes.py` |
| 90 | 11 | `src/api/project_actions.py` |
| 45 | 770 | `src/api/web_chat_api.py` |
| 38 | 1 | `src/tests/test_agent_harness.py` |

Untracked (not in `--stat`):

| Lines | Path |
|---|---|
| 134 | `src/api/http_authz.py` |
| 176 | `src/tests/test_http_authz.py` |
| 61 | `.cuttle/scripts/electron-sandbox.sh` |

Working tree: **dirty**, no staged files. Deleted files: **none**.

### Tracked / added files — purpose

| File | Role |
|---|---|
| `src/api/http_authz.py` | New helpers: `is_owner_user`, `require_owner`, `require_ui_operator` (loopback short-circuit), `require_authenticated`, `require_chat_session_access` (defined, **never called**). |
| `src/api/device_workers/routes.py` | Split worker-runtime auth (`_auth_or_401` → `authorize_worker_request`) vs UI/admin (`_ui_operator_or_401` → `require_ui_operator`) vs either (`_ui_or_worker_or_401`). |
| `src/api/device_workers/auth.py` | `lan_access_enabled()`: on settings read failure, return **False** instead of True. |
| `src/api/web_chat_api.py` | Owner gate on sandbox POST and LAN-access POST; action-form run always requires a verified session; follow-up messages check chat ownership; process start/stop return 410; many graph-pipeline handlers reduced to 410. |
| `src/api/project_actions.py` | HMAC-SHA256 + `iat`/`exp` on `inline.*` confirm tokens. |
| `src/api/action_forms.py` | Pass `session_id` into token encode; `owner_user_id` check inside `execute_action_form_submission`. |
| `src/api/agent_harness/catalog.py` | Skip `{project}/.cuttle/agents` Python import unless `CUTTLE_ALLOW_PROJECT_ADAPTERS` or `agent_harness.allow_project_adapters`. |
| `src/tests/test_http_authz.py` | Negative tests: unsigned tokens, sandbox anon/guest, action-form anon, stop-webapi 410, LAN job submit, worker-token job submit, enroll when LAN disabled. |
| `src/tests/test_agent_harness.py` | Opt-in env for project drop-in discovery; new test that `evilbot` adapter.py is not executed without opt-in. |
| `.gitignore` | Ignore `src/data/db/action_hmac_secret`. |
| `.cuttle/scripts/launch-cuttle-host.sh` / `launch-cuttle-client.sh` | Source `electron-sandbox.sh`; no longer always `--no-sandbox`. |
| `.cuttle/scripts/host-electron-restart.sh` | Removed `ELECTRON_DISABLE_SANDBOX=1`. |
| `.cuttle/scripts/electron-sandbox.sh` | Refuse start unless setuid `chrome-sandbox` **or** `CUTTLE_ELECTRON_NO_SANDBOX=1`. |
| `README.md` | Ubuntu 24.04 sandbox helper instructions. |

### Unrelated or incomplete removals

These were **intentional** as part of the same pass, but they are not auth gates:

- **Removed live implementations** behind `return _graph_pipelines_gone_response()` (save/load/delete pipeline, run-now, stop, register-running, start, schedule toggle, execution-start, etc.). Callers still get HTTP 410.
- **Process control** `/api/start-*` and `/api/stop-*` (including `pkill` / `taskkill` of `web_chat_api.py`) now always 410. `src/web/landing_page.html` still `fetch`es `/api/start-discord`; that path will fail closed.
- **Incomplete strip:** `/api/pipeline-execution-finish`, `/api/pipeline-check-running`, `/api/pipeline-job-status`, and `/api/pipeline-reload` still contain pre-410 bodies (query-tracker / `running_pipelines` bookkeeping). They were not given owner checks. Not a new feature; leftover after the 410 pass.

Not in this diff: TLS pinning, host-approved worker pairing, installer checksums, `web_chat_api.py` extraction.

---

## 2. Authentication / authorization matrix

Legend for cells:

- **deny** — 401/403/404 from the helper named in the row
- **allow** — helper returns success for that caller
- **n/a** — role does not apply (e.g. worker token unused because loopback already allowed)

`request.remote_addr` is Werkzeug’s peer address. **No `ProxyFix` / `X-Forwarded-For` handling exists** under `src/` (search: no matches). A reverse proxy on loopback would make **all** proxied clients look like loopback.

Helpers:

| Helper | File | Rule |
|---|---|---|
| `require_owner()` | `http_authz.py` | Cookie/bearer session via `get_request_session_token` + `verify_auth_session`; guests never owner; if `OWNER_USER_EMAIL` unset, any non-guest is owner. **No loopback bypass.** |
| `require_ui_operator()` | `http_authz.py` | If `request_is_loopback()` → synthetic user `{id:0, auth_provider:loopback}` **without a cookie**. Else same as `require_owner()`. Worker bearer is **not** accepted. |
| `_auth_or_401()` | `device_workers/routes.py` | `authorize_worker_request`: loopback **or** shared env token **or** enrolled device token. |
| `authorize_enroll_request()` | `device_workers/auth.py` | Loopback **or** already-valid worker auth **or** RFC1918 + `lan_access_enabled()`. |
| Inline session check | `web_chat_api.py` | `verify_auth_session`; optional `get_chat_session(nid, user_id)`. |

Loopback detection in `http_authz.is_loopback_addr`: `127.0.0.1`, `::1`, `localhost`, `::ffff:127.*`.  
`device_workers.auth.is_loopback` is **narrower** (no IPv4-mapped `::ffff:127.`). Runtime worker routes use the narrower helper.

### Worker blueprint (`/api/workers`)

| Endpoint | Enforcer | Anon LAN `192.168.x` | Guest cookie LAN | Owner cookie LAN | Worker Bearer LAN | Loopback (no cookie) |
|---|---|---|---|---|---|---|
| `POST /enroll` | `authorize_enroll_request` | allow if `lan_access_enabled` | same (IP, not cookie) | same | allow (refresh) | allow |
| `GET /` list | `_ui_operator_or_401` | deny 401 | deny 403 | allow | deny 401 | **allow** |
| `POST /self-update` | `_ui_operator_or_401` | deny | deny 403 | allow | deny | **allow** |
| `DELETE /<id>` | `_ui_operator_or_401` | deny | deny 403 | allow | deny | **allow** |
| `POST /register` | `_auth_or_401` | deny unless token | deny unless token | deny unless token | allow | **allow** (no token) |
| `POST /heartbeat` | same as register | same | same | same | allow | **allow** |
| `GET /jobs` | `_ui_operator_or_401` | deny | deny 403 | allow | deny | **allow** |
| `POST /jobs` | `_ui_operator_or_401` | deny | deny 403 | allow | **deny** (pytest) | **allow** |
| `POST /jobs/claim` | `_auth_or_401` | deny unless token | — | — | allow | **allow** |
| `GET /jobs/<id>` | `_ui_or_worker_or_401` | deny | deny 403 | allow | allow | **allow** |
| `POST .../heartbeat` | `_auth_or_401` | deny unless token | — | — | allow | **allow** |
| `POST .../complete` | `_auth_or_401` | deny unless token | — | — | allow | **allow** |
| `POST .../fail` | `_auth_or_401` | deny unless token | — | — | allow | **allow** |
| `POST .../cancel` | `_ui_operator_or_401` | deny | deny 403 | allow | deny | **allow** |
| `POST /plan` | `_ui_operator_or_401` | deny | deny 403 | allow | deny | **allow** |
| `POST /<id>/probe` | `_ui_operator_or_401` | deny | deny 403 | allow | deny | **allow** |
| `GET /ssh-approval/pending` | `_ui_operator_or_401` | deny | deny 403 | allow | deny | **allow** |
| `POST /ssh-approval/request` | `_auth_or_401` | deny unless token | — | — | allow | **allow** |
| `GET /ssh-approval/<id>` | `_ui_or_worker_or_401` | deny | deny 403 | allow | allow | **allow** |
| `POST /ssh-approval/<id>/decide` | `_ui_operator_or_401` | deny | deny 403 | allow | deny | **allow** |
| `POST /jobs/blender-shard` | `_ui_operator_or_401` | deny | deny 403 | allow | deny | **allow** |
| `GET /jobs/batch/<id>` | `_ui_operator_or_401` | deny | deny 403 | allow | deny | **allow** |

Guest/owner LAN cells for `_ui_operator_or_401` assume `OWNER_USER_EMAIL` is set and the guest does not match it. If the env var is **unset**, every non-guest account is an owner (`is_owner_user`).

Pytest evidence for the high-risk cells: `test_lan_job_submit_without_owner_is_401`, `test_worker_token_cannot_submit_jobs`, `test_flask_workers_routes` (loopback submit/claim/complete), `test_enroll_from_lan`, `test_lan_enroll_denied_when_lan_disabled`. **Guest cookie on worker admin routes was not HTTP-tested.**

### Chat / settings / process (modified)

| Endpoint | Enforcer | Anon | Guest | Owner | Worker token as `Authorization: Bearer` | Loopback anon |
|---|---|---|---|---|---|---|
| `POST /api/settings/sandbox` | `require_owner()` | 401 (`test_anonymous_sandbox_post_rejected`, remote_addr LAN) | 403 (`test_guest_cannot_change_sandbox`) | not HTTP-tested | treated as session token; invalid → 401 | **401** (no loopback bypass) |
| `POST /api/settings/lan-access` | `require_owner()` | 401 (by code; **no dedicated test**) | 403 by code | not HTTP-tested | 401 if not a real session | 401 |
| `GET /api/settings/sandbox` | **none** (unchanged) | allow | allow | allow | n/a | allow |
| `GET /api/settings/lan-access` | **none** on GET | allow | allow | allow | n/a | allow |
| `POST /api/action-form/run` | verified session + optional client session ownership + `owner_user_id` in executor | 401 (`test_anonymous_action_form_run_rejected`) | allow if they own the chat | allow if they own the chat | 401 (not a session) | **401** without cookie |
| `POST /api/action-form/followup-message` | session + `get_chat_session` | 401 | own chat only | own chat only | 401 | 401 without cookie |
| `POST /api/action-form/dismiss` | session only; **no `get_chat_session`** | 401 | any session_id they send | same | 401 | 401 |
| `POST /api/stop-webapi` (and sibling start/stop) | none; always 410 | 410 (`test_stop_webapi_is_gone`) | 410 | 410 | 410 | 410 |

`require_chat_session_access` is unused; dismiss can patch history for a session_id the caller does not own if they are merely logged in.

### Why loopback is treated as worker **admin**

`require_ui_operator()`:

```106:107:src/api/http_authz.py
    if request_is_loopback():
        return {"id": 0, "username": "loopback", "auth_provider": "loopback"}, None
```

Intent: Host Electron, pytest `test_client`, and `python -m api.device_workers.cli` on the same machine historically called these routes with **no cookie**. Flask `test_client` default `REMOTE_ADDR` is `127.0.0.1`, so tests keep passing without minting an owner session.

### Can a local process / browser / reverse proxy abuse that?

| Attacker | Verdict from code |
|---|---|
| Arbitrary process on the Host (`curl https://127.0.0.1:8080/api/workers/jobs`) | **Yes.** No cookie required. Can enqueue mesh jobs, cancel, blender-shard, self-update, SSH decide. |
| Browser tab on the Host (including Guest) | **Yes**, same as curl: loopback wins **before** guest/owner checks. |
| Phone / LAN Client | **No**, unless `OWNER_USER_EMAIL` is unset and they have a non-guest session, or they hit a proxy that presents as 127.0.0.1. |
| Reverse proxy bound on loopback forwarding LAN | **Yes, if Flask sees `remote_addr=127.0.0.1`.** No `ProxyFix` in tree. Cuttle’s LAN mode typically binds Flask itself to `0.0.0.0` (`lan_access.py`), so phone IPs are real peer addresses **unless** an extra proxy is added. |
| Spoof `X-Forwarded-For: 127.0.0.1` | **No** with current stack (header ignored). |
| Worker token | **Cannot** submit jobs (explicit pytest). Can still claim/complete on LAN with token; on loopback can claim **without** token (`authorize_worker_request` loopback short-circuit — **pre-existing**, still present). |

This loopback-as-operator choice is a **conscious residual trust of the Host OS user**, not a fix of “anyone on LAN.” It does **not** match a model of “only the signed-in owner.”

---

## 3. HMAC confirmation tokens

### Secret

1. Env `CUTTLE_ACTION_HMAC_SECRET` if non-empty (UTF-8 bytes), cached in `project_actions._hmac_secret_cache`.
2. Else file `src/data/db/action_hmac_secret` (gitignored). Created with `secrets.token_bytes(32)`, `chmod 600` best-effort.
3. Else on `OSError`: **hard-coded** `b"cuttle-dev-action-hmac-fallback"` — a shared secret if the data dir is unwritable.

Process cache survives within one Flask process. **Daemon/Flask restart** re-reads env or file; tokens remain valid if the file/env is unchanged. Deleting the file mints a **new** secret → all outstanding HMAC tokens fail.

### Lifetime and replay

- `iat` / `exp`: `exp = now + max(60, ttl)` with default `_PENDING_TTL_SEC = 3600`.
- Signature: HMAC-SHA256 over canonical JSON **excluding** `sig`, `hmac.compare_digest`.
- **No `jti` / nonce.** Replay of a valid token works until `exp` **or** one-shot form lock in chat history (`read_action_form_lock_from_history`). Reusable forms can be clicked again by design.
- If `CUTTLE_ALLOW_UNSIGNED_ACTION_TOKENS` is truthy, missing `sig` is accepted and **`exp` is not checked** (`decode_inline_action_payload`).

### Binding

| Field | Bound? |
|---|---|
| `action` + `params` + `project_path` | Yes, inside signed body |
| `session_id` | Optional at encode time; `encode_form_fallback` copies `spec.session_id` |
| Chat ownership at HTTP | `api_action_form_run` checks client `session_id` if numeric; then `execute_action_form_submission(..., owner_user_id=)` checks **resolved** card session |
| Cross-chat confirm | `execute_inline_action` rejects if both token and call `session_id` numeric-parse and differ |

If a signed token has **no** `session_id`, ownership falls through to whatever chat the authenticated user owns (client or spec). Allowlist of `.cuttle/actions` still applies.

### Old unsigned tokens

`decode_inline_action_payload` returns `None` without `sig` unless the unsigned env flag is set. Pytest: `test_unsigned_inline_token_rejected`.

**Client rebuild after Flask restart is still unsigned.** `src/web/js/chat_page.js` (`tokenForCard` / ~11848–11864) builds:

```text
inline. + base64url(JSON.stringify({action, project_path, params:{spec}}))
```

with **no HMAC**. Interpreter proof (2026-09-27): `decode_inline_action_payload(client_rebuild) is None`; server `encode_inline_action_payload` decodes successfully.

Consequence: while Flask still holds the in-memory pending `form_id`, clicks work. After a **daemon-owned Flask restart**, the client rebuilds an unsigned token → toast “form expired” / invalid payload unless unsigned tokens are re-enabled. Historical `data-fallback="inline.…"` blobs without `sig` fail the same way.

This is an **incomplete pairing of server HMAC with the documented restart-safe client path** (`action_forms.py` comments at `encode_form_fallback` / rewrite pending tags).

---

## 4. Regression testing

### Commands actually run (this review)

```bash
cd /home/kidchemical/Desktop/Cuttle
.venv/bin/python -m pytest \
  src/tests/test_http_authz.py \
  src/tests/test_device_workers.py \
  src/tests/test_project_actions.py \
  src/tests/test_action_forms.py \
  src/tests/test_agent_harness.py \
  src/tests/test_auth_guest.py \
  src/tests/test_web_terminal_access.py \
  -q --tb=no
# Result: 157 passed, 1 skipped, 2 warnings in ~4.5s

.venv/bin/python -m pytest \
  src/tests/test_agent_harness_smoke.py \
  src/tests/test_cuttle_managed_process_guard.py \
  -q --tb=no
# Result: 30 passed, 35 skipped in 0.41s
```

Skip in the primary suite: `test_action_forms.py:915` — “Escape-Purgatory / Epochs dogfood projects not mounted”.

`test_agent_harness_smoke.py` skips are live CLI smokes (no Cursor/Codex/etc. invoked).

**Full `src/tests/` was not run.** Failures in this review: **none** in the suites above.

### Workflow checklist (honest)

| Workflow | Executed? | Evidence |
|---|---|---|
| Local / remote **agent** execution (`/cursor`, etc.) | **No** | No harness smoke with a live CLI |
| Worker claim / heartbeat / complete | **Unit/Flask blueprint only** | `test_flask_workers_routes` (loopback, isolated app) |
| Worker administration (Jobs UI, LAN owner) | **Partial** | LAN anon/worker-token **denied** in pytest; owner cookie path **not** tested; loopback **allowed** |
| Guest chat vs owner chat | **Partial** | Guest login + sandbox 403; **no** `/api/chat` turn |
| Action-form run (authenticated, real CH session) | **No HTTP happy path** | Anon 401 tested; `execute_action_form_submission` unit tests **do not** pass `owner_user_id` |
| Action-form follow-up message | **No dedicated test** | Ownership check present in `web_chat_api.py` only |
| Electron Host / Client **startup** | **Not launched** | Inspected scripts; `chrome-sandbox` is setuid root (`-rwsr-xr-x`, uid 0); `apparmor_restrict_unprivileged_userns=1`; helper present so `electron-sandbox.sh` should **allow** sandboxed start. `electron --version` only (v28.3.3). |
| Adapter discovery | **Yes (unit)** | Bundled catalog tests; project drop-in gated; `CUTTLE_AGENTS_DIR` still trusted |
| Daemon / Flask restart + session recovery | **Not executed** | Would interrupt this chat; HMAC+client rebuild issue is code-level, not runtime-proven |

Do not treat unchecked rows as passing.

---

## 5. Compatibility and remaining security gaps vs original findings

### Original finding → current state

| Finding | Addressed? | Residual |
|---|---|---|
| Worker admin unauthenticated | **Partial** | LAN closed for anon; loopback still full admin; enroll still RFC1918+setting |
| Sandbox POST unauthenticated | **Yes for mutation** | GET still open; other settings POSTs (starred-slash, pipeline-limits, …) **unchanged** |
| Action-form run without token | **Yes** | Guest can still run forms on **their** chats; dismiss lacks ownership |
| Unsigned confirm tokens | **Server yes, client no** | Restart-safe JS rebuild is unsigned → likely **breaks cards after Flask restart** |
| LAN enroll = identity | **Not fixed** | Fallback on settings error is now False (safer); pairing still absent |
| Electron `--no-sandbox` | **Default sandbox** | Explicit opt-in; Ubuntu needs setuid helper (documented) |
| `web_chat_api.py` split | **Not done** | 410 cleanup only |
| Project `adapter.py` import | **Default off** | Instance `.cuttle/agents` and `CUTTLE_AGENTS_DIR` still `exec_module` |
| Installer checksums / TLS pin | **Not done** | |

### Workflows likely to break

1. **Action cards after Flask restart** (see §3). Same for old history with unsigned `inline.` fallbacks.
2. **Phone Jobs UI as Guest** — 403 on worker admin; must be owner (or loopback Host).
3. **Project-local harness agents** — `/projbot` style drop-ins disappear until opt-in. `CUTTLE_AGENTS_DIR` drop-ins still load.
4. **Linux Electron without setuid helper and without `CUTTLE_ELECTRON_NO_SANDBOX=1`** — launcher **exits 1** (by design). This machine currently has a root setuid helper, so Host *should* start; not observed.
5. **`landing_page.html` start-discord** — 410.
6. **Unsigned env escape hatch** — if set, HMAC and expiry are skipped for tokens without `sig`.

### New or leftover attack surface

- Loopback operator (Host-equivalent RCE/job mesh).
- Hard-coded HMAC fallback if secret file cannot be written.
- `require_ui_operator` synthetic user is never checked for guest.
- Worker loopback still skips bearer on claim/complete (`authorize_worker_request`).
- HMAC tokens replayable for up to 1h if the form is reusable or lock is not persisted.
- `GET /api/settings/sandbox` still leaks policy to anyone who can reach Flask.

### Unused helper

`require_chat_session_access` is dead code; follow-up uses a copy of the check, dismiss does not.

---

## 6. Rollback plan

Do **not** `git checkout --` on mixed files unless the entire dirty file should revert.

Tracked:

```bash
git restore -- \
  .cuttle/scripts/host-electron-restart.sh \
  .cuttle/scripts/launch-cuttle-client.sh \
  .cuttle/scripts/launch-cuttle-host.sh \
  .gitignore \
  README.md \
  src/api/action_forms.py \
  src/api/agent_harness/catalog.py \
  src/api/device_workers/auth.py \
  src/api/device_workers/routes.py \
  src/api/project_actions.py \
  src/api/web_chat_api.py \
  src/tests/test_agent_harness.py
```

Untracked (delete):

```bash
rm -f src/api/http_authz.py src/tests/test_http_authz.py .cuttle/scripts/electron-sandbox.sh
```

Optional: remove `src/data/db/action_hmac_secret` if minted.

Then **restart Flask** so the restored modules load (`flask.restart` / `/restart`). Electron launchers only apply on next Host/Client start.

Temporary compatibility without full rollback: `CUTTLE_ALLOW_UNSIGNED_ACTION_TOKENS=1` (weakens HMAC) and/or `CUTTLE_ELECTRON_NO_SANDBOX=1`.

---

## 7. Conclusion

The LAN “can reach Flask ⇒ admin” hole is **narrowed** for worker job submit, sandbox POST, and action-form run, with pytest evidence for several negative cases.

It is **not** fully verified:

- Production Flask may still be on pre-change code until restart.
- HMAC is bypassed by the **existing client unsigned rebuild**, which conflicts with Flask-restart recovery.
- Loopback remains a full worker-admin and worker-runtime trust domain.
- Worker pairing, TLS pinning, unused settings POSTs, and live agent/Electron/daemon paths were **not** exercised.

Treat this as a **partial hardening with a known action-form restart regression**, not a completed security program.

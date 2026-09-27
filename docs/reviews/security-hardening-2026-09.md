# Security hardening — final acceptance (2026-09-27)

Status: **milestone closed for the hardening scope**, with the provenance gap from
history-only recovery closed in the uncommitted follow-up on this working tree.
`web_chat_api.py` architecture is **explicitly deferred**.

HEAD: `8a783bb` *Implement security hardening and sandboxing for Electron launch scripts*
(branch `main`, **ahead of `origin/main` by 1**). Additional uncommitted work from this
acceptance pass is listed in §5.

This document replaces the earlier inspection-only draft. Every pytest count below
was produced in this pass. Unexecuted workflows are marked **not executed**.

---

## 1. Action-card provenance

### Threat

After Flask restart, action-form execution recovered a spec from SQLite assistant
HTML. Existence of a `cuttle_action_form_pending` tag in a chat the caller owns
was treated as authenticity. An authenticated user (or any writer of assistant
rows) could plant unsigned HTML and run allowlisted actions (`flask.restart`, …).

### History writers inspected

| Path | Writes assistant HTML? | Provenance |
|---|---|---|
| Agent persist (`_make_auth_assistant_saver` / `_rewrite_assistant_response_actions`) | Yes, after `prepare_assistant_text_for_actions` | Server rewrite stamps HMAC `sig` on the spec JSON |
| `POST /api/action-form/followup-message` | Yes | Same rewrite; auth is `require_chat_session_access` (not a raw HTML dump) |
| `POST /api/auth/sessions/<id>/messages` | **system only** | Client cannot post `role=assistant` |
| Dismiss / lock (`mark_action_form_consumed_in_history`) | Patches existing pending JSON (`locked` / toast) | Signing body **excludes** lock/toast; original `sig` still verifies |
| Discord / subagent / supervised delivery | Assistant text from those pipelines | Must go through the same rewrite when tags are present; unsigned pending tags do not execute after recovery |
| Conversation import API | **None found** (no bulk assistant import endpoint) | N/A |

There is still no public “import foreign transcript as assistant” route. Direct
SQLite writes remain possible for anyone with the DB file (Host OS trust).

### Server signature (this pass)

On rewrite, the pending JSON includes `id`, `session_id`, and `sig` =
HMAC-SHA256 over a canonical subset (`kind=__action_form_spec__` plus mode,
options, fields, project_path, …). Recovery:

1. Match pending tag `id=` in **this** chat.
2. `verify_action_form_spec` (fail closed if missing/invalid `sig`).
3. Signed `session_id` must equal the lookup chat (blocks copy-paste into another session).

Confirms already used HMAC `fallback="inline.…"` (now includes `session_id`).
Unsigned client confirm tokens are not minted. Historical unsigned cards return
the existing expired/invalid toasts — they do not raise.

HMAC secret: env `CUTTLE_ACTION_HMAC_SECRET`, else gitignored file
`src/data/db/action_hmac_secret` (created `chmod 600`). **No hardcoded fallback.**
Unavailable secret raises `RuntimeError` at sign time (`test_hmac_secret_unavailable_raises`).

The HMAC secret is **not** sent to the browser.

---

## 2. Restart recovery

**Live daemon Flask on :8080 was not recycled** (would drop the hosting chat).

Isolated evidence:

| Check | How | Result |
|---|---|---|
| Fresh **Flask HTTP process** (ephemeral port, isolated SQLite) | `test_action_form_survives_fresh_flask_process` | Pending Q&A recovered; tampered `flask.restart` in client spec **not** executed |
| Fresh **interpreter** confirm Cancel | `test_confirm_survives_fresh_interpreter` | History HMAC fallback; Cancel succeeded |
| Registry flush in-process | `clear_forms_for_tests` / `clear_pending_for_tests` plus history | Covered in `test_http_authz.py` |
| Tamper / fabricate | Same suite | Fail closed |
| Other-user session | `test_action_form_spec_rejected_for_other_user` | HTTP 404 |
| Expired HMAC | `test_expired_hmac_token_rejected` (`time` warped +120s) | `decode` → `None` |
| Invalid blob | `test_invalid_hmac_blob_does_not_raise` | `None`, no exception |
| Unsigned planted HTML | `test_unsigned_assistant_html_in_history_cannot_execute` | `success: false` |
| Secret persistence across processes | Child env `CUTTLE_ACTION_HMAC_SECRET` same as parent | Required for the Flask-process test to pass |

Daemon: `.cuttle/scripts/restart-daemon.sh --dry-run` only. Daemon PID 20312 was
listed and **not** killed.

---

## 3. Tests actually run (this pass)

### Command A — hardening + recovery

```bash
cd /home/kidchemical/Desktop/Cuttle
.venv/bin/python -m pytest \
  src/tests/test_http_authz.py \
  src/tests/test_action_form_process_restart.py \
  src/tests/test_device_workers.py \
  src/tests/test_project_actions.py \
  src/tests/test_action_forms.py \
  src/tests/test_agent_harness.py \
  src/tests/test_auth_guest.py \
  src/tests/test_web_terminal_access.py \
  src/tests/test_agent_harness_smoke.py \
  src/tests/test_cuttle_managed_process_guard.py \
  src/tests/test_agent_stop_then_followup.py \
  src/tests/test_flask_restart.py \
  src/tests/test_p0_p1_restart_session.py \
  src/tests/test_restart_native_command.py \
  src/tests/test_starred_agent_removal.py \
  -q --tb=line
```

**Result: 299 passed, 44 skipped, 25 warnings in 11.86s. Failures: 0.**

Skips include live CLI harness smokes (`test_agent_harness_smoke.py`) and
dogfood-project skips when Escape-Purgatory / Epochs are not mounted.

### What those files cover vs checklist

| Workflow | Executed? | Evidence |
|---|---|---|
| Owner vs guest (auth + sandbox + mesh jobs) | **Yes (HTTP/unit)** | `test_auth_guest`, `test_guest_cannot_change_sandbox`, `test_guest_cannot_submit_mesh_job`, `test_owner_session_can_submit_mesh_job` |
| Guest `/api/chat` agent turn | **No** | No live `/cursor` |
| Agent cancel + follow-up | **Yes (unit)** | `test_agent_stop_then_followup.py` — not a live CLI |
| Session sticky / star removal | **Yes (unit)** | `test_starred_agent_removal.py` |
| Worker register / list / claim / heartbeat / complete | **Yes** | `test_register_and_list_workers`, `test_claim_complete_ping`, `test_flask_workers_routes` (UI operator mocked for HTTP admin) |
| Worker cancel | **Yes** | `test_cancel_and_plan_and_blender_validate` |
| Owner-only admin (sandbox, LAN jobs, loopback deny) | **Yes** | `test_http_authz.py` (incl. `test_loopback_without_session_cannot_submit_jobs` → 401) |
| Action-form run / dismiss ownership / follow-up mint | **Yes** | run + followup + unsigned plant; dismiss uses `require_chat_session_access` (pytest on dismiss HTTP not added this pass) |
| Project adapter opt-in | **Yes** | `test_agent_harness.py` `evilbot` not imported without flag |
| Electron **GUI** Host/Client start | **Not launched** | Sandbox helper sourced successfully; `SANDBOX_ARGS` empty (no `--no-sandbox`); `chrome-sandbox` setuid root. Opening a window was skipped. |
| Daemon restart | **Dry-run only** | `restart-daemon.sh --dry-run` |
| Full `src/tests/` including e2e | **Not run** | |

Do not treat unchecked rows as passing.

### Electron sandbox (inspected, not GUI)

- `electron-sandbox.sh` / launchers: `--no-sandbox` **only** if `CUTTLE_ELECTRON_NO_SANDBOX=1`.
- Helper: `/home/kidchemical/Desktop/Cuttle/electron/node_modules/electron/dist/chrome-sandbox` mode `-rwsr-xr-x`, uid 0.
- `apparmor_restrict_unprivileged_userns=1`; setuid helper makes `_cuttle_sandbox_usable` succeed.
- `bash -n` on launch-host, launch-client, electron-sandbox: OK.

---

## 4. Implementation consistency (this pass)

| Topic | Finding | Action |
|---|---|---|
| History vs authenticity | History HTML was unsigned | HMAC `sig` on rewritten specs; recovery verifies |
| Follow-up auth | Duplicated session cookie check | Now `require_chat_session_access` |
| Loopback UI admin | Removed in HEAD (`require_ui_operator` = owner session) | Confirmed; pytest 401 without cookie |
| HMAC hardcoded fallback | **Absent** in current `_action_hmac_secret` | Doc earlier draft was stale |
| Automatic `--no-sandbox` | **None** without opt-in env | Confirmed by grep + sourcing helper |
| `require_chat_session_access` | Used by run, dismiss, followup | Not dead |
| Client unsigned confirm encoder | Removed in HEAD | Confirmed (`encodeInlineActionPayload` gone) |
| `web_chat_api.py` split | Not done | Deferred |
| Worker runtime loopback | Still tokenless claim/complete on 127.0.0.1 | Pre-existing; not changed this pass |
| LAN enroll | Still RFC1918 + setting | Deferred pairing |
| `GET /api/settings/sandbox` | Still unauthenticated | Deferred |
| `CUTTLE_ALLOW_UNSIGNED_ACTION_TOKENS` | Still a weaken-HMAC escape hatch | Leave documented |
| Follow-up mint | Chat owner can ask the **server** to rewrite any spec (signed) | By design for Steam/Discord follow-ups; not raw HTML injection |

No unrelated API contract changes beyond fail-closed recovery (unsigned/history-only
specs expire instead of executing).

---

## 5. This acceptance pass — files

Uncommitted vs `8a783bb`:

```
 M src/api/action_forms.py
 M src/api/project_actions.py
 M src/api/web_chat_api.py
 M src/tests/test_http_authz.py
 M docs/reviews/security-hardening-2026-09.md
?? src/tests/test_action_form_process_restart.py
```

### Issues found and fixed here

1. Recovered action cards trusted unsigned SQLite HTML → **HMAC + session bind**.
2. Follow-up message used a parallel auth path → **`require_chat_session_access`**.
3. Restart tests were in-process only → **isolated Flask process + fresh interpreter**.

### Remaining risks (deferred)

- Host OS user with the SQLite file / HMAC file.
- Worker **runtime** loopback without device token.
- LAN enroll without pairing.
- Live `/cursor` turn, Electron window, production Flask recycle — **you** (see §6).
- Extracting `web_chat_api.py` — deferred; map in `docs/guides/WEB_CHAT_API.md`.
- HMAC `inline.*` replay until `exp`: **not automatically a bug** on deliberately reusable cards. Treat destructive/admin actions separately from harmless reusable picks.
- `GET` sandbox/LAN settings still public.

---

## 6. Manual verification (operator)

Python on disk is not the Flask process that is hosting chat until you restart Flask.

1. Click the Flask restart card (graceful / when-idle) so this tree loads.
2. Optional: Confirm/Cancel on an old card after that restart.
3. Optional: start Host/Client from the launch scripts and confirm Chromium is sandboxed
   (no `CUTTLE_ELECTRON_NO_SANDBOX`).
4. Do **not** run `restart-daemon.sh` from this chat unless idle work is acceptable.

---

## 7. Git working tree

```
## main...origin/main [ahead 1]
```

HEAD `8a783bb` is the bulk hardening commit (not pushed). Uncommitted: provenance +
tests + this document.

**Do not** `git push` from the agent. **Do not** amend `8a783bb` unless you intend
to rewrite the already-created commit.

Recommended **new** commit (this pass only), after you review the diff:

```
Bind recovered action cards to server HMAC provenance

Recovered forms now verify a server signature and signed session id in
persisted history so planted assistant HTML cannot mint privileged actions
after Flask restart. Add isolated process-restart tests.
```

Treat the hardening milestone as **closed** unless a material vulnerability
remains after you restart production Flask and spot-check one card.

---

## 8. Rollback (full milestone)

Restore `8a783bb` and drop uncommitted files, then restart Flask:

```bash
git restore -- \
  src/api/action_forms.py \
  src/api/project_actions.py \
  src/api/web_chat_api.py \
  src/tests/test_http_authz.py \
  docs/reviews/security-hardening-2026-09.md
rm -f src/tests/test_action_form_process_restart.py
# To drop the whole milestone commit as well (destructive): git reset --hard origin/main
```

Temporary weaken: `CUTTLE_ALLOW_UNSIGNED_ACTION_TOKENS=1` (not recommended).
Electron unsandboxed: `CUTTLE_ELECTRON_NO_SANDBOX=1` only.

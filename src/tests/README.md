# Cuttle Tests

Pytest suite. Default entry (per AGENTS.md):

```bash
.venv/bin/python -m pytest src/tests/  # Linux / macOS
.venv\Scripts\python.exe -m pytest src/tests/
# Single file
.venv\Scripts\python.exe -m pytest src/tests/test_foo.py
```

## Test tooling and artifacts

Run tests with `pytest` (see above). Generated artifacts belong in the gitignored
`results/` directory; do not write developer reports under `src/scripts/output/`,
`src/web/logs/`, or `src/test-results/`.

Operator network diagnostics are the narrow exception: `src/scripts/diagnose_*`
are manually invoked troubleshooting tools, not pytest tests. The phone connectivity
diagnostic can change firewall/urlacl settings and requires Administrator on Windows.

## Chat lifecycle regression gate

Keep Node.js on `PATH` when validating frontend changes. The behavioral JS
tests otherwise skip; a green Python-only run does not verify browser logic.
These tests execute the shipped modules/page adapters without a live provider:

```bash
node --version
.venv/bin/python -m pytest -q src/tests/test_stream_detach_status.py src/tests/test_chat_terminal_status.py src/tests/test_p5e_stream_oracles.py src/tests/test_p5f_pipeline_oracles.py src/tests/test_chat_generation.py src/tests/test_chat_activity.py src/tests/test_chat_pending_result.py src/tests/test_architecture_boundaries.py
```

Lifecycle coverage must include a **detached and slow subscriber**, as well as
fully drained SSE: progress still updates, busy remains held until the worker
finishes, success/failure clears live status, and Stop → resend rejects the
old producer's status and result. Test the polling UI after its in-flight
prompt has cleared, including shell hub calls with an empty message list.

Real Chromium rendering of this race (desktop + phone, success + refusal,
brief bubble flashes, newer remote activity, idle Stop cleanup, and refresh):

```bash
.venv/bin/python -m pytest src/tests/e2e/test_chat_terminal_activity.py -q
```

Requires Playwright and Chromium from `src/requirements/requirements-dev.txt`.
This test serves production assets with an isolated API; it never reaches the
running Flask or a provider. Set `CUTTLE_API_URL=http://127.0.0.1:9` for a full
offline suite so older live-server E2E fixtures skip instead of touching an
install. Unit/layout tests always use temporary auth databases. Reusable
live QA/demo identities are described in `.cuttle/docs/fixture-accounts.md`.

Isolated suites (`e2e/test_shared_diff_modal.py`,
`e2e/test_chat_terminal_activity.py`) share `apply_request_guard`: a
context-level route registered before any page route that falls through
same-origin fixture/API traffic and aborts everything else (CDNs, fonts,
sibling localhost services). Page-level API intercepts are scoped to the
fixture origin (`<static_server>/api/**`, `<static_server>/qa-host.html`),
never bare `**` patterns. `test_request_guard_blocks_external_allows_same_origin`
pins the guard plus a same-origin fake-API round trip. No service-worker
registration exists in the served pages, so no worker block is installed.
Reproduce offline from a repo/worktree root (substitute that checkout's own
`.venv` python when one exists there; otherwise reuse an existing venv
interpreter, e.g. via symlink, without copying environments):

```bash
(
set -e
mkdir -p temp
export PATH="<node-dir>:$PATH"
node --version  # required gate: missing node silently skips frontend validation
export TMPDIR="$PWD/temp" GIT_CEILING_DIRECTORIES="$PWD/temp"
export CUTTLE_API_URL=http://127.0.0.1:9 CUTTLE_AGENT_STEER=0 CUTTLE_JEV_WATCH=0 CUTTLE_MOBILE_AUTO_REBUILD=0
export CUTTLE_WINDOWS_PREFIXES=C:/Projects/Cuttle  # intentional Windows-path
  # mapping so Windows-path fixtures resolve under an arbitrary worktree folder name
unset CUTTLE_ALLOW_SPEND CUTTLE_AGENT_SMOKE CUTTLE_AGENT_SMOKE_SCOPE \
  CUTTLE_TEST_ALLOW_EXTERNAL_RUNNERS CUTTLE_ROUTER_DB CUTTLE_JEV_LABEL_CACHE \
  CUTTLE_DEVICE_WORKERS_DB OPENAI_API_KEY ANTHROPIC_API_KEY API_KEY \
  GOOGLE_API_KEY AZURE_OPENAI_API_KEY DEEPSEEK_API_KEY GROK_API_KEY \
  XAI_API_KEY OPENROUTER_API_KEY META_API_KEY MODEL_API_KEY GOVEE_API_KEY \
  DISCORD_TOKEN DISCORD_BOT_TOKEN
.venv/bin/python -m pytest -q \
  --basetemp=temp/pytest-a2-browser \
  src/tests/e2e/test_shared_diff_modal.py \
  src/tests/e2e/test_chat_terminal_activity.py
)
```

(`CUTTLE_TEST_CHROMIUM` overrides the default `/opt/google/chrome/chrome`;
`--basetemp=temp/...` keeps pytest temp dirs inside the ignored `temp/`;
`TMPDIR` under the same temp keeps the fake-vendor `tempfile.gettempdir`
allowance aligned; `GIT_CEILING_DIRECTORIES` keeps intentional non-repo
fixtures from resolving into the real checkout. Run everything in a
subshell so the scrubbed/exported env never alters the user shell. This
recipe assumes an isolated checkout with no live `src/.env`, live DB,
settings, or resume copies — root imports can reload keys from `src/.env`
(`override=False`), so no existing worktree is automatically safe. Not
main-checkout-only: any isolated worktree runs the same recipe.)

Offline broad gate (same env, historical exclusions): `.venv/bin/python -m
pytest -q -p no:warnings src/tests/ --ignore=src/tests/unit -rf -rs
--basetemp=temp/pytest-a2-broad` (never bare `pytest`). Skip reasons are
reported by `-rs`, not asserted and not passing coverage — 2026-10-01
baseline snapshot: 41 spend/scope-gated live tests, 21 platform/fixture
cases (incl. the generated-Android-config skip). Measured 2026-10-01:
broad 2125 passed / 62 skipped, focused gate 92 passed, browser suites 14
passed (13 pre-existing + 1 guard test). The broad measurement preceded
the Android test split; the final targeted file run reported 21 passed /
1 skipped, with the tracked-source assertion counted separately. The
initial broad run is a historical measurement only: its logs show an
unintended `[MOBILE]` APK rebuild attempt (`JAVA_HOME` missing) during
API module import/collection (`PYTEST_CURRENT_TEST` is unset at
collection, so that guard did not apply). Corrected by the
`pytest_configure` hard override (`CUTTLE_MOBILE_AUTO_REBUILD=0`) plus
focused verification in `test_pytest_configure_isolation.py` that the
configured env beats an inherited `1` and collection-style route
registration starts no thread/Gradle. Latest focused gate (hook + mobile
Android + mobile webview + boundary): 52 passed / 1 skipped.

## Shadow gates (S1/S2, Linux-only)

Real-app shadow suites run the production Flask from an isolated snapshot —
never the live server. **Linux only**: `test_dev_instance.py`, `test_shadow_chat_journeys.py`, and
`e2e/test_shadow_chat_stop_resend.py` skip off-Linux (`sys.platform !=
"linux"`); pure B1 tests stay platform-neutral. S1 passed 37 (22 dev + 15
architecture). S2 real-HTTP run: 34 passed / 66
executions / zero guard denials. Manager combined candidate: all three real
browser tests passed with no skips (4.80 s), and all 71 shadow/HTTP/boundary
tests passed (8.47 s). Earlier broad result kept: 2255 passed / 61 skipped /
zero failures. S1/S2 implementation is verified and activated (Linux);
historical counts above are the acceptance evidence.
Details: `docs/architecture/development-instance-safety.md`.

Run from the repo root with the same scrubbed offline env as above (plus the
shadow CLI owns its ports — `prepare` then `up`, ephemeral by default, live
`8080/8000/8888` refused):

Candidate application modules must be tracked; the snapshot intentionally
omits other untracked files. For a deeply nested checkout on Linux, keep
`TMPDIR` in a short project-owned `temp/` path: Chromium's singleton socket
can exceed the Unix socket path limit. A browser skip is an unmet gate,
not passing coverage.

```bash
.venv/bin/python -m pytest -q src/tests/test_dev_instance.py src/tests/test_architecture_boundaries.py src/tests/test_shadow_chat_journeys.py
.venv/bin/python -m pytest -q src/tests/e2e/test_shadow_chat_stop_resend.py
```

Every test also uses temporary Flask restart status/request/event files.
Delivery completion/cancellation hooks can consult a pending restart, so
isolating only the dedicated restart test files is insufficient. The runtime
also rejects idle handoff from a process other than the PID/generation that
scheduled the wait. `test_flask_restart.py::test_foreign_process_cannot_fire_flask_pending_restart`
reproduces direct, completion-hook, and cancellation-hook attempts; it must
remain blocked without changing the pending status or writing a daemon request.

## Architecture transport and history-sync gates

Run in the scrubbed isolated environment above (no `src/.env` or user data):

```bash
.venv/bin/python -m pytest -q src/tests/test_chat_stream.py src/tests/test_chat_generation.py src/tests/test_architecture_boundaries.py
.venv/bin/python -m pytest -q src/tests/e2e/test_chat_stream_reader.py src/tests/e2e/test_chat_sync_lifetime.py src/tests/e2e/test_chat_stop_resend.py src/tests/e2e/test_shadow_chat_stop_resend.py
```

Sync tests hold real native response bodies after headers, including old
malformed status, A→B→A navigation and stale cleanup of a replaced claim.
Synthetic visibility input proves policy, not OS-minimize behavior. Stream
tests use native readers/AbortSignal with empty recovery controls. Shadow
journeys separately prove real Flask/auth/SQLite cancellation and persistence.

Optional cost measurements reuse the same guarded browser fixtures:

```bash
CUTTLE_COST_PROFILE=1 .venv/bin/python -m pytest -q src/tests/e2e/test_chat_cost_profile.py
```

The per-project polling regression is also a normal explicit browser gate:
`src/tests/e2e/test_pending_changes_polling.py` (shared/different projects and
standalone native intervals).

Three fresh contexts/databases per workload; output defaults to ignored
`temp/f1-measurements.json` (`CUTTLE_COST_PROFILE_OUT` overrides it). This is
a measurement aid, not a timing-threshold CI gate.
Browser skips remain unmet validation gates.

## Layout

```
src/tests/
├── test_*.py        # pytest files (chat, agents, jobs, restart, workers, …)
├── unit/            # small focused tests + paid provider diagnostics (spend-gated)
├── e2e/             # Playwright browser tests (app shell, cards, badges, history)
├── fixtures/        # shared fixture files
└── conftest.py
```

## Token-spending tests (read before running)

A plain `pytest src/tests/` spends **nothing** — every live test is gated off
by default. Two opt-in flags exist; **never set either without asking the user
first** (both spend real money):

| Flag | What it unlocks |
|---|---|
| `CUTTLE_ALLOW_SPEND=1` | Script diagnostics: `unit/test_api_key.py`, `unit/test_openai_connection.py` (real `gpt-3.5-turbo` completions). Without it they print `[SKIP]` and pass without calling anything. See `spend_guard.py`. |
| `CUTTLE_AGENT_SMOKE=1` (+ `CUTTLE_AGENT_SMOKE_SCOPE`) | Pytest live tests: `test_agent_harness_smoke.py` (`test_live_*`), `test_agent_resume_contract.py::test_live_cursor_resume_two_turn`, `test_agent_stop_then_followup.py::test_live_stop_then_followup` (`stop_followup` scope; ≤2 prompts/agent). Skip reasons say so explicitly. |

Rule for agents: if a run needs either flag, stop and ask. Do not set them
proactively, do not bury them in a larger command.

## Contributing

1. New tests go flat in `src/tests/` as `test_*.py` (pytest discovers them).
2. Mock subprocesses — never launch a GUI app, a server, or a harness for real.
3. Live-server tests must `pytest.skip` when the server is down.

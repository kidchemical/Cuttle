# Cuttle Tests

Pytest suite. Default entry (per AGENTS.md):

```bash
.venv\Scripts\python.exe -m pytest src/tests/
# Single file
.venv\Scripts\python.exe -m pytest src/tests/test_foo.py
# Never pytest the whole tree against a live server unless you mean it:
# integration/test_discord_api_e2e.py hits http://127.0.0.1:8080 when up, skips when down.
```

## Layout

```
src/tests/
├── test_*.py        # ~130 pytest files (chat, agents, jobs, restart, discord, …)
├── unit/            # legacy tool tests (security, API keys, …)
├── integration/     # live-server / content tests (pytest, skip when deps missing)
│   ├── test_discord_api_e2e.py          # /api/health; asserts pipeline-trigger-discord is 404 if server up
│   └── test_discord_remote_execution.py # gateway files absent; Electron does not start Discord
├── e2e/             # Playwright-style browser tests (app shell, badges, history)
├── deprecated/      # archaeology only (Self Improvement); do not add cases
├── fixtures/        # shared fixtures + leftover self-improvement samples
├── conftest.py
└── run_*.py         # legacy direct-run harnesses (largely superseded by pytest)
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

## Removed (2026-09)

- `integration/test_bot_startup.py`, `test_wsl_integration.py` — hardcoded
  `/home/<user>/...` paths, shelled out to `wsl`, tested machine setup.
- `integration/test_discord_bot_integration.py` — asserted against local fake
  parsers (`/cursor-ui`) that test no shipped code.

Runner references were cleaned in `run_all_tests.py`, `run_safe_tests.py`,
`run_tests_wsl_compatible.py`.

## Contributing

1. New tests go flat in `src/tests/` as `test_*.py` (pytest discovers them).
2. Mock subprocesses — never launch a GUI app, a server, or a harness for real.
3. Live-server tests must `pytest.skip` when the server is down (see
   `integration/test_discord_api_e2e.py`).
4. `deprecated/` is archaeology — do not add new cases there.

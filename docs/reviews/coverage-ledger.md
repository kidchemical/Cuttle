# Coverage ledger (resume here)

**HEAD:** `4f2880c`. CH-000743-21 AST parse is **not** complete execution review.

## Done this pass (pass 3)

- Product: graphs retired; Telegram/Slack unwanted; Discord REST keep; gateway DM investigate.
- Consumer tables: [`graph-discord-consumers.md`](graph-discord-consumers.md)
- All **60** `src/api/*.py` classified by inbound refs (execution consumers vs tests/docs).
- Jobs: `loadPipelines` already empty; cuttle-jobs + workers are the live UI.
- `pipeline-execution-finish` caller = `create_test_execution.py` only.
- `sandbox_policy.py` unused in production (graph tool-node helper). **`/api/settings/sandbox` is a live owner-gated POST + public GET** (`test_http_authz.py`, GitHub #4) — not graph-removal.

## Next execution review (do not rebuild 918-row inventory)

1. `src/api/agent_harness/` adapters + kernel (keep; confirm no graph-only helpers).
2. `src/api/device_workers/` + `cuttle_jobs/` (must survive stream 1).
3. `src/api/agent_router/` 
4. `src/web/js/chat_page.js` Discord invite / DM copy (block 1c until listed).
5. `GET /api/pipeline-chats` UI.
6. Remaining `src/scripts/utilities/*_cli_tool.py` already used by harness — spot-check only.

## Still structure-only

Android, Electron extras, most tests, most HTML, vendor. Histogram from generator still useful: inventoried 375 / structure 525 / execution+partial ~18 before pass-3 promotions of all `src/api/*.py` consumers.

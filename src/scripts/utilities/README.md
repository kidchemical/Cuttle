# Utility scripts

Helpers for CLI adapters, diagnostics, testing and documentation media live here.
Use Python **3.11+** with the repository's root `.venv`; install runtime and dev
dependencies as shown in the [root README](../../../README.md#development).
Each utility's arguments and working-directory needs are defined by its source
and `--help`; do not assume every helper resolves paths the same way.

## CLI adapters and query inspection

- `claude_cli_tool.py`: Claude Code `claude -p`, resume and JSON usage.
- `claude_cli_session_store.py`: per-chat resume/model state.
- `claude_code_tool.py`: legacy re-export of the adapter.
- Query inspector: `/query_log.html?id=<query_id>`; logs under
  `src/web/logs/query_data_<id>.json` are indexed by chat `query_id`.

## API-dependent utilities

Only utilities that call the API require an active Host. Start the daemon via
`./start_cuttle.sh` on POSIX or the root README's Windows daemon command;
Electron Host can connect to/start it. Do not start Flask directly alongside an
existing Host. HTTPS uses :8080 (self-signed by default); HTTP :8000 is the
same-app cleartext portal. Inspect the utility's URL and trust options before
using it. CLI module examples need the source package on the path:

```bash
# POSIX, from the repository root
PYTHONPATH=src .venv/bin/python -m api.chat_cli --help
```

```powershell
# Windows PowerShell, from the repository root
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m api.chat_cli --help
```

See [agent ops](../../../.cuttle_global/docs/agent-ops-cli.md),
[architecture map](../../../docs/architecture/repository-map.md),
[review snapshots](../../../docs/reviews/), and the [test suite](../../tests/).

# Python dependencies

Three files:

| File | Contents |
|---|---|
| [`requirements.txt`](requirements.txt) | Runtime: daemon, Flask API, harnesses, attachments, workers, auth |
| [`requirements-optional.txt`](requirements-optional.txt) | Lazy-import extras that degrade gracefully when absent (`zeroconf` for LAN discovery) |
| [`requirements-dev.txt`](requirements-dev.txt) | Test/dev tooling (`pytest`, `pytest-asyncio`, `playwright`) |

```bash
.venv/bin/pip install -r src/requirements/requirements.txt
# extras as needed:
.venv/bin/pip install -r src/requirements/requirements-optional.txt
.venv/bin/pip install -r src/requirements/requirements-dev.txt
```

Windows-only pins (`pywinpty`) are `sys_platform == "win32"` in the runtime file — Linux/macOS skip them. There is no separate WSL requirements list (Muse Code is a native CLI; WSL is only a leftover fallback in the Muse adapter).

Cuttle does not host an MCP server. Do not look for `requirements-mcp.txt`.

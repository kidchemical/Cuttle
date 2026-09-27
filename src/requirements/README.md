# Python dependencies

One file: [`requirements.txt`](requirements.txt).

```bash
.venv/bin/pip install -r src/requirements/requirements.txt
```

Windows pins (`pywin32`, `pyautogui`, …) are `sys_platform == "win32"` in that file — Linux/macOS skip them. There is no separate WSL requirements list (Muse Code is a native CLI; WSL is only a leftover fallback in the Muse adapter).

Cuttle does not host an MCP server. Do not look for `requirements-mcp.txt`.

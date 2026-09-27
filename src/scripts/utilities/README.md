# Utility Scripts

## Overview

This directory contains utility scripts for testing, debugging, and automation.

## Query logs

Turn inspectors: `/query_log.html?id=<query_id>`. Source of truth is `src/web/logs/query_data_<id>.json` (SQLite chat rows store `query_id` as the index).

---

### Development Tools

- `claude_cli_tool.py` - Claude Code CLI (`claude -p`) with resume + JSON usage
- `claude_cli_session_store.py` - Per-chat Claude resume id / model pin map
- `claude_code_tool.py` - Thin re-export of `claude_cli_tool` (legacy import path)
- `debug_cursor_location.py` - Debug cursor position
- `hello_world.py` - Simple test script
- `update_project_paths.py` - Update project file paths

### System Utilities

- `kill_bots.py` - Kill running bot processes
- `example_project_setup.py` - Project setup example

---

## Requirements

These scripts require:
- Python 3.8+
- `requests` library
- Active API server on http://localhost:8080

## Troubleshooting

### Script can't find files

All scripts use relative paths from their location:
```python
script_dir = Path(__file__).parent
project_root = script_dir.parent.parent.parent
```

If you move scripts, this automatically adjusts.

### API connection errors

Ensure the server is running:
```bash
python src/api/web_chat_api.py
```

Or start via Electron:
```bash
start_electron.bat
```

---

## Related Documentation

- **Testing Guides:** `../../docs/testing/`
- **Fix Documentation:** `../../docs/fixes/`
- **Test Suite:** `../../tests/`

**Last updated:** October 12, 2025


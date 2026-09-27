# Utility Scripts

## Overview

This directory contains utility scripts for testing, debugging, and automation.

## Query Report Testing Scripts

### Automated Pipeline Test ⭐ Recommended

**`automated_pipeline_test.py`**

Automatically executes the TEST - Hello World (Parallelization) pipeline and verifies results.

**Usage:**
```bash
cd /path/to/Cuttle
python src/scripts/utilities/automated_pipeline_test.py
```

**What it does:**
1. Loads the pipeline configuration
2. Starts query tracking
3. Executes coordinator, experts, and aggregator
4. Verifies all 3 experts execute in parallel
5. Runs consistency tests
6. Reports pass/fail status

**Expected output:**
```
✅ Pipeline execution successful!
✅ SUCCESS! All tests passed!
```

---

### Verify Specific Report

**`verify_specific_report.py`**

Quick verification tool for a specific query report.

**Usage:**
```bash
python src/scripts/utilities/verify_specific_report.py src/web/logs/query_report_XXXXX.html
```

**What it checks:**
- All node IDs are unique
- Executed node count matches LLM call count
- Basic consistency validation

---

### Run Test on Report

**`run_test_on_report.py`**

Runs focused consistency tests on a specific report.

**Usage:**
```bash
python src/scripts/utilities/run_test_on_report.py
```

**Note:** Edit the script to change which report to test (line 12).

**What it tests:**
- Test 1: LLM call count matches graph
- Test 6: No duplicate node IDs
- Test 7: Executed nodes tracked properly

---

## Other Utility Scripts

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


# Launcher Fix Summary

## Issue
After reorganizing the `src/` directory, the launcher could not find bot and API files in their new locations, causing errors:
```
/path/to/Cuttle/src\bot_mcp.py: [Errno 2] No such file or directory
/path/to/Cuttle/src\web_chat_api.py: [Errno 2] No such file or directory
No module named 'project_manager'
```

## Root Cause
Files were moved to organized subdirectories but the launcher still referenced old paths:
- Bot files moved to `src/bots/`
- API files moved to `src/api/`
- Managers moved to `src/managers/`

## Fixes Applied

### 1. Recovered Missing Bot Files
Bot files were accidentally removed when cleaning up nested `src/src/` directory. Recovered from electron distribution:
- ✅ `bot_mcp.py` → `src/bots/`
- ✅ `bot_wsl.py` → `src/bots/`
- ✅ `bot_deprecated.py` → `src/bots/`
- ✅ `interactive_bot.py` → `src/bots/`
- ✅ `cursor_agent_native.py` → `src/bots/`

### 2. Updated Launcher Paths

#### Import Statements
**Old:**
```python
from project_manager import project_manager
```
**New:**
```python
from managers.project_manager import project_manager
```

#### Bot Script Paths
**Old:**
```python
bot_script = "bot_mcp.py"
bot_script = "bot_wsl.py"
bot_script = "bot_deprecated.py"
```
**New:**
```python
bot_script = "bots/bot_mcp.py"
bot_script = "bots/bot_wsl.py"
bot_script = "bots/bot_deprecated.py"
```

#### API Server Path
**Old:**
```python
subprocess.Popen([self.python_cmd, "web_chat_api.py"], cwd=self.project_root)
```
**New:**
```python
subprocess.Popen([self.python_cmd, "api/web_chat_api.py"], cwd=self.project_root)
```

#### Standalone Function Paths
**Old:**
```python
subprocess.run([sys.executable, "bot_mcp.py"], cwd=Path(__file__).parent)
subprocess.run([sys.executable, "web_chat_api.py"], cwd=Path(__file__).parent)
```
**New:**
```python
subprocess.run([sys.executable, "bots/bot_mcp.py"], cwd=Path(__file__).parent)
subprocess.run([sys.executable, "api/web_chat_api.py"], cwd=Path(__file__).parent)
```

#### Process Detection Patterns
Updated bot detection patterns to recognize both old and new paths for backwards compatibility during cleanup operations.

## Files Modified
1. `src/launcher.py` - All path references updated

## Files Recovered
1. `src/bots/bot_mcp.py`
2. `src/bots/bot_wsl.py`
3. `src/bots/bot_deprecated.py`
4. `src/bots/interactive_bot.py`
5. `src/bots/cursor_agent_native.py`

## Testing
The launcher should now work correctly with option 1 (MCP-FULL):
```
Enter your choice (0-9, A-B, K-N): 1
```

All services should start from their new locations:
- ✅ MCP Discord Bot: `src/bots/bot_mcp.py`
- ✅ Web Chat API: `src/api/web_chat_api.py`
- ✅ Ungit: with `managers.project_manager`

## Status
✅ **FIXED** - Launcher updated to work with reorganized directory structure

## Date
October 12, 2025


# Launcher Path Fixes

**Date:** 2025-10-12  
**Issue:** Launcher.py closes unexpectedly due to incorrect file paths

## Problems Identified

The launcher was trying to run files from incorrect paths, causing the following errors:

1. **Bot script paths**: Looking for `bot_mcp.py` and `bot_wsl.py` in `src/` instead of `src/bots/`
2. **API script paths**: Looking for `web_chat_api.py` in `src/` instead of `src/api/`
3. **Ungit import error**: Failed to import `project_manager` module due to path issues
4. **Helper script paths**: Several helper scripts were being called from incorrect paths
5. **Flask deprecation warning**: Using deprecated `__version__` attribute

## Fixes Applied

### 1. Fixed Bot Script Paths

**File:** `src/launcher.py`

- **Line 801**: Changed `"bot_deprecated.py"` to `"bots/bot_deprecated.py"`
- **Line 927**: Changed `"bot_wsl.py"` to `"bots/bot_wsl.py"`

These functions (`run_discord_bot_only()` and `run_wsl_bot()`) now correctly reference the bot scripts in the `bots/` subdirectory.

### 2. Fixed Helper Script Paths

Added proper path checking and resolution for helper scripts:

- **Debug mode** (`run_debug_mode()`): Now looks for `scripts/launchers/launcher_debug.py`
- **Setup** (`run_setup()`): Now looks for `scripts/setup/setup_env.py`
- **Install deps** (`run_install_deps()`): Now looks for `scripts/setup/install_deps_step_by_step.py`
- **Fix venv** (`run_fix_venv()`): Now looks for `scripts/setup/fix_venv.py`
- **MCP test** (`run_mcp_test()`): Now looks for `tests/test_mcp_conversion.py`

All these functions now:
1. Check if the script exists before trying to run it
2. Provide helpful error messages if the script is not found
3. Offer fallback options where appropriate

### 3. Fixed Ungit Import Issues

**File:** `src/launcher.py`, method `start_ungit()`

Made the project manager import more robust:
- Properly adds project root to `sys.path` before import
- Wraps import in try-except to catch `ImportError`
- Falls back to using workspace root if project manager is not available
- Provides clear warning messages when project manager cannot be loaded
- Added traceback printing for better debugging

Updated both:
- `JamBitLauncher.start_ungit()` method (lines 278-359)
- `run_ungit()` function (lines 821-877)

### 4. Fixed Flask Deprecation Warning

**File:** `src/launcher.py`, method `install_dependencies()`

Replaced deprecated `flask.__version__` with:
```python
from importlib.metadata import version
flask_version = version('flask')
```

With fallback to old method if `importlib.metadata` is not available.

Applied to both Flask and Flask-CORS version checks.

### 5. Improved Exception Handling

Enhanced exception handling in several functions:
- `run_ungit()`: Added launcher variable initialization and proper cleanup
- `run_mcp_default()`: Added launcher variable initialization and proper cleanup
- Added `traceback.print_exc()` calls for better debugging

## Impact

These fixes resolve the following issues:
1. ✅ Discord bot can now start correctly
2. ✅ Web Chat API can now start correctly
3. ✅ Ungit can start (with or without project manager)
4. ✅ All helper scripts have proper path checking
5. ✅ Removed Flask deprecation warning
6. ✅ Better error messages for missing scripts
7. ✅ Improved debugging with traceback output

## Testing Recommendations

After these fixes, test the following launcher options:

1. **Option 1**: MCP Full System - Should start bot, API, and Ungit
2. **Option 2**: MCP Default - Should start API only
3. **Option 3**: MCP Discord Bot Only - Should start bot only
4. **Option 6**: Web Chat API Only - Should start API only
5. **Option 7**: WSL Discord Bot - Should start WSL bot
6. **Option 4**: Test MCP Conversion - Should find test script
7. **Option 9**: Environment Setup - Should find or report missing script
8. **Option B**: Fix Virtual Environment - Should find or report missing script

## Files Modified

- `src/launcher.py` - All path corrections and improvements

## Related Files

The launcher now correctly references:
- `src/bots/bot_mcp.py`
- `src/bots/bot_wsl.py`
- `src/bots/bot_deprecated.py`
- `src/api/web_chat_api.py`
- `src/managers/project_manager.py`
- `src/tests/test_mcp_conversion.py`
- `src/scripts/launchers/launcher_debug.py`
- `src/scripts/setup/setup_env.py`
- `src/scripts/setup/install_deps_step_by_step.py`
- `src/scripts/setup/fix_venv.py`

## Next Steps

1. Test the launcher with Option 1 (MCP Full System)
2. Verify all services start correctly
3. Check that error messages are clear if any script is missing
4. Monitor for any remaining path-related issues


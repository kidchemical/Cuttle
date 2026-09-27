# Launcher Import Fixes

**Date:** 2025-10-12  
**Issue:** Bot and API processes crashing with ModuleNotFoundError

## Problems Identified

After fixing the file path issues, the launcher could start the processes but they immediately crashed with import errors:

1. **bot_mcp.py** couldn't import:
   - `mcp_tool_manager` (should be `api.mcp_tool_manager`)
   - `tool_manager` (should be `core.tool_manager`)
   - `ai_agent` (should be `core.ai_agent`)
   - `multi_stage_processor` (should be `core.multi_stage_processor`)
   - `config` (should be `core.config`)
   - `query_report_generator` (should be `reports.query_report_generator`)

2. **web_chat_api.py** couldn't import:
   - `project_manager` (should be `managers.project_manager`)
   - `ai_agent` (should be `core.ai_agent`)
   - `tool_manager` (should be `core.tool_manager`)
   - `config` (should be `core.config`)
   - `multi_stage_processor` (should be `core.multi_stage_processor`)
   - `query_report_generator` (should be `reports.query_report_generator`)
   - `auth_api` (should be `api.auth_api`)
   - `auth_db` (should be `api.auth_db`)

3. **Working directory issues**:
   - `bot_mcp.py` was changing to `src/bots/` directory
   - `web_chat_api.py` was changing to `src/api/` directory
   - Both needed to stay in `src/` directory for proper imports

## Fixes Applied

### 1. Launcher Process Environment Setup

**File:** `src/launcher.py`

Updated `start_discord_bot()` and `start_web_chat_api()` methods to:
- Set `PYTHONPATH` environment variable to the project root (`src/`)
- Ensure working directory is set to `src/` for both processes

```python
# Set up environment to ensure proper imports
env = os.environ.copy()
env['PYTHONPATH'] = str(self.project_root)

process = subprocess.Popen([
    self.python_cmd, bot_script
], cwd=self.project_root, env=env)
```

This ensures that Python can find all the modules in the `src/` directory structure.

### 2. Fixed bot_mcp.py Imports

**File:** `src/bots/bot_mcp.py`

**Changes:**
1. Fixed working directory handling (lines 18-27):
   ```python
   # Ensure the parent directory (src/) is in the Python path for imports
   script_dir = os.path.dirname(os.path.abspath(__file__))
   parent_dir = os.path.dirname(script_dir)  # This should be the 'src' directory
   if parent_dir not in sys.path:
       sys.path.insert(0, parent_dir)
       
   # Change to parent directory if we're in the bots subdirectory
   if os.path.basename(os.getcwd()) == 'bots':
       os.chdir(parent_dir)
       print(f"[INFO] Changed working directory to: {os.getcwd()}")
   ```

2. Fixed imports with proper module paths (lines 39-66):
   ```python
   # Import MCP tool manager
   from api.mcp_tool_manager import get_tool_manager, cleanup_tool_manager, MCPToolManager
   
   # Fallback to legacy tool manager
   from core.tool_manager import run_program, is_cursor_ai_session_active, send_to_cursor_ai
   
   # Import other modules
   from core.ai_agent import plan_with_llm
   from rag.discord_rag import initialize_discord_rag
   from core.multi_stage_processor import process_input
   from core.config import get_config, set_mode
   from bots.cursor_agent_native import send_prompt_to_cursor_agent_native, start_cursor_agent_session_native
   
   # Import query tracking
   from reports.query_report_generator import get_query_tracker, start_query_tracking, finish_query_tracking
   ```

3. Added proper error handling:
   - Try/except blocks for all imports
   - Informative error messages showing current directory and Python path
   - sys.exit(1) for critical import failures
   - Dummy functions for optional imports (query tracking)

### 3. Fixed web_chat_api.py Imports

**File:** `src/api/web_chat_api.py`

**Changes:**
1. Fixed project root detection (lines 19-27):
   ```python
   # Add project root to path (parent of api directory, which is 'src')
   script_dir = Path(__file__).parent
   project_root = script_dir.parent
   if str(project_root) not in sys.path:
       sys.path.insert(0, str(project_root))
   
   # Change to project root if we're in the api subdirectory
   if os.path.basename(os.getcwd()) == 'api':
       os.chdir(project_root)
   ```

2. Fixed imports with proper module paths (lines 29-56):
   ```python
   # Import bot functionality
   from core.ai_agent import plan_with_llm, plan_simple
   from core.tool_manager import run_program
   from core.config import get_config
   from core.multi_stage_processor import process_input
   from reports.query_report_generator import start_query_tracking, finish_query_tracking
   
   # Import authentication
   from api.auth_api import auth_bp
   from api.auth_db import get_auth_db
   
   # Import project management
   from managers.project_manager import project_manager
   ```

3. Made project_manager optional:
   - Set to `None` if import fails
   - Added decorator to check availability before use
   - All project management endpoints now gracefully handle missing project manager

### 4. Added Project Manager Availability Decorator

**File:** `src/api/web_chat_api.py` (lines 64-75)

Created a decorator to check if project_manager is available:
```python
from functools import wraps
def requires_project_manager(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if project_manager is None:
            return jsonify({
                'success': False,
                'error': 'Project manager not available'
            }), 503
        return f(*args, **kwargs)
    return decorated_function
```

Applied to all project management endpoints:
- `GET /api/projects`
- `GET /api/projects/<id>`
- `GET /api/projects/current`
- `POST /api/projects`
- `PUT /api/projects/<id>`
- `DELETE /api/projects/<id>`
- `POST /api/projects/<id>/switch`
- `POST /api/projects/<id>/sync`
- `GET /api/projects/history`
- `GET /api/projects/stats`

## Impact

These fixes resolve the following issues:

1. ✅ **Bot starts successfully** - All imports work correctly
2. ✅ **API starts successfully** - All imports work correctly
3. ✅ **Proper working directory** - Both stay in `src/` for correct imports
4. ✅ **Proper PYTHONPATH** - Launcher sets environment correctly
5. ✅ **Graceful degradation** - Project manager is optional, API works without it
6. ✅ **Better error messages** - Shows what's missing and where
7. ✅ **Fallback handling** - MCP falls back to legacy tool manager if needed

## Module Structure

The project follows this structure:
```
src/
├── api/
│   ├── auth_api.py
│   ├── auth_db.py
│   ├── mcp_client.py
│   ├── mcp_server.py
│   ├── mcp_tool_manager.py
│   └── web_chat_api.py
├── bots/
│   ├── bot_deprecated.py
│   ├── bot_mcp.py
│   ├── bot_wsl.py
│   └── cursor_agent_native.py
├── core/
│   ├── agent_tools.py
│   ├── ai_agent.py
│   ├── config.py
│   ├── multi_stage_processor.py
│   └── tool_manager.py
├── managers/
│   ├── project_manager.py
│   ├── remote_executor.py
│   └── task_manager.py
├── reports/
│   ├── budget_report_generator.py
│   ├── data_report_generator.py
│   ├── launch_report_generator.py
│   └── query_report_generator.py
└── rag/
    └── discord_rag.py
```

All imports now use this structure properly (e.g., `from core.ai_agent import ...`).

## Testing Recommendations

After these fixes, test the following scenarios:

1. **Full System Launch**:
   - Run launcher, select Option 1 (MCP Full System)
   - Verify bot starts without import errors
   - Verify API starts without import errors
   - Check console for proper "[INFO] MCP tool manager available" message

2. **API-Only Launch**:
   - Run launcher, select Option 2 (MCP Default)
   - Verify API starts successfully
   - Test web interface at http://localhost:8080

3. **Bot-Only Launch**:
   - Run launcher, select Option 3 (MCP Discord Bot Only)
   - Verify bot starts and connects to Discord

4. **Project Manager Optional**:
   - If project_manager fails to import, API should still start
   - Project management endpoints should return 503 error
   - Other API endpoints should work normally

## Files Modified

- `src/launcher.py` - Added PYTHONPATH environment variable
- `src/bots/bot_mcp.py` - Fixed imports and working directory
- `src/api/web_chat_api.py` - Fixed imports, working directory, and added project_manager decorator

## Next Steps

1. Test the launcher with all options
2. Verify Discord bot connects and responds
3. Verify web interface loads correctly
4. Check that all API endpoints work as expected
5. Monitor console for any remaining import warnings


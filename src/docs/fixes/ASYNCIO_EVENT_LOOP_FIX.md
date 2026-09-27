# AsyncIO Event Loop Fix for Web Chat Claude Commands

## Problem

When using Claude commands (e.g., `/claude hello world`) in the web chat on the landing page, the following error occurred:

```
✅ Action: [FAIL] **Claude Code Error:** There is no current event loop in thread 'Thread-30 (process_request_thread)'.
```

## Root Cause

The issue occurred in `claude_code_tool.py` in the `send_prompt_to_claude_code_sync()` function. This function serves as a bridge between synchronous code (like Flask request handlers) and the async Claude Code CLI functionality.

The problem:
1. Flask runs each request in a separate thread (e.g., `Thread-30`)
2. These threads do not have an asyncio event loop by default
3. The old code used `asyncio.get_event_loop()`, which in Python 3.10+ raises a `RuntimeError` when called from a thread without an event loop
4. The error was not properly caught, causing the command to fail

## Solution

Updated the `send_prompt_to_claude_code_sync()` function to properly handle both scenarios:

### Before (Problematic)
```python
def send_prompt_to_claude_code_sync(prompt: str, project_hint: str = None) -> str:
    try:
        loop = asyncio.get_event_loop()  # ❌ Fails in Flask threads
        if loop.is_running():
            # ... handle running loop
        else:
            return loop.run_until_complete(...)
    except Exception as e:
        return f"[FAIL] **Claude Code Error:** {str(e)}"
```

### After (Fixed)
```python
def send_prompt_to_claude_code_sync(prompt: str, project_hint: str = None) -> str:
    try:
        # Try to get RUNNING loop (not just any loop)
        loop = asyncio.get_running_loop()
        # If we're here, we're in an async context - run in separate thread
        # ... (thread-based execution)
    except RuntimeError:
        # ✅ No running loop - we're in sync context (Flask thread)
        # Create new event loop and run the async code
        new_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(new_loop)
        try:
            result = new_loop.run_until_complete(
                send_prompt_to_claude_code(prompt, project_hint)
            )
            return result
        finally:
            new_loop.close()
            asyncio.set_event_loop(None)
```

## Key Improvements

1. **Use `asyncio.get_running_loop()`** instead of `asyncio.get_event_loop()`
   - `get_running_loop()` raises `RuntimeError` when there's no running loop, which we can catch
   - This is the recommended approach in Python 3.10+

2. **Explicit event loop creation** for synchronous contexts
   - When in a Flask thread (no event loop), we create a fresh event loop
   - Run the async code in this new loop
   - Clean up properly afterwards

3. **Better error handling**
   - Catch and handle the specific `RuntimeError` from `get_running_loop()`
   - Provide detailed error messages with stack traces for debugging

## Testing

To test the fix:

1. Start the web chat API:
   ```bash
   python web_chat_api.py
   ```

2. Open the landing page in your browser:
   ```
   http://localhost:8080
   ```

3. Try a Claude command in the chat:
   ```
   /claude hello world
   ```

4. The command should now execute successfully without the event loop error

## Impact

This fix affects:
- ✅ Web chat Claude commands (`/claude ...`)
- ✅ Any other async tools called from Flask request handlers
- ✅ Maintains backward compatibility with Discord bot (which has its own event loop)

## Files Modified

- `claude_code_tool.py` - Fixed the `send_prompt_to_claude_code_sync()` function

## Related

This is a common issue when bridging sync and async Python code, especially in web frameworks like Flask that use threaded request handlers. The fix follows Python's asyncio best practices for Python 3.10+.

## Date

October 9, 2025


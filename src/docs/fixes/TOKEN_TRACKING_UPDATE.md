# Token Tracking Update for External AI Tools

## Summary
Added comprehensive token usage tracking for the `/claude` command and prepared the infrastructure for tracking any external AI tool calls (Cursor, Claude, etc.).

## Changes Made

### 1. Updated `tool_manager.py`
**Function:** `_track_tool_call()`
- Added optional parameters: `model`, `tokens`, `cost`
- These parameters are now passed to `tracker.add_tool_call()` for proper token tracking

**Before:**
```python
def _track_tool_call(tool_name: str, parameters: dict, start_time: float, result: str, success: bool = True):
```

**After:**
```python
def _track_tool_call(tool_name: str, parameters: dict, start_time: float, result: str, success: bool = True,
                    model: str = None, tokens: Dict = None, cost: float = 0.0):
```

### 2. Updated `bot_mcp.py`
**Function:** `handle_claude_command()`
- Now extracts usage information from Claude Code Tool results
- Passes token data to `_track_tool_call()` for proper logging

**Key Changes:**
- Extracts `usage_info` from the result
- Converts token data to the format expected by query reports:
  - `total_tokens`
  - `prompt_tokens` (from `input_tokens`)
  - `completion_tokens` (from `output_tokens`)
- Passes model name as `claude-3-{model}` for consistency
- Includes cost calculation from Claude Code Tool

## How It Works

1. **Token Extraction:** When `/claude` command executes, it receives a result dict containing `usage_info` from `ClaudeCodeTool.execute_claude_command()`

2. **Token Tracking:** The usage info is extracted and converted to a standardized format:
   ```python
   tokens_dict = {
       "total_tokens": usage_info.get("total_tokens", 0),
       "prompt_tokens": usage_info.get("input_tokens", 0),
       "completion_tokens": usage_info.get("output_tokens", 0)
   }
   ```

3. **Report Integration:** The token data flows through:
   - `_track_tool_call()` → 
   - `tracker.add_tool_call()` → 
   - Also calls `tracker.add_llm_call()` (if tokens > 0) →
   - Shows up in query report HTML

4. **Display:** Token usage appears in:
   - Tool Calls section (with model name, token count, breakdown)
   - LLM Calls section (for budget tracking)
   - Total token count and cost summary

## Query Report Display

The query reports now show for `/claude` commands:
- 🦑 Model name (e.g., "claude-3-haiku")
- Token count (e.g., "1,234 tokens")
- Token breakdown (Prompt: 400 | Completion: 834)
- Cost (e.g., "$0.001234")

This information appears in:
1. **Execution Stages** - shows the tool call with token info
2. **Tool Calls** - detailed breakdown of each tool invocation
3. **LLM Calls** - aggregated for budget tracking
4. **Cost Summary** - total tokens and costs

## Testing

To test the changes:
1. Use `/claude "Create a hello world file"` in Discord
2. Check the query report (logs)
3. Verify token count is displayed in:
   - Tool Calls section
   - LLM Calls section
   - Cost Summary

## Future Enhancements

The infrastructure is now ready to track tokens for:
- **Cursor API calls** (when direct API integration is available)
- **Other AI tools** (GPT-4, etc.)
- Any tool that makes external AI API calls

Simply pass the `model`, `tokens`, and `cost` parameters to `_track_tool_call()` when available.

## Technical Notes

- The Claude Code Tool already parses token usage from CLI output
- Token tracking is backward compatible (optional parameters)
- Failed commands don't report tokens (None values used)
- The query report system handles both tool calls with and without token info


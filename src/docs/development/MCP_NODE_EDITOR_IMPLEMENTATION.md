# MCP Tools Node Editor Implementation

## Summary

Successfully integrated MCP (Model Context Protocol) tools into the Cuttle Node Editor, including Claude Code and Cursor command support. LLM nodes now have a dedicated `tools` input port for connecting MCP tools.

## Implementation Date
October 12, 2025

## Changes Made

### 1. New Node Types (node_types.js)

#### MCP Tool Nodes
- **tool-claude-code**: Execute Claude Code commands with configurable models (Haiku, Sonnet, Opus)
- **tool-cursor**: Send commands to Cursor IDE with project selection
- **tool-mcp-generic**: Generic MCP tool executor for any MCP server tool

#### LLM Node Enhancements
- Added `tools` input port to all LLM nodes (OpenAI, Anthropic)
- Added `enableTools` configuration option
- LLM nodes can now accept tool definitions and use them during generation

### 2. Node Palette UI (node_editor.html)

#### New Category: MCP Tools
Added a dedicated "MCP Tools" category in the node palette with:
- Claude Code node
- Cursor Command node
- Generic MCP Tool node

Also added these nodes to the context menu for easy access.

### 3. Pipeline Executor (pipeline_executor.js)

#### Enhanced Tool Execution
- Added intelligent input processing for MCP tools
- Claude Code: Extracts prompts from various input formats
- Cursor: Extracts commands from different input types
- Better error handling and logging for MCP tool execution
- Returns properly formatted text results

### 4. Backend API (web_chat_api.py)

#### New MCP Tool Handlers in `/api/execute-tool`

**Claude Code Handler:**
- Imports ClaudeCodeTool
- Configurable model selection (haiku/sonnet/opus)
- Project path mapping
- Session management
- Usage tracking

**Cursor Handler:**
- Project path mapping for common projects
- Command execution via tool_manager
- Custom path support

**Generic MCP Handler:**
- Async MCP tool manager integration
- JSON parameter parsing
- Dynamic tool execution
- Error handling

### 5. Example Pipeline

Created `pipelines/example_mcp_tools.json` demonstrating:
- Manual trigger
- Text input for prompts
- Claude Code execution
- LLM with tool access
- Cursor command execution
- Parallel tool usage
- Log outputs

### 6. Documentation

Created comprehensive guide: `docs/guides/MCP_TOOLS_NODE_EDITOR.md`

Topics covered:
- Overview of MCP tools
- Detailed node type documentation
- LLM tool integration guide
- Example pipelines
- Best practices
- Troubleshooting
- API integration details

## Features

### Core Capabilities
✅ Claude Code integration with model selection
✅ Cursor IDE command execution
✅ Generic MCP tool support
✅ LLM nodes can use tools via input port
✅ Project path mapping
✅ Query tracking for cost analysis
✅ Comprehensive error handling
✅ Visual node palette integration

### Configuration Options

**Claude Code:**
- Model: haiku, sonnet, opus
- Project: sandbox, current, custom
- Auto-execute toggle

**Cursor:**
- Project selection (current, escape_purgatory, pc_bot, jambit, custom)
- Open files toggle
- Wait for completion toggle

**MCP Generic:**
- Server selection
- Tool name input
- JSON parameters

**LLM Nodes:**
- Enable/disable tools
- Tools input port for connecting tool nodes

## Usage Examples

### Basic Claude Code
```
[Text Input: "Create hello.py"] → [Claude Code] → [Log Output]
```

### LLM with Tools
```
[User Input] → [LLM (tools enabled)] → [Response]
                         ↓
                 [Claude Code]
                 [Cursor]
                 [MCP Tool]
```

### Multi-Step Pipeline
```
[Trigger] → [LLM: Plan] → [Claude Code] → [Cursor] → [Log]
```

## Technical Details

### Frontend-Backend Flow

1. **User creates node** → Node type registered with definition
2. **User configures** → Properties stored in node.config
3. **Pipeline runs** → Executor calls executeToolNode()
4. **Input processing** → Extract prompt/command from various formats
5. **API call** → POST to /api/execute-tool
6. **Backend execution** → Import appropriate tool, execute
7. **Result return** → JSON response with result/error
8. **Display** → Log to console, pass to next node

### MCP Integration Points

**Frontend:**
- `node_types.js` - Node definitions
- `pipeline_executor.js` - Execution logic
- `node_editor.html` - UI elements

**Backend:**
- `web_chat_api.py` - API endpoints
- `claude_code_tool.py` - Claude Code implementation
- `tool_manager.py` - Tool execution
- `mcp_tool_manager.py` - MCP server communication

### Query Tracking

All MCP tool executions are tracked:
- Tool name and parameters
- Start/end time
- Success/failure status
- Token usage (for Claude Code)
- Cost calculation
- Results preview

Reports available at: `/query_reports.html`

## Testing Recommendations

### Test Cases

1. **Basic Claude Code**
   - Create simple script
   - Generate complex function
   - Refactor existing code

2. **Cursor Commands**
   - Open file
   - Navigate to line
   - Apply edits

3. **LLM + Tools**
   - LLM decides to use Claude Code
   - LLM decides to use Cursor
   - LLM uses multiple tools in sequence

4. **Error Handling**
   - Invalid prompts
   - Missing API keys
   - Unreachable tools
   - Malformed JSON parameters

5. **Pipeline Integration**
   - Tool chaining
   - Parallel execution
   - Conditional tool selection

## Dependencies

### Required
- `claude_code_tool.py` - Claude Code implementation
- `tool_manager.py` - Tool execution
- `mcp_tool_manager.py` - MCP server communication
- Anthropic API key (for Claude Code)
- Cursor IDE installed (for Cursor commands)

### Optional
- MCP server running (for generic MCP tools)
- Unity MCP (for Unity-specific tools)

## Known Limitations

1. **Synchronous Execution**: MCP tools run sequentially (by design)
2. **Project Paths**: Hardcoded paths in API (can be made configurable)
3. **Tool Discovery**: Manual tool configuration (no auto-discovery yet)
4. **Real-time Feedback**: No streaming output (shows final result only)

## Future Enhancements

### Planned Features
- [ ] Real-time tool execution progress
- [ ] Tool result preview in properties panel
- [ ] Auto-discover available MCP tools
- [ ] Custom MCP server configuration UI
- [ ] Tool usage analytics dashboard
- [ ] Tool marketplace/library
- [ ] Streaming output for long-running tools
- [ ] Tool retry logic
- [ ] Tool timeout configuration
- [ ] Multi-tool parallel execution

### Potential Improvements
- Dynamic project path configuration
- Tool credential management UI
- Tool execution history per node
- Tool performance metrics
- Tool recommendation system

## Migration Notes

### For Existing Pipelines
- Old pipelines remain compatible
- No breaking changes to existing nodes
- New features opt-in via configuration

### For Developers
- MCP tool template available in node_types.js
- API endpoint pattern established
- Query tracking integrated automatically

## Support

### Troubleshooting
1. Check console logs in browser (F12)
2. Review query reports for detailed execution logs
3. Verify API server is running (port 8080)
4. Check that bot_mcp.py is running for MCP tools

### Common Issues

**"MCP client not connected"**
- Start bot_mcp.py
- Verify mcp_tool_manager is imported

**"Claude Code not available"**
- Check Anthropic API key in environment
- Verify claude_code_tool.py exists

**"Cursor command failed"**
- Ensure Cursor is in PATH
- Check project paths are valid

## References

- [MCP Tools Guide](docs/guides/MCP_TOOLS_NODE_EDITOR.md)
- [Example Pipeline](pipelines/example_mcp_tools.json)
- [MCP Protocol](https://modelcontextprotocol.io)
- [Claude Code Tool](claude_code_tool.py)
- [Tool Manager](tool_manager.py)

## Credits

Implementation by: Cursor AI Assistant
Date: October 12, 2025
Version: 1.0.0


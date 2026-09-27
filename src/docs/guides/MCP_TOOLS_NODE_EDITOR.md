# MCP Tools in Node Editor

This guide explains how to use MCP (Model Context Protocol) tools in the Cuttle Node Editor, including Claude Code and Cursor integration.

## Overview

The node editor now supports MCP tools that allow you to:
- Execute Claude Code commands for AI-powered code generation
- Send commands to Cursor IDE
- Use any generic MCP tool from connected servers
- Connect tools to LLM nodes for enhanced AI capabilities

## New Node Types

### 🔮 Claude Code Node
Execute Claude Code commands directly from your pipeline.

**Inputs:**
- `prompt` (string): The instruction for Claude Code

**Outputs:**
- `result` (string): The generated code or Claude's response

**Configuration:**
- **Model**: Choose between Haiku (cheaper), Sonnet (better), or Opus (best)
- **Project Context**: Select the project directory (sandbox, current, or custom path)
- **Auto-Execute**: Optionally auto-execute tool suggestions

**Example Use Cases:**
- Generate code snippets
- Refactor existing code
- Create entire files
- Fix bugs with AI assistance

### 🖱️ Cursor Command Node
Send commands to Cursor IDE for file operations and navigation.

**Inputs:**
- `command` (string): The command to send to Cursor

**Outputs:**
- `result` (string): Status message from Cursor

**Configuration:**
- **Project**: Select which project to open in Cursor
- **Open Files**: Automatically open relevant files
- **Wait for Completion**: Wait for Cursor to finish before continuing

**Example Use Cases:**
- Open specific files
- Navigate to specific lines
- Apply edits suggested by LLM
- Trigger Cursor AI commands

### 🛠️ MCP Tool Node (Generic)
Execute any MCP tool from connected servers.

**Inputs:**
- `input` (any): Input data for the tool

**Outputs:**
- `output` (any): Tool execution result

**Configuration:**
- **MCP Server**: Choose which MCP server to use
- **Tool Name**: Name of the specific tool to execute
- **Parameters**: JSON object with tool parameters

## LLM Tool Integration

All LLM nodes (OpenAI, Anthropic) now have:
- **Tools input port**: Connect tool nodes to provide tools to the LLM
- **Enable Tools checkbox**: Toggle whether the LLM can use connected tools

### How Tool Integration Works

1. **Connect Tools**: Wire tool nodes to the `tools` input of an LLM node
2. **Enable Tools**: Check the "Enable Tools" option in LLM properties
3. **LLM Decides**: The LLM automatically decides when to use the tools
4. **Tool Execution**: Tools are executed and results are fed back to the LLM

## Example Pipelines

### Simple Claude Code Pipeline

```
[Text Input] → [Claude Code] → [Log Output]
     ↓
"Create a hello world script"
```

### LLM with Tool Access

```
[Text Input] → [LLM Node] → [Output]
                    ↓
              [Claude Code]
              [Cursor]
              [MCP Tool]
```

In this setup:
- The LLM can choose to use Claude Code, Cursor, or other MCP tools
- Tools are connected to the `tools` input port
- The LLM automatically decides when each tool is appropriate

### Multi-Step Code Generation

```
[Trigger] → [LLM: Generate Plan] → [Claude Code] → [Cursor] → [Log]
```

Flow:
1. LLM creates a plan for code generation
2. Claude Code generates the actual code
3. Cursor opens the file and applies the code
4. Results are logged

## Advanced Features

### Tool Chaining
Connect multiple MCP tools in sequence:
```
[Input] → [Claude Code] → [Cursor] → [MCP Tool] → [Output]
```

### Parallel Tool Execution
Use a Parallel node to run multiple tools simultaneously:
```
           ┌→ [Claude Code] →┐
[Input] →  ├→ [Cursor]       ├→ [Merge] → [Output]
           └→ [MCP Tool]     →┘
```

### Conditional Tool Selection
Use Conditional nodes to choose which tool based on context:
```
[Input] → [Conditional] → True  → [Claude Code] → [Output]
                       → False → [Cursor]      → [Output]
```

## Best Practices

### 1. Model Selection
- **Haiku**: Fast and cheap for simple tasks
- **Sonnet**: Best balance for most tasks
- **Opus**: Most capable for complex reasoning

### 2. Project Context
- Use specific project paths for better context
- "sandbox" for experimental/isolated work
- "current" for working in the active directory

### 3. Error Handling
- Always connect tool outputs to Log nodes for debugging
- Use Text Output debug nodes to inspect intermediate results
- Check console for detailed execution logs

### 4. Tool Combinations
- Combine Claude Code for generation + Cursor for application
- Use LLM for planning + MCP tools for execution
- Chain tools for multi-step workflows

## Troubleshooting

### Claude Code Not Working
- Verify Anthropic API key is set
- Check that claude_code_tool.py is available
- Review console logs for detailed errors

### Cursor Not Responding
- Ensure Cursor is installed and accessible via PATH
- Verify project paths are correct
- Check that Cursor IDE is running

### MCP Tools Failing
- Confirm MCP server is running (check bot_mcp.py)
- Verify tool names match available MCP tools
- Check parameter formatting (must be valid JSON)

## API Integration

The node editor communicates with MCP tools via these API endpoints:

- `/api/execute-tool` - Execute any tool node
- `/api/llm-request` - Execute LLM with optional tools
- Tools are tracked in query reports for cost analysis

## Future Enhancements

Planned features:
- Real-time tool result preview
- Tool usage statistics per pipeline
- Custom MCP server configuration in UI
- Tool marketplace/library

## Related Documentation

- [MCP Server Setup](../setup/MCP_SERVER_SETUP.md)
- [Discord Bot MCP Commands](../guides/DISCORD_MCP_COMMANDS.md)
- [Tool Manager API](../development/TOOLS_API.md)

## Support

For issues or questions:
- Check console logs in the node editor
- Review query reports at `/query_reports.html`
- Check Discord bot logs if using bot integration


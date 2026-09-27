# Cuttle Node Graph Editor

> **Removed (2026-09-26).** Visual pipeline graphs, the trigger executor, and the Node Editor are gone. `/node_editor.html` redirects to the Router editor. Chat uses slash agents + the agent router. Recover old graphs from git/Gitea history. The rest of this file is historical.

A visual pipeline builder for creating AI agent workflows using a node-based interface, similar to n8n but specifically designed for Cuttle's tool ecosystem.

![Node Editor](../img/cuttle-logo.png)

## Overview

The Node Graph Editor allows you to visually design and execute AI agent pipelines by connecting different types of nodes together. Each node represents a specific operation (trigger, LLM call, tool execution, output, etc.), and connections between nodes define the data flow.

## Getting Started

### Launch the Editor

```bash
python start_node_editor.py
```

This will:
1. Start the web server on port 8080
2. Automatically open the node editor in your browser
3. Display the editor at `http://localhost:8080/node_editor.html`

### Interface Layout

The editor consists of four main areas:

1. **Toolbar** (Top): File operations, execution controls, zoom controls
2. **Node Palette** (Left): Available node types organized by category
3. **Canvas** (Center): Main workspace for building pipelines
4. **Properties Panel** (Right): Configure selected node properties

## Node Types

### 🎯 Trigger Nodes (Input Stage)

Start points for your pipeline execution.

- **Manual Trigger**: Manually start the pipeline
- **Webhook**: Trigger via HTTP webhook
- **Schedule**: Trigger on a cron schedule
- **File Watch**: Trigger when files change

### 🤖 LLM Nodes

AI language model integrations.

- **OpenAI**: Use GPT models (GPT-4o, GPT-4-turbo, GPT-3.5-turbo)
  - Configurable model, temperature, max tokens
  - System prompt customization
  
- **Anthropic Claude**: Use Claude models (Opus, Sonnet, Haiku)
  - Similar configuration to OpenAI
  
- **Prompt Builder**: Build dynamic prompts with template variables
  - Use `{{variable}}` for substitution

### 🛠️ Tool Nodes

Execute system operations and automations.

- **Screenshot**: Capture desktop or specific windows
  - Target: Desktop, Unity, Cursor
  
- **Input Control**: Automate keyboard/mouse input
  - Actions: Type text, Press key, Click
  
- **Process Manager**: Launch and manage programs
  - Launch Unity, Cursor, or custom programs
  - Project hints for specific instances
  
- **Window Control**: Find and manipulate windows
  - Find, focus, or close windows by title
  
- **OCR**: Extract text from images
  - Multiple language support
  
- **Custom Tool**: Execute custom tool functions
  - Specify tool name and parameters

### 📤 Output Nodes (Output Stage)

Send results to various destinations.

- **Log Output**: Write to console logs
  - Log levels: Debug, Info, Warning, Error
  
- **File Output**: Save to file system
  - Formats: Text, JSON, CSV
  
- **Discord Output**: Send to Discord channels
  - Plain text, embeds, or code blocks
  
- **Webhook Output**: POST to external webhooks
  - Configurable URL and method

### 📦 Group Nodes

Composite nodes that encapsulate complex logic.

- **Toolbox**: Container for multiple tools
  - Define available tools
  - Attach to agent nodes
  
- **AI Agent**: Complete agent with LLM + Tools
  - Choose LLM model
  - Select available tools
  - Optional post-processing step
  - Example: LLM plans → Execute tools → LLM summarizes
  
- **Parallel Execution**: Run operations in parallel
  - Configure max concurrent operations
  
- **Conditional**: Branch based on conditions
  - JavaScript expression evaluation
  - True/False output paths

### 🔧 Utility Nodes

Data manipulation and flow control.

- **Transform Data**: Apply JavaScript transformations
  - Custom function body
  
- **Filter**: Filter arrays based on conditions
  
- **Merge**: Combine data from multiple sources
  - Modes: Combine, Override, Append
  
- **Delay**: Add time delays between operations
  - Configurable delay in milliseconds

## Building a Pipeline

### 1. Add Nodes

**Method 1**: Drag from palette
- Find the node in the left palette
- Drag it onto the canvas
- Drop to place

**Method 2**: Search
- Use the search box in the palette
- Type to filter nodes
- Drag the result to canvas

### 2. Connect Nodes

1. Click on an **output port** (right side of a node)
2. Drag to an **input port** (left side of another node)
3. Release to create the connection

**Port Colors** indicate data types:
- 🟢 Green: Trigger
- 🔵 Blue: String
- 🟠 Orange: Number
- 🟣 Purple: Boolean
- 🔴 Pink: Array
- 🔵 Teal: Object
- 🔴 Red: File
- ⚪ Gray: Any

### 3. Configure Nodes

1. Click on a node to select it
2. Properties panel opens on the right
3. Edit configuration values:
   - Text inputs for simple values
   - Dropdowns for options
   - Text areas for longer content (prompts, code)
   - Checkboxes for boolean flags

### 4. Execute Pipeline

1. Click the **Run** button (▶️) in the toolbar
2. Execution log modal opens
3. Watch real-time progress:
   - Node states update (running → success/error)
   - Log entries show execution steps
   - Errors are highlighted in red

## Example Pipelines

### Example 1: Simple Screenshot + LLM Description

```
Manual Trigger → Screenshot (Desktop) → OpenAI (Describe image) → Log Output
```

1. Add Manual Trigger node
2. Add Screenshot node (target: desktop)
3. Add OpenAI node (prompt: "Describe what you see")
4. Add Log Output node
5. Connect: Trigger → Screenshot → OpenAI → Log
6. Run pipeline

### Example 2: AI Agent with Tools

```
Manual Trigger → AI Agent (LLM + Toolbox) → Discord Output
```

The AI Agent node contains:
- LLM for planning
- Toolbox with: screenshot, input, process
- Optional post-processing LLM

### Example 3: Scheduled Automation

```
Schedule (Cron) → Process (Launch Unity) → Screenshot → File Output
```

1. Schedule node (e.g., "0 9 * * *" for daily at 9am)
2. Process Manager (launch Unity with project hint)
3. Screenshot (target: Unity)
4. File Output (save screenshot)

### Example 4: Conditional Workflow

```
Webhook → Transform → Conditional → [True: Action A] / [False: Action B]
```

The Conditional node evaluates a condition and routes to different outputs.

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Ctrl+S` | Save pipeline |
| `Ctrl+C` | Copy selected nodes |
| `Ctrl+V` | Paste copied nodes |
| `Ctrl+A` | Select all nodes |
| `Delete` / `Backspace` | Delete selected nodes |
| `Shift+Click` | Pan canvas |
| `Scroll` | Zoom in/out |
| `Middle Click` | Pan canvas |

## Mouse Controls

- **Left Click**: Select node, start dragging
- **Left Click + Drag**: Move node or create connection
- **Right Click**: Context menu
- **Middle Click / Shift+Left Click**: Pan canvas
- **Scroll Wheel**: Zoom in/out

## Context Menu

Right-click on a node to access:
- **Edit Node**: Open properties panel
- **Duplicate**: Create a copy
- **Delete**: Remove the node
- **Group Selection**: Create a group node (future)
- **Ungroup**: Ungroup a group node (future)

## Saving & Loading Pipelines

### Save Pipeline

1. Click **Save** button (💾)
2. Enter pipeline name
3. Add optional description
4. Click **Save**

Pipelines are saved to `pipelines/` directory as JSON files.

### Open Pipeline

1. Click **Open** button (📂)
2. Select from list of saved pipelines
3. Click to load

### Pipeline Format

Pipelines are stored as JSON with:
- Metadata (name, description, version)
- Node definitions (type, position, configuration)
- Connection definitions (source/target nodes and ports)
- View state (zoom level, viewport position)

```json
{
  "name": "My Pipeline",
  "description": "Does something cool",
  "version": "1.0.0",
  "nodes": [...],
  "connections": [...],
  "view": {
    "zoom": 1.0,
    "offsetX": 0,
    "offsetY": 0
  },
  "timestamp": "2025-09-30T12:00:00Z"
}
```

**Note**: The viewport zoom and position are automatically saved and restored when you save/load pipelines, so your view will be exactly as you left it.

## Execution Model

### Flow-Based Execution

1. **Start from Triggers**: Execution begins at trigger nodes (nodes with no inputs)
2. **Depth-First Traversal**: Each node executes after its dependencies complete
3. **Data Passing**: Output from one node becomes input to connected nodes
4. **State Tracking**: Nodes show their execution state:
   - ⚪ Idle: Not executed yet
   - 🔵 Running: Currently executing
   - 🟢 Success: Completed successfully
   - 🔴 Error: Failed with error

### Parallel Execution

Use the **Parallel Execution** group node to run multiple operations concurrently with a configurable concurrency limit.

### Error Handling

If a node fails:
- The node is marked with error state
- Error message is logged
- Downstream nodes are NOT executed
- Other independent branches continue

## Integration with Cuttle

### Tool Integration

All Cuttle tools are available as Tool Nodes:
- `agent_tools.py` functions are mapped to node types
- Configuration maps to function parameters
- Results are passed through connections

### LLM Integration

- Uses the same LLM clients as the main bot
- API keys from `.env` file
- Supports both OpenAI and Anthropic

### Agent Patterns

The AI Agent group node implements the standard Cuttle agent pattern:
1. LLM receives prompt and tool descriptions
2. LLM plans actions
3. Tools are executed
4. (Optional) LLM post-processes results

## API Endpoints

The node editor uses these backend endpoints:

### Pipeline Management
- `POST /api/save-pipeline`: Save pipeline to disk
- `GET /api/list-pipelines`: List all saved pipelines
- `GET /api/load-pipeline/<id>`: Load specific pipeline
- `DELETE /api/delete-pipeline/<id>`: Delete pipeline

### Execution
- `POST /api/llm-request`: Execute LLM node
- `POST /api/execute-tool`: Execute tool node
- `POST /api/execute-output`: Execute output node

## Troubleshooting

### Node Won't Execute

Check:
- ✅ All required inputs are connected
- ✅ Configuration is complete (no empty required fields)
- ✅ Previous nodes executed successfully
- ✅ API keys are configured (for LLM nodes)

### Connection Won't Create

Check:
- ✅ Connecting output to input (not input to input)
- ✅ Data types are compatible
- ✅ Target port isn't already connected

### Save/Load Issues

Check:
- ✅ `pipelines/` directory exists and is writable
- ✅ Pipeline name is valid (no special characters)
- ✅ Disk space available

### LLM Errors

Check:
- ✅ API keys are set in `.env` file
- ✅ API key is valid and has credits
- ✅ Model name is correct
- ✅ Token limits are reasonable

## Advanced Features

### Custom Tool Creation

To add custom tools:

1. Create tool function in `agent_tools.py`
2. Add mapping in `web_chat_api.py` → `/api/execute-tool`
3. (Optional) Add new node type in `web/js/node_types.js`

### Pipeline Templates

Save common patterns as pipeline templates:

1. Build the pipeline
2. Save with descriptive name
3. Load as starting point for new pipelines

### Nested Groups

Group nodes can contain sub-pipelines:

1. Select multiple nodes
2. Right-click → "Group Selection"
3. Creates a composite node
4. Edit the group to see internal pipeline

## Tips & Best Practices

### Organization

- 📝 Use descriptive node names
- 🎨 Group related nodes visually
- 📂 Save intermediate versions
- 📋 Add pipeline descriptions

### Performance

- ⚡ Use Parallel nodes for independent operations
- 💾 Minimize LLM calls (they're slow and expensive)
- 🔄 Cache results when possible
- ⏱️ Add delays between API-heavy operations

### Debugging

- 📊 Add Log Output nodes to inspect data
- 🔍 Run pipeline step-by-step (use delays)
- 📝 Check execution log for errors
- 🧪 Test nodes individually before connecting

### Reusability

- 🧩 Create reusable group nodes
- 📦 Save common patterns as templates
- 🔧 Use generic Toolbox nodes
- 🎯 Parameterize configurations

## Future Enhancements

Planned features:
- [ ] Variable system for pipeline-wide parameters
- [ ] Breakpoints and step debugging
- [ ] Node search by type/name
- [ ] Minimap for large pipelines
- [ ] Export to code (Python script generation)
- [ ] Import from existing scripts
- [ ] Collaborative editing
- [ ] Version control integration
- [ ] Pipeline analytics and optimization

## Support

For issues or questions:
1. Check this guide
2. Check the main [Setup Instructions](../setup/SETUP_INSTRUCTIONS.md)
3. Review example pipelines in `pipelines/examples/`
4. Check the execution logs for errors

## License

Part of the Cuttle project. See main project license.

---

**Happy Pipeline Building! 🔗✨**


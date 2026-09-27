# Execution Graph Enhancement - Query Report to Node Editor Alignment

## Summary

The query report execution graph has been enhanced to accurately represent the actual node editor pipeline flow, similar to LangGraph visualizations. The system now tracks and displays:

1. **Graph Structure**: Nodes and connections from the node editor
2. **Parallel Execution**: Shows when multiple nodes run simultaneously
3. **Execution Mode**: Displays the agent execution strategy (single, multi, multi-lite)
4. **Execution Order**: Shows the sequence in which nodes were executed
5. **Node Status**: Visual indicators for success/failure/not-executed

## What Was Changed

### 1. Query Report Generator (`query_report_generator.py`)

#### New Data Structures
Added graph structure tracking to `execution_data`:
```python
"graph_structure": {
    "nodes": [],           # List of node objects with id, name, type, category
    "connections": [],     # List of connections between nodes
    "execution_order": []  # Chronological execution sequence
},
"execution_mode": None  # single, multi, multi-lite
```

#### New Methods
- **`add_graph_node()`**: Register a node in the graph
- **`add_graph_connection()`**: Register a connection between nodes
- **`record_node_execution()`**: Track when a node executes (with timing and status)
- **`set_graph_structure()`**: Set complete graph structure at once
- **`_create_node_editor_graph_html()`**: Generate LangGraph-style visualization
- **`_create_linear_graph_html()`**: Original linear visualization for non-pipeline executions

#### Enhanced Visualization
The agent execution graph now:
- **Detects graph structure**: Checks if pipeline data is available
- **Shows parallel execution**: Multiple nodes in same layer displayed side-by-side
- **Color-codes by category**:
  - 🎯 Trigger (purple)
  - 🤖 LLM (blue)
  - 🛠️ Tool (orange)
  - 📤 Output (green)
  - 📦 Group (pink)
  - 🔧 Utility (indigo)
- **Status indicators**: Green border (success), red border (error), dashed border (not executed)
- **Execution badges**: Shows execution order number (#1, #2, #3...)
- **Timing info**: Displays duration for executed nodes

### 2. Pipeline Executor (`web/js/pipeline_executor.js`)

#### Graph Structure Tracking
Pipeline executor now sends graph structure on execution start:
```javascript
graphStructure: {
    nodes: [
        { id, name, type, category }
    ],
    connections: [
        { from, to, fromPort, toPort }
    ]
}
```

#### Node Execution Tracking
Each node execution is recorded via `/api/record-node-execution`:
- Tracks success/failure status
- Records timing information
- Updates execution order

### 3. Web Chat API (`web_chat_api.py`)

#### Enhanced `/api/pipeline-execution-start`
Now accepts `graphStructure` parameter:
- Stores nodes and connections in query tracker
- Logs graph structure details

#### New `/api/record-node-execution`
Tracks individual node execution:
- Accepts `queryId`, `nodeId`, `success`
- Records execution timing and status
- Updates execution order

### 4. Execution Mode Tracking

#### Multi-Stage Processor
The execution mode is now tracked and displayed:
- **Single**: Direct LLM processing
- **Multi**: Regex → LLM Evaluation → LLM Execution/Conversation
- **Multi-Lite** (default): Regex → Combined LLM Processing

This appears in:
- Query report agent config
- Linear execution graph badge
- Report metadata

## How It Works

### Execution Flow

1. **User clicks "Run" in Node Editor**
   ```
   Pipeline Executor → /api/pipeline-execution-start
   ```

2. **Backend receives graph structure**
   ```python
   tracker.set_graph_structure(nodes, connections)
   ```

3. **Nodes execute sequentially or in parallel**
   ```javascript
   For each node:
     - Execute node logic
     - POST /api/record-node-execution
     - Backend records timing and status
   ```

4. **Pipeline completes**
   ```
   Pipeline Executor → /api/pipeline-execution-finish
   ```

5. **Query report generated**
   - Detects graph structure exists
   - Uses `_create_node_editor_graph_html()`
   - Renders LangGraph-style visualization

### Graph Layout Algorithm

The visualization uses a **breadth-first layer algorithm**:

1. **Find root nodes**: Nodes with no incoming connections
2. **Build layers**: BFS traversal to group nodes by depth
3. **Detect parallel execution**: Multiple nodes in same layer
4. **Render with visual indicators**:
   - Horizontal layout for parallel nodes
   - Vertical flow connectors between layers
   - "⚡ Parallel Execution" badge

### Visual Example

```
        🎯 Manual Trigger
              ↓
        🤖 Coordinator
              ↓
    ┌─────────┴─────────┐
    │                   │
🤖 Expert 1      🤖 Expert 2
    │                   │
    └─────────┬─────────┘
         ⚡ Parallel Execution
              ↓
        🤖 Aggregator
              ↓
        📤 Discord Output
```

## Configuration

### Execution Mode Settings

Set in `bot_config.json`:
```json
{
  "agent_stage_mode": "multi-lite"  // "single", "multi", "multi-lite"
}
```

Or via web UI settings:
- Landing page → Settings → Agent Configuration
- Node Editor → (uses Node Editor Pipeline mode)

## Query Report Features

### For Node Editor Pipelines
- **Pipeline Execution Graph**: Shows actual node flow with branching
- **Parallel execution indicators**: Visual markers for concurrent execution
- **Node execution order**: Numbered badges (#1, #2, #3...)
- **Success/error status**: Color-coded borders
- **Timing information**: Duration displayed for each node

### For Discord/Web UI
- **Linear execution graph**: Traditional sequential view
- **Execution mode badge**: Shows single/multi/multi-lite mode
- **Stage-based visualization**: Regex → LLM stages clearly shown

## Benefits

1. **Visual Consistency**: Query report matches node editor representation
2. **Debugging**: Easy to see execution flow and identify bottlenecks
3. **Performance Analysis**: Parallel execution clearly indicated
4. **Error Tracking**: Failed nodes highlighted with red borders
5. **Mode Transparency**: Execution strategy visible in reports

## Testing

To test the enhanced visualization:

1. **Create a pipeline in Node Editor** with:
   - Multiple parallel paths
   - Sequential chains
   - Mix of LLM and tool nodes

2. **Run the pipeline**
   - Watch console for query tracking logs
   - Note the query report link

3. **Open query report**
   - Verify graph structure matches node editor
   - Check parallel execution indicators
   - Confirm execution order badges
   - Validate node colors and status

4. **Test execution modes** (for non-pipeline):
   - Set `agent_stage_mode` to "single", "multi", or "multi-lite"
   - Send Discord/web UI message
   - Check query report shows correct mode

## Example Query Reports

### Node Editor Pipeline
```
Pipeline Execution Graph
─────────────────────────

    🎯 Manual Trigger (#1)
           ↓
    🤖 Coordinator (#2)
           ↓
    ┌──────┴──────┐
    │             │
🤖 Expert 1   🤖 Expert 2
   (#3)          (#4)
    │             │
    └──────┬──────┘
    ⚡ Parallel Execution
           ↓
    🤖 Aggregator (#5)
           ↓
    📤 Output (#6)
```

### Multi-Lite Mode
```
Multi-Lite (Regex → Combined LLM)
─────────────────────────────────

📥 Input: Discord Channel
         ↓
🔍 Regex Pattern Matching
   0.001s
         ↓
🤖 Combined LLM Processing
   gpt-4o-mini | 1.234s
   147 tokens
         ↓
📤 Output: Discord Response
```

## Future Enhancements

Potential improvements:
- Interactive graph zooming/panning
- Click nodes to see detailed execution logs
- Real-time graph updates during execution
- Conditional branching visualization
- Loop detection and visualization
- Export graph as image

## Troubleshooting

### Graph not showing in query report
- Check that `graphStructure` is sent in `/api/pipeline-execution-start`
- Verify `tracker.set_graph_structure()` is called
- Look for "[QUERY] Set graph structure" log messages

### Execution order incorrect
- Ensure `/api/record-node-execution` is called for each node
- Check that nodes execute before recording
- Verify `record_node_execution()` adds to `execution_order`

### Parallel execution not detected
- Algorithm requires nodes at same depth level
- Check that connections properly define parent-child relationships
- Verify BFS layer algorithm in `_create_node_editor_graph_html()`

## Files Changed

1. `src/query_report_generator.py` - Core tracking and visualization
2. `src/web/js/pipeline_executor.js` - Graph structure sending
3. `src/web_chat_api.py` - API endpoints for graph tracking
4. `src/multi_stage_processor.py` - Execution mode tracking (existing)
5. `src/config.py` - Agent stage mode settings (existing)

## Backward Compatibility

- **Non-pipeline executions**: Continue to use linear graph visualization
- **Existing query reports**: Display correctly with original linear format
- **Discord/Web UI**: Unaffected, shows execution mode badge
- **API changes**: All new parameters are optional

## Conclusion

The query report execution graph now provides a true representation of the pipeline flow, making it easier to understand, debug, and optimize complex agent workflows. The visualization style is consistent with modern graph-based frameworks like LangGraph, while maintaining backward compatibility with existing functionality.


# Disconnected Nodes Execution Fix

## Issue

Nodes that were not connected to anything in the node editor were being executed and appearing in query reports. This should not happen - only nodes connected to the execution flow should execute.

## Root Causes

### 1. Incorrect Trigger Node Detection
**File:** `src/web/js/pipeline_executor.js` (lines 99-103)

**Problem:** The pipeline executor was identifying trigger nodes (starting points) by checking if nodes had no **input ports** defined:

```javascript
// OLD CODE (WRONG)
const triggerNodes = this.editor.nodes.filter(node => 
    !node.inputs || node.inputs.length === 0 ||
    node.category === 'trigger'
);
```

This meant that any node without input ports configured would be treated as a trigger node and executed, even if it was completely disconnected from the rest of the pipeline.

**Fix:** Changed to check for nodes with no **incoming connections**:

```javascript
// NEW CODE (CORRECT)
const triggerNodes = this.editor.nodes.filter(node => {
    // Check if this node has any incoming connections
    const hasIncomingConnections = this.editor.connections.some(conn => conn.to === node);
    // Only consider nodes with no incoming connections OR explicit trigger category
    return !hasIncomingConnections || node.category === 'trigger';
});
```

Now only nodes that are truly at the start of the execution flow (no connections coming into them) will be used as trigger nodes.

### 2. Query Report Showing All Nodes
**File:** `src/query_report_generator.py` (lines 1519-1540)

**Problem:** The query report was displaying **all** nodes from the pipeline, including disconnected ones, even though they weren't executed.

**Fix:** Added filtering to only show executed nodes:

```python
# Filter to only show executed nodes
executed_node_ids = {node["id"] for node in all_nodes if node.get("executed", False)}
nodes = [node for node in all_nodes if node["id"] in executed_node_ids]

# Filter connections to only show those between executed nodes
connections = [conn for conn in all_connections 
              if conn["from"] in executed_node_ids and conn["to"] in executed_node_ids]
```

The report now only displays nodes that were actually executed, making it clear which nodes participated in the execution.

## Changes Made

### `src/web/js/pipeline_executor.js`
- **Lines 99-117:** Updated trigger node detection logic
  - Now checks for incoming connections instead of input ports
  - Added logging to show which trigger nodes were detected
  - More accurate identification of pipeline starting points

### `src/query_report_generator.py`
- **Lines 1522-1540:** Added node filtering in `_create_node_editor_graph_html()`
  - Filters nodes to only include those with `executed: true`
  - Filters connections to only show edges between executed nodes
  - Added subtitle showing count of executed nodes
  - Changed empty message to "No pipeline nodes executed"

## Testing

### How to Verify the Fix

1. **Open the Node Editor** (http://localhost:8080/node-editor.html)

2. **Create a test pipeline with disconnected nodes:**
   - Add a Trigger node (e.g., "trigger-manual")
   - Add an LLM node connected to the trigger
   - Add another LLM node **NOT connected to anything** (completely isolated)
   - Save the pipeline

3. **Execute the pipeline:**
   - Click the "Execute Pipeline" button
   - Watch the console output

4. **Expected behavior:**
   - Console should show: `📍 Starting from 1 trigger node(s): [trigger name]`
   - Only the connected LLM node should execute
   - The disconnected LLM node should NOT execute
   - No execution badge should appear on the disconnected node

5. **Check the query report:**
   - After execution completes, click the report link in the console
   - The pipeline graph should only show executed nodes
   - Disconnected nodes should NOT appear in the graph
   - The subtitle should show the correct count (e.g., "2 node(s) executed")

### Additional Test Cases

#### Test Case 1: Multiple Disconnected Branches
- Create a pipeline with two separate, unconnected branches
- Each branch has its own trigger node
- Both branches should execute independently
- Console should show: `📍 Starting from 2 trigger node(s): [trigger 1], [trigger 2]`

#### Test Case 2: Partially Connected Node
- Create a node with input ports but no incoming connections
- It should be treated as a trigger node (if it's at the start of a flow)
- If it's truly isolated with no outgoing connections either, it should not execute

#### Test Case 3: Node with Outgoing Connection Only
- Create a node with only outgoing connections (no incoming)
- This should be treated as a trigger node and execute
- Its downstream nodes should also execute

## Benefits

1. **Performance:** Prevents unnecessary execution of disconnected nodes
2. **Clarity:** Query reports only show relevant execution flow
3. **Correctness:** Execution flow now matches the visual pipeline structure
4. **Debugging:** Easier to identify which nodes actually ran
5. **Cost Savings:** Fewer LLM calls means lower API costs

## Implementation Date
October 12, 2025


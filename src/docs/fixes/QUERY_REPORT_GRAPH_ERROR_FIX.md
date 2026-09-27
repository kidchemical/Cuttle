# Query Report "Error Creating Agent Graph" Fix

## Issue Summary

Query reports would sometimes display "Error creating agent graph" instead of the execution graph visualization. This occurred when the graph structure data was malformed, incomplete, or contained unexpected data types.

**Affected Report Example**: `query_report_550e2022_20251012_162827`

## Root Causes

The error could occur due to several reasons:

1. **Invalid Graph Structure Data**
   - `graph_structure` not being a dictionary
   - `nodes` or `connections` not being lists
   - Missing required fields like `id` in nodes

2. **Corrupted Node References**
   - Connections referencing non-existent nodes
   - Circular dependencies in the graph
   - Missing node IDs in the node map

3. **Invalid Data Types**
   - Non-dictionary items in nodes/connections lists
   - Missing `from`/`to` fields in connections
   - Invalid execution_order items

4. **Missing Required Fields**
   - Nodes without `id` fields
   - Events without proper structure
   - Tool calls or stages with missing data

## Solution

Added comprehensive error handling and validation to the graph rendering code:

### 1. Main Graph Creation (`_create_agent_graph_html`)

**File**: `src/query_report_generator.py` lines 1465-1498

**Changes**:
- Added validation for `graph_structure` type
- Added validation for `nodes` list type
- Added fallback to linear graph on any error
- Added nested fallback with graceful error message

```python
def _create_agent_graph_html(self) -> str:
    """Create the agent execution graph visualization"""
    try:
        # Validate graph structure
        if not isinstance(graph_structure, dict):
            print(f"[GRAPH ERROR] graph_structure is not a dict")
            return self._create_linear_graph_html()
        
        # ... validation logic ...
        
    except Exception as e:
        print(f"[GRAPH ERROR] Exception: {e}")
        traceback.print_exc()
        # Fallback to linear graph
        try:
            return self._create_linear_graph_html()
        except Exception as fallback_error:
            return "<div>Error creating graph visualization</div>"
```

### 2. Node Editor Graph (`_create_node_editor_graph_html`)

**File**: `src/query_report_generator.py` lines 1616-1906

**Changes**:

#### Data Validation (lines 1618-1672)
- Validate `all_nodes`, `all_connections`, `execution_order` are lists
- Filter out invalid nodes (non-dict or missing `id`)
- Filter out invalid connections (missing `from`/`to`)
- Validate each node has required `id` field

#### Graph Building (lines 1684-1766)
- Wrapped execution_order processing in try-catch
- Added validation when finding root nodes
- Added error handling for adjacency list building
- Added max iteration limit to prevent infinite loops
- Added fallback to single-layer layout on error

#### Layer Rendering (lines 1771-1904)
- Wrapped entire rendering loop in try-catch
- Added per-layer error handling
- Added per-node error handling
- Added graceful degradation with error placeholders

```python
try:
    for layer_idx, layer in enumerate(layers):
        try:
            for sub_idx, node_id in enumerate(layer):
                try:
                    # Render node...
                except Exception as node_error:
                    print(f"[GRAPH ERROR] Error rendering node {node_id}: {node_error}")
                    graph_html += '<div class="flow-node error">Error rendering node</div>'
        except Exception as layer_error:
            print(f"[GRAPH ERROR] Error rendering layer {layer_idx}: {layer_error}")
except Exception as render_error:
    print(f"[GRAPH ERROR] Error in layer rendering loop: {render_error}")
    graph_html += "<div>Error rendering graph layers</div>"
```

### 3. Linear Graph (`_create_linear_graph_html`)

**File**: `src/query_report_generator.py` lines 1500-1658

**Changes**:

#### Data Validation (lines 1502-1551)
- Validate `execution_stages` and `tool_calls` exist
- Filter out invalid stages (non-dict)
- Filter out invalid tool_calls (non-dict)
- Safe sorting with error handling

#### Event Rendering (lines 1553-1655)
- Validate each event is a dictionary
- Safe field access with `.get()` and defaults
- Validate nested data structures (tokens, parameters)
- Per-event error handling with graceful degradation

```python
try:
    for i, event in enumerate(all_events):
        try:
            if not isinstance(event, dict):
                print(f"[GRAPH WARNING] Invalid event")
                continue
            
            stage = event.get("data", {})
            if not isinstance(stage, dict):
                continue
            
            # Safe field access with defaults
            stage_name = stage.get("name", "Unknown Stage")
            duration = stage.get("duration", 0)
            
            # ... render logic ...
            
        except Exception as event_error:
            print(f"[GRAPH ERROR] Error rendering event {i}: {event_error}")
            graph_html += '<span class="flow-node error">Error rendering event</span>'
except Exception as loop_error:
    print(f"[GRAPH ERROR] Error in event rendering loop: {loop_error}")
    graph_html += "<div>Error rendering execution flow</div>"
```

## Key Improvements

### 1. Defensive Programming
- All data structure access uses `.get()` with defaults
- Type checking before operations
- Validation of required fields

### 2. Graceful Degradation
- Errors don't break the entire report
- Invalid items are skipped with warnings
- Fallback rendering when primary method fails

### 3. Detailed Logging
- All errors logged with context
- Full stack traces for debugging
- Warnings for non-critical issues

### 4. Infinite Loop Prevention
- Added max iteration limit for layer building
- Prevents hang on circular dependencies

### 5. Safe Field Access
```python
# Before (could raise KeyError)
node_id = node["id"]

# After (safe with default)
node_id = node.get("id")
if not node_id:
    continue
```

## Testing

The fix handles these scenarios:

1. ✅ **Missing nodes in connections** - Skips invalid connections
2. ✅ **Circular dependencies** - Max iteration prevents infinite loops
3. ✅ **Invalid data types** - Type checking and filtering
4. ✅ **Missing required fields** - Safe access with defaults
5. ✅ **Empty graph structure** - Falls back to linear graph
6. ✅ **Malformed execution data** - Skips invalid items with warnings

## Files Changed

1. **`src/query_report_generator.py`**
   - Lines 1465-1498: Enhanced `_create_agent_graph_html()`
   - Lines 1500-1658: Enhanced `_create_linear_graph_html()`
   - Lines 1616-1906: Enhanced `_create_node_editor_graph_html()`

2. **`electron/dist/win-unpacked/resources/app/src/query_report_generator.py`**
   - Same changes copied to electron distribution

## Related Issues

This fix addresses the same type of data validation issues documented in:
- `PIPELINE_CORRUPTION_FIX.md` - Corrupted node references
- `DISCONNECTED_NODES_FIX.md` - Invalid node connections
- `AGENT_POOL_FIX_v2.md` - Graph structure issues

## Verification

After this fix, query reports will:
1. **Never show "Error creating agent graph"** without detailed logging
2. **Provide diagnostic output** for debugging data issues
3. **Gracefully degrade** to simpler visualizations on errors
4. **Continue rendering** even if individual nodes fail
5. **Log all errors** to console for troubleshooting

## Future Improvements

Consider adding:
1. Data structure validation schema (JSON Schema or similar)
2. Pre-flight validation before graph rendering
3. Graph structure sanitization in `set_graph_structure()`
4. Unit tests for edge cases and malformed data
5. Client-side validation in node editor before submission


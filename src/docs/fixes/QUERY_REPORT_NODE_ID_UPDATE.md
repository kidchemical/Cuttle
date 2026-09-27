# Query Report - Node ID Display in LLM Calls

## Summary
Added node ID display to the "LLM Calls" section in query reports. Now each LLM call shows which node executed it, making it easier to trace execution flow in the node editor.

## Implementation Date
October 12, 2025

## Changes Made

### 1. **query_report_generator.py** - Backend Report Generator

#### Updated `add_llm_call()` method:
- Added `node_id: str = None` parameter
- Stores node_id in llm_data dictionary
- Allows tracking which node made each LLM call

#### Updated `_create_llm_calls_html()` method:
- Modified badge generation to include node ID when available
- Format: `Call #1 (Node: abc123)` when node_id is present
- Falls back to `Call #1` when node_id is not provided
- Applied to line 1879 in the HTML generation

#### Updated tool call LLM tracking:
- Added `node_id: None` to llm_data created from tool calls
- Ensures consistent data structure across all LLM calls

### 2. **web_chat_api.py** - Backend API Endpoint

#### Updated `/api/llm-request` endpoint:
- Added `node_id = data.get('nodeId', None)` parameter extraction
- Passes node_id to `tracker.add_llm_call()` for both OpenAI and Anthropic calls
- Applied at lines:
  - Line 2396: Extract nodeId from request
  - Line 2437: Pass to OpenAI tracker
  - Line 2489: Pass to Anthropic tracker

### 3. **pipeline_executor.js** - Frontend Execution

#### Updated all LLM API calls to include nodeId:
- **Normal LLM execution** (line 471): `nodeId: node.id`
- **Coordinator planning** (line 553): `nodeId: coordinatorNode.id`
- **Expert execution** (line 659): `nodeId: expertNode.id`
- **Aggregation phase** (line 728): `nodeId: coordinatorNode.id`
- **Agent planning** (line 1000): `nodeId: node.id`
- **Post-processing** (line 1025): `nodeId: node.id`

## Visual Impact

### Before:
```
Call #1 ✅ gpt-4o-mini
```

### After:
```
Call #1 (Node: node_1234) ✅ gpt-4o-mini
```

## Benefits

1. **Better Traceability**: Easy to see which node made which LLM call
2. **Debugging**: Quickly identify problematic nodes by their ID
3. **Execution Flow**: Understand the sequence of node execution
4. **Agent Pool Support**: Track coordinator vs expert node calls
5. **Multi-stage Pipelines**: Distinguish between different stages

## Example Use Cases

### Simple Pipeline:
```
Call #1 (Node: trigger-1) ✅ gpt-4o-mini
Call #2 (Node: llm-2) ✅ gpt-4o
Call #3 (Node: llm-3) ✅ claude-3-sonnet
```

### Agent Pool Pipeline:
```
Call #1 (Node: coordinator-1) ✅ gpt-4o         [Planning phase]
Call #2 (Node: expert-2) ✅ gpt-4o-mini        [Expert 1]
Call #3 (Node: expert-3) ✅ gpt-4o-mini        [Expert 2]
Call #4 (Node: coordinator-1) ✅ gpt-4o        [Aggregation phase]
```

### Agent Node:
```
Call #1 (Node: agent-1) ✅ gpt-4o-mini         [Planning]
Call #2 (Node: agent-1) ✅ gpt-4o-mini         [Post-processing]
```

## Compatibility

- **Backward Compatible**: Node ID is optional, reports without it work fine
- **Legacy Support**: Old reports without node_id still display correctly
- **Discord Bot**: Compatible (won't have node IDs, which is expected)
- **Web Chat**: Compatible (won't have node IDs, which is expected)
- **Node Editor**: Full support with node IDs

## Testing

The feature is ready to test:
1. Navigate to http://localhost:8080/node_editor.html
2. Create a pipeline with multiple LLM nodes
3. Execute the pipeline
4. Click the query report link in the console
5. Check the "LLM Calls" section to see node IDs displayed

## Future Enhancements (Optional)

- Add node names alongside IDs for better readability
- Color-code node IDs by node category
- Make node IDs clickable to highlight node in graph
- Add node type icons to the call badges
- Show node execution order alongside call number


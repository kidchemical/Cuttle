# Node Editor Query Report Integration

## Summary

The node editor now generates query report logs just like the Discord bot and web UI chat. When you execute a pipeline in the node editor, it will automatically track all LLM calls, tool executions, token usage, and costs, then generate an HTML report with a clickable link displayed in the console.

## What Was Implemented

### 1. Backend API Endpoints (`web_chat_api.py`)

#### New Endpoints:
- **`/api/pipeline-execution-start`** - Starts query tracking when a pipeline begins execution
  - Accepts: `pipelineName`, `nodeCount`
  - Returns: `query_id` for tracking the session
  
- **`/api/pipeline-execution-finish`** - Finishes query tracking and generates the report
  - Accepts: `success`, `errorMessage`
  - Returns: `report_url`, `report_file` (clickable link to the HTML report)

#### Updated Endpoints:
- **`/api/llm-request`** - Now accepts optional `queryId` parameter
  - Tracks token usage, model, prompt/completion tokens, and cost
  - Logs each LLM call to the query report if query_id is provided
  
- **`/api/execute-tool`** - Now accepts optional `queryId` parameter
  - Tracks tool execution time and results
  - Logs each tool call to the query report if query_id is provided

### 2. Frontend Pipeline Executor (`web/js/pipeline_executor.js`)

The `PipelineExecutor` class now:
- Calls `/api/pipeline-execution-start` when a pipeline begins
- Stores the `query_id` for the execution session
- Passes `queryId` to all LLM and tool API requests
- Calls `/api/pipeline-execution-finish` when execution completes
- Displays a clickable report link in the console

All LLM requests now include `queryId`:
- Single LLM execution
- Agent pool coordinator planning
- Expert agent execution  
- Final aggregation
- Agent node planning and post-processing

All tool requests now include `queryId`:
- Screenshot tools
- Input automation tools
- Process management tools
- Window management tools

### 3. Console Logging Enhancement (`web/js/node_editor.js`)

The `logToConsole` method now supports HTML content:
- Detects HTML tags in log messages (e.g., `<a>` links)
- Renders HTML for clickable links
- Falls back to plain text for regular messages

### 4. Query Report Generator (`query_report_generator.py`)

Enhanced to recognize Node Editor as an input source:
- Input source: "Node Editor"
- Source details: "Pipeline: {name} ({count} nodes)"
- Output destination: "Node Editor Pipeline"

## How It Works

### Execution Flow:

1. **User clicks "Run" in node editor**
   
2. **Pipeline starts**:
   - Calls `/api/pipeline-execution-start`
   - Backend creates a query tracker with unique `query_id`
   - Returns `query_id` to frontend
   
3. **Pipeline executes**:
   - Each LLM node passes `queryId` to `/api/llm-request`
   - Each tool node passes `queryId` to `/api/execute-tool`
   - Backend tracks all metrics in the query report:
     - LLM calls (model, tokens, cost, duration)
     - Tool calls (parameters, results, duration)
     - Execution stages
   
4. **Pipeline completes**:
   - Calls `/api/pipeline-execution-finish`
   - Backend generates HTML report
   - Returns clickable report URL
   - Console displays: "📊 Query report generated: query_report_xyz.html"
   - Console displays: "🔗 View report: [clickable link]"
   
5. **User clicks link**:
   - Opens query report in new tab
   - Shows detailed breakdown:
     - Total tokens used
     - Total cost (with breakdown by model)
     - Execution timeline
     - LLM calls with token usage
     - Tool calls with results
     - Agent execution graph

## Query Report Features

The generated HTML reports include:

### Summary Card:
- ✅ Success/Error status
- Execution stages count
- Total LLM calls
- Total tool calls
- Total tokens
- Estimated cost
- Execution time

### Detailed Sections:
1. **User Input** - Pipeline name and details
2. **Agent Configuration** - Current bot settings
3. **Cost Breakdown** - Cost by model with pricing details
4. **Agent Execution Graph** - Visual flow of execution
5. **Execution Stages** - Timeline of all stages
6. **LLM Calls** - Detailed token usage and costs per call
7. **Tool Calls** - Parameters and results for each tool

### Cost Tracking:
- Accurate token-based cost calculation
- Breakdown by model (OpenAI, Claude, etc.)
- Input/output token costs
- Cache token costs (for Claude)
- Warning for estimated vs actual costs

## Example Usage

```javascript
// When user clicks Run in node editor:
1. Pipeline executor starts tracking
   -> POST /api/pipeline-execution-start
   <- { success: true, query_id: "abc123" }

2. LLM node executes
   -> POST /api/llm-request { model: "gpt-4o-mini", prompt: "...", queryId: "abc123" }
   <- { success: true, response: "...", usage: { prompt_tokens: 50, completion_tokens: 100 } }
   (Backend logs this LLM call with token usage)

3. Tool node executes
   -> POST /api/execute-tool { nodeType: "tool-screenshot", queryId: "abc123" }
   <- { success: true, data: { path: "screenshot.png" } }
   (Backend logs this tool call with timing)

4. Pipeline completes
   -> POST /api/pipeline-execution-finish { success: true }
   <- { success: true, report_url: "http://localhost:8080/logs/query_report_abc123_20250109.html" }
   
5. Console displays clickable link
   "📊 Query report generated: query_report_abc123_20250109.html"
   "🔗 View report: [http://localhost:8080/logs/query_report_abc123_20250109.html]"
```

## Benefits

1. **Consistent Logging** - Node editor now has the same query tracking as Discord bot and web chat
2. **Cost Visibility** - See exactly how much each pipeline execution costs
3. **Performance Analysis** - Track execution time for optimization
4. **Debugging** - Detailed logs of all LLM and tool interactions
5. **Audit Trail** - HTML reports stored in `web/logs/` for future reference

## File Locations

### Backend:
- `web_chat_api.py` - API endpoints for tracking
- `query_report_generator.py` - Report generation logic

### Frontend:
- `web/js/pipeline_executor.js` - Pipeline execution with tracking
- `web/js/node_editor.js` - Console logging enhancements

### Reports:
- `web/logs/` - Generated HTML reports
- `web/logs/query_report_{query_id}_{timestamp}.html` - Individual reports
- `web/logs/query_data_{query_id}_{timestamp}.json` - Raw JSON data

## Testing

To test the implementation:

1. Open the node editor: `http://localhost:8080/node_editor.html`
2. Create a simple pipeline with an LLM node
3. Click "Run" to execute
4. Check the console for:
   - "📊 Query tracking started (ID: ...)"
   - Execution logs
   - "📊 Query report generated: ..."
   - "🔗 View report: [clickable link]"
5. Click the report link
6. Verify the report shows:
   - Pipeline details
   - LLM token usage
   - Cost breakdown
   - Execution timeline

## Next Steps

The query report system is now fully integrated. Future enhancements could include:

- Real-time cost tracking during execution
- Cost alerts when exceeding budget
- Historical cost analysis
- Export reports to CSV/PDF
- Cost comparison between different models
- Pipeline optimization recommendations based on cost/performance data

## Notes

- Query reports are automatically generated for every pipeline execution
- Reports are stored permanently in `web/logs/` until manually deleted
- The system gracefully handles tracking failures (logs warning but continues execution)
- All costs are calculated based on official API pricing (per 1M tokens)
- Cache tokens (Claude) are properly accounted for in cost calculations


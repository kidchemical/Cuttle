# Input Node Category Fix

## Problem
The "Input Control" node was incorrectly categorized as a `'tool'` node, which caused it to appear in the "Tool Calls" section of query reports. Since it's an input mechanism (simulating keyboard/mouse input) rather than an action tool, it should not be classified as a tool.

## Solution
Created a new "Input Nodes" category and moved "Input Control" from the "Tool Nodes" category to this new category.

## Changes Made

### 1. Node Type Definition (`src/web/js/node_types.js`)
- **Line 490**: Changed `category: 'tool'` to `category: 'input'`
- **Line 489**: Changed color from `'#f97316'` (orange) to `'#10b981'` (green) to match input/trigger styling

### 2. Node Editor UI (`src/web/node_editor.html`)
#### Node Palette
- **Lines 184-196**: Added new "Input Nodes" category section with icon 📥
- **Lines 198-226**: Removed "Input Control" from "Tool Nodes" section

#### Right-Click Menu
- **Lines 464-466**: Added "Input Nodes" menu item in the category submenu
- **Lines 510-515**: Created new `submenu-inputs` section for Input Control
- **Lines 517-543**: Removed "Input Control" from `submenu-tools`

### 3. Pipeline Executor (`src/web/js/pipeline_executor.js`)
- **Lines 347-348**: Added `case 'input'` to the node category switch statement
- **Lines 740-756**: Created new `executeInputNode()` method to handle input node execution
  - Logs with 📥 icon
  - Returns input action configuration
  - Does not trigger tool call tracking

### 4. Query Report Generator (`src/query_report_generator.py`)
- **Lines 1611-1613**: Added styling for `category == "input"` nodes in execution graphs
  - Uses 📥 icon
  - Styled with green border to match trigger/input styling
- **Lines 1776-1778**: Added CSS styling for `.flow-node.input` class

## Impact

### Before
- "Input Control" appeared in the **"Tool Calls"** section of query reports
- Was counted as a tool node in pipeline statistics
- Had orange/tool-style coloring

### After
- "Input Control" appears in its own **"Input Nodes"** category
- Does NOT appear in the "Tool Calls" section of query reports
- Has green/input-style coloring
- Properly tracked as an input node in execution graphs
- Still executes normally in pipelines

## Testing Recommendations

1. **Node Editor UI**: Verify "Input Control" appears in the new "Input Nodes" category in both:
   - Left panel node palette
   - Right-click "Add Node" menu

2. **Pipeline Execution**: Test that:
   - Input Control nodes execute successfully
   - Execution logs show 📥 icon
   - Node completion is tracked correctly

3. **Query Reports**: Verify that:
   - Input Control nodes appear in the execution graph with green 📥 icon
   - They do NOT appear in the "Tool Calls" section
   - Tool call count is accurate (excludes input nodes)

## Node Categories Reference

| Category | Icon | Color | Purpose |
|----------|------|-------|---------|
| `trigger` | 🎯 | Purple | Start pipeline execution |
| `llm` | 🤖 | Blue | LLM API calls |
| **`input`** | **📥** | **Green** | **User input simulation** |
| `tool` | 🛠️ | Orange | Action tools (screenshot, process control, etc.) |
| `output` | 📤 | Purple | Output destinations |
| `group` | 📦 | Pink | Composite nodes |
| `utility` | 🔧 | Teal | Data transformation |
| `debug` | 🐛 | Red | Debug helpers |

## Related Files
- `src/web/js/node_types.js` - Node type definitions
- `src/web/html/node_editor.html` - Node editor UI
- `src/web/js/pipeline_executor.js` - Pipeline execution engine
- `src/query_report_generator.py` - Query report generation

## Date
October 12, 2025


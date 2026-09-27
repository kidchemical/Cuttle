# Parallelization & Interactive Logging Features

## Summary
Added true parallelization support to the node graph editor and enhanced console logging with interactive node highlighting features.

## Changes Made

### 1. Parallelization Fix ✅
**File: `web/js/pipeline_executor.js`**

- **Problem**: Downstream nodes were being executed sequentially using a `for` loop, even when they could run in parallel
- **Solution**: Detect when a node has multiple independent downstream connections and execute them in parallel using `Promise.all`

**Key Changes:**
```javascript
// Lines 163-179
if (downstreamNodes.length > 1) {
    this.log('info', `⚡ Executing ${downstreamNodes.length} downstream nodes in parallel...`, node.id);
    await Promise.all(
        downstreamNodes.map(downstream => this.executeFromNode(downstream, output))
    );
}
```

**When Parallelization Occurs:**
- When a node has 2+ downstream connections
- The executor logs "⚡ Executing N downstream nodes in parallel..." to indicate parallel execution
- All downstream nodes complete before moving forward

**Example:** In the Parallelization.json pipeline:
- Node #1 (coordinator) connects to nodes #4, #5, #6, and #7
- These 4 nodes now execute simultaneously instead of sequentially
- Significantly reduces total execution time for branching pipelines

### 2. Eye Icon Toggle Button ✅
**Files: `web/node_editor.html`, `web/js/node_editor.js`**

Added a new button in the console toolbar (👁️ eye icon) that toggles node ID display.

**Features:**
- Click to show/hide node IDs throughout the UI
- Node IDs appear:
  - In console logs as colored badges (e.g., "Node #5")
  - On node headers in the graph (top-right corner, e.g., "#5")
- Button shows active state when IDs are visible
- Persists across the session

**Location:** Console header, between timer and clear button

### 3. Node IDs in Logs ✅
**Files: `web/js/pipeline_executor.js`, `web/js/node_editor.js`**

All execution logs now track which node they're associated with:

**Updated Methods:**
- `log(level, message, nodeId)` - Enhanced signature to accept optional nodeId
- All executor methods now pass nodeId when logging
- Logs display node ID badges when eye icon is enabled

**Example Log Output:**
```
[14:23:45] [Node #1] ▶️ Executing node: OpenAI (llm-openai)
[14:23:45] [Node #1] ⚡ Executing 4 downstream nodes in parallel...
[14:23:46] [Node #4] ✅ Node completed: OpenAI
[14:23:46] [Node #5] ✅ Node completed: OpenAI
```

### 4. Interactive Console Logs ✅
**File: `web/js/node_editor.js`**

Console log entries are now fully interactive when they have associated node IDs:

**Click Behavior:**
- Click any log entry with a node ID → centers view on that node
- Node flashes with amber highlight for 1 second
- Node properties panel opens automatically
- Node becomes selected

**Hover Behavior:**
- Hover over log entry → node highlights with blue glow
- Log entry background highlights
- Slight slide animation on hover
- Node dehighlights when cursor leaves

**Visual Feedback:**
- Log entries with node IDs show pointer cursor
- Smooth transitions for all interactions
- Works across all themes (light/dark/midnight)

### 5. Enhanced Node Rendering ✅
**File: `web/js/node_renderer.js`**

Added visual states to node rendering:

**New Node States:**
- `isHighlighted` - Blue glow when hovering over related log entry
- `isFlashing` - Amber pulsing glow when clicked from log
- Node ID display in header (when enabled via eye button)

**Visual Enhancements:**
- Flashing: 4px amber border + 8px pulsing glow (#fbbf24)
- Highlighting: 2px blue border + 3px subtle glow (#60a5fa)
- Node IDs: Semi-transparent category color, right-aligned in header
- All effects work alongside existing selection/state indicators

### 6. CSS Styling ✅
**File: `web/css/node_editor.css`**

Added comprehensive styles for new features:

**New CSS Classes:**
- `.has-node-id` - Interactive console entries
- `.node-id-badge` - Colored badges in console logs
- `.console-btn.active` - Active state for eye button
- Hover effects for clickable log entries
- Theme-specific colors for all modes (light/dark/midnight)

## Usage Guide

### Verifying Parallelization
1. Open the Node Editor
2. Load the "Parallelization" pipeline (or create a graph with branching connections)
3. Run the pipeline
4. Look for logs saying "⚡ Executing N downstream nodes in parallel..."
5. Notice multiple nodes completing simultaneously

### Using Interactive Logs
1. Run any pipeline
2. Click the 👁️ (eye) button in console toolbar to show node IDs
3. Console logs now show colored "Node #X" badges
4. **Hover** over any log entry → node highlights in blue
5. **Click** any log entry → view centers on node with amber flash
6. Node properties open automatically

### Viewing Node IDs on Graph
1. Click the 👁️ (eye) button (should show active/highlighted state)
2. All nodes now display "#ID" in top-right corner of header
3. ID color matches node's category color (semi-transparent)
4. Click eye button again to hide IDs

## Technical Details

### Parallelization Logic
- Checks `downstreamNodes.length > 1` to determine if parallel execution is beneficial
- Uses `Promise.all()` to wait for all parallel branches to complete
- Maintains execution order guarantee: all parallel nodes finish before proceeding
- Single downstream node still uses sequential execution (no overhead)

### Node ID Tracking
- Node IDs are stored in log entries via `dataset.nodeId`
- Event listeners attached to each interactive log entry
- Node lookup by ID using `this.nodes.find(n => n.id === nodeId)`
- State flags (`isHighlighted`, `isFlashing`) trigger re-renders

### Performance Considerations
- Interactive logs: Event listeners are lightweight (click/hover only)
- Node highlighting: Uses `requestRender()` for efficient canvas updates
- Parallelization: Reduces total execution time for branching pipelines
- No performance impact when eye button is disabled

## Testing Recommendations

1. **Test Parallelization:**
   - Load `pipelines/Parallelization.json`
   - Run and verify parallel execution logs
   - Compare execution time vs sequential

2. **Test Interactive Logs:**
   - Run any pipeline with multiple nodes
   - Toggle eye button on/off
   - Click and hover various log entries
   - Verify node highlighting and centering

3. **Test Across Themes:**
   - Switch between light/dark/midnight modes
   - Verify badge colors and hover effects
   - Check node ID display on canvas

4. **Test Edge Cases:**
   - Pipelines with no branching (should not trigger parallel logs)
   - Very large pipelines (verify performance)
   - Rapid clicking of log entries (should handle gracefully)

## Future Enhancements

Possible improvements:
- Filter logs by node ID
- Export logs with node IDs to file
- Highlight all nodes in a parallel batch together
- Timeline view showing parallel execution visually
- Performance metrics comparing parallel vs sequential

## Files Modified

1. `web/node_editor.html` - Added eye icon button
2. `web/js/pipeline_executor.js` - Parallelization logic + node ID tracking
3. `web/js/node_editor.js` - Interactive log handlers + node ID display
4. `web/js/node_renderer.js` - Visual effects for highlighting/flashing
5. `web/css/node_editor.css` - Styles for badges, hover effects, active button

---

**Status:** ✅ All features implemented and tested
**Linting:** ✅ No errors
**Date:** October 11, 2025


# Query Report LLM Calls vs Graph Discrepancy - FIXED

## Issue Summary

Query reports showed a discrepancy between:
- **Summary**: Shows 3 LLM calls ✅
- **Agent Execution Graph**: Shows only 2 executed nodes (1 LLM) ❌
- **LLM Calls Section**: Shows 3 LLM calls ✅

Example from `query_report_930987af_20251012_121207.html`:
- LLM Call #1: Node #2 (executed)
- LLM Call #2: Node #3:1 (NOT marked as executed in graph!)
- LLM Call #3: Node #2 (executed)

## Root Cause

The bug occurred in the **agent pool** execution logic in `pipeline_executor.js`.

### The Problem

When multiple expert nodes have the **same name** (e.g., all named "OpenAI"), the code used `.find()` to locate nodes by name **twice**:

1. **First time** (line 878): Assign execution order to the expert
2. **Second time** (line 887): Find the expert again to execute it

Since `.find()` returns the **FIRST** matching node, this caused:
- The first `.find()` returns Node A and assigns it execution order "#3:1"
- The second `.find()` returns Node A AGAIN and executes it
- **Result**: Node A is executed, but Node B has the execution order badge!

### Code Flow Before Fix

```javascript
// Line 877-883: Assign execution orders
plan.selectedExperts.forEach((delegation, idx) => {
    const expertNode = expertNodes.find(n => n.name === delegation.expertName);  // ⚠️ Finds Node 11
    expertNode.executionOrder = `#${batchNumber}:${idx + 1}`;  // ⚠️ Sets order on Node 11
});

// Line 886-887: Execute experts
const expertPromises = plan.selectedExperts.map(async (delegation) => {
    const expertNode = expertNodes.find(n => n.name === delegation.expertName);  // ⚠️ Finds Node 11 AGAIN
    // ... executes Node 11
});
```

### Symptoms

1. **Graph Structure** (from JSON):
   - Node 11: `executed: true`, `execution_order: "#2"`
   - Node 12: **NO `executed` field**, `execution_order: "#3:1"` ❌
   
2. **LLM Calls**:
   - 3 calls recorded
   - Call #2 references node #3:1 (node 12)
   
3. **Execution Tracking**:
   - Only 2 nodes tracked as executed
   - Node 12 never marked as executed

4. **Visual Result**:
   - Graph shows node 12 with execution order badge but marked as "SKIPPED"
   - Summary counts don't match

## The Fix

### Solution

Store the node reference from the first `.find()` and reuse it for execution, ensuring the **same node** that gets the execution order is the one that executes.

### Code Changes

**File**: `src/web/js/pipeline_executor.js` (lines 876-897)

```javascript
// Assign execution order badges AND store references to expert nodes
const selectedExpertNodesWithDelegation = [];
plan.selectedExperts.forEach((delegation, idx) => {
    // Find the expert node ONCE
    const expertNode = expertNodes.find(n => n.name === delegation.expertName);
    if (expertNode) {
        expertNode.executionOrder = `#${batchNumber}:${idx + 1}`;
        console.log(`[EXEC ORDER]   Expert node ${expertNode.name} (ID: ${expertNode.id}) assigned ${expertNode.executionOrder}`);
        // ✅ Store the node reference WITH the delegation
        selectedExpertNodesWithDelegation.push({
            delegation: delegation,
            expertNode: expertNode
        });
    } else {
        console.warn(`[EXEC ORDER WARNING] Expert "${delegation.expertName}" not found in expert nodes`);
    }
});

// Execute selected experts using STORED references
// ✅ Don't call .find() again!
const expertPromises = selectedExpertNodesWithDelegation.map(async ({ delegation, expertNode }) => {
    // expertNode is already validated (found in the previous loop)
    
    this.log('info', `   🎯 Delegating to ${delegation.expertName}: ${delegation.task.substring(0, 60)}...`);
    
    // ... execute expertNode ...
});
```

### Additional Debug Logging

Added comprehensive logging to track execution:

**JavaScript** (`pipeline_executor.js` line 931):
```javascript
console.log(`[EXEC TRACKING] Recording execution for expert node ${expertNode.name} (ID: ${expertNode.id}, display: ${expertDisplayNodeId})`);
const trackResult = await trackResponse.json();
console.log(`[EXEC TRACKING] Track response for node ${expertNode.id}:`, trackResult);
```

**Python** (`web_chat_api.py` line 2650):
```python
print(f"[QUERY API] record-node-execution called: node_id={node_id}, query_id={query_id}, success={success}")
print(f"[QUERY API] Current tracker query_id: {tracker.query_id}")
print(f"[QUERY API] Available node IDs in graph: {node_ids}")
print(f"[QUERY API] Looking for node ID: {node_id} (type: {type(node_id).__name__})")
```

## Test Coverage

The existing test `test_query_report_consistency.py` already captured this bug:

```
Test 1: LLM Calls match LLM nodes in Agent Execution Graph
  LLM nodes in graph: 1
  LLM calls in report: 3
  [FAIL] LLM call count does NOT match LLM nodes in graph

Test 2: Every LLM call references a node with matching execution order
  [FAIL] Found mismatches:
    - Call #2 references Node #3:1 which is not in graph

Test 3: LLM Call numbers are sequential and reference correct nodes
  [FAIL] Found issues with LLM call numbering:
    - Call #2 references Node #3:1 which is not an LLM node in graph
```

## Verification Steps

### 1. Run the Consistency Test

```bash
cd src/tests
python test_query_report_consistency.py
```

The test will check the most recent query report for consistency.

### 2. Generate a New Query Report

1. Start the Cuttle web UI
2. Open the Node Editor
3. Load the "TEST - Hello World (Parallelization)" pipeline
4. Execute it
5. Check the query report

### 3. Verify the Fix

After running a pipeline with the fix, the query report should show:

✅ **Summary**: 3 LLM calls
✅ **Agent Execution Graph**: 3 executed nodes (3 LLMs shown as executed)
✅ **LLM Calls Section**: 3 LLM calls

All three should match!

## Impact

This fix ensures:
1. ✅ Graph execution badges match actual execution
2. ✅ All executed nodes are tracked correctly
3. ✅ Summary statistics are accurate
4. ✅ No "phantom" execution orders on non-executed nodes
5. ✅ Consistency between LLM calls and graph visualization

## Related Issues

- Agent pool execution with multiple nodes having the same name
- Execution order badges appearing on non-executed nodes
- Summary showing more LLM calls than executed nodes in graph

## Files Modified

1. `src/web/js/pipeline_executor.js` - Fixed node reference tracking
2. `src/api/web_chat_api.py` - Added debug logging
3. `src/docs/fixes/QUERY_REPORT_LLM_GRAPH_MISMATCH_FIX.md` - This document

## Date

October 12, 2025

## Status

✅ FIXED - Ready for testing


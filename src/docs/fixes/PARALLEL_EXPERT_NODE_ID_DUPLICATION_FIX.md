# Parallel Expert Node ID Duplication Fix

## Issue Summary

When multiple experts with identical names were selected for parallel execution, all experts were assigned the same node ID in LLM call tracking, causing incorrect reporting.

**Query Report:** `query_report_a7a43655_20251012_134256.html`

**Symptoms:**
1. Summary shows 5 LLM calls ✅
2. Agent Execution Graph shows only 1 expert node executed (2 skipped in parallel layer) ❌
3. LLM Calls section shows 5 calls, but Calls #2, #3, #4 all show Node **#3:3** ❌

**Expected:**
- Call #2: Node #3:1
- Call #3: Node #3:2
- Call #4: Node #3:3

**Actual:**
- Call #2: Node #3:3 ❌
- Call #3: Node #3:3 ❌
- Call #4: Node #3:3 ❌

## Root Cause

### The Problem

In `src/web/js/pipeline_executor.js` (line 894-898, before fix):

```javascript
plan.selectedExperts.forEach((delegation, idx) => {
    const expertNode = expertNodes.find(n => n.name === delegation.expertName);
    if (expertNode) {
        expertNode.executionOrder = `#${batchNumber}:${idx + 1}`;
        // ...
    }
});
```

When all 3 expert nodes are named "OpenAI", the `.find()` method returns the **FIRST** matching node every time:

**Iteration 1:**
- Find first node named "OpenAI" → Node 12
- Assign `executionOrder = "#3:1"`

**Iteration 2:**
- Find first node named "OpenAI" → Node 12 (same node!)
- Assign `executionOrder = "#3:2"` (overwrites #3:1!)

**Iteration 3:**
- Find first node named "OpenAI" → Node 12 (same node!)
- Assign `executionOrder = "#3:3"` (overwrites #3:2!)

**Result:**
- Node 12: executionOrder = "#3:3"
- Node 24: No execution order (never assigned)
- Node 34: No execution order (never assigned)

All 3 LLM calls use Node 12's execution order (#3:3), tracked 3 times.

### Graph Structure Impact

From the JSON (lines 178-233):
```json
{
  "id": 12,
  "execution_order": "#3:3",
  "executed": true
},
{
  "id": 24,
  "category": "llm"
  // No execution_order, not executed
},
{
  "id": 34,
  "category": "llm"
  // No execution_order, not executed
}
```

And execution tracking (lines 310-323):
```json
"execution_order": [
  { "node_id": 12, "timestamp": ... },
  { "node_id": 12, "timestamp": ... },  // Same node!
  { "node_id": 12, "timestamp": ... }   // Same node!
]
```

## The Fix

### Solution

Track which nodes have been assigned and exclude them from subsequent matches using a `Set`:

**File:** `src/web/js/pipeline_executor.js` (lines 889-915)

```javascript
// Assign execution order badges AND store references to expert nodes
const selectedExpertNodesWithDelegation = [];
const usedExpertNodeIds = new Set(); // ✅ Track which nodes we've already assigned

plan.selectedExperts.forEach((delegation, idx) => {
    // Find an expert node matching this name that hasn't been used yet
    // ✅ Important: When multiple nodes have the same name, use different instances
    const expertNode = expertNodes.find(n => 
        n.name === delegation.expertName && !usedExpertNodeIds.has(n.id)
    );
    
    if (expertNode) {
        expertNode.executionOrder = `#${batchNumber}:${idx + 1}`;
        console.log(`[EXEC ORDER]   Expert node ${expertNode.name} (ID: ${expertNode.id}) assigned ${expertNode.executionOrder}`);
        
        // ✅ Mark this node as used
        usedExpertNodeIds.add(expertNode.id);
        
        // Store the node reference WITH the delegation
        selectedExpertNodesWithDelegation.push({
            delegation: delegation,
            expertNode: expertNode
        });
    } else {
        console.warn(`[EXEC ORDER WARNING] Expert "${delegation.expertName}" not found (or all matching nodes already assigned)`);
    }
});
```

### How It Works Now

**Iteration 1:**
- Find first node named "OpenAI" NOT in usedExpertNodeIds → Node 12
- Assign `executionOrder = "#3:1"`
- Add 12 to usedExpertNodeIds

**Iteration 2:**
- Find first node named "OpenAI" NOT in usedExpertNodeIds → Node 24 (12 is excluded!)
- Assign `executionOrder = "#3:2"`
- Add 24 to usedExpertNodeIds

**Iteration 3:**
- Find first node named "OpenAI" NOT in usedExpertNodeIds → Node 34 (12, 24 excluded!)
- Assign `executionOrder = "#3:3"`
- Add 34 to usedExpertNodeIds

**Result:**
- Node 12: `executionOrder = "#3:1"` ✅
- Node 24: `executionOrder = "#3:2"` ✅
- Node 34: `executionOrder = "#3:3"` ✅

Each LLM call gets the correct unique node ID!

## Test Enhancement

Added better error reporting to Test 6 in `test_query_report_consistency.py`:

```python
# Test 6: Check for duplicate node IDs in LLM calls
for call in report_data['llm_calls']:
    node_id = call['node_id']
    if node_id in node_ids_used:
        duplicate_node_ids.append(
            f"Call #{call['call_number']} uses Node #{node_id} "
            f"which was already used (by Call #{node_ids_used.index(node_id) + 1})"
        )
    node_ids_used.append(node_id)
```

**Output when issue detected:**
```
Test 6: LLM calls should not have duplicate node IDs
  [FAIL] Found duplicate node IDs:
    - Call #3 uses Node #3:3 which was already used (by Call #2)
    - Call #4 uses Node #3:3 which was already used (by Call #2)

  ⚠️ Issue: Same node ID appears multiple times in LLM calls
  This usually means:
    - Parallel experts all using same node ID (e.g., all #3:3 instead of #3:1, #3:2, #3:3)
    - .find() returning same node when all have identical names
  → Fix: Use expert index instead of name matching, or ensure unique node assignment
```

## Expected Results After Fix

### Query Report Should Show:

**Summary:**
- ✅ 5 LLM Calls

**Agent Execution Graph:**
- ✅ Node 11 (#2) - Coordinator - EXECUTED
- ✅ Node 12 (#3:1) - Expert 1 - EXECUTED
- ✅ Node 24 (#3:2) - Expert 2 - EXECUTED
- ✅ Node 34 (#3:3) - Expert 3 - EXECUTED
- ✅ Node 26 (#4) - Aggregator - EXECUTED

**LLM Calls:**
- ✅ Call #1 (Node: #2) - Coordinator
- ✅ Call #2 (Node: #3:1) - Expert 1
- ✅ Call #3 (Node: #3:2) - Expert 2
- ✅ Call #4 (Node: #3:3) - Expert 3
- ✅ Call #5 (Node: #4) - Aggregator

All sections consistent!

## Benefits

1. ✅ **Unique node IDs** - Each expert gets correct execution order
2. ✅ **Accurate tracking** - Each node tracked individually
3. ✅ **Correct graph** - All expert nodes shown as executed
4. ✅ **Scales properly** - Works with 2, 3, 5, or any number of parallel experts
5. ✅ **Test coverage** - Enhanced test catches this issue

## Edge Cases Handled

### Case 1: Mixed Names
If experts have different names, original behavior works:
- "Expert A" → Node 1
- "Expert B" → Node 2
- "Expert A" → Node 3 (different node, same name)

### Case 2: More Selections Than Nodes
If coordinator selects 5 experts but only 3 available:
- First 3 get assigned to nodes
- Last 2 trigger warning: "all matching nodes already assigned"

### Case 3: All Identical Names (This Fix)
All named "OpenAI":
- Uses first available node
- Marks as used, finds next
- Each gets unique assignment

## Related Issues

- Duplicate node IDs in parallel expert execution
- Graph showing wrong number of executed nodes
- LLM call tracking showing same node multiple times

## Files Modified

1. ✅ `src/web/js/pipeline_executor.js` - Fixed node assignment with exclusion tracking
2. ✅ `src/tests/test_query_report_consistency.py` - Enhanced Test 6 with better error messages
3. ✅ `src/docs/fixes/PARALLEL_EXPERT_NODE_ID_DUPLICATION_FIX.md` - This document

## Date

October 12, 2025

## Status

✅ FIXED - Each parallel expert now gets unique node ID tracking


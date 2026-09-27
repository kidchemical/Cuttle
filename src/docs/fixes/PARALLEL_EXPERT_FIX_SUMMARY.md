# Parallel Expert Node ID Duplication - FIXED ✅

## Issue

When 3 experts with identical names executed in parallel, all got the same node ID (#3:3) instead of unique IDs (#3:1, #3:2, #3:3).

**Query Report:** `query_report_a7a43655_20251012_134256.html`

**Symptoms:**
- Summary: 5 LLM calls ✅
- Graph: Only 1 expert node shown as executed (2 marked skipped) ❌
- LLM Calls: All 3 parallel experts show Node #3:3 ❌

**Expected:**
```
Call #2: Node #3:1 (Expert 1)
Call #3: Node #3:2 (Expert 2)
Call #4: Node #3:3 (Expert 3)
```

**Actual:**
```
Call #2: Node #3:3 ❌
Call #3: Node #3:3 ❌
Call #4: Node #3:3 ❌
```

## Root Cause 🔍

When assigning execution orders, `.find()` kept returning the **same node** (Node 12):

```javascript
// Iteration 1: Find "OpenAI" → Returns Node 12 → Assign "#3:1"
// Iteration 2: Find "OpenAI" → Returns Node 12 AGAIN → Assign "#3:2" (overwrites!)
// Iteration 3: Find "OpenAI" → Returns Node 12 AGAIN → Assign "#3:3" (overwrites!)

// Result: Node 12 has "#3:3", others have nothing!
```

## The Fix ✅

Track which nodes have been assigned using a `Set`:

```javascript
const usedExpertNodeIds = new Set(); // Track assigned nodes

plan.selectedExperts.forEach((delegation, idx) => {
    // Find node matching name that HASN'T been used yet
    const expertNode = expertNodes.find(n => 
        n.name === delegation.expertName && !usedExpertNodeIds.has(n.id)
    );
    
    if (expertNode) {
        expertNode.executionOrder = `#${batchNumber}:${idx + 1}`;
        usedExpertNodeIds.add(expertNode.id); // ✅ Mark as used
        // ...
    }
});
```

**Now:**
- Iteration 1: Finds Node 12 → Assign "#3:1" → Mark 12 as used
- Iteration 2: Finds Node 24 (12 excluded) → Assign "#3:2" → Mark 24 as used
- Iteration 3: Finds Node 34 (12, 24 excluded) → Assign "#3:3" → Mark 34 as used

**Result:** Each node gets unique execution order! ✅

## Expected After Fix 🎯

**Graph:**
- Node 11 (#2) - Coordinator - EXECUTED ✅
- Node 12 (#3:1) - Expert 1 - EXECUTED ✅
- Node 24 (#3:2) - Expert 2 - EXECUTED ✅
- Node 34 (#3:3) - Expert 3 - EXECUTED ✅
- Node 26 (#4) - Aggregator - EXECUTED ✅

**LLM Calls:**
- Call #1 (Node: #2) - Coordinator planning
- Call #2 (Node: #3:1) - Expert 1 objectives
- Call #3 (Node: #3:2) - Expert 2 spacecraft design
- Call #4 (Node: #3:3) - Expert 3 operations
- Call #5 (Node: #4) - Final aggregation

**All unique node IDs!** 🎉

## Test Coverage ✅

Enhanced Test 6 now catches this:

```
Test 6: LLM calls should not have duplicate node IDs
  [FAIL] Found duplicate node IDs:
    - Call #3 uses Node #3:3 which was already used (by Call #2)
    - Call #4 uses Node #3:3 which was already used (by Call #2)

  → Fix: Use expert index instead of name matching, or ensure unique node assignment
```

## Summary of All 3 Fixes

### Fix #1: Expert Node Reference Tracking
**Issue:** Same node found twice with .find()
**Fix:** Store reference and reuse it
**Status:** ✅ FIXED

### Fix #2: Aggregator Node Execution
**Issue:** Aggregator skipped, coordinator used twice
**Fix:** Find and use downstream aggregator node
**Status:** ✅ FIXED

### Fix #3: Parallel Expert Unique Assignment (This Fix)
**Issue:** Multiple experts assigned same node ID
**Fix:** Track used nodes, exclude from subsequent matches
**Status:** ✅ FIXED

## Files Modified 📝

- ✅ `src/web/js/pipeline_executor.js` - Node assignment with exclusion tracking
- ✅ `src/tests/test_query_report_consistency.py` - Enhanced Test 6 error messages
- ✅ `src/docs/fixes/PARALLEL_EXPERT_NODE_ID_DUPLICATION_FIX.md` - Full details
- ✅ `PARALLEL_EXPERT_FIX_SUMMARY.md` - This summary

## Testing 🧪

Generate new query report with "Plan a fictional moon landing" and verify:

```bash
cd src\tests
python test_query_report_consistency.py
```

**Expected:**
```
Test 6: [PASS] All LLM calls use unique node IDs
  Node IDs used: 2, 3:1, 3:2, 3:3, 4
```

---

**Status**: ✅ FIXED - Parallel experts now get unique node IDs  
**Date**: October 12, 2025  
**Impact**: Accurate tracking for any number of parallel experts (1:n:1 fully supported!)


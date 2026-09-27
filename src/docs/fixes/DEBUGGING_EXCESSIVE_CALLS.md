# Debugging Excessive LLM Calls

## Changes Made

### 1. Added Call Numbers to LLM Calls Section ✅

**File**: `src/query_report_generator.py`

Now each LLM call in the query report shows a clear badge:

```
Call #1 ✅ gpt-4o-mini
Call #2 ✅ gpt-4o-mini
Call #3 ✅ gpt-4o-mini
```

**Styling**: Purple gradient badge with white text, similar to execution order badges in the graph.

### 2. Enhanced Debugging Logs ✅

**File**: `src/web/js/pipeline_executor.js`

Added detailed logging to help debug agent pool execution:

```javascript
// When agent pool is detected
🔀 Agent Pool Detected: 5 expert agents available
📋 Experts: Expert 1, Expert 2, Expert 3, Expert 4, Expert 5

// When marking downstream complete
🔒 Marking downstream nodes as complete to prevent re-execution...
✓ Marked Node A as completed (agent pool aggregation)
✓ Marked Node B as completed (agent pool aggregation)
✓ Marked 8 downstream node(s) as complete

// When filtering downstream nodes
✓ All downstream nodes already executed
```

## How to Verify the Fix

### Step 1: Clear Browser Cache
The latest JavaScript changes need to be loaded:
1. Press `Ctrl+Shift+Delete` (Chrome/Edge) or `Ctrl+Shift+R` (Firefox)
2. Or hard refresh the node editor page

### Step 2: Run Your Pipeline
1. Open node_editor.html
2. Load or create your Parallelization pipeline
3. Click "Run"
4. **Watch the console output**

### Step 3: Check Console Logs

**Good execution** (agent pool working correctly):
```
▶️ Executing node: OpenAI (llm-openai)
🤖 LLM Request: OpenAI
   Model: gpt-4o-mini
   Prompt: Each expert, say "Hello World" + your agent name...
   🔀 Agent Pool Detected: 5 expert agents available
   📋 Experts: OpenAI, OpenAI, OpenAI, OpenAI, OpenAI
   📋 Coordinator Planning Phase...
   📊 Analysis: [coordinator's reasoning]
   👥 Selected 4 expert(s) for delegation
   ⚡ Executing experts in parallel...
   🎯 Delegating to Expert 1: [task]
   🎯 Delegating to Expert 2: [task]
   🎯 Delegating to Expert 3: [task]
   🎯 Delegating to Expert 4: [task]
   ✅ Expert 1 completed
   ✅ Expert 2 completed
   ✅ Expert 3 completed
   ✅ Expert 4 completed
   🔄 Aggregating 4 expert response(s)...
   🎉 Final Response: [result]
   🔒 Marking downstream nodes as complete to prevent re-execution...
   ✓ Marked Aggregator 1 as completed (agent pool aggregation)
   ✓ Marked Aggregator 2 as completed (agent pool aggregation)
   ✓ Marked Aggregator 3 as completed (agent pool aggregation)
   ✓ Marked Aggregator 4 as completed (agent pool aggregation)
   ✓ Marked Final Aggregator as completed (agent pool aggregation)
   ✓ Marked 12 downstream node(s) as complete
✅ Node completed: OpenAI
✓ All downstream nodes already executed  ← GOOD!
```

**Bad execution** (if still broken):
```
✅ Node completed: OpenAI
⚡ Executing 5 downstream nodes in parallel...  ← BAD! Should be filtered
▶️ Executing node: OpenAI (expert 1)  ← BAD! Already executed
```

### Step 4: Check Query Report

Open the generated query report and verify:

**Expected**:
- **LLM Calls**: 6 (1 coordinator + 4 experts + 1 aggregation)
- **Call badges**: Call #1, Call #2, Call #3, Call #4, Call #5, Call #6

**Problem indicators**:
- **LLM Calls**: 14-16+ (excessive)
- Multiple aggregation calls with same prompts

## Current Issue Analysis

Looking at `query_report_1ca0041b_20251011_202501`:
- **16 LLM calls** (should be 6)
- **9 nodes** in pipeline (6 LLM nodes)

### Hypothesis

This query report was generated **before the latest fixes**. The timestamp shows `20251011_202501` which is likely before you accepted the recursive downstream marking changes.

### To Test

1. **Ensure latest code is active**:
   - Check that `pipeline_executor.js` has the recursive `markDownstreamComplete` function
   - Check that it marks downstream of BOTH experts AND coordinator

2. **Run a fresh execution**:
   - Clear browser cache
   - Reload node editor
   - Run the Parallelization pipeline
   - Generate new query report

3. **Compare results**:
   - Check if LLM call count drops from 16 to 6
   - Verify console shows "Marked X downstream node(s) as complete"
   - Confirm "All downstream nodes already executed" message

## Potential Remaining Issues

If after clearing cache and rerunning, you still see 16 calls:

### Issue 1: Expert Nodes Not Being Stored
Check if expert nodes are being added to `this.nodeData` during agent pool execution.

**Debug**: Add logging in `executeLLMWithAgentPool`:
```javascript
this.nodeData.set(expertNode.id, { response: expertResult.response });
this.log('info', `   💾 Stored result for ${expertNode.name} (ID: ${expertNode.id})`);
```

### Issue 2: Wrong Node IDs
The downstream node IDs might not match the stored IDs.

**Debug**: Log IDs during filtering:
```javascript
const nodesToExecute = downstreamNodes.filter(downstream => {
    const hasData = this.nodeData.has(downstream.id);
    this.log('info', `   🔍 Checking ${downstream.name} (ID: ${downstream.id}): ${hasData ? 'SKIP' : 'EXECUTE'}`);
    return !hasData;
});
```

### Issue 3: Aggregator Nodes Connected Differently
The aggregator nodes might have multiple input connections, and they're being triggered by a different path.

**Debug**: Check the pipeline graph structure:
- Are aggregators connected to experts?
- Are there merge nodes?
- Are there multiple paths to the final output?

### Issue 4: Promise.all Timing
The parallel execution might complete before the downstream marking happens.

**Debug**: Ensure marking happens synchronously after aggregation.

## Expected Flow (Correct)

```
1. Coordinator detects 5 downstream LLM nodes
   └─ Triggers executeLLMWithAgentPool()

2. Coordinator plans (1 LLM call)
   └─ Selects 4 experts

3. Experts execute in parallel (4 LLM calls)
   └─ Results stored in this.nodeData

4. Internal aggregation (1 LLM call)
   └─ Aggregated result created

5. Mark ALL downstream nodes recursively
   ├─ Expert 1 → Agg 1 → Final Agg
   ├─ Expert 2 → Agg 2 → Final Agg
   ├─ Expert 3 → Agg 3 → Final Agg
   ├─ Expert 4 → Agg 4 → Final Agg
   └─ Expert 5 → Agg 5 → Final Agg (not selected, still marked)

6. Return to normal flow
   └─ Check downstream of coordinator
   └─ All experts already in this.nodeData
   └─ Filter returns empty array
   └─ Log: "All downstream nodes already executed"

7. Pipeline continues
   └─ No more LLM nodes to execute
   └─ Reach output nodes

Total: 6 LLM calls
```

## Files Modified

1. **`src/query_report_generator.py`**
   - Lines 1839-1866: Added call number badges to LLM calls
   - Lines 935-945: Added CSS styling for call-number-badge

2. **`src/web/js/pipeline_executor.js`**
   - Lines 372-378: Enhanced agent pool detection logging
   - Lines 665-678: Added downstream marking count logging

## Next Steps

1. ✅ Call numbers now displayed in query reports
2. ✅ Enhanced logging added for debugging
3. ⏳ **User needs to**:
   - Clear browser cache
   - Reload node editor
   - Run pipeline again
   - Check new query report
   - Share results if still seeing 16 calls

## If Still Broken

If after a fresh run with cleared cache you still see 16 calls:

1. **Share the console output** - Look for the logging messages
2. **Check the execution graph** - Which nodes show as executed vs not-executed?
3. **Verify JavaScript loaded** - Check browser dev tools → Sources → pipeline_executor.js
4. **Check for errors** - Any errors in browser console?

---

## Summary

- ✅ Added **Call #1, Call #2...** badges to LLM Calls section
- ✅ Added **detailed logging** to debug agent pool execution
- ⏳ **Need fresh pipeline run** to verify 16→6 call reduction
- 📋 **Enhanced monitoring** to catch remaining issues

The recursive downstream marking should prevent all duplicate aggregations. The enhanced logging will help us identify if there's still an issue with the fix.


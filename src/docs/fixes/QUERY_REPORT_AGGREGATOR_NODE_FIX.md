# Query Report Aggregator Node Fix - Agent Pool Flow

## Issue Summary (Second Discrepancy)

After fixing the first issue (expert node tracking), a new discrepancy was discovered in query report `dd2b70ed_20251012_125232.html`:

1. **Agent Execution Graph** - Shows 2 LLM nodes executed; final LLM node (Node 26) shows as "SKIPPED" ❌
2. **Query Execution Summary** - Shows 3 LLM calls correctly ✅
3. **LLM Calls** - Shows 3 LLM nodes ran, but node IDs are wrong:
   - Call #1: Node #2 ✅ (Coordinator)
   - Call #2: Node #3:1 ✅ (Expert)
   - Call #3: Node #2 ❌ (Should be #4 for aggregator, not #2 again!)

## Root Cause

The agent pool flow was using the **coordinator node** (Node 11) to perform **both** the initial planning AND the final aggregation, instead of using the designated downstream aggregation node (Node 26).

### Flow Before Fix

```
Node 11 (Coordinator) → Plan delegation (#2)
  ↓
Node 12 (Expert) → Execute task (#3:1)
  ↓
Node 11 (Coordinator AGAIN) → Aggregate results (#2 again!)
  ↓
Node 26 (Aggregator) → SKIPPED (marked as complete without executing)
```

### Why This Happened

In `pipeline_executor.js` line 1012 (old code):

```javascript
nodeId: coordDisplayNodeId  // Include node ID for tracking (aggregation phase)
```

The final aggregation LLM call was using the **coordinator's display ID** instead of finding and using the actual downstream aggregation node.

Additionally, lines 1048-1059 marked **all downstream nodes** (including Node 26) as complete/skipped after the coordinator did the aggregation.

## The Fix

### Key Changes

1. **Find the dedicated aggregator node** - Look for LLM nodes downstream of the expert nodes
2. **Assign execution order** to the aggregator node (#4)
3. **Use the aggregator node** for the final LLM call instead of the coordinator
4. **Track aggregator execution** via `/api/record-node-execution`
5. **Exclude the aggregator** from the "mark as complete" logic since it actually executes

### Code Changes

**File**: `src/web/js/pipeline_executor.js` (lines 987-1043)

```javascript
// Find the downstream aggregation node (common downstream of all expert nodes)
const expertDownstreamNodes = new Set();
expertNodes.forEach(expert => {
    this.getDownstreamNodes(expert).forEach(dn => expertDownstreamNodes.add(dn));
});

// Find LLM nodes in the downstream set (aggregator candidates)
const aggregatorCandidates = Array.from(expertDownstreamNodes).filter(n => n.category === 'llm');

// Use the first aggregator candidate if available, otherwise use coordinator
const aggregatorNode = aggregatorCandidates.length > 0 ? aggregatorCandidates[0] : coordinatorNode;
const usesDedicatedAggregator = aggregatorCandidates.length > 0;

if (usesDedicatedAggregator) {
    this.log('info', `   📊 Using dedicated aggregator node: ${aggregatorNode.name}`, aggregatorNode.id);
    
    // Assign execution order to aggregator
    this.executionCounter++;
    aggregatorNode.executionOrder = `#${this.executionCounter}`;
    console.log(`[EXEC ORDER] Aggregator node ${aggregatorNode.name} (ID: ${aggregatorNode.id}) assigned ${aggregatorNode.executionOrder}`);
} else {
    this.log('info', `   📊 Using coordinator for aggregation (no dedicated aggregator found)`);
}

// ... build aggregation prompt ...

// Get display node ID for the aggregator
const aggregatorDisplayNodeId = aggregatorNode.executionOrder || 
                               (aggregatorNode.operationalId ? `#${aggregatorNode.operationalId}` : null) || 
                               aggregatorNode.id;

// Make LLM call using aggregator node
const finalResponse = await fetch('/api/llm-request', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
        nodeType: aggregatorNode.type,
        model: aggregatorNode.config.model,
        prompt: aggregationPrompt,
        systemPrompt: aggregatorNode.config.systemPrompt || 'You are a coordinator synthesizing expert opinions.',
        temperature: aggregatorNode.config.temperature || 0.5,
        maxTokens: aggregatorNode.config.maxTokens || 2000,
        queryId: this.queryId,
        nodeId: aggregatorDisplayNodeId  // ✅ Use aggregator's node ID
    })
});
```

**Tracking aggregator execution** (lines 1050-1079):

```javascript
if (finalResult.success) {
    // Track aggregator node execution if it's a dedicated node
    if (usesDedicatedAggregator) {
        console.log(`[EXEC TRACKING] Recording execution for aggregator node ${aggregatorNode.name} (ID: ${aggregatorNode.id}, display: ${aggregatorDisplayNodeId})`);
        
        // Mark aggregator as executed in nodeData
        this.nodeData.set(aggregatorNode.id, {
            response: finalResult.response,
            mode: 'agent-pool-aggregator',
            executed: true
        });
        
        // Track with query tracker
        if (this.queryId) {
            try {
                const trackResponse = await fetch('/api/record-node-execution', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        queryId: this.queryId,
                        nodeId: aggregatorNode.id,
                        success: true
                    })
                });
                const trackResult = await trackResponse.json();
                console.log(`[EXEC TRACKING] Track response for aggregator node ${aggregatorNode.id}:`, trackResult);
            } catch (error) {
                console.error(`[EXEC TRACKING ERROR] Failed to record aggregator node execution:`, error);
            }
        }
    }
}
```

**Exclude aggregator from skip logic** (lines 1085-1133):

```javascript
const markDownstreamComplete = (node, excludeNodeId = null) => {
    const downstream = this.getDownstreamNodes(node);
    for (const downNode of downstream) {
        // ✅ Skip the aggregator node if it's the one doing the aggregation
        if (excludeNodeId && downNode.id === excludeNodeId) {
            continue;
        }
        
        if (!this.nodeData.has(downNode.id)) {
            // ... mark as skipped ...
        }
    }
};

// Mark downstream but exclude the aggregator since it executed
const excludeAggregatorId = usesDedicatedAggregator ? aggregatorNode.id : null;

for (const expertNode of expertNodes) {
    markDownstreamComplete(expertNode, excludeAggregatorId);  // ✅ Exclude aggregator
}

markDownstreamComplete(coordinatorNode, excludeAggregatorId);  // ✅ Exclude aggregator

// Mark downstream of aggregator (its own downstream can be skipped)
if (usesDedicatedAggregator) {
    markDownstreamComplete(aggregatorNode, null);
}
```

### Flow After Fix

```
Node 11 (Coordinator) → Plan delegation (#2)
  ↓
Node 12 (Expert) → Execute task (#3:1)
  ↓
Node 26 (Aggregator) → Aggregate results (#4) ✅
  ↓
Output nodes → Marked as complete
```

## Expected Results

After this fix, the query report should show:

### Summary
- ✅ 3 LLM Calls

### Agent Execution Graph
- ✅ Node 11 (Coordinator) #2 - EXECUTED
- ✅ Node 12 (Expert) #3:1 - EXECUTED  
- ✅ Node 26 (Aggregator) #4 - EXECUTED (no longer skipped!)

### LLM Calls Section
- ✅ Call #1 (Node: #2) - Coordinator planning
- ✅ Call #2 (Node: #3:1) - Expert execution
- ✅ Call #3 (Node: #4) - Final aggregation (not #2 anymore!)

**All three sections match perfectly!**

## Benefits

1. ✅ **Respects graph structure** - Aggregation happens at the correct node
2. ✅ **Accurate execution tracking** - Node 26 marked as executed, not skipped
3. ✅ **Correct node IDs** - LLM calls show unique node IDs (#2, #3:1, #4)
4. ✅ **Better visualization** - Graph shows complete execution flow
5. ✅ **Uses aggregator's config** - Temperature, maxTokens, systemPrompt from the aggregator node

## Backwards Compatibility

The fix includes a fallback:

```javascript
const aggregatorNode = aggregatorCandidates.length > 0 ? aggregatorCandidates[0] : coordinatorNode;
```

If no downstream aggregator node is found (older pipelines or different patterns), it falls back to using the coordinator as before.

## Testing

### 1. Run the Consistency Test

```bash
cd src\tests
python test_query_report_consistency.py
```

### 2. Generate a New Query Report

1. Start Cuttle web UI
2. Execute "TEST - Hello World (Parallelization)" pipeline
3. Check the query report

### 3. Expected Test Results

```
Test 1: LLM Calls match LLM nodes in Agent Execution Graph
  LLM nodes in graph: 3
  LLM calls in report: 3
  [PASS] LLM call count matches LLM nodes in graph

Test 2: Every LLM call references a node with matching execution order
  [PASS] All LLM calls reference valid LLM nodes in graph

Test 3: LLM Call numbers are sequential and reference correct nodes
  [PASS] All LLM calls are sequential and reference valid LLM nodes

  LLM Call → Node Mapping:
    Call #1 → Node #2 (OpenAI - Coordinator)
    Call #2 → Node #3:1 (OpenAI - Expert)
    Call #3 → Node #4 (OpenAI - Aggregator)  ← Now correct!
```

## Related Issues

This fix addresses:
- Agent pool aggregation using wrong node ID
- Final aggregation node showing as "SKIPPED" when it shouldn't be
- Duplicate node IDs in LLM calls section (#2 appearing twice)
- Graph not showing complete execution flow

## Files Modified

1. `src/web/js/pipeline_executor.js` - Agent pool aggregation logic
2. `src/docs/fixes/QUERY_REPORT_AGGREGATOR_NODE_FIX.md` - This document

## Date

October 12, 2025

## Status

✅ FIXED - Ready for testing

## Combined with Previous Fix

This fix works together with the previous fix (`QUERY_REPORT_LLM_GRAPH_MISMATCH_FIX.md`) to provide complete query report consistency:

1. **First fix**: Expert nodes use consistent node references (no duplicate .find())
2. **Second fix**: Aggregation uses dedicated aggregator node (not coordinator twice)

Together, these ensure perfect consistency across all sections of the query report!


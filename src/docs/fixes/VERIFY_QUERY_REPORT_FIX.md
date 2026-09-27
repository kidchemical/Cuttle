# How to Verify the Query Report Fix

## Quick Verification Steps

### Step 1: Generate a New Query Report

1. **Start the Cuttle web UI**:
   ```bash
   cd /path/to/Cuttle
   start_electron.bat
   ```

2. **Execute a Pipeline**:
   - Open the Node Editor
   - Load "TEST - Hello World (Parallelization)" pipeline
   - Click "Execute" 
   - Wait for completion

3. **Note the Query ID**:
   - Look at the console output or the report
   - You'll see a query ID like `930987af`

### Step 2: Run the Consistency Test

```bash
cd /path/to/Cuttle/src\tests
python test_query_report_consistency.py
```

### Expected Results

#### Before Fix (Old Reports)
```
Test 1: LLM Calls match LLM nodes in Agent Execution Graph
  LLM nodes in graph: 1
  LLM calls in report: 3
  [FAIL] LLM call count does NOT match LLM nodes in graph

Test 2: Every LLM call references a node with matching execution order
  [FAIL] Found mismatches:
    - Call #2 references Node #3:1 which is not in graph
```

#### After Fix (New Reports)
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
    Call #1 → Node #2 (OpenAI)
    Call #2 → Node #3:1 (OpenAI)
    Call #3 → Node #2 (OpenAI)
```

### Step 3: Visual Verification

Open the query report HTML file in your browser:

**Before Fix**:
- 🔴 Summary: 3 LLM Calls
- 🔴 Graph: Shows only 2 executed nodes (1 LLM)
- 🔴 LLM Calls section: 3 calls

**After Fix**:
- ✅ Summary: 3 LLM Calls
- ✅ Graph: Shows 3 executed nodes (3 LLMs with badges #2, #3:1, #2)
- ✅ LLM Calls section: 3 calls

**All three should match!**

## What the Fix Does

The fix ensures that when multiple expert nodes have the same name (e.g., "OpenAI"), the system:

1. ✅ Finds the expert node ONCE when assigning execution orders
2. ✅ Stores that node reference
3. ✅ Reuses the SAME reference when executing
4. ✅ Tracks execution on the correct node

This prevents the bug where:
- Node A gets an execution order badge but never executes
- Node B executes but doesn't get marked as executed

## Troubleshooting

### If tests still fail:

1. **Clear browser cache**: The JavaScript changes need to reload
2. **Restart the server**: Ensure Python changes are loaded
3. **Check console logs**: Look for `[EXEC TRACKING]` messages
4. **Verify node IDs**: Check that expert nodes have unique IDs

### Debug Output

With the fix, you should see in the console:

```
[EXEC ORDER] Agent pool batch 3 for 1 experts (counter: 3)
[EXEC ORDER]   Expert node OpenAI (ID: 12) assigned #3:1
[EXEC TRACKING] Recording execution for expert node OpenAI (ID: 12, display: #3:1)
[EXEC TRACKING] Track response for node 12: {success: true}
```

And in the Python logs:

```
[QUERY API] record-node-execution called: node_id=12, query_id=930987af, success=True
[QUERY API] Available node IDs in graph: ['11', '12', '15', '21', '23', '24', '26', '34', '36']
[QUERY API] Looking for node ID: 12 (type: int)
[QUERY API] Successfully recorded node execution: 12 (success: True)
```

## Next Steps

After verification passes:
1. ✅ Commit the fix
2. 📝 Update the changelog
3. 🎉 Celebrate consistent query reports!

## Related Files

- `src/web/js/pipeline_executor.js` - Main fix
- `src/api/web_chat_api.py` - Debug logging
- `src/tests/test_query_report_consistency.py` - Test
- `src/docs/fixes/QUERY_REPORT_LLM_GRAPH_MISMATCH_FIX.md` - Detailed explanation


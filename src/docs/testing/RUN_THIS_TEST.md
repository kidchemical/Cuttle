# 🧪 Run This Test - Step by Step Guide

## Your Current Situation

The consistency test is checking an **old report** (from before the fixes).

You need to:
1. ✅ Generate a **NEW** query report with all fixes applied
2. ✅ Run the consistency test on that new report
3. ✅ Verify all tests pass

## Step-by-Step Testing

### Step 1: Execute the Pipeline 🚀

1. **Navigate to Node Editor:**
   - Open your browser
   - Go to: http://localhost:8080/node_editor.html
   - OR click the Node Editor link in the Cuttle UI

2. **Load the Pipeline:**
   - Click "Load" button
   - Select "TEST - Hello World (Parallelization)"
   - Pipeline should appear on the canvas

3. **Enter Test Prompt:**
   In the text input field, enter:
   ```
   Plan a fictional moon landing
   ```

4. **Click Execute:**
   - Click the "Execute" button
   - Watch the execution log on the right side

5. **Wait for Completion:**
   - Should take about 25-45 seconds
   - Look for "Pipeline execution completed successfully!"

### Step 2: Check Console Logs 👀

While executing, watch for these console messages:

**✅ GOOD (What you should see):**
```
[EXEC ORDER] Agent pool batch 3 for 3 experts (counter: 3)
[EXEC ORDER]   Expert node OpenAI (ID: 12) assigned #3:1
[EXEC ORDER]   Expert node OpenAI (ID: 24) assigned #3:2
[EXEC ORDER]   Expert node OpenAI (ID: 34) assigned #3:3
[EXEC TRACKING] Recording execution for expert node OpenAI (ID: 12, display: #3:1)
[EXEC TRACKING] Recording execution for expert node OpenAI (ID: 24, display: #3:2)
[EXEC TRACKING] Recording execution for expert node OpenAI (ID: 34, display: #3:3)
[EXEC ORDER] Aggregator node OpenAI (ID: 26) assigned #4
```

**❌ BAD (Old broken behavior):**
```
[EXEC ORDER]   Expert node OpenAI (ID: 12) assigned #3:1
[EXEC ORDER]   Expert node OpenAI (ID: 12) assigned #3:2  ← Same ID!
[EXEC ORDER]   Expert node OpenAI (ID: 12) assigned #3:3  ← Same ID!
```

### Step 3: Note the Query ID 📝

After execution completes, note the Query ID from the console or report.
Example: `a7a43655` or similar 8-character ID

### Step 4: Run Consistency Test ✅

```bash
cd src\tests
python test_query_report_consistency.py
```

### Step 5: Verify All Tests Pass 🎯

**Expected Output:**
```
[SUCCESS] ALL TESTS PASSED - Query report is consistent!
```

**Specifically check:**
- Test 6: [PASS] All LLM calls use unique node IDs
  - Node IDs used: 2, 3:1, 3:2, 3:3, 4
- Test 7: [PASS] All LLM nodes that made calls are marked as executed

### Step 6: Visual Verification in Report 👁️

Open the query report HTML file:
- Location: `src/web/logs/query_report_XXXXXXXX_YYYYMMDD_HHMMSS.html`

**Check Summary Section:**
- Shows: 5 LLM Calls ✅

**Check Agent Execution Graph:**
- Text Input (#1) - Green/Executed
- OpenAI (#2) Coordinator - Green/Executed
- **Parallel Layer** (this is key!):
  - OpenAI (#3:1) - Green/Executed ✅
  - OpenAI (#3:2) - Green/Executed ✅
  - OpenAI (#3:3) - Green/Executed ✅
- OpenAI (#4) Aggregator - Green/Executed
- Output nodes - Dashed/Skipped

**Check LLM Calls Section:**
```
Call #1 (Node: #2) - Coordinator
Call #2 (Node: #3:1) - Expert 1
Call #3 (Node: #3:2) - Expert 2
Call #4 (Node: #3:3) - Expert 3
Call #5 (Node: #4) - Aggregator
```

**All unique node IDs!** ✅

### Step 7: Verify Coordinator Behavior 🤖

In Call #1 (Coordinator), check the response:

**✅ GOOD:**
```json
{
  "selectedExperts": [
    { "expertName": "OpenAI", "task": "Define objectives..." },
    { "expertName": "OpenAI", "task": "Design spacecraft..." },
    { "expertName": "OpenAI", "task": "Develop operations..." }
  ]
}
```
3 experts selected, each with distinct task!

**❌ BAD:**
```json
{
  "selectedExperts": [
    { "expertName": "OpenAI", "task": "Draft complete mission plan..." }
  ]
}
```
Only 1 expert selected for a complex task.

### Step 8: Verify Expert Responses 📊

**✅ GOOD:**
- Expert 1: Talks about objectives only
- Expert 2: Talks about spacecraft design only
- Expert 3: Talks about operations only
- No invented team members (Alex Johnson, etc.)

**❌ BAD:**
- Expert invents fictional people
- Expert responds on behalf of multiple sub-agents
- Responses overlap or duplicate content

## Quick Status Check

Run this command to see what fixes are active:

```bash
# Check if fixes are in place
grep -n "usedExpertNodeIds" src/web/js/pipeline_executor.js
# Should return line numbers if fix is applied

grep -n "aggregatorNode =" src/web/js/pipeline_executor.js  
# Should return line numbers if aggregator fix is applied
```

## If Something Fails

### Scenario A: Duplicate Node IDs (Test 6 fails)

**Issue:** All experts show #3:3
**Cause:** `usedExpertNodeIds` fix not applied
**Fix:** Check that line 891 has `const usedExpertNodeIds = new Set();`

### Scenario B: Only 1 Expert Selected

**Issue:** Coordinator selects 1 expert for complex task
**Cause:** Browser cache (old JavaScript)
**Fix:** Hard refresh (Ctrl+Shift+R) and re-run

### Scenario C: Aggregator Skipped (Test 7 fails)

**Issue:** Node 26 shows as skipped
**Cause:** Aggregator tracking not working
**Fix:** Check lines 1050-1078 for aggregator tracking code

## Success! 🎉

When you see:
```
[SUCCESS] ALL TESTS PASSED - Query report is consistent!

Test 6: [PASS] All LLM calls use unique node IDs
  Node IDs used: 2, 3:1, 3:2, 3:3, 4

Test 7: [PASS] All LLM nodes that made calls are marked as executed
```

**All 3 fixes are working correctly!** 🚀

---

**Remember:** You must execute the pipeline in the web UI first to generate a new report!


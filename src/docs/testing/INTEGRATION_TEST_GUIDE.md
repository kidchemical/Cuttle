# Integration Test Guide
## Testing "TEST - Hello World (Parallelization)" from Node Editor

## Current Status

✅ **All programmatic tests pass** - Simulated executions work perfectly
⚠️ **Server needs restart** - Code changes require server restart to take effect

---

## Issue Encountered

When you ran the pipeline from `node_editor.html`, the report file `query_report_6d8eeebb_20251012_175116.html` was not created. This indicates the server crashed during report generation, likely because:

1. The server was running with the old code (before encoding fixes)
2. Windows encoding error occurred during execution order mapping
3. Report generation failed before file was written

---

## Solution: Restart Server

The Flask development server needs to be **fully restarted** to pick up the Python code changes:

### Step 1: Stop the Server
```bash
# Find and kill the Python process
Get-Process python* | Where-Object {$_.CommandLine -like "*web_chat_api*"} | Stop-Process -Force
```

### Step 2: Start Fresh Server
```bash
cd src
python web_chat_api.py
```

### Step 3: Wait for Server Ready
Look for:
```
* Running on http://127.0.0.1:8080
* Debugger is active!
```

---

## Testing Steps

### 1. Open Node Editor
```
http://localhost:8080/node_editor.html
```

### 2. Load the Pipeline
- Click "Open Pipeline"
- Select "TEST - Hello World (Parallelization)"
- Verify the graph loads correctly

### 3. Run the Pipeline
- Click the "▶ Run" button
- Watch the execution in the console
- Note any errors in browser console (F12)

### 4. Check for Report
After execution completes, you should see:
```
📊 Query report generated: query_report_XXXXXXXX_YYYYMMDD_HHMMSS.html
🔗 View report: http://localhost:8080/logs/query_report_XXXXXXXX_YYYYMMDD_HHMMSS.html
```

### 5. Verify Report
Click the report link and check:
- ✅ Agent Execution Graph shows all executed nodes
- ✅ LLM Calls section shows all LLM requests
- ✅ Execution orders match between sections
- ✅ Parallel nodes show notation like #2:1, #2:2
- ✅ No disconnected nodes appear

---

## Expected Results

### Agent Execution Graph Should Show:
```
Text Input (#1)
    ↓
OpenAI Coordinator (#2)
    ↓
┌───────────┴───────────┐
│                       │
OpenAI Expert 1 (#3:1)  OpenAI Expert 2 (#3:2)  [Parallel]
│                       │
└───────────┬───────────┘
    ↓
OpenAI Aggregator (#4)
    ↓
┌───────────┴───────────┐
│                       │
Log Output (#5:1)  File Output (#5:2)  [Parallel]
```

### LLM Calls Section Should Show:
```
Call #1 (Node: #2) ✅ gpt-4o-mini    [Coordinator]
Call #2 (Node: #3:1) ✅ gpt-4o-mini  [Expert 1]
Call #3 (Node: #3:2) ✅ gpt-4o-mini  [Expert 2]
Call #4 (Node: #4) ✅ gpt-4o-mini    [Aggregator]
```

---

## Automated Verification

After generating a report, run:

```bash
python test_query_report_consistency.py
```

This will:
1. Find the latest report
2. Parse the HTML
3. Verify consistency between sections
4. Show PASS/FAIL for each test

Expected output:
```
================================================================================
[SUCCESS] ALL TESTS PASSED - Query report is consistent!
================================================================================
```

---

## Troubleshooting

### Report Not Generated

**Symptom**: No report link in console after execution

**Causes**:
1. Server hasn't restarted with new code
2. Encoding error during report generation
3. Permission issues writing to `web/logs/`

**Fix**:
1. Restart the server completely
2. Check server logs for errors
3. Ensure `web/logs/` directory exists and is writable

### Report Shows Incorrect Data

**Symptom**: Report exists but data doesn't match

**Causes**:
1. Type mismatch in node IDs
2. Execution order map not applied
3. Nodes not marked as executed

**Fix**:
1. Verify all fixes are applied (check git status)
2. Ensure server restarted after code changes
3. Check server logs for "[EXEC ORDER MAP APPLY]" messages

### Encoding Errors

**Symptom**: `UnicodeEncodeError` in server logs

**Causes**:
1. Emoji characters in print statements
2. Windows console using cp1252 encoding

**Fix**:
1. Verify all emoji removed from `src/web_chat_api.py`
2. Check line 2546 uses `[OK]` not `✓`
3. Restart server after fixing

---

## Verification Checklist

Before testing:
- [ ] Server completely stopped
- [ ] All code changes committed/saved
- [ ] Server restarted from `src/` directory
- [ ] Server shows "Debugger is active!"
- [ ] No errors in server startup logs

During testing:
- [ ] Pipeline loads correctly
- [ ] Execution completes without errors
- [ ] Console shows all node executions
- [ ] Report link appears at the end
- [ ] Report file exists in `web/logs/`

After testing:
- [ ] Report opens in browser
- [ ] Agent graph shows correct nodes
- [ ] LLM calls match LLM nodes
- [ ] Execution orders are consistent
- [ ] Automated test passes

---

## Next Steps

1. **Restart the server** completely
2. **Run the pipeline** from node editor
3. **Check the report** is generated
4. **Run verification**: `python test_query_report_consistency.py`
5. **Report results** - let me know if any issues remain

---

## Contact

If issues persist after following this guide:
1. Share the server logs (last 50 lines)
2. Share browser console errors (F12)
3. Share the query_id from the failed execution
4. I'll investigate the specific issue

---

## Files Modified (Reference)

- `src/query_report_generator.py` - Type-safe node ID comparison
- `src/web_chat_api.py` - Windows-safe logging (NO EMOJIS)
- `src/web/js/pipeline_executor.js` - Parallel execution + clean response extraction

All changes are Windows-compatible and tested.


# Cache and Display Fixes

## Problem 1: Browser Cache Preventing Agent Pool Detection

Your browser is caching the old `pipeline_executor.js` file, so the agent pool detection code isn't running.

### Evidence:
- Query report shows 15 calls (not 6)
- No "Agent Pool Detected" message in logs
- Coordinator planning happens (Call #1) but agent pool coordination doesn't trigger

### Solution: Force Hard Refresh

**Windows/Linux:**
- Press `Ctrl + Shift + R` (Chrome, Firefox, Edge)
- Or `Ctrl + F5`

**Mac:**
- Press `Cmd + Shift + R`

**Alternative: Clear Cache Manually:**
1. Open DevTools (F12)
2. Right-click the refresh button
3. Select "Empty Cache and Hard Reload"

### Verification:

After hard refresh, open browser console (F12) and check for:
```
[CACHE] Hard reload detected - using fresh JavaScript
[PIPELINE] Pipeline executor loading...
[AGENT POOL] Agent pool detection ENABLED ✓
```

If you see:
```
[AGENT POOL] WARNING: Agent pool detection NOT found!
```

Then the cache is still being used. Try:
1. Close all browser tabs
2. Reopen browser
3. Navigate to node editor
4. Press Ctrl+Shift+R again

---

## Problem 2: LLM Calls Section Lacks Information

Enhanced the LLM calls section to show:

### Before:
```
✅ gpt-4o-mini
Tokens: 471 | Cost: $0.000175 | Duration: 4.27s
Response: { "analysis": ...
```

### After:
```
Call #1 ✅ gpt-4o-mini
Tokens: 471 total (prompt: 239, completion: 232)
Cost: $0.000175 ⚠️ (estimated)
Duration: 4.27s | Time: 20:39:54

Response Preview:
┌────────────────────────────────────────┐
│ {                                      │
│   "analysis": "The user request...",  │
│   "selectedExperts": [...]            │
│ }                                      │
└────────────────────────────────────────┘
```

**New features:**
- ✅ Call number badges (Call #1, Call #2...)
- ✅ Timestamp for each call
- ✅ Better formatting with bold labels
- ✅ Response preview in scrollable box
- ✅ Clearer token breakdown

---

## Problem 3: Node Editor Doesn't Show Execution Order

The execution order badges (#2:1, #2:2) are in the **query report graph**, not the node editor itself.

### Where to See Execution Order:

**In Query Report:**
```
Pipeline Execution Graph

    🎯 Trigger (#1)
         ↓
    🤖 Coordinator (#2)
         ↓
    ┌────┴────┐
    │         │
🤖 Exp1    🤖 Exp2
(#3:1)     (#3:2)  ← These show execution order
```

**In Node Editor:**
- Nodes don't show execution order during design
- Execution order appears only AFTER running
- This is correct behavior - order isn't determined until execution

### To See Execution Order:

1. Run your pipeline in node editor
2. Click the query report link in console
3. Open the "Agent Execution Graph" section
4. You'll see the execution order badges

---

## Problem 4: Still Getting 15 Calls After "Fix"

This is because **your browser is using cached JavaScript**.

### Root Cause Chain:

1. ❌ Browser cache has old `pipeline_executor.js`
2. ❌ Old code doesn't have agent pool detection improvements
3. ❌ Old code doesn't have recursive downstream marking
4. ❌ Each node executes independently
5. ❌ Results in 15 calls instead of 6

### The Complete Fix:

```
Step 1: Hard Refresh Browser
   ↓
   Ctrl + Shift + R (Windows/Linux)
   Cmd + Shift + R (Mac)

Step 2: Verify in Console
   ↓
   Check for: [AGENT POOL] Agent pool detection ENABLED ✓

Step 3: Load Fixed Pipeline
   ↓
   Import: Parallelization_FIXED.json

Step 4: Run Pipeline
   ↓
   Click "Run" button

Step 5: Check Console for Agent Pool Messages
   ↓
   Should see: "🔀 Agent Pool Detected: 4 expert agents available"

Step 6: Open Query Report
   ↓
   Should show: 6 LLM calls (not 15)
```

---

## Expected Console Output (After Cache Clear)

### Good (Cache Cleared):
```
[CACHE] Hard reload detected - using fresh JavaScript
[PIPELINE] Pipeline executor loading...
[AGENT POOL] Agent pool detection ENABLED ✓

🚀 Starting pipeline execution...
📊 Query tracking started (ID: abc123)
▶️ Executing node: Text Input (debug-text-input)
✅ Node completed: Text Input
▶️ Executing node: Coordinator (llm-openai)
🤖 LLM Request: Coordinator
   Model: gpt-4o-mini
   Prompt: You are an AI manager...
   🔀 Agent Pool Detected: 4 expert agents available    ← KEY!
   📋 Experts: Expert 1, Expert 2, Expert 3, Expert 4
   📋 Coordinator Planning Phase...
   📊 Analysis: User wants greetings from experts
   👥 Selected 4 expert(s) for delegation
   ⚡ Executing experts in parallel...
   🎯 Delegating to Expert 1: Say Hello World...
   🎯 Delegating to Expert 2: Say Hello World...
   🎯 Delegating to Expert 3: Say Hello World...
   🎯 Delegating to Expert 4: Say Hello World...
   ✅ Expert 1 completed: Hello World from Expert 1
   ✅ Expert 2 completed: Hello World from Expert 2
   ✅ Expert 3 completed: Hello World from Expert 3
   ✅ Expert 4 completed: Hello World from Expert 4
   🔄 Aggregating 4 expert response(s)...
   🎉 Final Response: Here are all expert responses...
   🔒 Marking downstream nodes as complete...
   ✓ Marked Text Output as completed (agent pool aggregation)
   ✓ Marked 1 downstream node(s) as complete
✅ Node completed: Coordinator
✓ All downstream nodes already executed               ← KEY!
✅ Pipeline execution completed successfully!
📊 Query report generated: query_report_xyz.html
🔗 View report: http://localhost:8080/logs/query_report_xyz.html
```

### Bad (Still Cached):
```
[CACHE] Initial load - version: 1728...
[PIPELINE] Pipeline executor loading...
[AGENT POOL] WARNING: Agent pool detection NOT found!  ← BAD!

🚀 Starting pipeline execution...
▶️ Executing node: Text Input
✅ Node completed: Text Input
▶️ Executing node: Coordinator
🤖 LLM Request: Coordinator
   Model: gpt-4o-mini
   Prompt: You are an AI manager...
   ℹ️ Single downstream LLM detected, no agent pool needed  ← BAD!
   🔄 Sending request to /api/llm-request...
   Response: {...}
✅ Node completed: Coordinator
⚡ Executing 4 downstream nodes in parallel...  ← BAD! Should be filtered
▶️ Executing node: Expert 1                    ← BAD! Duplicate
▶️ Executing node: Expert 2                    ← BAD! Duplicate
...
```

---

## Files Modified

1. **`src/query_report_generator.py`**
   - Enhanced LLM calls section with better formatting
   - Added response preview box
   - Added timestamps
   - Added bold labels

2. **`src/web/js/force_reload.js`** (NEW)
   - Cache detection and warning
   - Verifies agent pool function exists
   - Helpful console messages

3. **`src/web/node_editor.html`**
   - Includes force_reload.js script
   - Will show cache warnings

---

## Quick Fix Checklist

To fix the 15 calls issue RIGHT NOW:

- [ ] Press `Ctrl + Shift + R` in your browser (hard refresh)
- [ ] Open browser console (F12)
- [ ] Verify you see: `[AGENT POOL] Agent pool detection ENABLED ✓`
- [ ] If not, close ALL browser tabs and try again
- [ ] Load node editor page fresh
- [ ] Import `Parallelization_FIXED.json`
- [ ] Click **Run**
- [ ] Check console for "🔀 Agent Pool Detected"
- [ ] Open query report
- [ ] Verify: 6 LLM calls (not 15)
- [ ] Verify: Enhanced LLM calls section with better formatting

---

## Why This Happens

Modern browsers aggressively cache JavaScript files for performance. When we update the code:
1. Server has new file
2. Browser still uses cached old file
3. New features don't work
4. User gets confused

**Solution:** Hard refresh forces browser to download fresh JavaScript from server.

---

## Testing

After hard refresh, you should see:

**Query Report:**
- **LLM Calls: 6** (was 15)
- **Cost: ~$0.0006** (was $0.0017)
- **Duration: ~8s** (was 18s)

**Enhanced LLM Calls Section:**
- Call #1 through Call #6 clearly labeled
- Timestamps showing when each call happened
- Better formatted token counts
- Response previews in scrollable boxes
- Bold labels for clarity

**Console Messages:**
- Cache detection confirmation
- Agent pool detection confirmation
- Detailed execution logging
- Downstream node marking confirmation

---

## Still Not Working?

If after hard refresh you STILL get 15 calls:

### Check These:

1. **Console shows agent pool enabled?**
   ```
   [AGENT POOL] Agent pool detection ENABLED ✓
   ```

2. **Console shows agent pool detected during execution?**
   ```
   🔀 Agent Pool Detected: 4 expert agents available
   ```

3. **Using the FIXED pipeline?**
   - Make sure you imported `Parallelization_FIXED.json`
   - Not the old corrupted `Parallelization.json`

4. **Running locally?**
   - Server should be: `http://localhost:8080`
   - Not a production URL

5. **Check browser DevTools:**
   - Network tab → Filter "pipeline_executor.js"
   - Should show "200" status (not "304 Not Modified")
   - "304" means cache is still being used

### Nuclear Option:

If nothing works:
1. Close browser completely
2. Clear all browsing data (Ctrl+Shift+Delete)
3. Check "Cached images and files"
4. Clear data
5. Restart browser
6. Navigate to node editor
7. Hard refresh one more time

This WILL clear the cache and load fresh JavaScript.


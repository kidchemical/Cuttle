# Testing Pipeline Fixes - Comprehensive Verification Guide

## Quick Test Steps

### 1. Execute the Pipeline

1. **Open Node Editor:**
   - Navigate to http://localhost:8080/node_editor.html
   - OR start with: `start_electron.bat`

2. **Load Pipeline:**
   - Load "TEST - Hello World (Parallelization)"
   - Should show: 1 text input → 1 coordinator → 3 experts → 1 aggregator

3. **Execute with Test Prompt:**
   ```
   Plan a fictional moon landing
   ```
   
4. **Wait for Completion:**
   - Watch the console logs
   - Look for execution order assignments

### 2. Verify Console Output

You should see:

```
[EXEC ORDER] Agent pool batch 3 for 3 experts (counter: 3)
[EXEC ORDER]   Expert node OpenAI (ID: 12) assigned #3:1
[EXEC ORDER]   Expert node OpenAI (ID: 24) assigned #3:2
[EXEC ORDER]   Expert node OpenAI (ID: 34) assigned #3:3
```

**✅ Good Sign:** All 3 different node IDs (12, 24, 34)
**❌ Bad Sign:** Same ID repeated (12, 12, 12)

### 3. Run Consistency Test

```bash
cd src\tests
python test_query_report_consistency.py
```

### 4. Expected Test Results

```
================================================================================
QUERY REPORT CONSISTENCY TEST
================================================================================

Step 1: Finding latest query report...
[OK] Found report: query_report_XXXXXX_20251012_HHMMSS.html

Step 2: Parsing query report...
[OK] Agent graph nodes: 9
[OK] LLM calls: 5

Step 3: Verifying internal consistency...
--------------------------------------------------------------------------------

  Nodes in Agent Execution Graph:
    - Text Input (ui-input) - Exec Order: 1
    - OpenAI (llm) - Exec Order: 2
    - OpenAI (llm) - Exec Order: 3:1
    - OpenAI (llm) - Exec Order: 3:2
    - OpenAI (llm) - Exec Order: 3:3
    - OpenAI (llm) - Exec Order: 4
    - ... (skipped nodes)

  LLM Calls in report:
    - Call #1 (Node: #2) - gpt-4o-mini
    - Call #2 (Node: #3:1) - gpt-4o-mini
    - Call #3 (Node: #3:2) - gpt-4o-mini
    - Call #4 (Node: #3:3) - gpt-4o-mini
    - Call #5 (Node: #4) - gpt-4o-mini

Test 1: LLM Calls match LLM nodes in Agent Execution Graph
  LLM nodes in graph: 5
  LLM calls in report: 5
  [PASS] LLM call count matches LLM nodes in graph ✅

Test 2: Every LLM call references a node with matching execution order
  [PASS] All LLM calls reference valid LLM nodes in graph ✅

Test 3: LLM Call numbers are sequential and reference correct nodes
  [PASS] All LLM calls are sequential and reference valid LLM nodes ✅

  LLM Call → Node Mapping:
    Call #1 → Node #2 (OpenAI - Coordinator)
    Call #2 → Node #3:1 (OpenAI - Expert 1: Objectives)
    Call #3 → Node #3:2 (OpenAI - Expert 2: Spacecraft)
    Call #4 → Node #3:3 (OpenAI - Expert 3: Operations)
    Call #5 → Node #4 (OpenAI - Aggregator)

Test 4: Agent Execution Graph shows complete execution flow
  Trigger/Input nodes: 1
  LLM nodes: 5
  Output nodes: 0
  [PASS] Graph shows complete flow (triggers + LLMs) ✅

Test 5: Graph represents complete pipeline structure (executed + skipped nodes)
  Total pipeline nodes (from JSON): 9
    - Executed: 6 (1 input + 5 LLMs)
    - Skipped/Not executed: 3 (1 trigger + 2 outputs)
  Nodes shown in HTML graph: 9
  [PASS] All nodes (executed + skipped) are shown in graph ✅

Test 6: LLM calls should not have duplicate node IDs
  [PASS] All LLM calls use unique node IDs ✅
  Node IDs used: 2, 3:1, 3:2, 3:3, 4

Test 7: Executed LLM nodes should be marked as executed in graph
  [PASS] All LLM nodes that made calls are marked as executed ✅

================================================================================
[SUCCESS] ALL TESTS PASSED - Query report is consistent! ✅
================================================================================
```

## What to Verify in Query Report

### Summary Section ✅
- **LLM Calls:** 5
- **Execution Stages:** 2-3
- **Total Tokens:** ~3000-6000 (depending on responses)
- **Execution Time:** ~25-45s

### Agent Execution Graph ✅

Should show **5 executed LLM nodes:**

```
Text Input (#1) - EXECUTED
   ↓
OpenAI (#2) - Coordinator - EXECUTED
   ↓
[Parallel Layer]
├─ OpenAI (#3:1) - Expert 1 (Objectives) - EXECUTED
├─ OpenAI (#3:2) - Expert 2 (Spacecraft) - EXECUTED  
└─ OpenAI (#3:3) - Expert 3 (Operations) - EXECUTED
   ↓
OpenAI (#4) - Aggregator - EXECUTED
   ↓
[Output nodes] - SKIPPED
```

### LLM Calls Section ✅

**Call #1 (Node: #2)** - Coordinator Planning
- Input: Shows 3 available experts
- Output: JSON selecting 3 experts with different tasks

**Call #2 (Node: #3:1)** - Expert 1
- Input: "Define objectives..."
- Output: Mission objectives

**Call #3 (Node: #3:2)** - Expert 2
- Input: "Design spacecraft..."
- Output: Technical specifications

**Call #4 (Node: #3:3)** - Expert 3
- Input: "Develop operations..."
- Output: Mission timeline

**Call #5 (Node: #4)** - Aggregator
- Input: All 3 expert responses
- Output: Synthesized final plan

## Red Flags to Watch For 🚩

### ❌ Duplicate Node IDs
```
Call #2: Node #3:3
Call #3: Node #3:3  ← DUPLICATE!
Call #4: Node #3:3  ← DUPLICATE!
```

If you see this, the fix didn't work.

### ❌ Wrong Expert Count
```
Coordinator selects: 1 expert
(Should select 3 for "Plan a moon landing")
```

### ❌ Skipped Experts
```
Graph shows:
- Expert 1: EXECUTED
- Expert 2: SKIPPED  ← WRONG!
- Expert 3: SKIPPED  ← WRONG!
```

All selected experts should show EXECUTED.

### ❌ Summary Mismatch
```
Summary: 5 LLM calls
Graph: 3 LLM nodes  ← MISMATCH!
```

Numbers should match.

## Quick Verification Checklist

After executing the pipeline, check:

- [ ] Console shows 3 different node IDs assigned (12, 24, 34)
- [ ] Summary shows 5 LLM calls
- [ ] Graph shows 5 executed LLM nodes (not skipped)
- [ ] LLM Calls show unique node IDs (2, 3:1, 3:2, 3:3, 4)
- [ ] Test passes all 7 checks
- [ ] Coordinator selected 3 experts (not 1)
- [ ] All 3 experts have different tasks (objectives, spacecraft, operations)
- [ ] No hallucinated team members in responses

## Performance Check

With 3 parallel experts:

**Expected Timeline:**
```
Total: ~25-40s
- Coordinator planning: ~3-7s
- 3 Experts (parallel): ~10-18s (longest of the 3)
- Aggregator synthesis: ~10-15s
```

**Not:** ~60-70s (which would indicate sequential execution)

## Troubleshooting

### If test fails:

1. **Clear browser cache** - Ctrl+Shift+R to hard refresh
2. **Check server logs** - Look for [EXEC TRACKING] messages
3. **Verify JavaScript loaded** - Check browser console for errors
4. **Re-run pipeline** - Sometimes first run after code change needs refresh

### If still seeing issues:

1. **Restart server:**
   ```bash
   # Stop the server (Ctrl+C)
   python src/api/web_chat_api.py
   ```

2. **Restart Electron:**
   ```bash
   start_electron.bat
   ```

3. **Check for cached files:**
   - Clear `src/web/logs/` old reports if needed

## Success Criteria

✅ All 7 tests pass
✅ Unique node IDs for all parallel experts
✅ Graph shows all 5 LLM nodes as executed
✅ Coordinator uses all 3 experts for complex task
✅ No hallucinated team members
✅ Parallel execution (faster than sequential)

## Next Steps After Verification

Once verified:
1. ✅ Mark testing complete
2. 📝 Document results
3. 🎉 All 3 query report fixes confirmed working!

---

**Test File:** `test_pipeline_execution.py` (this file provides manual test instructions)  
**Consistency Test:** `src/tests/test_query_report_consistency.py` (automated verification)  
**Date:** October 12, 2025


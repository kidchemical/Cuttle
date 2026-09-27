# Test Enhancements for Query Report Fixes

## Overview

The test `test_query_report_consistency.py` has been enhanced with two new tests to catch the specific issues that were fixed in the query report discrepancies.

## New Tests Added

### Test 6: LLM calls should not have duplicate node IDs

**What it checks:**
- Ensures each LLM call references a unique node ID
- Detects when the same node (e.g., coordinator) is used multiple times

**Example of what it catches:**
```
LLM Calls:
  - Call #1 (Node: #2) ← Coordinator
  - Call #2 (Node: #3:1) ← Expert
  - Call #3 (Node: #2) ← DUPLICATE! Should be #4
```

**Output when issue detected:**
```
Test 6: LLM calls should not have duplicate node IDs
  [FAIL] Found duplicate node IDs:
    - Call #3 uses Node #2 which was already used

  ⚠️ Issue: Same node ID appears multiple times in LLM calls
  This usually means:
    - Coordinator is being used twice (planning + aggregation)
    - Aggregator node is marked as skipped instead of executed
  → Fix: Ensure dedicated aggregator node executes final synthesis
```

**Implementation:**
```python
# Test 6: Check for duplicate node IDs in LLM calls
node_ids_used = []
duplicate_node_ids = []

for call in report_data['llm_calls']:
    node_id = call['node_id']
    if node_id in node_ids_used:
        duplicate_node_ids.append(f"Call #{call['call_number']} uses Node #{node_id} which was already used")
    node_ids_used.append(node_id)

if not duplicate_node_ids:
    print("  [PASS] All LLM calls use unique node IDs")
else:
    print("  [FAIL] Found duplicate node IDs:")
    for dup in duplicate_node_ids:
        print(f"    - {dup}")
    all_tests_passed = False
```

### Test 7: Executed LLM nodes should be marked as executed in graph

**What it checks:**
- Verifies that nodes which made LLM calls are marked as `executed: true` in the graph structure
- Detects when nodes execute but aren't tracked properly

**Example of what it catches:**
```
Graph Structure (JSON):
  - Node 11: executed: true, execution_order: "#2" ✓
  - Node 12: executed: false, execution_order: "#3:1" ✗ (but made an LLM call!)
  - Node 26: executed: false ✗ (but made an LLM call as aggregator!)
```

**Output when issue detected:**
```
Test 7: Executed LLM nodes should be marked as executed in graph
  [FAIL] Found LLM nodes that made calls but are marked as skipped:
    - OpenAI (ID: 12, Exec: #3:1)
    - OpenAI (ID: 26, Exec: #4)

  ⚠️ Issue: These nodes made LLM calls but aren't tracked as executed
  This usually means:
    - Aggregator node made a call but wasn't tracked via /api/record-node-execution
    - Node was marked as 'skipped' or 'complete' instead of 'executed'
  → Fix: Ensure node execution is tracked when LLM calls are made
```

**Implementation:**
```python
# Test 7: LLM nodes that have calls shouldn't be marked as skipped
if json_data and 'graph_structure' in json_data:
    all_nodes = graph_structure.get('nodes', [])
    
    # Get node IDs from LLM calls (these nodes executed)
    executed_node_ids_from_calls = set()
    for call in report_data['llm_calls']:
        node_id_str = call['node_id'].replace('#', '')
        executed_node_ids_from_calls.add(node_id_str)
    
    # Check if these nodes are marked as executed in the graph structure
    skipped_but_executed = []
    
    for node in all_nodes:
        if node.get('category') == 'llm':
            node_exec_order = node.get('execution_order', '').replace('#', '')
            node_display_id = node.get('display_id', '').replace('#', '')
            
            is_in_llm_calls = (node_exec_order in executed_node_ids_from_calls or 
                              node_display_id in executed_node_ids_from_calls)
            
            is_marked_executed = node.get('executed', False)
            
            if is_in_llm_calls and not is_marked_executed:
                skipped_but_executed.append({
                    'node_name': node.get('name'),
                    'node_id': node.get('id'),
                    'exec_order': node_exec_order or node_display_id
                })
    
    if not skipped_but_executed:
        print("  [PASS] All LLM nodes that made calls are marked as executed")
    else:
        print("  [FAIL] Found LLM nodes that made calls but are marked as skipped:")
        for node_info in skipped_but_executed:
            print(f"    - {node_info['node_name']} (ID: {node_info['node_id']}, Exec: #{node_info['exec_order']})")
        all_tests_passed = False
```

## Test Coverage Summary

The test suite now includes **7 comprehensive tests**:

1. ✅ LLM call count matches LLM nodes in graph
2. ✅ Every LLM call references a valid node
3. ✅ LLM call numbers are sequential
4. ✅ Graph shows complete execution flow
5. ✅ Graph shows all pipeline nodes (executed + skipped)
6. ✅ **NEW**: No duplicate node IDs in LLM calls
7. ✅ **NEW**: Executed LLM nodes are properly tracked

## Issues Caught by Enhanced Tests

### Issue #1: Expert Node Reference Bug
**Caught by:** Test 3, Test 7
- Test 3 detects node #3:1 not in graph
- Test 7 detects node 12 made a call but not marked executed

### Issue #2: Aggregator Node Skipped Bug
**Caught by:** Test 6, Test 7
- Test 6 detects duplicate node ID (#2 used twice)
- Test 7 detects aggregator node made a call but not marked executed

## Running the Test

```bash
cd src\tests
python test_query_report_consistency.py
```

## Expected Output (After Fixes)

```
================================================================================
QUERY REPORT CONSISTENCY TEST
================================================================================

Step 1: Finding latest query report...
[OK] Found report: query_report_XXXXXX_YYYYMMDD_HHMMSS.html

Step 2: Parsing query report...
[OK] Agent graph nodes: 9
[OK] LLM calls: 3

Step 3: Verifying internal consistency...
--------------------------------------------------------------------------------

  Nodes in Agent Execution Graph:
    - Text Input (ui-input) - Exec Order: 1
    - OpenAI (llm) - Exec Order: 2
    - OpenAI (llm) - Exec Order: 3:1
    - OpenAI (llm) - Exec Order: 4
    - ... (skipped nodes)

  LLM Calls in report:
    - Call #1 (Node: #2) - gpt-4o-mini
    - Call #2 (Node: #3:1) - gpt-4o-mini
    - Call #3 (Node: #4) - gpt-4o-mini

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
    Call #3 → Node #4 (OpenAI - Aggregator)

Test 4: Agent Execution Graph shows complete execution flow
  Trigger/Input nodes: 1
  LLM nodes: 3
  Output nodes: 0
  [PASS] Graph shows complete flow (triggers + LLMs)

Test 5: Graph represents complete pipeline structure (executed + skipped nodes)
  Total pipeline nodes (from JSON): 9
    - Executed: 4
    - Skipped/Not executed: 5
  Nodes shown in HTML graph: 9
  [PASS] All nodes (executed + skipped) are shown in graph

Test 6: LLM calls should not have duplicate node IDs
  [PASS] All LLM calls use unique node IDs
  Node IDs used: 2, 3:1, 4

Test 7: Executed LLM nodes should be marked as executed in graph
  [PASS] All LLM nodes that made calls are marked as executed

================================================================================
[SUCCESS] ALL TESTS PASSED - Query report is consistent!
================================================================================
```

## Benefits

1. ✅ **Catches duplicate node usage** - Detects when coordinator is used multiple times
2. ✅ **Catches execution tracking bugs** - Detects when nodes execute but aren't tracked
3. ✅ **Clear error messages** - Provides specific guidance on what went wrong
4. ✅ **Preventive testing** - Catches regressions before they reach production
5. ✅ **Comprehensive coverage** - Tests both HTML report and JSON data structure

## Maintenance

When adding new features or modifying the query report system:
1. Run `test_query_report_consistency.py` after changes
2. If adding new node types or execution patterns, update the test accordingly
3. Keep the test messages clear and actionable for future debugging

## Related Files

- `src/tests/test_query_report_consistency.py` - Enhanced test file
- `src/docs/fixes/QUERY_REPORT_LLM_GRAPH_MISMATCH_FIX.md` - First issue fix
- `src/docs/fixes/QUERY_REPORT_AGGREGATOR_NODE_FIX.md` - Second issue fix
- `src/docs/fixes/VERIFY_QUERY_REPORT_FIX.md` - Verification guide

## Date

October 12, 2025

## Status

✅ ENHANCED - Tests now catch both fixed issues


#!/usr/bin/env python3
"""
Test: LLM Calls Must Match Executed LLM Nodes

CRITICAL BUG THIS TEST CATCHES:
    Nodes marked as 'executed: true' in the graph but which never made LLM calls.
    This happens when agent pool marks downstream nodes as complete - they get
    tracked as executed even though they were skipped.
    
    Result: Graph shows "5 LLM nodes executed" but only 3 LLM calls in report!
    
CORRECT BEHAVIOR:
    Only nodes that ACTUALLY EXECUTED (made LLM calls, processed data) should
    be marked as 'executed: true'. Nodes that were skipped/pre-filled should
    NOT be marked as executed.
"""

import json
import sys
from pathlib import Path

# Fix Windows console encoding
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except:
        pass

def get_latest_query_data() -> Path:
    """Find the most recent query data JSON file"""
    logs_dir = Path(__file__).parent.parent / "web" / "logs"
    
    if not logs_dir.exists():
        print(f"[X] Logs directory not found: {logs_dir}")
        return None
    
    # Find all query data JSON files
    json_files = list(logs_dir.glob("query_data_*.json"))
    
    if not json_files:
        print(f"[X] No query data files found in {logs_dir}")
        return None
    
    # Sort by modification time (most recent first)
    latest_file = max(json_files, key=lambda p: p.stat().st_mtime)
    return latest_file

def test_llm_calls_vs_executed_nodes():
    """Test that LLM calls count matches executed LLM nodes count"""
    
    print("=" * 80)
    print("TEST: LLM CALLS VS EXECUTED LLM NODES")
    print("=" * 80)
    print()
    
    # Load latest query data
    json_file = get_latest_query_data()
    if not json_file:
        print("[X] Could not find query data file")
        return False
    
    print(f"[i] Loading: {json_file.name}")
    
    with open(json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # Extract data
    llm_calls = data.get('llm_calls', [])
    graph_structure = data.get('graph_structure', {})
    nodes = graph_structure.get('nodes', [])
    
    # Count executed LLM nodes
    executed_llm_nodes = [
        node for node in nodes 
        if node.get('category') == 'llm' and node.get('executed', False)
    ]
    
    print()
    print("Data Summary:")
    print("-" * 80)
    print(f"  Total nodes in pipeline: {len(nodes)}")
    print(f"  LLM calls made: {len(llm_calls)}")
    print(f"  LLM nodes marked as executed: {len(executed_llm_nodes)}")
    print()
    
    # Show LLM calls
    print("LLM Calls (actual API calls made):")
    for i, call in enumerate(llm_calls, 1):
        node_id = call.get('node_id', 'Unknown')
        model = call.get('model', 'Unknown')
        print(f"  {i}. Node {node_id} - {model}")
    print()
    
    # Show executed LLM nodes
    print("LLM Nodes marked as 'executed: true':")
    for node in executed_llm_nodes:
        node_id = node.get('id')
        name = node.get('name', 'Unknown')
        exec_order = node.get('execution_order', 'N/A')
        print(f"  - Node {node_id} ({name}) - Order: {exec_order}")
    print()
    
    # THE TEST
    print("=" * 80)
    print("TEST RESULT:")
    print("=" * 80)
    
    if len(llm_calls) == len(executed_llm_nodes):
        print(f"[PASS] ✓ LLM calls ({len(llm_calls)}) matches executed LLM nodes ({len(executed_llm_nodes)})")
        print()
        return True
    else:
        print(f"[FAIL] ✗ MISMATCH DETECTED!")
        print(f"  LLM calls made: {len(llm_calls)}")
        print(f"  LLM nodes marked executed: {len(executed_llm_nodes)}")
        print(f"  Discrepancy: {abs(len(llm_calls) - len(executed_llm_nodes))} nodes")
        print()
        
        if len(executed_llm_nodes) > len(llm_calls):
            print("⚠️  ROOT CAUSE:")
            print("  More nodes marked as executed than LLM calls made.")
            print("  This means some nodes are marked 'executed: true' even though")
            print("  they never actually ran (didn't make LLM calls).")
            print()
            print("  LIKELY CAUSE:")
            print("  - Agent pool's markDownstreamComplete() is calling")
            print("    /api/record-node-execution for skipped nodes")
            print("  - These nodes receive pre-filled data but don't actually execute")
            print()
            print("  FIX:")
            print("  - Remove tracking call from markDownstreamComplete()")
            print("  - Only track nodes that ACTUALLY execute (make LLM calls)")
        else:
            print("⚠️  ROOT CAUSE:")
            print("  More LLM calls than executed nodes in graph.")
            print("  This means some LLM calls were made but nodes weren't tracked.")
            print()
            print("  LIKELY CAUSE:")
            print("  - Missing /api/record-node-execution calls")
            print("  - Expert nodes or aggregator not being tracked")
        
        print()
        return False

if __name__ == "__main__":
    try:
        success = test_llm_calls_vs_executed_nodes()
        exit(0 if success else 1)
    except Exception as e:
        print(f"\n[ERROR] Test crashed: {e}")
        import traceback
        traceback.print_exc()
        exit(1)


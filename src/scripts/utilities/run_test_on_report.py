#!/usr/bin/env python3
"""Run consistency test on a specific report"""

import sys
from pathlib import Path

# Script is in src/scripts/utilities/, get project root
script_dir = Path(__file__).parent
project_root = script_dir.parent.parent.parent

# Update the test to use a specific report
report_path = project_root / "src" / "web" / "logs" / "query_report_0f62e005_20251012_135937.html"

# Import test functions
sys.path.insert(0, str(project_root / 'src' / 'tests'))
from test_query_report_consistency import parse_query_report, load_query_data_json

def run_test_on_specific_report(report_path):
    """Run all consistency tests on a specific report"""
    print("=" * 80)
    print(f"TESTING REPORT: {report_path.name}")
    print("=" * 80)
    print()
    
    # Parse report
    report_data = parse_query_report(report_path)
    json_data = load_query_data_json(report_path)
    
    print(f"Agent graph nodes: {len(report_data['agent_graph_nodes'])}")
    print(f"LLM calls: {len(report_data['llm_calls'])}")
    print()
    
    all_tests_passed = True
    
    # Test 1: LLM call count matches graph
    print("Test 1: LLM Calls match LLM nodes in Agent Execution Graph")
    graph_llm_nodes = [node for node in report_data['agent_graph_nodes'] if node['category'] == 'llm']
    print(f"  LLM nodes in graph: {len(graph_llm_nodes)}")
    print(f"  LLM calls in report: {len(report_data['llm_calls'])}")
    
    if len(report_data['llm_calls']) == len(graph_llm_nodes):
        print("  [PASS] LLM call count matches LLM nodes in graph ✅")
    else:
        print("  [FAIL] LLM call count does NOT match LLM nodes in graph ❌")
        all_tests_passed = False
    print()
    
    # Test 6: No duplicate node IDs
    print("Test 6: LLM calls should not have duplicate node IDs")
    node_ids = [call['node_id'] for call in report_data['llm_calls']]
    duplicates = [nid for nid in node_ids if node_ids.count(nid) > 1]
    
    if not duplicates:
        print(f"  [PASS] All LLM calls use unique node IDs ✅")
        print(f"  Node IDs used: {', '.join(node_ids)}")
    else:
        print(f"  [FAIL] Found duplicate node IDs: {set(duplicates)} ❌")
        all_tests_passed = False
    print()
    
    # Test 7: Executed nodes properly tracked
    print("Test 7: Executed LLM nodes should be marked as executed in graph")
    
    if json_data and 'graph_structure' in json_data:
        all_nodes = json_data['graph_structure'].get('nodes', [])
        llm_nodes = [n for n in all_nodes if n.get('category') == 'llm']
        executed_llm = [n for n in llm_nodes if n.get('executed', False)]
        
        print(f"  Total LLM nodes: {len(llm_nodes)}")
        print(f"  Executed LLM nodes: {len(executed_llm)}")
        print(f"  LLM calls in report: {len(report_data['llm_calls'])}")
        
        if len(executed_llm) == len(report_data['llm_calls']):
            print(f"  [PASS] All LLM nodes that made calls are marked as executed ✅")
        else:
            print(f"  [FAIL] Mismatch between executed nodes and LLM calls ❌")
            all_tests_passed = False
    print()
    
    # Show mapping
    print("LLM Call → Node Mapping:")
    for call in report_data['llm_calls']:
        print(f"  Call #{call['call_number']} → Node #{call['node_id']}")
    print()
    
    print("=" * 80)
    if all_tests_passed:
        print("✅ ALL KEY TESTS PASSED - Report is accurate!")
    else:
        print("❌ SOME TESTS FAILED - Issues found")
    print("=" * 80)
    
    return all_tests_passed

if __name__ == "__main__":
    success = run_test_on_specific_report(report_path)
    sys.exit(0 if success else 1)


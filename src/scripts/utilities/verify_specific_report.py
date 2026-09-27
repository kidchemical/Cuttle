#!/usr/bin/env python3
"""Verify a specific query report"""

import sys
from pathlib import Path

# Script is in src/scripts/utilities/, get project root
script_dir = Path(__file__).parent
project_root = script_dir.parent.parent.parent

# Import the test functions
sys.path.insert(0, str(project_root / 'src' / 'tests'))
from test_query_report_consistency import parse_query_report, load_query_data_json

def verify_report(report_file):
    """Verify a specific report file"""
    report_path = Path(report_file)
    
    if not report_path.exists():
        print(f"[ERROR] Report not found: {report_path}")
        return False
    
    print("=" * 80)
    print(f"VERIFYING REPORT: {report_path.name}")
    print("=" * 80)
    print()
    
    # Parse the report
    report_data = parse_query_report(report_path)
    json_data = load_query_data_json(report_path)
    
    print(f"Agent graph nodes: {len(report_data['agent_graph_nodes'])}")
    print(f"LLM calls: {len(report_data['llm_calls'])}")
    print()
    
    # Check node IDs
    print("LLM Call Node IDs:")
    node_ids = []
    for call in report_data['llm_calls']:
        node_id = call['node_id']
        node_ids.append(node_id)
        print(f"  Call #{call['call_number']}: Node {node_id}")
    
    print()
    
    # Check for duplicates
    unique_ids = set(node_ids)
    if len(unique_ids) == len(node_ids):
        print(f"✅ All {len(node_ids)} node IDs are unique!")
    else:
        print(f"❌ Found duplicate node IDs!")
        print(f"   Total calls: {len(node_ids)}")
        print(f"   Unique IDs: {len(unique_ids)}")
    
    print()
    
    # Check executed nodes from JSON
    if json_data and 'graph_structure' in json_data:
        nodes = json_data['graph_structure'].get('nodes', [])
        llm_nodes = [n for n in nodes if n.get('category') == 'llm']
        executed_llm_nodes = [n for n in llm_nodes if n.get('executed', False)]
        
        print("Graph Structure (from JSON):")
        print(f"  Total LLM nodes: {len(llm_nodes)}")
        print(f"  Executed LLM nodes: {len(executed_llm_nodes)}")
        print()
        
        print("Executed LLM Nodes:")
        for node in executed_llm_nodes:
            exec_order = node.get('execution_order', node.get('display_id', '?'))
            print(f"  - Node {node.get('id')} ({node.get('name')}): {exec_order}")
        
        print()
        
        # Verify counts match
        if len(executed_llm_nodes) == len(report_data['llm_calls']):
            print(f"✅ LLM node count matches! ({len(executed_llm_nodes)} executed = {len(report_data['llm_calls'])} calls)")
        else:
            print(f"❌ Count mismatch! {len(executed_llm_nodes)} executed nodes != {len(report_data['llm_calls'])} calls")
    
    print()
    print("=" * 80)
    
    # Determine success
    all_unique = len(unique_ids) == len(node_ids)
    counts_match = len(executed_llm_nodes) == len(report_data['llm_calls']) if json_data else False
    
    if all_unique and counts_match:
        print("✅ VERIFICATION PASSED - Report is accurate!")
        return True
    else:
        print("❌ VERIFICATION FAILED - Issues found")
        return False

if __name__ == "__main__":
    report_file = sys.argv[1] if len(sys.argv) > 1 else "src/web/logs/query_report_0f62e005_20251012_135937.html"
    success = verify_report(report_file)
    sys.exit(0 if success else 1)


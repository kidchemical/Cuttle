#!/usr/bin/env python3
"""Verify all query reports for consistency"""

from test_query_report_consistency import parse_query_report
from pathlib import Path

reports = [
    'query_report_8af27785_20251012_173157.html',
    'query_report_82f04417_20251012_173338.html',
    'query_report_5e2dca30_20251012_173558.html',
]

print("=" * 100)
print("COMPREHENSIVE QUERY REPORT VERIFICATION")
print("=" * 100)

all_passed = True

# Updated path: now in src/tests/, need to go up to root
logs_dir = Path(__file__).parent.parent.parent / "web" / "logs"

for report_name in reports:
    report_path = logs_dir / report_name
    if not report_path.exists():
        print(f"\n[SKIP] {report_name} - File not found")
        continue
    
    print(f"\n{report_name}")
    print("-" * 100)
    
    try:
        result = parse_query_report(report_path)
        
        # Show nodes
        print(f"  Agent Graph: {len(result['agent_graph_nodes'])} nodes")
        for node in result['agent_graph_nodes']:
            print(f"    - {node['name']:25} ({node['category']:8}) - Exec Order: {node['exec_order']}")
        
        # Show LLM calls
        print(f"\n  LLM Calls: {len(result['llm_calls'])} calls")
        for call in result['llm_calls']:
            print(f"    - Call #{call['call_number']} (Node: #{call['node_id']:5}) - {call['model']}")
        
        # Verify
        llm_nodes = [n for n in result['agent_graph_nodes'] if n['category'] == 'llm']
        match = len(llm_nodes) == len(result['llm_calls'])
        
        print(f"\n  Consistency Check:")
        print(f"    LLM nodes in graph: {len(llm_nodes)}")
        print(f"    LLM calls in report: {len(result['llm_calls'])}")
        print(f"    Result: {'[PASS]' if match else '[FAIL]'}")
        
        if not match:
            all_passed = False
            
    except Exception as e:
        print(f"  [ERROR] {e}")
        all_passed = False

print("\n" + "=" * 100)
if all_passed:
    print("[SUCCESS] ALL REPORTS ARE CONSISTENT!")
else:
    print("[FAILURE] Some reports have issues")
print("=" * 100)


#!/usr/bin/env python3
"""Quick check of the parallel execution report"""

from test_query_report_consistency import parse_query_report
from pathlib import Path

# Check the parallel execution report
# Updated path: now in src/tests/, need to go up to root
report_path = Path(__file__).parent.parent.parent / "web" / "logs" / "query_report_82f04417_20251012_173338.html"
print(f"\nAnalyzing: {report_path.name}")
print("=" * 80)

result = parse_query_report(report_path)

print(f"\nAgent Graph Nodes: {len(result['agent_graph_nodes'])}")
for node in result['agent_graph_nodes']:
    print(f"  - {node['name']:20} ({node['category']:8}) - Exec Order: {node['exec_order']}")

print(f"\nLLM Calls: {len(result['llm_calls'])}")
for call in result['llm_calls']:
    print(f"  - Call #{call['call_number']} (Node: #{call['node_id']:4}) - {call['model']}")

# Verify consistency
llm_nodes = [n for n in result['agent_graph_nodes'] if n['category'] == 'llm']
print(f"\n" + "=" * 80)
print(f"LLM nodes in graph: {len(llm_nodes)}")
print(f"LLM calls in report: {len(result['llm_calls'])}")

if len(llm_nodes) == len(result['llm_calls']):
    print("[PASS] Perfect match!")
else:
    print("[FAIL] Mismatch!")


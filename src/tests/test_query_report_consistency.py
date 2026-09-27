#!/usr/bin/env python3
"""
Test Query Report Consistency
Verifies that the Agent Execution Graph matches the LLM Calls section and the pipeline configuration.
"""

import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Set

# Fix Windows console encoding for emoji support
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except:
        pass

try:
    from bs4 import BeautifulSoup
except ImportError:
    print("[!] BeautifulSoup4 not installed. Installing...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "beautifulsoup4"])
    from bs4 import BeautifulSoup

def load_pipeline_config(pipeline_name: str = "TEST - Hello World (Parallelization)") -> Dict:
    """Load pipeline configuration from saved pipelines"""
    # Updated path: now in src/tests/, need to go up one level to src/
    pipelines_file = Path(__file__).parent.parent / "web" / "data" / "saved_pipelines.json"
    
    if not pipelines_file.exists():
        print(f"[X] Pipeline file not found: {pipelines_file}")
        return None
    
    with open(pipelines_file, 'r', encoding='utf-8') as f:
        pipelines = json.load(f)
    
    for pipeline in pipelines:
        if pipeline.get('name') == pipeline_name:
            return pipeline
    
    print(f"[X] Pipeline '{pipeline_name}' not found")
    return None

def get_latest_query_report() -> Path:
    """Find the most recent query report HTML file"""
    # Updated path: now in src/tests/, need to go up one level to src/
    logs_dir = Path(__file__).parent.parent / "web" / "logs"
    
    if not logs_dir.exists():
        print(f"[X] Logs directory not found: {logs_dir}")
        return None
    
    # Find all query report HTML files
    report_files = list(logs_dir.glob("query_report_*.html"))
    
    if not report_files:
        print(f"[X] No query reports found in {logs_dir}")
        print(f"[!] Please run a pipeline in the node editor first to generate a query report")
        print(f"[!] Then run this test again")
        return None
    
    # Sort by modification time (most recent first)
    latest_report = max(report_files, key=lambda p: p.stat().st_mtime)
    return latest_report

def load_query_data_json(report_path: Path) -> Dict:
    """Load the corresponding JSON data file for a query report"""
    # Extract query ID from report filename
    # Format: query_report_<query_id>_<timestamp>.html
    filename = report_path.stem  # Remove .html
    parts = filename.split('_')
    
    if len(parts) < 3:
        print(f"[X] Could not extract query ID from filename: {filename}")
        return None
    
    query_id = parts[2]  # query_report_<ID>_timestamp
    
    # Find corresponding JSON file
    json_pattern = f"query_data_{query_id}_*.json"
    json_files = list(report_path.parent.glob(json_pattern))
    
    if not json_files:
        print(f"[!] No JSON data file found for query {query_id}")
        return None
    
    json_file = json_files[0]
    
    try:
        with open(json_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"[X] Error loading JSON file: {e}")
        return None

def parse_query_report(report_path: Path) -> Dict:
    """Parse query report HTML and extract relevant information"""
    with open(report_path, 'r', encoding='utf-8') as f:
        html_content = f.read()
    
    soup = BeautifulSoup(html_content, 'html.parser')
    
    result = {
        'report_path': str(report_path),
        'agent_graph_nodes': [],
        'llm_calls': [],
        'execution_order_map': {}
    }
    
    # Extract Agent Execution Graph nodes
    agent_graph = soup.find('div', class_='agent-graph')
    if agent_graph:
        flow_nodes = agent_graph.find_all('div', class_='flow-node')
        for node_div in flow_nodes:
            node_text = node_div.get_text(strip=True)
            # Extract node name and execution order
            # Format: "🎯 Text Input ⏱️ 0.00s #1"
            node_info = {
                'text': node_text,
                'category': None,
                'name': None,
                'exec_order': None
            }
            
            # Extract execution order (e.g., #1, #2, #3:1)
            exec_order_match = re.search(r'#(\d+(?::\d+)?)', node_text)
            if exec_order_match:
                node_info['exec_order'] = exec_order_match.group(1)
            
            # Extract node name (between emoji and timing info)
            # Remove emojis and timing/exec order info
            clean_text = re.sub(r'[🎯🤖🛠️📤📦🔧📥]', '', node_text)
            clean_text = re.sub(r'⏱️\s*[\d.]+s', '', clean_text)
            clean_text = re.sub(r'#\d+(?::\d+)?', '', clean_text)
            node_info['name'] = clean_text.strip()
            
            # Determine category from classes
            classes = node_div.get('class', [])
            if 'trigger' in classes:
                node_info['category'] = 'trigger'
            elif 'llm' in classes:
                node_info['category'] = 'llm'
            elif 'tool' in classes:
                node_info['category'] = 'tool'
            elif 'output' in classes:
                node_info['category'] = 'output'
            elif 'input' in classes:
                node_info['category'] = 'input'
            
            result['agent_graph_nodes'].append(node_info)
    
    # Extract LLM Calls
    llm_section = soup.find('div', id='llm-section')
    if llm_section:
        # Find all stage items (LLM calls are in stage-item divs)
        call_items = llm_section.find_all('div', class_='stage-item')
        for call_div in call_items:
            call_text = call_div.get_text()
            
            # Extract call number and node ID
            # Format: "Call #1 (Node: #1) ✅ gpt-4o-mini"
            call_info = {
                'text': call_text,
                'call_number': None,
                'node_id': None,
                'model': None
            }
            
            # Extract call number
            call_match = re.search(r'Call #(\d+)', call_text)
            if call_match:
                call_info['call_number'] = call_match.group(1)
            
            # Extract node ID
            node_match = re.search(r'Node:\s*#(\d+(?::\d+)?)', call_text)
            if node_match:
                call_info['node_id'] = node_match.group(1)
            
            # Extract model
            if 'gpt-' in call_text:
                model_match = re.search(r'(gpt-[\w-]+)', call_text)
                if model_match:
                    call_info['model'] = model_match.group(1)
            elif 'claude-' in call_text:
                model_match = re.search(r'(claude-[\w-]+)', call_text)
                if model_match:
                    call_info['model'] = model_match.group(1)
            
            result['llm_calls'].append(call_info)
    
    return result

def analyze_pipeline_nodes(pipeline: Dict) -> Dict:
    """Analyze pipeline configuration to get expected execution"""
    nodes = pipeline.get('nodes', [])
    connections = pipeline.get('connections', [])
    
    # Build adjacency list
    adjacency = {}
    for conn in connections:
        from_idx = conn['from']
        to_idx = conn['to']
        if from_idx not in adjacency:
            adjacency[from_idx] = []
        adjacency[from_idx].append(to_idx)
    
    # Find trigger nodes (no incoming connections)
    incoming = set()
    for conn in connections:
        incoming.add(conn['to'])
    
    trigger_indices = []
    for idx in range(len(nodes)):
        if idx not in incoming and idx in adjacency:  # Has outgoing but no incoming
            trigger_indices.append(idx)
    
    # Get reachable nodes from triggers
    reachable = set()
    queue = trigger_indices.copy()
    visited = set(trigger_indices)
    
    while queue:
        current = queue.pop(0)
        reachable.add(current)
        
        if current in adjacency:
            for next_node in adjacency[current]:
                if next_node not in visited:
                    visited.add(next_node)
                    queue.append(next_node)
    
    # Extract node info
    result = {
        'total_nodes': len(nodes),
        'trigger_nodes': [],
        'llm_nodes': [],
        'output_nodes': [],
        'reachable_nodes': [],
        'disconnected_nodes': []
    }
    
    for idx in range(len(nodes)):
        node = nodes[idx]
        node_info = {
            'index': idx,
            'name': node.get('name'),
            'type': node.get('type'),
            'category': node.get('category')
        }
        
        if idx in reachable:
            result['reachable_nodes'].append(node_info)
            
            if node.get('category') == 'trigger' or node.get('category') == 'input':
                result['trigger_nodes'].append(node_info)
            elif node.get('category') == 'llm':
                result['llm_nodes'].append(node_info)
            elif node.get('category') == 'output':
                result['output_nodes'].append(node_info)
        else:
            result['disconnected_nodes'].append(node_info)
    
    return result

def run_consistency_test():
    """Run the consistency test"""
    print("=" * 80)
    print("QUERY REPORT CONSISTENCY TEST")
    print("=" * 80)
    print()
    
    # Step 1: Get latest query report
    print("Step 1: Finding latest query report...")
    report_path = get_latest_query_report()
    if not report_path:
        print("[X] TEST FAILED: Could not find query report")
        return False
    print(f"[OK] Found report: {report_path.name}")
    print()
    
    # Step 2: Parse query report
    print("Step 2: Parsing query report...")
    report_data = parse_query_report(report_path)
    print(f"[OK] Agent graph nodes: {len(report_data['agent_graph_nodes'])}")
    print(f"[OK] LLM calls: {len(report_data['llm_calls'])}")
    print()
    
    # Step 3: Verify internal consistency
    print("Step 3: Verifying internal consistency...")
    print("-" * 80)
    
    all_tests_passed = True
    
    # Show what we found in the graph
    print("\n  Nodes in Agent Execution Graph:")
    for node in report_data['agent_graph_nodes']:
        print(f"    - {node['name']} ({node['category']}) - Exec Order: {node['exec_order']}")
    
    # Show what we found in LLM calls
    print("\n  LLM Calls in report:")
    for call in report_data['llm_calls']:
        print(f"    - Call #{call['call_number']} (Node: #{call['node_id']}) - {call['model']}")
    
    # Test 1: LLM calls should match LLM nodes in graph
    print("\nTest 1: LLM Calls match LLM nodes in Agent Execution Graph")
    graph_llm_nodes = [node for node in report_data['agent_graph_nodes'] if node['category'] == 'llm']
    
    print(f"  LLM nodes in graph: {len(graph_llm_nodes)}")
    print(f"  LLM calls in report: {len(report_data['llm_calls'])}")
    
    if len(report_data['llm_calls']) == len(graph_llm_nodes):
        print("  [PASS] LLM call count matches LLM nodes in graph")
    else:
        print("  [FAIL] LLM call count does NOT match LLM nodes in graph")
        all_tests_passed = False
    
    # Test 2: Every LLM call should reference a node in the graph
    print("\nTest 2: Every LLM call references a node with matching execution order")
    
    # Build map of execution orders in graph
    graph_exec_orders = {node['exec_order']: node for node in report_data['agent_graph_nodes'] if node['exec_order']}
    
    mismatches = []
    for call in report_data['llm_calls']:
        call_node_id = call['node_id']
        if call_node_id not in graph_exec_orders:
            mismatches.append(f"Call #{call['call_number']} references Node #{call_node_id} which is not in graph")
        else:
            graph_node = graph_exec_orders[call_node_id]
            if graph_node['category'] != 'llm':
                mismatches.append(f"Call #{call['call_number']} references Node #{call_node_id} which is '{graph_node['category']}', not 'llm'")
    
    if not mismatches:
        print("  [PASS] All LLM calls reference valid LLM nodes in graph")
    else:
        print("  [FAIL] Found mismatches:")
        for mismatch in mismatches:
            print(f"    - {mismatch}")
        all_tests_passed = False
    
    # Test 3: LLM Call numbers are sequential and reference valid LLM nodes
    print("\nTest 3: LLM Call numbers are sequential and reference correct nodes")
    
    # Build a map of which nodes are LLMs by their execution order
    llm_nodes_by_exec_order = {}
    for node in report_data['agent_graph_nodes']:
        if node['category'] == 'llm' and node['exec_order']:
            llm_nodes_by_exec_order[node['exec_order']] = node
    
    call_issues = []
    for idx, call in enumerate(report_data['llm_calls'], 1):
        call_num = call['call_number']
        node_id = call['node_id']
        
        # Check 1: Call numbers should be sequential (1, 2, 3...)
        if call_num != str(idx):
            call_issues.append(f"Call #{call_num} is not sequential (expected #{idx})")
        
        # Check 2: The referenced node should be an LLM node in the graph
        if node_id not in llm_nodes_by_exec_order:
            call_issues.append(f"Call #{call_num} references Node #{node_id} which is not an LLM node in graph")
    
    if not call_issues:
        print("  [PASS] All LLM calls are sequential and reference valid LLM nodes")
        
        # Show the mapping for clarity
        print("\n  LLM Call → Node Mapping:")
        for call in report_data['llm_calls']:
            corresponding_node = llm_nodes_by_exec_order.get(call['node_id'])
            if corresponding_node:
                print(f"    Call #{call['call_number']} → Node #{call['node_id']} ({corresponding_node['name']})")
    else:
        print("  [FAIL] Found issues with LLM call numbering:")
        for issue in call_issues:
            print(f"    - {issue}")
        all_tests_passed = False
    
    # Test 4: Graph should show complete execution flow
    print("\nTest 4: Agent Execution Graph shows complete execution flow")
    
    trigger_nodes = [node for node in report_data['agent_graph_nodes'] if node['category'] in ['trigger', 'input']]
    llm_nodes = [node for node in report_data['agent_graph_nodes'] if node['category'] == 'llm']
    output_nodes = [node for node in report_data['agent_graph_nodes'] if node['category'] == 'output']
    
    print(f"  Trigger/Input nodes: {len(trigger_nodes)}")
    print(f"  LLM nodes: {len(llm_nodes)}")
    print(f"  Output nodes: {len(output_nodes)}")
    
    has_triggers = len(trigger_nodes) > 0
    has_llms = len(llm_nodes) > 0
    
    if has_triggers and has_llms:
        print("  [PASS] Graph shows complete flow (triggers + LLMs)")
    elif has_llms:
        print("  [PARTIAL PASS] Graph shows LLMs but no trigger nodes")
    else:
        print("  [FAIL] Graph is missing critical nodes")
        all_tests_passed = False
    
    # Test 5: Check if graph shows ALL pipeline nodes (including skipped ones)
    print("\nTest 5: Graph represents complete pipeline structure (executed + skipped nodes)")
    
    # Load the JSON data to get the full graph structure
    json_data = load_query_data_json(report_path)
    
    if json_data and 'graph_structure' in json_data:
        graph_structure = json_data['graph_structure']
        all_pipeline_nodes = graph_structure.get('nodes', [])
        
        # Separate executed vs skipped nodes
        executed_pipeline_nodes = [n for n in all_pipeline_nodes if n.get('executed', False)]
        skipped_pipeline_nodes = [n for n in all_pipeline_nodes if not n.get('executed', False)]
        
        # Get node IDs shown in HTML graph
        html_graph_node_names = {node['name'] for node in report_data['agent_graph_nodes']}
        
        print(f"  Total pipeline nodes (from JSON): {len(all_pipeline_nodes)}")
        print(f"    - Executed: {len(executed_pipeline_nodes)}")
        print(f"    - Skipped/Not executed: {len(skipped_pipeline_nodes)}")
        print(f"  Nodes shown in HTML graph: {len(report_data['agent_graph_nodes'])}")
        
        # Check if skipped nodes are missing from graph
        if skipped_pipeline_nodes:
            skipped_node_names = [n.get('name', 'Unknown') for n in skipped_pipeline_nodes]
            missing_from_graph = []
            
            for node_name in skipped_node_names:
                if node_name not in html_graph_node_names:
                    missing_from_graph.append(node_name)
            
            if missing_from_graph:
                print(f"\n  [FAIL] {len(missing_from_graph)} skipped node(s) not shown in graph:")
                for name in missing_from_graph:
                    print(f"    - {name}")
                print("\n  ⚠️ Issue: Agent pool or pipeline logic marked these nodes as complete")
                print("  but they don't appear in the graph because executed: False")
                print("  → Graph should show these nodes with 'skipped' or 'bypassed' visual indicator")
                all_tests_passed = False
            else:
                print("  [PASS] All skipped nodes are represented in graph")
        else:
            print("  [PASS] No skipped nodes (all pipeline nodes were executed)")
    else:
        print("  [SKIP] Could not load JSON data to verify complete structure")
    
    # Test 6: Check for duplicate node IDs in LLM calls
    print("\nTest 6: LLM calls should not have duplicate node IDs")
    
    node_ids_used = []
    duplicate_node_ids = []
    
    for call in report_data['llm_calls']:
        node_id = call['node_id']
        if node_id in node_ids_used:
            duplicate_node_ids.append(f"Call #{call['call_number']} uses Node #{node_id} which was already used (by Call #{node_ids_used.index(node_id) + 1})")
        node_ids_used.append(node_id)
    
    if not duplicate_node_ids:
        print("  [PASS] All LLM calls use unique node IDs")
        print(f"  Node IDs used: {', '.join(node_ids_used)}")
    else:
        print("  [FAIL] Found duplicate node IDs:")
        for dup in duplicate_node_ids:
            print(f"    - {dup}")
        print()
        print("  ⚠️ Issue: Same node ID appears multiple times in LLM calls")
        print("  This usually means:")
        print("    - Coordinator is being used twice (planning + aggregation)")
        print("    - Parallel experts all using same node ID (e.g., all #3:3 instead of #3:1, #3:2, #3:3)")
        print("    - .find() returning same node when all have identical names")
        print("  → Fix: Use expert index instead of name matching, or ensure unique node assignment")
        all_tests_passed = False
    
    # Test 7: LLM nodes that have calls shouldn't be marked as skipped
    print("\nTest 7: Executed LLM nodes should be marked as executed in graph")
    
    if json_data and 'graph_structure' in json_data:
        graph_structure = json_data['graph_structure']
        all_nodes = graph_structure.get('nodes', [])
        
        # Get node IDs from LLM calls (these nodes executed)
        executed_node_ids_from_calls = set()
        for call in report_data['llm_calls']:
            # Extract numeric ID from display ID (e.g., "#3:1" -> "3:1")
            node_id_str = call['node_id'].replace('#', '')
            executed_node_ids_from_calls.add(node_id_str)
        
        # Check if these nodes are marked as executed in the graph structure
        skipped_but_executed = []
        
        for node in all_nodes:
            if node.get('category') == 'llm':
                node_id = str(node.get('id'))
                node_exec_order = node.get('execution_order', '').replace('#', '')
                node_display_id = node.get('display_id', '').replace('#', '')
                
                # Check by execution_order or display_id
                is_in_llm_calls = (node_exec_order in executed_node_ids_from_calls or 
                                  node_display_id in executed_node_ids_from_calls)
                
                is_marked_executed = node.get('executed', False)
                
                if is_in_llm_calls and not is_marked_executed:
                    skipped_but_executed.append({
                        'node_name': node.get('name'),
                        'node_id': node_id,
                        'exec_order': node_exec_order or node_display_id or node_id
                    })
        
        if not skipped_but_executed:
            print("  [PASS] All LLM nodes that made calls are marked as executed")
        else:
            print("  [FAIL] Found LLM nodes that made calls but are marked as skipped:")
            for node_info in skipped_but_executed:
                print(f"    - {node_info['node_name']} (ID: {node_info['node_id']}, Exec: #{node_info['exec_order']})")
            print()
            print("  ⚠️ Issue: These nodes made LLM calls but aren't tracked as executed")
            print("  This usually means:")
            print("    - Aggregator node made a call but wasn't tracked via /api/record-node-execution")
            print("    - Node was marked as 'skipped' or 'complete' instead of 'executed'")
            print("  → Fix: Ensure node execution is tracked when LLM calls are made")
            all_tests_passed = False
    else:
        print("  [SKIP] Could not load JSON data to verify execution status")
    
    # Final result
    print("\n" + "=" * 80)
    if all_tests_passed:
        print("[SUCCESS] ALL TESTS PASSED - Query report is consistent!")
    else:
        print("[FAILURE] SOME TESTS FAILED - Query report has inconsistencies")
    print("=" * 80)
    
    return all_tests_passed

if __name__ == "__main__":
    try:
        success = run_consistency_test()
        exit(0 if success else 1)
    except Exception as e:
        print(f"\n[ERROR] TEST ERROR: {e}")
        import traceback
        traceback.print_exc()
        exit(1)


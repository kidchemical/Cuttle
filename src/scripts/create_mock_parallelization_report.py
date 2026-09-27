#!/usr/bin/env python3
"""
Create a mock query report that simulates the parallelization issue
where downstream nodes are marked complete but not executed.
"""

import json
import sys
import time
from pathlib import Path

# Fix Windows console encoding
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except:
        pass

def create_mock_report():
    """Create a mock query report with agent pool parallelization"""
    
    query_id = "test_parallel"
    timestamp = time.time()
    
    # Create JSON data with skipped nodes
    json_data = {
        "query_id": query_id,
        "timestamp": "2025-10-12T11:11:24",
        "user_input": "Introduce your team to me",
        "execution_stages": [],
        "llm_calls": [
            {
                "model": "gpt-4o-mini",
                "prompt_tokens": 25,
                "completion_tokens": 50,
                "total_tokens": 75,
                "cost": 0.000025,
                "cost_is_estimated": True,
                "start_time": timestamp,
                "end_time": timestamp + 3,
                "duration": 3.0,
                "success": True,
                "prompt_preview": "Introduce your team to me",
                "response_preview": "Let me coordinate the experts...",
                "node_id": "#2"
            },
            {
                "model": "gpt-4o-mini",
                "prompt_tokens": 30,
                "completion_tokens": 100,
                "total_tokens": 130,
                "cost": 0.000045,
                "cost_is_estimated": True,
                "start_time": timestamp + 4,
                "end_time": timestamp + 7,
                "duration": 3.0,
                "success": True,
                "prompt_preview": "Provide an introduction to the team...",
                "response_preview": "Certainly! Here's an introduction...",
                "node_id": "#3:1"
            },
            {
                "model": "gpt-4o-mini",
                "prompt_tokens": 150,
                "completion_tokens": 200,
                "total_tokens": 350,
                "cost": 0.000105,
                "cost_is_estimated": True,
                "start_time": timestamp + 8,
                "end_time": timestamp + 17,
                "duration": 9.0,
                "success": True,
                "prompt_preview": "Synthesize the expert responses...",
                "response_preview": "Hello everyone! I'm excited to introduce...",
                "node_id": "#4"
            }
        ],
        "tool_calls": [],
        "total_execution_time": 17.0,
        "total_tokens": 555,
        "total_cost": 0.000175,
        "success": True,
        "error_message": None,
        "graph_structure": {
            "nodes": [
                # Input - executed
                {
                    "id": 36,
                    "name": "Text Input",
                    "type": "ui-text-input",
                    "category": "input",
                    "executed": True,
                    "success": True,
                    "execution_order": "#1",
                    "display_id": "#1"
                },
                # Coordinator - executed
                {
                    "id": 11,
                    "name": "OpenAI",
                    "type": "llm-openai",
                    "category": "llm",
                    "executed": True,
                    "success": True,
                    "execution_order": "#2",
                    "display_id": "#2"
                },
                # Expert 1 - executed (selected by coordinator)
                {
                    "id": 24,
                    "name": "OpenAI",
                    "type": "llm-openai",
                    "category": "llm",
                    "executed": True,
                    "success": True,
                    "execution_order": "#3:1",
                    "display_id": "#3:1"
                },
                # Expert 2 - NOT executed (not selected)
                {
                    "id": 25,
                    "name": "OpenAI",
                    "type": "llm-openai",
                    "category": "llm",
                    "executed": False,  # ← Skipped!
                    "success": False
                },
                # Expert 3 - NOT executed (not selected)
                {
                    "id": 26,
                    "name": "OpenAI",
                    "type": "llm-openai",
                    "category": "llm",
                    "executed": False,  # ← Skipped!
                    "success": False
                },
                # Aggregator - executed internally but marked as complete
                {
                    "id": 12,
                    "name": "OpenAI",
                    "type": "llm-openai",
                    "category": "llm",
                    "executed": True,  # Agent pool creates internal aggregation
                    "success": True,
                    "execution_order": "#4",
                    "display_id": "#4"
                },
                # File Output - NOT executed (marked complete)
                {
                    "id": 15,
                    "name": "File Output",
                    "type": "output-file",
                    "category": "output",
                    "executed": False,  # ← Marked complete!
                    "success": False
                },
                # Log Output - NOT executed (marked complete)
                {
                    "id": 21,
                    "name": "Log Output",
                    "type": "output-log",
                    "category": "output",
                    "executed": False,  # ← Marked complete!
                    "success": False
                }
            ],
            "connections": [
                {"from": 36, "to": 11},
                {"from": 11, "to": 24},
                {"from": 11, "to": 25},
                {"from": 11, "to": 26},
                {"from": 24, "to": 12},
                {"from": 25, "to": 12},
                {"from": 26, "to": 12},
                {"from": 12, "to": 15},
                {"from": 12, "to": 21}
            ],
            "execution_order": [
                {"node_id": 36, "timestamp": timestamp, "success": True},
                {"node_id": 11, "timestamp": timestamp + 1, "success": True},
                {"node_id": 24, "timestamp": timestamp + 4, "success": True},
                {"node_id": 12, "timestamp": timestamp + 8, "success": True}
            ]
        },
        "execution_mode": "multi-lite",
        "agent_config": {
            "pipeline_name": "TEST - Hello World (Parallelization)",
            "node_count": 8,
            "llm_nodes": 5,
            "tool_nodes": 0,
            "models_used": ["gpt-4o-mini"],
            "pipeline_description": "Test agent pool parallelization",
            "execution_mode": "Node Editor Pipeline"
        }
    }
    
    # Save JSON file
    # Updated path: now in src/scripts/, need to go up to root
    logs_dir = Path(__file__).parent.parent.parent / "web" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    
    json_filename = f"query_data_{query_id}_20251012_111124.json"
    json_path = logs_dir / json_filename
    
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(json_data, f, indent=2)
    
    print(f"✓ Created mock JSON: {json_path}")
    
    # Create a simple HTML report
    html_content = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Mock Query Report - test_parallel</title>
</head>
<body>
    <h1>Mock Query Report - Agent Pool Parallelization Issue</h1>
    
    <div class='agent-graph'>
        <div class="flow-node input executed success" data-node-id="36">
            📥 Text Input<br><small>⏱️ 0.00s</small><br><small class='exec-order'>#1</small>
        </div>
        
        <div class="flow-node llm executed success" data-node-id="11">
            🤖 OpenAI<br><small>⏱️ 3.00s</small><br><small class='exec-order'>#2</small>
        </div>
        
        <div class="flow-node llm executed success" data-node-id="24">
            🤖 OpenAI<br><small>⏱️ 3.00s</small><br><small class='exec-order'>#3:1</small>
        </div>
        
        <div class="flow-node llm executed success" data-node-id="12">
            🤖 OpenAI<br><small>⏱️ 9.00s</small><br><small class='exec-order'>#4</small>
        </div>
        
        <!-- Note: Expert 2, Expert 3, File Output, and Log Output are NOT shown 
             because they have executed: False -->
    </div>
    
    <div id='llm-section'>
        <div class='stage-item'>
            <span class="call-number-badge">Call #1 (Node: #2)</span> ✅ gpt-4o-mini
        </div>
        <div class='stage-item'>
            <span class="call-number-badge">Call #2 (Node: #3:1)</span> ✅ gpt-4o-mini
        </div>
        <div class='stage-item'>
            <span class="call-number-badge">Call #3 (Node: #4)</span> ✅ gpt-4o-mini
        </div>
    </div>
</body>
</html>"""
    
    html_filename = f"query_report_{query_id}_20251012_111124.html"
    html_path = logs_dir / html_filename
    
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    print(f"✓ Created mock HTML: {html_path}")
    print()
    print("Summary:")
    print("  Total pipeline nodes: 8")
    print("  Nodes executed: 4 (Text Input, Coordinator, Expert 1, Aggregator)")
    print("  Nodes skipped: 4 (Expert 2, Expert 3, File Output, Log Output)")
    print()
    print("Issue demonstrated:")
    print("  - LLM calls: 3 (for nodes #2, #3:1, #4)")
    print("  - Graph shows: 4 nodes (only executed ones)")
    print("  - Missing from graph: 4 skipped nodes")
    print()
    print("Run test_query_report_consistency.py to verify this is caught!")
    
    return True

def main():
    """Create the mock report"""
    try:
        print("=" * 80)
        print("CREATING MOCK PARALLELIZATION REPORT")
        print("=" * 80)
        print()
        
        create_mock_report()
        return 0
    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    exit(main())


#!/usr/bin/env python3
"""
Create a test with parallel LLM execution
"""

import requests
import time

BASE_URL = "http://localhost:8080"

def create_parallel_test():
    """Create a test with parallel LLMs"""
    
    print("Creating parallel execution test...\n")
    
    # Graph: Text Input → 2 parallel LLMs
    graph_nodes = [
        {"id": 36, "name": "Text Input", "type": "input-text", "category": "input"},
        {"id": 11, "name": "OpenAI A", "type": "llm-openai", "category": "llm"},
        {"id": 12, "name": "OpenAI B", "type": "llm-openai", "category": "llm"},
    ]
    
    graph_connections = [
        {"from": 36, "to": 11},  # Text Input → OpenAI A
        {"from": 36, "to": 12},  # Text Input → OpenAI B (parallel)
    ]
    
    start_response = requests.post(f"{BASE_URL}/api/pipeline-execution-start", json={
        "pipelineName": "TEST - Parallel LLMs",
        "nodeCount": 3,
        "pipelineConfig": {
            "llmNodeCount": 2,
            "toolNodeCount": 0,
            "modelsUsed": ["gpt-4o-mini"]
        },
        "graphStructure": {
            "nodes": graph_nodes,
            "connections": graph_connections
        }
    })
    
    result = start_response.json()
    query_id = result['query_id']
    print(f"Query ID: {query_id}")
    
    # Record Text Input node
    print("Recording: Text Input (#1)")
    requests.post(f"{BASE_URL}/api/record-node-execution", json={
        "queryId": query_id,
        "nodeId": 36,
        "success": True
    })
    
    # Record parallel LLM executions with #2:1 and #2:2 format
    print("Recording: OpenAI A (#2:1) - parallel")
    requests.post(f"{BASE_URL}/api/llm-request", json={
        "nodeType": "llm-openai",
        "model": "gpt-4o-mini",
        "prompt": "Parallel request A",
        "systemPrompt": "You are assistant A",
        "temperature": 0.7,
        "maxTokens": 50,
        "queryId": query_id,
        "nodeId": "#2:1"
    })
    requests.post(f"{BASE_URL}/api/record-node-execution", json={
        "queryId": query_id,
        "nodeId": 11,
        "success": True
    })
    
    print("Recording: OpenAI B (#2:2) - parallel")
    requests.post(f"{BASE_URL}/api/llm-request", json={
        "nodeType": "llm-openai",
        "model": "gpt-4o-mini",
        "prompt": "Parallel request B",
        "systemPrompt": "You are assistant B",
        "temperature": 0.7,
        "maxTokens": 50,
        "queryId": query_id,
        "nodeId": "#2:2"
    })
    requests.post(f"{BASE_URL}/api/record-node-execution", json={
        "queryId": query_id,
        "nodeId": 12,
        "success": True
    })
    
    # Finish with parallel execution order map
    execution_order_map = {
        "36": "#1",
        "11": "#2:1",
        "12": "#2:2"
    }
    
    finish_response = requests.post(f"{BASE_URL}/api/pipeline-execution-finish", json={
        "success": True,
        "executionOrderMap": execution_order_map
    })
    
    finish_result = finish_response.json()
    print(f"\nReport: {finish_result.get('report_file')}")
    return True

if __name__ == "__main__":
    create_parallel_test()
    print("\nNow run: python src/tests/test_query_report_consistency.py")


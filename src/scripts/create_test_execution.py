#!/usr/bin/env python3
"""
Create a test pipeline execution to generate a query report
"""

import requests
import time
import json

BASE_URL = "http://localhost:8080"

def create_test_execution():
    """Create a test pipeline execution"""
    
    print("Waiting for server to be ready...")
    for i in range(10):
        try:
            response = requests.get(f"{BASE_URL}/api/health", timeout=2)
            if response.status_code == 200:
                print("Server is ready!")
                break
        except:
            pass
        time.sleep(1)
    else:
        print("Server not responding, trying anyway...")
    
    print("\n1. Starting pipeline execution...")
    
    # Create a simple test graph structure
    graph_nodes = [
        {"id": 36, "name": "Text Input", "type": "input-text", "category": "input"},
        {"id": 11, "name": "OpenAI", "type": "llm-openai", "category": "llm"},
        {"id": 12, "name": "OpenAI 2", "type": "llm-openai", "category": "llm"},
    ]
    
    graph_connections = [
        {"from": 36, "to": 11},
        {"from": 11, "to": 12}
    ]
    
    start_response = requests.post(f"{BASE_URL}/api/pipeline-execution-start", json={
        "pipelineName": "TEST - Hello World (Parallelization)",
        "nodeCount": 3,
        "pipelineConfig": {
            "llmNodeCount": 2,
            "toolNodeCount": 0,
            "modelsUsed": ["gpt-4o-mini"],
            "pipelineDescription": "Test pipeline"
        },
        "graphStructure": {
            "nodes": graph_nodes,
            "connections": graph_connections
        }
    })
    
    result = start_response.json()
    query_id = result['query_id']
    print(f"Query ID: {query_id}")
    
    print("\n2. Recording node executions...")
    
    # Record Text Input node
    print("Recording: Text Input (#1)")
    requests.post(f"{BASE_URL}/api/record-node-execution", json={
        "queryId": query_id,
        "nodeId": 36,
        "success": True
    })
    time.sleep(0.1)
    
    # Record first LLM call
    print("Recording: OpenAI (#2)")
    requests.post(f"{BASE_URL}/api/llm-request", json={
        "nodeType": "llm-openai",
        "model": "gpt-4o-mini",
        "prompt": "Test prompt 1",
        "systemPrompt": "You are a helpful assistant",
        "temperature": 0.7,
        "maxTokens": 50,
        "queryId": query_id,
        "nodeId": "#2"
    })
    requests.post(f"{BASE_URL}/api/record-node-execution", json={
        "queryId": query_id,
        "nodeId": 11,
        "success": True
    })
    time.sleep(0.1)
    
    # Record second LLM call
    print("Recording: OpenAI 2 (#3)")
    requests.post(f"{BASE_URL}/api/llm-request", json={
        "nodeType": "llm-openai",
        "model": "gpt-4o-mini",
        "prompt": "Test prompt 2",
        "systemPrompt": "You are a helpful assistant",
        "temperature": 0.7,
        "maxTokens": 50,
        "queryId": query_id,
        "nodeId": "#3"
    })
    requests.post(f"{BASE_URL}/api/record-node-execution", json={
        "queryId": query_id,
        "nodeId": 12,
        "success": True
    })
    time.sleep(0.1)
    
    print("\n3. Finishing execution...")
    
    execution_order_map = {
        "36": "#1",
        "11": "#2",
        "12": "#3"
    }
    
    finish_response = requests.post(f"{BASE_URL}/api/pipeline-execution-finish", json={
        "success": True,
        "errorMessage": None,
        "executionOrderMap": execution_order_map
    })
    
    finish_result = finish_response.json()
    if finish_result.get('success'):
        print(f"[OK] Report: {finish_result.get('report_file')}")
        print(f"[OK] URL: {finish_result.get('report_url')}")
        return True
    else:
        print(f"[FAIL] {finish_result}")
        return False

if __name__ == "__main__":
    success = create_test_execution()
    if success:
        print("\n[SUCCESS] Test execution created!")
        print("\nNow run: python src/tests/test_query_report_consistency.py")
    else:
        print("\n[FAIL] Could not create test execution")


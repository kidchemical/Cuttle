"""
Test script to verify query report graph error handling improvements.

This tests various edge cases that previously caused "Error creating agent graph".
"""

import sys
from reports.query_report_generator import QueryReportGenerator

def test_invalid_graph_structure():
    """Test with invalid graph_structure types"""
    print("\n=== Test 1: Invalid graph_structure (not a dict) ===")
    gen = QueryReportGenerator()
    gen.start_query("Test query")
    gen.execution_data["graph_structure"] = "invalid_string"  # Should be dict
    
    try:
        html = gen._create_agent_graph_html()
        print("[PASS] Handled gracefully, returned HTML length:", len(html))
        assert "Error" not in html or "linear" in html.lower() or "No execution" in html
        print("[PASS] Test passed - no crash")
    except Exception as e:
        print(f"[FAIL] Test failed with exception: {e}")
        return False
    
    return True

def test_invalid_nodes_list():
    """Test with invalid nodes (not a list)"""
    print("\n=== Test 2: Invalid nodes (not a list) ===")
    gen = QueryReportGenerator()
    gen.start_query("Test query")
    gen.execution_data["graph_structure"] = {
        "nodes": "invalid_string",  # Should be list
        "connections": [],
        "execution_order": []
    }
    
    try:
        html = gen._create_agent_graph_html()
        print("[PASS] Handled gracefully, returned HTML length:", len(html))
        assert "Error" not in html or "linear" in html.lower() or "No execution" in html
        print("[PASS] Test passed - no crash")
    except Exception as e:
        print(f"[FAIL] Test failed with exception: {e}")
        return False
    
    return True

def test_corrupted_node_references():
    """Test with connections referencing non-existent nodes"""
    print("\n=== Test 3: Corrupted node references ===")
    gen = QueryReportGenerator()
    gen.start_query("Test query")
    gen.execution_data["graph_structure"] = {
        "nodes": [
            {"id": "node1", "name": "Node 1", "executed": True, "category": "trigger"},
            {"id": "node2", "name": "Node 2", "executed": True, "category": "llm"}
        ],
        "connections": [
            {"from": "node1", "to": "node2"},
            {"from": "node2", "to": "node999"},  # node999 doesn't exist!
            {"from": "node888", "to": "node1"}   # node888 doesn't exist!
        ],
        "execution_order": []
    }
    
    try:
        html = gen._create_node_editor_graph_html()
        print("[PASS] Handled gracefully, returned HTML length:", len(html))
        assert "Node 1" in html and "Node 2" in html
        print("[PASS] Test passed - rendered valid nodes, skipped invalid connections")
    except Exception as e:
        print(f"[FAIL] Test failed with exception: {e}")
        return False
    
    return True

def test_missing_node_ids():
    """Test with nodes missing ID field"""
    print("\n=== Test 4: Nodes missing ID field ===")
    gen = QueryReportGenerator()
    gen.start_query("Test query")
    gen.execution_data["graph_structure"] = {
        "nodes": [
            {"name": "Node 1", "executed": True},  # Missing id!
            {"id": "node2", "name": "Node 2", "executed": True, "category": "llm"},
            "invalid_node",  # Not even a dict!
            {"id": None, "name": "Node 3"}  # id is None
        ],
        "connections": [],
        "execution_order": []
    }
    
    try:
        html = gen._create_node_editor_graph_html()
        print("[PASS] Handled gracefully, returned HTML length:", len(html))
        assert "Node 2" in html  # Valid node should render
        print("[PASS] Test passed - rendered valid node, skipped invalid ones")
    except Exception as e:
        print(f"[FAIL] Test failed with exception: {e}")
        return False
    
    return True

def test_circular_dependencies():
    """Test with circular dependencies"""
    print("\n=== Test 5: Circular dependencies ===")
    gen = QueryReportGenerator()
    gen.start_query("Test query")
    gen.execution_data["graph_structure"] = {
        "nodes": [
            {"id": "node1", "name": "Node 1", "executed": True, "category": "trigger"},
            {"id": "node2", "name": "Node 2", "executed": True, "category": "llm"},
            {"id": "node3", "name": "Node 3", "executed": True, "category": "output"}
        ],
        "connections": [
            {"from": "node1", "to": "node2"},
            {"from": "node2", "to": "node3"},
            {"from": "node3", "to": "node1"}  # Creates circular dependency!
        ],
        "execution_order": []
    }
    
    try:
        html = gen._create_node_editor_graph_html()
        print("[PASS] Handled gracefully, returned HTML length:", len(html))
        # Should handle circular dependency and still render something
        assert "Node 1" in html or "Node 2" in html or "Node 3" in html
        print("[PASS] Test passed - handled circular dependency")
    except Exception as e:
        print(f"[FAIL] Test failed with exception: {e}")
        return False
    
    return True

def test_invalid_execution_stages():
    """Test linear graph with invalid execution stages"""
    print("\n=== Test 6: Invalid execution stages ===")
    gen = QueryReportGenerator()
    gen.start_query("Test query")
    gen.execution_data["execution_stages"] = [
        {"type": "llm", "name": "Stage 1", "duration": 1.5, "start_time": 0},
        "invalid_stage",  # Not a dict!
        {"type": "tool", "name": "Stage 2", "duration": 0.5, "start_time": 1.5},
        {"name": "Stage 3"}  # Missing required fields
    ]
    gen.execution_data["tool_calls"] = []
    
    try:
        html = gen._create_linear_graph_html()
        print("[PASS] Handled gracefully, returned HTML length:", len(html))
        assert "Stage 1" in html and "Stage 2" in html
        print("[PASS] Test passed - rendered valid stages, skipped invalid ones")
    except Exception as e:
        print(f"[FAIL] Test failed with exception: {e}")
        return False
    
    return True

def test_missing_tokens_field():
    """Test with missing or malformed tokens field"""
    print("\n=== Test 7: Missing/malformed tokens field ===")
    gen = QueryReportGenerator()
    gen.start_query("Test query")
    gen.execution_data["execution_stages"] = [
        {"type": "llm", "name": "Stage 1", "duration": 1.5, "start_time": 0, "tokens": "invalid"},  # tokens not a dict
        {"type": "llm", "name": "Stage 2", "duration": 1.5, "start_time": 1.5, "tokens": {"total_tokens": 100}},  # Valid
    ]
    gen.execution_data["tool_calls"] = []
    
    try:
        html = gen._create_linear_graph_html()
        print("[PASS] Handled gracefully, returned HTML length:", len(html))
        assert "Stage 1" in html and "Stage 2" in html
        print("[PASS] Test passed - handled invalid tokens field")
    except Exception as e:
        print(f"[FAIL] Test failed with exception: {e}")
        return False
    
    return True

def test_empty_graph_structure():
    """Test with empty graph structure"""
    print("\n=== Test 8: Empty graph structure ===")
    gen = QueryReportGenerator()
    gen.start_query("Test query")
    gen.execution_data["graph_structure"] = {
        "nodes": [],
        "connections": [],
        "execution_order": []
    }
    gen.execution_data["execution_stages"] = []
    gen.execution_data["tool_calls"] = []
    
    try:
        html = gen._create_agent_graph_html()
        print("[PASS] Handled gracefully, returned HTML length:", len(html))
        assert "No" in html and ("execution" in html or "nodes" in html)
        print("[PASS] Test passed - showed appropriate message")
    except Exception as e:
        print(f"[FAIL] Test failed with exception: {e}")
        return False
    
    return True

def run_all_tests():
    """Run all tests"""
    print("=" * 60)
    print("Query Report Graph Error Handling Tests")
    print("=" * 60)
    
    tests = [
        test_invalid_graph_structure,
        test_invalid_nodes_list,
        test_corrupted_node_references,
        test_missing_node_ids,
        test_circular_dependencies,
        test_invalid_execution_stages,
        test_missing_tokens_field,
        test_empty_graph_structure
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            if test():
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"[FAIL] Test crashed: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    
    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed out of {len(tests)} tests")
    print("=" * 60)
    
    return failed == 0

if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)


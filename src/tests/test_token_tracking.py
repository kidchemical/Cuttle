"""
Test script to verify token tracking is working for external AI tools

This script simulates a tool call with token information to verify
that the tracking infrastructure is properly set up.
"""

import time
from reports.query_report_generator import track_tool_call as _track_tool_call
from reports.query_report_generator import start_query_tracking, finish_query_tracking

def test_tool_call_with_tokens():
    """Test tracking a tool call with token information"""
    
    # Start query tracking
    query_id = start_query_tracking(
        user_input="/claude \"Create a hello world file\"",
        user_context={
            'display_name': 'TestUser',
            'id': 12345,
            'command_type': 'claude'
        }
    )
    
    print(f"✓ Started query tracking: {query_id}")
    
    # Simulate a tool call with token information
    tool_start_time = time.time()
    time.sleep(0.1)  # Simulate processing
    
    # Example token data (like what Claude Code Tool returns)
    tokens_dict = {
        "total_tokens": 1234,
        "prompt_tokens": 400,
        "completion_tokens": 834
    }
    
    _track_tool_call(
        tool_name="claude_code",
        parameters={"prompt": "Create a hello world file", "project": "sandbox"},
        start_time=tool_start_time,
        success=True,
        result="Successfully created hello_world.py",
        model="claude-3-haiku",
        tokens=tokens_dict,
        cost=0.001234
    )
    
    print(f"✓ Tracked tool call with tokens: {tokens_dict}")
    
    # Finish query tracking
    report_path, json_path = finish_query_tracking(success=True)
    
    print(f"✓ Generated query report: {report_path}")
    print(f"✓ Generated JSON data: {json_path}")
    print("\n📊 Open the HTML report to verify token usage is displayed!")
    
    # Verify the JSON data
    import json
    with open(json_path, 'r') as f:
        data = json.load(f)
    
    # Check if tool calls have token information
    if data.get("tool_calls"):
        for call in data["tool_calls"]:
            if call.get("tokens"):
                print(f"\n✅ SUCCESS! Tool call has token information:")
                print(f"   - Model: {call.get('model')}")
                print(f"   - Total Tokens: {call['tokens'].get('total_tokens')}")
                print(f"   - Cost: ${call.get('cost')}")
                return True
    
    print("\n❌ FAILURE! Tool call does not have token information")
    return False

def test_tool_call_without_tokens():
    """Test tracking a tool call without token information (regular tool)"""
    
    query_id = start_query_tracking(
        user_input="/screenshot main",
        user_context={'display_name': 'TestUser', 'id': 12345}
    )
    
    tool_start_time = time.time()
    time.sleep(0.1)
    
    _track_tool_call(
        tool_name="screenshot",
        parameters={"target": "main"},
        start_time=tool_start_time,
        success=True,
        result="Screenshot captured"
    )
    
    report_path, json_path = finish_query_tracking(success=True)
    
    print(f"\n✓ Generated report for regular tool (no tokens): {report_path}")
    
    # Verify the JSON data
    import json
    with open(json_path, 'r') as f:
        data = json.load(f)
    
    # Check that tool calls work without token information
    if data.get("tool_calls"):
        print(f"✅ Regular tool call tracked successfully (no tokens expected)")
        return True
    
    print("❌ Failed to track regular tool call")
    return False

if __name__ == "__main__":
    print("🧪 Testing Token Tracking for External AI Tools\n")
    print("=" * 60)
    
    print("\nTest 1: Tool call WITH token information (Claude Code)")
    print("-" * 60)
    test1_passed = test_tool_call_with_tokens()
    
    print("\n" + "=" * 60)
    print("\nTest 2: Tool call WITHOUT token information (Regular tool)")
    print("-" * 60)
    test2_passed = test_tool_call_without_tokens()
    
    print("\n" + "=" * 60)
    print("\n📋 Test Results:")
    print(f"   Test 1 (AI with tokens): {'✅ PASS' if test1_passed else '❌ FAIL'}")
    print(f"   Test 2 (Regular tool):   {'✅ PASS' if test2_passed else '❌ FAIL'}")
    
    if test1_passed and test2_passed:
        print("\n🎉 All tests passed! Token tracking is working correctly.")
    else:
        print("\n⚠️ Some tests failed. Check the implementation.")


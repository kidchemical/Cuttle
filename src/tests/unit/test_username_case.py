"""
Test to demonstrate that username case doesn't matter for owner verification.
"""

import os
import sys
from pathlib import Path
from unittest.mock import Mock

# Add project root directory to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# Mock environment variables
os.environ["DISCORD_TOKEN"] = "test_token"
os.environ["OWNER_ID"] = "99999"  # test owner id
os.environ["OPENAI_API_KEY"] = "test_key"

# Import the owner verification function
from bot import is_owner, OWNER_ID  # bot.py compat shim

def test_username_case_independence():
    """Test that username case doesn't affect owner verification"""
    
    print("[TEST] Testing Username Case Independence")
    print("=" * 50)
    
    # Test different username variations
    username_variations = [
        "TestOwner",      # Original case
        "testowner",      # All lowercase
        "TESTOWNER",      # All uppercase
        "testOwner",      # Mixed case
        "TeStOwNeR",      # Alternating case
        "kid chemical",     # With space
        "TestOwner123",   # With numbers
    ]
    
    print(f"Bot checks for Discord ID: {OWNER_ID}")
    print(f"Discord ID type: {type(OWNER_ID)}")
    print()
    
    all_passed = True
    
    for username in username_variations:
        # Create mock message with different username but same ID
        mock_message = Mock()
        mock_message.author.id = OWNER_ID  # Same ID as owner
        mock_message.author.display_name = username
        
        # Test owner verification
        is_owner_result = is_owner(mock_message)
        
        status = "[PASS] PASS" if is_owner_result else "[FAIL] FAIL"
        print(f"{status} Username: '{username}' -> Owner: {is_owner_result}")
        
        if not is_owner_result:
            all_passed = False
    
    print()
    
    # Test with different ID but same username
    print("Testing with wrong ID but correct username:")
    mock_message_wrong_id = Mock()
    mock_message_wrong_id.author.id = 999999999  # Wrong ID
    mock_message_wrong_id.author.display_name = "TestOwner"  # Correct username
    
    wrong_id_result = is_owner(mock_message_wrong_id)
    status = "[PASS] PASS" if not wrong_id_result else "[FAIL] FAIL"
    print(f"{status} Username: 'TestOwner' with wrong ID -> Owner: {wrong_id_result}")
    
    if wrong_id_result:
        all_passed = False
    
    print()
    print("=" * 50)
    if all_passed:
        print("[SUCCESS] ALL TESTS PASSED!")
        print("[PASS] Username case does NOT affect owner verification")
        print("[PASS] Only the Discord ID matters for authentication")
    else:
        print("[FAIL] Some tests failed - there may be an issue")
    
    return all_passed

def demonstrate_security():
    """Demonstrate how the security works"""
    print("\n[SECURITY] Security Explanation:")
    print("=" * 50)
    print("The bot uses Discord's unique numeric user ID for authentication.")
    print("This ID never changes, even if you:")
    print("  • Change your username")
    print("  • Change your display name") 
    print("  • Change your nickname in servers")
    print("  • Use different capitalization")
    print()
    print(f"Your Discord ID: {OWNER_ID}")
    print("This ID is what the bot checks, not your username.")
    print()
    print("So 'testowner', 'TestOwner', 'TESTOWNER' all work!")
    print("Because they all have the same Discord ID: {OWNER_ID}")

def run_username_case_tests():
    """Test runner function for username case tests"""
    success = test_username_case_independence()
    demonstrate_security()
    return success

if __name__ == "__main__":
    success = run_username_case_tests()
    sys.exit(0 if success else 1)

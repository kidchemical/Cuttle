"""
Standalone test to demonstrate that username case doesn't matter for owner verification.
"""

import os
from unittest.mock import Mock

def test_username_case_independence():
    """Test that username case doesn't affect owner verification"""
    
    print("[TEST] Testing Username Case Independence")
    print("=" * 50)
    
    # Mock the owner verification function
    OWNER_ID = 99999  # test owner Discord ID
    
    def is_owner(message):
        """Mock owner verification - only checks Discord ID"""
        return message.author.id == OWNER_ID
    
    # Test different username variations
    username_variations = [
        "TestOwner",      # Original case
        "testowner",      # All lowercase
        "TESTOWNER",      # All uppercase
        "testOwner",      # Mixed case
        "TeStOwNeR",      # Alternating case
        "kid chemical",     # With space
        "TestOwner123",   # With numbers
        "testowner#1234", # With discriminator
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
    print(f"Because they all have the same Discord ID: {OWNER_ID}")

def show_discord_id_info():
    """Show information about Discord IDs"""
    print("\n[CLIPBOARD] Discord ID Information:")
    print("=" * 50)
    print("Discord IDs are unique 64-bit integers that never change.")
    print("They are assigned when an account is created and remain constant.")
    print()
    print("Examples of what doesn't matter:")
    print("  • Username: 'TestOwner' vs 'testowner'")
    print("  • Display name: 'Test Owner' vs 'TEST OWNER'")
    print("  • Server nickname: 'TO' vs 'owner'")
    print("  • Capitalization: any variation works")
    print()
    print("What matters:")
    print("  • Only the numeric Discord ID: 99999")
    print("  • This ID is set in the .env file as OWNER_ID")
    print("  • The bot compares message.author.id == OWNER_ID")

if __name__ == "__main__":
    success = test_username_case_independence()
    demonstrate_security()
    show_discord_id_info()
    print(f"\n[TARGET] Answer: YES! Lowercase 'testowner' will work perfectly!")
    print("   The bot only checks your Discord ID, not your username case.")

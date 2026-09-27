"""
Unit tests for bot security and owner verification.
"""

import unittest
from unittest.mock import Mock, patch, MagicMock
import sys
import os
from pathlib import Path

# Add project root directory to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# Mock environment variables before importing bot modules
os.environ["DISCORD_TOKEN"] = "test_token"
os.environ["OWNER_ID"] = "99999"  # test owner id
os.environ["OPENAI_API_KEY"] = "test_key"

from bot import is_owner, OWNER_ID
try:
    from core.multi_stage_processor import process_input
except ImportError:
    from multi_stage_processor import process_input  # type: ignore
try:
    from core.config import get_config
except ImportError:
    from config import get_config  # type: ignore

class TestSecurity(unittest.TestCase):
    """Test security features and owner verification"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.owner_id = int(os.environ["OWNER_ID"])  # Use actual OWNER_ID from environment
        self.non_owner_id = 987654321  # Random non-owner ID
        
        # Mock Discord message objects
        self.owner_message = Mock()
        self.owner_message.author = Mock()
        self.owner_message.author.id = self.owner_id
        self.owner_message.author.display_name = "TestOwner"
        
        self.non_owner_message = Mock()
        self.non_owner_message.author = Mock()
        self.non_owner_message.author.id = self.non_owner_id
        self.non_owner_message.author.display_name = "RandomUser"
        
        # Reset config to default
        config = get_config()
        config.set("mode", "default")
    
    def test_owner_verification_correct_id(self):
        """Test that owner verification works with correct ID"""
        self.assertEqual(OWNER_ID, self.owner_id)
        self.assertTrue(is_owner(self.owner_message))
    
    def test_owner_verification_wrong_id(self):
        """Test that owner verification rejects wrong ID"""
        self.assertFalse(is_owner(self.non_owner_message))
    
    def test_owner_id_from_env(self):
        """Test that OWNER_ID is loaded from environment"""
        self.assertEqual(OWNER_ID, int(os.environ["OWNER_ID"]))
    
    def test_special_commands_work_for_owner(self):
        """Test that special commands work for owner"""
        # These should work regardless of mode
        test_commands = [
            "help",
            "mode", 
            "modes",
            "set mode default",
            "restart",
            "version",
            "usage"
        ]
        
        for command in test_commands:
            with self.subTest(command=command):
                result = process_input(command)
                self.assertIsNotNone(result, f"Command '{command}' should return a result")
                
                # Should not be an empty list or None
                if isinstance(result, list):
                    self.assertGreater(len(result), 0, f"Command '{command}' should return non-empty list")
    
    def test_regular_commands_work_for_owner(self):
        """Test that regular commands work for owner"""
        test_commands = [
            "run unity: escape purgatory",
            "open cursor",
            "take screenshot",
            "launch notepad"
        ]
        
        for command in test_commands:
            with self.subTest(command=command):
                result = process_input(command)
                self.assertIsNotNone(result, f"Command '{command}' should return a result")
    
    def test_owner_id_is_numeric(self):
        """Test that OWNER_ID is a valid numeric ID"""
        self.assertIsInstance(OWNER_ID, int)
        self.assertGreater(OWNER_ID, 0)
        self.assertLess(OWNER_ID, 2**63)  # Discord IDs are 64-bit integers
    
    def test_owner_id_not_default(self):
        """Test that OWNER_ID is not the default value"""
        # The default value is 0 if not set
        self.assertNotEqual(OWNER_ID, 0, "OWNER_ID should be set to the owner's ID, not default 0")
    
    def test_mode_switching_security(self):
        """Test that mode switching works correctly"""
        # Test setting different modes
        modes_to_test = ["default", "multi_stage", "llm_only", "regex_only"]
        
        for mode in modes_to_test:
            with self.subTest(mode=mode):
                result = process_input(f"set mode {mode}")
                self.assertIn("Mode changed to", str(result))
                
                # Verify the mode was actually changed
                config = get_config()
                self.assertEqual(config.get_mode(), mode)
    
    def test_invalid_mode_rejection(self):
        """Test that invalid modes are rejected"""
        invalid_modes = ["invalid", "hack", "admin", "root"]
        
        for mode in invalid_modes:
            with self.subTest(mode=mode):
                result = process_input(f"set mode {mode}")
                self.assertIn("Invalid mode", str(result))
    
    def test_help_command_shows_current_mode(self):
        """Test that help command shows current mode"""
        # Set a specific mode
        process_input("set mode regex_only")
        
        # Get help
        result = process_input("help")
        
        # Should contain current mode
        self.assertIn("regex_only", str(result))
        self.assertIn("Current Mode", str(result))
    
    def test_restart_command_security(self):
        """Test that restart command is properly handled"""
        result = process_input("restart")
        
        # Should return a command structure
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["tool"], "run_program")
        self.assertEqual(result[0]["app"], "restart")
    
    def test_config_persistence_security(self):
        """Test that config changes persist and are secure"""
        original_mode = get_config().get_mode()
        
        # Change mode
        process_input("set mode llm_only")
        
        # Verify change
        self.assertEqual(get_config().get_mode(), "llm_only")
        
        # Reset to original
        process_input(f"set mode {original_mode}")
        self.assertEqual(get_config().get_mode(), original_mode)
    
    def test_owner_id_environment_validation(self):
        """Test that OWNER_ID environment variable is properly validated"""
        # Test with valid ID
        with patch.dict(os.environ, {"OWNER_ID": "123456789"}):
            # Reimport to get new value
            import importlib
            import bot
            importlib.reload(bot)
            self.assertEqual(bot.OWNER_ID, 123456789)
        
        # Test with invalid ID (should default to 0)
        with patch.dict(os.environ, {"OWNER_ID": "invalid"}):
            importlib.reload(bot)
            # Should handle invalid ID gracefully
            self.assertIsInstance(bot.OWNER_ID, int)
            self.assertEqual(bot.OWNER_ID, 0)
        
        # Restore original environment
        importlib.reload(bot)

class TestDiscordSecurity(unittest.TestCase):
    """Test Discord-specific security features"""
    
    def setUp(self):
        """Set up Discord test fixtures"""
        self.owner_id = int(os.environ["OWNER_ID"])  # Use actual OWNER_ID from environment
        self.non_owner_id = 987654321
    
    def test_message_author_verification(self):
        """Test that message author verification works correctly"""
        # Mock owner message
        owner_msg = Mock()
        owner_msg.author = Mock()
        owner_msg.author.id = self.owner_id
        owner_msg.author.display_name = "TestOwner"
        
        # Mock non-owner message
        non_owner_msg = Mock()
        non_owner_msg.author = Mock()
        non_owner_msg.author.id = self.non_owner_id
        non_owner_msg.author.display_name = "RandomUser"
        
        # Test owner verification
        self.assertTrue(is_owner(owner_msg))
        self.assertFalse(is_owner(non_owner_msg))
    
    def test_owner_id_comparison(self):
        """Test that owner ID comparison is strict"""
        # Test exact match
        owner_msg = Mock()
        owner_msg.author = Mock()
        owner_msg.author.id = OWNER_ID
        self.assertTrue(is_owner(owner_msg))
        
        # Test close but not exact match
        owner_msg.author.id = OWNER_ID + 1
        self.assertFalse(is_owner(owner_msg))
        
        owner_msg.author.id = OWNER_ID - 1
        self.assertFalse(is_owner(owner_msg))

class TestCommandSecurity(unittest.TestCase):
    """Test command execution security"""
    
    def test_command_execution_authorization(self):
        """Test that commands require proper authorization"""
        # All commands should work when called directly (simulating owner)
        test_commands = [
            "run unity: escape purgatory",
            "open cursor",
            "take screenshot", 
            "restart",
            "set mode default",
            "help"
        ]
        
        for command in test_commands:
            with self.subTest(command=command):
                result = process_input(command)
                # Should not raise exceptions or return None
                self.assertIsNotNone(result)
    
    def test_sensitive_commands_protection(self):
        """Test that sensitive commands are properly protected"""
        sensitive_commands = [
            "restart",
            "set mode",
            "modes"
        ]
        
        for command in sensitive_commands:
            with self.subTest(command=command):
                result = process_input(command)
                # Should return valid response, not error
                self.assertIsNotNone(result)
                self.assertNotEqual(str(result).strip(), "")

def run_security_tests():
    """Run all security tests and return results"""
    print("[SECURITY] Running Security Tests...")
    
    # Create test suite
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    # Add test cases
    suite.addTests(loader.loadTestsFromTestCase(TestSecurity))
    suite.addTests(loader.loadTestsFromTestCase(TestDiscordSecurity))
    suite.addTests(loader.loadTestsFromTestCase(TestCommandSecurity))
    
    # Run tests
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    # Print summary
    print(f"\n[STATS] Test Results:")
    print(f"[PASS] Tests Run: {result.testsRun}")
    print(f"[PASS] Passed: {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"[FAIL] Failed: {len(result.failures)}")
    print(f"[FAIL] Errors: {len(result.errors)}")
    
    if result.failures:
        print(f"\n[FAIL] Failures:")
        for test, traceback in result.failures:
            print(f"  - {test}: {traceback}")
    
    if result.errors:
        print(f"\n[FAIL] Errors:")
        for test, traceback in result.errors:
            print(f"  - {test}: {traceback}")
    
    return result.wasSuccessful()

if __name__ == "__main__":
    success = run_security_tests()
    sys.exit(0 if success else 1)

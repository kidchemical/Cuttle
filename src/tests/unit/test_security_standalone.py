"""
Standalone security tests that don't require Discord connection.
"""

import unittest
from unittest.mock import Mock, patch
import sys
import os
from pathlib import Path

# Add project root directory to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# Mock environment variables
os.environ["DISCORD_TOKEN"] = "test_token"
os.environ["OWNER_ID"] = "123456789"  # the owner's ID
os.environ["OPENAI_API_KEY"] = "test_key"

# Import modules that don't require Discord connection
try:
    from core.config import get_config, BotConfig
except ImportError:
    from config import get_config  # type: ignore
    BotConfig = None
# MultiStageProcessor deprecated - pipeline system used now
MultiStageProcessor = None

class TestSecurityStandalone(unittest.TestCase):
    """Test security features without Discord connection"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.owner_id = 123456789  # the owner's ID
        self.non_owner_id = 987654321  # Random non-owner ID
        
        # Reset config to default
        config = get_config()
        config.set("mode", "default")
    
    def test_owner_id_from_env(self):
        """Test that OWNER_ID is loaded from environment"""
        owner_id = int(os.environ["OWNER_ID"])
        self.assertEqual(owner_id, 123456789)
        self.assertIsInstance(owner_id, int)
        self.assertGreater(owner_id, 0)
        self.assertNotEqual(owner_id, 0)  # Should not be default value
    
    def test_config_security(self):
        """Test that config system is secure"""
        config = get_config()
        
        # Test default values
        self.assertEqual(config.get_mode(), "default")
        self.assertTrue(config.should_show_thinking())
        self.assertFalse(config.is_debug_mode())
        
        # Test mode switching
        config.set_mode("regex_only")
        self.assertEqual(config.get_mode(), "regex_only")
        
        # Test invalid mode rejection
        result = config.set_mode("invalid_mode")
        self.assertFalse(result)
        self.assertEqual(config.get_mode(), "regex_only")  # Should not change
    
    def test_special_commands_security(self):
        """Test that special commands work securely"""
        if MultiStageProcessor is None:
            self.skipTest("MultiStageProcessor deprecated - pipeline system used")
        processor = MultiStageProcessor()
        
        # Test mode switching commands
        test_commands = [
            ("set mode default", "Mode changed to"),
            ("set mode regex_only", "Mode changed to"),
            ("set mode llm_only", "Mode changed to"),
            ("set mode multi_stage", "Mode changed to"),
            ("set mode invalid", "Invalid mode"),
        ]
        
        for command, expected in test_commands:
            with self.subTest(command=command):
                result = processor._handle_special_commands(command)
                self.assertIsNotNone(result)
                self.assertIn(expected, str(result))
    
    def test_help_command_security(self):
        """Test that help command shows secure information"""
        if MultiStageProcessor is None:
            self.skipTest("MultiStageProcessor deprecated - pipeline system used")
        processor = MultiStageProcessor()
        
        # Test help command
        result = processor._handle_special_commands("help")
        self.assertIsNotNone(result)
        
        help_text = str(result)
        # Should contain mode information
        self.assertIn("Current Mode", help_text)
        self.assertIn("Available Modes", help_text)
        
        # Should not contain sensitive information
        self.assertNotIn("token", help_text.lower())
        self.assertNotIn("secret", help_text.lower())
        self.assertNotIn("password", help_text.lower())
    
    def test_mode_display_security(self):
        """Test that mode display is secure"""
        if MultiStageProcessor is None:
            self.skipTest("MultiStageProcessor deprecated - pipeline system used")
        processor = MultiStageProcessor()
        
        # Test mode command
        result = processor._handle_special_commands("mode")
        self.assertIsNotNone(result)
        
        mode_text = str(result)
        # Should contain current mode
        self.assertIn("Current Mode", mode_text)
        self.assertIn("default", mode_text)
        
        # Should not contain sensitive information
        self.assertNotIn("token", mode_text.lower())
        self.assertNotIn("secret", mode_text.lower())
    
    def test_restart_command_security(self):
        """Test that restart command is properly structured"""
        if MultiStageProcessor is None:
            self.skipTest("MultiStageProcessor deprecated - pipeline system used")
        processor = MultiStageProcessor()
        
        # Test restart command
        result = processor._handle_special_commands("restart")
        self.assertIsNotNone(result)
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 1)
        
        command = result[0]
        self.assertEqual(command["tool"], "run_program")
        self.assertEqual(command["app"], "restart")
    
    def test_config_persistence_security(self):
        """Test that config changes persist securely"""
        config = get_config()
        original_mode = config.get_mode()
        
        try:
            # Change mode
            config.set_mode("regex_only")
            self.assertEqual(config.get_mode(), "regex_only")
            
            # Create new config instance (simulates restart)
            new_config = BotConfig()
            self.assertEqual(new_config.get_mode(), "regex_only")
            
        finally:
            # Reset to original
            config.set_mode(original_mode)
    
    def test_command_processing_security(self):
        """Test that command processing is secure"""
        if MultiStageProcessor is None:
            self.skipTest("MultiStageProcessor deprecated - pipeline system used")
        processor = MultiStageProcessor()
        
        # Test that special commands bypass mode logic
        test_commands = [
            "help",
            "mode",
            "set mode default",
            "restart"
        ]
        
        for command in test_commands:
            with self.subTest(command=command):
                # Should work in any mode
                for mode in ["default", "regex_only", "llm_only", "multi_stage"]:
                    config = get_config()
                    config.set_mode(mode)
                    
                    result = processor.process_input(command)
                    self.assertIsNotNone(result, f"Command '{command}' should work in {mode} mode")
    
    def test_environment_variable_security(self):
        """Test that environment variables are properly handled"""
        # Test required environment variables
        required_vars = ["OWNER_ID"]
        
        for var in required_vars:
            with self.subTest(var=var):
                self.assertIn(var, os.environ)
                value = os.environ[var]
                self.assertIsNotNone(value)
                self.assertNotEqual(value.strip(), "")
                
                # OWNER_ID should be numeric
                if var == "OWNER_ID":
                    self.assertTrue(value.isdigit())
                    owner_id = int(value)
                    self.assertGreater(owner_id, 0)
                    self.assertNotEqual(owner_id, 0)  # Should not be default
    
    def test_invalid_input_handling(self):
        """Test that invalid inputs are handled securely"""
        if MultiStageProcessor is None:
            self.skipTest("MultiStageProcessor deprecated - pipeline system used")
        processor = MultiStageProcessor()
        
        # Test various invalid inputs
        invalid_inputs = [
            "",
            "   ",
            "null",
            "undefined",
            "<script>alert('xss')</script>",
            "../../etc/passwd",
            "rm -rf /",
            "DROP TABLE users;",
        ]
        
        for invalid_input in invalid_inputs:
            with self.subTest(input=invalid_input):
                result = processor.process_input(invalid_input)
                # Should not crash or return sensitive information
                self.assertIsNotNone(result)
                
                if isinstance(result, str):
                    # Should not contain any of the invalid input (except empty string which is expected)
                    if invalid_input.strip():  # Skip empty string test
                        self.assertNotIn(invalid_input, result)

def run_security_tests():
    """Run all security tests and return results"""
    print("[SECURITY] Running Security Tests (Standalone)...")
    
    # Create test suite
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    # Add test cases
    suite.addTests(loader.loadTestsFromTestCase(TestSecurityStandalone))
    
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

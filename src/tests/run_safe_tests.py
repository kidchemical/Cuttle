#!/usr/bin/env python3
"""
Safe test runner for JamBit OS
This version only runs safe tests that don't involve mouse input, window control, or other potentially disruptive operations.
"""

import sys
import os
import subprocess
import webbrowser
import platform
from datetime import datetime
from pathlib import Path

# Add the project root to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Import HTML reporter
from html_reporter import HTMLTestReporter

def run_safe_tests():
    """Run only safe tests that don't involve system control"""
    print("[TEST] Running Safe Tests Only")
    print("=" * 60)
    print("This will run tests that are safe and don't involve:")
    print("• Mouse input automation")
    print("• Window control operations")
    print("• System process manipulation")
    print("• Screenshot capture")
    print("=" * 60)
    
    test_results = {}
    
    # Safe test modules that don't involve system control
    safe_tests = [
        ("API Key Test", "unit.test_api_key"),
        ("OpenAI Connection", "unit.test_openai_connection"),
        ("Claude Code Integration", "unit.test_claude_code"),
        ("Security Tests", "unit.test_security"),
        ("Username Case Tests", "unit.test_username_case"),
    ]
    
    # Unsafe test modules that involve system control (excluded)
    unsafe_tests = [
        ("Process Management", "unit.test_process_tools"),
        ("Window Management", "unit.test_window_tools"),
        ("Input Automation", "unit.test_input_tools"),
        ("Screenshot Tools", "unit.test_screenshot_tools"),
        ("OCR Tools", "unit.test_ocr_tools"),
        ("Windows Tools", "unit.test_windows_tools"),
        ("Window Focus", "unit.test_window_focus"),
    ]
    
    print("[PASS] Safe Tests (Will Run):")
    for test_name, test_module in safe_tests:
        print(f"   • {test_name}")
    
    print("\n[WARNING] Unsafe Tests (Skipped for Safety):")
    for test_name, test_module in unsafe_tests:
        print(f"   • {test_name}")
    
    # Run safe tests
    for test_name, test_module in safe_tests:
        print(f"\n{'='*20} {test_name} {'='*20}")
        try:
            # Import and run the test
            module_parts = test_module.split('.')
            module = __import__(test_module)
            for part in module_parts[1:]:
                module = getattr(module, part)
            
            # Find the test function
            test_function = None
            for attr_name in dir(module):
                if attr_name.startswith('run_') and callable(getattr(module, attr_name)):
                    test_function = getattr(module, attr_name)
                    break
            
            if test_function:
                success = test_function()
                test_results[test_name] = success
            else:
                print(f"[FAIL] No test function found in {test_module}")
                test_results[test_name] = False
                
        except ImportError as e:
            print(f"[FAIL] Could not import {test_module}: {e}")
            test_results[test_name] = False
        except Exception as e:
            print(f"[FAIL] {test_name} test failed with error: {e}")
            test_results[test_name] = False
    
    return test_results

def run_safe_individual_test_files():
    """Run individual test files that are safe"""
    print("\n[FILE] Running Safe Individual Test Files")
    print("=" * 60)
    
    test_results = {}
    tests_dir = Path(__file__).parent
    
    # Safe individual test files
    safe_test_files = [
        "test_api_key.py",
        "test_openai_connection.py", 
        "test_claude_code.py",
        "test_security.py",
        "test_username_case.py",
    ]
    
    # Unsafe test files (excluded)
    unsafe_test_files = [
        "test_process_tools.py",
        "test_window_tools.py", 
        "test_input_tools.py",
        "test_screenshot_tools.py",
        "test_ocr_tools.py",
        "test_windows_tools.py",
        "test_window_focus.py",
    ]
    
    print("[PASS] Safe Test Files (Will Run):")
    for test_file in safe_test_files:
        print(f"   • {test_file}")
    
    print("\n[WARNING] Unsafe Test Files (Skipped for Safety):")
    for test_file in unsafe_test_files:
        print(f"   • {test_file}")
    
    # Run safe test files
    for test_file_name in safe_test_files:
        test_file = tests_dir / "unit" / test_file_name
        if not test_file.exists():
            continue
            
        print(f"\n{'='*20} {test_file_name} {'='*20}")
        try:
            # Run the test file directly
            result = subprocess.run([sys.executable, str(test_file)], 
                                  capture_output=True, text=True, timeout=60)
            
            if result.returncode == 0:
                print(f"[PASS] {test_file_name} passed")
                test_results[test_file_name] = True
            else:
                print(f"[FAIL] {test_file_name} failed")
                if result.stderr:
                    print(f"   Error: {result.stderr}")
                if result.stdout:
                    print(f"   Output: {result.stdout}")
                test_results[test_file_name] = False
                
        except subprocess.TimeoutExpired:
            print(f"⏰ {test_file_name} timed out")
            test_results[test_file_name] = False
        except Exception as e:
            print(f"[FAIL] {test_file_name} failed with error: {e}")
            test_results[test_file_name] = False
    
    return test_results

def generate_safe_test_summary(safe_results, individual_results):
    """Generate safe test summary"""
    print("\n" + "=" * 80)
    print("[STATS] SAFE TEST SUMMARY")
    print("=" * 80)
    
    # Calculate totals
    total_safe = len(safe_results)
    successful_safe = sum(1 for success in safe_results.values() if success)
    
    total_individual = len(individual_results)
    successful_individual = sum(1 for success in individual_results.values() if success)
    
    total_tests = total_safe + total_individual
    total_successful = successful_safe + successful_individual
    
    # Print summary
    print(f"[CHART] OVERALL STATISTICS:")
    print(f"   Total Safe Tests: {total_tests}")
    print(f"   Successful: {total_successful}")
    print(f"   Failed: {total_tests - total_successful}")
    print(f"   Success Rate: {(total_successful/total_tests)*100:.1f}%")
    
    print(f"\n[TEST] SAFE TESTS ({successful_safe}/{total_safe}):")
    for suite_name, success in safe_results.items():
        status = "[PASS] PASS" if success else "[FAIL] FAIL"
        print(f"   {suite_name:<25} {status}")
    
    print(f"\n[FILE] SAFE INDIVIDUAL TEST FILES ({successful_individual}/{total_individual}):")
    for suite_name, success in individual_results.items():
        status = "[PASS] PASS" if success else "[FAIL] FAIL"
        print(f"   {suite_name:<25} {status}")
    
    # Environment information
    print(f"\n[ENV] ENVIRONMENT:")
    print(f"   Python Version: {sys.version}")
    print(f"   Working Directory: {os.getcwd()}")
    print(f"   Test Directory: {Path(__file__).parent}")
    print(f"   Project Root: {Path(__file__).parent.parent}")
    
    # Check for common issues
    print(f"\n[WARNING]  COMMON ISSUES CHECK:")
    
    # Check for .env file
    env_file = Path(__file__).parent.parent / '.env'
    if env_file.exists():
        print(f"   [PASS] .env file exists")
    else:
        print(f"   [FAIL] .env file missing - Discord bot tests may fail")
    
    # Check for bot_config.json
    config_file = Path(__file__).parent.parent / 'bot_config.json'
    if config_file.exists():
        print(f"   [PASS] bot_config.json exists")
    else:
        print(f"   [FAIL] bot_config.json missing - Bot configuration tests may fail")
    
    # Check for Discord.py
    try:
        import discord
        print(f"   [PASS] discord.py is available")
    except ImportError:
        print(f"   [FAIL] discord.py not available - Discord bot tests will fail")
    
    # Check for OpenAI
    try:
        import openai
        print(f"   [PASS] openai is available")
    except ImportError:
        print(f"   [FAIL] openai not available - AI agent tests will fail")
    
    # Overall result
    overall_success = all(safe_results.values()) and all(individual_results.values())
    
    print(f"\n{'='*80}")
    if overall_success:
        print("[SUCCESS] ALL SAFE TESTS PASSED! JamBit OS core functionality is working.")
    else:
        print("[WARNING]  SOME SAFE TESTS FAILED! Check the detailed output above.")
        print("   Common causes:")
        print("   • Missing dependencies (install with pip install -r requirements/requirements.txt)")
        print("   • Missing environment variables (create .env file)")
        print("   • Network connectivity issues")
        print("   • Discord bot token not configured")
        print("   • OpenAI API key not configured")
    
    print(f"{'='*80}")
    
    return overall_success

def open_test_report(report_path):
    """Open the test report in the default browser"""
    try:
        # Convert to absolute path
        abs_path = os.path.abspath(report_path)
        
        # Open in browser
        webbrowser.open(f"file://{abs_path}")
        print(f"\n[WORLD] Test report opened in browser: {abs_path}")
        return True
    except Exception as e:
        print(f"\n[WARNING] Could not open test report: {e}")
        print(f"[LIGHTBULB] You can manually open: {os.path.abspath(report_path)}")
        return False

def main():
    """Main safe test runner"""
    print("[START] Safe JamBit OS Tests")
    print("=" * 80)
    print("This will run only safe tests that don't involve system control")
    print("Unsafe tests (mouse input, window control, etc.) are excluded")
    print("=" * 80)
    
    # Run safe tests
    safe_results = run_safe_tests()
    
    # Run safe individual test files
    individual_results = run_safe_individual_test_files()
    
    # Generate summary
    overall_success = generate_safe_test_summary(safe_results, individual_results)
    
    # Calculate detailed metrics
    total_tests = len(safe_results) + len(individual_results)
    successful_tests = sum(1 for r in safe_results.values() if r) + sum(1 for r in individual_results.values() if r)
    failed_tests = sum(1 for r in safe_results.values() if not r) + sum(1 for r in individual_results.values() if not r)
    success_rate = (successful_tests / total_tests * 100) if total_tests > 0 else 0
    
    # Calculate test suite metrics
    safe_tests_passed = sum(1 for r in safe_results.values() if r)
    safe_tests_failed = sum(1 for r in safe_results.values() if not r)
    individual_tests_passed = sum(1 for r in individual_results.values() if r)
    individual_tests_failed = sum(1 for r in individual_results.values() if not r)
    
    # Generate HTML report
    test_results = {
        'overall_stats': {
            'total_tests': total_tests,
            'successful_tests': successful_tests,
            'failed_tests': failed_tests,
            'success_rate': success_rate,
            'safe_tests_passed': safe_tests_passed,
            'safe_tests_failed': safe_tests_failed,
            'individual_tests_passed': individual_tests_passed,
            'individual_tests_failed': individual_tests_failed,
            'test_duration': 'N/A',  # Could be calculated if we track timing
            'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        },
        'environment_info': {
            'Python Version': f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            'Platform': 'Safe Test Mode',
            'Working Directory': os.getcwd(),
            'Test Directory': str(Path(__file__).parent),
            'Project Root': str(Path(__file__).parent.parent),
            'OS': os.name,
            'Architecture': platform.architecture()[0] if hasattr(platform, 'architecture') else 'Unknown'
        },
        'safe_tests': {name: {'status': 'PASS' if result else 'FAIL', 'message': 'Test completed', 'duration': 'N/A'} for name, result in safe_results.items()},
        'individual_tests': {name: {'status': 'PASS' if result else 'FAIL', 'message': 'Test completed', 'duration': 'N/A'} for name, result in individual_results.items()}
    }
    
    # Generate HTML report
    reporter = HTMLTestReporter()
    report_path = reporter.generate_report(test_results)
    
    print(f"\n[STATS] HTML Test Report generated: {report_path}")
    
    # Open the test report
    open_test_report(report_path)
    
    print("=" * 80)
    
    return overall_success

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)

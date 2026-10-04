#!/usr/bin/env python3
"""
WSL-compatible test runner for JamBit OS
This version skips Windows-only tests and handles WSL limitations gracefully
"""

import sys
import os
import subprocess
from pathlib import Path

# Add the project root to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Import HTML reporter
from html_reporter import HTMLTestReporter

def check_wsl_environment():
    """Check if we're running in WSL and what's available"""
    print("[SEARCH] Checking WSL Environment...")
    print("=" * 50)
    
    # Check if we're in WSL
    try:
        with open('/proc/version', 'r') as f:
            version_info = f.read()
            if 'microsoft' in version_info.lower() or 'wsl' in version_info.lower():
                print("[PASS] Running in WSL environment")
                return True
            else:
                print("[WARNING] Not running in WSL")
                return False
    except:
        print("[WARNING] Could not determine WSL status")
        return False

def run_wsl_compatible_tests():
    """Run tests that are compatible with WSL"""
    print("[TEST] Running WSL-Compatible Tests")
    print("=" * 60)
    
    test_results = {}
    
    # Test modules that should work in WSL
    wsl_compatible_tests = [
        ("API Key Test", "unit.test_api_key"),
        ("OpenAI Connection", "unit.test_openai_connection"),
        ("Claude Code Integration", "unit.test_claude_code"),
    ]
    
    # Test modules that won't work in WSL (Windows-only)
    windows_only_tests = [
        ("Process Management", "unit.test_process_tools"),
        ("Window Management", "unit.test_window_tools"),
        ("Input Automation", "unit.test_input_tools"),
        ("Screenshot Tools", "unit.test_screenshot_tools"),
        ("OCR Tools", "unit.test_ocr_tools"),
        ("Windows Tools", "unit.test_windows_tools"),
    ]
    
    print("[PASS] WSL-Compatible Tests:")
    for test_name, test_module in wsl_compatible_tests:
        print(f"   • {test_name}")
    
    print("\n[WARNING] Windows-Only Tests (Skipped in WSL):")
    for test_name, test_module in windows_only_tests:
        print(f"   • {test_name}")
    
    # Run WSL-compatible tests
    for test_name, test_module in wsl_compatible_tests:
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

def run_individual_test_files():
    """Run individual test files that can be executed directly"""
    print("\n[FILE] Running Individual Test Files")
    print("=" * 60)
    
    test_results = {}
    tests_dir = Path(__file__).parent
    
    # Find all test files
    test_files = []
    for test_file in tests_dir.rglob("test_*.py"):
        if test_file.name != "run_all_tests.py" and test_file.name != "run_tests_wsl_compatible.py":
            test_files.append(test_file)
    
    for test_file in test_files:
        print(f"\n{'='*20} {test_file.name} {'='*20}")
        try:
            # Run the test file directly
            result = subprocess.run([sys.executable, str(test_file)], 
                                  capture_output=True, text=True, timeout=60)
            
            if result.returncode == 0:
                print(f"[PASS] {test_file.name} passed")
                test_results[test_file.name] = True
            else:
                print(f"[FAIL] {test_file.name} failed")
                print(f"   Error: {result.stderr}")
                test_results[test_file.name] = False
                
        except subprocess.TimeoutExpired:
            print(f"⏰ {test_file.name} timed out")
            test_results[test_file.name] = False
        except Exception as e:
            print(f"[FAIL] {test_file.name} failed with error: {e}")
            test_results[test_file.name] = False
    
    return test_results

def generate_wsl_test_summary(compatible_results, individual_results):
    """Generate WSL-specific test summary"""
    print("\n" + "=" * 80)
    print("[STATS] WSL TEST SUMMARY")
    print("=" * 80)
    
    # Calculate totals
    total_compatible = len(compatible_results)
    successful_compatible = sum(1 for success in compatible_results.values() if success)
    
    total_individual = len(individual_results)
    successful_individual = sum(1 for success in individual_results.values() if success)
    
    total_tests = total_compatible + total_individual
    total_successful = successful_compatible + successful_individual
    
    # Print summary
    print(f"[CHART] OVERALL STATISTICS:")
    print(f"   Total WSL-Compatible Tests: {total_tests}")
    print(f"   Successful: {total_successful}")
    print(f"   Failed: {total_tests - total_successful}")
    print(f"   Success Rate: {(total_successful/total_tests)*100:.1f}%")
    
    print(f"\n[TEST] WSL-COMPATIBLE TESTS ({successful_compatible}/{total_compatible}):")
    for suite_name, success in compatible_results.items():
        status = "[PASS] PASS" if success else "[FAIL] FAIL"
        print(f"   {suite_name:<25} {status}")
    
    print(f"\n[FILE] INDIVIDUAL TEST FILES ({successful_individual}/{total_individual}):")
    for suite_name, success in individual_results.items():
        status = "[PASS] PASS" if success else "[FAIL] FAIL"
        print(f"   {suite_name:<25} {status}")
    
    # WSL-specific information
    print(f"\n🐧 WSL ENVIRONMENT:")
    print(f"   Python Version: {sys.version}")
    print(f"   Working Directory: {os.getcwd()}")
    print(f"   Test Directory: {Path(__file__).parent}")
    print(f"   Project Root: {Path(__file__).parent.parent}")
    
    # Check for WSL-specific issues
    print(f"\n[WARNING]  WSL LIMITATIONS:")
    print(f"   • Windows automation tools (pygetwindow, pyautogui) not supported")
    print(f"   • Window management tests will fail")
    print(f"   • Screenshot tools may have limited functionality")
    print(f"   • Process management tests may behave differently")
    
    # Check for common issues
    print(f"\n[SEARCH] DEPENDENCY CHECK:")
    
    # Check for .env file
    env_file = Path(__file__).parent.parent / '.env'
    if env_file.exists():
        print(f"   [PASS] .env file exists")
    else:
        print(f"   [FAIL] .env file missing - Discord bot tests may fail")
    
    # Report the active runtime/model preference store
    from core.runtime_paths import runtime_config_path
    config_file = runtime_config_path()
    if config_file.exists():
        print(f"   [PASS] runtime/model config exists")
    else:
        print(f"   [INFO] runtime/model config absent (defaults are valid)")
    
    # Check for requests (Discord REST agent-ops)
    try:
        import requests
        print(f"   [PASS] requests is available")
    except ImportError:
        print(f"   [FAIL] requests not available")
    
    # Check for OpenAI
    try:
        import openai
        print(f"   [PASS] openai is available")
    except ImportError:
        print(f"   [FAIL] openai not available - AI agent tests will fail")
    
    # Check for other dependencies
    dependencies = ['PIL', 'psutil', 'mss', 'pytesseract', 'cv2']
    for dep in dependencies:
        try:
            __import__(dep)
            print(f"   [PASS] {dep} is available")
        except ImportError:
            print(f"   [FAIL] {dep} not available - Some tests may fail")
    
    # Overall result
    overall_success = all(compatible_results.values()) and all(individual_results.values())
    
    print(f"\n{'='*80}")
    if overall_success:
        print("[SUCCESS] ALL WSL-COMPATIBLE TESTS PASSED!")
        print("JamBit OS is working correctly in WSL environment!")
    else:
        print("[WARNING]  SOME WSL-COMPATIBLE TESTS FAILED!")
        print("Check the detailed output above for issues.")
        print("Note: Windows-only tests are expected to fail in WSL")
    
    print(f"{'='*80}")
    
    return overall_success

def main():
    """Main WSL test runner"""
    print("[START] WSL-Compatible JamBit OS Tests")
    print("=" * 80)
    print("This will run tests that are compatible with WSL environment")
    print("Windows-only tests will be skipped")
    print("=" * 80)
    
    # Check WSL environment
    is_wsl = check_wsl_environment()
    
    if not is_wsl:
        print("[WARNING] This test runner is designed for WSL environments")
        print("   Some tests may not work as expected")
    
    # Run WSL-compatible tests
    compatible_results = run_wsl_compatible_tests()
    
    # Run individual test files
    individual_results = run_individual_test_files()
    
    # Generate summary
    overall_success = generate_wsl_test_summary(compatible_results, individual_results)
    
    # Generate HTML report
    test_results = {
        'overall_stats': {
            'total_tests': len(compatible_results) + len(individual_results),
            'successful_tests': sum(1 for r in compatible_results.values() if r) + sum(1 for r in individual_results.values() if r),
            'failed_tests': sum(1 for r in compatible_results.values() if not r) + sum(1 for r in individual_results.values() if not r),
            'success_rate': 0
        },
        'environment_info': {
            'Python Version': f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            'Platform': 'WSL' if is_wsl else 'Unknown',
            'Working Directory': os.getcwd(),
            'Test Directory': str(Path(__file__).parent),
            'Project Root': str(Path(__file__).parent.parent)
        },
        'wsl_compatible_tests': {name: {'status': 'PASS' if result else 'FAIL', 'message': 'Test completed'} for name, result in compatible_results.items()},
        'individual_tests': {name: {'status': 'PASS' if result else 'FAIL', 'message': 'Test completed'} for name, result in individual_results.items()}
    }
    
    # Calculate success rate
    total = test_results['overall_stats']['total_tests']
    if total > 0:
        test_results['overall_stats']['success_rate'] = (test_results['overall_stats']['successful_tests'] / total) * 100
    
    # Generate HTML report
    reporter = HTMLTestReporter()
    report_path = reporter.generate_report(test_results)
    
    print(f"\n[STATS] HTML Test Report generated: {report_path}")
    print("=" * 80)
    
    return overall_success

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)

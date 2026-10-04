"""Run all tests for JamBit OS

This script runs all unit and integration tests for the JamBit OS.
"""

import sys
import os
import subprocess
from pathlib import Path

# Add the project root to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Import unit tests with error handling
unit_test_modules = []
try:
    from unit.test_process_tools import run_process_tests
    unit_test_modules.append(("Process Management", run_process_tests))
except ImportError as e:
    print(f"WARNING: Could not import test_process_tools: {e}")

try:
    from unit.test_window_tools import run_window_tests
    unit_test_modules.append(("Window Management", run_window_tests))
except ImportError as e:
    print(f"WARNING: Could not import test_window_tools: {e}")

try:
    from unit.test_input_tools import run_input_tests
    unit_test_modules.append(("Input Automation", run_input_tests))
except ImportError as e:
    print(f"WARNING: Could not import test_input_tools: {e}")

try:
    from unit.test_screenshot_tools import run_screenshot_tests
    unit_test_modules.append(("Screenshot Tools", run_screenshot_tests))
except ImportError as e:
    print(f"WARNING: Could not import test_screenshot_tools: {e}")

try:
    from unit.test_ocr_tools import run_ocr_tests
    unit_test_modules.append(("OCR Tools", run_ocr_tests))
except ImportError as e:
    print(f"WARNING: Could not import test_ocr_tools: {e}")

try:
    from unit.test_windows_tools import run_windows_tests
    unit_test_modules.append(("Windows Tools", run_windows_tests))
except ImportError as e:
    print(f"WARNING: Could not import test_windows_tools: {e}")



try:
    from unit.test_claude_code import run_all_claude_tests
    unit_test_modules.append(("Claude Code Integration", run_all_claude_tests))
except ImportError as e:
    print(f"WARNING: Could not import test_claude_code: {e}")

# Integration tests are run via pytest (src/tests/integration/); the legacy
# WSL/bot-startup runners were removed in 2026-09 (machine-specific paths,
# tested no real code). This list stays as an extension point.
integration_test_modules = []


def run_unit_tests():
    """Run all unit tests"""
    print("[TEST] Running Unit Tests")
    print("=" * 60)
    
    test_results = {}
    
    # Run all available unit test suites
    for suite_name, test_function in unit_test_modules:
        print(f"\n{'='*20} {suite_name} {'='*20}")
        try:
            success = test_function()
            test_results[suite_name] = success
        except Exception as e:
            print(f"[FAIL] {suite_name} tests failed with error: {e}")
            test_results[suite_name] = False
    
    if not unit_test_modules:
        print("[WARNING] No unit test modules could be imported")
        print("   This usually means missing dependencies")
        print("   Try running: pip install -r requirements/requirements.txt")
    
    return test_results

def run_integration_tests():
    """Run all integration tests"""
    print("\n[LINK] Running Integration Tests")
    print("=" * 60)
    
    test_results = {}
    
    # Run all available integration test suites
    for suite_name, test_function in integration_test_modules:
        print(f"\n{'='*20} {suite_name} {'='*20}")
        try:
            success = test_function()
            test_results[suite_name] = success
        except Exception as e:
            print(f"[FAIL] {suite_name} tests failed with error: {e}")
            test_results[suite_name] = False
    
    if not integration_test_modules:
        print("[INFO] No legacy integration suites registered.")
        print("   Run pytest instead: .venv\\Scripts\\python.exe -m pytest src/tests/integration/")
    
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
        if test_file.name != "run_all_tests.py":
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

def generate_test_summary(unit_results, integration_results, individual_results):
    """Generate comprehensive test summary"""
    print("\n" + "=" * 80)
    print("[STATS] COMPREHENSIVE TEST SUMMARY")
    print("=" * 80)
    
    # Calculate totals
    total_unit = len(unit_results)
    successful_unit = sum(1 for success in unit_results.values() if success)
    
    total_integration = len(integration_results)
    successful_integration = sum(1 for success in integration_results.values() if success)
    
    total_individual = len(individual_results)
    successful_individual = sum(1 for success in individual_results.values() if success)
    
    total_tests = total_unit + total_integration + total_individual
    total_successful = successful_unit + successful_integration + successful_individual
    
    # Print summary
    print(f"[CHART] OVERALL STATISTICS:")
    print(f"   Total Test Suites: {total_tests}")
    print(f"   Successful: {total_successful}")
    print(f"   Failed: {total_tests - total_successful}")
    print(f"   Success Rate: {(total_successful/total_tests)*100:.1f}%")
    
    print(f"\n[TEST] UNIT TESTS ({successful_unit}/{total_unit}):")
    for suite_name, success in unit_results.items():
        status = "[PASS] PASS" if success else "[FAIL] FAIL"
        print(f"   {suite_name:<25} {status}")
    
    print(f"\n[LINK] INTEGRATION TESTS ({successful_integration}/{total_integration}):")
    for suite_name, success in integration_results.items():
        status = "[PASS] PASS" if success else "[FAIL] FAIL"
        print(f"   {suite_name:<25} {status}")
    
    print(f"\n[FILE] INDIVIDUAL TEST FILES ({successful_individual}/{total_individual}):")
    for suite_name, success in individual_results.items():
        status = "[PASS] PASS" if success else "[FAIL] FAIL"
        print(f"   {suite_name:<25} {status}")
    
    # Debug information
    print(f"\n[SEARCH] DEBUG INFORMATION:")
    print(f"   Python Version: {sys.version}")
    print(f"   Working Directory: {os.getcwd()}")
    print(f"   Test Directory: {Path(__file__).parent}")
    print(f"   Project Root: {Path(__file__).parent.parent}")
    
    # Environment information
    print(f"\n[ENV] ENVIRONMENT:")
    print(f"   OS: {os.name}")
    print(f"   Platform: {sys.platform}")
    
    # Check for common issues
    print(f"\n[WARNING]  COMMON ISSUES CHECK:")
    
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
    
    # Check for WSL
    try:
        result = subprocess.run(['wsl', '--version'], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            print(f"   [PASS] WSL is available")
        else:
            print(f"   [FAIL] WSL not available - WSL integration tests will fail")
    except:
        print(f"   [FAIL] WSL not available - WSL integration tests will fail")
    
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
    dependencies = ['PIL', 'psutil', 'mss', 'pytesseract', 'cv2', 'pygetwindow', 'pyautogui']
    missing_deps = []
    for dep in dependencies:
        try:
            __import__(dep)
            print(f"   [PASS] {dep} is available")
        except ImportError:
            print(f"   [FAIL] {dep} not available - Some tests may fail")
            missing_deps.append(dep)
        except NotImplementedError as e:
            if 'pygetwindow' in str(e).lower() and 'linux' in str(e).lower():
                print(f"   [WARNING] {dep} not supported on Linux/WSL - This is expected")
            else:
                print(f"   [FAIL] {dep} not available - Some tests may fail")
                missing_deps.append(dep)
    
    if missing_deps:
        print(f"\n[TOOLS] DEPENDENCY INSTALLATION:")
        print(f"   Missing dependencies: {', '.join(missing_deps)}")
        print(f"   Install with: pip install -r requirements/requirements.txt")
        print(f"   Or install individually:")
        for dep in missing_deps:
            if dep == 'PIL':
                print(f"     pip install pillow")
            elif dep == 'cv2':
                print(f"     pip install opencv-python")
            else:
                print(f"     pip install {dep}")
    
    # Overall result
    overall_success = all(unit_results.values()) and all(integration_results.values()) and all(individual_results.values())
    
    print(f"\n{'='*80}")
    if overall_success:
        print("[SUCCESS] ALL TESTS PASSED! JamBit OS is fully functional.")
    else:
        print("[WARNING]  SOME TESTS FAILED! Check the detailed output above.")
        print("   Common causes:")
        print("   • Missing dependencies (install with pip install -r requirements/requirements.txt)")
        print("   • Missing environment variables (create .env file)")
        print("   • WSL not installed or configured")
        print("   • Windows-specific features not available")
        print("   • Network connectivity issues")
        print("   • Discord bot token not configured")
        print("   • OpenAI API key not configured")
    
    print(f"{'='*80}")
    
    return overall_success

def run_all_tests():
    """Run all tests and provide a comprehensive summary"""
    print("[START] Running All JamBit OS Tests")
    print("=" * 80)
    print("This will run unit tests, integration tests, and individual test files")
    print("=" * 80)
    
    # Run unit tests
    unit_results = run_unit_tests()
    
    # Run integration tests
    integration_results = run_integration_tests()
    
    # Run individual test files
    individual_results = run_individual_test_files()
    
    # Generate comprehensive summary
    overall_success = generate_test_summary(unit_results, integration_results, individual_results)
    
    return overall_success


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
#!/usr/bin/env python3
"""
Run Cuttle safe tests with comprehensive logging and HTML report generation
"""

import sys
import os
import subprocess
import time
from datetime import datetime
from io import StringIO
from pathlib import Path

# Add the project root to the path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.html_reporter import HTMLTestReporter
from tests.reporting.history import TestHistoryManager

# Import OpenAI for AI summarization
try:
    from openai import OpenAI
    import os as _os
    _k = _os.getenv("OPENAI_API_KEY")
    openai_client = OpenAI(api_key=_k) if _k else None
except ImportError:
    openai_client = None

class TestLogger:
    """Capture and manage test output logging"""
    
    def __init__(self):
        self.log_buffer = StringIO()
        self.test_results = {}
        self.start_time = datetime.now()
        
    def log(self, message):
        """Add a message to the log buffer"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_entry = f"[{timestamp}] {message}\n"
        self.log_buffer.write(log_entry)
        print(message)  # Also print to console
        
    def run_test(self, test_name, test_command):
        """Run a single test and capture its output"""
        self.log(f"Starting {test_name}...")
        
        try:
            # Run the test command and capture output
            result = subprocess.run(
                test_command,
                shell=True,
                capture_output=True,
                text=True,
                cwd=str(Path(__file__).resolve().parents[2])
            )
            
            # Log the output
            if result.stdout:
                self.log(f"{test_name} STDOUT:")
                self.log(result.stdout)
            
            if result.stderr:
                self.log(f"{test_name} STDERR:")
                self.log(result.stderr)
            
            # Store test results
            self.test_results[test_name] = {
                'command': test_command,
                'return_code': result.returncode,
                'stdout': result.stdout,
                'stderr': result.stderr,
                'success': result.returncode == 0,
                'duration': (datetime.now() - self.start_time).total_seconds()
            }
            
            if result.returncode == 0:
                self.log(f"[PASS] {test_name} completed successfully")
            else:
                self.log(f"[FAIL] {test_name} failed with exit code {result.returncode}")
                
        except Exception as e:
            self.log(f"[ERROR] {test_name} crashed with error: {str(e)}")
            self.test_results[test_name] = {
                'command': test_command,
                'return_code': -1,
                'stdout': '',
                'stderr': str(e),
                'success': False,
                'duration': (datetime.now() - self.start_time).total_seconds()
            }
    
    def get_full_log(self):
        """Get the complete log as a string"""
        return self.log_buffer.getvalue()
    
    def get_summary_stats(self):
        """Calculate summary statistics"""
        total_tests = len(self.test_results)
        successful_tests = sum(1 for result in self.test_results.values() if result['success'])
        failed_tests = total_tests - successful_tests
        success_rate = (successful_tests / total_tests * 100) if total_tests > 0 else 0
        
        return {
            'total_tests': total_tests,
            'successful_tests': successful_tests,
            'failed_tests': failed_tests,
            'success_rate': success_rate
        }
    
    def generate_ai_summary(self):
        """Generate AI summary of test results"""
        if not openai_client:
            return "AI summarization not available - OpenAI API key not configured"
        
        try:
            # Prepare summary data for AI
            stats = self.get_summary_stats()
            full_log = self.get_full_log()
            
            # Create a comprehensive summary for the AI
            summary_prompt = f"""
            Analyze the following Cuttle test results and provide a comprehensive, actionable summary with detailed insights:

            Test Statistics:
            - Total Tests: {stats['total_tests']}
            - Successful: {stats['successful_tests']}
            - Failed: {stats['failed_tests']}
            - Success Rate: {stats['success_rate']:.1f}%

            Detailed Test Results:
            """
            
            # Categorize failures for better analysis
            failure_categories = {
                'authentication': [],
                'network': [],
                'configuration': [],
                'permission': [],
                'dependency': [],
                'timeout': [],
                'other': []
            }
            
            for test_name, result in self.test_results.items():
                status = "✅ PASSED" if result['success'] else "❌ FAILED"
                summary_prompt += f"\n- {test_name}: {status}"
                
                if not result['success'] and result['stderr']:
                    # Extract key error info
                    error_lines = result['stderr'].split('\n')
                    key_error = ""
                    for line in error_lines:
                        if any(keyword in line.lower() for keyword in ['error', 'failed', 'exception', 'traceback']):
                            key_error = line.strip()
                            break
                    
                    if key_error:
                        summary_prompt += f" ({key_error[:100]}...)"
                        
                        # Categorize the failure
                        error_lower = result['stderr'].lower()
                        if any(keyword in error_lower for keyword in ['token', 'auth', 'credential', 'unauthorized']):
                            failure_categories['authentication'].append(test_name)
                        elif any(keyword in error_lower for keyword in ['connection', 'network', 'timeout', 'refused']):
                            failure_categories['network'].append(test_name)
                        elif any(keyword in error_lower for keyword in ['config', 'setting', 'environment', 'variable']):
                            failure_categories['configuration'].append(test_name)
                        elif any(keyword in error_lower for keyword in ['permission', 'access denied', 'forbidden']):
                            failure_categories['permission'].append(test_name)
                        elif any(keyword in error_lower for keyword in ['import', 'module', 'package', 'not found']):
                            failure_categories['dependency'].append(test_name)
                        elif any(keyword in error_lower for keyword in ['timeout', 'slow', 'performance']):
                            failure_categories['timeout'].append(test_name)
                        else:
                            failure_categories['other'].append(test_name)
            
            # Add failure categorization to prompt
            summary_prompt += f"""

            Failure Analysis by Category:
            """
            for category, tests in failure_categories.items():
                if tests:
                    summary_prompt += f"\n- {category.upper()}: {', '.join(tests)}"
            
            summary_prompt += f"""

            Please provide a comprehensive analysis with the following structure:

            <div class="ai-analysis">
                <h3>📊 Overall Assessment</h3>
                <p>[Brief 2-3 sentence assessment of the test suite health]</p>
                
                <h3>🔍 Key Issues Identified</h3>
                <ul>
                    [List specific issues with emojis and color-coded priority levels]
                </ul>
                
                <h3>💡 Recommended Actions</h3>
                <ul>
                    [Provide specific, actionable recommendations]
                </ul>
                
                <h3>📈 Priority Matrix</h3>
                <div class="priority-matrix">
                    <div class="priority-high">
                        <strong><span style="color: #ff4444;">🔴 HIGH PRIORITY</span></strong>
                        <ul>[Critical issues that need immediate attention]</ul>
                    </div>
                    <div class="priority-medium">
                        <strong><span style="color: #ff8800;">🟡 MEDIUM PRIORITY</span></strong>
                        <ul>[Important issues that should be addressed soon]</ul>
                    </div>
                    <div class="priority-low">
                        <strong><span style="color: #44ff44;">🟢 LOW PRIORITY</span></strong>
                        <ul>[Minor issues that can be addressed later]</ul>
                    </div>
                </div>
                
                <h3>📋 Next Steps</h3>
                <ol>
                    [Provide a step-by-step action plan]
                </ol>
                
                <hr>
                
                <h3>📊 Summary Metrics</h3>
                <div class="metrics-summary">
                    <div class="metric-item">
                        <span class="metric-label">Test Health Score:</span>
                        <span class="metric-value">[Score out of 100]</span>
                    </div>
                    <div class="metric-item">
                        <span class="metric-label">Critical Issues:</span>
                        <span class="metric-value">[Count]</span>
                    </div>
                    <div class="metric-item">
                        <span class="metric-label">Estimated Fix Time:</span>
                        <span class="metric-value">[Time estimate]</span>
                    </div>
                </div>
            </div>

            IMPORTANT FORMATTING REQUIREMENTS:
            - Use the exact HTML structure provided above
            - Include emojis for visual appeal
            - Use color-coded priority levels as specified
            - Make recommendations specific and actionable
            - Provide realistic time estimates
            - Focus on developer-friendly language
            - Keep the analysis comprehensive but concise
            """
            
            # Get AI summary
            response = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are an expert test analysis assistant specializing in software testing and quality assurance. Provide comprehensive, actionable analysis of test results for developers. Focus on root cause analysis, prioritization, and practical solutions. ALWAYS use the exact HTML structure provided in the prompt and include emojis, color-coded priority levels, and specific actionable recommendations."},
                    {"role": "user", "content": summary_prompt}
                ],
                max_tokens=800,
                temperature=0.2
            )
            
            return response.choices[0].message.content.strip()
            
        except Exception as e:
            return f"AI summarization failed: {str(e)}"

def run_safe_tests_with_logging():
    """Run all safe tests with comprehensive logging"""
    
    logger = TestLogger()
    
    logger.log("[START] Starting Cuttle Safe Test Suite")
    logger.log("=" * 60)
    logger.log(f"Test session started at: {logger.start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.log("Mode: SAFE (no automation tests)")
    logger.log("")
    
    # Define safe test commands
    safe_tests = [
        ("Security Tests", "python tests/unit/test_security_standalone.py"),
        ("API Key Tests", "python tests/unit/test_api_key.py"),
        ("OpenAI Connection Tests", "python tests/unit/test_openai_connection.py"),
        ("RAG System Tests", "python tests/unit/test_rag_system.py")
    ]
    
    # Run each test
    for test_name, test_command in safe_tests:
        logger.log(f"\n{'='*20} {test_name} {'='*20}")
        logger.run_test(test_name, test_command)
        logger.log(f"{'='*60}")
    
    # Generate summary
    stats = logger.get_summary_stats()
    logger.log(f"\n[STATS] TEST SUMMARY")
    logger.log(f"Total Tests: {stats['total_tests']}")
    logger.log(f"Successful: {stats['successful_tests']}")
    logger.log(f"Failed: {stats['failed_tests']}")
    logger.log(f"Success Rate: {stats['success_rate']:.1f}%")
    
    # Generate AI summary
    logger.log(f"\n[AI] Generating AI summary...")
    ai_summary = logger.generate_ai_summary()
    
    # Log AI summary with Unicode handling
    try:
        logger.log(f"AI Summary: {ai_summary}")
    except UnicodeEncodeError:
        # Replace Unicode characters for console display
        safe_summary = ai_summary.encode('ascii', 'replace').decode('ascii')
        logger.log(f"AI Summary: {safe_summary}")
    
    # Store test session in history
    logger.log(f"\n[HISTORY] Storing test session in history...")
    history_manager = TestHistoryManager()
    session_id = store_test_session_in_history(history_manager, logger, ai_summary)
    logger.log(f"[HISTORY] Test session stored with ID: {session_id}")
    
    # Generate HTML report with full logging and AI summary
    logger.log(f"\n[REPORT] Generating HTML report...")
    generate_html_report_with_logs(logger, ai_summary, history_manager)
    
    return logger

def store_test_session_in_history(history_manager: TestHistoryManager, logger: TestLogger, ai_summary: str) -> int:
    """Store the current test session in history"""
    
    # Create test results data structure
    test_results = {
        'overall_stats': logger.get_summary_stats(),
        'environment_info': {
            'test_mode': 'SAFE',
            'automation_tests': 'SKIPPED',
            'timestamp': logger.start_time.isoformat(),
            'platform': 'Windows',
            'python_version': sys.version,
            'notes': 'Safe mode prevents automation tests that could move mouse or open applications'
        },
        'detailed_results': logger.test_results,
        'total_duration': (datetime.now() - logger.start_time).total_seconds(),
        'ai_summary': ai_summary
    }
    
    return history_manager.store_test_session(test_results)

def generate_html_report_with_logs(logger, ai_summary=None, history_manager=None):
    """Generate HTML report including full test logs and AI summary"""
    
    # Create comprehensive test results data
    test_results = {
        'overall_stats': logger.get_summary_stats(),
        'test_suites': {},
        'environment_info': {
            'test_mode': 'SAFE',
            'automation_tests': 'SKIPPED',
            'timestamp': logger.start_time.isoformat(),
            'platform': 'Windows',
            'python_version': sys.version,
            'notes': 'Safe mode prevents automation tests that could move mouse or open applications'
        },
        'full_log': logger.get_full_log(),
        'detailed_results': logger.test_results,
        'ai_summary': ai_summary or "AI summarization not available"
    }
    
    # Convert detailed results to test_suites format
    for test_name, result in logger.test_results.items():
        status = 'PASS' if result['success'] else 'FAIL'
        
        # Extract error info if failed
        error_msg = ""
        if not result['success']:
            if result['stderr']:
                # Try to extract meaningful error from stderr
                stderr_lines = result['stderr'].strip().split('\n')
                if stderr_lines:
                    # Get the last meaningful error line
                    for line in reversed(stderr_lines):
                        if line.strip() and not line.strip().startswith('Traceback'):
                            error_msg = line.strip()
                            break
                if not error_msg:
                    error_msg = result['stderr'].strip()[:200] + "..." if len(result['stderr']) > 200 else result['stderr'].strip()
        
        test_results['test_suites'][test_name] = {
            'status': status,
            'tests_run': 1,
            'tests_passed': 1 if result['success'] else 0,
            'tests_failed': 0 if result['success'] else 1,
            'error': error_msg,
            'duration': f"{result['duration']:.2f}s",
            'description': f"Individual test execution - {'Success' if result['success'] else 'Failed'}"
        }
    
    # Get time series data if history manager is available
    time_series_data = None
    failure_trends = None
    if history_manager:
        try:
            time_series_data = history_manager.get_time_series_data("1d", "hour")
            failure_trends = history_manager.get_failure_trends("1d")
        except Exception as e:
            print(f'[WARNING] Could not retrieve time series data: {e}')
    
    # Generate report using enhanced HTML reporter
    reporter = HTMLTestReporter()
    report_path = reporter.generate_report_with_logs(test_results, time_series_data, failure_trends)
    
    print(f'[REPORT] HTML report generated: {report_path}')
    print(f'[REPORT] Opening report in browser...')
    
    # Open the report in the default browser
    try:
        os.system(f'start {report_path}')
        print(f'[SUCCESS] Report opened in browser')
    except Exception as e:
        print(f'[WARNING] Could not open browser automatically: {e}')
        print(f'[INFO] Please manually open: {report_path}')
    
    return report_path

if __name__ == "__main__":
    run_safe_tests_with_logging()

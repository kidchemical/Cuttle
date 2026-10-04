#!/usr/bin/env python3
"""
HTML Test Reporter for Cuttle
Generates comprehensive HTML reports for test results
"""

import os
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional


class HTMLTestReporter:
    """Generate HTML test reports with comprehensive test result visualization"""
    
    def __init__(self, output_dir=None):
        """Initialize the HTML reporter
        
        Args:
            output_dir: Directory to save HTML reports
        """
        self.output_dir = Path(output_dir) if output_dir is not None else Path(__file__).resolve().parent / "results"
        self.output_dir.mkdir(parents=True, exist_ok=True)
    
    def generate_report(self, test_results: Dict[str, Any]) -> str:
        """Generate a basic HTML test report
        
        Args:
            test_results: Dictionary containing test results and statistics
            
        Returns:
            Path to the generated HTML report
        """
        return self.generate_report_with_logs(test_results)
    
    def generate_report_with_logs(self, test_results: Dict[str, Any], 
                                 time_series_data: Optional[Dict] = None,
                                 failure_trends: Optional[Dict] = None) -> str:
        """Generate comprehensive HTML test report with logs and analytics
        
        Args:
            test_results: Dictionary containing test results and statistics
            time_series_data: Optional time series data for trend analysis
            failure_trends: Optional failure trend data
            
        Returns:
            Path to the generated HTML report
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_filename = f"test_report_with_logs_{timestamp}.html"
        report_path = self.output_dir / report_filename
        
        # Extract data from test_results
        overall_stats = test_results.get('overall_stats', {})
        test_suites = test_results.get('test_suites', {})
        environment_info = test_results.get('environment_info', {})
        full_log = test_results.get('full_log', '')
        detailed_results = test_results.get('detailed_results', {})
        ai_summary = test_results.get('ai_summary', '')
        
        # Generate HTML content
        html_content = self._generate_html_content(
            overall_stats, test_suites, environment_info, 
            full_log, detailed_results, ai_summary,
            time_series_data, failure_trends
        )
        
        # Write HTML file
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        return str(report_path)
    
    def _generate_html_content(self, overall_stats: Dict, test_suites: Dict, 
                              environment_info: Dict, full_log: str,
                              detailed_results: Dict, ai_summary: str,
                              time_series_data: Optional[Dict] = None,
                              failure_trends: Optional[Dict] = None) -> str:
        """Generate the complete HTML content for the test report"""
        
        # Calculate statistics
        total_tests = overall_stats.get('total_tests', 0)
        successful_tests = overall_stats.get('successful_tests', 0)
        failed_tests = overall_stats.get('failed_tests', 0)
        success_rate = overall_stats.get('success_rate', 0)
        
        # Determine overall status
        if success_rate >= 90:
            status_color = "#4CAF50"  # Green
            status_text = "EXCELLENT"
        elif success_rate >= 70:
            status_color = "#FF9800"  # Orange
            status_text = "GOOD"
        else:
            status_color = "#F44336"  # Red
            status_text = "NEEDS ATTENTION"
        
        html_content = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Cuttle Test Report - {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</title>
    <style>
        {self._get_css_styles()}
    </style>
</head>
<body>
    <div class="container">
        <header class="header">
            <h1>🦑 Cuttle Test Report</h1>
            <div class="timestamp">Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</div>
        </header>
        
        <div class="status-banner" style="background-color: {status_color};">
            <h2>Overall Status: {status_text}</h2>
            <div class="status-stats">
                <span>Success Rate: {success_rate:.1f}%</span>
                <span>Tests: {successful_tests}/{total_tests}</span>
            </div>
        </div>
        
        <div class="summary-grid">
            <div class="summary-card">
                <h3>📊 Test Statistics</h3>
                <div class="stat-item">
                    <span class="stat-label">Total Tests:</span>
                    <span class="stat-value">{total_tests}</span>
                </div>
                <div class="stat-item">
                    <span class="stat-label">Successful:</span>
                    <span class="stat-value success">{successful_tests}</span>
                </div>
                <div class="stat-item">
                    <span class="stat-label">Failed:</span>
                    <span class="stat-value failure">{failed_tests}</span>
                </div>
                <div class="stat-item">
                    <span class="stat-label">Success Rate:</span>
                    <span class="stat-value">{success_rate:.1f}%</span>
                </div>
            </div>
            
            <div class="summary-card">
                <h3>🌍 Environment</h3>
                <div class="env-info">
                    <div><strong>Test Mode:</strong> {environment_info.get('test_mode', 'Unknown')}</div>
                    <div><strong>Platform:</strong> {environment_info.get('platform', 'Unknown')}</div>
                    <div><strong>Python:</strong> {environment_info.get('Python Version', 'Unknown')}</div>
                    <div><strong>OS:</strong> {environment_info.get('OS', 'Unknown')}</div>
                </div>
            </div>
        </div>
        
        {self._generate_ai_summary_section(ai_summary)}
        
        {self._generate_failure_insights_section(detailed_results, test_suites)}
        
        {self._generate_test_suites_section(test_suites)}
        
        {self._generate_detailed_results_section(detailed_results)}
        
        {self._generate_raw_error_logs_section(detailed_results, full_log)}
        
        {self._generate_time_series_section(time_series_data, failure_trends)}
        
        <footer class="footer">
            <p>Generated by Cuttle Test Reporter</p>
            <p>Report ID: {datetime.now().strftime("%Y%m%d_%H%M%S")}</p>
        </footer>
    </div>
    
    <script>
        {self._get_javascript()}
    </script>
</body>
</html>
"""
        return html_content
    
    def _get_css_styles(self) -> str:
        """Get CSS styles for the HTML report"""
        return """
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            line-height: 1.6;
            color: #333;
            background-color: #f5f5f5;
        }
        
        .container {
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
        }
        
        .header {
            text-align: center;
            margin-bottom: 30px;
            padding: 20px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border-radius: 10px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        }
        
        .header h1 {
            font-size: 2.5em;
            margin-bottom: 10px;
        }
        
        .timestamp {
            font-size: 1.1em;
            opacity: 0.9;
        }
        
        .status-banner {
            text-align: center;
            padding: 20px;
            margin-bottom: 30px;
            border-radius: 10px;
            color: white;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        }
        
        .status-banner h2 {
            font-size: 2em;
            margin-bottom: 10px;
        }
        
        .status-stats {
            font-size: 1.2em;
            display: flex;
            justify-content: center;
            gap: 30px;
        }
        
        .summary-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        
        .summary-card {
            background: white;
            padding: 20px;
            border-radius: 10px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        
        .summary-card h3 {
            color: #667eea;
            margin-bottom: 15px;
            font-size: 1.3em;
        }
        
        .stat-item {
            display: flex;
            justify-content: space-between;
            padding: 8px 0;
            border-bottom: 1px solid #eee;
        }
        
        .stat-item:last-child {
            border-bottom: none;
        }
        
        .stat-label {
            font-weight: 500;
        }
        
        .stat-value {
            font-weight: bold;
        }
        
        .stat-value.success {
            color: #4CAF50;
        }
        
        .stat-value.failure {
            color: #F44336;
        }
        
        .env-info div {
            padding: 5px 0;
        }
        
        .section {
            background: white;
            margin-bottom: 30px;
            border-radius: 10px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            overflow: hidden;
        }
        
        .section-header {
            background: #667eea;
            color: white;
            padding: 15px 20px;
            font-size: 1.3em;
            font-weight: bold;
        }
        
        .section-content {
            padding: 20px;
        }
        
        .test-suite {
            margin-bottom: 20px;
            padding: 15px;
            border-radius: 8px;
            border-left: 4px solid #ddd;
        }
        
        .test-suite.pass {
            background-color: #f8fff8;
            border-left-color: #4CAF50;
        }
        
        .test-suite.fail {
            background-color: #fff8f8;
            border-left-color: #F44336;
        }
        
        .test-suite.warning {
            background-color: #fffbf0;
            border-left-color: #FF9800;
        }
        
        .test-suite h4 {
            margin-bottom: 10px;
            color: #333;
        }
        
        .test-suite .status {
            font-weight: bold;
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 0.9em;
        }
        
        .test-suite .status.pass {
            background-color: #4CAF50;
            color: white;
        }
        
        .test-suite .status.fail {
            background-color: #F44336;
            color: white;
        }
        
        .test-suite .status.warning {
            background-color: #FF9800;
            color: white;
        }
        
        .test-suite .error {
            background-color: #ffebee;
            padding: 10px;
            border-radius: 4px;
            margin-top: 10px;
            font-family: monospace;
            font-size: 0.9em;
            color: #c62828;
        }
        
        .log-content {
            background-color: #f8f9fa;
            border: 1px solid #e9ecef;
            border-radius: 4px;
            padding: 15px;
            font-family: 'Courier New', monospace;
            font-size: 0.9em;
            max-height: 400px;
            overflow-y: auto;
            white-space: pre-wrap;
        }
        
        .footer {
            text-align: center;
            padding: 20px;
            color: #666;
            border-top: 1px solid #eee;
            margin-top: 30px;
        }
        
        .collapsible {
            cursor: pointer;
            user-select: none;
        }
        
        .collapsible:hover {
            background-color: #f0f0f0;
        }
        
        .collapsible-content {
            display: none;
            padding-top: 10px;
        }
        
        .collapsible-content.active {
            display: block;
        }
        
        /* Enhanced AI Summary Styles */
        .ai-summary-container {
            background: linear-gradient(135deg, #f0f8ff 0%, #e6f3ff 100%);
            padding: 20px;
            border-radius: 12px;
            border-left: 5px solid #2196F3;
            box-shadow: 0 2px 8px rgba(33, 150, 243, 0.1);
        }
        
        .ai-analysis {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        }
        
        .ai-analysis h3 {
            color: #2c3e50;
            margin: 20px 0 10px 0;
            font-size: 1.2em;
            border-bottom: 2px solid #3498db;
            padding-bottom: 5px;
        }
        
        .ai-analysis h3:first-child {
            margin-top: 0;
        }
        
        .ai-analysis p {
            margin: 10px 0;
            line-height: 1.6;
        }
        
        .ai-analysis ul, .ai-analysis ol {
            margin: 10px 0;
            padding-left: 20px;
        }
        
        .ai-analysis li {
            margin: 5px 0;
            line-height: 1.5;
        }
        
        .priority-matrix {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 15px;
            margin: 15px 0;
        }
        
        .priority-high, .priority-medium, .priority-low {
            padding: 15px;
            border-radius: 8px;
            border-left: 4px solid;
        }
        
        .priority-high {
            background: #ffebee;
            border-left-color: #f44336;
        }
        
        .priority-medium {
            background: #fff3e0;
            border-left-color: #ff9800;
        }
        
        .priority-low {
            background: #e8f5e8;
            border-left-color: #4caf50;
        }
        
        .metrics-summary {
            background: #f8f9fa;
            padding: 15px;
            border-radius: 8px;
            border: 1px solid #e9ecef;
        }
        
        .metric-item {
            display: flex;
            justify-content: space-between;
            padding: 8px 0;
            border-bottom: 1px solid #e9ecef;
        }
        
        .metric-item:last-child {
            border-bottom: none;
        }
        
        .metric-label {
            font-weight: 500;
            color: #495057;
        }
        
        .metric-value {
            font-weight: bold;
            color: #2c3e50;
        }
        
        /* Failure Insights Styles */
        .failure-insights-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
            padding: 15px;
            background: linear-gradient(135deg, #ffebee 0%, #ffcdd2 100%);
            border-radius: 8px;
            border-left: 4px solid #F44336;
        }
        
        .failure-stats {
            display: flex;
            gap: 20px;
        }
        
        .failure-count, .category-count {
            background: rgba(244, 67, 54, 0.1);
            padding: 5px 10px;
            border-radius: 15px;
            font-weight: bold;
            color: #d32f2f;
        }
        
        .failure-category {
            margin-bottom: 20px;
            border: 1px solid #e0e0e0;
            border-radius: 8px;
            overflow: hidden;
        }
        
        .category-header {
            background: #f5f5f5;
            padding: 15px;
            margin: 0;
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 10px;
            transition: background-color 0.3s;
        }
        
        .category-header:hover {
            background: #eeeeee;
        }
        
        .category-icon {
            font-size: 1.2em;
        }
        
        .category-content {
            padding: 20px;
            background: white;
        }
        
        .category-description {
            background: #f8f9fa;
            padding: 15px;
            border-radius: 6px;
            margin-bottom: 15px;
            border-left: 3px solid #6c757d;
        }
        
        .category-recommendations {
            background: #e8f5e8;
            padding: 15px;
            border-radius: 6px;
            margin-bottom: 15px;
            border-left: 3px solid #28a745;
        }
        
        .category-recommendations ul {
            margin: 10px 0 0 0;
            padding-left: 20px;
        }
        
        .failure-item {
            background: #fff5f5;
            border: 1px solid #ffcdd2;
            border-radius: 6px;
            padding: 15px;
            margin-bottom: 10px;
        }
        
        .failure-name {
            font-weight: bold;
            color: #d32f2f;
            margin-bottom: 8px;
        }
        
        .failure-preview {
            font-family: monospace;
            background: #f8f9fa;
            padding: 10px;
            border-radius: 4px;
            margin-bottom: 8px;
            font-size: 0.9em;
            color: #495057;
        }
        
        .failure-meta {
            display: flex;
            gap: 15px;
            font-size: 0.9em;
            color: #6c757d;
        }
        
        /* Raw Error Logs Styles */
        .error-logs-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
            padding: 15px;
            background: linear-gradient(135deg, #fff3e0 0%, #ffe0b2 100%);
            border-radius: 8px;
            border-left: 4px solid #FF9800;
        }
        
        .error-controls {
            display: flex;
            gap: 10px;
            align-items: center;
        }
        
        .error-controls input, .error-controls select {
            padding: 8px 12px;
            border: 1px solid #ddd;
            border-radius: 4px;
            font-size: 0.9em;
        }
        
        .error-logs-container {
            max-height: 600px;
            overflow-y: auto;
        }
        
        .error-log-item {
            border: 1px solid #e0e0e0;
            border-radius: 8px;
            margin-bottom: 15px;
            overflow: hidden;
        }
        
        .error-log-header {
            background: #f8f9fa;
            padding: 15px;
            cursor: pointer;
            display: flex;
            justify-content: space-between;
            align-items: center;
            transition: background-color 0.3s;
        }
        
        .error-log-header:hover {
            background: #e9ecef;
        }
        
        .error-test-name {
            font-weight: bold;
            color: #495057;
        }
        
        .error-type-badge {
            padding: 4px 8px;
            border-radius: 12px;
            font-size: 0.8em;
            font-weight: bold;
            color: white;
        }
        
        .error-type-badge.authentication { background: #dc3545; }
        .error-type-badge.network { background: #007bff; }
        .error-type-badge.permission { background: #6f42c1; }
        .error-type-badge.configuration { background: #fd7e14; }
        .error-type-badge.dependency { background: #20c997; }
        .error-type-badge.timeout { background: #ffc107; color: #212529; }
        .error-type-badge.other { background: #6c757d; }
        
        .error-return-code, .error-duration {
            font-size: 0.9em;
            color: #6c757d;
        }
        
        .error-log-content {
            display: none;
            padding: 20px;
            background: white;
        }
        
        .error-log-content.active {
            display: block;
        }
        
        .error-details h5 {
            margin-bottom: 10px;
            color: #495057;
        }
        
        .error-stderr, .error-stdout {
            margin-bottom: 15px;
        }
        
        .error-stderr pre, .error-stdout pre {
            background: #f8f9fa;
            border: 1px solid #e9ecef;
            border-radius: 4px;
            padding: 15px;
            font-size: 0.85em;
            max-height: 300px;
            overflow-y: auto;
            white-space: pre-wrap;
        }
        
        .full-log-section {
            margin-top: 30px;
            padding-top: 20px;
            border-top: 2px solid #e0e0e0;
        }
        
        .log-controls {
            display: flex;
            gap: 10px;
            margin-bottom: 15px;
        }
        
        .download-btn, .clear-btn {
            padding: 8px 16px;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 0.9em;
            transition: background-color 0.3s;
        }
        
        .download-btn {
            background: #28a745;
            color: white;
        }
        
        .download-btn:hover {
            background: #218838;
        }
        
        .clear-btn {
            background: #dc3545;
            color: white;
        }
        
        .clear-btn:hover {
            background: #c82333;
        }
        
        @media (max-width: 768px) {
            .container {
                padding: 10px;
            }
            
            .header h1 {
                font-size: 2em;
            }
            
            .status-stats {
                flex-direction: column;
                gap: 10px;
            }
            
            .summary-grid {
                grid-template-columns: 1fr;
            }
        }
        """
    
    def _generate_ai_summary_section(self, ai_summary: str) -> str:
        """Generate enhanced AI summary section"""
        if not ai_summary or ai_summary == "AI summarization not available":
            return ""
        
        return f"""
        <div class="section">
            <div class="section-header collapsible" onclick="toggleCollapsible(this)">
                🤖 AI Analysis Summary
            </div>
            <div class="section-content collapsible-content">
                <div class="ai-summary-container">
                    {ai_summary}
                </div>
            </div>
        </div>
        """
    
    def _generate_failure_insights_section(self, detailed_results: Dict, test_suites: Dict) -> str:
        """Generate detailed failure insights section"""
        # Analyze failures
        failures = []
        for test_name, result in detailed_results.items():
            if not result.get('success', False):
                failures.append({
                    'name': test_name,
                    'error': result.get('stderr', ''),
                    'return_code': result.get('return_code', -1),
                    'duration': result.get('duration', 0)
                })
        
        # Also check test suites for failures
        for suite_name, suite_data in test_suites.items():
            if suite_data.get('status', '').upper() == 'FAIL':
                failures.append({
                    'name': suite_name,
                    'error': suite_data.get('error', ''),
                    'return_code': None,
                    'duration': None
                })
        
        if not failures:
            return ""
        
        # Categorize failures
        failure_categories = self._categorize_failures(failures)
        
        insights_html = f"""
        <div class="failure-insights-header">
            <h3>🔍 Failure Analysis</h3>
            <div class="failure-stats">
                <span class="failure-count">{len(failures)} failure(s) detected</span>
                <span class="category-count">{len(failure_categories)} category(ies)</span>
            </div>
        </div>
        """
        
        # Generate category insights
        for category, category_failures in failure_categories.items():
            insights_html += f"""
            <div class="failure-category">
                <h4 class="category-header" onclick="toggleCategory('{category}')">
                    <span class="category-icon">{self._get_category_icon(category)}</span>
                    {category} ({len(category_failures)} failure(s))
                </h4>
                <div class="category-content" id="category-{category}">
                    <div class="category-description">
                        {self._get_category_description(category)}
                    </div>
                    <div class="category-recommendations">
                        <strong>Recommended Actions:</strong>
                        <ul>
                            {self._get_category_recommendations(category)}
                        </ul>
                    </div>
                    <div class="failure-list">
                        {self._generate_failure_list(category_failures)}
                    </div>
                </div>
            </div>
            """
        
        return f"""
        <div class="section">
            <div class="section-header collapsible" onclick="toggleCollapsible(this)">
                🚨 Detailed Failure Insights
            </div>
            <div class="section-content collapsible-content">
                {insights_html}
            </div>
        </div>
        """
    
    def _generate_test_suites_section(self, test_suites: Dict) -> str:
        """Generate test suites section"""
        if not test_suites:
            return ""
        
        suites_html = ""
        for suite_name, suite_data in test_suites.items():
            status = suite_data.get('status', 'UNKNOWN').lower()
            tests_run = suite_data.get('tests_run', 0)
            tests_passed = suite_data.get('tests_passed', 0)
            tests_failed = suite_data.get('tests_failed', 0)
            error = suite_data.get('error', '')
            description = suite_data.get('description', '')
            
            error_html = ""
            if error:
                error_html = f'<div class="error">Error: {error}</div>'
            
            suites_html += f"""
            <div class="test-suite {status}">
                <h4>{suite_name}</h4>
                <div>
                    <span class="status {status}">{status.upper()}</span>
                    <span style="margin-left: 15px;">Tests: {tests_passed}/{tests_run}</span>
                </div>
                <div style="margin-top: 8px; color: #666;">{description}</div>
                {error_html}
            </div>
            """
        
        return f"""
        <div class="section">
            <div class="section-header collapsible" onclick="toggleCollapsible(this)">
                📋 Test Suites
            </div>
            <div class="section-content collapsible-content">
                {suites_html}
            </div>
        </div>
        """
    
    def _generate_detailed_results_section(self, detailed_results: Dict) -> str:
        """Generate detailed results section"""
        if not detailed_results:
            return ""
        
        results_html = ""
        for test_name, result in detailed_results.items():
            success = result.get('success', False)
            return_code = result.get('return_code', -1)
            duration = result.get('duration', 0)
            stderr = result.get('stderr', '')
            
            status_class = "pass" if success else "fail"
            status_text = "PASS" if success else "FAIL"
            
            error_html = ""
            if stderr:
                error_html = f'<div class="error">{stderr}</div>'
            
            results_html += f"""
            <div class="test-suite {status_class}">
                <h4>{test_name}</h4>
                <div>
                    <span class="status {status_class}">{status_text}</span>
                    <span style="margin-left: 15px;">Duration: {duration:.2f}s</span>
                    <span style="margin-left: 15px;">Exit Code: {return_code}</span>
                </div>
                {error_html}
            </div>
            """
        
        return f"""
        <div class="section">
            <div class="section-header collapsible" onclick="toggleCollapsible(this)">
                🔍 Detailed Test Results
            </div>
            <div class="section-content collapsible-content">
                {results_html}
            </div>
        </div>
        """
    
    def _generate_raw_error_logs_section(self, detailed_results: Dict, full_log: str) -> str:
        """Generate comprehensive raw error logs section"""
        # Extract all errors from detailed results
        error_logs = []
        for test_name, result in detailed_results.items():
            if not result.get('success', False):
                error_logs.append({
                    'test_name': test_name,
                    'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    'return_code': result.get('return_code', -1),
                    'duration': result.get('duration', 0),
                    'stderr': result.get('stderr', ''),
                    'stdout': result.get('stdout', ''),
                    'error_type': self._classify_error_type(result.get('stderr', ''))
                })
        
        if not error_logs and not full_log:
            return ""
        
        # Generate error logs HTML
        error_logs_html = ""
        if error_logs:
            error_logs_html = """
            <div class="error-logs-header">
                <h3>📋 Individual Test Error Logs</h3>
                <div class="error-controls">
                    <input type="text" id="errorSearch" placeholder="Search errors..." onkeyup="filterErrors()">
                    <select id="errorTypeFilter" onchange="filterErrors()">
                        <option value="">All Error Types</option>
                        <option value="authentication">Authentication</option>
                        <option value="network">Network</option>
                        <option value="permission">Permission</option>
                        <option value="configuration">Configuration</option>
                        <option value="dependency">Dependency</option>
                        <option value="timeout">Timeout</option>
                        <option value="other">Other</option>
                    </select>
                </div>
            </div>
            <div class="error-logs-container" id="errorLogsContainer">
            """
            
            for i, error_log in enumerate(error_logs):
                error_logs_html += f"""
                <div class="error-log-item" data-error-type="{error_log['error_type']}" data-test-name="{error_log['test_name']}">
                    <div class="error-log-header" onclick="toggleErrorLog({i})">
                        <span class="error-test-name">{error_log['test_name']}</span>
                        <span class="error-type-badge {error_log['error_type']}">{error_log['error_type'].upper()}</span>
                        <span class="error-return-code">Exit Code: {error_log['return_code']}</span>
                        <span class="error-duration">{error_log['duration']:.2f}s</span>
                    </div>
                    <div class="error-log-content" id="errorLog-{i}">
                        <div class="error-details">
                            <h5>Error Details:</h5>
                            <div class="error-stderr">
                                <strong>STDERR:</strong>
                                <pre>{error_log['stderr']}</pre>
                            </div>
                            {f'<div class="error-stdout"><strong>STDOUT:</strong><pre>{error_log["stdout"]}</pre></div>' if error_log['stdout'] else ''}
                        </div>
                    </div>
                </div>
                """
            
            error_logs_html += "</div>"
        
        # Add full log section
        full_log_html = ""
        if full_log:
            full_log_html = f"""
            <div class="full-log-section">
                <h3>📝 Complete Test Execution Log</h3>
                <div class="log-controls">
                    <button onclick="downloadLog()" class="download-btn">📥 Download Log</button>
                    <button onclick="clearLog()" class="clear-btn">🗑️ Clear Display</button>
                </div>
                <div class="log-content" id="fullLogContent">{full_log}</div>
            </div>
            """
        
        return f"""
        <div class="section">
            <div class="section-header collapsible" onclick="toggleCollapsible(this)">
                📋 Raw Error Logs & Debug Information
            </div>
            <div class="section-content collapsible-content">
                {error_logs_html}
                {full_log_html}
            </div>
        </div>
        """
    
    def _generate_time_series_section(self, time_series_data: Optional[Dict], 
                                     failure_trends: Optional[Dict]) -> str:
        """Generate time series analytics section"""
        if not time_series_data and not failure_trends:
            return ""
        
        content = ""
        
        if time_series_data:
            content += f"""
            <div style="margin-bottom: 20px;">
                <h4>📈 Time Series Data</h4>
                <div style="background-color: #f8f9fa; padding: 15px; border-radius: 8px;">
                    <pre>{json.dumps(time_series_data, indent=2)}</pre>
                </div>
            </div>
            """
        
        if failure_trends:
            content += f"""
            <div>
                <h4>📉 Failure Trends</h4>
                <div style="background-color: #f8f9fa; padding: 15px; border-radius: 8px;">
                    <pre>{json.dumps(failure_trends, indent=2)}</pre>
                </div>
            </div>
            """
        
        if content:
            return f"""
            <div class="section">
                <div class="section-header collapsible" onclick="toggleCollapsible(this)">
                    📊 Analytics & Trends
                </div>
                <div class="section-content collapsible-content">
                    {content}
                </div>
            </div>
            """
        
        return ""
    
    def _categorize_failures(self, failures: list) -> Dict[str, list]:
        """Categorize failures by type"""
        categories = {
            'Authentication Issues': [],
            'Network & Connectivity': [],
            'Configuration Problems': [],
            'Permission & Access': [],
            'Dependency Issues': [],
            'Timeout & Performance': [],
            'Other Issues': []
        }
        
        for failure in failures:
            error_text = failure['error'].lower()
            categorized = False
            
            # Authentication issues
            if any(keyword in error_text for keyword in ['token', 'auth', 'credential', 'unauthorized', 'forbidden', 'login']):
                categories['Authentication Issues'].append(failure)
                categorized = True
            
            # Network issues
            elif any(keyword in error_text for keyword in ['connection', 'network', 'timeout', 'refused', 'unreachable', 'dns']):
                categories['Network & Connectivity'].append(failure)
                categorized = True
            
            # Configuration issues
            elif any(keyword in error_text for keyword in ['config', 'setting', 'environment', 'variable', 'missing', 'invalid']):
                categories['Configuration Problems'].append(failure)
                categorized = True
            
            # Permission issues
            elif any(keyword in error_text for keyword in ['permission', 'access denied', 'privilege', 'denied', 'forbidden']):
                categories['Permission & Access'].append(failure)
                categorized = True
            
            # Dependency issues
            elif any(keyword in error_text for keyword in ['import', 'module', 'package', 'dependency', 'not found', 'missing']):
                categories['Dependency Issues'].append(failure)
                categorized = True
            
            # Timeout issues
            elif any(keyword in error_text for keyword in ['timeout', 'slow', 'performance', 'hang', 'stuck']):
                categories['Timeout & Performance'].append(failure)
                categorized = True
            
            if not categorized:
                categories['Other Issues'].append(failure)
        
        # Remove empty categories
        return {k: v for k, v in categories.items() if v}
    
    def _get_category_icon(self, category: str) -> str:
        """Get icon for failure category"""
        icons = {
            'Authentication Issues': '🔐',
            'Network & Connectivity': '🌐',
            'Configuration Problems': '⚙️',
            'Permission & Access': '🚫',
            'Dependency Issues': '📦',
            'Timeout & Performance': '⏱️',
            'Other Issues': '❓'
        }
        return icons.get(category, '❓')
    
    def _get_category_description(self, category: str) -> str:
        """Get description for failure category"""
        descriptions = {
            'Authentication Issues': 'Tests failed due to authentication or authorization problems. Check API keys, tokens, and credentials.',
            'Network & Connectivity': 'Tests failed due to network connectivity issues. Check internet connection and service availability.',
            'Configuration Problems': 'Tests failed due to configuration issues. Check environment variables and settings.',
            'Permission & Access': 'Tests failed due to permission or access control issues. Check file permissions and user rights.',
            'Dependency Issues': 'Tests failed due to missing or incompatible dependencies. Check package installations.',
            'Timeout & Performance': 'Tests failed due to timeout or performance issues. Check system resources and response times.',
            'Other Issues': 'Tests failed due to other unspecified issues. Review error details for more information.'
        }
        return descriptions.get(category, 'Unknown category.')
    
    def _get_category_recommendations(self, category: str) -> str:
        """Get recommendations for failure category"""
        recommendations = {
            'Authentication Issues': [
                '<li>Verify API keys and tokens are valid and not expired</li>',
                '<li>Check authentication configuration in environment variables</li>',
                '<li>Ensure proper permissions are granted for the service</li>'
            ],
            'Network & Connectivity': [
                '<li>Check internet connectivity and firewall settings</li>',
                '<li>Verify service endpoints are accessible</li>',
                '<li>Test network connectivity to external services</li>'
            ],
            'Configuration Problems': [
                '<li>Review and update configuration files</li>',
                '<li>Check environment variables are set correctly</li>',
                '<li>Validate configuration syntax and format</li>'
            ],
            'Permission & Access': [
                '<li>Check file and directory permissions</li>',
                '<li>Verify user has necessary privileges</li>',
                '<li>Review access control lists and policies</li>'
            ],
            'Dependency Issues': [
                '<li>Install missing packages and dependencies</li>',
                '<li>Update package versions to compatible ones</li>',
                '<li>Check virtual environment setup</li>'
            ],
            'Timeout & Performance': [
                '<li>Increase timeout values if appropriate</li>',
                '<li>Check system resources (CPU, memory, disk)</li>',
                '<li>Optimize test execution and reduce load</li>'
            ],
            'Other Issues': [
                '<li>Review detailed error messages</li>',
                '<li>Check system logs for additional context</li>',
                '<li>Consider updating test environment</li>'
            ]
        }
        return ''.join(recommendations.get(category, ['<li>Review error details for specific guidance</li>']))
    
    def _generate_failure_list(self, failures: list) -> str:
        """Generate HTML for failure list"""
        if not failures:
            return "<p>No failures in this category.</p>"
        
        failure_html = ""
        for failure in failures:
            error_preview = failure['error'][:200] + "..." if len(failure['error']) > 200 else failure['error']
            failure_html += f"""
            <div class="failure-item">
                <div class="failure-name">{failure['name']}</div>
                <div class="failure-preview">{error_preview}</div>
                <div class="failure-meta">
                    <span>Exit Code: {failure['return_code']}</span>
                    {f'<span>Duration: {failure["duration"]:.2f}s</span>' if failure['duration'] is not None else ''}
                </div>
            </div>
            """
        return failure_html
    
    def _classify_error_type(self, error_text: str) -> str:
        """Classify error type based on error text"""
        error_lower = error_text.lower()
        
        if any(keyword in error_lower for keyword in ['token', 'auth', 'credential', 'unauthorized']):
            return 'authentication'
        elif any(keyword in error_lower for keyword in ['connection', 'network', 'timeout', 'refused']):
            return 'network'
        elif any(keyword in error_lower for keyword in ['permission', 'access denied', 'forbidden']):
            return 'permission'
        elif any(keyword in error_lower for keyword in ['config', 'setting', 'environment', 'variable']):
            return 'configuration'
        elif any(keyword in error_lower for keyword in ['import', 'module', 'package', 'not found']):
            return 'dependency'
        elif any(keyword in error_lower for keyword in ['timeout', 'slow', 'performance']):
            return 'timeout'
        else:
            return 'other'
    
    def _get_javascript(self) -> str:
        """Get JavaScript for interactive features"""
        return """
        function toggleCollapsible(element) {
            const content = element.nextElementSibling;
            if (content.classList.contains('active')) {
                content.classList.remove('active');
                element.style.backgroundColor = '';
            } else {
                content.classList.add('active');
                element.style.backgroundColor = '#5a6fd8';
            }
        }
        
        function toggleCategory(categoryName) {
            const content = document.getElementById('category-' + categoryName);
            if (content) {
                if (content.style.display === 'none' || content.style.display === '') {
                    content.style.display = 'block';
                } else {
                    content.style.display = 'none';
                }
            }
        }
        
        function toggleErrorLog(index) {
            const content = document.getElementById('errorLog-' + index);
            if (content) {
                content.classList.toggle('active');
            }
        }
        
        function filterErrors() {
            const searchTerm = document.getElementById('errorSearch').value.toLowerCase();
            const typeFilter = document.getElementById('errorTypeFilter').value;
            const errorItems = document.querySelectorAll('.error-log-item');
            
            errorItems.forEach(item => {
                const testName = item.getAttribute('data-test-name').toLowerCase();
                const errorType = item.getAttribute('data-error-type');
                
                const matchesSearch = testName.includes(searchTerm);
                const matchesType = !typeFilter || errorType === typeFilter;
                
                if (matchesSearch && matchesType) {
                    item.style.display = 'block';
                } else {
                    item.style.display = 'none';
                }
            });
        }
        
        function downloadLog() {
            const logContent = document.getElementById('fullLogContent').textContent;
            const blob = new Blob([logContent], { type: 'text/plain' });
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'test_log_' + new Date().toISOString().slice(0, 19).replace(/:/g, '-') + '.txt';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            window.URL.revokeObjectURL(url);
        }
        
        function clearLog() {
            if (confirm('Are you sure you want to clear the log display?')) {
                document.getElementById('fullLogContent').textContent = 'Log cleared by user.';
            }
        }
        
        // Enhanced auto-expand functionality
        document.addEventListener('DOMContentLoaded', function() {
            // Auto-expand sections with failures
            const failSections = document.querySelectorAll('.test-suite.fail');
            if (failSections.length > 0) {
                // Find the parent section and expand it
                const parentSection = failSections[0].closest('.section');
                if (parentSection) {
                    const header = parentSection.querySelector('.section-header');
                    const content = parentSection.querySelector('.section-content');
                    if (header && content) {
                        content.classList.add('active');
                        header.style.backgroundColor = '#5a6fd8';
                    }
                }
            }
            
            // Auto-expand failure insights if there are failures
            const failureInsights = document.querySelector('.failure-insights-header');
            if (failureInsights) {
                const parentSection = failureInsights.closest('.section');
                if (parentSection) {
                    const header = parentSection.querySelector('.section-header');
                    const content = parentSection.querySelector('.section-content');
                    if (header && content) {
                        content.classList.add('active');
                        header.style.backgroundColor = '#5a6fd8';
                    }
                }
            }
            
            // Auto-expand first error category if there are failures
            const firstCategory = document.querySelector('.category-header');
            if (firstCategory) {
                const categoryName = firstCategory.textContent.split(' (')[0];
                toggleCategory(categoryName);
            }
            
            // Add keyboard shortcuts
            document.addEventListener('keydown', function(e) {
                // Ctrl+F to focus search
                if (e.ctrlKey && e.key === 'f') {
                    e.preventDefault();
                    const searchInput = document.getElementById('errorSearch');
                    if (searchInput) {
                        searchInput.focus();
                    }
                }
                
                // Escape to clear search
                if (e.key === 'Escape') {
                    const searchInput = document.getElementById('errorSearch');
                    const typeFilter = document.getElementById('errorTypeFilter');
                    if (searchInput) {
                        searchInput.value = '';
                    }
                    if (typeFilter) {
                        typeFilter.value = '';
                    }
                    filterErrors();
                }
            });
            
            // Add tooltips for error type badges
            const errorBadges = document.querySelectorAll('.error-type-badge');
            errorBadges.forEach(badge => {
                badge.title = 'Click to filter by this error type';
                badge.style.cursor = 'pointer';
                badge.addEventListener('click', function() {
                    const typeFilter = document.getElementById('errorTypeFilter');
                    if (typeFilter) {
                        typeFilter.value = this.classList[1]; // Get the error type class
                        filterErrors();
                    }
                });
            });
        });
        """

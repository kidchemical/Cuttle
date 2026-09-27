#!/usr/bin/env python3
"""
Launch Report Generator for Cuttle
Generates comprehensive launch reports showing system status, startup logs, and error analysis
"""

import json
import os
import glob
import time
import platform
import psutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional

class LaunchReportGenerator:
    """Generates comprehensive launch reports for Cuttle system startup"""
    
    def __init__(self, output_dir: str = "web/logs"):
        # Handle both absolute and relative paths
        output_path = Path(output_dir)
        if not output_path.is_absolute():
            # Relative paths are resolved from the project root (src/)
            project_root = Path(__file__).parent.parent
            output_path = project_root / output_dir
        self.output_dir = output_path
        self.output_dir.mkdir(parents=True, exist_ok=True)
    
    def collect_system_info(self) -> Dict[str, Any]:
        """Collect system information"""
        return {
            'platform': platform.system(),
            'platform_version': platform.version(),
            'python_version': platform.python_version(),
            'cpu_count': psutil.cpu_count(),
            'memory_total': round(psutil.virtual_memory().total / (1024**3), 2),
            'memory_available': round(psutil.virtual_memory().available / (1024**3), 2),
            'launch_time': time.strftime('%Y-%m-%d %H:%M:%S'),
            'uptime': time.time() - psutil.boot_time()
        }
    
    def collect_service_status(self, bot_available: bool = False, active_sessions: int = 0) -> Dict[str, Any]:
        """Collect service status information"""
        return {
            'web_api': 'running',
            'bot_available': bot_available,
            'active_sessions': active_sessions,
            'port': 8080
        }
    
    def collect_launch_logs(self) -> List[Dict[str, Any]]:
        """Collect launch logs (warnings and errors from startup)"""
        launch_logs = []
        
        # Look for recent log files that might contain startup information
        log_files = []
        for pattern in ['*.log', 'launch_*.log', 'startup_*.log', 'bot_*.log']:
            log_files.extend(glob.glob(pattern))
        
        # Also check for any recent output files that might contain startup logs
        if self.output_dir.exists():
            for pattern in ['*startup*.txt', '*launch*.txt', '*init*.txt']:
                log_files.extend(glob.glob(str(self.output_dir / pattern)))
        
        # Read and parse log files for errors and warnings
        for log_file in log_files[-5:]:  # Only check last 5 log files
            try:
                with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                    lines = f.readlines()
                    for line in lines:
                        line_lower = line.lower()
                        if 'error' in line_lower or 'exception' in line_lower:
                            launch_logs.append({
                                'type': 'error',
                                'timestamp': time.strftime('%H:%M:%S'),
                                'message': line.strip(),
                                'source': os.path.basename(log_file)
                            })
                        elif 'warning' in line_lower or 'warn' in line_lower:
                            launch_logs.append({
                                'type': 'warning',
                                'timestamp': time.strftime('%H:%M:%S'),
                                'message': line.strip(),
                                'source': os.path.basename(log_file)
                            })
            except Exception as e:
                print(f"Error reading log file {log_file}: {e}")
        
        return launch_logs
    
    def check_startup_issues(self, bot_available: bool = False) -> List[Dict[str, Any]]:
        """Check for common startup issues"""
        issues = []
        
        # Check for common startup issues
        if not bot_available:
            issues.append({
                'type': 'warning',
                'timestamp': time.strftime('%H:%M:%S'),
                'message': 'Bot modules not available - some features may be limited',
                'source': 'system'
            })
        
        # Check for missing dependencies
        missing_deps = []
        try:
            import discord
        except ImportError:
            missing_deps.append('discord.py')
        
        try:
            import openai
        except ImportError:
            missing_deps.append('openai')
        
        if missing_deps:
            issues.append({
                'type': 'warning',
                'timestamp': time.strftime('%H:%M:%S'),
                'message': f'Missing dependencies: {", ".join(missing_deps)}',
                'source': 'system'
            })
        
        return issues
    
    def generate_launch_report(self, bot_available: bool = False, active_sessions: int = 0, output_file: Optional[str] = None) -> str:
        """Generate comprehensive launch report"""
        print(f"[LAUNCH] Starting launch report generation...")
        
        if output_file is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_file = f"launch_report_{timestamp}.html"
        
        if not output_file.endswith('.html'):
            output_file += '.html'
        
        output_path = self.output_dir / output_file
        print(f"[LAUNCH] Output path: {output_path}")
        
        # Collect all data
        system_info = self.collect_system_info()
        services_status = self.collect_service_status(bot_available, active_sessions)
        launch_logs = self.collect_launch_logs()
        startup_issues = self.check_startup_issues(bot_available)
        
        # Combine all issues and logs
        all_issues = launch_logs + startup_issues
        error_count = len([issue for issue in all_issues if issue['type'] == 'error'])
        warning_count = len([issue for issue in all_issues if issue['type'] == 'warning'])
        
        # Generate HTML content
        print(f"[LAUNCH] Generating HTML content...")
        html_content = self._generate_html_content(system_info, services_status, all_issues, error_count, warning_count)
        
        print(f"[LAUNCH] Writing HTML file to {output_path}")
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        print(f"[LAUNCH] Launch report generated successfully: {output_path}")
        return str(output_path)
    
    def _generate_html_content(self, system_info: Dict[str, Any], services_status: Dict[str, Any], 
                             all_issues: List[Dict[str, Any]], error_count: int, warning_count: int) -> str:
        """Generate the HTML content for the launch report"""
        
        # Calculate memory usage percentage
        memory_used = system_info['memory_total'] - system_info['memory_available']
        memory_percent = round((memory_used / system_info['memory_total']) * 100, 1) if system_info['memory_total'] > 0 else 0
        
        # Generate status cards HTML with enhanced styling
        system_cards_html = f"""
        <div class="status-card">
            <h3>🖥️ System Information</h3>
            <div class="status-item">
                <span>Platform:</span>
                <span class="status-value">{system_info['platform']}</span>
            </div>
            <div class="status-item">
                <span>Python Version:</span>
                <span class="status-value">{system_info['python_version']}</span>
            </div>
            <div class="status-item">
                <span>CPU Cores:</span>
                <span class="status-value">{system_info['cpu_count']} cores</span>
            </div>
            <div class="status-item">
                <span>Memory Total:</span>
                <span class="status-value">{system_info['memory_total']} GB</span>
            </div>
            <div class="status-item">
                <span>Memory Available:</span>
                <span class="status-value">{system_info['memory_available']} GB</span>
            </div>
            <div class="status-item">
                <span>Memory Usage:</span>
                <span class="status-value">{memory_percent}%</span>
            </div>
        </div>
        
        <div class="status-card">
            <h3>🚀 Service Status</h3>
            <div class="status-item">
                <span>Web API Server:</span>
                <span class="status-value">✅ Running</span>
            </div>
            <div class="status-item">
                <span>Discord Bot:</span>
                <span class="status-value {'error' if not services_status['bot_available'] else ''}">
                    {'❌ Offline' if not services_status['bot_available'] else '✅ Online'}
                </span>
            </div>
            <div class="status-item">
                <span>Active Sessions:</span>
                <span class="status-value">{services_status['active_sessions']}</span>
            </div>
            <div class="status-item">
                <span>Server Port:</span>
                <span class="status-value">:{services_status['port']}</span>
            </div>
            <div class="status-item">
                <span>Status:</span>
                <span class="status-value">{'⚠️ Limited' if not services_status['bot_available'] else '✅ Full'}</span>
            </div>
        </div>
        
        <div class="status-card">
            <h3>📊 Launch Summary</h3>
            <div class="status-item">
                <span>Launch Time:</span>
                <span class="status-value">{system_info['launch_time']}</span>
            </div>
            <div class="status-item">
                <span>System Uptime:</span>
                <span class="status-value">{round(system_info['uptime'] / 3600, 1)}h</span>
            </div>
            <div class="status-item">
                <span>Errors Detected:</span>
                <span class="status-value {'error' if error_count > 0 else ''}">{error_count}</span>
            </div>
            <div class="status-item">
                <span>Warnings Detected:</span>
                <span class="status-value {'warning' if warning_count > 0 else ''}">{warning_count}</span>
            </div>
            <div class="status-item">
                <span>Overall Status:</span>
                <span class="status-value {'error' if error_count > 0 else 'warning' if warning_count > 0 else ''}">
                    {'❌ Issues' if error_count > 0 else '⚠️ Warnings' if warning_count > 0 else '✅ Healthy'}
                </span>
            </div>
        </div>
        """
        
        # Generate error log section HTML
        error_log_html = ""
        if all_issues:
            error_log_html = f"""
            <div class="error-log-section">
                <h2>⚠️ Launch Logs & Issues</h2>
                <div class="log-summary">
                    <div class="log-stat error">
                        <span class="log-count">{error_count}</span>
                        <span class="log-label">Errors</span>
                    </div>
                    <div class="log-stat warning">
                        <span class="log-count">{warning_count}</span>
                        <span class="log-label">Warnings</span>
                    </div>
                </div>
                <div class="log-entries">
            """
            
            for issue in all_issues:
                log_type_class = issue['type']
                log_icon = "❌" if issue['type'] == 'error' else "⚠️"
                
                error_log_html += f"""
                    <div class="log-entry {log_type_class}">
                        <div class="log-header">
                            <span class="log-icon">{log_icon}</span>
                            <span class="log-timestamp">{issue['timestamp']}</span>
                            <span class="log-source">{issue['source']}</span>
                        </div>
                        <div class="log-message">{issue['message']}</div>
                    </div>
                """
            
            error_log_html += """
                </div>
            </div>
            """
        
        html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🚀 Cuttle Launch Report</title>
    <link rel="stylesheet" href="/css/shared_navigation.css">
    <link rel="stylesheet" href="/css/launch_report.css">
</head>
<body class="dark-mode" data-page="launch">
    <!-- Header with Navigation -->
    <header class="header">
        <div class="nav">
            <div class="logo">
                <a href="/" onclick="goToHome(); return false;">
                    <img src="/img/cuttle-logo.png" alt="Cuttle" class="header-logo">
                </a>
            </div>
            <div class="nav-menu">
                <button class="menu-toggle" onclick="toggleMenu()">
                    <span class="menu-icon">☰</span>
                </button>
            </div>
        </div>
    </header>

    <!-- Menu Dropdown -->
    <div class="menu-dropdown" id="menuDropdown">
        <div class="menu-section-title">Navigation</div>
        <a href="/" class="menu-item">🏠 Home</a>
        <a href="/chat_page.html" class="menu-item">💬 Chat Interface</a>
        <a href="/router_editor.html" class="menu-item">🔗 Router</a>
        <div class="menu-separator"></div>
        <div class="menu-section-title">Reports</div>
        <a href="/test_reports.html" class="menu-item">🧪 Test Reports</a>
        <a href="/query_reports.html" class="menu-item">📊 Query Reports</a>
        <a href="/launch_reports.html" class="menu-item active">🚀 Launch Reports</a>
        <a href="/data_reports.html" class="menu-item">📈 Data Reports</a>
        <div class="menu-separator"></div>
        <div class="menu-section-title">System</div>
        <a href="/control_panel.html" class="menu-item">🎛️ Control Panel</a>
        <a href="/settings_page.html" class="menu-item">⚙️ Settings</a>
        <a href="/about_page.html" class="menu-item">ℹ️ About</a>
        <div class="menu-separator"></div>
        <button class="menu-item" onclick="toggleTheme()">🌓 Toggle Theme</button>
    </div>

    <div class="container">
        <div class="page-header">
            <h1>🚀 Cuttle Launch Report</h1>
            <div class="subtitle">System Launch Analysis & Performance Metrics</div>
            <p><strong>Generated:</strong> {system_info['launch_time']}</p>
        </div>
        
        <div class="status-grid">
            {system_cards_html}
        </div>
        
        {error_log_html}
        
        <div class="actions">
            <a href="/" class="btn">🏠 Go to Dashboard</a>
            <a href="/api/health" class="btn secondary">🔍 API Health Check</a>
        </div>
        
        <div class="footer">
            <p>© 2024 Cuttle - AI-Powered Development Assistant</p>
            <p>Launch Report Generated on {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
        </div>
    </div>

    <script src="/js/shared_navigation.js"></script>
    <script>
        // Initialize theme and navigation
        document.addEventListener('DOMContentLoaded', function() {{
            if (window.initializeTheme) {{
                window.initializeTheme();
            }}
        }});
    </script>
</body>
</html>"""
        
        return html_content

def generate_launch_report(bot_available: bool = False, active_sessions: int = 0, output_file: Optional[str] = None) -> str:
    """Convenience function to generate a launch report"""
    generator = LaunchReportGenerator()
    return generator.generate_launch_report(bot_available, active_sessions, output_file)

if __name__ == "__main__":
    # Generate launch report
    report_path = generate_launch_report()
    if report_path:
        print(f"Launch report generated successfully: {report_path}")
    else:
        print("Failed to generate launch report")

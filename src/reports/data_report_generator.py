#!/usr/bin/env python3
"""
Data Reports Generator for Cuttle
Generates comprehensive project analytics reports showing file structure, 
code metrics, change tracking, and development statistics useful for 
Unity, C#, game development, and general software engineering.
"""

import json
import os
import glob
import subprocess
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from collections import defaultdict, Counter
import statistics
import re

class DataReportGenerator:
    """Generates comprehensive data reports for project analysis"""
    
    def __init__(self, project_root: str = ".", output_dir: str = "web/logs"):
        # Handle both absolute and relative paths
        output_path = Path(output_dir)
        if not output_path.is_absolute():
            # Relative paths are resolved from the project root (src/)
            src_root = Path(__file__).parent.parent
            output_path = src_root / output_dir
        self.project_root = Path(project_root)
        self.output_dir = output_path
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # File type categories for analysis
        self.file_categories = {
            'code': ['.py', '.cs', '.js', '.ts', '.cpp', '.c', '.h', '.hpp', '.java', '.rb', '.go', '.rs', '.php', '.swift', '.kt'],
            'config': ['.json', '.yaml', '.yml', '.toml', '.ini', '.cfg', '.conf', '.xml', '.env'],
            'docs': ['.md', '.txt', '.rst', '.doc', '.docx', '.pdf'],
            'assets': ['.png', '.jpg', '.jpeg', '.gif', '.svg', '.ico', '.bmp', '.tiff', '.webp'],
            'audio': ['.mp3', '.wav', '.ogg', '.flac', '.aac', '.m4a'],
            'video': ['.mp4', '.avi', '.mov', '.mkv', '.webm', '.flv'],
            'archives': ['.zip', '.rar', '.7z', '.tar', '.gz', '.bz2'],
            'unity': ['.unity', '.prefab', '.mat', '.asset', '.controller', '.anim'],
            'data': ['.csv', '.sql', '.db', '.sqlite', '.xlsx', '.xls'],
            'logs': ['.log', '.out', '.err'],
            'other': []
        }
        
        # Unity-specific patterns
        self.unity_patterns = {
            'scripts': r'.*\.cs$',
            'prefabs': r'.*\.prefab$',
            'materials': r'.*\.mat$',
            'scenes': r'.*\.unity$',
            'animations': r'.*\.anim$',
            'controllers': r'.*Controller\.cs$',
            'managers': r'.*Manager\.cs$',
            'ui': r'.*UI.*\.cs$'
        }
    
    def get_file_stats(self) -> Dict[str, Any]:
        """Analyze file structure and statistics"""
        stats = {
            'total_files': 0,
            'total_dirs': 0,
            'total_size': 0,
            'by_category': defaultdict(lambda: {'count': 0, 'size': 0, 'files': []}),
            'by_extension': defaultdict(lambda: {'count': 0, 'size': 0}),
            'largest_files': [],
            'directory_structure': {},
            'unity_files': defaultdict(int),
            'code_metrics': {}
        }
        
        # Walk through project directory
        for root, dirs, files in os.walk(self.project_root):
            # Skip certain directories
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ['node_modules', '__pycache__', 'venv', 'env', 'build', 'dist']]
            
            rel_root = os.path.relpath(root, self.project_root)
            if rel_root != '.':
                stats['total_dirs'] += 1
                stats['directory_structure'][rel_root] = len(files)
            
            for file in files:
                if file.startswith('.'):
                    continue
                    
                file_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_path, self.project_root)
                
                try:
                    file_size = os.path.getsize(file_path)
                    stats['total_files'] += 1
                    stats['total_size'] += file_size
                    
                    # Get file extension
                    ext = Path(file).suffix.lower()
                    if not ext:
                        ext = 'no_extension'
                    
                    # Categorize file
                    category = 'other'
                    for cat, extensions in self.file_categories.items():
                        if ext in extensions:
                            category = cat
                            break
                    
                    # Update category stats
                    stats['by_category'][category]['count'] += 1
                    stats['by_category'][category]['size'] += file_size
                    stats['by_category'][category]['files'].append({
                        'path': rel_path,
                        'size': file_size,
                        'ext': ext
                    })
                    
                    # Update extension stats
                    stats['by_extension'][ext]['count'] += 1
                    stats['by_extension'][ext]['size'] += file_size
                    
                    # Track largest files
                    stats['largest_files'].append({
                        'path': rel_path,
                        'size': file_size,
                        'category': category
                    })
                    
                    # Unity-specific analysis
                    if category == 'unity' or ext in ['.cs', '.unity', '.prefab', '.mat', '.asset']:
                        for pattern_name, pattern in self.unity_patterns.items():
                            if re.match(pattern, rel_path, re.IGNORECASE):
                                stats['unity_files'][pattern_name] += 1
                    
                except (OSError, PermissionError):
                    continue
        
        # Sort largest files
        stats['largest_files'].sort(key=lambda x: x['size'], reverse=True)
        stats['largest_files'] = stats['largest_files'][:20]
        
        return stats
    
    def get_code_metrics(self) -> Dict[str, Any]:
        """Analyze code metrics and complexity"""
        metrics = {
            'languages': defaultdict(lambda: {'files': 0, 'lines': 0, 'chars': 0}),
            'total_lines': 0,
            'total_chars': 0,
            'complexity_metrics': {},
            'import_analysis': defaultdict(int),
            'function_counts': defaultdict(int),
            'class_counts': defaultdict(int)
        }
        
        # Language patterns
        language_patterns = {
            'python': r'.*\.py$',
            'csharp': r'.*\.cs$',
            'javascript': r'.*\.js$',
            'typescript': r'.*\.ts$',
            'html': r'.*\.html$',
            'css': r'.*\.css$',
            'json': r'.*\.json$',
            'yaml': r'.*\.(yaml|yml)$'
        }
        
        for root, dirs, files in os.walk(self.project_root):
            # Skip certain directories
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ['node_modules', '__pycache__', 'venv', 'env', 'build', 'dist']]
            
            for file in files:
                file_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_path, self.project_root)
                
                # Determine language
                language = None
                for lang, pattern in language_patterns.items():
                    if re.match(pattern, rel_path, re.IGNORECASE):
                        language = lang
                        break
                
                if not language:
                    continue
                
                try:
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                        lines = content.splitlines()
                        
                        metrics['languages'][language]['files'] += 1
                        metrics['languages'][language]['lines'] += len(lines)
                        metrics['languages'][language]['chars'] += len(content)
                        metrics['total_lines'] += len(lines)
                        metrics['total_chars'] += len(content)
                        
                        # Language-specific analysis
                        if language == 'python':
                            self._analyze_python_file(content, metrics)
                        elif language == 'csharp':
                            self._analyze_csharp_file(content, metrics)
                        elif language == 'javascript':
                            self._analyze_javascript_file(content, metrics)
                        
                except (OSError, PermissionError, UnicodeDecodeError):
                    continue
        
        return metrics
    
    def _analyze_python_file(self, content: str, metrics: Dict[str, Any]):
        """Analyze Python-specific metrics"""
        # Count functions and classes
        function_count = len(re.findall(r'^def\s+\w+', content, re.MULTILINE))
        class_count = len(re.findall(r'^class\s+\w+', content, re.MULTILINE))
        
        metrics['function_counts']['python'] += function_count
        metrics['class_counts']['python'] += class_count
        
        # Count imports
        imports = re.findall(r'^(?:from\s+\w+\s+)?import\s+[\w\s,]+', content, re.MULTILINE)
        for imp in imports:
            metrics['import_analysis'][imp.strip()] += 1
    
    def _analyze_csharp_file(self, content: str, metrics: Dict[str, Any]):
        """Analyze C#-specific metrics"""
        # Count methods and classes
        method_count = len(re.findall(r'(?:public|private|protected|internal)\s+(?:static\s+)?\w+\s+\w+\s*\(', content))
        class_count = len(re.findall(r'(?:public|private|protected|internal)?\s*class\s+\w+', content))
        
        metrics['function_counts']['csharp'] += method_count
        metrics['class_counts']['csharp'] += class_count
        
        # Count using statements
        usings = re.findall(r'using\s+[\w\.]+;', content)
        for using in usings:
            metrics['import_analysis'][using.strip()] += 1
    
    def _analyze_javascript_file(self, content: str, metrics: Dict[str, Any]):
        """Analyze JavaScript-specific metrics"""
        # Count functions
        function_count = len(re.findall(r'(?:function\s+\w+|const\s+\w+\s*=\s*(?:async\s+)?\(|let\s+\w+\s*=\s*(?:async\s+)?\(|\w+\s*:\s*(?:async\s+)?\w*\s*\()', content))
        
        metrics['function_counts']['javascript'] += function_count
        
        # Count requires/imports
        imports = re.findall(r'(?:require\(|import\s+).*', content)
        for imp in imports:
            metrics['import_analysis'][imp.strip()] += 1
    
    def get_git_stats(self) -> Dict[str, Any]:
        """Get Git repository statistics"""
        git_stats = {
            'is_git_repo': False,
            'commit_count': 0,
            'branch_count': 0,
            'contributors': [],
            'recent_commits': [],
            'file_changes': defaultdict(int),
            'commit_activity': defaultdict(int)
        }
        
        try:
            # Check if it's a git repository
            result = subprocess.run(['git', 'rev-parse', '--git-dir'], 
                                  cwd=self.project_root, capture_output=True, text=True)
            if result.returncode != 0:
                return git_stats
            
            git_stats['is_git_repo'] = True
            
            # Get commit count
            result = subprocess.run(['git', 'rev-list', '--count', 'HEAD'], 
                                  cwd=self.project_root, capture_output=True, text=True)
            if result.returncode == 0:
                git_stats['commit_count'] = int(result.stdout.strip())
            
            # Get branch count
            result = subprocess.run(['git', 'branch', '-a'], 
                                  cwd=self.project_root, capture_output=True, text=True)
            if result.returncode == 0:
                git_stats['branch_count'] = len([line for line in result.stdout.splitlines() 
                                               if line.strip() and not line.startswith('*')])
            
            # Get recent commits (last 30)
            result = subprocess.run(['git', 'log', '--oneline', '-30', '--pretty=format:%h|%an|%ad|%s'], 
                                  cwd=self.project_root, capture_output=True, text=True)
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    if '|' in line:
                        parts = line.split('|', 3)
                        if len(parts) >= 4:
                            git_stats['recent_commits'].append({
                                'hash': parts[0],
                                'author': parts[1],
                                'date': parts[2],
                                'message': parts[3]
                            })
            
            # Get contributors
            result = subprocess.run(['git', 'shortlog', '-sn'], 
                                  cwd=self.project_root, capture_output=True, text=True)
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    parts = line.split('\t', 1)
                    if len(parts) == 2:
                        git_stats['contributors'].append({
                            'commits': int(parts[0]),
                            'name': parts[1]
                        })
            
            # Get file change statistics
            result = subprocess.run(['git', 'log', '--name-only', '--pretty=format:', '--since=30 days ago'], 
                                  cwd=self.project_root, capture_output=True, text=True)
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    if line.strip():
                        git_stats['file_changes'][line.strip()] += 1
            
        except Exception as e:
            print(f"Error getting git stats: {e}")
        
        return git_stats
    
    def get_disk_usage(self) -> Dict[str, Any]:
        """Calculate disk usage by directory and file type"""
        usage = {
            'total_size': 0,
            'by_directory': defaultdict(int),
            'by_category': defaultdict(int),
            'largest_directories': [],
            'size_breakdown': {}
        }
        
        for root, dirs, files in os.walk(self.project_root):
            # Skip certain directories
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ['node_modules', '__pycache__', 'venv', 'env', 'build', 'dist']]
            
            dir_size = 0
            rel_root = os.path.relpath(root, self.project_root)
            
            for file in files:
                if file.startswith('.'):
                    continue
                    
                file_path = os.path.join(root, file)
                try:
                    file_size = os.path.getsize(file_path)
                    dir_size += file_size
                    usage['total_size'] += file_size
                    
                    # Categorize by extension
                    ext = Path(file).suffix.lower()
                    category = 'other'
                    for cat, extensions in self.file_categories.items():
                        if ext in extensions:
                            category = cat
                            break
                    usage['by_category'][category] += file_size
                    
                except (OSError, PermissionError):
                    continue
            
            if dir_size > 0:
                usage['by_directory'][rel_root] = dir_size
        
        # Sort largest directories
        usage['largest_directories'] = sorted(
            [(path, size) for path, size in usage['by_directory'].items()],
            key=lambda x: x[1], reverse=True
        )[:20]
        
        return usage
    
    def generate_data_report(self, output_file: Optional[str] = None) -> str:
        """Generate comprehensive data report"""
        print(f"[DATA] Starting data report generation...")
        
        if output_file is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_file = f"data_report_{timestamp}.html"
        
        if not output_file.endswith('.html'):
            output_file += '.html'
        
        output_path = self.output_dir / output_file
        print(f"[DATA] Output path: {output_path}")
        
        # Gather all data
        print(f"[DATA] Analyzing file structure...")
        file_stats = self.get_file_stats()
        
        print(f"[DATA] Analyzing code metrics...")
        code_metrics = self.get_code_metrics()
        
        print(f"[DATA] Getting git statistics...")
        git_stats = self.get_git_stats()
        
        print(f"[DATA] Calculating disk usage...")
        disk_usage = self.get_disk_usage()
        
        # Generate HTML content
        print(f"[DATA] Generating HTML content...")
        html_content = self._generate_html_content(file_stats, code_metrics, git_stats, disk_usage)
        
        print(f"[DATA] Writing HTML file to {output_path}")
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        print(f"[DATA] Data report generated successfully: {output_path}")
        return str(output_path)
    
    def _generate_html_content(self, file_stats: Dict[str, Any], code_metrics: Dict[str, Any], 
                             git_stats: Dict[str, Any], disk_usage: Dict[str, Any]) -> str:
        """Generate the HTML content for the data report"""
        
        # Calculate additional metrics
        total_files = file_stats['total_files']
        total_dirs = file_stats['total_dirs']
        total_size = file_stats['total_size']
        total_lines = code_metrics['total_lines']
        
        # Format sizes
        def format_size(size_bytes):
            for unit in ['B', 'KB', 'MB', 'GB']:
                if size_bytes < 1024.0:
                    return f"{size_bytes:.1f} {unit}"
                size_bytes /= 1024.0
            return f"{size_bytes:.1f} TB"
        
        # Top file categories by count
        top_categories = sorted(
            [(cat, data['count']) for cat, data in file_stats['by_category'].items()],
            key=lambda x: x[1], reverse=True
        )[:10]
        
        # Top file categories by size
        top_categories_size = sorted(
            [(cat, data['size']) for cat, data in file_stats['by_category'].items()],
            key=lambda x: x[1], reverse=True
        )[:10]
        
        # Top extensions
        top_extensions = sorted(
            [(ext, data['count']) for ext, data in file_stats['by_extension'].items()],
            key=lambda x: x[1], reverse=True
        )[:15]
        
        # Unity-specific stats
        unity_stats = dict(file_stats['unity_files'])
        
        # Language stats
        language_stats = dict(code_metrics['languages'])
        
        html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>📊 Cuttle Data Report</title>
    <link rel="stylesheet" href="../css/shared_navigation.css">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/date-fns@2.29.3/index.min.js"></script>
    <style>
        /* Import shared navigation styles */
        @import url('../css/shared_navigation.css');
        
        /* Data report specific styles */
        .report-container {{
            max-width: 1400px;
            margin: 0 auto;
            padding: 20px;
            background: var(--bg-secondary);
            border-radius: 20px;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.1);
            margin-top: 20px;
        }}
        
        .page-header {{
            text-align: center;
            padding: 40px 30px;
            background: var(--header-bg);
            color: var(--text-inverse);
            margin-bottom: 30px;
            border-radius: 20px 20px 0 0;
        }}
        
        .page-header h1 {{
            font-size: 2.5em;
            font-weight: 700;
            margin-bottom: 10px;
            background: linear-gradient(135deg, #ffffff 0%, #f0f0f0 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }}
        
        .page-header .subtitle {{
            font-size: 1.2em;
            opacity: 0.9;
        }}
        
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }}
        
        .stat-card {{
            background: var(--bg-secondary);
            backdrop-filter: blur(10px);
            border-radius: 15px;
            padding: 25px;
            text-align: center;
            box-shadow: 0 8px 32px var(--card-shadow);
            transition: transform 0.3s ease;
            border: 1px solid var(--border-color);
        }}
        
        .stat-card:hover {{
            transform: translateY(-5px);
        }}
        
        .stat-card h3 {{
            color: var(--accent-color);
            font-size: 2em;
            margin-bottom: 10px;
            font-weight: 700;
        }}
        
        .stat-card p {{
            color: var(--text-secondary);
            font-size: 1.1em;
        }}
        
        .chart-container {{
            background: var(--bg-secondary);
            backdrop-filter: blur(10px);
            border-radius: 20px;
            padding: 30px;
            margin-bottom: 30px;
            box-shadow: 0 8px 32px var(--card-shadow);
            border: 1px solid var(--border-color);
        }}
        
        .chart-container h2 {{
            color: var(--accent-color);
            font-size: 1.8em;
            margin-bottom: 20px;
            text-align: center;
        }}
        
        .chart-wrapper {{
            position: relative;
            height: 400px;
            margin-bottom: 20px;
        }}
        
        .breakdown-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }}
        
        .breakdown-card {{
            background: var(--bg-secondary);
            backdrop-filter: blur(10px);
            border-radius: 15px;
            padding: 25px;
            box-shadow: 0 8px 32px var(--card-shadow);
            border: 1px solid var(--border-color);
        }}
        
        .breakdown-card h3 {{
            color: var(--accent-color);
            font-size: 1.5em;
            margin-bottom: 20px;
            text-align: center;
        }}
        
        .breakdown-item {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 10px 0;
            border-bottom: 1px solid var(--border-color);
        }}
        
        .breakdown-item:last-child {{
            border-bottom: none;
        }}
        
        .breakdown-item .name {{
            font-weight: 600;
            color: var(--text-primary);
        }}
        
        .breakdown-item .value {{
            color: var(--accent-color);
            font-weight: 700;
        }}
        
        .unity-section {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 30px;
            border-radius: 20px;
            margin-bottom: 30px;
        }}
        
        .unity-section h2 {{
            color: white;
            text-align: center;
            margin-bottom: 20px;
        }}
        
        .unity-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
        }}
        
        .unity-stat {{
            background: rgba(255, 255, 255, 0.1);
            padding: 20px;
            border-radius: 10px;
            text-align: center;
        }}
        
        .unity-stat h4 {{
            font-size: 1.8em;
            margin-bottom: 5px;
        }}
        
        .unity-stat p {{
            opacity: 0.9;
        }}
        
        .footer {{
            background: var(--bg-secondary);
            backdrop-filter: blur(10px);
            border-radius: 20px;
            padding: 20px;
            text-align: center;
            box-shadow: 0 8px 32px var(--card-shadow);
            border: 1px solid var(--border-color);
        }}
        
        .footer p {{
            color: var(--text-secondary);
            margin: 5px 0;
        }}
        
        @media (max-width: 768px) {{
            .container {{
                padding: 10px;
            }}
            
            .stats-grid {{
                grid-template-columns: 1fr;
            }}
            
            .breakdown-grid {{
                grid-template-columns: 1fr;
            }}
            
            .unity-grid {{
                grid-template-columns: 1fr;
            }}
        }}
    </style>
</head>
<body class="dark-mode" data-page="data">
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
        <button class="menu-item" onclick="event.stopPropagation(); showPage('home')">
            🏠 Home
        </button>
        <button class="menu-item" onclick="event.stopPropagation(); showPage('control')">
            🎛️ Control Panel
        </button>
        <button class="menu-item" onclick="event.stopPropagation(); showPage('settings')">
            ⚙️ Settings
        </button>
        <button class="menu-item" onclick="event.stopPropagation(); showPage('about')">
            ℹ️ About
        </button>
        <button class="menu-item" onclick="event.stopPropagation(); showPage('reports')">
            📊 Query Reports
        </button>
        <button class="menu-item" onclick="event.stopPropagation(); showPage('budget')">
            💰 Budget Reports
        </button>
        <button class="menu-item active" onclick="event.stopPropagation(); showPage('data')">
            📊 Data Reports
        </button>
        <button class="menu-item" onclick="event.stopPropagation(); showPage('test')">
            🧪 Test Reports
        </button>
        <button class="menu-item" onclick="event.stopPropagation(); showPage('launch')">
            🚀 Launch Reports
        </button>
        <div class="menu-separator"></div>
        <button class="menu-item" onclick="event.stopPropagation(); toggleTheme()">
            <span class="theme-icon">🌙</span> Theme
        </button>
    </div>

    <div class="report-container">
        <div class="page-header">
            <h1>📊 Data Report</h1>
            <p class="subtitle">Project Analytics & Development Insights</p>
            <p><strong>Project Root:</strong> {self.project_root}</p>
            <p><strong>Generated:</strong> {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
        </div>
        
        <div class="stats-grid">
            <div class="stat-card">
                <h3>{total_files:,}</h3>
                <p>Total Files</p>
            </div>
            <div class="stat-card">
                <h3>{total_dirs:,}</h3>
                <p>Directories</p>
            </div>
            <div class="stat-card">
                <h3>{format_size(total_size)}</h3>
                <p>Total Size</p>
            </div>
            <div class="stat-card">
                <h3>{total_lines:,}</h3>
                <p>Lines of Code</p>
            </div>
            <div class="stat-card">
                <h3>{git_stats['commit_count']:,}</h3>
                <p>Git Commits</p>
            </div>
            <div class="stat-card">
                <h3>{git_stats['branch_count']:,}</h3>
                <p>Git Branches</p>
            </div>
        </div>
        
        {f'''
        <div class="unity-section">
            <h2>🎮 Unity Development Stats</h2>
            <div class="unity-grid">
                <div class="unity-stat">
                    <h4>{unity_stats.get('scripts', 0)}</h4>
                    <p>C# Scripts</p>
                </div>
                <div class="unity-stat">
                    <h4>{unity_stats.get('prefabs', 0)}</h4>
                    <p>Prefabs</p>
                </div>
                <div class="unity-stat">
                    <h4>{unity_stats.get('materials', 0)}</h4>
                    <p>Materials</p>
                </div>
                <div class="unity-stat">
                    <h4>{unity_stats.get('scenes', 0)}</h4>
                    <p>Scenes</p>
                </div>
                <div class="unity-stat">
                    <h4>{unity_stats.get('animations', 0)}</h4>
                    <p>Animations</p>
                </div>
                <div class="unity-stat">
                    <h4>{unity_stats.get('controllers', 0)}</h4>
                    <p>Controllers</p>
                </div>
            </div>
        </div>
        ''' if any(unity_stats.values()) else ''}
        
        <div class="chart-container">
            <h2>📁 File Distribution by Category</h2>
            <div class="chart-wrapper">
                <canvas id="categoryChart"></canvas>
            </div>
        </div>
        
        <div class="chart-container">
            <h2>💾 Disk Usage by Category</h2>
            <div class="chart-wrapper">
                <canvas id="diskUsageChart"></canvas>
            </div>
        </div>
        
        <div class="chart-container">
            <h2>🔤 Top File Extensions</h2>
            <div class="chart-wrapper">
                <canvas id="extensionChart"></canvas>
            </div>
        </div>
        
        <div class="breakdown-grid">
            <div class="breakdown-card">
                <h3>📊 File Categories by Count</h3>
                {"".join([f'''
                <div class="breakdown-item">
                    <span class="name">{cat}</span>
                    <span class="value">{count:,}</span>
                </div>
                ''' for cat, count in top_categories])}
            </div>
            
            <div class="breakdown-card">
                <h3>💾 File Categories by Size</h3>
                {"".join([f'''
                <div class="breakdown-item">
                    <span class="name">{cat}</span>
                    <span class="value">{format_size(size)}</span>
                </div>
                ''' for cat, size in top_categories_size])}
            </div>
            
            <div class="breakdown-card">
                <h3>💻 Code Languages</h3>
                {"".join([f'''
                <div class="breakdown-item">
                    <span class="name">{lang}</span>
                    <span class="value">{data['lines']:,} lines</span>
                </div>
                ''' for lang, data in language_stats.items() if data['lines'] > 0])}
            </div>
        </div>
        
        <div class="chart-container">
            <h2>🏗️ Largest Directories</h2>
            <div class="chart-wrapper">
                <canvas id="directoryChart"></canvas>
            </div>
        </div>
        
        {f'''
        <div class="breakdown-grid">
            <div class="breakdown-card">
                <h3>👥 Git Contributors</h3>
                {"".join([f'''
                <div class="breakdown-item">
                    <span class="name">{contrib['name']}</span>
                    <span class="value">{contrib['commits']} commits</span>
                </div>
                ''' for contrib in git_stats['contributors'][:10]])}
            </div>
            
            <div class="breakdown-card">
                <h3>📝 Recent Commits</h3>
                {"".join([f'''
                <div class="breakdown-item">
                    <span class="name">{commit['message'][:50]}...</span>
                    <span class="value">{commit['author']}</span>
                </div>
                ''' for commit in git_stats['recent_commits'][:10]])}
            </div>
        </div>
        ''' if git_stats['is_git_repo'] else ''}
        
        <div class="footer">
            <p>© 2024 Cuttle - AI-Powered Development Assistant</p>
            <p>Data Report Generated on {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
        </div>
    </div>

    <script>
        // Chart.js configuration
        Chart.defaults.font.family = "'Segoe UI', Tahoma, Geneva, Verdana, sans-serif";
        Chart.defaults.color = getComputedStyle(document.documentElement).getPropertyValue('--text-secondary');
        
        // Category distribution chart
        const categoryData = {json.dumps(dict(top_categories))};
        const categoryCtx = document.getElementById('categoryChart').getContext('2d');
        new Chart(categoryCtx, {{
            type: 'doughnut',
            data: {{
                labels: Object.keys(categoryData),
                datasets: [{{
                    data: Object.values(categoryData),
                    backgroundColor: [
                        '#8B5CF6', '#7C3AED', '#A855F7', '#C084FC', '#DDD6FE',
                        '#EC4899', '#F43F5E', '#F97316', '#EAB308', '#22C55E'
                    ],
                    borderWidth: 2,
                    borderColor: '#fff'
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{
                    legend: {{
                        position: 'right',
                    }},
                    title: {{
                        display: true,
                        text: 'File Distribution by Category'
                    }}
                }}
            }}
        }});
        
        // Disk usage chart
        const diskUsageData = {json.dumps(dict(top_categories_size))};
        const diskCtx = document.getElementById('diskUsageChart').getContext('2d');
        new Chart(diskCtx, {{
            type: 'bar',
            data: {{
                labels: Object.keys(diskUsageData),
                datasets: [{{
                    label: 'Size (bytes)',
                    data: Object.values(diskUsageData),
                    backgroundColor: 'rgba(139, 92, 246, 0.7)',
                    borderColor: '#8B5CF6',
                    borderWidth: 1
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                scales: {{
                    y: {{
                        beginAtZero: true,
                        title: {{
                            display: true,
                            text: 'Size (bytes)'
                        }}
                    }},
                    x: {{
                        title: {{
                            display: true,
                            text: 'File Category'
                        }}
                    }}
                }},
                plugins: {{
                    legend: {{
                        position: 'top',
                    }},
                    title: {{
                        display: true,
                        text: 'Disk Usage by Category'
                    }}
                }}
            }}
        }});
        
        // Extension chart
        const extensionData = {json.dumps(dict(top_extensions))};
        const extensionCtx = document.getElementById('extensionChart').getContext('2d');
        new Chart(extensionCtx, {{
            type: 'horizontalBar',
            data: {{
                labels: Object.keys(extensionData),
                datasets: [{{
                    label: 'File Count',
                    data: Object.values(extensionData),
                    backgroundColor: 'rgba(139, 92, 246, 0.7)',
                    borderColor: '#8B5CF6',
                    borderWidth: 1
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                scales: {{
                    x: {{
                        beginAtZero: true,
                        title: {{
                            display: true,
                            text: 'File Count'
                        }}
                    }},
                    y: {{
                        title: {{
                            display: true,
                            text: 'File Extension'
                        }}
                    }}
                }},
                plugins: {{
                    legend: {{
                        position: 'top',
                    }},
                    title: {{
                        display: true,
                        text: 'Top File Extensions'
                    }}
                }}
            }}
        }});
        
        // Directory chart
        const directoryData = {json.dumps(dict(disk_usage['largest_directories'][:10]))};
        const directoryCtx = document.getElementById('directoryChart').getContext('2d');
        new Chart(directoryCtx, {{
            type: 'horizontalBar',
            data: {{
                labels: Object.keys(directoryData),
                datasets: [{{
                    label: 'Size (bytes)',
                    data: Object.values(directoryData),
                    backgroundColor: 'rgba(139, 92, 246, 0.7)',
                    borderColor: '#8B5CF6',
                    borderWidth: 1
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                scales: {{
                    x: {{
                        beginAtZero: true,
                        title: {{
                            display: true,
                            text: 'Size (bytes)'
                        }}
                    }},
                    y: {{
                        title: {{
                            display: true,
                            text: 'Directory'
                        }}
                    }}
                }},
                plugins: {{
                    legend: {{
                        position: 'top',
                    }},
                    title: {{
                        display: true,
                        text: 'Largest Directories'
                    }}
                }}
            }}
        }});
    </script>
    
    <script src="../js/shared_navigation.js"></script>
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

def generate_data_report(output_file: Optional[str] = None) -> str:
    """Convenience function to generate a data report"""
    generator = DataReportGenerator()
    return generator.generate_data_report(output_file)

if __name__ == "__main__":
    # Generate data report
    report_path = generate_data_report()
    if report_path:
        print(f"Data report generated successfully: {report_path}")
    else:
        print("Failed to generate data report")

#!/usr/bin/env python3
"""
Budget Report Generator for Cuttle
Generates comprehensive budget reports showing aggregated expenses across all queries
with breakdowns by LLM model, agent stage mode, and node type.
"""

import json
import os
import glob
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from collections import defaultdict, Counter
import statistics

class BudgetReportGenerator:
    """Generates comprehensive budget reports for Cuttle query expenses"""
    
    def __init__(self, output_dir: str = "web/logs"):
        # Handle both absolute and relative paths
        output_path = Path(output_dir)
        if not output_path.is_absolute():
            # Relative paths are resolved from the project root (src/)
            project_root = Path(__file__).parent.parent
            output_path = project_root / output_dir
        self.output_dir = output_path
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Model pricing (per 1M tokens) - updated as of 2024
        self.model_pricing = {
            "gpt-4o-mini": {"input": 0.15, "output": 0.60},
            "gpt-4o": {"input": 2.50, "output": 10.00},
            "gpt-4-turbo": {"input": 10.00, "output": 30.00},
            "gpt-3.5-turbo": {"input": 0.50, "output": 1.50},
            "claude-3-5-sonnet-latest": {"input": 3.00, "output": 15.00},
            "claude-3-haiku": {"input": 0.25, "output": 1.25},
            "claude-3-opus": {"input": 15.00, "output": 75.00},
            "claude-3-sonnet": {"input": 3.00, "output": 15.00}
        }
    
    def load_all_query_reports(self) -> List[Dict[str, Any]]:
        """Load all query report JSON files"""
        query_reports = []
        
        # Find all query report JSON files (check both old output dir and new logs dir)
        json_files = glob.glob(str(self.output_dir / "query_data_*.json"))
        if not json_files:
            # Fallback to old output directory
            old_output_dir = Path("output")
            json_files = glob.glob(str(old_output_dir / "query_data_*.json"))
        print(f"[BUDGET] Found {len(json_files)} query data files")
        
        for json_file in json_files:
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    query_reports.append(data)
            except Exception as e:
                print(f"Error loading {json_file}: {e}")
                continue
        
        print(f"[BUDGET] Successfully loaded {len(query_reports)} query reports")
        return query_reports
    
    def calculate_cost(self, model: str, prompt_tokens: int, completion_tokens: int) -> float:
        """Calculate cost for a given model and token usage"""
        if model not in self.model_pricing:
            # Default pricing for unknown models
            return (prompt_tokens * 0.001 + completion_tokens * 0.002) / 1000
        
        pricing = self.model_pricing[model]
        input_cost = (prompt_tokens / 1_000_000) * pricing["input"]
        output_cost = (completion_tokens / 1_000_000) * pricing["output"]
        
        return input_cost + output_cost
    
    def aggregate_expenses(self, query_reports: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Aggregate expenses from all query reports"""
        aggregated = {
            "total_queries": len(query_reports),
            "total_cost": 0.0,
            "total_tokens": 0,
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
            "date_range": {"start": None, "end": None},
            "by_model": defaultdict(lambda: {
                "cost": 0.0,
                "tokens": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "calls": 0,
                "queries": set()
            }),
            "by_stage_type": defaultdict(lambda: {
                "cost": 0.0,
                "tokens": 0,
                "calls": 0,
                "queries": set()
            }),
            "by_node_type": defaultdict(lambda: {
                "cost": 0.0,
                "tokens": 0,
                "calls": 0,
                "queries": set()
            }),
            "daily_expenses": defaultdict(float),
            "hourly_expenses": defaultdict(float),
            "query_costs": [],
            "model_usage": defaultdict(int),
            "stage_usage": defaultdict(int),
            "node_usage": defaultdict(int)
        }
        
        for report in query_reports:
            query_id = report.get("query_id", "unknown")
            timestamp = report.get("timestamp", "")
            
            # Update date range
            if timestamp:
                try:
                    report_date = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                    if aggregated["date_range"]["start"] is None or report_date < aggregated["date_range"]["start"]:
                        aggregated["date_range"]["start"] = report_date
                    if aggregated["date_range"]["end"] is None or report_date > aggregated["date_range"]["end"]:
                        aggregated["date_range"]["end"] = report_date
                    
                    # Daily expenses
                    date_key = report_date.strftime("%Y-%m-%d")
                    aggregated["daily_expenses"][date_key] += report.get("total_cost", 0.0)
                    
                    # Hourly expenses
                    hour_key = report_date.strftime("%Y-%m-%d %H:00")
                    aggregated["hourly_expenses"][hour_key] += report.get("total_cost", 0.0)
                    
                except Exception as e:
                    print(f"Error parsing timestamp {timestamp}: {e}")
            
            # Aggregate LLM calls
            llm_calls = report.get("llm_calls", [])
            for call in llm_calls:
                model = call.get("model", "unknown")
                cost = call.get("cost", 0.0)
                tokens = call.get("total_tokens", 0)
                prompt_tokens = call.get("prompt_tokens", 0)
                completion_tokens = call.get("completion_tokens", 0)
                
                # Update totals
                aggregated["total_cost"] += cost
                aggregated["total_tokens"] += tokens
                aggregated["total_prompt_tokens"] += prompt_tokens
                aggregated["total_completion_tokens"] += completion_tokens
                
                # Update by model
                aggregated["by_model"][model]["cost"] += cost
                aggregated["by_model"][model]["tokens"] += tokens
                aggregated["by_model"][model]["prompt_tokens"] += prompt_tokens
                aggregated["by_model"][model]["completion_tokens"] += completion_tokens
                aggregated["by_model"][model]["calls"] += 1
                aggregated["by_model"][model]["queries"].add(query_id)
                
                aggregated["model_usage"][model] += 1
            
            # Aggregate execution stages
            execution_stages = report.get("execution_stages", [])
            for stage in execution_stages:
                stage_type = stage.get("type", "unknown")
                stage_cost = 0.0
                stage_tokens = 0
                
                # Calculate stage cost from tokens if available
                if stage.get("tokens"):
                    tokens_data = stage["tokens"]
                    if stage.get("model"):
                        stage_cost = self.calculate_cost(
                            stage["model"],
                            tokens_data.get("prompt_tokens", 0),
                            tokens_data.get("completion_tokens", 0)
                        )
                        stage_tokens = tokens_data.get("total_tokens", 0)
                
                # Update by stage type
                aggregated["by_stage_type"][stage_type]["cost"] += stage_cost
                aggregated["by_stage_type"][stage_type]["tokens"] += stage_tokens
                aggregated["by_stage_type"][stage_type]["calls"] += 1
                aggregated["by_stage_type"][stage_type]["queries"].add(query_id)
                
                aggregated["stage_usage"][stage_type] += 1
            
            # Aggregate tool calls (as node types)
            tool_calls = report.get("tool_calls", [])
            for tool in tool_calls:
                tool_name = tool.get("tool_name", "unknown")
                
                # Update by node type
                aggregated["by_node_type"][tool_name]["calls"] += 1
                aggregated["by_node_type"][tool_name]["queries"].add(query_id)
                
                aggregated["node_usage"][tool_name] += 1
            
            # Store individual query cost
            aggregated["query_costs"].append({
                "query_id": query_id,
                "cost": report.get("total_cost", 0.0),
                "tokens": report.get("total_tokens", 0),
                "timestamp": timestamp,
                "success": report.get("success", True)
            })
        
        # Convert sets to counts for JSON serialization
        for model_data in aggregated["by_model"].values():
            model_data["queries"] = len(model_data["queries"])
        
        for stage_data in aggregated["by_stage_type"].values():
            stage_data["queries"] = len(stage_data["queries"])
        
        for node_data in aggregated["by_node_type"].values():
            node_data["queries"] = len(node_data["queries"])
        
        return aggregated
    
    def generate_time_series_data(self, aggregated: Dict[str, Any]) -> Dict[str, Any]:
        """Generate time series data for charts"""
        time_series = {
            "daily": [],
            "hourly": [],
            "cumulative": []
        }
        
        # Daily time series
        daily_expenses = dict(aggregated["daily_expenses"])
        if daily_expenses:
            sorted_days = sorted(daily_expenses.keys())
            cumulative_cost = 0.0
            
            for day in sorted_days:
                daily_cost = daily_expenses[day]
                cumulative_cost += daily_cost
                
                time_series["daily"].append({
                    "date": day,
                    "cost": daily_cost,
                    "cumulative": cumulative_cost
                })
        
        # Hourly time series (last 7 days)
        hourly_expenses = dict(aggregated["hourly_expenses"])
        if hourly_expenses:
            sorted_hours = sorted(hourly_expenses.keys())
            # Limit to last 7 days for performance
            if len(sorted_hours) > 168:  # 7 days * 24 hours
                sorted_hours = sorted_hours[-168:]
            
            for hour in sorted_hours:
                time_series["hourly"].append({
                    "datetime": hour,
                    "cost": hourly_expenses[hour]
                })
        
        return time_series
    
    def generate_budget_report(self, output_file: Optional[str] = None) -> str:
        """Generate comprehensive budget report"""
        print(f"[BUDGET] Starting budget report generation...")
        
        if output_file is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_file = f"budget_report_{timestamp}.html"
        
        if not output_file.endswith('.html'):
            output_file += '.html'
        
        output_path = self.output_dir / output_file
        print(f"[BUDGET] Output path: {output_path}")
        
        # Load and aggregate data
        query_reports = self.load_all_query_reports()
        if not query_reports:
            print("[BUDGET] No query reports found to generate budget report")
            return None
        
        print(f"[BUDGET] Processing {len(query_reports)} query reports...")
        aggregated = self.aggregate_expenses(query_reports)
        time_series = self.generate_time_series_data(aggregated)
        
        # Generate HTML content
        print(f"[BUDGET] Generating HTML content...")
        html_content = self._generate_html_content(aggregated, time_series)
        
        print(f"[BUDGET] Writing HTML file to {output_path}")
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        print(f"[BUDGET] Budget report generated successfully: {output_path}")
        return str(output_path)
    
    def _generate_html_content(self, aggregated: Dict[str, Any], time_series: Dict[str, Any]) -> str:
        """Generate the HTML content for the budget report"""
        
        # Calculate statistics
        total_cost = aggregated["total_cost"]
        total_queries = aggregated["total_queries"]
        avg_cost_per_query = total_cost / total_queries if total_queries > 0 else 0
        
        # Top models by cost
        top_models = sorted(
            aggregated["by_model"].items(),
            key=lambda x: x[1]["cost"],
            reverse=True
        )[:5]
        
        # Top stage types by cost
        top_stages = sorted(
            aggregated["by_stage_type"].items(),
            key=lambda x: x[1]["cost"],
            reverse=True
        )[:5]
        
        # Top node types by usage
        top_nodes = sorted(
            aggregated["by_node_type"].items(),
            key=lambda x: x[1]["calls"],
            reverse=True
        )[:5]
        
        # Date range
        date_range = aggregated["date_range"]
        date_range_str = "N/A"
        if date_range["start"] and date_range["end"]:
            start_str = date_range["start"].strftime("%Y-%m-%d")
            end_str = date_range["end"].strftime("%Y-%m-%d")
            date_range_str = f"{start_str} to {end_str}"
        
        html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>💰 Cuttle Budget Report</title>
            <link rel="stylesheet" href="../css/shared_navigation.css">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/date-fns@2.29.3/index.min.js"></script>
    <style>
        /* Import shared navigation styles */
        @import url('../css/shared_navigation.css');
        
        /* Budget report specific styles */
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
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
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
            
            .header h1 {{
                font-size: 2em;
            }}
            
            .stats-grid {{
                grid-template-columns: 1fr;
            }}
            
            .breakdown-grid {{
                grid-template-columns: 1fr;
            }}
        }}
    </style>
</head>
<body class="dark-mode" data-page="budget">
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
        <div class="menu-separator"></div>
        <div class="menu-section-title">Admin Tools</div>
        <button class="menu-item" onclick="event.stopPropagation(); showPage('data')">
            📊 Reports Hub
        </button>
        <div class="menu-separator"></div>
        <button class="menu-item" onclick="event.stopPropagation(); toggleTheme()">
            <span class="theme-icon">🌙</span> Theme
        </button>
    </div>

    <div class="report-container">
        <div class="page-header">
            <h1>💰 Budget Report</h1>
            <p class="subtitle">Comprehensive Expense Analysis & Usage Statistics</p>
            <p><strong>Date Range:</strong> {date_range_str}</p>
            <p><strong>Generated:</strong> {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
        </div>
        
        <div class="stats-grid">
            <div class="stat-card">
                <h3>${total_cost:.4f}</h3>
                <p>Total Cost</p>
            </div>
            <div class="stat-card">
                <h3>{total_queries:,}</h3>
                <p>Total Queries</p>
            </div>
            <div class="stat-card">
                <h3>${avg_cost_per_query:.4f}</h3>
                <p>Avg Cost per Query</p>
            </div>
            <div class="stat-card">
                <h3>{aggregated["total_tokens"]:,}</h3>
                <p>Total Tokens</p>
            </div>
        </div>
        
        <div class="chart-container">
            <h2>📈 Daily Expense Trends</h2>
            <div class="chart-wrapper">
                <canvas id="dailyChart"></canvas>
            </div>
        </div>
        
        <div class="chart-container">
            <h2>🕐 Hourly Expense Distribution (Last 7 Days)</h2>
            <div class="chart-wrapper">
                <canvas id="hourlyChart"></canvas>
            </div>
        </div>
        
        <div class="breakdown-grid">
            <div class="breakdown-card">
                <h3>🤖 Top Models by Cost</h3>
                {"".join([f'''
                <div class="breakdown-item">
                    <span class="name">{model}</span>
                    <span class="value">${data["cost"]:.4f}</span>
                </div>
                ''' for model, data in top_models])}
            </div>
            
            <div class="breakdown-card">
                <h3>⚙️ Top Stage Types by Cost</h3>
                {"".join([f'''
                <div class="breakdown-item">
                    <span class="name">{stage_type}</span>
                    <span class="value">${data["cost"]:.4f}</span>
                </div>
                ''' for stage_type, data in top_stages])}
            </div>
            
            <div class="breakdown-card">
                <h3>🔧 Top Node Types by Usage</h3>
                {"".join([f'''
                <div class="breakdown-item">
                    <span class="name">{node_type}</span>
                    <span class="value">{data["calls"]} calls</span>
                </div>
                ''' for node_type, data in top_nodes])}
            </div>
        </div>
        
        <div class="chart-container">
            <h2>🥧 Cost Breakdown by Model</h2>
            <div class="chart-wrapper">
                <canvas id="modelPieChart"></canvas>
            </div>
        </div>
        
        <div class="chart-container">
            <h2>📊 Stage Type Distribution</h2>
            <div class="chart-wrapper">
                <canvas id="stageBarChart"></canvas>
            </div>
        </div>
        
        <div class="footer">
            <p>© 2024 Cuttle - AI-Powered Development Assistant</p>
            <p>Budget Report Generated on {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
        </div>
    </div>
    </div>

    <script>
        // Chart.js configuration
        Chart.defaults.font.family = "'Segoe UI', Tahoma, Geneva, Verdana, sans-serif";
        Chart.defaults.color = getComputedStyle(document.documentElement).getPropertyValue('--text-secondary');
        
        // Daily expense chart
        const dailyData = {json.dumps(time_series["daily"])};
        const dailyCtx = document.getElementById('dailyChart').getContext('2d');
        new Chart(dailyCtx, {{
            type: 'line',
            data: {{
                labels: dailyData.map(d => d.date),
                datasets: [{{
                    label: 'Daily Cost ($)',
                    data: dailyData.map(d => d.cost),
                    borderColor: '#8B5CF6',
                    backgroundColor: 'rgba(139, 92, 246, 0.1)',
                    borderWidth: 3,
                    fill: true,
                    tension: 0.4
                }}, {{
                    label: 'Cumulative Cost ($)',
                    data: dailyData.map(d => d.cumulative),
                    borderColor: '#7C3AED',
                    backgroundColor: 'rgba(124, 58, 237, 0.1)',
                    borderWidth: 3,
                    fill: false,
                    tension: 0.4,
                    yAxisID: 'y1'
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
                            text: 'Daily Cost ($)'
                        }}
                    }},
                    y1: {{
                        type: 'linear',
                        display: true,
                        position: 'right',
                        title: {{
                            display: true,
                            text: 'Cumulative Cost ($)'
                        }},
                        grid: {{
                            drawOnChartArea: false,
                        }},
                    }}
                }},
                plugins: {{
                    legend: {{
                        position: 'top',
                    }},
                    title: {{
                        display: true,
                        text: 'Daily Expense Trends'
                    }}
                }}
            }}
        }});
        
        // Hourly expense chart
        const hourlyData = {json.dumps(time_series["hourly"])};
        const hourlyCtx = document.getElementById('hourlyChart').getContext('2d');
        new Chart(hourlyCtx, {{
            type: 'bar',
            data: {{
                labels: hourlyData.map(h => h.datetime),
                datasets: [{{
                    label: 'Hourly Cost ($)',
                    data: hourlyData.map(h => h.cost),
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
                            text: 'Cost ($)'
                        }}
                    }},
                    x: {{
                        title: {{
                            display: true,
                            text: 'Date & Hour'
                        }}
                    }}
                }},
                plugins: {{
                    legend: {{
                        position: 'top',
                    }},
                    title: {{
                        display: true,
                        text: 'Hourly Expense Distribution'
                    }}
                }}
            }}
        }});
        
        // Model pie chart
        const modelData = {json.dumps(dict(top_models))};
        const modelCtx = document.getElementById('modelPieChart').getContext('2d');
        new Chart(modelCtx, {{
            type: 'doughnut',
            data: {{
                labels: Object.keys(modelData),
                datasets: [{{
                    data: Object.values(modelData).map(d => d.cost),
                    backgroundColor: [
                        '#8B5CF6',
                        '#7C3AED',
                        '#A855F7',
                        '#C084FC',
                        '#DDD6FE'
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
                        text: 'Cost Distribution by Model'
                    }}
                }}
            }}
        }});
        
        // Stage bar chart
        const stageData = {json.dumps(dict(top_stages))};
        const stageCtx = document.getElementById('stageBarChart').getContext('2d');
        new Chart(stageCtx, {{
            type: 'bar',
            data: {{
                labels: Object.keys(stageData),
                datasets: [{{
                    label: 'Cost ($)',
                    data: Object.values(stageData).map(d => d.cost),
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
                            text: 'Cost ($)'
                        }}
                    }},
                    x: {{
                        title: {{
                            display: true,
                            text: 'Stage Type'
                        }}
                    }}
                }},
                plugins: {{
                    legend: {{
                        position: 'top',
                    }},
                    title: {{
                        display: true,
                        text: 'Cost by Stage Type'
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

def generate_budget_report(output_file: Optional[str] = None) -> str:
    """Convenience function to generate a budget report"""
    generator = BudgetReportGenerator()
    return generator.generate_budget_report(output_file)

if __name__ == "__main__":
    # Generate budget report
    report_path = generate_budget_report()
    if report_path:
        print(f"Budget report generated successfully: {report_path}")
    else:
        print("Failed to generate budget report")

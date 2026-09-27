# Claude Code Tool Usage Tracking

This document describes the comprehensive usage tracking features added to the Claude Code Tool integration for the PC AI Agent.

## 🎯 Overview

The Claude Code Tool now includes extensive usage tracking capabilities that monitor token consumption, costs, and execution metrics. This information is logged, displayed in responses, and integrated into the query report system.

## ✨ Features

### 1. Automatic Usage Tracking
- **Token Counting**: Automatically parses Claude CLI output to extract token usage information
- **Cost Estimation**: Calculates estimated costs based on current Anthropic pricing
- **Session Tracking**: Maintains usage statistics per session and globally
- **Call History**: Keeps a rolling history of recent API calls

### 2. Enhanced Response Formatting
Claude responses now include usage information:
```
✅ Claude Code in `/path/to/project` (took 2.34s) | 📊 1234 tokens (400→834) | $0.001234 | 🦑 haiku | Session: 3 calls, 4567 tokens, $0.0045
```

### 3. Query Report Integration
- **Dedicated Section**: Claude usage gets its own section in query reports
- **Model Breakdown**: Shows usage statistics by model (Haiku, Sonnet, Opus)
- **Recent Calls**: Lists the last 5 API calls with details
- **CLI Tool Integration**: Includes data from `cc usage` and `claude-monitor` if available

### 4. CLI Usage Monitoring
Support for external CLI tools:
- **cc usage**: Claude Code usage command
- **claude-monitor**: Advanced usage monitoring tool
- **Automatic Detection**: Checks for tool availability and retrieves data

## 🔧 Implementation Details

### Usage Data Structure
```python
{
    "total_calls": 5,
    "total_tokens": 12345,
    "total_cost": 0.0123,
    "model_usage": {
        "haiku": {"calls": 3, "tokens": 8000, "cost": 0.008},
        "sonnet": {"calls": 2, "tokens": 4345, "cost": 0.0043}
    },
    "session_usage": {
        "session_abc123": {"calls": 5, "tokens": 12345, "cost": 0.0123}
    },
    "call_history": [...],
    "cli_usage_data": {...}
}
```

### Token Parsing Patterns
The system recognizes multiple token usage patterns from Claude CLI output:
- `Used 1234 tokens (123 input + 1111 output)`
- `Token usage: 1234 total (123 input, 1111 output)`
- `📊 Tokens: 1234 (123 in, 1111 out)`
- `Tokens: 1234 (123 input, 1111 output)`

### Model Pricing (2024)
- **Haiku**: $0.25/1M input, $1.25/1M output
- **Sonnet**: $3.00/1M input, $15.00/1M output  
- **Opus**: $15.00/1M input, $75.00/1M output

## 📊 Usage Examples

### Basic Usage
```python
from claude_code_tool import get_claude_code_tool

# Get Claude tool instance
claude_tool = get_claude_code_tool("my_session")

# Execute command (usage automatically tracked)
result = await claude_tool.execute_claude_command("Create a Python function")

# Get usage summary
usage = claude_tool.get_usage_summary()
print(f"Total calls: {usage['total_calls']}")
print(f"Total tokens: {usage['total_tokens']}")
print(f"Total cost: ${usage['total_cost']:.6f}")
```

### Integration with Query Reports
```python
from query_report_generator import start_query_tracking, finish_query_tracking
from claude_code_tool import get_claude_code_tool

# Start query tracking
query_id = start_query_tracking("User request", user_context)

# Execute Claude command
claude_tool = get_claude_code_tool()
result = await claude_tool.execute_claude_command("User's request")

# Usage data is automatically added to query report
# Finish tracking to generate report
report_path, json_path = finish_query_tracking(True)
```

### CLI Tool Integration
```python
# Check available CLI tools
cli_tools = claude_tool._check_cli_usage_tools()
print(f"cc usage available: {cli_tools['cc_usage']}")
print(f"claude-monitor available: {cli_tools['claude_monitor']}")

# Get CLI usage data
cli_data = claude_tool.get_cli_usage_data()
if cli_data['cc_usage']:
    print("cc usage output:", cli_data['cc_usage']['output'])
```

## 🗂️ File Structure

### Core Files
- `claude_code_tool.py`: Main Claude tool with usage tracking
- `query_report_generator.py`: Enhanced with Claude usage integration
- `test_claude_usage.py`: Test script for usage tracking features

### Generated Files
- `output/claude_usage_{session_id}.json`: Session-specific usage data
- `output/query_report_{query_id}_{timestamp}.html`: HTML reports with Claude usage
- `output/query_data_{query_id}_{timestamp}.json`: Raw query execution data

## 🚀 Getting Started

### 1. Test the Features
```bash
python test_claude_usage.py
```

### 2. View Usage Data
Check the generated files in the `output/` directory:
- JSON files contain raw usage data
- HTML files provide visual reports with usage breakdowns

### 3. Monitor CLI Tools
If you have `cc` or `claude-monitor` installed, the system will automatically:
- Detect their availability
- Retrieve usage data from them
- Include the data in query reports

## 🔍 Query Report Features

### Claude Usage Section
The query reports now include a dedicated "🦑 Claude Usage" section with:

1. **Usage Summary**: Total calls, tokens, and cost
2. **Model Breakdown**: Statistics per model used
3. **Recent Calls**: Last 5 API calls with details
4. **CLI Integration**: Data from external monitoring tools

### Visual Elements
- **Token Counters**: Shows input→output token breakdown
- **Cost Estimations**: Real-time cost calculations
- **Model Indicators**: Visual model identification
- **Session Tracking**: Multi-session usage aggregation

## ⚙️ Configuration

### Model Selection
The tool supports multiple Claude models with automatic pricing:
```python
# Use different models
claude_tool = get_claude_code_tool(model="haiku")    # Cheapest
claude_tool = get_claude_code_tool(model="sonnet")   # Balanced
claude_tool = get_claude_code_tool(model="opus")     # Most capable
```

### Session Management
```python
# Create new session
claude_tool = get_claude_code_tool("unique_session_id")

# Get session-specific usage
usage_data = claude_tool.get_usage_for_query_report()
```

## 🔧 Advanced Features

### Custom Token Parsing
The system includes robust token parsing that handles various output formats and gracefully falls back to estimation when exact parsing fails.

### Persistent Storage
Usage data is automatically saved every 10 calls and can be manually saved:
```python
claude_tool.save_usage_data("custom_path.json")
```

### Integration Points
- **Query Reports**: Automatic integration with existing report system
- **Discord Responses**: Enhanced formatting with usage information
- **Web UI**: Usage data available through web interface
- **CLI Tools**: External tool integration for comprehensive monitoring

## 📈 Benefits

1. **Cost Transparency**: Always know how much each request costs
2. **Usage Monitoring**: Track token consumption patterns
3. **Performance Insights**: Monitor execution times and efficiency
4. **Budget Management**: Set usage limits and monitor spending
5. **Debugging Support**: Detailed call history for troubleshooting
6. **Reporting**: Professional usage reports for documentation

## 🔮 Future Enhancements

Potential future improvements:
- **Usage Alerts**: Notify when approaching limits
- **Budget Tracking**: Set and monitor spending limits
- **Analytics Dashboard**: Visual usage analytics
- **Export Features**: Export usage data in various formats
- **Integration APIs**: REST APIs for usage data access

## 🐛 Troubleshooting

### Common Issues

1. **No Token Data**: If token parsing fails, the system will estimate based on total tokens
2. **CLI Tools Not Found**: The system continues to work without external CLI tools
3. **Missing Usage in Reports**: Ensure query tracking is properly initialized

### Debug Mode
Enable debug output by checking the console for `[Claude Code]` prefixed messages.

## 📝 Notes

- Usage data is stored in memory during execution and saved periodically
- Cost estimates are based on current Anthropic pricing (2024)
- Token parsing patterns are updated as Claude CLI output formats change
- The system gracefully handles missing or malformed usage data
- All features are backward compatible with existing code

---

*This usage tracking system provides comprehensive monitoring of Claude API usage while maintaining the simplicity and reliability of the existing Claude Code Tool integration.*

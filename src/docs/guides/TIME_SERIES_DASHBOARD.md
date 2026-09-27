# Time Series Dashboard Documentation

## Overview

The Time Series Dashboard provides comprehensive visualization of test failure trends over time, helping you identify patterns, track improvements, and monitor the health of your Cuttle test suite.

## Features

### 👁️ View Mode Toggle
- **Simple View** (Default) - Clean, focused view showing only essential information:
  - Test summary statistics
  - AI summary
  - Time series dashboard
- **Full View** - Complete detailed view showing all sections:
  - All simple view content
  - Detailed test results
  - Environment information
  - Full test logs
- **Persistent Preferences** - View mode preference saved in browser localStorage
- **Smooth Transitions** - Animated transitions between view modes

### 📈 Interactive Time Series Chart
- **Real-time visualization** of test failures and successes over time
- **Dual-line chart** showing both failed and successful test counts
- **Interactive tooltips** with detailed information for each data point
- **Responsive design** that works on desktop and mobile devices

### ⏰ Time Range Selector
- **1 Hour** - Recent activity monitoring
- **6 Hours** - Short-term trend analysis
- **1 Day** (Default) - Daily test patterns
- **1 Week** - Weekly performance overview
- **1 Month** - Monthly trend analysis
- **3 Months** - Quarterly performance review
- **6 Months** - Semi-annual analysis
- **1 Year** - Annual performance tracking
- **5 Years** - Long-term historical analysis

### 📊 Granularity Selector
- **Minute** - High-resolution monitoring for debugging
- **Hour** (Default) - Standard operational monitoring
- **Day** - Daily aggregation for trend analysis
- **Week** - Weekly aggregation for long-term patterns

### 📋 Trend Summary Cards
- **Failure Trend** - Visual indicator showing if failures are increasing, decreasing, or stable
- **Total Failures** - Aggregate count of failed tests in the selected time range
- **Average Failure Rate** - Percentage of tests that failed
- **Data Points** - Number of time intervals with data

## Technical Architecture

### Database Storage
- **SQLite Database** (`output/test_history.db`)
- **Automatic data collection** from every test run
- **Optimized queries** with indexed timestamps
- **Automatic cleanup** of old data (configurable retention period)

### API Integration
- **RESTful API** endpoints for dynamic chart updates
- **Real-time data fetching** without page refresh
- **Error handling** with graceful fallbacks
- **CORS support** for cross-origin requests

### Chart Library
- **Chart.js** for interactive visualizations
- **Responsive design** that adapts to screen size
- **Smooth animations** and transitions
- **Export capabilities** (future enhancement)

## Usage

### Starting the Dashboard

1. **Run Tests with Dashboard**:
   ```bash
   # This automatically generates the HTML report with time series data
   python scripts/run_tests_with_logging.py
   ```

2. **View Mode Controls**:
   - **📋 Button** (Simple View) - Click to switch to full view
   - **📄 Button** (Full View) - Click to switch to simple view
   - **🌙 Button** - Toggle between dark and light themes
   - **Preferences** - Your view mode choice is automatically saved

3. **Start API Server** (Optional, for dynamic updates):
   ```bash
   # Start the API server for real-time chart updates
   python scripts/start_api_server.py
   
   # Or use the Python script
   python scripts\start_api_server.py
   ```

4. **Open HTML Report**:
   - The HTML report opens automatically in your browser
   - Navigate to the "📈 Test Failure Trends" section
   - Use the time range and granularity selectors to explore data

### API Endpoints

#### GET /api/timeseries
Fetches time series data for the chart.

**Parameters:**
- `timeRange` (optional): Time range (1h, 6h, 1d, 1w, 1m, 3m, 6m, 1y, 5y)
- `granularity` (optional): Data granularity (minute, hour, day, week)

**Example:**
```bash
curl "http://localhost:5000/api/timeseries?timeRange=1d&granularity=hour"
```

#### GET /api/health
Health check endpoint.

**Example:**
```bash
curl "http://localhost:5000/api/health"
```

#### GET /api/stats
Get basic statistics about stored data.

**Example:**
```bash
curl "http://localhost:5000/api/stats"
```

## Data Structure

### Test Session Record
```json
{
  "id": 361,
  "timestamp": "2025-09-26T21:22:03",
  "total_tests": 5,
  "successful_tests": 5,
  "failed_tests": 0,
  "success_rate": 100.0,
  "test_mode": "SAFE",
  "duration_seconds": 45.2,
  "ai_summary": "All tests passed successfully...",
  "created_at": "2025-09-26T21:22:03"
}
```

### Time Series Data Point
```json
{
  "timestamp": "2025-09-26T20:00:00",
  "total_tests": 15,
  "successful_tests": 12,
  "failed_tests": 3,
  "session_count": 3,
  "avg_success_rate": 80.0
}
```

### Failure Trends Analysis
```json
{
  "trend": "decreasing",
  "failure_rate_change": -5.2,
  "total_failures": 45,
  "avg_failure_rate": 12.5,
  "data_points_count": 24
}
```

## Configuration

### Database Settings
- **Location**: `output/test_history.db`
- **Retention**: 90 days (configurable)
- **Cleanup**: Automatic removal of old data

### API Settings
- **Host**: localhost (configurable)
- **Port**: 5000 (configurable)
- **Debug Mode**: Enabled in development

### Chart Settings
- **Default Time Range**: 1 day
- **Default Granularity**: Hour
- **Chart Height**: 400px (responsive)
- **Animation Duration**: 300ms

## Troubleshooting

### Common Issues

1. **No Historical Data**
   - **Cause**: First time running tests
   - **Solution**: Run tests multiple times to build up historical data
   - **Workaround**: Use the sample data generator

2. **API Not Available**
   - **Cause**: API server not running
   - **Solution**: Start the API server with `scripts/start_api_server.py`
   - **Fallback**: Chart works with static data from HTML generation

3. **Chart Not Loading**
   - **Cause**: Chart.js library not loaded
   - **Solution**: Check internet connection for CDN access
   - **Fallback**: Chart loads automatically from CDN

4. **Database Errors**
   - **Cause**: File permissions or disk space
   - **Solution**: Check `output/` directory permissions
   - **Recovery**: Delete `output/test_history.db` to reset

### Debug Mode

Enable debug mode for detailed logging:

```python
# In time_series_api.py
app.run(debug=True, host='0.0.0.0', port=5000)
```

### Sample Data Generation

Generate sample data for testing:

```python
from scripts.test_history_manager import TestHistoryManager, create_sample_data

manager = TestHistoryManager()
create_sample_data(manager, days_back=30)
```

## Future Enhancements

### Planned Features
- **Export functionality** (PNG, PDF, CSV)
- **Email notifications** for failure spikes
- **Custom alerts** based on failure thresholds
- **Comparison mode** between different time periods
- **Test suite filtering** (show specific test categories)
- **Performance metrics** (test execution time trends)

### Integration Possibilities
- **Slack notifications** for failure trends
- **Grafana integration** for advanced dashboards
- **CI/CD pipeline integration** for automated monitoring
- **Webhook support** for external system integration

## Performance Considerations

### Database Optimization
- **Indexed timestamps** for fast queries
- **Automatic cleanup** prevents database bloat
- **Batch inserts** for efficient data storage
- **Connection pooling** for concurrent access

### Chart Performance
- **Data point limits** to prevent browser slowdown
- **Lazy loading** of historical data
- **Caching** of frequently accessed data
- **Responsive updates** without full page reload

### Memory Usage
- **Streaming data** for large time ranges
- **Data aggregation** to reduce memory footprint
- **Garbage collection** of old chart instances
- **Efficient data structures** for JavaScript

## Security Considerations

### Data Privacy
- **Local storage** - data stays on your machine
- **No external transmission** of test results
- **API access** limited to localhost by default
- **Optional authentication** for production use

### Input Validation
- **Parameter sanitization** for API endpoints
- **SQL injection protection** with parameterized queries
- **XSS prevention** with proper HTML escaping
- **Rate limiting** for API endpoints (future)

## Contributing

### Development Setup
1. Install dependencies: `pip install -r requirements/requirements.txt`
2. Run tests: `python scripts/run_tests_with_logging.py`
3. Start API server: `python scripts/start_api_server.py`
4. Make changes and test locally

### Code Structure
- `scripts/test_history_manager.py` - Database and data management
- `scripts/time_series_api.py` - REST API endpoints
- `tests/html_reporter.py` - HTML generation and chart integration
- `scripts/run_tests_with_logging.py` - Test runner with history collection

### Testing
- Unit tests for data management functions
- Integration tests for API endpoints
- End-to-end tests for complete workflow
- Performance tests for large datasets

---

*This dashboard provides powerful insights into your test suite's health and performance over time. Use it to identify patterns, track improvements, and ensure the reliability of your Cuttle system.*

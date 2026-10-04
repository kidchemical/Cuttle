#!/usr/bin/env python3
"""
Simple API endpoint for time series chart updates
"""

import sys
from pathlib import Path

# Ensure src is on path so "tests.reporting.history" resolves when run as tests/reporting/history_api.py
_src = Path(__file__).resolve().parents[2]
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

import json
from flask import Flask, jsonify, request
from tests.reporting.history import TestHistoryManager

app = Flask(__name__)

# Initialize history manager
history_manager = TestHistoryManager()

@app.route('/api/timeseries', methods=['GET'])
def get_time_series_data():
    """Get time series data for the dashboard"""
    try:
        time_range = request.args.get('timeRange', '1d')
        granularity = request.args.get('granularity', 'hour')
        
        # Validate parameters
        valid_ranges = ['1h', '6h', '1d', '1w', '1m', '3m', '6m', '1y', '5y']
        valid_granularities = ['minute', 'hour', 'day', 'week']
        
        if time_range not in valid_ranges:
            time_range = '1d'
        if granularity not in valid_granularities:
            granularity = 'hour'
        
        # Get time series data
        time_series_data = history_manager.get_time_series_data(time_range, granularity)
        failure_trends = history_manager.get_failure_trends(time_range)
        
        response = {
            'success': True,
            'data': {
                'time_series': time_series_data,
                'trends': failure_trends
            }
        }
        
        return jsonify(response)
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'service': 'time-series-api'
    })

@app.route('/api/stats', methods=['GET'])
def get_stats():
    """Get basic statistics about stored data"""
    try:
        # Get recent data to show stats
        recent_data = history_manager.get_time_series_data("1d", "hour")
        
        stats = {
            'total_sessions': recent_data.get('total_sessions', 0),
            'data_points': len(recent_data.get('data_points', [])),
            'date_range': recent_data.get('date_range', {}),
            'latest_trends': history_manager.get_failure_trends("1d")
        }
        
        return jsonify({
            'success': True,
            'stats': stats
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

if __name__ == '__main__':
    print("Starting Time Series API server...")
    print("API endpoints:")
    print("  GET /api/timeseries?timeRange=1d&granularity=hour")
    print("  GET /api/health")
    print("  GET /api/stats")
    print("\nStarting server on http://localhost:5000")
    
    app.run(debug=True, host='0.0.0.0', port=5000)

#!/usr/bin/env python3
"""
Test History Manager - Store and retrieve historical test data for time series analysis
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional
import sqlite3

class TestHistoryManager:
    """Manages historical test data storage and retrieval"""
    
    def __init__(self, db_path=None):
        """Initialize the test history manager"""
        self.db_path = str(db_path or (Path(__file__).resolve().parents[1] / "results" / "test_history.db"))
        self.ensure_db_exists()
    
    def ensure_db_exists(self):
        """Create database and tables if they don't exist"""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Create test_sessions table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS test_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    total_tests INTEGER NOT NULL,
                    successful_tests INTEGER NOT NULL,
                    failed_tests INTEGER NOT NULL,
                    success_rate REAL NOT NULL,
                    test_mode TEXT NOT NULL,
                    duration_seconds REAL,
                    ai_summary TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Create test_results table for detailed results
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS test_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    test_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    duration_seconds REAL,
                    error_message TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (session_id) REFERENCES test_sessions (id)
                )
            """)
            
            # Create index for faster queries
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_sessions_timestamp 
                ON test_sessions (timestamp)
            """)
            
            conn.commit()
    
    def store_test_session(self, test_results: Dict[str, Any]) -> int:
        """Store a test session result and return session ID"""
        timestamp = datetime.now().isoformat()
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Insert test session
            cursor.execute("""
                INSERT INTO test_sessions 
                (timestamp, total_tests, successful_tests, failed_tests, success_rate, test_mode, duration_seconds, ai_summary)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                timestamp,
                test_results['overall_stats']['total_tests'],
                test_results['overall_stats']['successful_tests'],
                test_results['overall_stats']['failed_tests'],
                test_results['overall_stats']['success_rate'],
                test_results['environment_info'].get('test_mode', 'UNKNOWN'),
                test_results.get('total_duration', 0),
                test_results.get('ai_summary', '')
            ))
            
            session_id = cursor.lastrowid
            
            # Insert detailed test results
            for test_name, result in test_results.get('detailed_results', {}).items():
                cursor.execute("""
                    INSERT INTO test_results 
                    (session_id, test_name, status, duration_seconds, error_message)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    session_id,
                    test_name,
                    'PASS' if result['success'] else 'FAIL',
                    result.get('duration', 0),
                    result.get('stderr', '') if not result['success'] else None
                ))
            
            conn.commit()
            return session_id
    
    def get_time_series_data(self, 
                           time_range: str = "1d", 
                           granularity: str = "hour") -> Dict[str, Any]:
        """Get time series data for the dashboard"""
        
        # Calculate time range
        now = datetime.now()
        time_ranges = {
            "1h": timedelta(hours=1),
            "6h": timedelta(hours=6),
            "1d": timedelta(days=1),
            "1w": timedelta(weeks=1),
            "1m": timedelta(days=30),
            "3m": timedelta(days=90),
            "6m": timedelta(days=180),
            "1y": timedelta(days=365),
            "5y": timedelta(days=1825)
        }
        
        start_time = now - time_ranges.get(time_range, time_ranges["1d"])
        
        # Calculate granularity intervals
        granularities = {
            "minute": 60,
            "hour": 3600,
            "day": 86400,
            "week": 604800
        }
        
        interval_seconds = granularities.get(granularity, granularities["hour"])
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Query test sessions within time range
            cursor.execute("""
                SELECT 
                    timestamp,
                    total_tests,
                    successful_tests,
                    failed_tests,
                    success_rate,
                    test_mode,
                    duration_seconds
                FROM test_sessions 
                WHERE timestamp >= ? 
                ORDER BY timestamp
            """, (start_time.isoformat(),))
            
            sessions = cursor.fetchall()
            
            # Group by granularity intervals
            grouped_data = self._group_by_interval(sessions, interval_seconds, granularity)
            
            return {
                'time_range': time_range,
                'granularity': granularity,
                'data_points': grouped_data,
                'total_sessions': len(sessions),
                'date_range': {
                    'start': start_time.isoformat(),
                    'end': now.isoformat()
                }
            }
    
    def _group_by_interval(self, sessions: List[tuple], interval_seconds: int, granularity: str) -> List[Dict]:
        """Group sessions by time intervals"""
        if not sessions:
            return []
        
        grouped = {}
        
        for session in sessions:
            timestamp = datetime.fromisoformat(session[0])
            
            # Calculate interval start
            if granularity == "minute":
                interval_start = timestamp.replace(second=0, microsecond=0)
            elif granularity == "hour":
                interval_start = timestamp.replace(minute=0, second=0, microsecond=0)
            elif granularity == "day":
                interval_start = timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
            elif granularity == "week":
                # Start of week (Monday)
                days_since_monday = timestamp.weekday()
                interval_start = (timestamp - timedelta(days=days_since_monday)).replace(hour=0, minute=0, second=0, microsecond=0)
            
            interval_key = interval_start.isoformat()
            
            if interval_key not in grouped:
                grouped[interval_key] = {
                    'timestamp': interval_key,
                    'total_tests': 0,
                    'successful_tests': 0,
                    'failed_tests': 0,
                    'session_count': 0,
                    'avg_success_rate': 0
                }
            
            # Aggregate data
            grouped[interval_key]['total_tests'] += session[1]
            grouped[interval_key]['successful_tests'] += session[2]
            grouped[interval_key]['failed_tests'] += session[3]
            grouped[interval_key]['session_count'] += 1
        
        # Calculate averages
        for interval_data in grouped.values():
            if interval_data['total_tests'] > 0:
                interval_data['avg_success_rate'] = (interval_data['successful_tests'] / interval_data['total_tests']) * 100
        
        # Convert to sorted list
        return sorted(grouped.values(), key=lambda x: x['timestamp'])
    
    def get_failure_trends(self, time_range: str = "1d") -> Dict[str, Any]:
        """Get failure trend analysis"""
        time_series = self.get_time_series_data(time_range, "hour")
        
        if not time_series['data_points']:
            return {
                'trend': 'stable',
                'failure_rate_change': 0,
                'total_failures': 0,
                'avg_failure_rate': 0
            }
        
        data_points = time_series['data_points']
        total_failures = sum(point['failed_tests'] for point in data_points)
        total_tests = sum(point['total_tests'] for point in data_points)
        
        if total_tests == 0:
            return {
                'trend': 'stable',
                'failure_rate_change': 0,
                'total_failures': 0,
                'avg_failure_rate': 0
            }
        
        avg_failure_rate = (total_failures / total_tests) * 100
        
        # Calculate trend (compare first half vs second half)
        if len(data_points) >= 4:
            mid_point = len(data_points) // 2
            first_half_rate = sum(point['failed_tests'] for point in data_points[:mid_point]) / max(1, sum(point['total_tests'] for point in data_points[:mid_point])) * 100
            second_half_rate = sum(point['failed_tests'] for point in data_points[mid_point:]) / max(1, sum(point['total_tests'] for point in data_points[mid_point:])) * 100
            
            rate_change = second_half_rate - first_half_rate
            
            if rate_change > 5:
                trend = 'increasing'
            elif rate_change < -5:
                trend = 'decreasing'
            else:
                trend = 'stable'
        else:
            trend = 'stable'
            rate_change = 0
        
        return {
            'trend': trend,
            'failure_rate_change': rate_change,
            'total_failures': total_failures,
            'avg_failure_rate': avg_failure_rate,
            'data_points_count': len(data_points)
        }
    
    def cleanup_old_data(self, days_to_keep: int = 90):
        """Clean up old test data to prevent database bloat"""
        cutoff_date = datetime.now() - timedelta(days=days_to_keep)
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Delete old test results first (foreign key constraint)
            cursor.execute("""
                DELETE FROM test_results 
                WHERE session_id IN (
                    SELECT id FROM test_sessions 
                    WHERE timestamp < ?
                )
            """, (cutoff_date.isoformat(),))
            
            # Delete old test sessions
            cursor.execute("""
                DELETE FROM test_sessions 
                WHERE timestamp < ?
            """, (cutoff_date.isoformat(),))
            
            conn.commit()
            
            return cursor.rowcount

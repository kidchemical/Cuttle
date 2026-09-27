#!/usr/bin/env python3
"""
Start the time series API server for dynamic chart updates (port 5000).

For the main Cuttle web app (Tools page, chat, node editor on port 8080), run:
  python src/api/web_chat_api.py
or from project root:
  .venv\\Scripts\\python.exe src\\api\\web_chat_api.py
"""

import subprocess
import sys
import os
import time
from pathlib import Path

def start_api_server():
    """Start the Flask API server for time series data"""
    
    print("🚀 Starting Time Series API Server...")
    print("=" * 50)
    
    # Change to project root directory
    project_root = Path(__file__).parent.parent
    os.chdir(project_root)
    
    # Check if Flask is installed
    try:
        import flask
        print(f"✅ Flask {flask.__version__} is available")
    except ImportError:
        print("❌ Flask not found. Installing...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "flask>=2.3.0"])
            print("✅ Flask installed successfully")
        except subprocess.CalledProcessError:
            print("❌ Failed to install Flask")
            return False
    
    # Start the API server
    try:
        print("\n🌐 Starting API server on http://localhost:5000")
        print("📊 API endpoints:")
        print("   GET /api/timeseries?timeRange=1d&granularity=hour")
        print("   GET /api/health")
        print("   GET /api/stats")
        print("\n💡 Press Ctrl+C to stop the server")
        print("=" * 50)
        
        # Run the API server
        subprocess.run([sys.executable, "scripts/time_series_api.py"])
        
    except KeyboardInterrupt:
        print("\n\n🛑 API server stopped by user")
        return True
    except Exception as e:
        print(f"\n❌ Error starting API server: {e}")
        return False

if __name__ == "__main__":
    success = start_api_server()
    if not success:
        sys.exit(1)

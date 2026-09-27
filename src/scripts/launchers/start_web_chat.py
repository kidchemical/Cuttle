#!/usr/bin/env python3
"""
Start the Cuttle Web Chat API Server
"""

import subprocess
import sys
import os
from pathlib import Path

def start_web_chat():
    """Start the web chat API server"""
    
    print("🚀 Starting Cuttle Web Chat API Server...")
    print("=" * 50)
    
    # Change to project root directory
    project_root = Path(__file__).parent
    os.chdir(project_root)
    
    # Check if Flask is installed
    try:
        import flask
        print(f"✅ Flask {flask.__version__} is available")
    except ImportError:
        print("❌ Flask not found. Installing...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "flask>=2.3.0", "flask-cors>=4.0.0"])
            print("✅ Flask and Flask-CORS installed successfully")
        except subprocess.CalledProcessError:
            print("❌ Failed to install Flask")
            return False
    
    # Check if Flask-CORS is installed
    try:
        import flask_cors
        print(f"✅ Flask-CORS {flask_cors.__version__} is available")
    except ImportError:
        print("❌ Flask-CORS not found. Installing...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "flask-cors>=4.0.0"])
            print("✅ Flask-CORS installed successfully")
        except subprocess.CalledProcessError:
            print("❌ Failed to install Flask-CORS")
            return False
    
    # Start the web chat API server
    try:
        print("\n🌐 Starting Web Chat API server on http://localhost:8080")
        print("📊 API endpoints:")
        print("   GET  /                    - Landing page")
        print("   GET  /control_panel.html  - Control panel")
        print("   GET  /settings_page.html  - Settings page")
        print("   GET  /about_page.html     - About page")
        print("   POST /api/chat            - Send chat message")
        print("   GET  /api/health          - Health check")
        print("   GET  /api/sessions        - List sessions")
        print("   GET  /api/sessions/<id>   - Get session history")
        print("   POST /api/clear-session/<id> - Clear session")
        print("\n💡 Open http://localhost:8080 in your browser")
        print("🛑 Press Ctrl+C to stop the server")
        print("=" * 50)
        
        # Run the web chat API server
        subprocess.run([sys.executable, "web_chat_api.py"])
        
    except KeyboardInterrupt:
        print("\n\n🛑 Web Chat API server stopped by user")
        return True
    except Exception as e:
        print(f"\n❌ Error starting server: {e}")
        return False

if __name__ == "__main__":
    success = start_web_chat()
    if not success:
        sys.exit(1)

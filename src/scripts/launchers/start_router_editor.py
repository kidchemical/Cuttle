#!/usr/bin/env python3
"""
Router Editor Launcher
Starts the web server and opens the Cuttle Router page in a browser
"""

import sys
import webbrowser
import time
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

def main():
    print("=" * 60)
    print("🔀 Cuttle Router")
    print("=" * 60)
    print()
    print("Starting web server...")
    print()

    # Import and start the web server
    try:
        from web_chat_api import app

        print("✅ Router page is ready!")
        print()
        print("📍 URL: http://localhost:8080/router_editor.html")
        print()
        print("Features:")
        print("  • Core routing config (brain mode, default / escalation / fallback targets)")
        print("  • Use-case blocks (criteria → preferred targets), saved to settings.json")
        print("  • Target health: 7-day outcomes + quality-regression signals")
        print("  • Temporary demotions with automatic re-promotion")
        print()
        print("Press Ctrl+C to stop the server")
        print("=" * 60)
        print()

        # Wait a moment for server to start, then open browser
        time.sleep(1)
        try:
            webbrowser.open('http://localhost:8080/router_editor.html')
            print("🌐 Opening browser...")
            print()
        except Exception as e:
            print(f"⚠️ Could not open browser automatically: {e}")
            print("   Please open http://localhost:8080/router_editor.html manually")
            print()

        # Start the server
        app.run(host='0.0.0.0', port=8080, debug=False)

    except KeyboardInterrupt:
        print()
        print()
        print("=" * 60)
        print("👋 Router page stopped")
        print("=" * 60)
        sys.exit(0)
    except Exception as e:
        print()
        print(f"❌ Error starting the Router page: {e}")
        print()
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == '__main__':
    main()

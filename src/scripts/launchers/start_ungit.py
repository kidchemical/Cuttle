#!/usr/bin/env python3
"""
Ungit Launcher for Cuttle
Starts Ungit with the current project from the project manager
"""

import sys
import subprocess
import time
import webbrowser
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

def main():
    print("=" * 60)
    print("🌐 Cuttle Ungit Launcher")
    print("=" * 60)
    print()
    
    try:
        # Import project manager
        from project_manager import project_manager
        
        # Get current project
        current_project = project_manager.get_current_project()
        
        if not current_project:
            print("❌ No current project selected")
            print("Please select a project in the Custom Git UI first")
            return False
        
        project_path = Path(current_project['path'])
        if not project_path.exists():
            print(f"❌ Project path does not exist: {project_path}")
            return False
        
        print(f"📁 Current Project: {current_project['name']}")
        print(f"📍 Path: {project_path}")
        print()
        
        # Check if Ungit is installed
        try:
            result = subprocess.run(['ungit', '--version'], 
                                  capture_output=True, text=True, check=True)
            print(f"✅ Ungit found: {result.stdout.strip()}")
        except (subprocess.CalledProcessError, FileNotFoundError):
            print("❌ Ungit not found. Please install it first:")
            print("   npm install -g ungit")
            return False
        
        print()
        print("🚀 Starting Ungit...")
        print(f"📍 Repository: {project_path}")
        print()
        
        # Start Ungit with the current project path
        ungit_process = subprocess.Popen([
            'ungit', 
            '--port', '8448',
            '--launchBrowser', 'false',
            '--rootPath', str(project_path)
        ])
        
        print("✅ Ungit started!")
        print()
        print("📍 URL: http://localhost:8448")
        print(f"📁 Repository: {project_path}")
        print()
        print("Features:")
        print("  • Visual Git operations")
        print("  • Branch visualization")
        print("  • Commit history")
        print("  • File diff viewer")
        print("  • Merge conflict resolution")
        print()
        print("Press Ctrl+C to stop Ungit")
        print("=" * 60)
        
        # Wait a moment for Ungit to start, then open browser
        time.sleep(2)
        try:
            webbrowser.open('http://localhost:8448')
            print("🌐 Opening browser...")
            print()
        except Exception as e:
            print(f"⚠️ Could not open browser automatically: {e}")
            print("   Please open http://localhost:8448 manually")
            print()
        
        # Wait for the process to complete
        try:
            ungit_process.wait()
        except KeyboardInterrupt:
            print("\n🛑 Stopping Ungit...")
            ungit_process.terminate()
            ungit_process.wait()
            print("✅ Ungit stopped")
        
        return True
        
    except Exception as e:
        print(f"❌ Error starting Ungit: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)

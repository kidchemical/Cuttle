#!/usr/bin/env python3
"""
Debug script to find Cursor installation location
"""

import sys
import os
from pathlib import Path

# Add the project root to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tool_manager import find_cursor_exe

def debug_cursor_location():
    """Debug script to find where Cursor is installed"""
    print("🔍 Cursor Location Debug Script")
    print("=" * 50)
    
    print("\n1. Environment Variables:")
    cursor_exe = os.getenv("CURSOR_EXE")
    if cursor_exe:
        print(f"   CURSOR_EXE: {cursor_exe}")
        print(f"   Exists: {Path(cursor_exe).is_file()}")
    else:
        print("   CURSOR_EXE: Not set")
    
    print(f"\n2. System Information:")
    print(f"   LocalAppData: {os.environ.get('LocalAppData', 'Not set')}")
    print(f"   AppData: {os.environ.get('AppData', 'Not set')}")
    print(f"   ProgramFiles: {os.environ.get('ProgramFiles', 'Not set')}")
    print(f"   ProgramFiles(x86): {os.environ.get('ProgramFiles(x86)', 'Not set')}")
    print(f"   USERNAME: {os.getenv('USERNAME', 'Not set')}")
    
    print(f"\n3. Common Cursor Paths:")
    common_paths = [
        Path(os.environ.get("LocalAppData", "")) / r"Programs\Cursor\Cursor.exe",
        Path(os.environ.get("AppData", "")) / r"Cursor\Cursor.exe",
        Path(os.environ.get("ProgramFiles", "")) / r"Cursor\Cursor.exe",
        Path(os.environ.get("ProgramFiles(x86)", "")) / r"Cursor\Cursor.exe",
        Path("C:/Program Files/Cursor/Cursor.exe"),
        Path("C:/Program Files (x86)/Cursor/Cursor.exe"),
        Path("C:/Cursor/Cursor.exe"),
        Path("D:/Cursor/Cursor.exe"),
        Path("F:/Cursor/Cursor.exe"),
        Path.home() / "AppData/Local/Programs/Cursor/Cursor.exe",
    ]
    
    for path in common_paths:
        exists = path.is_file()
        print(f"   {path}: {'✅ EXISTS' if exists else '❌ Not found'}")
    
    print(f"\n4. PATH Search:")
    import shutil
    cursor_in_path = shutil.which("cursor")
    cursor_exe_in_path = shutil.which("Cursor.exe")
    
    print(f"   'cursor' in PATH: {cursor_in_path or 'Not found'}")
    print(f"   'Cursor.exe' in PATH: {cursor_exe_in_path or 'Not found'}")
    
    print(f"\n5. Using find_cursor_exe():")
    cursor_path = find_cursor_exe()
    if cursor_path:
        print(f"   ✅ Found: {cursor_path}")
        return True
    else:
        print(f"   ❌ Not found")
        return False

def suggest_solutions():
    """Suggest solutions if Cursor is not found"""
    print(f"\n💡 Solutions:")
    print(f"   1. Install Cursor from https://cursor.sh/")
    print(f"   2. Set CURSOR_EXE environment variable to point to Cursor.exe")
    print(f"   3. Add Cursor installation directory to your PATH")
    print(f"   4. Check if Cursor is installed in a non-standard location")
    
    print(f"\n🔧 Manual Check:")
    print(f"   Try running 'cursor' or 'Cursor.exe' in your command prompt")
    print(f"   If it works, note the full path and set CURSOR_EXE environment variable")

if __name__ == "__main__":
    print("🚀 Cursor Location Debug Tool")
    print("This will help identify where Cursor is installed on your system")
    print()
    
    found = debug_cursor_location()
    
    if not found:
        suggest_solutions()
    
    sys.exit(0 if found else 1)

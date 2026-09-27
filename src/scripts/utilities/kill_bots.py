#!/usr/bin/env python3
"""
Direct script to kill existing bot processes
"""

import os
import sys
import platform
import psutil
from pathlib import Path

# Add the current directory to the path so we can import from launcher
sys.path.insert(0, str(Path(__file__).parent))

from launcher import JamBitLauncher

def main():
    print("[STOP] Killing existing bot processes...")
    print("=" * 50)
    
    try:
        launcher = JamBitLauncher()
        launcher.kill_existing_bots()
        print("=" * 50)
        print("[OK] Kill operation completed!")
        print("[INFO] You can now start fresh bot instances.")
        
    except Exception as e:
        print("=" * 50)
        print(f"[ERROR] Failed to kill existing processes: {e}")
        print("[WARN] You may need to manually close terminal windows or kill processes.")
        print("[INFO] Try running as administrator if you get permission errors.")

if __name__ == "__main__":
    main()

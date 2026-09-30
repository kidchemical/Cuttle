#!/usr/bin/env python3
"""
Step-by-Step Dependency Installer for JamBit OS
Installs dependencies one by one with timeout handling
"""

import subprocess
import sys
import time
import platform
from pathlib import Path

# Simple text replacements for Windows console compatibility
if platform.system() == "Windows":
    # Use simple text instead of emojis for Windows console
    GEAR = "[GEAR]"
    PACKAGE = "[INSTALL]"
    CHECK = "[OK]"
    ERROR = "[ERROR]"
    TIMEOUT = "[TIMEOUT]"
    SNAKE = "[PYTHON]"
    GLOBE = "[ENV]"
    TARGET = "[TARGET]"
    CHART = "[STATS]"
    TEST = "[TEST]"
    PARTY = "[SUCCESS]"
    LIGHTBULB = "[INFO]"
    THINKING = "[QUESTION]"
    SKIP = "[SKIP]"
else:
    # Use emojis for other systems
    GEAR = "🔧"
    PACKAGE = "📦"
    CHECK = "✅"
    ERROR = "❌"
    TIMEOUT = "⏰"
    SNAKE = "🐍"
    GLOBE = "🌍"
    TARGET = "🎯"
    CHART = "📊"
    TEST = "🧪"
    PARTY = "🎉"
    LIGHTBULB = "💡"
    THINKING = "🤔"
    SKIP = "⏭️"

def install_package(python_cmd, package, timeout=60):
    """Install a single package with timeout"""
    print(f"{PACKAGE} Installing {package}...")
    try:
        result = subprocess.run([
            python_cmd, "-m", "pip", "install", package
        ], capture_output=True, text=True, timeout=timeout)
        
        if result.returncode == 0:
            print(f"{CHECK} Successfully installed: {package}")
            return True
        else:
            print(f"{ERROR} Failed to install {package}")
            print(f"   stdout: {result.stdout}")
            print(f"   stderr: {result.stderr}")
            return False
    except subprocess.TimeoutExpired:
        print(f"{TIMEOUT} Timeout installing {package}")
        return False
    except Exception as e:
        print(f"{ERROR} Error installing {package}: {e}")
        return False

def main():
    print(f"{GEAR} Step-by-Step Dependency Installer")
    print("=" * 50)
    
    project_root = Path(__file__).parent
    
    # Check if we're on Windows or WSL
    is_wsl = False
    try:
        with open('/proc/version', 'r') as f:
            is_wsl = 'microsoft' in f.read().lower()
    except:
        pass
    
    if is_wsl:
        python_cmd = str(project_root / ".venv_wsl" / "bin" / "python3")
    else:
        python_cmd = str(project_root / ".venv" / "Scripts" / "python.exe")
    
    print(f"{SNAKE} Using Python: {python_cmd}")
    print(f"{GLOBE} Environment: {'WSL' if is_wsl else 'Windows'}")
    print("=" * 50)
    
    # Essential packages first (lightweight)
    essential_packages = [
        "flask>=2.3.0",
        "flask-cors>=4.0.0",
        "python-dotenv",
        "requests>=2.31.0",
    ]
    
    # Additional packages (heavier)
    additional_packages = [
        "openai",
        "pillow",
        "psutil",
        "pytesseract",
        "opencv-python"
    ]
    
    print(f"{TARGET} Installing essential packages first...")
    essential_success = 0
    for package in essential_packages:
        if install_package(python_cmd, package, timeout=120):
            essential_success += 1
        time.sleep(2)  # Brief pause between installs
    
    print(f"\n{CHART} Essential packages: {essential_success}/{len(essential_packages)} installed")
    
    if essential_success < len(essential_packages):
        print(f"{ERROR} Some essential packages failed. Stopping here.")
        return False
    
    # Test if bot can start with just essentials
    print(f"\n{TEST} Testing bot with essential packages...")
    try:
        result = subprocess.run([
            python_cmd, "-c", 
            "import flask, flask_cors, dotenv, requests; print('[OK] All essential imports successful')"
        ], capture_output=True, text=True, timeout=30)
        
        if result.returncode == 0:
            print(result.stdout.strip())
            print(f"{PARTY} Bot should work with essential packages!")
        else:
            print(f"{ERROR} Import test failed: {result.stderr}")
            return False
    except Exception as e:
        print(f"{ERROR} Import test error: {e}")
        return False
    
    # Ask if user wants to continue with additional packages
    print("\n" + "=" * 50)
    print(f"{LIGHTBULB} Essential packages are installed and working!")
    print(f"{THINKING} Do you want to install additional packages?")
    print("   - These include AI and image processing tools")
    print("   - They're larger and may take longer over network drives")
    
    response = input("\nInstall additional packages? (y/n): ").lower().strip()
    
    if response in ['y', 'yes']:
        print(f"\n{TARGET} Installing additional packages...")
        additional_success = 0
        for package in additional_packages:
            if install_package(python_cmd, package, timeout=180):
                additional_success += 1
            time.sleep(2)
        
        print(f"\n{CHART} Additional packages: {additional_success}/{len(additional_packages)} installed")
    else:
        print(f"{SKIP} Skipping additional packages")
    
    print(f"\n{PARTY} Installation process completed!")
    print("=" * 50)
    return True

if __name__ == "__main__":
    try:
        success = main()
        input("\nPress Enter to exit...")
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n🛑 Installation cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        input("\nPress Enter to exit...")
        sys.exit(1)

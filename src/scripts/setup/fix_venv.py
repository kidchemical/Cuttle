#!/usr/bin/env python3
"""
Virtual Environment Fixer for JamBit OS
Recreates broken virtual environments on different machines
"""

import subprocess
import sys
import os
import shutil
from pathlib import Path

def main():
    print("🔧 JamBit OS Virtual Environment Fixer")
    print("=" * 50)
    
    project_root = Path(__file__).parent
    print(f"📁 Project root: {project_root}")
    print(f"🐍 System Python: {sys.executable}")
    print("=" * 50)
    
    # Check if we're on Windows or WSL
    is_wsl = False
    try:
        with open('/proc/version', 'r') as f:
            is_wsl = 'microsoft' in f.read().lower()
    except:
        pass
    
    print(f"🌍 Environment: {'WSL' if is_wsl else 'Windows'}")
    
    if is_wsl:
        venv_path = project_root / ".venv_wsl"
        python_exe = "python3"
    else:
        venv_path = project_root / ".venv"
        python_exe = sys.executable
    
    print(f"📂 Virtual environment path: {venv_path}")
    
    # Backup old venv if it exists
    if venv_path.exists():
        backup_path = project_root / f"{venv_path.name}_backup"
        print(f"🔄 Backing up existing virtual environment to: {backup_path}")
        
        if backup_path.exists():
            shutil.rmtree(backup_path)
        shutil.move(str(venv_path), str(backup_path))
    
    # Create new virtual environment
    print(f"🏗️ Creating new virtual environment...")
    try:
        subprocess.check_call([
            python_exe, "-m", "venv", str(venv_path)
        ])
        print("✅ Virtual environment created successfully")
    except Exception as e:
        print(f"❌ Failed to create virtual environment: {e}")
        return False
    
    # Get the correct Python executable path
    if is_wsl:
        venv_python = venv_path / "bin" / "python3"
    else:
        venv_python = venv_path / "Scripts" / "python.exe"
    
    print(f"🐍 Virtual environment Python: {venv_python}")
    
    # Test the new venv
    print("🧪 Testing new virtual environment...")
    try:
        result = subprocess.run([str(venv_python), "--version"], 
                              capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            print(f"✅ Virtual environment works: {result.stdout.strip()}")
        else:
            print(f"❌ Virtual environment test failed: {result.stderr}")
            return False
    except Exception as e:
        print(f"❌ Virtual environment test error: {e}")
        return False
    
    # Upgrade pip
    print("⬆️ Upgrading pip...")
    try:
        subprocess.check_call([
            str(venv_python), "-m", "pip", "install", "--upgrade", "pip"
        ])
        print("✅ pip upgraded")
    except Exception as e:
        print(f"❌ Failed to upgrade pip: {e}")
    
    # Install requirements
    requirements_file = project_root / "requirements" / "requirements.txt"
    if requirements_file.exists():
        print(f"📦 Installing requirements from {requirements_file}...")
        try:
            subprocess.check_call([
                str(venv_python), "-m", "pip", "install", "-r", str(requirements_file)
            ])
            print("✅ Requirements installed")
        except Exception as e:
            print(f"❌ Failed to install requirements: {e}")
    else:
        print("⚠️ No requirements.txt found, installing basic dependencies...")
        basic_deps = [
            "flask>=2.3.0", "flask-cors>=4.0.0",
            "python-dotenv", "openai", "pillow", "psutil",
            "pytesseract", "opencv-python"
        ]

        try:
            subprocess.check_call([
                str(venv_python), "-m", "pip", "install"
            ] + basic_deps)
            print("✅ Basic dependencies installed")
        except Exception as e:
            print(f"❌ Failed to install basic dependencies: {e}")
    
    print("\n🎉 Virtual environment fix completed!")
    print("=" * 50)
    print("💡 You can now run the launcher again:")
    if is_wsl:
        print("   python launcher.py")
    else:
        print("   python launcher.py")
        print("   or python launcher_debug.py")
    print("=" * 50)
    
    return True

if __name__ == "__main__":
    try:
        success = main()
        input("\nPress Enter to exit...")
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n🛑 Operation cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        input("\nPress Enter to exit...")
        sys.exit(1)

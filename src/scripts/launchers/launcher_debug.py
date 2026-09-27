#!/usr/bin/env python3
"""
Debug Launcher for Cuttle
Enhanced error reporting and network drive compatibility
"""

import subprocess
import sys
import os
import time
import threading
import signal
import platform
import traceback
from pathlib import Path

class JamBitDebugLauncher:
    def __init__(self):
        self.project_root = Path(__file__).parent
        self.processes = []
        self.running = True
        
        # Detect environment
        self.is_wsl = self.detect_wsl()
        self.python_cmd = self.get_python_command()
        
        print("🚀 Cuttle Debug Launcher")
        print("=" * 60)
        print(f"📁 Project root: {self.project_root}")
        print(f"🐍 Python: {self.python_cmd}")
        print(f"🌍 Environment: {'WSL' if self.is_wsl else 'Windows'}")
        print(f"🔗 Network drive: {self.is_network_drive()}")
        print(f"📂 Current working directory: {os.getcwd()}")
        print("=" * 60)
    
    def is_network_drive(self):
        """Check if running from a network drive"""
        try:
            drive = Path(self.project_root).anchor
            # Check if it's a UNC path or mapped network drive
            return (drive.startswith('\\\\') or 
                   (len(drive) == 3 and drive[1:3] == ':\\' and 
                    os.path.exists(drive) and 
                    os.popen(f'net use {drive[:-1]} 2>nul').read().strip() != ''))
        except:
            return False
    
    def detect_wsl(self):
        """Detect if running in WSL"""
        try:
            with open('/proc/version', 'r') as f:
                return 'microsoft' in f.read().lower()
        except:
            return False
    
    def get_python_command(self):
        """Get the appropriate Python command with debug info"""
        print("🔍 Checking Python environment...")
        
        if self.is_wsl:
            # Check for WSL virtual environment
            wsl_venv = self.project_root / ".venv_wsl" / "bin" / "python3"
            print(f"   WSL venv path: {wsl_venv}")
            print(f"   WSL venv exists: {wsl_venv.exists()}")
            if wsl_venv.exists():
                return str(wsl_venv)
            return "python3"
        else:
            # Check for Windows virtual environment
            win_venv = self.project_root / ".venv" / "Scripts" / "python.exe"
            print(f"   Windows venv path: {win_venv}")
            print(f"   Windows venv exists: {win_venv.exists()}")
            
            if win_venv.exists():
                # Test if the venv Python actually works
                try:
                    result = subprocess.run([str(win_venv), "--version"], 
                                          capture_output=True, text=True, timeout=5)
                    if result.returncode == 0:
                        print(f"   ✅ Virtual environment Python works: {result.stdout.strip()}")
                        return str(win_venv)
                    else:
                        print(f"   ❌ Virtual environment Python broken: {result.stderr.strip()}")
                        print(f"   🔄 Falling back to system Python")
                except Exception as e:
                    print(f"   ❌ Virtual environment Python test failed: {e}")
                    print(f"   🔄 Falling back to system Python")
            
            print(f"   System Python: {sys.executable}")
            return sys.executable
    
    def install_dependencies(self):
        """Install required dependencies with detailed logging"""
        print("⚙️ Checking dependencies...")
        
        # Test Python command first
        try:
            result = subprocess.run([self.python_cmd, "--version"], 
                                  capture_output=True, text=True, timeout=10)
            print(f"✅ Python version check: {result.stdout.strip()}")
            if result.stderr:
                print(f"⚠️ Python stderr: {result.stderr.strip()}")
        except Exception as e:
            print(f"❌ Python command failed: {e}")
            return False
        
        # Check Flask for web chat API
        try:
            result = subprocess.run([self.python_cmd, "-c", "import flask; print(f'Flask {flask.__version__}')"], 
                                  capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                print(f"✅ {result.stdout.strip()}")
            else:
                print(f"📦 Installing Flask...")
                print(f"   Error: {result.stderr}")
                self.install_package("flask>=2.3.0 flask-cors>=4.0.0")
        except Exception as e:
            print(f"❌ Flask check failed: {e}")
            return False
        
        # Check Flask-CORS
        try:
            result = subprocess.run([self.python_cmd, "-c", "import flask_cors; print(f'Flask-CORS {flask_cors.__version__}')"], 
                                  capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                print(f"✅ {result.stdout.strip()}")
            else:
                print(f"📦 Installing Flask-CORS...")
                self.install_package("flask-cors>=4.0.0")
        except Exception as e:
            print(f"❌ Flask-CORS check failed: {e}")
        
        # Check Discord.py
        try:
            result = subprocess.run([self.python_cmd, "-c", "import discord; print(f'Discord.py {discord.__version__}')"], 
                                  capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                print(f"✅ {result.stdout.strip()}")
            else:
                print(f"📦 Installing Discord.py and dependencies...")
                deps = [
                    "discord.py>=2.3.0", "python-dotenv", "openai", 
                    "pillow", "psutil", "mss", "pytesseract", "opencv-python"
                ]
                if not self.is_wsl:
                    deps.extend(["pywin32", "pyautogui", "pygetwindow", "pywinauto", "keyboard", "mouse"])
                
                self.install_package(" ".join(deps))
        except Exception as e:
            print(f"❌ Discord.py check failed: {e}")
        
        return True
    
    def install_package(self, package):
        """Install a package with detailed error reporting"""
        try:
            print(f"   Installing: {package}")
            result = subprocess.run([
                self.python_cmd, "-m", "pip", "install"
            ] + package.split(), 
            capture_output=True, text=True, timeout=120)
            
            if result.returncode == 0:
                print(f"✅ Successfully installed: {package}")
            else:
                print(f"❌ Failed to install {package}")
                print(f"   Return code: {result.returncode}")
                print(f"   stdout: {result.stdout}")
                print(f"   stderr: {result.stderr}")
                return False
        except subprocess.TimeoutExpired:
            print(f"❌ Installation timeout for: {package}")
            return False
        except Exception as e:
            print(f"❌ Installation error for {package}: {e}")
            return False
        
        return True
    
    def start_discord_bot(self):
        """Start the Discord bot with error capture"""
        print("🦑 Starting Discord bot...")
        
        if self.is_wsl:
            bot_script = "bot_wsl.py"
        else:
            bot_script = "bot_deprecated.py"
        
        bot_path = self.project_root / bot_script
        print(f"   Bot script: {bot_path}")
        print(f"   Bot exists: {bot_path.exists()}")
        
        try:
            # Test if bot script can be imported first
            print("   Testing bot script import...")
            test_result = subprocess.run([
                self.python_cmd, "-c", f"import sys; sys.path.insert(0, '{self.project_root}'); import {bot_script[:-3]}"
            ], capture_output=True, text=True, timeout=10)
            
            if test_result.returncode != 0:
                print(f"❌ Bot script import failed:")
                print(f"   stdout: {test_result.stdout}")
                print(f"   stderr: {test_result.stderr}")
                return False
            
            print("   Bot script import successful")
            
            # Start the bot
            process = subprocess.Popen([
                self.python_cmd, bot_script
            ], cwd=self.project_root, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE,
            text=True)
            
            self.processes.append(("Discord Bot", process))
            print(f"✅ Discord bot started (PID: {process.pid})")
            
            # Give it a moment to start, then check if it's still running
            time.sleep(2)
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                print(f"❌ Discord bot exited immediately:")
                print(f"   Return code: {process.returncode}")
                print(f"   stdout: {stdout}")
                print(f"   stderr: {stderr}")
                self.processes.remove(("Discord Bot", process))
                return False
            
            return True
        except Exception as e:
            print(f"❌ Failed to start Discord bot: {e}")
            print(f"   Traceback: {traceback.format_exc()}")
            return False
    
    def start_web_chat_api(self):
        """Start the Web Chat API server with error capture"""
        print("🌐 Starting Web Chat API server...")
        
        api_path = self.project_root / "web_chat_api.py"
        print(f"   API script: {api_path}")
        print(f"   API exists: {api_path.exists()}")
        
        try:
            # Test if API script can be imported first
            print("   Testing API script import...")
            test_result = subprocess.run([
                self.python_cmd, "-c", f"import sys; sys.path.insert(0, '{self.project_root}'); import web_chat_api"
            ], capture_output=True, text=True, timeout=10)
            
            if test_result.returncode != 0:
                print(f"❌ API script import failed:")
                print(f"   stdout: {test_result.stdout}")
                print(f"   stderr: {test_result.stderr}")
                return False
            
            print("   API script import successful")
            
            # Start the API server
            process = subprocess.Popen([
                self.python_cmd, "web_chat_api.py"
            ], cwd=self.project_root,
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE,
            text=True)
            
            self.processes.append(("Web Chat API", process))
            print(f"✅ Web Chat API started (PID: {process.pid})")
            print("💡 Web interface: http://localhost:8080")
            
            # Give it a moment to start, then check if it's still running
            time.sleep(2)
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                print(f"❌ Web Chat API exited immediately:")
                print(f"   Return code: {process.returncode}")
                print(f"   stdout: {stdout}")
                print(f"   stderr: {stderr}")
                self.processes.remove(("Web Chat API", process))
                return False
            
            return True
        except Exception as e:
            print(f"❌ Failed to start Web Chat API: {e}")
            print(f"   Traceback: {traceback.format_exc()}")
            return False
    
    def monitor_processes(self):
        """Monitor running processes with detailed logging"""
        print("\n🔍 Starting process monitoring...")
        while self.running:
            time.sleep(5)
            
            for name, process in self.processes[:]:
                if process.poll() is not None:
                    print(f"⚠️ {name} stopped unexpectedly (exit code: {process.returncode})")
                    try:
                        stdout, stderr = process.communicate(timeout=1)
                        if stdout:
                            print(f"   stdout: {stdout}")
                        if stderr:
                            print(f"   stderr: {stderr}")
                    except:
                        pass
                    self.processes.remove((name, process))
            
            if not self.processes:
                print("❌ All processes stopped")
                break
    
    def stop_all_processes(self):
        """Stop all running processes"""
        print("\n🛑 Stopping all processes...")
        self.running = False
        
        for name, process in self.processes:
            try:
                print(f"Stopping {name}...")
                process.terminate()
                
                # Wait for graceful shutdown
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    print(f"Force killing {name}...")
                    process.kill()
                    process.wait()
                
                print(f"✅ {name} stopped")
            except Exception as e:
                print(f"❌ Error stopping {name}: {e}")
        
        self.processes.clear()
    
    def signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        print(f"\n📡 Received signal {signum}")
        self.stop_all_processes()
        sys.exit(0)
    
    def run(self):
        """Main launcher function with comprehensive error handling"""
        try:
            print("🚀 Starting Cuttle Debug Launcher...")
            
            # Set up signal handlers
            signal.signal(signal.SIGINT, self.signal_handler)
            signal.signal(signal.SIGTERM, self.signal_handler)
            
            # Install dependencies
            if not self.install_dependencies():
                print("❌ Dependency installation failed")
                return False
            
            # Start services
            bot_started = self.start_discord_bot()
            api_started = self.start_web_chat_api()
            
            if not bot_started and not api_started:
                print("❌ Failed to start any services")
                return False
            
            # Show status
            print("\n🎉 Cuttle is running!")
            print("=" * 60)
            if bot_started:
                print("🦑 Discord bot: Active")
            if api_started:
                print("🌐 Web Chat API: http://localhost:8080")
            print("🛑 Press Ctrl+C to stop all services")
            print("=" * 60)
            
            # Monitor processes
            self.monitor_processes()
            
        except KeyboardInterrupt:
            print("\n📡 Keyboard interrupt received")
        except Exception as e:
            print(f"\n❌ Unexpected error: {e}")
            print(f"   Traceback: {traceback.format_exc()}")
        finally:
            self.stop_all_processes()
        
        return True

def main():
    launcher = JamBitDebugLauncher()
    success = launcher.run()
    
    print("\n" + "=" * 60)
    print("🏁 Launcher finished")
    print(f"   Success: {success}")
    print("=" * 60)
    
    # Pause before closing to see any final output
    input("\nPress Enter to exit...")
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Unified Launcher for Cuttle
Starts both Discord bot and Web Chat API server
"""

import subprocess
import sys
import os
import time
import threading
import signal
import platform
import psutil
from pathlib import Path

# Simple text replacements for Windows console compatibility
if platform.system() == "Windows":
    # Use simple text instead of emojis for Windows console
    ROCKET = "[START]"
    FOLDER = "[DIR]"
    SNAKE = "[PYTHON]"
    GLOBE = "[ENV]"
    GEAR = "[CHECK]"
    PACKAGE = "[INSTALL]"
    CHECK = "[OK]"
    ROBOT = "[BOT]"
    WORLD = "[WEB]"
    LIGHTBULB = "[INFO]"
    PARTY = "[READY]"
    STOP = "[STOP]"
    WARNING = "[WARN]"
    ERROR = "[ERROR]"
    SIGNAL = "[SIGNAL]"
    KEYBOARD = "[KEYBOARD]"
else:
    # Use emojis for other systems
    ROCKET = "🚀"
    FOLDER = "📁"
    SNAKE = "🐍"
    GLOBE = "🌍"
    GEAR = "⚙️"
    PACKAGE = "📦"
    CHECK = "✅"
    ROBOT = "🦑"
    WORLD = "🌐"
    LIGHTBULB = "💡"
    PARTY = "🎉"
    STOP = "🛑"
    WARNING = "⚠️"
    ERROR = "❌"
    SIGNAL = "📡"
    KEYBOARD = "📡"

class JamBitLauncher:
    def __init__(self):
        self.project_root = Path(__file__).parent
        self.processes = []
        self.running = True
        
        # Detect environment
        self.is_wsl = self.detect_wsl()
        self.python_cmd = self.get_python_command()
        
        print(f"{ROCKET} Cuttle Unified Launcher")
        print("=" * 50)
        print(f"{FOLDER} Project root: {self.project_root}")
        print(f"{SNAKE} Python: {self.python_cmd}")
        print(f"{GLOBE} Environment: {'WSL' if self.is_wsl else 'Windows'}")
        print(f"{LIGHTBULB} All execution uses the pipeline system")
        print("=" * 50)
    
    def detect_wsl(self):
        """Detect if running in WSL"""
        try:
            with open('/proc/version', 'r') as f:
                return 'microsoft' in f.read().lower()
        except:
            return False
    
    def get_python_command(self):
        """Get the appropriate Python command"""
        try:
            from core.runtime_paths import venv_python

            return str(venv_python(self.project_root))
        except Exception:
            pass
        if self.is_wsl:
            # Check for WSL virtual environment
            wsl_venv = self.project_root / ".venv_wsl" / "bin" / "python3"
            if wsl_venv.exists():
                return str(wsl_venv)
            posix_venv = self.project_root / ".venv" / "bin" / "python3"
            if posix_venv.exists():
                return str(posix_venv)
            return "python3"
        if sys.platform != "win32":
            posix_venv = self.project_root / ".venv" / "bin" / "python3"
            if posix_venv.exists():
                return str(posix_venv)
            return sys.executable or "python3"
        else:
            # Check for Windows virtual environment
            win_venv = self.project_root / ".venv" / "Scripts" / "python.exe"
            if win_venv.exists():
                return str(win_venv)
            
            # Try to find a working Python executable
            python_candidates = [
                sys.executable,  # Try the current Python first
                "python",        # Try python command
                "python3",       # Try python3 command
                "py",            # Try py launcher
            ]
            
            for python_cmd in python_candidates:
                try:
                    # Test if the Python command works
                    result = subprocess.run([python_cmd, "--version"], 
                                          capture_output=True, text=True, timeout=5)
                    if result.returncode == 0:
                        return python_cmd
                except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                    continue
            
            # If all else fails, return the original sys.executable
            return sys.executable
    
    def install_dependencies(self, include_mcp=False):
        """Install required dependencies"""
        print(f"{GEAR} Checking dependencies...")
        
        # Check Flask for web chat API
        try:
            import flask
            # Use importlib.metadata to get version to avoid deprecation warning
            try:
                from importlib.metadata import version
                flask_version = version('flask')
            except:
                flask_version = flask.__version__
            print(f"{CHECK} Flask {flask_version} available")
        except ImportError:
            print(f"{PACKAGE} Installing Flask...")
            subprocess.check_call([
                self.python_cmd, "-m", "pip", "install", 
                "flask>=2.3.0", "flask-cors>=4.0.0"
            ])
            print(f"{CHECK} Flask installed")
        
        # Check Flask-CORS
        try:
            import flask_cors
            # Use importlib.metadata to get version to avoid deprecation warning
            try:
                from importlib.metadata import version
                flask_cors_version = version('flask-cors')
            except:
                flask_cors_version = flask_cors.__version__ if hasattr(flask_cors, '__version__') else 'unknown'
            print(f"{CHECK} Flask-CORS {flask_cors_version} available")
        except ImportError:
            print(f"{PACKAGE} Installing Flask-CORS...")
            subprocess.check_call([
                self.python_cmd, "-m", "pip", "install", "flask-cors>=4.0.0"
            ])
            print(f"{CHECK} Flask-CORS installed")
        
        # Check Ungit
        self.check_ungit_dependency()
        
        # Check Discord.py
        try:
            import discord
            print(f"{CHECK} Discord.py {discord.__version__} available")
        except ImportError:
            print(f"{PACKAGE} Installing Discord.py...")
            deps = [
                "discord.py>=2.3.0", "python-dotenv", 
                "pillow", "psutil", "mss", "pytesseract", "opencv-python"
            ]
            # OpenAI is optional - only install if user has API key
            try:
                import openai
            except ImportError:
                deps.append("openai")
            if not self.is_wsl:
                deps.extend(["pywin32", "pyautogui", "pygetwindow", "pywinauto", "keyboard", "mouse"])
            else:
                # For WSL, still install keyboard and mouse for input tools
                deps.extend(["keyboard", "mouse"])
            
            subprocess.check_call([
                self.python_cmd, "-m", "pip", "install"
            ] + deps)
            print(f"{CHECK} Discord.py and dependencies installed")
        
        # Check MCP dependencies if requested
        if include_mcp:
            print(f"{GEAR} Checking MCP dependencies...")
            try:
                import mcp
                print(f"{CHECK} MCP library available")
            except ImportError:
                print(f"{PACKAGE} Installing MCP...")
                mcp_deps = [
                    "mcp>=1.0.0",
                    "asyncio-mqtt>=0.13.0",
                    "websockets>=11.0.0", 
                    "aiofiles>=23.0.0",
                    "pydantic>=2.0.0",
                    "typing-extensions>=4.0.0"
                ]
                try:
                    subprocess.check_call([
                        self.python_cmd, "-m", "pip", "install", "--upgrade"
                    ] + mcp_deps)
                    print(f"{CHECK} MCP dependencies installed")
                    
                    # Verify installation
                    try:
                        import mcp
                        print(f"{CHECK} MCP library verified")
                    except ImportError:
                        print(f"{WARNING} MCP installed but import failed - may need restart")
                        
                except subprocess.CalledProcessError as e:
                    print(f"{ERROR} Failed to install MCP dependencies: {e}")
                    print(f"{LIGHTBULB} Try running as administrator or check your Python environment")
    
    def check_ungit_dependency(self):
        """Check if Ungit is installed and auto-install if not found"""
        print(f"{GEAR} Checking Ungit dependency...")
        
        try:
            # Check if ungit command is available
            # Try multiple ways to find ungit command
            ungit_commands = ['ungit', 'ungit.cmd', 'ungit.ps1']
            
            for cmd in ungit_commands:
                try:
                    if platform.system() == "Windows":
                        # On Windows, try both direct command and with .cmd extension
                        result = subprocess.run([cmd, '--version'], 
                                              capture_output=True, text=True, timeout=5, 
                                              shell=True)
                    else:
                        result = subprocess.run([cmd, '--version'], 
                                              capture_output=True, text=True, timeout=5)
                    
                    if result.returncode == 0:
                        version = result.stdout.strip()
                        print(f"{CHECK} Ungit {version} available")
                        return True
                except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
                    continue
            
            # If none of the commands worked, try to install
            print(f"{WARNING} Ungit not found in PATH")
            print(f"{PACKAGE} Auto-installing Ungit...")
            return self.install_ungit()
            
        except Exception as e:
            print(f"{WARNING} Error checking Ungit: {e}")
            return False
    
    def install_nodejs(self):
        """Install Node.js and npm automatically on Windows"""
        try:
            print(f"{PACKAGE} Installing Node.js and npm...")
            
            if platform.system() == "Windows":
                # Use winget to install Node.js on Windows 10+
                print(f"{LIGHTBULB} Attempting to install Node.js via winget...")
                result = subprocess.run(['winget', 'install', 'OpenJS.NodeJS', '--silent'], 
                                      capture_output=True, text=True, timeout=300)
                
                if result.returncode == 0:
                    print(f"{CHECK} Node.js installed successfully via winget")
                    # Refresh environment variables (basic approach - might require restart)
                    print(f"{LIGHTBULB} Node.js installed. You may need to restart your terminal.")
                    print(f"{LIGHTBULB} Attempting to use npm now...")
                    
                    # Try to verify npm is available
                    import time
                    time.sleep(2)  # Wait for installation to complete
                    
                    try:
                        verify_result = subprocess.run(['npm', '--version'], 
                                                     capture_output=True, text=True, timeout=5, 
                                                     shell=True)
                        if verify_result.returncode == 0:
                            version = verify_result.stdout.strip()
                            print(f"{CHECK} npm {version} is now available")
                            return True
                        else:
                            print(f"{WARNING} Node.js installed but npm not yet in PATH")
                            print(f"{LIGHTBULB} Please restart your terminal and try again")
                            return False
                    except:
                        print(f"{WARNING} npm not yet available in current session")
                        print(f"{LIGHTBULB} Please restart your terminal and try again")
                        return False
                else:
                    print(f"{ERROR} Failed to install via winget: {result.stderr}")
                    print(f"{LIGHTBULB} Please install Node.js manually from: https://nodejs.org/")
                    return False
            else:
                print(f"{ERROR} Automatic Node.js installation only supported on Windows")
                print(f"{LIGHTBULB} Please install Node.js manually:")
                print(f"{LIGHTBULB} - Ubuntu/Debian: sudo apt install nodejs npm")
                print(f"{LIGHTBULB} - macOS: brew install node")
                return False
                
        except FileNotFoundError:
            print(f"{ERROR} winget not found. Please install Node.js manually from: https://nodejs.org/")
            return False
        except Exception as e:
            print(f"{ERROR} Error installing Node.js: {e}")
            print(f"{LIGHTBULB} Please install Node.js manually from: https://nodejs.org/")
            return False
    
    def install_ungit(self):
        """Install Ungit globally using npm"""
        try:
            print(f"{PACKAGE} Installing Ungit globally...")
            # Install Ungit globally
            result = subprocess.run(['npm', 'install', '-g', 'ungit'], 
                                  capture_output=True, text=True, timeout=120)
            if result.returncode == 0:
                print(f"{CHECK} Ungit installed successfully")
                # Verify installation
                verify_result = subprocess.run(['ungit', '--version'], 
                                             capture_output=True, text=True, timeout=5)
                if verify_result.returncode == 0:
                    version = verify_result.stdout.strip()
                    print(f"{CHECK} Ungit {version} verified")
                    return True
                else:
                    print(f"{WARNING} Ungit installed but verification failed")
                    return False
            else:
                print(f"{ERROR} Failed to install Ungit: {result.stderr}")
                print(f"{LIGHTBULB} You can install it manually: npm install -g ungit")
                return False
        except subprocess.TimeoutExpired:
            print(f"{ERROR} Ungit installation timed out")
            print(f"{LIGHTBULB} You can install it manually: npm install -g ungit")
            return False
        except FileNotFoundError:
            print(f"{ERROR} npm command not found")
            print(f"{LIGHTBULB} Attempting to install Node.js automatically...")
            
            # Try to install Node.js automatically
            if self.install_nodejs():
                print(f"{LIGHTBULB} Node.js installed. Retrying Ungit installation...")
                # Retry Ungit installation
                try:
                    result = subprocess.run(['npm', 'install', '-g', 'ungit'], 
                                          capture_output=True, text=True, timeout=120, shell=True)
                    if result.returncode == 0:
                        print(f"{CHECK} Ungit installed successfully")
                        return True
                    else:
                        print(f"{ERROR} Failed to install Ungit after Node.js installation")
                        return False
                except Exception as e:
                    print(f"{ERROR} Error installing Ungit after Node.js installation: {e}")
                    return False
            else:
                print(f"{LIGHTBULB} Please install Node.js manually from: https://nodejs.org/")
                print(f"{LIGHTBULB} Then install Ungit manually: npm install -g ungit")
                return False
        except Exception as e:
            print(f"{ERROR} Error installing Ungit: {e}")
            print(f"{LIGHTBULB} You can install it manually: npm install -g ungit")
            return False
    
    def start_ungit(self):
        """Start Ungit with the current project"""
        print(f"{WORLD} Starting Ungit...")
        
        try:
            # Try to import project manager to get current project
            project_path = None
            try:
                # Add project root to Python path
                if str(self.project_root) not in sys.path:
                    sys.path.insert(0, str(self.project_root))
                from managers.project_manager import project_manager
                
                # Get current project
                current_project = project_manager.get_current_project()
                if current_project and 'path' in current_project:
                    project_path = Path(current_project['path'])
                    if project_path.exists():
                        print(f"{FOLDER} Current Project: {current_project['name']}")
                        print(f"{FOLDER} Path: {project_path}")
                    else:
                        print(f"{WARNING} Project path does not exist: {project_path}")
                        print(f"{WARNING} This path may be from a different machine or network drive")
                        print(f"{LIGHTBULB} Please update the project path in the database or switch to a valid project")
                        project_path = None
                else:
                    print(f"{WARNING} No current project selected")
            except ImportError as ie:
                print(f"{WARNING} Project manager not available: {ie}")
                print(f"{LIGHTBULB} Starting Ungit with workspace root instead")
            except Exception as e:
                print(f"{WARNING} Could not get current project: {e}")
                import traceback
                traceback.print_exc()
            
            # Use project path if available, otherwise use workspace root
            if not project_path:
                project_path = self.project_root
                print(f"{FOLDER} Using workspace root: {project_path}")
            
            # Check if Ungit is already running
            try:
                import requests
                response = requests.get('http://localhost:8448', timeout=2)
                if response.status_code == 200:
                    print(f"{CHECK} Ungit is already running on port 8448")
                    print(f"{LIGHTBULB} URL: http://localhost:8448")
                    return True
            except:
                pass  # Ungit not running, continue
            
            # Start Ungit with current project
            # Use the same command detection logic as check_ungit_dependency
            ungit_cmd = 'ungit'
            if platform.system() == "Windows":
                # Try to find the working ungit command
                ungit_commands = ['ungit', 'ungit.cmd', 'ungit.ps1']
                for cmd in ungit_commands:
                    try:
                        test_result = subprocess.run([cmd, '--version'], 
                                                   capture_output=True, text=True, timeout=2,
                                                   shell=True)
                        if test_result.returncode == 0:
                            ungit_cmd = cmd
                            break
                    except:
                        continue
            
            process = subprocess.Popen([
                ungit_cmd, 
                '--port', '8448',
                '--launchBrowser', 'false',
                '--rootPath', str(project_path)
            ], cwd=self.project_root, shell=(platform.system() == "Windows"))
            
            self.processes.append(("Ungit", process))
            print(f"{CHECK} Ungit started (PID: {process.pid})")
            print(f"{LIGHTBULB} URL: http://localhost:8448")
            print(f"{FOLDER} Repository: {project_path}")
            return True
            
        except Exception as e:
            print(f"{ERROR} Failed to start Ungit: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def start_discord_bot(self, use_mcp=False):
        """Start the Discord bot"""
        print(f"{ROBOT} Starting Discord bot...")
        
        # Always use MCP bot (legacy bots removed)
        bot_script = "bots/bot_mcp.py"
        
        try:
            # Set up environment to ensure proper imports
            env = os.environ.copy()
            env['PYTHONPATH'] = str(self.project_root)
            
            process = subprocess.Popen([
                self.python_cmd, bot_script
            ], cwd=self.project_root, env=env)
            
            bot_type = "Discord Bot"
            self.processes.append((bot_type, process))
            print(f"{CHECK} {bot_type} started (PID: {process.pid})")
            return True
        except Exception as e:
            print(f"{ERROR} Failed to start Discord bot: {e}")
            return False
    
    def start_web_chat_api(self):
        """Start the Web Chat API server"""
        print(f"{WORLD} Starting Web Chat API server...")
        
        try:
            # Set up environment to ensure proper imports
            env = os.environ.copy()
            
            # Get user site-packages path
            import site
            user_site = site.USER_SITE
            
            # Build PYTHONPATH with both project root and user site-packages
            pythonpath_parts = [str(self.project_root)]
            if user_site:
                pythonpath_parts.append(user_site)
            
            # Add existing PYTHONPATH if present
            if 'PYTHONPATH' in env:
                pythonpath_parts.append(env['PYTHONPATH'])
            
            env['PYTHONPATH'] = os.pathsep.join(pythonpath_parts)
            env['PYTHONNOUSERSITE'] = '0'  # Enable user site-packages
            
            print(f"{LIGHTBULB} PYTHONPATH includes user site: {user_site}")
            
            process = subprocess.Popen([
                self.python_cmd, "api/web_chat_api.py"
            ], cwd=self.project_root, env=env)
            
            self.processes.append(("Web Chat API", process))
            print(f"{CHECK} Web Chat API started (PID: {process.pid})")
            print(f"{LIGHTBULB} Web interface: http://localhost:8080")
            return True
        except Exception as e:
            print(f"{ERROR} Failed to start Web Chat API: {e}")
            return False
    
    def auto_start_default_pipeline(self):
        """Auto-start the default pipeline if enabled"""
        try:
            from managers.settings_manager import get_settings_manager
            
            settings_mgr = get_settings_manager()
            
            # Check if auto-start is enabled
            if not settings_mgr.should_auto_start():
                print(f"{LIGHTBULB} Auto-start is disabled - skipping default pipeline")
                return
            
            # Get default pipeline
            default_pipeline = settings_mgr.get_default_pipeline()
            pipeline_path = settings_mgr.get_pipeline_path(default_pipeline)
            
            if not pipeline_path:
                print(f"{WARNING} Default pipeline '{default_pipeline}' not found - skipping auto-start")
                return
            
            print(f"{ROCKET} Auto-starting default pipeline: {default_pipeline}")
            
            # Wait for web server to be ready
            import time
            max_retries = 10
            retry_delay = 0.5
            server_ready = False
            
            for attempt in range(max_retries):
                try:
                    import requests
                    # Simple health check
                    response = requests.get('http://localhost:8080/api/health', timeout=2)
                    if response.status_code == 200:
                        server_ready = True
                        break
                except:
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay)
            
            if not server_ready:
                print(f"{WARNING} Web server not ready - pipeline will need to be started manually")
                print(f"{LIGHTBULB} Visit http://localhost:8080/node_editor.html to start the pipeline")
                return
            
            # Call the API to start the pipeline
            try:
                import requests
                response = requests.post('http://localhost:8080/api/pipeline-start', 
                                        json={'pipelineName': default_pipeline},
                                        timeout=10)
                
                if response.ok:
                    result = response.json()
                    if result.get('success'):
                        if result.get('already_running'):
                            print(f"{CHECK} Pipeline '{default_pipeline}' is already running")
                        elif result.get('is_persistent'):
                            print(f"{CHECK} Pipeline '{default_pipeline}' started successfully")
                            print(f"{SIGNAL} Persistent pipeline - running continuously")
                            triggers = result.get('triggers', [])
                            if triggers:
                                trigger_names = ', '.join([t.get('name', t.get('type', 'Unknown')) for t in triggers])
                                print(f"{SIGNAL} Persistent triggers: {trigger_names}")
                            print(f"{WORLD} Web interface: http://localhost:8080")
                            print(f"{CHECK} Pipeline is now listening for triggers!")
                        else:
                            print(f"{LIGHTBULB} Pipeline '{default_pipeline}' loaded (one-shot mode)")
                            print(f"{LIGHTBULB} Execute manually via web interface: http://localhost:8080/node_editor.html")
                    else:
                        print(f"{WARNING} Failed to start pipeline: {result.get('error', 'Unknown error')}")
                else:
                    print(f"{WARNING} Failed to start pipeline (HTTP {response.status_code})")
                    
            except requests.exceptions.RequestException as e:
                print(f"{WARNING} Could not connect to web server: {e}")
                print(f"{LIGHTBULB} You can manually start pipelines from the web interface")
            except Exception as e:
                print(f"{WARNING} Error calling pipeline start API: {e}")
                
        except Exception as e:
            print(f"{WARNING} Error auto-starting default pipeline: {e}")
            print(f"{LIGHTBULB} You can manually start pipelines from the web interface")
    
    def monitor_processes(self):
        """Monitor running processes"""
        while self.running:
            time.sleep(5)
            
            for name, process in self.processes[:]:
                if process.poll() is not None:
                    print(f"{WARNING} {name} stopped unexpectedly (exit code: {process.returncode})")
                    self.processes.remove((name, process))
            
            if not self.processes:
                print(f"{ERROR} All processes stopped")
                break
    
    def stop_all_processes(self):
        """Stop all running processes"""
        print(f"\n{STOP} Stopping all processes...")
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
                
                print(f"{CHECK} {name} stopped")
            except Exception as e:
                print(f"{ERROR} Error stopping {name}: {e}")
        
        self.processes.clear()
    
    def open_launch_report(self):
        """Open the launch report in the default browser"""
        try:
            import webbrowser
            import time
            
            # Wait a moment for services to fully initialize
            time.sleep(2)
            
            # Open the launch report
            webbrowser.open('http://localhost:8080/launch-report')
            print(f"{WORLD} Launch report opened in browser: http://localhost:8080/launch-report")
            
        except Exception as e:
            print(f"{WARNING} Could not open launch report: {e}")
            print(f"{LIGHTBULB} You can manually open: http://localhost:8080")
    
    def kill_existing_bots(self):
        """Kill existing bot processes (Python, terminal, PowerShell)"""
        print(f"{STOP} Killing existing bot processes...")
        killed_count = 0
        
        # Get current process PID to avoid killing ourselves
        current_pid = os.getpid()
        print(f"{LIGHTBULB} Current process PID: {current_pid} (will be preserved)")
        
        try:
            # Use system taskkill commands for more effective killing
            import subprocess
            import platform
            
            if platform.system() == "Windows":
                # Kill Python processes except current one
                print("Killing Python processes (excluding current)...")
                try:
                    # Use psutil for more precise control to avoid killing current process
                    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                        try:
                            if proc.info['name'] and 'python.exe' in proc.info['name'].lower():
                                # Skip current process
                                if proc.info['pid'] == current_pid:
                                    print(f"Skipping current process: {proc.info['pid']}")
                                    continue
                                
                                # Check if it's running one of our bot scripts
                                cmdline = proc.info['cmdline']
                                if cmdline and len(cmdline) > 1:
                                    script_name = cmdline[-1].lower()
                                    if any(bot_script in script_name for bot_script in ['bot_mcp.py', 'web_chat_api.py', 'bots/bot', 'api/web_chat', 'launcher.py']):
                                        print(f"Killing Python bot process: {proc.info['pid']} - {script_name}")
                                        proc.terminate()
                                        killed_count += 1
                                        
                                        # Wait a moment then force kill if needed
                                        try:
                                            proc.wait(timeout=3)
                                        except psutil.TimeoutExpired:
                                            proc.kill()
                                            print(f"Force killed process: {proc.info['pid']}")
                                            
                        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                            continue
                            
                except Exception as e:
                    print(f"Error killing Python processes: {e}")
                
                # Kill Pythonw processes except current one
                try:
                    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                        try:
                            if proc.info['name'] and 'pythonw.exe' in proc.info['name'].lower():
                                # Skip current process
                                if proc.info['pid'] == current_pid:
                                    print(f"Skipping current process: {proc.info['pid']}")
                                    continue
                                
                                # Check if it's running one of our bot scripts
                                cmdline = proc.info['cmdline']
                                if cmdline and len(cmdline) > 1:
                                    script_name = cmdline[-1].lower()
                                    if any(bot_script in script_name for bot_script in ['bot_mcp.py', 'web_chat_api.py', 'bots/bot', 'api/web_chat', 'launcher.py']):
                                        print(f"Killing Pythonw bot process: {proc.info['pid']} - {script_name}")
                                        proc.terminate()
                                        killed_count += 1
                                        
                                        # Wait a moment then force kill if needed
                                        try:
                                            proc.wait(timeout=3)
                                        except psutil.TimeoutExpired:
                                            proc.kill()
                                            print(f"Force killed process: {proc.info['pid']}")
                                            
                        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                            continue
                            
                except Exception as e:
                    print(f"Error killing Pythonw processes: {e}")
                
                # Kill terminal processes that might be running bots (excluding current)
                terminal_processes = ['cmd.exe', 'powershell.exe', 'pwsh.exe', 'WindowsTerminal.exe', 'conhost.exe']
                for term_proc in terminal_processes:
                    try:
                        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                            try:
                                if proc.info['name'] and term_proc.lower() in proc.info['name'].lower():
                                    # Skip current process
                                    if proc.info['pid'] == current_pid:
                                        print(f"Skipping current terminal process: {proc.info['pid']}")
                                        continue
                                    
                                    # Check if the terminal is running bot commands
                                    cmdline = ' '.join(proc.info['cmdline']) if proc.info['cmdline'] else ""
                                    if any(bot_script in cmdline.lower() for bot_script in ['bot_mcp.py', 'launcher.py']):
                                        print(f"Killing terminal process running bot: {proc.info['pid']} - {proc.info['name']}")
                                        proc.terminate()
                                        killed_count += 1
                                        
                                        # Wait a moment then force kill if needed
                                        try:
                                            proc.wait(timeout=3)
                                        except psutil.TimeoutExpired:
                                            proc.kill()
                                            print(f"Force killed terminal process: {proc.info['pid']}")
                                            
                            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                                continue
                                
                    except Exception as e:
                        print(f"Error killing {term_proc} processes: {e}")
            else:
                # For non-Windows systems, use psutil approach
                print("Using psutil for process killing...")
                
                # Get current project directory for comparison
                current_dir = str(self.project_root).lower()
                
                # Find and kill Python processes running bot scripts (excluding current)
                for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                    try:
                        if proc.info['name'] and 'python' in proc.info['name'].lower():
                            # Skip current process
                            if proc.info['pid'] == current_pid:
                                print(f"Skipping current process: {proc.info['pid']}")
                                continue
                                
                            cmdline = proc.info['cmdline']
                            if cmdline and len(cmdline) > 1:
                                # Check if it's running one of our bot scripts
                                script_name = cmdline[-1].lower()
                                if any(bot_script in script_name for bot_script in ['bot_mcp.py', 'web_chat_api.py', 'bots/bot', 'api/web_chat']):
                                    # Check if it's running from our project directory
                                    if len(cmdline) > 2:
                                        working_dir = cmdline[1] if len(cmdline) > 1 else ""
                                        if current_dir in working_dir.lower() or current_dir in script_name:
                                            print(f"Killing Python bot process: {proc.info['pid']} - {script_name}")
                                            proc.terminate()
                                            killed_count += 1
                                            
                                            # Wait a moment then force kill if needed
                                            try:
                                                proc.wait(timeout=3)
                                            except psutil.TimeoutExpired:
                                                proc.kill()
                                                print(f"Force killed process: {proc.info['pid']}")
                                                
                    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                        continue
                
                # Kill terminal processes that might be running bots (excluding current)
                terminal_processes = ['bash', 'zsh', 'sh']
                for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                    try:
                        if proc.info['name'] and any(term in proc.info['name'].lower() for term in terminal_processes):
                            # Skip current process
                            if proc.info['pid'] == current_pid:
                                print(f"Skipping current terminal process: {proc.info['pid']}")
                                continue
                                
                            cmdline = ' '.join(proc.info['cmdline']) if proc.info['cmdline'] else ""
                            # Check if the terminal is running Python bot commands
                            if any(bot_script in cmdline.lower() for bot_script in ['bot_mcp.py', 'launcher.py']):
                                print(f"Killing terminal process running bot: {proc.info['pid']} - {proc.info['name']}")
                                proc.terminate()
                                killed_count += 1
                                
                                # Wait a moment then force kill if needed
                                try:
                                    proc.wait(timeout=3)
                                except psutil.TimeoutExpired:
                                    proc.kill()
                                    print(f"Force killed terminal process: {proc.info['pid']}")
                                    
                    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                        continue
            
            if killed_count > 0:
                print(f"{CHECK} Successfully killed {killed_count} processes")
            else:
                print(f"{LIGHTBULB} No existing bot processes found")
                
        except Exception as e:
            print(f"{ERROR} Error killing existing processes: {e}")
            print(f"{WARNING} You may need to manually close terminal windows or kill processes")
    
    def signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        print(f"\n{SIGNAL} Received signal {signum}")
        self.stop_all_processes()
        sys.exit(0)
    
    def run(self, use_mcp=False):
        """Main launcher function"""
        try:
            # Set up signal handlers
            signal.signal(signal.SIGINT, self.signal_handler)
            signal.signal(signal.SIGTERM, self.signal_handler)
            
            # Install dependencies
            self.install_dependencies(include_mcp=True)
            
            # Start services
            bot_started = self.start_discord_bot(use_mcp=use_mcp)
            api_started = self.start_web_chat_api()
            
            # Start Ungit if available
            ungit_started = False
            if use_mcp and self.check_ungit_dependency():
                ungit_started = self.start_ungit()
            
            if not bot_started and not api_started and not ungit_started:
                print(f"{ERROR} Failed to start any services")
                return False
            
            # Show status
            print(f"\n{PARTY} Cuttle is running!")
            print("=" * 50)
            if bot_started:
                print(f"{ROBOT} Discord Bot: Active")
            if api_started:
                print(f"{WORLD} Web Chat API: http://localhost:8080")
            if ungit_started:
                print(f"{WORLD} Ungit: http://localhost:8448")
            print(f"{STOP} Press Ctrl+C to stop all services")
            print("=" * 50)
            
            # Signal initialization complete
            print(f"{PARTY} [READY] Initialization complete!")
            
            # Auto-start default pipeline if enabled
            self.auto_start_default_pipeline()
            
            # Open launch report after initialization
            self.open_launch_report()
            
            # Monitor processes
            self.monitor_processes()
            
        except KeyboardInterrupt:
            print(f"\n{KEYBOARD} Keyboard interrupt received")
        except Exception as e:
            print(f"\n{ERROR} Unexpected error: {e}")
        finally:
            self.stop_all_processes()
        
        return True

def show_menu():
    """Show the launcher menu and handle user selection"""
    print(f"\n{ROCKET} Cuttle Launcher Menu")
    print("=" * 50)
    print("Select a launcher option:")
    print()
    print("1. [FULL] Full System - Web Chat + Discord + Ungit (Recommended)")
    print("2. [WEB] Web Chat Only - Pipeline-based chat interface")
    print("3. [DISCORD] Discord Bot Only")
    print("4. [DAEMON] Daemon Mode - Hot-swap pipelines, auto-restart on crash")
    print()
    print("K. [KILL] Kill Existing Processes")
    print("0. [EXIT] Exit")
    print("=" * 50)
    print(f"{LIGHTBULB} All execution now uses the pipeline system!")
    print(f"{LIGHTBULB} Create and edit pipelines in the Node Editor")
    
    # Check if we're in a non-interactive environment
    # Temporarily disabled to allow interactive menu
    # if not sys.stdin.isatty():
    #     print(f"{WARNING} Non-interactive environment detected. Starting MCP full system...")
    #     launcher = JamBitLauncher()
    #     return launcher.run(use_mcp=True)
    
    while True:
        try:
            choice = input("Enter your choice (0-4, K): ").strip().upper()
            
            if choice == "1":
                print(f"\n{ROCKET} Starting Full System...")
                launcher = JamBitLauncher()
                return launcher.run(use_mcp=True)
            elif choice == "2":
                print(f"\n{WORLD} Starting Web Chat Only...")
                return run_web_chat_only()
            elif choice == "3":
                print(f"\n{ROBOT} Starting Discord Bot Only...")
                return run_discord_bot_only()
            elif choice == "4":
                print(f"\n{SIGNAL} Starting Cuttle Daemon (hot-swap + auto-restart)...")
                return run_daemon_mode()
            elif choice == "K":
                print(f"\n{STOP} Killing Existing Processes...")
                return run_kill_existing_bots()
            elif choice == "0":
                print(f"\n{STOP} Exiting...")
                return True
            else:
                print(f"{WARNING} Invalid choice. Please enter 0, 1, 2, 3, 4, or K.")
        except KeyboardInterrupt:
            print(f"\n{KEYBOARD} Keyboard interrupt received. Exiting...")
            return True
        except EOFError:
            print(f"\n{WARNING} No input available. Starting full system...")
            launcher = JamBitLauncher()
            return launcher.run(use_mcp=True)
        except Exception as e:
            print(f"{ERROR} Error: {e}")
            return False

def run_discord_bot_only():
    """Run Discord bot only"""
    try:
        subprocess.run([sys.executable, "bots/bot_mcp.py"], cwd=Path(__file__).parent)
        return True
    except Exception as e:
        print(f"{ERROR} Failed to start Discord bot: {e}")
        return False

def run_daemon_mode():
    """Run Cuttle daemon: manages Flask + Discord, hot-swaps pipelines, auto-restarts."""
    try:
        daemon_script = Path(__file__).parent / "scripts" / "cuttle_daemon.py"
        if not daemon_script.exists():
            print(f"{ERROR} Daemon script not found: {daemon_script}")
            return False
        subprocess.run([sys.executable, str(daemon_script)], cwd=Path(__file__).parent)
        return True
    except Exception as e:
        print(f"{ERROR} Failed to start daemon: {e}")
        return False

def run_ungit():
    """Run Ungit with current project"""
    print(f"\n{WORLD} Starting Ungit with Current Project...")
    print("=" * 50)
    
    launcher = None
    try:
        launcher = JamBitLauncher()
        
        # Set up signal handlers
        signal.signal(signal.SIGINT, launcher.signal_handler)
        signal.signal(signal.SIGTERM, launcher.signal_handler)
        
        # Check dependencies (including Ungit)
        launcher.install_dependencies(include_mcp=True)
        
        # Check if Ungit is available
        if not launcher.check_ungit_dependency():
            print(f"{ERROR} Ungit is not installed")
            print(f"{LIGHTBULB} Please install Ungit first: npm install -g ungit")
            return False
        
        # Start Ungit
        ungit_started = launcher.start_ungit()
        
        if not ungit_started:
            print(f"{ERROR} Failed to start Ungit")
            return False
        
        # Show status
        print(f"\n{PARTY} Ungit is running!")
        print("=" * 50)
        print(f"{WORLD} Ungit URL: http://localhost:8448")
        print(f"{LIGHTBULB} Ungit is running with your current project")
        print(f"{STOP} Press Ctrl+C to stop")
        print("=" * 50)
        
        # Signal initialization complete
        print(f"{PARTY} [READY] Initialization complete!")
        
        # Monitor processes
        launcher.monitor_processes()
        
        return True
        
    except KeyboardInterrupt:
        print(f"\n{KEYBOARD} Keyboard interrupt received")
        if launcher:
            launcher.stop_all_processes()
        return True
    except Exception as e:
        print(f"\n{ERROR} Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return False

def run_web_chat_only():
    """Run Web Chat API only with pipeline support"""
    print(f"\n{ROCKET} Starting Web Chat Only...")
    print("=" * 50)
    
    launcher = None
    try:
        launcher = JamBitLauncher()
        
        # Set up signal handlers
        signal.signal(signal.SIGINT, launcher.signal_handler)
        signal.signal(signal.SIGTERM, launcher.signal_handler)
        
        # Install all dependencies including MCP (same as full system)
        launcher.install_dependencies(include_mcp=True)
        
        # Start only Web Chat API (not Discord bot)
        api_started = launcher.start_web_chat_api()
        
        if not api_started:
            print(f"{ERROR} Failed to start Web Chat API")
            return False
        
        # Show status
        print(f"\n{PARTY} Web Chat is running!")
        print("=" * 50)
        print(f"{WORLD} Web Chat API: http://localhost:8080")
        print(f"{LIGHTBULB} Discord bot not running in this mode")
        print(f"{LIGHTBULB} All messages route through pipelines")
        print(f"{STOP} Press Ctrl+C to stop")
        print("=" * 50)
        
        # Signal initialization complete
        print(f"{PARTY} [READY] Initialization complete!")
        
        # Open launch report after initialization
        launcher.open_launch_report()
        
        # Monitor processes
        launcher.monitor_processes()
        
        return True
        
    except KeyboardInterrupt:
        print(f"\n{KEYBOARD} Keyboard interrupt received")
        if launcher:
            launcher.stop_all_processes()
        return True
    except Exception as e:
        print(f"\n{ERROR} Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return False

def run_kill_existing_bots():
    """Kill existing bot processes"""
    print(f"{STOP} Killing existing bot processes...")
    print("=" * 50)
    
    try:
        launcher = JamBitLauncher()
        launcher.kill_existing_bots()
        print("=" * 50)
        print(f"{CHECK} Kill operation completed!")
        print(f"{LIGHTBULB} You can now start fresh bot instances.")
        
    except Exception as e:
        print("=" * 50)
        print(f"{ERROR} Failed to kill existing processes: {e}")
        print(f"{WARNING} You may need to manually close terminal windows or kill processes.")
        print(f"{LIGHTBULB} Try running as administrator if you get permission errors.")
    
    return False  # Return False to stay in menu loop

def main():
    # Check if we should show menu or run directly
    if len(sys.argv) > 1:
        # Direct mode - check for specific flags
        if "--ungit" in sys.argv or "-u" in sys.argv:
            # Start Ungit directly
            success = run_ungit()
            sys.exit(0 if success else 1)
        else:
            # Default to full system for any arguments
            launcher = JamBitLauncher()
            success = launcher.run(use_mcp=True)
            sys.exit(0 if success else 1)
    else:
        # Menu mode
        success = show_menu()
        sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()

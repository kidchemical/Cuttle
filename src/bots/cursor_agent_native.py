"""
Native cursor-agent interface for Python integration.
This module provides a clean interface to cursor-agent using cursor-agent-tools package.
"""

import asyncio
import subprocess
import shutil
import os
from pathlib import Path
from typing import Optional, Dict, Any
import json
import time

# Load environment variables
def reload_env():
    """Reload environment variables from .env file"""
    try:
        from dotenv import load_dotenv
        import os
        # Get the project root (2 levels up from bots/)
        script_dir = os.path.dirname(os.path.abspath(__file__))  # src/bots
        src_dir = os.path.dirname(script_dir)  # src
        project_root = os.path.dirname(src_dir)  # repo root
        
        # Try both locations: project root and src/
        env_files = [
            os.path.join(project_root, '.env'),
            os.path.join(src_dir, '.env'),
        ]
        
        loaded = False
        for env_file in env_files:
            if os.path.exists(env_file):
                print(f"[Cursor Agent] Loading .env from: {env_file}")
                load_dotenv(env_file, override=True)
                loaded = True
        
        return loaded
    except ImportError:
        return False
    except Exception as e:
        print(f"[Cursor Agent] Error loading .env: {e}")
        return False

# Initial load
reload_env()

# Try to import cursor-agent-tools
try:
    from cursor_agent_tools import create_agent
    CURSOR_AGENT_TOOLS_AVAILABLE = True
    print("[Cursor Agent] cursor-agent-tools package imported successfully")
except ImportError as e:
    CURSOR_AGENT_TOOLS_AVAILABLE = False
    print(f"[Cursor Agent] cursor-agent-tools package import failed: {e}")

class CursorAgentNative:
    """Native cursor-agent interface using cursor-agent-tools package"""
    
    def __init__(self, project_path: str = None):
        # Reload environment variables to get latest API keys
        reload_env()
        
        # Set project path (don't change cwd here to avoid breaking imports)
        if project_path:
            self.project_path = project_path
        else:
            # Default to the actual project root, not the bots directory
            script_dir = os.path.dirname(os.path.abspath(__file__))  # src/bots
            src_dir = os.path.dirname(script_dir)  # src
            project_root = os.path.dirname(src_dir)  # repo root
            self.project_path = project_root
            
        print(f"[Cursor Agent] Working directory set to: {self.project_path}")
        self.session_active = False
        self.agent = None
        self._last_init_error = None
        
        # Initialize the cursor-agent if available
        if CURSOR_AGENT_TOOLS_AVAILABLE:
            print(f"[Cursor Agent] cursor-agent-tools package is available, attempting initialization...")
            
            # Check for API keys before trying
            has_anthropic = bool(os.getenv('ANTHROPIC_API_KEY'))
            has_openai = bool(os.getenv('OPENAI_API_KEY'))
            print(f"[Cursor Agent] API Keys - Anthropic: {has_anthropic}, OpenAI: {has_openai}")
            
            # Try different models in order of preference
            models_to_try = [
                'claude-3-haiku-20240307',     # Requires ANTHROPIC_API_KEY (Claude 3 Haiku - most reliable)
                'claude-3-sonnet-20240229',    # Requires ANTHROPIC_API_KEY (Claude 3 Sonnet)
                'claude-3-5-sonnet-20240620',  # Requires ANTHROPIC_API_KEY (Claude 3.5 Sonnet)
                'gpt-4o',                      # Requires OPENAI_API_KEY
                'gpt-3.5-turbo',              # Requires OPENAI_API_KEY
                'ollama/llama3.1',            # Requires local Ollama
            ]
            
            for model in models_to_try:
                try:
                    print(f"[Cursor Agent] Trying to initialize with model: {model}")
                    self.agent = create_agent(model=model)
                    print(f"[Cursor Agent] ✅ SUCCESS: cursor-agent-tools initialized with {model}")
                    break
                except Exception as e:
                    print(f"[Cursor Agent] ⚠️ Could not initialize with {model}: {e}")
                    # Store the last error for debugging
                    self._last_init_error = str(e)
                    continue
            
            if self.agent is None:
                error_msg = getattr(self, '_last_init_error', 'Unknown error')
                print("[Cursor Agent] ❌ ERROR: Could not initialize cursor-agent-tools with any model")
                print(f"[Cursor Agent] Last error: {error_msg}")
                print("[Cursor Agent] Please set up one of the following API keys:")
                print("[Cursor Agent] - ANTHROPIC_API_KEY for Claude models")
                print("[Cursor Agent] - OPENAI_API_KEY for GPT models")
                print("[Cursor Agent] - Or install Ollama for local models")
                print("[Cursor Agent] You can set API keys in your .env file or environment variables.")
        else:
            print("[Cursor Agent] ❌ cursor-agent-tools package is NOT available")
        
    
    def is_available(self) -> bool:
        """Check if cursor-agent is available"""
        return CURSOR_AGENT_TOOLS_AVAILABLE and self.agent is not None
    
    async def send_prompt(self, prompt: str, project_path: str = None) -> Dict[str, Any]:
        """
        Send a prompt to cursor-agent and return the response
        
        Args:
            prompt: The prompt to send to cursor-agent
            project_path: Optional project path (defaults to instance path)
            
        Returns:
            Dict with success status, output, and error information
        """
        # Use provided project path or instance default
        working_dir = project_path or self.project_path
        
        # Ensure the directory exists
        if not os.path.exists(working_dir):
            return {
                "success": False,
                "error": f"Project path does not exist: {working_dir}",
                "output": "",
                "method": "native"
            }
        
        print(f"[Cursor Agent] Sending prompt to cursor-agent")
        print(f"[Cursor Agent] Working directory: {working_dir}")
        print(f"[Cursor Agent] Prompt: {prompt[:100]}{'...' if len(prompt) > 100 else ''}")
        
        # Try cursor-agent-tools first if available
        if self.is_available():
            try:
                # Use cursor-agent-tools to process the prompt
                async def run_agent():
                    try:
                        response = await self.agent.chat(prompt)
                        return response
                    except Exception as e:
                        raise e
                
                # Run the agent directly (it's already async)
                result = await run_agent()
                
                print(f"INFO: Response received from cursor-agent-tools")
                print(f"INFO: Response length: {len(str(result))} chars")
                
                # Convert response to string if it's not already
                output = str(result) if result else ""
                success = bool(output.strip())
                
                return {
                    "success": success,
                    "output": output,
                    "error": None,
                    "return_code": 0 if success else 1,
                    "method": "native-tools"
                }
                
            except Exception as e:
                print(f"WARNING: cursor-agent-tools failed, falling back to CLI: {e}")
        
        # Fallback to CLI method in non-interactive mode
        try:
            # Check if cursor-agent CLI is available
            cursor_agent_path = shutil.which("cursor-agent")
            if not cursor_agent_path:
                return {
                    "success": False,
                    "error": "cursor-agent CLI not found. Install with: curl -fsSL https://cursor.sh/install.sh | sh",
                    "output": "",
                    "method": "native"
                }
            
            # Run cursor-agent in non-interactive mode
            cmd = [
                cursor_agent_path,
                "-p", prompt,  # Non-interactive mode
                "--output-format", "text"
            ]
            
            print(f"INFO: Running cursor-agent CLI: {' '.join(cmd)}")
            
            # Run the command in the project directory
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                cwd=working_dir,
                capture_output=True,
                text=True,
                timeout=60
            )
            
            print(f"INFO: cursor-agent CLI return code: {result.returncode}")
            print(f"INFO: cursor-agent CLI stdout length: {len(result.stdout)}")
            if result.stderr:
                print(f"INFO: cursor-agent CLI stderr: {result.stderr}")
            
            success = result.returncode == 0 and result.stdout.strip()
            
            return {
                "success": success,
                "output": result.stdout,
                "error": result.stderr if not success else None,
                "return_code": result.returncode,
                "method": "native-cli"
            }
            
        except subprocess.TimeoutExpired:
            error_msg = "cursor-agent CLI timed out after 60 seconds"
            print(f"ERROR: {error_msg}")
            return {
                "success": False,
                "error": error_msg,
                "output": "",
                "method": "native"
            }
        except Exception as e:
            error_msg = f"Error running cursor-agent CLI: {e}"
            print(f"ERROR: {error_msg}")
            return {
                "success": False,
                "error": error_msg,
                "output": "",
                "method": "native"
            }
    
    async def start_session(self, project_name: str = None) -> Dict[str, Any]:
        """
        Start a cursor-agent session (for compatibility with WSL interface)
        
        Args:
            project_name: Optional project name
            
        Returns:
            Dict with session status
        """
        if not self.is_available():
            return {
                "success": False,
                "error": "cursor-agent-tools not available",
                "session_id": None
            }
        
        # For native implementation, we don't need to maintain a persistent session
        # Just verify cursor-agent is working
        test_result = await self.send_prompt("Hello, this is a test message.")
        
        if test_result["success"]:
            self.session_active = True
            return {
                "success": True,
                "error": None,
                "session_id": f"native_{int(time.time())}",
                "project_path": self.project_path
            }
        else:
            return {
                "success": False,
                "error": test_result["error"],
                "session_id": None
            }
    
    def stop_session(self):
        """Stop the cursor-agent session"""
        self.session_active = False
        print("INFO: Native cursor-agent session stopped")
    
    def get_status(self) -> Dict[str, Any]:
        """Get current status"""
        return {
            "available": self.is_available(),
            "session_active": self.session_active,
            "project_path": self.project_path,
            "agent_initialized": self.agent is not None,
            "method": "native",
            "cursor_agent_tools_available": CURSOR_AGENT_TOOLS_AVAILABLE,
            "last_init_error": self._last_init_error
        }

# Global instance for compatibility
native_cursor_agent = CursorAgentNative()

async def send_prompt_to_cursor_agent_native(prompt: str, project_path: str = None) -> bool:
    """
    Send a prompt to cursor-agent using native method
    
    Args:
        prompt: The prompt to send
        project_path: Optional project path
        
    Returns:
        True if successful, False otherwise
    """
    result = await native_cursor_agent.send_prompt(prompt, project_path)
    return result["success"]

async def start_cursor_agent_session_native(project_name: str = None) -> str:
    """
    Start a native cursor-agent session
    
    Args:
        project_name: Optional project name
        
    Returns:
        Status message
    """
    result = await native_cursor_agent.start_session(project_name)
    
    if result["success"]:
        return f"SUCCESS: Native cursor-agent session started for project: {project_name or 'default'}"
    else:
        return f"ERROR: Failed to start native cursor-agent session: {result['error']}"

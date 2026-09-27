"""
Agentic Cursor Implementation
Full multi-stage reasoning with actual tool execution (like Cursor IDE)
"""

import os
import asyncio
from pathlib import Path
from typing import Optional, Dict, Any, List
import anthropic
from dotenv import load_dotenv

class CursorAgentAgentic:
    """Full agentic implementation with tool execution"""
    
    def __init__(self, project_path: str = None):
        # Load environment variables
        load_dotenv()
        
        # Set project path
        if project_path:
            self.project_path = Path(project_path)
        else:
            # Default to repo root (parent of src/), not src/
            script_dir = Path(__file__).resolve().parent  # src/bots
            src_dir = script_dir.parent  # src
            self.project_path = src_dir.parent  # repo root (parent of src/)
            print(f"[Cursor Agent] Calculated project root: {self.project_path}")
        
        print(f"[Cursor Agent] Project path: {self.project_path}")
        
        # Initialize Anthropic client
        api_key = os.getenv('ANTHROPIC_API_KEY')
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY not found in environment")
        
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = "claude-3-haiku-20240307"  # Claude 3 Haiku (most reliable)
        
        # Tool definitions
        self.tools = [
            {
                "name": "read_file",
                "description": "Read the contents of a file",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Path to the file relative to project root"
                        }
                    },
                    "required": ["path"]
                }
            },
            {
                "name": "write_file",
                "description": "Create or overwrite a file with content",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Path to the file relative to project root"
                        },
                        "content": {
                            "type": "string",
                            "description": "Content to write to the file"
                        }
                    },
                    "required": ["path", "content"]
                }
            },
            {
                "name": "list_directory",
                "description": "List files and directories in a path",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Path relative to project root (use '.' for root)"
                        }
                    },
                    "required": ["path"]
                }
            },
            {
                "name": "execute_command",
                "description": "Execute a shell command in the project directory",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "Command to execute"
                        }
                    },
                    "required": ["command"]
                }
            }
        ]
    
    def execute_tool(self, tool_name: str, tool_input: Dict[str, Any]) -> str:
        """Execute a tool and return the result"""
        try:
            if tool_name == "read_file":
                return self._read_file(tool_input["path"])
            elif tool_name == "write_file":
                return self._write_file(tool_input["path"], tool_input["content"])
            elif tool_name == "list_directory":
                return self._list_directory(tool_input["path"])
            elif tool_name == "execute_command":
                return self._execute_command(tool_input["command"])
            else:
                return f"Unknown tool: {tool_name}"
        except Exception as e:
            return f"Error executing {tool_name}: {str(e)}"
    
    def _read_file(self, path: str) -> str:
        """Read a file"""
        file_path = self.project_path / path
        if not file_path.exists():
            return f"File not found: {path}"
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            return f"File contents of {path}:\n\n{content}"
        except Exception as e:
            return f"Error reading file: {str(e)}"
    
    def _write_file(self, path: str, content: str) -> str:
        """Write a file"""
        file_path = self.project_path / path
        
        # Create parent directories if needed
        file_path.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            return f"✅ Successfully wrote {len(content)} characters to {path}"
        except Exception as e:
            return f"Error writing file: {str(e)}"
    
    def _list_directory(self, path: str) -> str:
        """List directory contents"""
        dir_path = self.project_path / path if path != '.' else self.project_path
        
        if not dir_path.exists():
            return f"Directory not found: {path}"
        
        if not dir_path.is_dir():
            return f"Not a directory: {path}"
        
        try:
            items = []
            for item in sorted(dir_path.iterdir()):
                item_type = "📁" if item.is_dir() else "📄"
                items.append(f"{item_type} {item.name}")
            
            return f"Contents of {path}:\n" + "\n".join(items)
        except Exception as e:
            return f"Error listing directory: {str(e)}"
    
    def _execute_command(self, command: str) -> str:
        """Execute a shell command"""
        import subprocess
        
        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=self.project_path,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            output = []
            if result.stdout:
                output.append(f"STDOUT:\n{result.stdout}")
            if result.stderr:
                output.append(f"STDERR:\n{result.stderr}")
            output.append(f"Return code: {result.returncode}")
            
            return "\n".join(output)
        except subprocess.TimeoutExpired:
            return "Command timed out after 30 seconds"
        except Exception as e:
            return f"Error executing command: {str(e)}"
    
    async def chat_streaming(self, prompt: str, max_turns: int = 10):
        """
        Stream the agentic conversation with real-time updates
        
        Yields status updates as the agent works
        """
        messages = [{"role": "user", "content": prompt}]
        
        yield {"type": "status", "message": f"🤖 Starting agentic reasoning (max {max_turns} turns)...\n\n"}
        
        for turn in range(max_turns):
            yield {"type": "status", "message": f"**Turn {turn + 1}/{max_turns}**\n"}
            
            # Call Claude
            yield {"type": "status", "message": "💭 Thinking...\n"}
            response = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                tools=self.tools,
                messages=messages
            )
            
            # Process response
            tool_uses = []
            text_responses = []
            
            for block in response.content:
                if block.type == "text":
                    text_responses.append(block.text)
                    yield {"type": "text", "message": f"{block.text}\n\n"}
                elif block.type == "tool_use":
                    tool_uses.append(block)
                    yield {"type": "tool", "message": f"🔧 Using tool: `{block.name}`\n"}
            
            # If no tool uses, we're done
            if not tool_uses:
                yield {"type": "complete", "message": f"✅ Task completed in {turn + 1} turns!\n"}
                return
            
            # Execute tools
            tool_results = []
            for tool_use in tool_uses:
                yield {"type": "status", "message": f"⚙️ Executing: `{tool_use.name}`...\n"}
                result = self.execute_tool(tool_use.name, tool_use.input)
                
                # Show brief result
                result_preview = result[:100] + "..." if len(result) > 100 else result
                yield {"type": "tool_result", "message": f"✓ Result: {result_preview}\n\n"}
                
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use.id,
                    "content": result
                })
            
            # Add assistant response and tool results to conversation
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})
        
        # Max turns reached
        yield {"type": "complete", "message": f"⚠️ Max turns ({max_turns}) reached. Task may be incomplete.\n"}
    
    async def chat(self, prompt: str, max_turns: int = 10) -> Dict[str, Any]:
        """
        Send a prompt and handle multi-turn agentic conversation with tool use
        
        Args:
            prompt: User's prompt
            max_turns: Maximum number of back-and-forth turns with Claude
            
        Returns:
            Dict with final response and execution log
        """
        messages = [{"role": "user", "content": prompt}]
        execution_log = []
        
        print(f"[Cursor Agent] Starting agentic chat with max {max_turns} turns")
        
        for turn in range(max_turns):
            print(f"[Cursor Agent] Turn {turn + 1}/{max_turns}")
            
            # Call Claude
            response = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                tools=self.tools,
                messages=messages
            )
            
            # Log this turn
            turn_log = {
                "turn": turn + 1,
                "stop_reason": response.stop_reason,
                "content": []
            }
            
            # Process response
            tool_uses = []
            text_responses = []
            
            for block in response.content:
                if block.type == "text":
                    text_responses.append(block.text)
                    turn_log["content"].append({"type": "text", "text": block.text})
                elif block.type == "tool_use":
                    tool_uses.append(block)
                    turn_log["content"].append({
                        "type": "tool_use",
                        "tool": block.name,
                        "input": block.input
                    })
            
            execution_log.append(turn_log)
            
            # If no tool uses, we're done
            if not tool_uses:
                final_text = "\n".join(text_responses) if text_responses else "Task completed."
                print(f"[Cursor Agent] Completed in {turn + 1} turns")
                return {
                    "success": True,
                    "response": final_text,
                    "turns": turn + 1,
                    "execution_log": execution_log
                }
            
            # Execute tools
            tool_results = []
            for tool_use in tool_uses:
                print(f"[Cursor Agent] Executing tool: {tool_use.name}")
                result = self.execute_tool(tool_use.name, tool_use.input)
                print(f"[Cursor Agent] Tool result: {result[:100]}...")
                
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use.id,
                    "content": result
                })
            
            # Add assistant response and tool results to conversation
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})
        
        # Max turns reached
        print(f"[Cursor Agent] Max turns ({max_turns}) reached")
        return {
            "success": True,
            "response": "Task completed (max turns reached). Check execution log for details.",
            "turns": max_turns,
            "execution_log": execution_log,
            "max_turns_reached": True
        }

# Async wrapper for web API
async def send_agentic_prompt(prompt: str, project_path: str = None) -> Dict[str, Any]:
    """Send a prompt to the agentic cursor agent"""
    agent = CursorAgentAgentic(project_path=project_path)
    return await agent.chat(prompt)

# Streaming wrapper
async def send_agentic_prompt_streaming(prompt: str, project_path: str = None):
    """Send a prompt and stream the agentic reasoning process"""
    agent = CursorAgentAgentic(project_path=project_path)
    async for update in agent.chat_streaming(prompt):
        yield update


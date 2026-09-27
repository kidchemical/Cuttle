"""Discord Bot with MCP Integration

This is an updated version of the Discord bot that uses MCP (Model Context Protocol)
for tool integration instead of direct tool imports.
"""

import os
import re
import traceback
import discord
import sys
import subprocess
from dotenv import load_dotenv
import asyncio
import json
import time
from pathlib import Path
import requests

# Ensure the parent directory (src/) is in the Python path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)  # This should be the 'src' directory
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)
    
# Change to parent directory if we're in the bots subdirectory
if os.path.basename(os.getcwd()) == 'bots':
    os.chdir(parent_dir)
    print(f"[INFO] Changed working directory to: {os.getcwd()}")

# Load environment variables
load_dotenv()

# Load OWNER_ID with error handling for invalid values
try:
    OWNER_ID = int(os.getenv("OWNER_ID", "0"))
except ValueError:
    print(f"WARNING: Invalid OWNER_ID value '{os.getenv('OWNER_ID')}'. Using default 0.")
    OWNER_ID = 0

# API base URL — Flask serves HTTPS with a self-signed cert (see web_chat_api.py).
# Plain http:// to :8080 fails with ConnectionError / WinError 10054.
API_BASE = (
    os.getenv("CUTTLE_API_URL")
    or os.getenv("CUTTLE_INTERNAL_API_BASE")
    or "https://127.0.0.1:8080"
).rstrip("/")


def _api_request_kwargs() -> dict:
    """Extra kwargs for requests to the local Cuttle API (skip verify on localhost TLS)."""
    extra: dict = {}
    if API_BASE.startswith("https://"):
        host = API_BASE.split("://", 1)[1].split("/", 1)[0].split(":")[0].lower()
        if host in ("127.0.0.1", "localhost", "::1"):
            extra["verify"] = False
            try:
                import urllib3
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            except Exception:
                pass
    return extra


def _is_dm_channel(message) -> bool:
    """True for DMs (no guild, or Discord DM channel type)."""
    if getattr(message, "guild", None) is None:
        return True
    channel = getattr(message, "channel", None)
    if channel is None:
        return False
    if isinstance(channel, discord.DMChannel):
        return True
    ch_type = getattr(channel, "type", None)
    name = getattr(ch_type, "name", None) if ch_type is not None else None
    return name in ("private", "dm")


def _bot_was_addressed(message, me) -> bool:
    """Mention of the bot, or a reply to one of the bot's messages."""
    if me is None:
        return False
    mentions = getattr(message, "mentions", None) or []
    me_id = getattr(me, "id", None)
    for user in mentions:
        if getattr(user, "id", None) == me_id:
            return True
    ref = getattr(message, "reference", None)
    resolved = getattr(ref, "resolved", None) if ref else None
    author = getattr(resolved, "author", None)
    return bool(author and getattr(author, "id", None) == me_id)


def should_ignore_guild_message(message, me) -> bool:
    """Guild chatter that isn't a mention/reply to the bot — stay silent."""
    if _is_dm_channel(message):
        return False
    return not _bot_was_addressed(message, me)


def strip_bot_mentions(text: str, me) -> str:
    """Remove <@id> / <@!id> tokens for this bot so leftover body is the real prompt."""
    raw = text or ""
    me_id = getattr(me, "id", None) if me is not None else None
    if me_id is None:
        return raw.strip()
    cleaned = re.sub(rf"<@!?{int(me_id)}>", "", raw)
    return cleaned.strip()


def _parse_api_error(response, fallback_label: str = "API") -> str:
    """Parse API error response and return a user-friendly message."""
    try:
        data = response.json()
        err = data.get("error", "")
        resp = data.get("response", "")
        status = response.status_code
        if status == 404:
            return (
                "⚠️ No agent handled that message. Star `/cursor` (or another tentacle) "
                f"in Cuttle chat, or send a slash command. UI: {API_BASE}"
            )
        if status == 503:
            return f"⚠️ {fallback_label} service temporarily unavailable. Please try again."
        if resp:
            return f"⚠️ {resp}"
        if err:
            return f"⚠️ {err}"
    except Exception:
        pass
    return f"⚠️ {fallback_label} unavailable (HTTP {response.status_code}). Please ensure Cuttle is running at {API_BASE}"


# Cuttle-as-MCP-server is retired. Discord is a chat/post surface, not an OS
# automation plane — Feature Updates / Prompt Lab use discord.post + discord_cli.
MCP_AVAILABLE = False

# Import other modules
try:
    from core.config import get_config
except ImportError as e:
    print(f"[ERROR] Failed to import required modules: {e}")
    print(f"[ERROR] Current working directory: {os.getcwd()}")
    print(f"[ERROR] Python path: {sys.path}")
    sys.exit(1)

# Import query tracking
try:
    from api.query_tracker import get_query_tracker, start_query_tracking, finish_query_tracking
except ImportError as e:
    print(f"[WARNING] Query tracking not available: {e}")
    # Provide dummy functions if not available
    def get_query_tracker():
        return None
    def start_query_tracking(user_id, query):
        return None
    def finish_query_tracking(tracker_id, response):
        pass

# Bot configuration
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# Global variables
tool_manager = None
config = get_config()

async def send_long_message(channel, content: str, **kwargs):
    """
    Send a message to Discord, splitting it into chunks if it exceeds 2000 characters.
    Respects Discord's character limit while trying to split at natural boundaries.
    """
    MAX_LENGTH = 2000
    
    if len(content) <= MAX_LENGTH:
        await channel.send(content, **kwargs)
        return
    
    # Split into chunks, trying to break at newlines
    chunks = []
    current_chunk = ""
    
    # Split by lines first
    lines = content.split('\n')
    
    for line in lines:
        # If a single line is too long, we need to split it
        if len(line) > MAX_LENGTH:
            # If we have a current chunk, save it first
            if current_chunk:
                chunks.append(current_chunk)
                current_chunk = ""
            
            # Split the long line into smaller parts
            while len(line) > MAX_LENGTH:
                # Find a good breaking point (space, comma, period)
                break_point = MAX_LENGTH
                for char in [' ', ',', '.', ';', ':', '-']:
                    last_char = line[:MAX_LENGTH].rfind(char)
                    if last_char > MAX_LENGTH - 100:  # Within 100 chars of limit
                        break_point = last_char + 1
                        break
                
                chunks.append(line[:break_point])
                line = line[break_point:]
            
            # Add remaining part of the line
            if line:
                current_chunk = line + '\n'
        else:
            # Check if adding this line would exceed the limit
            if len(current_chunk) + len(line) + 1 > MAX_LENGTH:
                chunks.append(current_chunk)
                current_chunk = line + '\n'
            else:
                current_chunk += line + '\n'
    
    # Add any remaining content
    if current_chunk:
        chunks.append(current_chunk)
    
    # Send all chunks
    for i, chunk in enumerate(chunks):
        chunk = chunk.strip()
        if chunk:
            # Add continuation markers
            if i == 0 and len(chunks) > 1:
                chunk += "\n\n*(continued...)*"
            elif i > 0 and i < len(chunks) - 1:
                chunk = "*(continued)*\n\n" + chunk + "\n\n*(continued...)*"
            elif i > 0:
                chunk = "*(continued)*\n\n" + chunk
            
            await channel.send(chunk, **kwargs)

async def send_to_cursor_ai_with_countdown(message, prompt: str, project_path: str = None):
    """Send prompt to Cursor AI with live countdown updates in Discord"""
    try:
        # Create output directory if it doesn't exist
        output_dir = Path("output/temp")
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Create a request file for the Cursor AI
        timestamp = int(time.time())
        cursor_request = {
            "prompt": prompt,
            "project_path": project_path,
            "timestamp": timestamp,
            "status": "pending",
            "message": prompt
        }
        
        request_file = output_dir / f"cursor_ai_request_{timestamp}.json"
        with open(request_file, 'w', encoding='utf-8') as f:
            json.dump(cursor_request, f, indent=2)
        
        # Send initial thinking message
        thinking_msg = await message.channel.send("[AI] **Cursor AI Agent:** 🤔 *Thinking...* (waiting 5 seconds)")
        
        # Create a countdown file to show processing status
        countdown_file = output_dir / f"cursor_ai_countdown_{timestamp}.json"
        
        # Live countdown with Discord message updates
        for i in range(5, 0, -1):
            countdown_data = {
                "timestamp": timestamp,
                "remaining_seconds": i,
                "status": "processing"
            }
            with open(countdown_file, 'w', encoding='utf-8') as f:
                json.dump(countdown_data, f, indent=2)
            
            # Update Discord message
            await thinking_msg.edit(content=f"[AI] **Cursor AI Agent:** 🤔 *Thinking...* (waiting {i} seconds)")
            await asyncio.sleep(1)
        
        # Send the prompt to Cursor IDE using Windows automation
        success = await send_prompt_to_cursor(prompt)
        
        # Create a response file
        response_file = output_dir / f"cursor_ai_response_{timestamp}.json"
        if success:
            response_data = {
                "timestamp": timestamp,
                "status": "completed",
                "response": f"**Cursor-Agent Response**\n\n**Your request:** \"{prompt}\"\n\n**Status:** [OK] **Successfully processed by cursor-agent!**\n\n**What happened:**\n- Your prompt was sent to cursor-agent CLI via WSL\n- cursor-agent processed your request and executed the task\n- The task has been completed in your project directory\n\n**Cursor-agent is working directly with your files:**\n- Files created/modified in your project\n- Changes are immediately available\n- No need to check Cursor IDE - cursor-agent did the work directly\n\n*The Discord-to-cursor-agent bridge is working perfectly!*",
                "project_path": project_path
            }
        else:
            response_data = {
                "timestamp": timestamp,
                "status": "failed",
                "response": f"**Cursor-Agent Response**\n\n**Your request:** \"{prompt}\"\n\n**Status:** [ERROR] **Failed to process with cursor-agent**\n\n**What happened:**\n- cursor-agent CLI could not process your request\n- This is likely due to WSL not being properly installed or configured\n\n**Setup Required:**\n1. **Install WSL Ubuntu:**\n   - Run: `wsl --install -d Ubuntu`\n   - Restart your computer after installation\n\n2. **Install cursor-agent:**\n   - Open WSL Ubuntu terminal\n   - Run: `curl -fsSL https://cursor.sh/install.sh | sh`\n\n3. **Test the setup:**\n   - Run: `wsl -- cursor-agent --help`\n\n**Alternative:** Use `/cursor-ui \"{prompt}\"` for Cursor IDE UI automation (works without WSL)\n\n*Once WSL and cursor-agent are set up, the Discord-to-cursor-agent bridge will work perfectly!*",
                "project_path": project_path
            }
        
        with open(response_file, 'w', encoding='utf-8') as f:
            json.dump(response_data, f, indent=2)
        
        # Update countdown to show completion
        countdown_data = {
            "timestamp": timestamp,
            "remaining_seconds": 0,
            "status": "completed"
        }
        with open(countdown_file, 'w', encoding='utf-8') as f:
            json.dump(countdown_data, f, indent=2)
        
        # Update Discord message with final response
        await thinking_msg.edit(content=response_data["response"])
        
    except Exception as e:
        error_msg = f"**Error in Cursor AI integration:** {str(e)}"
        await message.channel.send(error_msg)
        print(f"Error in send_to_cursor_ai_with_countdown: {e}")
        traceback.print_exc()

async def send_prompt_to_cursor(prompt: str) -> bool:
    """IDE send-keys retired."""
    _ = prompt
    return False

async def execute_tool_command(app: str, hint: str = None) -> str:
    """OS slash tools retired — Discord is post/read only."""
    _ = (app, hint)
    return (
        "OS tool dispatch is retired. Use `/cursor` (agent CLI) in DMs, or "
        "`discord.post` / `python -m api.discord_cli` for channels."
    )

async def route_command_through_processor(message, command: str, args: str = "") -> str:
    """Route a command through the pipeline trigger system via web API"""
    user_context = {
        "display_name": message.author.display_name,
        "id": message.author.id,
        "is_owner": message.author.id == OWNER_ID,
        "username": message.author.name,
        "discriminator": message.author.discriminator,
        "channel_id": str(message.channel.id),
        "channel_name": message.channel.name if hasattr(message.channel, 'name') else "DM",
        "channel_type": 'dm' if hasattr(message.channel, 'type') and message.channel.type and message.channel.type.name == 'private' else 'text',
        "guild_id": str(message.guild.id) if message.guild else None,
        "guild_name": message.guild.name if message.guild else None
    }
    
    try:
        # Try to execute via web API
        response = requests.post(f'{API_BASE}/api/pipeline-trigger-discord', json={
            'message': message.content,
            'user_context': user_context,
            'session': {
                'user_id': str(message.author.id),
                'channel_id': str(message.channel.id)
            }
        }, timeout=60, **_api_request_kwargs())
        
        if response.ok:
            result = response.json()
            if result.get('success'):
                return result.get('response', 'Command executed successfully')
            else:
                return "⚠️ Pipeline execution failed. Please check the Node Editor."
        else:
            err_msg = _parse_api_error(response, "Web server")
            return err_msg
    except requests.exceptions.ConnectionError:
        return f"⚠️ Web server not running. Please start it first: python src/api/web_chat_api.py\nOr ensure Cuttle is running at {API_BASE}"
    except Exception as e:
        return f"⚠️ Error executing command: {e}"

async def handle_screenshot_command(message, args: str) -> str:
    """Handle screenshot commands using MCP"""
    try:
        if not MCP_AVAILABLE or not tool_manager:
            return (
                "Cuttle no longer runs an in-process MCP tool server. "
                "Use a chat agent, or `python -m api.<module>` for Cuttle-owned ops."
            )
        
        args_lower = args.lower().strip()
        
        if "unity" in args_lower:
            # Take screenshot of Unity window
            result = await tool_manager.take_window_screenshot("Unity")
            if "Error:" not in result:
                # Extract file path from result if available
                if "saved to" in result.lower():
                    return f"✅ {result}"
                else:
                    return f"✅ Screenshot taken: {result}"
            else:
                return f"❌ {result}"
        
        elif "desktop" in args_lower or "screen" in args_lower:
            # Take full screen screenshot
            result = await tool_manager.take_screenshot()
            if "Error:" not in result:
                return f"✅ {result}"
            else:
                return f"❌ {result}"
        
        else:
            # Try to take screenshot of specified window
            result = await tool_manager.take_window_screenshot(args)
            if "Error:" not in result:
                return f"✅ {result}"
            else:
                return f"❌ {result}"
    
    except Exception as e:
        error_msg = f"Error taking screenshot: {e}"
        print(error_msg)
        traceback.print_exc()
        return error_msg

async def handle_process_command(message, args: str) -> str:
    """Handle process management commands using MCP"""
    try:
        if not MCP_AVAILABLE or not tool_manager:
            return (
                "Cuttle no longer runs an in-process MCP tool server. "
                "Use a chat agent, or `python -m api.<module>` for Cuttle-owned ops."
            )
        
        args_lower = args.lower().strip()
        
        if "kill" in args_lower and "unity" in args_lower:
            result = await tool_manager.kill_unity_process()
            return f"🔪 {result}"
        
        elif "list" in args_lower:
            limit = 10
            if "all" in args_lower:
                limit = 50
            
            result = await tool_manager.list_processes(limit=limit)
            return f"📋 **Process List:**\n```\n{result}\n```"
        
        elif "find" in args_lower:
            # Extract process name from args
            process_name = args_lower.replace("find", "").strip()
            if process_name:
                result = await tool_manager.find_process(process_name)
                return f"🔍 **Found processes:**\n```\n{result}\n```"
            else:
                return "❌ Please specify a process name to find"
        
        elif "resources" in args_lower or "usage" in args_lower:
            result = await tool_manager.get_system_resources()
            return f"💻 **System Resources:**\n```\n{result}\n```"
        
        else:
            return "❌ Unknown process command. Available: kill unity, list, find <name>, resources"
    
    except Exception as e:
        error_msg = f"Error handling process command: {e}"
        print(error_msg)
        traceback.print_exc()
        return error_msg

async def handle_window_command(message, args: str) -> str:
    """Handle window management commands using MCP"""
    try:
        if not MCP_AVAILABLE or not tool_manager:
            return (
                "Cuttle no longer runs an in-process MCP tool server. "
                "Use a chat agent, or `python -m api.<module>` for Cuttle-owned ops."
            )
        
        args_lower = args.lower().strip()
        
        if "list" in args_lower:
            result = await tool_manager.list_windows(visible_only=True)
            return f"🪟 **Window List:**\n```\n{result}\n```"
        
        elif "find" in args_lower:
            # Extract window title from args
            window_title = args_lower.replace("find", "").strip()
            if window_title:
                result = await tool_manager.find_window(window_title)
                return f"🔍 **Found window:**\n```\n{result}\n```"
            else:
                return "❌ Please specify a window title to find"
        
        elif "focus" in args_lower or "switch" in args_lower:
            # Extract window title from args
            window_title = args_lower.replace("focus", "").replace("switch", "").strip()
            if window_title:
                result = await tool_manager.switch_to_window(window_title)
                return f"🎯 {result}"
            else:
                return "❌ Please specify a window title to focus"
        
        elif "minimize" in args_lower:
            window_title = args_lower.replace("minimize", "").strip()
            if window_title:
                result = await tool_manager.minimize_window(window_title)
                return f"📉 {result}"
            else:
                return "❌ Please specify a window title to minimize"
        
        elif "maximize" in args_lower:
            window_title = args_lower.replace("maximize", "").strip()
            if window_title:
                result = await tool_manager.maximize_window(window_title)
                return f"📈 {result}"
            else:
                return "❌ Please specify a window title to maximize"
        
        elif "close" in args_lower:
            window_title = args_lower.replace("close", "").strip()
            if window_title:
                result = await tool_manager.close_window(window_title)
                return f"❌ {result}"
            else:
                return "❌ Please specify a window title to close"
        
        else:
            return "❌ Unknown window command. Available: list, find <title>, focus <title>, minimize <title>, maximize <title>, close <title>"
    
    except Exception as e:
        error_msg = f"Error handling window command: {e}"
        print(error_msg)
        traceback.print_exc()
        return error_msg

async def handle_input_command(message, args: str) -> str:
    """Handle input automation commands using MCP"""
    try:
        if not MCP_AVAILABLE or not tool_manager:
            return (
                "Cuttle no longer runs an in-process MCP tool server. "
                "Use a chat agent, or `python -m api.<module>` for Cuttle-owned ops."
            )
        
        args_lower = args.lower().strip()
        
        if "click" in args_lower:
            # Parse click coordinates: "click 100,200" or "click 100,200,right"
            parts = args_lower.replace("click", "").strip().split(",")
            if len(parts) >= 2:
                try:
                    x = int(parts[0].strip())
                    y = int(parts[1].strip())
                    button = parts[2].strip() if len(parts) > 2 else "left"
                    
                    result = await tool_manager.click_at(x, y, button)
                    return f"🖱️ {result}"
                except ValueError:
                    return "❌ Invalid coordinates. Use: click x,y or click x,y,button"
            else:
                return "❌ Please specify coordinates. Use: click x,y"
        
        elif "type" in args_lower:
            # Extract text to type
            text = args_lower.replace("type", "").strip()
            if text:
                result = await tool_manager.type_text(text)
                return f"⌨️ {result}"
            else:
                return "❌ Please specify text to type"
        
        elif "press" in args_lower:
            # Extract key to press
            key = args_lower.replace("press", "").strip()
            if key:
                result = await tool_manager.press_key(key)
                return f"🔘 {result}"
            else:
                return "❌ Please specify a key to press"
        
        elif "send" in args_lower:
            # Extract text to send
            text = args_lower.replace("send", "").strip()
            if text:
                result = await tool_manager.send_keys(text)
                return f"📤 {result}"
            else:
                return "❌ Please specify text to send"
        
        else:
            return "❌ Unknown input command. Available: click x,y, type <text>, press <key>, send <text>"
    
    except Exception as e:
        error_msg = f"Error handling input command: {e}"
        print(error_msg)
        traceback.print_exc()
        return error_msg

async def handle_system_command(message, args: str) -> str:
    """Handle system information commands using MCP"""
    try:
        if not MCP_AVAILABLE or not tool_manager:
            return (
                "Cuttle no longer runs an in-process MCP tool server. "
                "Use a chat agent, or `python -m api.<module>` for Cuttle-owned ops."
            )
        
        args_lower = args.lower().strip()
        
        if "info" in args_lower:
            result = await tool_manager.get_system_info()
            return f"💻 **System Information:**\n```\n{result}\n```"
        
        elif "disk" in args_lower or "storage" in args_lower:
            result = await tool_manager.get_disk_usage()
            return f"💾 **Disk Usage:**\n```\n{result}\n```"
        
        elif "network" in args_lower:
            result = await tool_manager.get_network_info()
            return f"🌐 **Network Information:**\n```\n{result}\n```"
        
        elif "uptime" in args_lower:
            result = await tool_manager.get_system_uptime()
            return f"⏰ **System Uptime:**\n```\n{result}\n```"
        
        elif "services" in args_lower:
            result = await tool_manager.get_running_services()
            return f"🔧 **Running Services:**\n```\n{result}\n```"
        
        else:
            return "❌ Unknown system command. Available: info, disk, network, uptime, services"
    
    except Exception as e:
        error_msg = f"Error handling system command: {e}"
        print(error_msg)
        traceback.print_exc()
        return error_msg

def _write_discord_status(connected: bool):
    """Write Discord connection status for /api/bot-status."""
    try:
        status_dir = Path.home() / "cuttle_logs"
        status_dir.mkdir(parents=True, exist_ok=True)
        status_path = status_dir / "discord_status.json"
        status_path.write_text(
            json.dumps({"connected": connected, "timestamp": time.time()}),
            encoding="utf-8",
        )
    except Exception as e:
        print(f"[WARNING] Could not write discord status: {e}")


@client.event
async def on_ready():
    """Bot ready event"""
    print(f'{client.user} has connected to Discord!')
    _write_discord_status(True)
    
    # Cuttle MCP server is retired — no subprocess. OS slash tools are retired too.
    print("[INFO] Discord bot ready (posts/reads via discord.post + discord_cli; no OS slash tools)")


@client.event
async def on_disconnect():
    """Bot disconnected from Discord."""
    _write_discord_status(False)


@client.event
async def on_message(message):
    """Handle incoming messages"""
    if message.author == client.user:
        return
    
    try:
        # Handle slash commands
        if message.content.startswith('/'):
            await handle_slash_command(message)
        else:
            # Handle regular messages with AI processing
            await handle_regular_message(message)
    
    except Exception as e:
        error_msg = f"Error processing message: {e}"
        print(error_msg)
        traceback.print_exc()
        await message.channel.send(f"❌ {error_msg}")
        
        # Finish query tracking with error
        try:
            finish_query_tracking(success=False, error_message=error_msg)
        except:
            pass  # Don't let query tracking errors break the error handling

async def clear_channel_messages(message):
    """Clear all messages in the current channel or DM"""
    try:
        # Check if user is the bot owner
        if message.author.id != OWNER_ID:
            await message.channel.send("❌ **Permission denied.** Only the bot owner can use this command.")
            return
        
        # Send confirmation message
        confirm_msg = await message.channel.send("🗑️ **Clearing all messages...** This may take a moment.")
        
        deleted_count = 0
        failed_count = 0
        
        # Delete messages in batches with proper rate limiting
        batch_size = 5  # Delete 5 messages at a time
        batch_delay = 2  # Wait 2 seconds between batches
        
        message_batch = []
        async for msg in message.channel.history(limit=None):
            try:
                # Don't delete messages older than 14 days (Discord limitation)
                if (message.created_at - msg.created_at).days > 14:
                    print(f"Skipping message older than 14 days: {msg.id}")
                    continue
                
                message_batch.append(msg)
                
                # Process batch when it reaches batch_size
                if len(message_batch) >= batch_size:
                    # Delete all messages in the batch concurrently
                    delete_tasks = [msg.delete() for msg in message_batch]
                    results = await asyncio.gather(*delete_tasks, return_exceptions=True)
                    
                    # Count successful deletions
                    for i, result in enumerate(results):
                        if isinstance(result, Exception):
                            print(f"Failed to delete message {message_batch[i].id}: {result}")
                            failed_count += 1
                        else:
                            deleted_count += 1
                    
                    # Clear the batch and wait
                    message_batch = []
                    await asyncio.sleep(batch_delay)
                    
                    # Update progress every 25 messages
                    if deleted_count % 25 == 0:
                        await confirm_msg.edit(content=f"🗑️ **Clearing messages...** Deleted: {deleted_count}, Failed: {failed_count}")
                    
            except Exception as e:
                print(f"Error processing message {msg.id}: {e}")
                failed_count += 1
                continue
        
        # Process remaining messages in the final batch
        if message_batch:
            delete_tasks = [msg.delete() for msg in message_batch]
            results = await asyncio.gather(*delete_tasks, return_exceptions=True)
            
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    print(f"Failed to delete message {message_batch[i].id}: {result}")
                    failed_count += 1
                else:
                    deleted_count += 1
        
        # Update confirmation message
        if failed_count > 0:
            await confirm_msg.edit(content=f"✅ **Channel cleared!**\n\n**Deleted:** {deleted_count} messages\n**Failed:** {failed_count} messages\n\n*Some messages may be too old to delete (Discord 14-day limit).*")
        else:
            await confirm_msg.edit(content=f"✅ **Channel cleared!**\n\n**Deleted:** {deleted_count} messages")
        
        # Delete the confirmation message after 5 seconds
        await asyncio.sleep(5)
        try:
            await confirm_msg.delete()
        except:
            pass
            
    except Exception as e:
        error_msg = f"Error clearing channel: {e}"
        print(error_msg)
        traceback.print_exc()
        await message.channel.send(f"❌ {error_msg}")

async def handle_claude_command(message, prompt: str):
    """Handle Claude Code commands"""
    try:
        # Import required modules
        from scripts.utilities.claude_code_tool import ClaudeCodeTool
        from api.query_tracker import start_query_tracking, finish_query_tracking
        from api.query_tracker import track_tool_call as _track_tool_call
        import time
        
        # Start query tracking for /claude commands
        # Create user context for /claude command
        claude_context = {
            'display_name': message.author.display_name,
            'id': message.author.id,
            'is_owner': message.author.id == OWNER_ID,
            'username': message.author.name,
            'discriminator': getattr(message.author, 'discriminator', '0'),
            'channel_id': str(message.channel.id),
            'channel_name': getattr(message.channel, 'name', 'DM'),
            'channel_type': 'dm' if hasattr(message.channel, 'type') and message.channel.type and message.channel.type.name == 'private' else 'text',
            'guild_id': str(message.guild.id) if message.guild else None,
            'guild_name': message.guild.name if message.guild else None,
            'command_type': 'claude',
            'prompt': prompt
        }
        
        query_id = start_query_tracking(
            user_input=f"/claude \"{prompt}\"",
            user_context=claude_context
        )
        print(f"[QUERY] Started tracking query {query_id} for: /claude \"{prompt}\"...")
        
        # Send initial thinking message
        thinking_msg = await message.channel.send("🤔 *Claude is thinking...*")
        
        # Use session ID based on user ID for consistency within user sessions
        user_hash = str(hash(str(message.author.id)))[-6:]  # Last 6 digits of hash
        session_id = f"{user_hash}_{int(time.time() // 3600)}"  # New session every hour per user
        
        tool_start_time = time.time()
        # Create Claude tool instance with cheaper Haiku model
        claude_tool = ClaudeCodeTool(session_id=session_id, model="haiku")
        
        # Execute the Claude command
        result = await claude_tool.execute_claude_command(prompt, "sandbox")
        
        # Extract usage information for tracking
        usage_info = result.get("usage_info", {})
        model_name = usage_info.get("model", "haiku")
        tokens_dict = None
        cost = 0.0
        
        if usage_info and usage_info.get("total_tokens", 0) > 0:
            tokens_dict = {
                "total_tokens": usage_info.get("total_tokens", 0),
                "prompt_tokens": usage_info.get("input_tokens", 0),
                "completion_tokens": usage_info.get("output_tokens", 0)
            }
            cost = usage_info.get("cost", 0.0)
        
        # Format model name consistently - only add prefix if not already present
        if not model_name.startswith("claude-"):
            formatted_model = f"claude-3-{model_name}"
        else:
            formatted_model = model_name
        
        # Track the tool call with token information
        _track_tool_call(
            tool_name="claude_code",
            parameters={"prompt": prompt, "project": "sandbox", "session_id": session_id},
            start_time=tool_start_time,
            success=result["success"],
            result=result["output"] if result["success"] else result["error"],
            model=formatted_model,
            tokens=tokens_dict,
            cost=cost
        )
        
        # Format and send the response
        response = claude_tool.format_response(result)
        await thinking_msg.edit(content=response)
        
        # Finish query tracking and generate report
        report_path, json_path = finish_query_tracking(success=result["success"])
        if report_path:
            # Get the filenames and create localhost URLs
            report_filename = os.path.basename(report_path)
            json_filename = os.path.basename(json_path)
            report_url = f"{API_BASE}/logs/{report_filename}"
            json_url = f"{API_BASE}/logs/{json_filename}"
            print(f"[QUERY] Query Report: {report_url}")
            print(f"[QUERY] Query Data: {json_url}")
            print(f"[QUERY] View query log: {API_BASE}/query_log.html")
        
    except ImportError as e:
        await message.channel.send(f"❌ **Claude Code tool not available.** Error: {e}")
        # Finish query tracking with error
        try:
            finish_query_tracking(success=False, error_message=f"Import error: {e}")
        except:
            pass
    except Exception as e:
        error_msg = f"Error executing Claude command: {e}"
        print(error_msg)
        traceback.print_exc()
        await message.channel.send(f"❌ {error_msg}")
        
        # Track failed tool call (no tokens on failure)
        try:
            _track_tool_call(
                tool_name="claude_code",
                parameters={"prompt": prompt, "project": "sandbox"},
                start_time=tool_start_time,
                success=False,
                result=str(e),
                model=None,
                tokens=None,
                cost=0.0
            )
        except:
            pass
        
        # Finish query tracking with error
        try:
            finish_query_tracking(success=False, error_message=str(e))
        except:
            pass

_OS_SLASH_RETIRED = (
    "Discord OS slash tools (`/screenshot`, `/unity`, send-keys, …) are retired. "
    "Post Feature Updates / Prompt Lab with a `discord.post` confirm card; "
    "read channels with `python -m api.discord_cli`. See `.cuttle/docs/discord.md`."
)


async def handle_slash_command(message):
    """Handle slash commands"""
    content = message.content[1:]  # Remove the '/'
    parts = content.split(' ', 1)
    command = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""
    
    # Debug logging
    print(f"[DEBUG] Slash command received: '{message.content}'")
    print(f"[DEBUG] Parsed command: '{command}'")
    print(f"[DEBUG] Parsed args: '{args}'")
    
    if command in ("screenshot", "process", "window", "input", "system", "unity", "open"):
        await message.channel.send(_OS_SLASH_RETIRED)
        return

    if command == "screenshot":
        result = await handle_screenshot_command(message, args)
        await message.channel.send(result)
    
    elif command == "process":
        # Route through multi-stage processor for proper query tracking
        result = await route_command_through_processor(message, command, args)
        await message.channel.send(result)
    
    elif command == "window":
        # Route through multi-stage processor for proper query tracking
        result = await route_command_through_processor(message, command, args)
        await message.channel.send(result)
    
    elif command == "input":
        # Route through multi-stage processor for proper query tracking
        result = await route_command_through_processor(message, command, args)
        await message.channel.send(result)
    
    elif command == "system":
        # Route through multi-stage processor for proper query tracking
        result = await route_command_through_processor(message, command, args)
        await message.channel.send(result)
    
    elif command == "tools":
        if MCP_AVAILABLE and tool_manager:
            tools = await tool_manager.get_available_tools()
            tools_list = "\n".join([f"• {tool}" for tool in tools[:20]])  # Show first 20 tools
            result = f"🛠️ **Available MCP Tools ({len(tools)}):**\n{tools_list}"
            if len(tools) > 20:
                result += f"\n... and {len(tools) - 20} more tools"
        else:
            result = (
                "Cuttle MCP tool packs are retired. Agents should use "
                "`python -m api.*` (chat_cli, panes_cli, discord_cli, …) — "
                "see `.cuttle/docs/agent-ops-cli.md`."
            )
        await message.channel.send(result)
    
    elif command == "cursor":
        # Strip surrounding quotes from args if present
        cleaned_args = args.strip().strip('"').strip("'")
        if not cleaned_args:
            await message.channel.send("❌ Please provide a prompt after /cursor. Example: `/cursor \"Hello World\"`")
            return
        
        # Use the agentic cursor agent (same as web chat)
        try:
            from scripts.utilities.cursor_cli_tool import _cursor_agent_oneline_prompt

            thinking_msg = await message.channel.send("🤔 *Cursor Agent is thinking...*")
            result = _cursor_agent_oneline_prompt(cleaned_args)
            if result and not str(result).startswith("[FAIL]"):
                response = f"**Cursor Agent**\n\n{result}"
            else:
                response = f"❌ **Cursor Agent**\n\n{result or 'No output'}"
            await thinking_msg.edit(content=response[:1900])
        except Exception as e:
            await message.channel.send(f"❌ **Error executing Cursor command:** {str(e)}")
    
    elif command == "open":
        # Route through multi-stage processor for proper query tracking
        result = await route_command_through_processor(message, command, args)
        await message.channel.send(result)
    
    elif command == "unity":
        # Route through multi-stage processor for proper query tracking
        result = await route_command_through_processor(message, command, args)
        await message.channel.send(result)
    
    elif command == "clear" and args == "--force":
        # Use multi-stage processor for query tracking, but handle clear command specially
        user_context = {
            "display_name": message.author.display_name,
            "id": message.author.id,
            "is_owner": message.author.id == OWNER_ID,
            "username": message.author.name,
            "discriminator": message.author.discriminator,
            "channel_id": str(message.channel.id),
            "channel_name": message.channel.name if hasattr(message.channel, 'name') else "DM",
            "channel_type": 'dm' if hasattr(message.channel, 'type') and message.channel.type and message.channel.type.name == 'private' else 'text',
            "guild_id": str(message.guild.id) if message.guild else None,
            "guild_name": message.guild.name if message.guild else None
        }
        
        # Start query tracking
        query_id = start_query_tracking(message.content, user_context)
        print(f"[QUERY] Started tracking query {query_id} for: {message.content[:50]}...")
        
        try:
            # Execute the clear command
            await clear_channel_messages(message)
            
            # Finish query tracking
            finish_query_tracking(success=True)
            print(f"[QUERY] Finished tracking query {query_id} successfully")
        except Exception as e:
            # Finish query tracking with error
            finish_query_tracking(success=False, error_message=str(e))
            print(f"[QUERY] Finished tracking query {query_id} with error: {e}")
            raise
    
    elif command == "claude":
        if args:
            # Strip surrounding quotes from args if present
            cleaned_args = args.strip().strip('"').strip("'")
            await handle_claude_command(message, cleaned_args)
        else:
            await message.channel.send("❌ Please provide a prompt for Claude. Example: `/claude create hello world file`")
    
    elif command == "help":
        # Start query tracking for help command
        user_context = {
            "display_name": message.author.display_name,
            "id": message.author.id,
            "is_owner": message.author.id == OWNER_ID,
            "username": message.author.name,
            "discriminator": message.author.discriminator,
            "channel_id": str(message.channel.id),
            "channel_name": message.channel.name if hasattr(message.channel, 'name') else "DM",
            "channel_type": 'dm' if hasattr(message.channel, 'type') and message.channel.type and message.channel.type.name == 'private' else 'text',
            "guild_id": str(message.guild.id) if message.guild else None,
            "guild_name": message.guild.name if message.guild else None
        }
        
        query_id = start_query_tracking(message.content, user_context)
        print(f"[QUERY] Started tracking query {query_id} for: {message.content[:50]}...")
        
        # Track regex stage (command parsing)
        from api.query_tracker import get_query_tracker
        tracker = get_query_tracker()
        if tracker.query_id:
            import time
            regex_start = time.time()
            tracker.add_execution_stage(
                "🔍 Regex Pattern Matching",
                "regex",
                regex_start,
                regex_start + 0.001,
                success=True,
                details={
                    "pattern": "help",
                    "matched": True,
                    "command": "help",
                    "args": ""
                }
            )
        
        # Track tool execution stage
        tool_start = time.time()
        if tracker.query_id:
            tracker.add_execution_stage(
                "📋 Help Command Execution",
                "tool",
                tool_start,
                tool_start + 0.001,  # Will be updated after execution
                success=True,
                details={
                    "tool": "help_command",
                    "command": "help",
                    "args": ""
                }
            )
        
        help_text = """
🦑 **Cuttle Discord bot**

Discord here is for **channel posts** (Feature Updates) and **reading** (Prompt Lab).

• Post: end an agent reply with a `discord.post` confirm card (see `.cuttle/docs/discord.md`)
• Read: `python -m api.discord_cli messages <alias> --project <path>`
• Chat in DMs still routes to Cuttle web chat / starred agents

**Bot**
• `/cursor <prompt>` — Cursor Agent CLI (`agent -p`)
• `/claude <prompt>` — Claude Code CLI
• `/clear --force` — owner: clear channel messages
• `/help` — this text

OS slash tools (`/screenshot`, `/unity`, send-keys) and the Cuttle MCP tool server are retired.
        """
        await message.channel.send(help_text)
        
        # Update tool execution end time
        tool_end = time.time()
        if tracker.query_id and tracker.execution_data and tracker.execution_data.get("execution_stages"):
            # Find the tool execution stage and update its end time
            for stage in tracker.execution_data["execution_stages"]:
                if stage.get("name") == "📋 Help Command Execution":
                    stage["end_time"] = tool_end
                    stage["duration"] = tool_end - tool_start
                    break
        
        # Finish query tracking
        finish_query_tracking(success=True)
    
    else:
        await message.channel.send(f"❌ Unknown command: {command}")

async def handle_regular_message(message):
    """Handle regular messages with AI processing via pipeline system.

    Guild channels: only respond when the bot is @mentioned or replied to.
    DMs: always process. Stay silent on empty / ops noise (no Flask spam).
    """
    try:
        me = client.user
        if should_ignore_guild_message(message, me):
            return

        # Create user context dictionary from Discord message
        user_context = {
            'name': message.author.display_name,
            'display_name': message.author.display_name,
            'id': str(message.author.id),
            'is_owner': message.author.id == OWNER_ID,
            'channel': message.channel.name if hasattr(message.channel, 'name') else 'DM',
            'channel_name': message.channel.name if hasattr(message.channel, 'name') else 'DM',
            'channel_id': str(message.channel.id),
            'channel_type': 'dm' if isinstance(message.channel, discord.DMChannel) else 'text',
            'guild': message.guild.name if message.guild else None,
            'guild_id': str(message.guild.id) if message.guild else None
        }

        # Collect image/PDF attachments for server-side vision pre-pass
        attachment_payload = []
        for att in (message.attachments or []):
            fname = att.filename or 'file'
            ct = (att.content_type or '') or ''
            lower = fname.lower()
            is_image = ct.startswith('image/') or lower.endswith(
                ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp')
            )
            is_pdf = ct == 'application/pdf' or lower.endswith('.pdf')
            if not (is_image or is_pdf):
                continue
            entry = {
                'filename': fname,
                'mime': ct,
            }
            try:
                raw = await att.read()
                from pathlib import Path as _Path
                tmp_dir = _Path(__file__).resolve().parents[1] / 'output' / 'uploads' / 'discord'
                tmp_dir.mkdir(parents=True, exist_ok=True)
                safe = ''.join(c if c.isalnum() or c in '._-' else '_' for c in fname) or 'file'
                tmp_path = tmp_dir / f"{int(time.time())}_{safe}"
                tmp_path.write_bytes(raw)
                entry['path'] = str(tmp_path)
            except Exception as e:
                print(f"[DISCORD] failed to download attachment {fname}: {e}")
                # Fall back to CDN URL for images (Claude can fetch some URLs)
                if is_image and att.url:
                    entry['url'] = att.url
                else:
                    continue
            attachment_payload.append(entry)

        msg_text = strip_bot_mentions(message.content or '', me)
        if not msg_text and not attachment_payload:
            # Mention-only / empty — don't hit the API (avoids "Empty message" spam).
            return

        in_dm = _is_dm_channel(message)

        try:
            # Try to execute via web API
            payload = {
                'message': msg_text,
                'user_context': user_context,
                'session': {
                    'user_id': str(message.author.id),
                    'channel_id': str(message.channel.id)
                }
            }
            if attachment_payload:
                payload['attachments'] = attachment_payload
            response = requests.post(
                f'{API_BASE}/api/pipeline-trigger-discord',
                json=payload,
                timeout=300,
                **_api_request_kwargs(),
            )

            if response.ok:
                result = response.json()
                if result.get('success'):
                    response_text = result.get('response', '')
                    if response_text:
                        await send_long_message(message.channel, response_text)
                else:
                    error = result.get('error', 'Unknown error')
                    print(f"[DISCORD] Pipeline execution failed: {error}")
                    if in_dm:
                        help_msg = (
                            "👋 Hi — I'm Cuttle. Pick a slash agent in the web app "
                            f"(`{API_BASE}`) or send `/cursor` here. Graphs/Node Editor are gone."
                        )
                        await send_long_message(message.channel, help_msg)
            else:
                # Don't spam guild channels with ops errors (Empty message, Flask down, etc.).
                err_body = ""
                try:
                    err_body = str((response.json() or {}).get("error") or "")
                except Exception:
                    pass
                if err_body.strip().lower() in ("empty message", "no message provided"):
                    print(f"[DISCORD] Ignoring empty-message API error in channel {message.channel.id}")
                    return
                if in_dm:
                    err_msg = _parse_api_error(response, "Web server")
                    await message.channel.send(err_msg)
                else:
                    print(
                        f"[DISCORD] API error {response.status_code} "
                        f"(silent in guild): {err_body or response.text[:200]}"
                    )

        except requests.exceptions.ConnectionError:
            print(f"[DISCORD] Flask unreachable at {API_BASE}")
            if in_dm:
                await message.channel.send(
                    f"⚠️ **Web server not running!**\n\n"
                    f"To start the server, run Cuttle or:\n"
                    f"```\npython src/api/web_chat_api.py\n```\n"
                    f"Ensure the server is available at {API_BASE}"
                )
        except requests.exceptions.Timeout:
            if in_dm:
                await message.channel.send("⚠️ That turn timed out. Please try again.")
            else:
                print("[DISCORD] Pipeline timeout (silent in guild)")

    except Exception as e:
        error_msg = f"Error processing message: {e}"
        print(error_msg)
        traceback.print_exc()
        # Avoid exception spam in public guild channels
        if _is_dm_channel(message):
            await message.channel.send(f"❌ {error_msg}")

async def cleanup():
    """Cleanup resources on shutdown"""
    return


def _wait_for_discord_dns(
    *, host: str = "discord.com", attempts: int = 24, delay: float = 5.0
) -> bool:
    """Block until discord.com resolves, or give up after ~2 minutes.

    At boot the PC's DNS/LAN is often not ready yet. Without this wait the bot
    fails login immediately, exits, and the daemon thrash-restarts it every 5s.
    """
    import socket

    for i in range(1, attempts + 1):
        try:
            socket.getaddrinfo(host, 443)
            if i > 1:
                print(f"[INFO] DNS for {host} ready after {i} attempt(s)")
            return True
        except socket.gaierror as e:
            print(f"[INFO] Waiting for DNS ({host}): {e} ({i}/{attempts})")
            time.sleep(delay)
    return False


def main():
    """Main entry point"""
    try:
        _write_discord_status(False)  # Mark offline until on_ready
        # Get Discord token
        token = os.getenv('DISCORD_TOKEN')
        if not token:
            print("Error: DISCORD_TOKEN not found in environment variables")
            sys.exit(1)

        if not _wait_for_discord_dns():
            print("Error: DNS for discord.com never became available")
            sys.exit(1)

        # Run the bot
        client.run(token)

    except KeyboardInterrupt:
        print("\n[INFO] Bot shutting down...")
        asyncio.run(cleanup())
    except Exception as e:
        print(f"Error running bot: {e}")
        traceback.print_exc()
        try:
            asyncio.run(cleanup())
        except Exception:
            pass
        # Non-zero so the daemon log is not "exited (code 0)" on DNS/login failure.
        sys.exit(1)

if __name__ == "__main__":
    main()

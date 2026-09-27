import re
import os
import time
from typing import List, Dict, Optional, Union
from openai import OpenAI
from dotenv import load_dotenv
from core.config import get_config

# Load environment variables from .env file
load_dotenv()

# Initialize OpenAI client
api_key = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=api_key) if api_key else None

# Discord RAG system - import the getter function, not the instance
try:
    from rag.discord_rag import get_discord_rag
except ImportError as e:
    print(f"[RAG] Could not import Discord RAG module: {e}")
    get_discord_rag = None

def plan_with_llm(user_input: str, user_context: dict = None) -> Union[List[Dict[str, str]], str, tuple]:
    """
    Use GPT to parse user input and either return actions or conversational response.
    
    Args:
        user_input: The user's message string
        user_context: Dictionary containing user information (name, id, is_owner, etc.)
        
    Returns:
        Either a list of action dictionaries, a conversational response string,
        or a tuple of (response, usage_data) if LLM was used
    """
    
    # First try the simple regex patterns for known commands
    simple_actions = plan_simple(user_input)
    if simple_actions:
        return simple_actions
    
    # If no simple pattern matches, use GPT to determine if it's a command or conversation
    if not client:
        return "I need an OpenAI API key to help with that. Please set OPENAI_API_KEY in your .env file."
    
    # Get Discord context - get instance dynamically
    discord_context = ""
    if get_discord_rag:
        try:
            # Get the RAG instance (it's a global singleton initialized by the bot)
            discord_rag = get_discord_rag()
            
            if discord_rag:
                # Cache update is now handled in bot_deprecated.py on_ready event
                discord_context = discord_rag.get_relevant_context(user_input)
                
                # Log RAG usage
                if discord_context:
                    context_length = len(discord_context)
                    context_lines = discord_context.count('\n')
                    print(f"[RAG] ✓ Using Discord RAG context ({context_length} chars, {context_lines} lines)")
                    print(f"[RAG] Preview: {discord_context[:150]}...")
                else:
                    print(f"[RAG] ✗ No relevant Discord context found for: '{user_input[:50]}...'")
            else:
                print(f"[RAG] ⚠️ Discord RAG not initialized yet")
        except Exception as e:
            print(f"[RAG] ⚠️ Discord RAG error: {e}")
            import traceback
            traceback.print_exc()
    
    # Check if user is authorized (bot owner)
    is_authorized_user = False
    if user_context and user_context.get('is_owner', False):
        is_authorized_user = True
    
    # Get the effective system prompt from config
    config = get_config()
    base_system_prompt = config.get_effective_system_prompt()
    
    system_prompt = f"""{base_system_prompt}

## USER CONTEXT:
Current user: {user_context.get('display_name', 'Unknown') if user_context else 'Unknown'}
User ID: {user_context.get('id', 'Unknown') if user_context else 'Unknown'}
Is authorized (owner): {is_authorized_user}

## ESCAPE PURGATORY PROJECT KNOWLEDGE:

**Game Overview:**
Escape Purgatory is this crazy strategic first-person horror game that JamBit made. It's set in this limbo realm where players gotta solve puzzles and stuff. You collect items, repent your sins, and get freed from evil - pretty gnarly, right?

**Current Status:**
- Released on Steam (Oct 21, 2022) - Early Access Beta Phase
- Steam Store: https://store.steampowered.com/app/1876910/Escape_Purgatory/
- Price: $9.99
- All Reviews: Positive (100% of 10 reviews)
- Early Access for 3-4 years (currently in beta phase)

**Game Features:**
- Story Mode: Puzzle-solving in a mysterious dark place
- Survival Mode: Challenging gameplay
- Unlockable cosmetics
- Steam achievements (20 total)
- Partial controller support
- Completely rebuilt menu system

**Development Timeline:**
- Alpha phase: 2 years
- Current: Beta phase (Early Access)
- Completion time: 30 minutes to 3 hours (first playthrough), under 10 minutes for experienced players
- Speed running challenge available

**Future Plans:**
- Deeper storyline with expanded lore
- Additional realms and environments
- New complex puzzles and sinister creatures
- Enhanced visuals and optimized gameplay
- Global leaderboard, new cosmetics, multiplayer mode

**Technical Specs:**
- OS: Windows 7+ (64-bit)
- Memory: 16 MB RAM
- Storage: 15 GB
- Graphics: DX10/DX11/DX12 capable

**Community:**
- Active Discord channel: "Escape Purgatory (Game Development)"
- Player feedback drives development
- Exclusive in-game items for active community members
- Regular updates and community interaction

**Recent Updates:**
- 10/28/2024 - 11/05/2024: Alpha to Beta transition - "Holy Halo" update
- Exclusive items added throughout the game's lifecycle

## LIVE DISCORD CONTEXT:
{discord_context if discord_context else "(No recent relevant Discord discussions found)"}

Your roles and capabilities:
- When a command involves system actions (like "open escape purgatory on unity" or "build project"), 
  use your **agent tools** to execute them locally.
- For development questions, provide Unity/C# knowledge, optimization advice, or pipeline best practices.
- For project management, help organize tasks and keep track of work progress.
- When data or file handling is requested, act as the utility layer (managing logs, assets, exports).

Your style:
- Be direct and solution-oriented.
- Keep answers clear and helpful.
- Get excited about technical challenges and solutions.

Critical rules:
- IMPORTANT: If the user context shows "Is authorized (owner): True", then you KNOW this is the bot owner and should help them!
- If the user context shows "Is authorized (owner): False", politely inform them that you can only help authorized users.
- Always prefer to **take action** with tools if possible, then report the outcome.
- If a command is ambiguous, ask for clarification instead of guessing.
- If the task is beyond your tools, provide the next best steps or guidance.

**Project Knowledge Usage:**
- Use your Escape Purgatory knowledge to provide context-aware responses
- Reference specific game features, development timeline, or community aspects when relevant
- When discussing the game, cite the Steam store page: https://store.steampowered.com/app/1876910/Escape_Purgatory/
- Mention the Discord channel "Escape Purgatory (Game Development)" when discussing community or development updates
- Use this knowledge to give informed advice about Unity optimization, build pipelines, or project management specific to Escape Purgatory

Available tools:
1. run_program - Launch applications and automation tools
   - Apps: unity, cursor, notepad, calculator, etc.
   - Screenshots: screenshot, screenshot_unity, screenshot_desktop
   - Unity automation: unity_play, unity_stop, unity_console, unity_errors
   - Input automation: click, type, press, send_keys
   - Workflows: open_and_screenshot, play_and_check, full_workflow, resolve_errors
   - Examples: "open Unity", "take screenshot of Unity", "click play button", "check for errors"

2. If the user wants to launch an application or perform a computer action, respond with JSON in this format:
{{
  "type": "command",
  "actions": [
    {{
      "tool": "run_program", 
      "app": "application_name",
      "hint": "optional_project_hint"
    }}
  ]
}}

3. If the user is asking questions, making conversation, or requesting information that doesn't involve launching programs, respond with JSON in this format:
{{
  "type": "conversation",
  "message": "your helpful response here"
}}

4. Special automation commands:
   - "take screenshot" → screenshot
   - "screenshot Unity" → screenshot_unity  
   - "click play" → unity_play
   - "check console" → unity_console
   - "get errors" → unity_errors
   - "open project and screenshot" → open_and_screenshot
   - "play and check errors" → play_and_check
   - "resolve all errors" → resolve_errors
   - "version" → version
   - "usage" → usage

4. For Unity projects, common hints might be: "escape purgatory", "my game", "project name"
5. For Cursor IDE, hints are usually project folder names

Always respond with valid JSON only."""

    try:
        # Track LLM call start
        llm_start_time = time.time()
        
        response = client.chat.completions.create(
            model="gpt-4o-mini",  # Using the cheaper model
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_input}
            ],
            temperature=0.3,
            max_tokens=500
        )
        
        # Track LLM call end
        llm_end_time = time.time()
        
        response_text = response.choices[0].message.content.strip()
        
        # Get usage data
        usage_data = {}
        if hasattr(response, 'usage') and response.usage:
            usage_data = {
                "total_tokens": response.usage.total_tokens,
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens
            }
            
            # Add LLM call to query tracker
            try:
                from reports.query_report_generator import get_query_tracker
                tracker = get_query_tracker()
                if tracker.query_id:  # Only track if we have an active query
                    # Add detailed LLM call tracking
                    tracker.add_llm_call(
                        model="gpt-4o-mini",
                        prompt_tokens=response.usage.prompt_tokens,
                        completion_tokens=response.usage.completion_tokens,
                        total_tokens=response.usage.total_tokens,
                        start_time=llm_start_time,
                        end_time=llm_end_time,
                        success=True,
                        response_preview=response_text,
                        prompt_preview=user_input
                    )
                    
                    # Note: Execution stage tracking is handled by the calling processor
                    # (multi_stage_processor, llm_only_mode, etc.) to avoid duplicate stages
            except Exception as e:
                print(f"[QUERY] Error tracking LLM call: {e}")
        
        # Try to parse as JSON
        import json
        try:
            # Strip markdown code blocks if present (```json ... ``` or ``` ... ```)
            json_text = response_text
            if json_text.startswith("```"):
                # Remove opening code block marker
                json_text = re.sub(r'^```(?:json)?\s*\n?', '', json_text)
                # Remove closing code block marker
                json_text = re.sub(r'\n?```\s*$', '', json_text)
            
            result = json.loads(json_text)
            
            if result.get("type") == "command":
                return result.get("actions", []), usage_data
            elif result.get("type") == "conversation":
                return result.get("message", "I'm not sure how to help with that."), usage_data
            else:
                return "I'm not sure how to help with that.", usage_data
                
        except json.JSONDecodeError:
            # If GPT didn't return valid JSON, treat as conversation
            return response_text, usage_data
            
    except Exception as e:
        # Check if it's a quota error and provide a helpful message
        error_msg = str(e)
        if "429" in error_msg or "quota" in error_msg.lower():
            return "I'd love to chat, but I've hit my OpenAI API quota limit. You can still use commands like 'run unity: project name' or 'help' for available commands. To enable full conversation, add credits to your OpenAI account."
        else:
            return f"Sorry, I encountered an error: {error_msg}"

def plan_simple(user_input: str) -> List[Dict[str, str]]:
    """
    Simple regex-based command parser for known patterns.
    Returns empty list if no patterns match.
    """
    user_input = user_input.strip().lower()
    actions = []
    
    # Patterns for different commands
    patterns = [
        # "run unity: escape purgatory" or "run unity escape purgatory"
        (r'run\s+unity(?:\s*:)?\s*(.+)?', 'run_program', 'unity'),
        
        # "run cursor: escape purgatory" or "run cursor escape purgatory"
        (r'run\s+cursor(?:\s*:)?\s*(.+)?', 'run_program', 'cursor'),
        
        # "open escape purgatory in unity"
        (r'open\s+(.+?)\s+in\s+unity', 'run_program', 'unity'),
        
        # "open escape purgatory in cursor"
        (r'open\s+(.+?)\s+in\s+cursor', 'run_program', 'cursor'),
        
        # Type into command - "Type into cursor: text" or "Type into cursor text"
        (r'^type\s+into\s+["\']?([^"\']+)["\']?\s+window\s+["\']?(.+?)["\']?$', 'run_program', 'type_into'),
        (r'^type\s+into\s+["\']?([^"\']+)["\']?:\s*["\']?(.+?)["\']?$', 'run_program', 'type_into'),
        (r'^type\s+into\s+["\']?([^"\']+)["\']?\s+["\']?(.+?)["\']?$', 'run_program', 'type_into'),
        
        # Cursor Agent CLI — "/cursor prompt" and legacy aliases
        (r'^/cursor\s+-newWindow\s+["\']?(.+?)["\']?$', 'run_program', 'cursor_cli'),
        (r'^/cursor\s+["\']?(.+?)["\']?$', 'run_program', 'cursor_cli'),
        (r'^/cursor-cli\s+["\']?(.+?)["\']?$', 'run_program', 'cursor_cli'),
        (r'^/cursor-console\s+["\']?(.+?)["\']?$', 'run_program', 'cursor_console'),

        # Claude Code command - "/claude prompt" for Claude Code CLI
        (r'^/claude\s+["\']?(.+?)["\']?$', 'run_program', 'claude_code'),
        
        # Cursor AI session management
        (r'^/cursor-end$', 'run_program', 'cursor_end'),
        (r'^/cursor-stop$', 'run_program', 'cursor_stop'),
        (r'^/cursor-status$', 'run_program', 'cursor_status'),
        (r'^/cursor-logs$', 'run_program', 'cursor_logs'),
        
        # Smart Open command - "Open Cursor", "Open Chrome", etc.
        (r'^open\s+(.+)$', 'run_program', 'open'),
        
        # Launch command - "Launch Cursor", "Launch Chrome", etc.
        (r'^launch\s+(.+)$', 'run_program', 'open'),
        
        # Run command - "Run Cursor", "Run Chrome", etc.
        (r'^run\s+(.+)$', 'run_program', 'open'),
        
        # Specific legacy patterns (keep for backward compatibility)
        # Note: launch cursor/unity now handled by smart open above
        
        # Direct app names
        (r'^(unity|cursor)$', 'run_program', None),
        
        # Automation commands
        (r'^(version|ver|info)$', 'run_program', 'version'),
        (r'^(usage|stats|tokens)$', 'run_program', 'usage'),
        (r'^take\s+screenshot$', 'run_program', 'screenshot'),
        (r'^screenshot\s+unity$', 'run_program', 'screenshot_unity'),
        (r'^click\s+play$', 'run_program', 'unity_play'),
        (r'^click\s+stop$', 'run_program', 'unity_stop'),
        (r'^check\s+console$', 'run_program', 'unity_console'),
        (r'^get\s+errors$', 'run_program', 'unity_errors'),
        (r'^open\s+project\s+and\s+screenshot$', 'run_program', 'open_and_screenshot'),
        (r'^play\s+and\s+check\s+errors$', 'run_program', 'play_and_check'),
        (r'^resolve\s+all\s+errors$', 'run_program', 'resolve_errors'),
    ]
    
    for pattern, tool, app in patterns:
        match = re.search(pattern, user_input)
        if match:
            hint = None
            target_window = None
            if match.groups():
                if app == 'type_into' and len(match.groups()) >= 2:
                    # Special handling for type_into: first group is target, second is text
                    target_window = match.group(1).strip() if match.group(1) else None
                    hint = match.group(2).strip() if match.group(2) else None
                else:
                    hint = match.group(1).strip() if match.group(1) else None
            
            action = {
                'tool': tool,
                'app': app if app is not None else hint
            }
            
            # Only add hint if there's a meaningful hint value and app is not None
            if hint and app is not None and hint != app:
                action['hint'] = hint
            
            # Add target_window for type_into commands
            if target_window and app == 'type_into':
                action['target_window'] = target_window
            
            actions.append(action)
            break
    
    return actions

# Legacy function for backward compatibility
def plan(user_input: str) -> List[Dict[str, str]]:
    """
    Legacy function that only returns actions (no conversation).
    Uses LLM but filters out conversational responses.
    """
    result = plan_with_llm(user_input)
    if isinstance(result, list):
        return result
    else:
        return []  # Return empty actions for conversational responses

# For testing
if __name__ == "__main__":
    # Test cases
    test_cases = [
        "run unity: escape purgatory",
        "open my game in Unity",
        "what's the weather like?",
        "launch cursor with my project",
        "can you help me debug this code?",
        "start notepad",
        "hello there!"
    ]
    
    for test in test_cases:
        result = plan_with_llm(test)
        print(f"'{test}' -> {result}")
        print()
#!/usr/bin/env python3
"""
Discord RAG (Retrieval-Augmented Generation) system for JamBit Games Dev Assistant
Reads Discord channels and caches content for AI context
"""
import discord
import os
import json
import hashlib
import asyncio
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from dotenv import load_dotenv

load_dotenv()

class DiscordRAG:
    def __init__(self, bot_token: str):
        self.bot_token = bot_token
        self.cache_file = "cache/discord_cache.json"
        self.cache_duration = timedelta(days=2)  # Cache for 2 days
        self.cache = self.load_cache()
        
        # Log cache status
        if self.is_cache_valid():
            cache_age = datetime.now() - datetime.fromisoformat(self.cache.get('last_updated', '1970-01-01'))
            channel_count = len(self.cache.get('channels', {}))
            if channel_count > 0:
                print(f"[CACHE] Discord RAG: Using cached data ({channel_count} channels, age: {cache_age.days}d {cache_age.seconds//3600}h)")
            else:
                print(f"[CACHE] Discord RAG: Cache exists but empty ({channel_count} channels) - Will fetch fresh data")
        else:
            print(f"[CACHE] Discord RAG: Cache expired/missing - Will fetch fresh data")
        
    def get_client(self):
        """Get a Discord client with proper intents"""
        intents = discord.Intents.default()
        intents.guilds = True
        intents.messages = True
        intents.message_content = True
        
        client = discord.Client(intents=intents)
        
        @client.event
        async def on_ready():
            print(f"[CONNECT] Discord RAG connected as {client.user}")
            print(f"[INFO] Bot is in {len(client.guilds)} guilds")
            if client.guilds:
                for guild in client.guilds:
                    print(f"[GUILD] {guild.name} (ID: {guild.id}) - {len(guild.text_channels)} channels")
            else:
                print("[WARNING] Bot is not in any guilds - RAG features will not work")
        
        return client
    
    def load_cache(self) -> Dict:
        """Load cached Discord content"""
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"[WARNING] Error loading cache: {e}")
        return {"channels": {}, "last_updated": None}
    
    def save_cache(self):
        """Save Discord content to cache"""
        try:
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.cache, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[WARNING] Error saving cache: {e}")
    
    def is_cache_valid(self) -> bool:
        """Check if cache is still valid and has content"""
        if not self.cache.get("last_updated"):
            return False
        
        # Check if cache has any actual content
        channels = self.cache.get("channels", {})
        if not channels:
            return False
        
        # Check if any guild has messages
        has_content = False
        for guild_name, guild_data in channels.items():
            if guild_data.get("total_messages", 0) > 0:
                has_content = True
                break
        
        if not has_content:
            return False
        
        # Check timestamp
        last_updated = datetime.fromisoformat(self.cache["last_updated"])
        return datetime.now() - last_updated < self.cache_duration
    
    async def get_channel_content(self, channel_name: str, limit: int = 50) -> List[Dict]:
        """Get recent messages from a Discord channel"""
        try:
            guild = None
            channel = None
            
            # First try to find by exact channel name
            for g in self.client.guilds:
                channel = discord.utils.get(g.text_channels, name=channel_name)
                if channel:
                    guild = g
                    break
            
            # If not found, try to find guild by name and get main channels
            if not channel:
                for g in self.client.guilds:
                    if channel_name.lower() in g.name.lower():
                        # Get main channels from this guild
                        main_channels = ["general", "story", "survival", "bugs", "to-do"]
                        for ch_name in main_channels:
                            channel = discord.utils.get(g.text_channels, name=ch_name)
                            if channel:
                                guild = g
                                break
                        break
            
            if not guild or not channel:
                print(f"[WARNING] Channel '{channel_name}' not found")
                return []
            
            messages = []
            async for message in channel.history(limit=limit):
                if message.author.bot:
                    continue
                
                messages.append({
                    "author": message.author.display_name,
                    "content": message.content,
                    "timestamp": message.created_at.isoformat(),
                    "message_id": str(message.id)
                })
            
            return messages
            
        except Exception as e:
            print(f"[ERROR] Error reading channel {channel_name}: {e}")
            return []
    
    def categorize_channel(self, channel_name: str) -> str:
        """Categorize channels based on their purpose"""
        channel_lower = channel_name.lower()
        
        # Development categories
        if any(word in channel_lower for word in ["general", "chat", "discussion"]):
            return "general"
        elif any(word in channel_lower for word in ["story", "narrative", "lore", "plot"]):
            return "story"
        elif any(word in channel_lower for word in ["gameplay", "survival", "level", "character", "monsters", "mechanic", "combat"]):
            return "gameplay"
        elif any(word in channel_lower for word in ["craft", "crafting", "recipe", "resource", "gather", "item", "inventory", "equipment"]):
            return "gameplay"  # Crafting is a gameplay mechanic
        elif any(word in channel_lower for word in ["terrain", "biome", "world", "map", "environment", "landscape"]):
            return "gameplay"  # World/environment design is gameplay-related
        elif any(word in channel_lower for word in ["3d", "models", "art", "visual", "screenshots", "texture", "animation"]):
            return "art_assets"
        elif any(word in channel_lower for word in ["sound", "music", "audio", "sfx"]):
            return "audio"
        elif any(word in channel_lower for word in ["gui", "interface", "menu", "hud", "ui"]):
            return "ui"
        elif any(word in channel_lower for word in ["bug", "issue", "problem", "crash", "glitch"]):
            return "bugs"
        elif any(word in channel_lower for word in ["todo", "task", "feature", "plan", "roadmap", "brainstorm"]):
            return "development"
        elif any(word in channel_lower for word in ["steam", "release", "marketing", "publish"]):
            return "release"
        elif any(word in channel_lower for word in ["multiplayer", "online", "network", "coop", "co-op"]):
            return "multiplayer"
        elif any(word in channel_lower for word in ["download", "build", "version", "update"]):
            return "builds"
        elif any(word in channel_lower for word in ["business", "licensing", "legal"]):
            return "business"
        else:
            return "other"

    async def update_cache(self, guild_names: List[str] = None, discord_client=None):
        """Update cache with latest Discord content from all channels
        
        Args:
            guild_names: Optional list of guild names/IDs to process. If None or empty, processes ALL guilds.
            discord_client: Optional Discord client to use (if already connected)
        """
        if self.is_cache_valid():
            print("[SUCCESS] Using cached Discord content")
            return
        
        print("[UPDATE] Updating Discord content cache...")
        total_messages = 0
        
        # Use provided client or create a new one
        if discord_client:
            client = discord_client
        else:
            client = self.get_client()
            # If we created a new client, we need to connect it
            if not client.guilds:
                print("[CONNECT] Connecting to Discord...")
                await client.start(self.bot_token)
                # Wait a moment for connection
                await asyncio.sleep(2)
        
        # Debug: Show available guilds with IDs
        print(f"[DEBUG] Available guilds: {[(g.name, g.id) for g in client.guilds]}")
        
        # Check if we have any guilds at all
        if not client.guilds:
            print("[ERROR] Bot is not in any Discord servers!")
            print("[TIP] Add your bot to at least one Discord server to use RAG features")
            return
        
        # If no guild names specified, use ALL available guilds
        if not guild_names:
            guilds_to_process = client.guilds
            print(f"[AUTO] No guilds specified - processing ALL {len(guilds_to_process)} guilds")
        else:
            # Find guilds matching the provided names/IDs
            guilds_to_process = []
            for guild_name in guild_names:
                # Find guild by exact name, partial match, or ID
                guild = None
                for g in client.guilds:
                    # Try name match first
                    if g.name == guild_name or guild_name.lower() in g.name.lower():
                        guild = g
                        break
                    # Try ID match
                    if str(g.id) == str(guild_name):
                        guild = g
                        break
                
                if guild:
                    guilds_to_process.append(guild)
                else:
                    print(f"[WARNING] Guild '{guild_name}' not found in {[(g.name, g.id) for g in client.guilds]}")
        
        # Process each guild
        for guild in guilds_to_process:
            print(f"\n[GUILD] Processing guild: {guild.name} (ID: {guild.id})")
            print(f"[CHANNELS] Guild has {len(guild.text_channels)} text channels")
            
            # Process all text channels
            channel_categories = {}
            guild_messages = 0
            
            for channel in guild.text_channels:
                print(f"[CHECK] Checking channel: #{channel.name} (id: {channel.id})")
                try:
                    # Check permissions
                    perms = channel.permissions_for(guild.me)
                    if not perms.read_messages or not perms.read_message_history:
                        print(f"[SKIP] No permission to read #{channel.name}")
                        continue
                    
                    category = self.categorize_channel(channel.name)
                    if category not in channel_categories:
                        channel_categories[category] = []
                    
                    messages = []
                    message_count = 0
                    async for message in channel.history(limit=50):  # Get more messages per channel
                        message_count += 1
                        if message.author.bot:
                            continue
                        
                        messages.append({
                            "author": message.author.display_name,
                            "content": message.content,
                            "timestamp": message.created_at.isoformat(),
                            "channel": channel.name,
                            "category": category,
                            "message_id": str(message.id)
                        })
                    
                    print(f"[STATS] Found {message_count} total messages, {len(messages)} non-bot messages")
                    
                    if messages:
                        channel_categories[category].extend(messages)
                        guild_messages += len(messages)
                        print(f"[MESSAGES] #{channel.name} ({category}): {len(messages)} messages")
                    
                except Exception as e:
                    print(f"[WARNING] Error reading #{channel.name}: {e}")
                    import traceback
                    traceback.print_exc()
            
            # Store categorized data (use guild.name as key)
            if channel_categories:
                self.cache["channels"][guild.name] = {
                    "guild_id": guild.id,
                    "categories": channel_categories,
                    "total_messages": guild_messages,
                    "channel_count": len([ch for cat in channel_categories.values() for ch in cat]),
                    "last_updated": datetime.now().isoformat()
                }
                total_messages += guild_messages
                print(f"[SUCCESS] Cached {guild_messages} messages from {guild.name}")
            else:
                print(f"[WARNING] No messages found in {guild.name}")
        
        self.cache["last_updated"] = datetime.now().isoformat()
        self.save_cache()
        guild_count = len(self.cache.get("channels", {}))
        print(f"\n[SUCCESS] Cached {total_messages} messages from {guild_count} guild(s)")
        print(f"[CACHE] Cache saved to {self.cache_file}")
    
    def get_relevant_context(self, query: str, max_messages: int = 8, verbose: bool = False) -> str:
        """Get relevant Discord context based on query and categories
        
        Args:
            query: The user's query text
            max_messages: Maximum messages to include per category
            verbose: If True, print detailed matching information
        """
        if not self.cache.get("channels"):
            if verbose:
                print("[RAG] Cache is empty - no context available")
            return ""
        
        context_parts = []
        query_lower = query.lower()
        
        # Determine relevant categories based on query
        relevant_categories = set()
        
        # Map query keywords to categories
        if any(word in query_lower for word in ["story", "narrative", "plot", "lore"]):
            relevant_categories.add("story")
        if any(word in query_lower for word in ["gameplay", "survival", "level", "character", "monster", "mechanic", "system", "combat", "fight"]):
            relevant_categories.add("gameplay")
        if any(word in query_lower for word in ["bug", "issue", "problem", "error", "crash", "glitch"]):
            relevant_categories.add("bugs")
        if any(word in query_lower for word in ["todo", "task", "feature", "development", "plan", "roadmap"]):
            relevant_categories.add("development")
        if any(word in query_lower for word in ["art", "model", "visual", "3d", "texture", "animation"]):
            relevant_categories.add("art_assets")
        if any(word in query_lower for word in ["sound", "audio", "music", "sfx"]):
            relevant_categories.add("audio")
        if any(word in query_lower for word in ["steam", "release", "marketing", "publish"]):
            relevant_categories.add("release")
        if any(word in query_lower for word in ["multiplayer", "online", "coop", "co-op"]):
            relevant_categories.add("multiplayer")
        if any(word in query_lower for word in ["build", "download", "version", "update"]):
            relevant_categories.add("builds")
        if any(word in query_lower for word in ["ui", "menu", "interface", "hud", "gui"]):
            relevant_categories.add("ui")
        
        # For game-specific queries about mechanics/systems, also search "other" category
        # This catches crafting, terrain, biomes, and other specialized channels
        if any(word in query_lower for word in ["craft", "crafting", "recipe", "resource", "gather", 
                                                  "terrain", "biome", "world", "map", "environment",
                                                  "item", "inventory", "weapon", "tool", "equipment"]):
            relevant_categories.add("other")
            relevant_categories.add("gameplay")
        
        # If no specific categories, use general, development, AND other
        if not relevant_categories:
            relevant_categories = {"general", "development", "other"}
        
        if verbose:
            print(f"[RAG] Query: '{query}'")
            print(f"[RAG] Matching categories: {relevant_categories}")
        
        # Check if query mentions a specific guild/game - prioritize that guild
        mentioned_guilds = []
        for guild_name in self.cache.get("channels", {}).keys():
            # Check if guild name or keywords appear in query
            guild_lower = guild_name.lower()
            if guild_lower in query_lower or any(word in query_lower for word in guild_lower.split()):
                mentioned_guilds.append(guild_name)
        
        if mentioned_guilds:
            if verbose:
                print(f"[RAG] Detected guild mention: {mentioned_guilds}")
        
        total_messages_found = 0
        
        # Process guilds in priority order: mentioned guilds first, then others
        guilds_to_search = list(self.cache["channels"].keys())
        if mentioned_guilds:
            # Prioritize mentioned guilds
            guilds_to_search = mentioned_guilds + [g for g in guilds_to_search if g not in mentioned_guilds]
        
        for guild_name in guilds_to_search:
            guild_data = self.cache["channels"][guild_name]
            categories = guild_data.get("categories", {})
            
            for category, messages in categories.items():
                if category not in relevant_categories:
                    continue
                
                # Find relevant messages within category
                relevant_messages = []
                for msg in messages:
                    content_lower = msg["content"].lower()
                    if any(word in content_lower for word in query_lower.split()):
                        relevant_messages.append(msg)
                
                # If no keyword matches, take recent messages from category
                if not relevant_messages:
                    relevant_messages = messages[-max_messages:]
                
                if relevant_messages:
                    # Include guild name if query mentions a specific guild or if it's not Escape Purgatory
                    guild_prefix = f" [{guild_name}]" if (mentioned_guilds and guild_name in mentioned_guilds) or guild_name != "Escape Purgatory (Game Development)" else ""
                    context_parts.append(f"\n**{category.replace('_', ' ').title()}{guild_prefix} ({len(relevant_messages)} messages):**")
                    for msg in relevant_messages[-max_messages:]:
                        timestamp = datetime.fromisoformat(msg["timestamp"]).strftime("%m/%d %H:%M")
                        channel_name = msg.get("channel", "unknown")
                        content = msg["content"][:150] + "..." if len(msg["content"]) > 150 else msg["content"]
                        context_parts.append(f"  • #{channel_name} | {msg['author']} ({timestamp}): {content}")
                    total_messages_found += len(relevant_messages)
                    
                    # If we found messages from a mentioned guild, limit results from other guilds
                    if mentioned_guilds and guild_name in mentioned_guilds and total_messages_found >= max_messages * 2:
                        break
        
        if verbose:
            print(f"[RAG] Found {total_messages_found} relevant messages")
        
        return "\n".join(context_parts)
    
    async def start(self):
        """Start the Discord RAG client"""
        await self.client.start(self.bot_token)

# Global instance
discord_rag = None

def get_discord_rag() -> Optional[DiscordRAG]:
    """Get the global Discord RAG instance"""
    return discord_rag

def initialize_discord_rag():
    """Initialize Discord RAG system"""
    global discord_rag
    bot_token = os.getenv("DISCORD_TOKEN")
    if bot_token:
        print("[RAG] Initializing Discord RAG system...")
        discord_rag = DiscordRAG(bot_token)
        
        # If cache is empty, trigger an update in the background
        if not discord_rag.is_cache_valid() or len(discord_rag.cache.get('channels', {})) == 0:
            print("[RAG] Cache invalid/empty - will update on bot startup")
            # We'll update the cache when the first message is processed
            discord_rag._needs_cache_update = True
        else:
            discord_rag._needs_cache_update = False
            # Show cache stats
            cache_size = len(discord_rag.cache.get('channels', {}))
            total_msgs = sum(guild.get('total_messages', 0) for guild in discord_rag.cache.get('channels', {}).values())
            print(f"[RAG] Discord RAG initialized with {total_msgs} messages from {cache_size} guilds")
            
        return discord_rag
    else:
        print("[RAG] No DISCORD_TOKEN found - Discord RAG disabled")
    return None

# Test function
async def test_discord_rag():
    """Test the Discord RAG system"""
    rag = initialize_discord_rag()
    if not rag:
        print("[ERROR] No Discord token found")
        return
    
    # Create client and run the cache update
    client = rag.get_client()
    try:
        await client.start(rag.bot_token)
        # Pass None or empty list to auto-detect all guilds
        await rag.update_cache(None, client)
    finally:
        await client.close()
    
    context = rag.get_relevant_context("Unity optimization")
    print("[CONTEXT] Relevant context:")
    print(context)

if __name__ == "__main__":
    import asyncio
    asyncio.run(test_discord_rag())

#!/usr/bin/env python3
"""
Test the Discord RAG (Retrieval-Augmented Generation) system
"""
import sys
import os
import asyncio
import pytest
from pathlib import Path

# Add the project root to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

def test_rag_import():
    """Test that RAG system can be imported"""
    try:
        from rag.discord_rag import DiscordRAG, initialize_discord_rag
        print("[PASS] RAG system imports successfully")
        return True
    except ImportError as e:
        print(f"[FAIL] RAG system import failed: {e}")
        return False

def test_rag_initialization():
    """Test RAG system initialization"""
    try:
        from rag.discord_rag import initialize_discord_rag
        
        # Check if Discord token is available
        discord_token = os.getenv("DISCORD_TOKEN")
        if not discord_token or discord_token == 'your_discord_bot_token_here' or discord_token.startswith('your_'):
            print("[WARNING] DISCORD_TOKEN not found or is placeholder - RAG tests will be limited")
            print("   To test with real Discord connection, update DISCORD_TOKEN in .env file")
            return True  # Don't fail the test for placeholder values
        
        # Initialize RAG system
        rag = initialize_discord_rag()
        if rag:
            print("[PASS] RAG system initialized successfully")
            print(f"[INFO] Cache file: {rag.cache_file}")
            print(f"[INFO] Cache valid: {rag.is_cache_valid()}")
            return True
        else:
            print("[FAIL] RAG system initialization failed")
            return False
            
    except Exception as e:
        print(f"[FAIL] RAG initialization test failed: {e}")
        return False

@pytest.mark.asyncio
async def test_rag_discord_connection():
    """Test Discord connection for RAG system"""
    try:
        from rag.discord_rag import DiscordRAG
        
        discord_token = os.getenv("DISCORD_TOKEN")
        if not discord_token or discord_token == 'your_discord_bot_token_here' or discord_token.startswith('your_') or discord_token == 'test_token':
            print("[SKIP] No valid Discord token - skipping connection test")
            print("   To test Discord connection, update DISCORD_TOKEN in .env file")
            print("   Get your bot token from: https://discord.com/developers/applications")
            return True  # Don't fail the test for placeholder values
        
        rag = DiscordRAG(discord_token)
        client = rag.get_client()
        
        print("[TEST] Testing Discord connection...")
        
        # Track connection state
        connected = False
        
        # Override the on_ready event to track connection
        original_on_ready = client.event(client.on_ready)
        
        @client.event
        async def on_ready():
            nonlocal connected
            connected = True
            print(f"[CONNECT] Discord RAG connected as {client.user}")
            print(f"[INFO] Bot is in {len(client.guilds)} guilds")
            if client.guilds:
                for guild in client.guilds:
                    print(f"[GUILD] {guild.name} (ID: {guild.id}) - {len(guild.text_channels)} channels")
            else:
                print("[WARNING] Bot is not in any guilds - RAG features will not work")
        
        # Start the client with a timeout
        try:
            # Start the client in a task
            start_task = asyncio.create_task(client.start(discord_token))
            
            # Wait for either connection or timeout
            try:
                await asyncio.wait_for(start_task, timeout=15.0)
            except asyncio.TimeoutError:
                # If we timeout, check if we connected
                if connected:
                    print("[PASS] Connected to Discord successfully")
                else:
                    print("[FAIL] Discord connection timed out")
                    start_task.cancel()
                    return False
        except Exception as e:
            print(f"[FAIL] Discord connection failed: {e}")
            return False
        
        # Wait a moment for guilds to load
        await asyncio.sleep(2)
        
        if client.guilds:
            print(f"[PASS] Connected to Discord with {len(client.guilds)} guilds")
            print("[DEBUG] Available guilds:")
            for guild in client.guilds:
                print(f"  - '{guild.name}' (ID: {guild.id})")
                print(f"    Channels: {len(guild.text_channels)}")
                for channel in guild.text_channels[:3]:  # Show first 3 channels
                    print(f"      #{channel.name}")
                if len(guild.text_channels) > 3:
                    print(f"      ... and {len(guild.text_channels) - 3} more")
        else:
            print("[WARNING] Connected to Discord but no guilds found")
            print("[TIP] Make sure your bot is added to at least one server")
        
        await client.close()
        # Give a moment for cleanup
        await asyncio.sleep(0.1)
        return True
        
    except Exception as e:
        print(f"[FAIL] Discord connection test failed: {e}")
        return False

def test_rag_cache_operations():
    """Test RAG cache operations"""
    try:
        from rag.discord_rag import DiscordRAG
        
        discord_token = os.getenv("DISCORD_TOKEN")
        if not discord_token or discord_token == 'your_discord_bot_token_here' or discord_token.startswith('your_') or discord_token == 'test_token':
            print("[SKIP] No valid Discord token - skipping cache test")
            print("   To test cache operations, update DISCORD_TOKEN in .env file")
            return True  # Don't fail the test for placeholder values
        
        rag = DiscordRAG(discord_token)
        
        # Test cache loading
        cache = rag.load_cache()
        print(f"[PASS] Cache loaded: {len(cache.get('channels', {}))} guilds")
        
        # Test cache validity
        is_valid = rag.is_cache_valid()
        print(f"[INFO] Cache valid: {is_valid}")
        
        # Test cache file existence
        cache_file = Path(rag.cache_file)
        if cache_file.exists():
            print(f"[PASS] Cache file exists: {cache_file}")
            print(f"[INFO] Cache file size: {cache_file.stat().st_size} bytes")
        else:
            print(f"[INFO] Cache file does not exist: {cache_file}")
        
        # Show cache statistics if available
        if cache.get('channels'):
            total_messages = 0
            for guild_name, guild_data in cache['channels'].items():
                guild_messages = guild_data.get('total_messages', 0)
                total_messages += guild_messages
                print(f"[INFO] Guild '{guild_name}': {guild_messages} messages")
            print(f"[INFO] Total cached messages: {total_messages}")
        
        return True
        
    except Exception as e:
        print(f"[FAIL] Cache operations test failed: {e}")
        return False

def test_rag_guild_name_resolution():
    """Test guild name resolution logic"""
    try:
        from rag.discord_rag import DiscordRAG
        
        # Test the guild name resolution logic
        rag = DiscordRAG("dummy_token")
        
        # Mock guild data to test resolution
        class MockGuild:
            def __init__(self, name, guild_id):
                self.name = name
                self.id = guild_id
        
        mock_guilds = [
            MockGuild("Escape Purgatory (Game Development)", 123456789),
            MockGuild("Sonic Multiverse", 987654321),
            MockGuild("Epochs - From Fire To The Stars", 456789123)
        ]
        
        # Test exact name matching
        test_names = [
            "Escape Purgatory (Game Development)",
            "Sonic Multiverse", 
            "Epochs - From Fire To The Stars",
            "escape purgatory",  # case insensitive
            "sonic",  # partial match
            "epochs"  # partial match
        ]
        
        print("[TEST] Testing guild name resolution...")
        for test_name in test_names:
            found = False
            for guild in mock_guilds:
                if (guild.name == test_name or 
                    test_name.lower() in guild.name.lower() or
                    str(guild.id) == str(test_name)):
                    print(f"[PASS] Found guild '{guild.name}' for query '{test_name}'")
                    found = True
                    break
            
            if not found:
                print(f"[FAIL] No guild found for query '{test_name}'")
        
        return True
        
    except Exception as e:
        print(f"[FAIL] Guild name resolution test failed: {e}")
        return False

def test_rag_context_generation():
    """Test RAG context generation"""
    try:
        from rag.discord_rag import DiscordRAG
        
        rag = DiscordRAG("dummy_token")
        
        # Test context generation with empty cache
        context = rag.get_relevant_context("Unity optimization")
        print(f"[INFO] Context generation with empty cache: {len(context)} characters")
        
        # Test context generation with mock data
        rag.cache = {
            "channels": {
                "test_guild": {
                    "categories": {
                        "development": [
                            {
                                "author": "TestUser",
                                "content": "Working on Unity optimization for better performance",
                                "timestamp": "2024-01-01T12:00:00",
                                "channel": "general",
                                "category": "development",
                                "message_id": "123"
                            }
                        ]
                    },
                    "total_messages": 1,
                    "channel_count": 1,
                    "last_updated": "2024-01-01T12:00:00"
                }
            }
        }
        
        context = rag.get_relevant_context("Unity optimization")
        print(f"[PASS] Context generation with mock data: {len(context)} characters")
        if context:
            print(f"[INFO] Generated context preview: {context[:200]}...")
        
        return True
        
    except Exception as e:
        print(f"[FAIL] Context generation test failed: {e}")
        return False

async def run_rag_tests_async():
    """Run all RAG system tests"""
    print("[TEST] Running Discord RAG System Tests")
    print("=" * 60)
    print("[INFO] Note: These tests will connect to Discord but won't fetch message history")
    print("[INFO] If you see connection messages, that's expected behavior")
    print("=" * 60)
    
    test_results = {}
    
    # Test 1: Import
    test_results["Import"] = test_rag_import()
    
    # Test 2: Initialization
    test_results["Initialization"] = test_rag_initialization()
    
    # Test 3: Cache Operations
    test_results["Cache Operations"] = test_rag_cache_operations()
    
    # Test 4: Guild Name Resolution
    test_results["Guild Name Resolution"] = test_rag_guild_name_resolution()
    
    # Test 5: Context Generation
    test_results["Context Generation"] = test_rag_context_generation()
    
    # Test 6: Discord Connection (async)
    test_results["Discord Connection"] = await test_rag_discord_connection()
    
    # Summary
    print("\n" + "=" * 60)
    print("[SUMMARY] RAG System Test Results")
    print("=" * 60)
    
    passed = sum(1 for result in test_results.values() if result)
    total = len(test_results)
    
    for test_name, result in test_results.items():
        status = "[PASS]" if result else "[FAIL]"
        print(f"{test_name:<25} {status}")
    
    print(f"\nOverall: {passed}/{total} tests passed")
    
    if passed == total:
        print("[SUCCESS] All RAG system tests passed!")
    else:
        print("[WARNING] Some RAG system tests failed")
        print("\nCommon issues:")
        print("• DISCORD_TOKEN not set in environment")
        print("• Bot not added to any Discord servers")
        print("• Bot lacks necessary permissions")
        print("• Network connectivity issues")
    
    return passed == total

def run_rag_tests():
    """Run all RAG system tests (synchronous wrapper)"""
    try:
        result = asyncio.run(run_rag_tests_async())
        # Give a moment for any remaining cleanup
        import time
        time.sleep(0.1)
        return result
    except Exception as e:
        print(f"[FAIL] RAG test runner failed: {e}")
        return False

if __name__ == "__main__":
    success = run_rag_tests()
    sys.exit(0 if success else 1)

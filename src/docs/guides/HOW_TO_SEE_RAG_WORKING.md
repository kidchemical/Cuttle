# How to See RAG Working in Your Bot

## What You Should See Now

### When Bot Starts (launcher.py option 1)
Look for these messages during startup:

```
[RAG] Initializing Discord RAG system...
[CACHE] Discord RAG: Using cached data (4 channels, age: 0d 1h)
[RAG] Discord RAG initialized with 597 messages from 4 guilds
[OK] Discord RAG: Initialized successfully
```

This confirms:
- ✅ RAG system is loading
- ✅ Cache has data (597 messages from 4 Discord servers)
- ✅ RAG is ready to use

### When You Send a Message
Every time you ask the bot something, you'll see one of these:

**If relevant context is found:**
```
[RAG] ✓ Using Discord RAG context (1589 chars, 30 lines)
[RAG] Preview: **Gameplay (79 messages):**...
```

**If no relevant context is found:**
```
[RAG] ✗ No relevant Discord context found for: 'your query...'
```

This is NORMAL - it just means your query didn't match any Discord discussions.

## Test Queries That WILL Show RAG Context

Try asking these questions to see RAG in action:

1. **"Tell me about the story"**
   - Should return ~1154 chars from #story channel
   
2. **"What monsters are in the game?"**
   - Should return ~1589 chars from #monsters channel
   
3. **"Are there any bugs?"**
   - Should return messages from #bugs channel
   
4. **"What about multiplayer?"**
   - Should return messages from #multiplayer channel

## What the LLM Receives

When RAG finds relevant context, it gets added to the system prompt like this:

```
## LIVE DISCORD CONTEXT:

**Gameplay (79 messages):**
  • #monsters | owner (09/14 08:33): So whenever you near the "skinwalker"...
  • #monsters | DeathTheGentleman (09/14 04:11): Another creature talked about...
  [... more messages ...]
```

The LLM can then reference these actual Discord conversations in its responses!

## Verification Results

✅ **All systems working:**
- [OK] RAG modules import successfully
- [OK] Discord RAG initialized with 597 messages from 4 guilds  
- [OK] ai_agent can access Discord RAG instance
- [OK] Context retrieval working (returns 1154-1589 chars depending on query)

## Files That Were Fixed

1. **`ai_agent.py`**
   - Now gets RAG instance dynamically (not at import time)
   - Added detailed logging for every query
   - Shows context length and preview

2. **`rag/discord_rag.py`**
   - Auto-detects all Discord guilds
   - Better initialization logging
   - Shows cache stats on startup

3. **`bot_deprecated.py` & `bot_mcp.py`**
   - Better error handling
   - Clearer initialization messages

## Still Not Seeing Logs?

If you're running `launcher.py` option 1 and NOT seeing the `[RAG]` logs:

1. **Make sure you're looking at the console output**, not just the Discord messages
2. **Check the bot actually started** - you should see initialization messages
3. **Try one of the test queries above** - they're guaranteed to match Discord content

## Summary

The RAG system IS working! You just need to look for the `[RAG]` log messages in your console. Every query now shows whether RAG context was used and what was found.

---

**Cache Status:** 597 messages, 4 guilds, 305 KB  
**Last Verified:** October 9, 2025


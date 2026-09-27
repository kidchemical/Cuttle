# Discord RAG Verification Guide

## Overview
This guide explains how to verify that the Discord RAG (Retrieval-Augmented Generation) system is working and being used by the LLM.

## Quick Status Check

### ✅ What's Working:
- **597 messages** cached from **4 Discord servers**
- Cache auto-updates every 2 days
- Context retrieval is functional
- LLM integration is active

## How to Verify RAG is Being Used

### Method 1: Check Console Logs (EASIEST)
When you interact with the bot (launcher.py option 1), look for these log messages:

```
[RAG] ✓ Using Discord RAG context (704 chars, 15 lines)
[RAG] Preview: **Gameplay (28 messages):**...
```

If you see `[RAG] ✗ No relevant Discord context found`, it means:
- The query didn't match any cached Discord discussions
- The bot will still respond, but without Discord context

### Method 2: Test Specific Queries
Ask the bot questions about topics discussed in your Discord:

**Good Test Queries:**
- "Tell me about the story" → Should pull from #story channel
- "What gameplay features are planned?" → Should pull from #monsters, #survival, etc.
- "Are there any bugs?" → Should pull from #bugs channel
- "What about multiplayer?" → Should pull from #multiplayer channel

**Example Response You'll See:**
```
[QUERY] Started tracking query...
[RAG] ✓ Using Discord RAG context (536 chars, 13 lines)
[RAG] Preview: **Bugs (10 messages):**
  • #bugs | owner (08/01 23:53): When recycling world...
```

### Method 3: Check Cache File
The cache is stored at: `cache/discord_cache.json`

Current status:
- **File size:** 305 KB (full of data!)
- **Guilds:** 4 servers
- **Messages:** 597 total
- **Last updated:** Check the timestamp in the file

## What Gets Passed to the LLM?

The LLM receives a system prompt that includes:

```
## LIVE DISCORD CONTEXT:
{relevant Discord messages from your servers}
```

This context includes:
- Recent messages from relevant channels
- Categorized by topic (story, gameplay, bugs, etc.)
- Up to 8 messages per category by default
- Truncated to 150 chars per message (to save tokens)

## Cached Discord Data Breakdown

### Escape Purgatory (Game Development) - 572 messages
- **Channels:** 25 text channels
- **Categories:** story, gameplay, art_assets, audio, ui, bugs, release, multiplayer, builds, other
- **Top Channels:** #general (49), #story (50), #level (50), #monsters (47), #3d-models (50)

### Sonic Multiverse - 4 messages
- **Channels:** 8 text channels
- **Limited activity:** Mostly empty channels

### Epochs - From Fire To The Stars - 20 messages
- **Channels:** 10 text channels
- **Categories:** general, brainstorming, crafting, terrain

### JamBit Games - 1 message
- **Channels:** 3 text channels
- **Very limited activity**

## Category Matching Keywords

The RAG system automatically matches your query to relevant categories:

| Keywords | Category |
|----------|----------|
| story, narrative, plot, lore | `story` |
| gameplay, survival, level, character, monster | `gameplay` |
| bug, issue, problem, error | `bugs` |
| todo, task, feature, development | `development` |
| art, model, visual, 3d | `art_assets` |
| sound, audio, music | `audio` |
| steam, release, marketing | `release` |
| multiplayer, online | `multiplayer` |
| build, download, version | `builds` |
| (no match) | `general` + `development` |

## Troubleshooting

### "I don't think the LLM is using the context"

**Check these things:**
1. **Look for RAG logs** in the console when you send a message
2. **Try queries that match your Discord discussions** - don't ask about things never discussed
3. **Check if cache is populated** - run the bot first to collect messages

### "RAG says 'No relevant context found'"

This is normal if:
- Your query is about a topic not discussed in Discord
- Your Discord channels are empty or have no matching keywords
- Cache hasn't been updated recently (>2 days old)

### "How do I force a cache update?"

**Option 1:** Delete the cache file and restart the bot
```bash
rm cache/discord_cache.json
python launcher.py
```

**Option 2:** Wait 2 days - it auto-updates

## Advanced: Test RAG Manually

Create a test script:
```python
from rag.discord_rag import initialize_discord_rag

rag = initialize_discord_rag()
context = rag.get_relevant_context("your query here", verbose=True)
print(context)
```

This will show exactly what context is being retrieved.

## Summary

✅ **Discord RAG IS working** - The system:
1. Caches 597 messages from your 4 Discord servers
2. Categorizes them by topic
3. Retrieves relevant context based on your queries
4. Passes this context to the LLM in the system prompt

✅ **How to confirm it's being used:**
- Look for `[RAG] ✓ Using Discord RAG context` in console logs
- Ask questions about topics discussed in your Discord
- The LLM will reference recent discussions when relevant

✅ **Updated files:**
- `ai_agent.py` - Added detailed RAG logging
- `rag/discord_rag.py` - Auto-detects all guilds, improved logging
- `bot_deprecated.py` - Uses auto-detection instead of hardcoded guilds

---

**Last updated:** October 9, 2025
**Cache status:** 597 messages, 4 guilds, 305 KB


# RAG (Retrieval-Augmented Generation) Directory

This directory contains RAG pipeline components that enhance AI responses by retrieving relevant context from cached data.

## Components

### `discord_rag.py`
**Purpose**: Discord-specific RAG system  
**Function**: Retrieves relevant Discord messages for AI context  
**Integration**: Works with Discord bot and AI agent  

**Key Features**:
- **Message Caching**: Stores Discord messages for offline access
- **Smart Retrieval**: Finds relevant messages based on user queries
- **Context Enhancement**: Provides AI with Discord conversation history
- **Performance Optimization**: Reduces Discord API calls

**Main Classes**:
- `DiscordRAG`: Main RAG system class
- `initialize_discord_rag()`: Initialization function

**Methods**:
- `update_cache()`: Update Discord message cache
- `get_relevant_context()`: Retrieve relevant messages for queries
- `is_cache_valid()`: Check if cache is still fresh
- `load_cache()`: Load cache from disk

## How It Works

1. **Cache Creation**: Discord messages are cached to `cache/discord_cache.json`
2. **Query Processing**: User queries are analyzed for keywords
3. **Context Retrieval**: Relevant cached messages are retrieved
4. **AI Enhancement**: Retrieved context is provided to AI for better responses

## Configuration

**Cache Duration**: 2 days (configurable)  
**Cache Location**: `cache/discord_cache.json`  
**Update Frequency**: On bot startup and when cache expires  

## Integration

The RAG system integrates with:
- **Discord Bot**: `bot_deprecated.py` - Main bot logic
- **AI Agent**: `ai_agent.py` - AI response generation
- **Cache System**: `cache/` directory for storage

## Future Enhancements

Planned RAG improvements:
- **Vector Embeddings**: Semantic search using embeddings
- **Multi-Source RAG**: Integration with other data sources
- **Advanced Filtering**: Better relevance scoring
- **Real-time Updates**: Live cache updates during conversations

## Usage Example

```python
from rag.discord_rag import initialize_discord_rag

# Initialize RAG system
discord_rag = initialize_discord_rag()

# Get relevant context for a query
context = discord_rag.get_relevant_context("Unity errors")
print(context)
```

## Dependencies

- **Discord.py**: Discord API access
- **JSON**: Cache file handling
- **Datetime**: Cache expiration management
- **Typing**: Type hints for better code quality

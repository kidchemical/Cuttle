# Cache Directory

This directory contains cache files that store temporary data to improve performance and reduce API calls.

## Files

### `discord_cache.json`
**Purpose**: Discord RAG cache for storing Discord messages  
**Content**: Cached Discord channel messages organized by guild and channel  
**Duration**: 2 days (automatically refreshed)  
**Size**: Varies based on Discord server activity  

**Structure**:
```json
{
  "last_updated": "2025-01-21T10:30:00",
  "channels": {
    "guild_name": {
      "channel_name": {
        "messages": [...],
        "total_messages": 150,
        "last_message": "2025-01-21T10:25:00"
      }
    }
  }
}
```

## Cache Management

- **Automatic Refresh**: Cache is automatically updated when expired
- **Manual Refresh**: Use Discord command to force cache update
- **Cleanup**: Old cache files can be safely deleted (will be regenerated)
- **Size Limit**: Cache is limited to prevent excessive disk usage

## Git Ignore

Cache files are excluded from Git to prevent committing large, frequently changing data.

## Usage

The cache is automatically managed by the Discord RAG system:
- Loads on bot startup
- Updates when expired
- Provides context for AI responses
- Reduces Discord API calls

## Troubleshooting

- **Cache not updating**: Check Discord bot permissions
- **Large cache size**: Consider reducing cache duration
- **Missing cache**: Bot will regenerate automatically

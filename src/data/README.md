# Data Directory

This directory contains the SQLite database for user authentication and chat sessions.

## Files

- `cuttle_auth.db` - Main authentication database (automatically created on first run)

## Database Schema

### Tables

1. **users** - User accounts
   - id, email, password_hash, display_name, auth_provider, created_at, etc.

2. **auth_sessions** - Login sessions
   - session_token, user_id, expires_at, last_activity

3. **chat_sessions** - Chat conversations
   - id, user_id, session_name, created_at, last_activity

4. **chat_messages** - Individual messages
   - id, chat_session_id, role, content, timestamp

5. **oauth_states** - OAuth flow tracking
   - state_token, provider, expires_at

## Backup

**Important**: Regularly backup this directory in production!

```bash
# Example backup command
cp data/cuttle_auth.db data/backups/cuttle_auth_$(date +%Y%m%d_%H%M%S).db
```

## Reset Database

To reset the database (delete all users and sessions):

```bash
rm data/cuttle_auth.db
# Database will be recreated on next server start
```


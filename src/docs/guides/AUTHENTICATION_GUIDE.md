# Cuttle Authentication System

Complete guide to user authentication and session management in Cuttle.

## Features

✅ **Multiple Authentication Methods**:
- Email/Password registration and login
- Google OAuth
- Microsoft OAuth  
- Facebook OAuth

✅ **Persistent Chat Sessions**:
- All chat history saved to database
- Access your conversations from any device
- Create and manage multiple chat sessions

✅ **Secure by Design**:
- Passwords hashed with SHA-256
- HTTP-only session cookies
- OAuth 2.0 standard compliance
- CSRF protection via state tokens

## Quick Start

### 1. Basic Setup (Email/Password Only)

No configuration needed! Just start the server:

```bash
python start_web_chat.py
```

Open http://localhost:8080 and click "Login" to create an account.

### 2. OAuth Setup (Optional)

To enable Google, Microsoft, or Facebook login:

1. Follow the [OAuth Setup Guide](OAUTH_SETUP.md)
2. Add credentials to `.env` file (see `docs/setup/env_example.txt`)
3. Restart the server

## User Experience

### For Guests (Not Logged In)

- Can use the chat interface
- Chat history stored in browser memory only
- History lost on page refresh
- Cannot create multiple sessions

### For Authenticated Users

- Chat history saved permanently
- Access conversations from any device
- Create unlimited chat sessions
- Full-screen chat interface
- Session management sidebar

## Authentication Flow

### Email/Password Registration

1. Click "Login" button
2. Switch to "Sign Up" tab
3. Enter display name, email, and password
4. Click "Create Account"
5. Automatically logged in

### Email/Password Login

1. Click "Login" button
2. Enter email and password
3. Click "Login"
4. Redirected to full-screen chat

### OAuth Login (Google/Microsoft/Facebook)

1. Click "Login" button
2. Click "Continue with [Provider]"
3. Authenticate with provider
4. Grant email permission
5. Automatically redirected back and logged in

## Session Management

### Creating Sessions

When logged in, you can create multiple chat sessions:

1. Click "➕ New Chat Session" in the sidebar
2. Enter a name or leave blank for auto-naming
3. New session is created and selected

### Switching Sessions

Click any session in the sidebar to switch to it. The chat history for that session will load immediately.

### Deleting Sessions

Hover over a session and click the "×" button to delete it. You'll be asked to confirm.

## User Interface

### When Not Logged In

- "Login" button in header
- Hero section with download button
- Features section
- Standard chat interface (non-persistent)

### When Logged In

- User avatar and name in header
- Full-screen chat interface
- Session management sidebar on the left
- Hero and features sections hidden
- Logout option in user menu

## API Endpoints

### Authentication

- `POST /api/auth/register` - Create new account
- `POST /api/auth/login` - Login with credentials
- `POST /api/auth/logout` - Logout current user
- `GET /api/auth/me` - Get current user info
- `GET /api/auth/oauth/{provider}` - Start OAuth flow
- `GET /api/auth/callback/{provider}` - OAuth callback

### Session Management

- `GET /api/auth/sessions` - List user's chat sessions
- `POST /api/auth/sessions` - Create new chat session
- `DELETE /api/auth/sessions/{id}` - Delete chat session
- `GET /api/auth/sessions/{id}/messages` - Get session messages

### Chat

- `POST /api/chat` - Send message (works for both authenticated and guest users)

## Database

### Location

`data/cuttle_auth.db` - SQLite database

### Tables

- **users** - User accounts
- **auth_sessions** - Login sessions (tokens)
- **chat_sessions** - Chat conversations
- **chat_messages** - Individual messages
- **oauth_states** - OAuth state tracking

### Backup

**Important**: Backup the database regularly!

```bash
# Create backup
cp data/cuttle_auth.db data/cuttle_auth_backup_$(date +%Y%m%d).db

# Restore from backup
cp data/cuttle_auth_backup_20231201.db data/cuttle_auth.db
```

## Security Considerations

### Password Security

- Passwords hashed with SHA-256
- Minimum 8 characters required
- Never stored in plain text
- Email addresses normalized (lowercase)

### Session Security

- Session tokens are 32-byte cryptographically random
- HTTP-only cookies (not accessible via JavaScript)
- 30-day expiration
- Automatic cleanup of expired sessions

### OAuth Security

- State tokens prevent CSRF attacks
- State tokens expire after 10 minutes
- Automatic verification of state
- Secure token exchange

### Production Recommendations

1. **Use HTTPS**: Enable `secure=True` for cookies
2. **Rotate secrets**: Change OAuth secrets periodically
3. **Monitor logs**: Watch for suspicious activity
4. **Backup database**: Regular automated backups
5. **Update dependencies**: Keep Flask and other packages updated

## Customization

### Session Duration

Change session expiration in `auth_db.py`:

```python
def create_auth_session(self, user_id: int, expires_hours: int = 720):
```

Default is 720 hours (30 days).

### Chat Interface

Modify the full-screen chat behavior in `web/js/auth.js`:

```javascript
function enterFullscreenChat() {
    // Customize what happens when user logs in
}
```

### User Avatar

Default avatars show user initials. To use profile images from OAuth:

```javascript
if (currentUser.profile_image) {
    userAvatar.innerHTML = `<img src="${currentUser.profile_image}">`;
}
```

## Troubleshooting

### "Invalid session" Error

- Session token expired or invalid
- User needs to log in again
- Check database for auth_sessions table

### OAuth Redirect Fails

- Verify redirect URI matches provider settings exactly
- Check OAuth credentials in `.env`
- Review server logs for detailed error

### Database Locked Error

- Another process is using the database
- Close all connections to `cuttle_auth.db`
- Restart the server

### Lost Admin Access

If you need to reset a password or delete a user:

```bash
# Connect to database
sqlite3 data/cuttle_auth.db

# List users
SELECT id, email, display_name FROM users;

# Delete a user (and all their data)
DELETE FROM users WHERE email = 'user@example.com';

# Exit
.quit
```

## Migration from Old System

If you have an existing Cuttle installation without authentication:

1. The old in-memory sessions still work for non-authenticated users
2. New users will automatically get database-backed sessions
3. Existing chat history is not migrated (stored in browser)
4. Users can create accounts and start fresh with persistent history

## Development

### Running Tests

```bash
# Test authentication endpoints
curl -X POST http://localhost:8080/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","password":"test123456","display_name":"Test User"}'
```

### Database Schema Changes

If you modify the schema in `auth_db.py`, delete the database to recreate:

```bash
rm data/cuttle_auth.db
python start_web_chat.py
```

### Adding New OAuth Providers

1. Add configuration to `OAUTH_CONFIGS` in `auth_api.py`
2. Add button to login form in `landing_page.html`
3. Follow OAuth 2.0 authorization code flow
4. Extract user email and profile from provider

## Support

For issues or questions:
1. Check this guide
2. Review [OAuth Setup Guide](OAUTH_SETUP.md)
3. Check server logs for errors
4. Verify database exists and is accessible

## License

Same as main Cuttle project.


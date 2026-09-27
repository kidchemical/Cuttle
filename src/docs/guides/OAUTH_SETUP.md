# OAuth Authentication Setup Guide

This guide explains how to configure OAuth authentication providers for Cuttle.

## Overview

Cuttle supports the following authentication methods:
1. **Email/Password** - Built-in authentication (no configuration needed)
2. **Google OAuth** - Sign in with Google
3. **Microsoft OAuth** - Sign in with Microsoft account
4. **Facebook OAuth** - Sign in with Facebook

## Linking Google to an existing local account

1. Sign in with your Cuttle username/password (e.g. `ian`).
2. Click the **Account** rail button → **Connect Google**.
3. Approve Google on this PC (`https://127.0.0.1:8080` redirect).
4. Your Google profile picture is saved on that local account. Phone clients keep
   using username/password — the avatar comes from `/api/auth/me`.

Requires `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` in `src/.env` and a Flask restart.

## Email/Password Authentication

Email/password authentication works out of the box with no configuration required. Users can:
- Register with email and password
- Login with their credentials
- All passwords are securely hashed using SHA-256

## OAuth Configuration

To enable OAuth providers, you need to:
1. Create OAuth apps with each provider
2. Add credentials to your `.env` file
3. Configure redirect URIs

### Google OAuth Setup

1. **Create a Google Cloud Project**:
   - Go to [Google Cloud Console](https://console.cloud.google.com/)
   - Create a new project or select an existing one
   - Enable the Google+ API

2. **Create OAuth 2.0 Credentials**:
   - Navigate to "APIs & Services" > "Credentials"
   - Click "Create Credentials" > "OAuth client ID"
   - Choose "Web application"
   - Authorized JavaScript origins (match how you open Cuttle):
     ```
     https://localhost:8080
     https://127.0.0.1:8080
     ```
   - Authorized redirect URIs (must match `OAUTH_REDIRECT_BASE` / `CUTTLE_API_URL`):
     ```
     https://127.0.0.1:8080/api/auth/callback/google
     https://localhost:8080/api/auth/callback/google
     ```
   - For production / LAN, also add your host:
     ```
     https://yourdomain.com/api/auth/callback/google
     ```

3. **Add to .env file** (`src/.env`):
   ```bash
   # Public origin Flask serves (HTTPS). OAuth redirects use this unless overridden.
   CUTTLE_API_URL=https://127.0.0.1:8080
   # Optional override if the browser host differs from CUTTLE_API_URL:
   # OAUTH_REDIRECT_BASE=https://localhost:8080

   GOOGLE_CLIENT_ID=your_client_id_here
   GOOGLE_CLIENT_SECRET=your_client_secret_here
   ```

### Microsoft OAuth Setup

1. **Register an Application**:
   - Go to [Azure Portal](https://portal.azure.com/)
   - Navigate to "Azure Active Directory" > "App registrations"
   - Click "New registration"
   - Name your app (e.g., "Cuttle")
   - Set supported account types to "Accounts in any organizational directory and personal Microsoft accounts"

2. **Configure Redirect URIs**:
   - In your app registration, go to "Authentication"
   - Add a platform > "Web"
   - Add redirect URIs:
     ```
     https://127.0.0.1:8080/api/auth/callback/microsoft
     https://localhost:8080/api/auth/callback/microsoft
     ```
   - For production:
     ```
     https://yourdomain.com/api/auth/callback/microsoft
     ```

3. **Create a Client Secret**:
   - Go to "Certificates & secrets"
   - Click "New client secret"
   - Copy the secret value (you won't be able to see it again!)

4. **Add to .env file**:
   ```bash
   MICROSOFT_CLIENT_ID=your_application_id_here
   MICROSOFT_CLIENT_SECRET=your_client_secret_here
   ```

### Facebook OAuth Setup

1. **Create a Facebook App**:
   - Go to [Facebook Developers](https://developers.facebook.com/)
   - Click "My Apps" > "Create App"
   - Choose "Consumer" as the app type
   - Fill in app details

2. **Configure Facebook Login**:
   - In your app dashboard, add "Facebook Login" product
   - Go to "Facebook Login" > "Settings"
   - Add valid OAuth redirect URIs:
     ```
     https://127.0.0.1:8080/api/auth/callback/facebook
     https://localhost:8080/api/auth/callback/facebook
     ```
   - For production:
     ```
     https://yourdomain.com/api/auth/callback/facebook
     ```

3. **Get App Credentials**:
   - Go to "Settings" > "Basic"
   - Copy your App ID and App Secret

4. **Add to .env file**:
   ```bash
   FACEBOOK_APP_ID=your_app_id_here
   FACEBOOK_APP_SECRET=your_app_secret_here
   ```

## .env File Example

Create a `.env` file in your project root with the following structure:

```bash
# OpenAI Configuration (for bot functionality)
OPENAI_API_KEY=sk-...

# Anthropic Configuration (for bot functionality)
ANTHROPIC_API_KEY=sk-ant-...

# Discord Bot (optional)
DISCORD_BOT_TOKEN=...

# Public API origin (HTTPS). OAuth redirect = {this}/api/auth/callback/{provider}
CUTTLE_API_URL=https://127.0.0.1:8080
# OAUTH_REDIRECT_BASE=https://localhost:8080

# Google OAuth (optional)
GOOGLE_CLIENT_ID=your_google_client_id
GOOGLE_CLIENT_SECRET=your_google_client_secret

# Microsoft OAuth (optional)
MICROSOFT_CLIENT_ID=your_microsoft_client_id
MICROSOFT_CLIENT_SECRET=your_microsoft_client_secret

# Facebook OAuth (optional)
FACEBOOK_APP_ID=your_facebook_app_id
FACEBOOK_APP_SECRET=your_facebook_app_secret
```

## Testing OAuth Locally

1. **Update Redirect URIs**:
   - Register `https://127.0.0.1:8080/api/auth/callback/{provider}` (and/or `https://localhost:8080/...`) in each provider console — must match `CUTTLE_API_URL` / `OAUTH_REDIRECT_BASE` exactly (scheme, host, port, path).

2. **Start the Server** (daemon or Flask on HTTPS :8080).

3. **Test Authentication**:
   - Open `https://127.0.0.1:8080` (or the same host as `OAUTH_REDIRECT_BASE`) in your browser
   - Click "Login" button
   - Try each OAuth provider
   - Check for any error messages

## Production Deployment

When deploying to production:

1. **Update Redirect URIs**: Add your production domain to all OAuth providers:
   - Google: `https://yourdomain.com/api/auth/callback/google`
   - Microsoft: `https://yourdomain.com/api/auth/callback/microsoft`
   - Facebook: `https://yourdomain.com/api/auth/callback/facebook`

2. **Set env**: `CUTTLE_API_URL=https://yourdomain.com` (or `OAUTH_REDIRECT_BASE=...`) — no code change needed

3. **Cookies**: Flask already sets `Secure` when the request is HTTPS (`_cookie_secure()` in `auth_api.py`). No manual cookie tweak needed.

4. **Use Environment Variables**: Never commit credentials to version control

## Troubleshooting

### OAuth Provider Not Working

1. **Check redirect URIs**: Make sure they match exactly in both your app and provider settings
2. **Verify credentials**: Ensure client ID and secret are correct in `.env`
3. **Check browser console**: Look for error messages
4. **Check server logs**: OAuth errors are logged to console

### "OAuth not configured" Error

- This means the client ID is missing from `.env`
- Add the required credentials and restart the server

### Email Permission Denied

- Some OAuth providers require explicit permission to access email
- Make sure email scope is requested in OAuth configuration

## Security Best Practices

1. **Keep secrets secret**: Never commit `.env` to version control
2. **Use HTTPS in production**: Always enable secure cookies
3. **Rotate secrets regularly**: Change OAuth secrets periodically
4. **Limit redirect URIs**: Only add URIs you actually use
5. **Monitor authentication logs**: Watch for suspicious activity

## Database

User data and chat sessions are stored in `data/cuttle_auth.db`. This SQLite database contains:
- User accounts (email, display name, auth provider)
- Authentication sessions (login tokens)
- Chat sessions and messages

**Backup regularly** in production!

## Support

If you encounter issues:
1. Check the [OAuth provider documentation](#oauth-configuration)
2. Review server logs for error messages
3. Verify your configuration matches this guide


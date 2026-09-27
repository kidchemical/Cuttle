# Cuttle Setup Instructions

## Quick Setup

1. **Install Dependencies**
   ```bash
   pip install -r requirements/requirements.txt
   ```

2. **Setup Environment Variables**
   ```bash
   python setup_env.py
   ```
   This will guide you through creating the `.env` file with:
   - Discord Bot Token
   - Your Discord User ID
   - OpenAI API Key (optional)

3. **Run the System**
   ```bash
   python launcher.py
   ```

## Manual Setup

If you prefer to set up manually:

1. **Create `.env` file** in the project root with:
   ```
   DISCORD_TOKEN=your_discord_bot_token_here
   OWNER_ID=your_discord_user_id_here
   OPENAI_API_KEY=your_openai_api_key_here
   ```
   **Note:** Leave `OPENAI_API_KEY` empty to run without AI features (bot will still work with basic commands)

2. **Get Discord Bot Token:**
   - Go to https://discord.com/developers/applications
   - Create a new application or select existing one
   - Go to 'Bot' section and copy the token

3. **Get Your Discord User ID:**
   - In Discord, enable Developer Mode (User Settings > Advanced > Developer Mode)
   - Right-click your username and select 'Copy User ID'

4. **Get OpenAI API Key (Optional):**
   - Go to https://platform.openai.com/api-keys
   - Create a new API key
   - **Skip this step** if you want to use basic commands only

## Running the System

- **Full System:** `python launcher.py` (starts both Discord bot and web API)
- **Discord Bot Only:** `python bot_deprecated.py`
- **Web API Only:** `python web_chat_api.py`
- **Debug Mode:** `python launcher_debug.py`
- **Install Dependencies:** `python install_deps_step_by_step.py`
- **Fix Virtual Environment:** `python fix_venv.py`

## Troubleshooting

### Common Issues

1. **"No module named 'openai'"**
   - Run: `pip install openai`

2. **"No module named 'pyautogui'"**
   - Run: `pip install pyautogui`

3. **"No module named 'mss'"**
   - Run: `pip install mss`

4. **"No module named 'pytesseract'"**
   - Run: `pip install pytesseract`

5. **Discord Bot not responding**
   - Check your Discord token in `.env`
   - Make sure the bot has proper permissions in your Discord server

6. **Web API 500 errors**
   - Check that all dependencies are installed
   - Verify your `.env` file is properly configured
   - Check the console output for detailed error messages

### Getting Help

If you encounter issues:
1. Check the console output for error messages
2. Verify all dependencies are installed
3. Ensure your `.env` file is properly configured
4. Try running individual components (bot_deprecated.py or web_chat_api.py) to isolate issues

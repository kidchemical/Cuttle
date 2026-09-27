# Cuttle Electron - Launch Guide

## Quick Start

### 1. Launch the Application
Simply run: `/path/to/Cuttle/electron\dist\win-unpacked\Cuttle.exe`

The application will:
- Start the Python Flask backend automatically
- Open the Cuttle interface in a native window
- Connect to localhost:8080 for the backend

### 2. Prerequisites
**Required:**
- Python 3.8+ installed and in PATH
- Python dependencies installed:
  ```bash
  pip install -r src/requirements/requirements.txt
  ```

**For AI Features:**
- API keys configured (see below)

## Setting Up API Keys

To use AI features (Claude, GPT-4, etc.), you need to configure API keys:

### Option 1: Environment Variables (Recommended for Development)
Set these environment variables before launching:
```powershell
# In PowerShell
$env:ANTHROPIC_API_KEY="sk-ant-..."
$env:OPENAI_API_KEY="sk-..."

# Then launch
.\Cuttle.exe
```

### Option 2: .env File (Recommended for Distribution)
1. Create a file named `.env` in `/path/to/Cuttle/src\`
2. Add your API keys:
```
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
```

Get API keys from:
- **Anthropic (Claude)**: https://console.anthropic.com/
- **OpenAI (GPT-4)**: https://platform.openai.com/api-keys

## Troubleshooting

### Error: "Failed to start Cuttle server: spawn"
- **Cause**: Python is not installed or not in PATH
- **Fix**: Install Python from python.org and ensure it's added to PATH

### Error: "Flask server failed to start after 30 seconds"
- **Cause**: Missing Python dependencies
- **Fix**: Run `pip install -r src/requirements/requirements.txt`

### Flask Server Starts but AI Features Don't Work
- **Cause**: API keys not configured
- **Fix**: Set up API keys using one of the methods above

### Check Server Logs
The Electron app outputs server logs to the console. To see them:
1. Open the app
2. Press `Ctrl+Shift+I` to open Developer Tools
3. Check the Console tab for Python output

## Building for Distribution

To create a distributable installer:

```bash
cd electron
npm run build        # Creates both installer and portable
npm run build:dir    # Creates unpacked directory (for testing)
```

Output location: `electron/dist/`

## Files Included in Build

The Electron app bundles:
- ✅ All Python source files
- ✅ Web interface (HTML/CSS/JS)
- ✅ Static assets
- ❌ Python runtime (must be installed separately)
- ❌ Python dependencies (must be installed separately)

## Next Steps

For a fully standalone app that doesn't require Python installation:
1. Consider using PyInstaller to bundle Python
2. Or bundle a portable Python distribution
3. See `BUILD_TROUBLESHOOTING.md` for advanced packaging options


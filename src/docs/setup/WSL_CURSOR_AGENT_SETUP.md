# WSL and Cursor-Agent Setup Guide

## Overview

The Cuttle Discord bot uses cursor-agent via WSL Ubuntu as the primary method for processing coding requests. This provides direct file manipulation and better integration than the Cursor IDE UI automation.

## Prerequisites

- Windows 10/11 with WSL support
- Administrator privileges for installation

## Step 1: Install WSL Ubuntu

1. **Open PowerShell as Administrator**
2. **Install WSL with Ubuntu:**
   ```powershell
   wsl --install -d Ubuntu
   ```
3. **Restart your computer** when prompted
4. **After restart, set up Ubuntu:**
   - Create a username and password when prompted
   - Update the system:
     ```bash
     sudo apt update && sudo apt upgrade -y
     ```

## Step 2: Install Cursor-Agent

1. **Open WSL Ubuntu terminal**
2. **Install cursor-agent:**
   ```bash
   curl -fsSL https://cursor.sh/install.sh | sh
   ```
3. **Verify installation:**
   ```bash
   cursor-agent --help
   ```
4. **Test non-interactive mode:**
   ```bash
   cursor-agent -p "Hello, this is a test" --output-format text
   ```

## Step 3: Test the Setup

1. **From Windows PowerShell, test WSL:**
   ```powershell
   wsl --status
   ```

2. **Test cursor-agent from Windows:**
   ```powershell
   wsl -- cursor-agent --help
   ```

3. **Test non-interactive mode from Windows:**
   ```powershell
   wsl -- cursor-agent -p "Test non-interactive mode" --output-format text
   ```

## Step 4: Configure Project Paths

The bot will automatically look for projects in these locations:
- `/mnt/f/Game Dev/Escape-Purgatory/source`
- `/mnt/f/dev`
- `/mnt/c/dev`
- Current directory

Make sure your projects are accessible from WSL.

## Troubleshooting

### WSL Not Working
- Ensure Windows Subsystem for Linux feature is enabled
- Check if virtualization is enabled in BIOS
- Try: `wsl --install` without specifying distribution

### Cursor-Agent Not Found
- Verify the installation path: `/home/[username]/.local/bin/cursor-agent`
- Check if the PATH includes `~/.local/bin`
- Try: `export PATH="$HOME/.local/bin:$PATH"`

### Permission Issues
- Make sure cursor-agent is executable: `chmod +x ~/.local/bin/cursor-agent`
- Check file permissions in your project directory

## Alternative: Cursor IDE UI Automation

If WSL/cursor-agent setup is not possible, you can use the fallback method:

```
/cursor-ui "your prompt here"
```

This uses Windows automation to send prompts directly to Cursor IDE.

## Testing the Integration

Once setup is complete, test with the Discord bot:

1. **Start a cursor-agent session:**
   ```
   /cursor "test project"
   ```

2. **Send a coding request:**
   ```
   create a text file that says 'hello world'
   ```

3. **Check the response** - it should show successful processing by cursor-agent

## Benefits of Cursor-Agent Integration

- **Non-interactive mode** - Runs without user prompts using `-p` flag
- **Direct file manipulation** - No need to open Cursor IDE
- **Faster processing** - Direct CLI execution
- **Better integration** - Works with any project structure
- **Automated workflows** - Can be integrated into build pipelines
- **Cross-platform** - Works on Windows, Linux, and macOS
- **Text output format** - Clean output suitable for automation

## Support

If you encounter issues:
1. Check the Discord bot console output for detailed error messages
2. Verify WSL and cursor-agent are working independently
3. Test with simple commands first before complex requests
4. Use `/cursor-ui` as a fallback for immediate needs

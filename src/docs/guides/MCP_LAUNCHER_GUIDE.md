# MCP Launcher Guide

## Quick Start with MCP (Now Default!)

MCP (Model Context Protocol) is now the default and recommended way to run Cuttle! Here's how to use it:

### 🚀 Simple Usage

1. **Run the launcher:**
   ```bash
   python launcher.py
   ```

2. **MCP options are now at the top:**
   - **Option 1**: MCP Full System (Recommended) ⭐
   - **Option 2**: MCP Discord Bot Only
   - **Option 3**: Test MCP Conversion
   - **Option 4**: Install MCP Dependencies

### 🎯 Command Line Usage

**MCP is now the default:**
```bash
python launcher.py          # Starts with MCP automatically
```

**For legacy mode (deprecated):**
```bash
python launcher.py --legacy
python launcher.py -l
```

### 📋 Menu Options

| Option | Description |
|--------|-------------|
| **1** | **MCP Full System (Recommended)** ⭐ |
| **2** | **MCP Discord Bot Only** |
| **3** | **Test MCP Conversion** |
| **4** | **Install MCP Dependencies** |
| 5 | Web Chat API Only |
| 6 | WSL Discord Bot |
| 7 | Debug Mode |
| 8 | Environment Setup |
| 9 | Run Safe Tests |
| A | Fix Virtual Environment |
| **B** | **Legacy Full System (Deprecated)** |
| **C** | **Legacy Discord Bot (Deprecated)** |
| **D** | **Legacy Dependencies (Deprecated)** |
| 0 | Exit |

### 🔧 What Happens Automatically

MCP is now the default! The launcher will:

1. **Always install MCP dependencies** - MCP dependencies are installed by default
2. **Start the MCP bot** - launches `bot_mcp.py` by default
3. **Show MCP status** - displays "MCP Discord Bot: Active" in the status
4. **Provide better error handling** - tools run in isolated MCP server process

### 🆚 MCP vs Legacy

| Feature | Legacy Bot | MCP Bot |
|---------|------------|---------|
| **Tool Integration** | Direct imports | MCP protocol |
| **Error Isolation** | Can crash bot | Isolated in server |
| **Reliability** | Basic | Enhanced |
| **AI Integration** | Limited | Future-ready |
| **Commands** | Same | Same |
| **Performance** | Good | Better |

### 🛠️ Troubleshooting

**MCP Dependencies Missing:**
- Select option **A** to install MCP dependencies
- Or run: `python setup_mcp_simple.py`

**MCP Bot Won't Start:**
- Select option **D** to test MCP conversion
- Check that all tool modules are available
- Fall back to legacy bot (option 1 or 2)

**Want to Test MCP:**
- Select option **D** to run comprehensive MCP tests
- This will validate the entire MCP setup

### 📚 All Discord Commands Work the Same

Whether you use the legacy bot or MCP bot, all your Discord commands work exactly the same:

- `/screenshot unity` - Take screenshot of Unity window
- `/process list` - List running processes  
- `/window focus Unity` - Focus Unity window
- `/input click 100,200` - Click at coordinates
- `/system info` - Get system information
- `/tools` - List available tools (MCP shows more tools)

### 🎉 Benefits of MCP

- **Better Error Handling** - Tool failures don't crash the bot
- **Improved Performance** - Tools run in separate process
- **Future-Ready** - Ready for advanced AI integrations
- **Backward Compatible** - All existing commands work
- **Easy Testing** - Built-in test suite

### 💡 Pro Tips

1. **Just Run It!** - Simply run `python launcher.py` and select option **1** (MCP Full System)
2. **Want to Test?** - Select option **3** to validate everything works
3. **Having Issues?** - Fall back to option **B** (legacy) while troubleshooting
4. **Need Help?** - Select option **3** to run comprehensive MCP tests

MCP is now the default and provides better reliability, error handling, and future AI integration capabilities!

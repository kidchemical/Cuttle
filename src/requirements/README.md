# Requirements Directory

This directory contains all Python dependency files for the Cuttle project.

## Files

### `requirements.txt`
**Purpose**: Main dependencies for Cuttle  
**Usage**: Standard Windows installation  
**Install**: `pip install -r requirements/requirements.txt`

**Includes**:
- Discord.py for bot functionality
- OpenAI and Anthropic for AI integration
- Image processing libraries (PIL, OpenCV, pytesseract)
- System automation libraries (psutil, mss)
- Windows automation tools (pywin32, pyautogui, etc.)

### `requirements-mcp.txt`

**Archived.** Cuttle-as-MCP-server is retired. The file lives at
`docs/archive/deprecated-cuttle-mcp/requirements-mcp.txt`. Do not install it
for normal Cuttle. Guest harnesses (Cursor/Codex) manage their own MCP deps.

### `requirements-wsl.txt`
**Purpose**: Dependencies for WSL (Windows Subsystem for Linux)  
**Usage**: When running in WSL environment  
**Install**: `pip install -r requirements/requirements-wsl.txt`

**Excludes**:
- Windows-only libraries (pywin32, pyautogui, etc.)
- GUI automation tools not supported in WSL

## Installation

### Standard Installation (Windows)
```bash
pip install -r requirements/requirements.txt
```

### MCP Installation (Recommended)
```bash
pip install -r requirements/requirements-mcp.txt
```

### WSL Installation
```bash
pip install -r requirements/requirements-wsl.txt
```

## Updating Dependencies

When adding new dependencies:

1. Add to the appropriate requirements file(s)
2. Specify minimum version: `package>=1.0.0`
3. Test installation on clean environment
4. Update this README if needed

## Dependency Groups

### Core Dependencies
Required for all installations:
- discord.py - Discord bot framework
- python-dotenv - Environment variable management
- openai - OpenAI API client
- anthropic - Anthropic Claude API client

### Image Processing
- pillow - Image manipulation
- pytesseract - OCR text extraction
- opencv-python - Computer vision

### System Automation
- psutil - Process and system monitoring
- mss - Fast screenshot capture

### Windows Automation
(Not included in WSL requirements)
- pywin32 - Windows API access
- pyautogui - GUI automation
- pygetwindow - Window management
- pywinauto - Advanced window control
- keyboard - Keyboard automation
- mouse - Mouse automation

### MCP Specific
- mcp - Model Context Protocol library
- Additional protocol dependencies

## Troubleshooting

### Installation Issues

**Error: "Could not find a version that satisfies the requirement"**
- Update pip: `python -m pip install --upgrade pip`
- Check Python version (3.8+ required)

**Error: "Microsoft Visual C++ 14.0 is required"**
- Install Visual Studio Build Tools
- Or use pre-built wheels: `pip install --only-binary :all: package-name`

**Error: "pywin32 installation failed"**
- Only installs on Windows
- Use `requirements-wsl.txt` for WSL environments

### Dependency Conflicts

If you encounter conflicts:
1. Create a fresh virtual environment
2. Install requirements in a clean state
3. Update conflicting packages to compatible versions

### Version Updates

To update all dependencies:
```bash
pip install --upgrade -r requirements/requirements.txt
```

To check outdated packages:
```bash
pip list --outdated
```

## Development Dependencies

For development, you may also want:
```bash
pip install pytest pytest-asyncio black flake8
```

## Notes

- Requirements files are intentionally verbose (one package per line) for clarity
- Version pins use `>=` to allow updates while ensuring minimum versions
- Some dependencies are optional (will gracefully fail if not available)
- The bot includes fallback modes when optional dependencies are missing

## Support

If you encounter dependency issues:
1. Check Python version: `python --version` (3.8+ required)
2. Update pip: `python -m pip install --upgrade pip`
3. Try installing individual packages to identify conflicts
4. Use `pip install -v` for verbose output
5. Check the [Setup Instructions](../docs/setup/SETUP_INSTRUCTIONS.md)

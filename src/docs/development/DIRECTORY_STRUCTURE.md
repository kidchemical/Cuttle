# Cuttle Directory Structure

This document describes the organized directory structure of the Cuttle project.

## 📁 Main Directory Structure

```
F:\dev\Cuttle\
├── 📁 tools/                    # Advanced automation tools
│   ├── process/                 # Process management (psutil)
│   ├── window/                  # Window management (pygetwindow)
│   ├── input/                   # Input automation (pyautogui, keyboard, mouse)
│   ├── screenshot/              # Screenshot tools (mss)
│   ├── ocr/                     # OCR/text extraction (pytesseract + opencv)
│   ├── windows/                 # Windows integration (pywin32)
│   ├── unity/                   # Unity-specific tools (planned)
│   ├── discord/                 # Discord utilities (planned)
│   ├── system/                  # General system utilities (planned)
│   ├── __init__.py              # Tools package initialization
│   └── TOOLS_README.md          # Comprehensive tools documentation
├── 📁 tests/                    # All test files
│   ├── test_process_tools.py    # Process management tests
│   ├── test_window_tools.py     # Window management tests
│   ├── test_input_tools.py      # Input automation tests
│   ├── test_screenshot_tools.py # Screenshot tool tests
│   ├── test_ocr_tools.py        # OCR tool tests
│   ├── test_windows_tools.py    # Windows tool tests
│   ├── test_security.py         # Security tests
│   ├── test_security_standalone.py
│   ├── test_username_case.py    # Username case tests
│   ├── test_username_case_standalone.py
│   ├── verify_security.py       # Security verification
│   ├── run_all_tests.py         # Test runner
│   └── __init__.py
├── 📁 cache/                    # Cache files
│   ├── discord_cache.json       # Discord RAG cache
│   └── README.md                # Cache directory documentation
├── 📁 rag/                      # RAG pipeline components
│   ├── discord_rag.py           # Discord RAG system
│   ├── __init__.py              # RAG package initialization
│   └── README.md                # RAG documentation
├── 📁 output/                   # Generated files and outputs
│   ├── temp/                    # Temporary files
│   │   ├── *.png                # Screenshots
│   │   ├── *.txt                # Temporary text files
│   │   ├── *.json               # Temporary JSON files
│   │   └── ...                  # Other temporary files
│   └── README.md                # Output directory documentation
├── 🦑 bot_deprecated.py                    # Main Discord bot
├── 🧠 ai_agent.py               # AI agent logic
├── 🔧 tool_manager.py           # Unified tool interface
├── ⚙️ config.py                 # Configuration management
├── 🔄 multi_stage_processor.py  # Multi-stage command processing
├── 🖥️ interactive_bot_deprecated.py        # Interactive terminal interface
├── 📋 bot_config.json           # Bot configuration
├── 💾 discord_cache.json        # Discord RAG cache
├── 📚 TOOLS_README.md           # Comprehensive tools documentation
├── 📁 DIRECTORY_STRUCTURE.md    # This file
├── 🚫 .gitignore                # Git ignore rules
└── 📁 __pycache__/              # Python cache (auto-generated)
```

## 📂 Directory Purposes

### `tools/` - Advanced Automation Tools
**Purpose**: Modular automation tool system  
**Contents**: Specialized tool modules for different automation tasks, comprehensive documentation  
**Usage**: Import specific tools as needed, e.g., `from tools.process import kill_process`
**Documentation**: See `tools/TOOLS_README.md` for complete API reference

### `tests/` - Test Suite
**Purpose**: Comprehensive testing for all tool modules  
**Contents**: Unit tests, integration tests, example prompt tests  
**Usage**: Run `python tests/run_all_tests.py` to test all modules

### `cache/` - Cache Files
**Purpose**: Storage for cache data to improve performance  
**Contents**: 
- `discord_cache.json` - Discord RAG cache
- Future cache files as needed
**Usage**: Automatically managed by RAG system, improves AI response quality

### `rag/` - RAG Pipeline Components
**Purpose**: Retrieval-Augmented Generation system components  
**Contents**: 
- `discord_rag.py` - Discord RAG implementation
- Future RAG components as needed
**Usage**: Enhances AI responses with relevant context from cached data

### `output/` - Generated Files
**Purpose**: Storage for temporary and generated files  
**Contents**: 
- `temp/` - Temporary files (screenshots, OCR results, etc.)
- Future subdirectories for different output types
**Usage**: Automatically managed by the bot, files can be safely deleted

### `docs/` - Documentation
**Purpose**: Organized project documentation  
**Contents**: 
- `setup/` - Installation and configuration guides
- `guides/` - User guides and tutorials
- `development/` - Technical documentation for developers
**Usage**: Reference for setup, usage, and development

### Root Directory - Core Files
**Purpose**: Main bot files and configuration  
**Contents**: Bot logic, AI agent, configuration, launchers  
**Usage**: Main entry points for running the bot

## 🔄 File Organization Rules

### ✅ Files That Belong Here
- **Core bot files**: `bot_deprecated.py`, `ai_agent.py`, `tool_manager.py`
- **Configuration**: `config.py`, `bot_config.json`, `.env`
- **Launchers**: `launcher.py`, `launcher_debug.py`

### ✅ Files That Belong in `tools/`
- **Tool modules**: All automation functionality
- **Specialized modules**: Process, window, input, screenshot, OCR, Windows tools
- **Tool documentation**: `TOOLS_README.md` for comprehensive API reference
- **Future tools**: Unity, Discord, system utilities

### ✅ Files That Belong in `tests/`
- **All test files**: `test_*.py`, `*_test.py`
- **Verification scripts**: Security, functionality verification
- **Test runners**: Comprehensive test execution

### ✅ Files That Belong in `cache/`
- **Cache files**: `discord_cache.json`, other cache data
- **Persistent caches**: Long-term storage for performance
- **Cache metadata**: Cache configuration and status files

### ✅ Files That Belong in `rag/`
- **RAG components**: `discord_rag.py`, other RAG implementations
- **RAG utilities**: Helper functions for retrieval systems
- **RAG configuration**: Settings for RAG pipelines

### ✅ Files That Belong in `output/`
- **Temporary files**: Screenshots, OCR results, temporary data
- **Generated files**: Files created during bot operation
- **Non-persistent files**: Files that can be safely deleted

### ✅ Files That Belong in `docs/`
- **Setup guides**: Installation and configuration documentation
- **User guides**: Tutorials and how-to guides
- **Development docs**: Architecture, API references, technical documentation
- **Main README**: Primary project documentation

## 🚫 Files That Should NOT Be in Root

### ❌ Test Files
- ~~`test_security.py`~~ → `tests/test_security.py`
- ~~`test_username_case.py`~~ → `tests/test_username_case.py`
- ~~`verify_security.py`~~ → `tests/verify_security.py`

### ❌ Temporary Files
- ~~`temp_screenshot_*.png`~~ → `output/temp/`
- ~~`temp_cursor_prompt_*.txt`~~ → `output/temp/`
- ~~`cursor_ai_request_*.json`~~ → `output/temp/`

### ❌ Generated Files
- Any files created during bot operation should go in `output/`
- Screenshots, OCR results, temporary data

### ❌ Documentation Files
- ~~`SETUP_INSTRUCTIONS.md`~~ → `docs/setup/`
- ~~`MCP_LAUNCHER_GUIDE.md`~~ → `docs/guides/`
- ~~`NODE_EDITOR_GUIDE.md`~~ → `docs/guides/`
- ~~`MCP_CONVERSION_SUMMARY.md`~~ → `docs/development/`
- All `*.md` files should be organized in `docs/`

## 🧹 Cleanup Guidelines

### Regular Cleanup
- **`output/temp/`**: Can be cleaned regularly, files are regenerated as needed
- **`cache/`**: Cache files can be deleted (will be regenerated), but affects performance
- **`__pycache__/`**: Python automatically manages this, safe to delete
- **Test files**: Keep in `tests/` directory, don't delete

### Git Management
- **`.gitignore`**: Excludes `output/`, `cache/`, `__pycache__/`, temporary files
- **Version control**: Only track source code, not generated files
- **Dependencies**: Track in `requirements/` directory or `install_deps_step_by_step.py`

## 🔧 Maintenance

### Adding New Tools
1. Create new module in `tools/[category]/`
2. Add tests in `tests/test_[category]_tools.py`
3. Update `tool_manager.py` to integrate new tools
4. Update `tools/TOOLS_README.md` with new tool documentation

### Adding New Tests
1. Place test files in `tests/` directory
2. Follow naming convention: `test_*.py`
3. Update `tests/run_all_tests.py` if needed

### Adding New Output Types
1. Create subdirectory in `output/` if needed
2. Update tool modules to use new directory
3. Update `.gitignore` if necessary

---

*This directory structure provides a clean, organized, and maintainable codebase for the Cuttle Discord assistant.*

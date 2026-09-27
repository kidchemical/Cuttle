# Cuttle Tests

This directory contains all tests for the Cuttle project, organized into unit tests and integration tests.

## Test Structure

```
tests/
├── unit/                    # Unit tests for individual components
│   ├── test_*.py           # Individual tool tests
│   └── __init__.py
├── integration/            # Integration tests for system behavior
│   ├── test_bot_startup.py # Bot startup and initialization tests
│   ├── test_discord_bot_integration.py # Discord bot functionality tests
│   ├── test_wsl_integration.py # WSL and cursor-agent integration tests
│   └── __init__.py
├── run_all_tests.py        # Main test runner
└── README.md              # This file
```

## Running Tests

### Windows (Recommended)
```bash
# Run all tests using Python
python run_all_tests.py
```

### WSL Direct
```bash
# Run tests directly in WSL
cd /mnt/f/dev/pc\ bot
python tests/run_all_tests.py
```

### Python Direct
```bash
# Run tests directly with Python
cd "F:\dev\Cuttle"
python tests/run_all_tests.py
```

## Test Categories

### Unit Tests
- **Process Management**: Tests for process launching and management
- **Window Management**: Tests for window operations and focus
- **Input Automation**: Tests for keyboard and mouse automation
- **Screenshot Tools**: Tests for screenshot capture and processing
- **OCR Tools**: Tests for optical character recognition
- **Windows Tools**: Tests for Windows-specific functionality
- **Security Tests**: Tests for security and permission checks
- **Username Case Tests**: Tests for username handling
- **Claude Code Integration**: Tests for Claude Code CLI integration and command parsing

### Integration Tests
- **Bot Startup**: Tests complete bot initialization and environment setup
- **Discord Bot Integration**: Tests Discord bot functionality and message handling
- **WSL Integration**: Tests WSL environment and cursor-agent integration

## Test Summary

The test runner provides a comprehensive summary including:

- **Overall Statistics**: Total tests, success rate, and failure count
- **Unit Test Results**: Individual component test results
- **Integration Test Results**: System-level test results
- **Individual Test Files**: Results from running test files directly
- **Debug Information**: Python version, working directory, and project structure
- **Environment Information**: OS, platform, and dependency availability
- **Common Issues Check**: Automated checks for common configuration problems

## Debugging

The test summary includes debugging information to help identify issues:

### Environment Checks
- ✅/❌ .env file exists
- ✅/❌ bot_config.json exists
- ✅/❌ WSL is available
- ✅/❌ discord.py is available
- ✅/❌ openai is available
- ✅/❌ Other dependencies are available

### Common Issues
- Missing dependencies
- Missing environment variables
- WSL not installed or configured
- Windows-specific features not available
- Network connectivity issues
- Discord bot token not configured
- OpenAI API key not configured

## Test Files

### Unit Tests
- `test_process_tools.py` - Process management functionality
- `test_window_tools.py` - Window operations and focus
- `test_input_tools.py` - Input automation (keyboard/mouse)
- `test_screenshot_tools.py` - Screenshot capture and processing
- `test_ocr_tools.py` - Optical character recognition
- `test_windows_tools.py` - Windows-specific tools
- `test_security.py` - Security and permission checks
- `test_username_case.py` - Username handling
- `test_api_key.py` - API key validation
- `test_openai_connection.py` - OpenAI API connectivity
- `test_cursor_command.py` - Cursor command functionality
- `test_cursor_newwindow.py` - Cursor new window functionality
- `test_window_focus.py` - Window focus operations
- `test_claude_code.py` - Claude Code CLI integration and command parsing

### Integration Tests
- `test_bot_startup.py` - Complete bot startup process
- `test_discord_bot_integration.py` - Discord bot functionality
- `test_wsl_integration.py` - WSL and cursor-agent integration

## Dependencies

Tests require the following Python packages:
- discord.py
- openai
- python-dotenv
- pillow
- psutil
- mss
- pytesseract
- opencv-python

## Environment Variables

Tests may require these environment variables:
- `DISCORD_TOKEN` - Discord bot token
- `OWNER_ID` - Discord user ID of the bot owner
- `OPENAI_API_KEY` - OpenAI API key (optional)

## WSL Requirements

For WSL integration tests:
- WSL must be installed and configured
- cursor-agent should be installed in WSL (optional)
- Python virtual environment in WSL

## Troubleshooting

### Test Failures
1. Check the test summary for specific failure reasons
2. Verify all dependencies are installed
3. Check environment variables are set correctly
4. Ensure WSL is properly configured (for WSL tests)
5. Check network connectivity (for API tests)

### Common Solutions
- Install missing dependencies: `pip install -r requirements/requirements.txt`
- Create .env file with required variables
- Install WSL: `wsl --install`
- Install cursor-agent in WSL: `curl -fsSL https://cursor.sh/install.sh | sh`

## Contributing

When adding new tests:
1. Place unit tests in `tests/unit/`
2. Place integration tests in `tests/integration/`
3. Follow the naming convention `test_*.py`
4. Include comprehensive error handling
5. Add test functions to the appropriate test runner
6. Update this README if adding new test categories

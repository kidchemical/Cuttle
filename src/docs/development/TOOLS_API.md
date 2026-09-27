# Cuttle Advanced Tools System

This document describes the comprehensive automation tool system for the Cuttle Discord assistant.

## 🏗️ Architecture

The tools are organized into specialized modules under the `tools/` directory:

```
tools/
├── __init__.py                 # Package initialization
├── process/                    # Process & Task Management
│   ├── __init__.py
│   └── process_manager.py      # psutil-based process operations
├── window/                     # Window Management
│   ├── __init__.py
│   └── window_manager.py       # pygetwindow/pywinauto window operations
├── input/                      # Input Automation
│   ├── __init__.py
│   └── input_automation.py     # pyautogui, keyboard, mouse automation
├── screenshot/                 # Screenshot Tools
│   ├── __init__.py
│   └── screenshot_manager.py   # mss-based fast screenshot capture
├── ocr/                        # OCR & Text Extraction
│   ├── __init__.py
│   └── ocr_manager.py          # pytesseract + opencv text extraction
├── windows/                    # Windows Integration
│   ├── __init__.py
│   └── windows_manager.py      # pywin32 deep Windows integration
├── unity/                      # Unity-specific tools (planned)
├── discord/                    # Discord utilities (planned)
└── system/                     # General system utilities (planned)
```

## 🛠️ Tool Categories

### 1. Process Management (`tools.process`)
**Purpose**: Manage processes, tasks, and system resources  
**Dependencies**: `psutil`

**Example Commands**:
- `Kill Unity task` → `kill_unity_process()`
- `List running processes` → `list_processes(limit=20)`
- `Find Python processes` → `find_process("python")`
- `Show system resources` → `get_system_resources()`
- `Monitor Unity for 10 seconds` → `monitor_process("Unity", 10)`

**Functions**:
- `kill_process(process_name, force=False)` - Kill processes by name
- `list_processes(filter_name=None, limit=20)` - List running processes
- `find_process(process_name)` - Find processes by name
- `get_process_info(pid)` - Get detailed process information
- `monitor_process(process_name, duration=30)` - Monitor process resource usage
- `kill_unity_process()` - Kill all Unity-related processes
- `get_system_resources()` - Get CPU, memory, disk usage

### 2. Window Management (`tools.window`)
**Purpose**: Find, control, and manage windows  
**Dependencies**: `pygetwindow`, `pywinauto`

**Example Commands**:
- `Switch to Unity and type play` → `switch_to_window("Unity")`
- `Find Unity window` → `find_window("Unity")`
- `List all windows` → `list_windows(visible_only=True)`
- `Minimize Unity window` → `minimize_window("Unity")`
- `Get Unity window info` → `get_window_info("Unity")`

**Functions**:
- `find_window(title, exact_match=False)` - Find window by title
- `list_windows(filter_title=None, visible_only=True)` - List all windows
- `focus_window(title)` - Focus a window
- `minimize_window(title)` - Minimize a window
- `maximize_window(title)` - Maximize a window
- `restore_window(title)` - Restore minimized window
- `close_window(title, force=False)` - Close a window
- `move_window(title, x, y)` - Move window to coordinates
- `resize_window(title, width, height)` - Resize window
- `get_window_info(title)` - Get detailed window information
- `get_active_window()` - Get currently active window
- `switch_to_window(title)` - Switch to and focus window

### 3. Input Automation (`tools.input`)
**Purpose**: Mouse and keyboard automation  
**Dependencies**: `pyautogui`, `keyboard`, `mouse`

**Example Commands**:
- `Click at (100, 100)` → `click_at(100, 100)`
- `Right-click at (200, 200)` → `right_click(200, 200)`
- `Type "hello world"` → `type_text("hello world")`
- `Press Enter key` → `send_key("enter")`
- `Press Ctrl+C` → `send_key_combination("ctrl", "c")`
- `Drag from (100, 100) to (200, 200)` → `drag_mouse(100, 100, 200, 200, 0.5)`

**Functions**:
- `click_at(x, y, button="left", clicks=1)` - Click at coordinates
- `click_window(window_title, x_offset=None, y_offset=None)` - Click on window
- `double_click(x, y)` - Double-click at coordinates
- `right_click(x, y)` - Right-click at coordinates
- `drag_mouse(start_x, start_y, end_x, end_y, duration=1.0)` - Drag mouse
- `scroll_mouse(x, y, scrolls=3, direction="up")` - Scroll mouse wheel
- `send_keys(text, interval=0.05)` - Send keystrokes
- `send_key(key)` - Send single key press
- `send_key_combination(*keys)` - Send key combination
- `type_text(text, interval=0.05, clear_first=False)` - Type text
- `press_key(key, duration=0.1)` - Press and hold key
- `move_mouse(x, y, duration=0.5)` - Move mouse to coordinates
- `get_mouse_position()` - Get current mouse position

### 4. Screenshot Tools (`tools.screenshot`)
**Purpose**: Fast, advanced screenshot capture  
**Dependencies**: `mss`, `PIL`

**Example Commands**:
- `Take screenshot of Unity console` → `take_window_screenshot("Unity")`
- `Take screenshot of desktop` → `take_screenshot()`
- `Take screenshot of region (100, 100, 800, 600)` → `take_region_screenshot(100, 100, 800, 600)`
- `Take 5 screenshots with 2 second intervals` → `take_multiple_screenshots(5, 2.0)`

**Functions**:
- `take_screenshot(monitor=1, save_path=None)` - Take full screen screenshot
- `take_window_screenshot(window_title, save_path=None)` - Screenshot specific window
- `take_region_screenshot(left, top, width, height, save_path=None)` - Screenshot region
- `take_multiple_screenshots(count, interval=1.0, monitor=1)` - Take multiple screenshots
- `save_screenshot(image, save_path)` - Save PIL Image to file
- `capture_screen_region(region)` - Capture region to PIL Image
- `get_screen_info()` - Get monitor information

### 5. OCR & Text Extraction (`tools.ocr`)
**Purpose**: Extract text from images and screenshots  
**Dependencies**: `pytesseract`, `opencv-python`, `PIL`

**Example Commands**:
- `Read errors from Unity console` → `extract_text_from_window("Unity", preprocessing="unity")`
- `Take screenshot of Unity console` → `extract_text_from_screenshot(preprocessing="console")`
- `Find "error" text in screenshot` → `find_text_in_image("image.png", "error")`
- `Get text confidence from Unity console` → `get_text_confidence("image.png")`

**Functions**:
- `extract_text_from_image(image_path, language="eng", preprocessing="default")` - Extract text from image
- `extract_text_from_screenshot(monitor=1, language="eng", preprocessing="default")` - Extract text from screenshot
- `extract_text_from_window(window_title, language="eng", preprocessing="default")` - Extract text from window
- `extract_text_from_region(left, top, width, height, language="eng", preprocessing="default")` - Extract text from region
- `find_text_in_image(image_path, search_text, language="eng")` - Find specific text in image
- `get_text_confidence(image_path, language="eng")` - Get text extraction confidence
- `preprocess_image_for_ocr(image, method="default")` - Preprocess image for better OCR
- `detect_text_regions(image_path, min_confidence=30)` - Detect text regions in image

**Preprocessing Methods**:
- `"default"` - Basic preprocessing
- `"enhanced"` - Enhanced for better recognition
- `"console"` - Optimized for console/terminal text
- `"unity"` - Optimized for Unity console errors

### 6. Windows Integration (`tools.windows`)
**Purpose**: Deep Windows system integration  
**Dependencies**: `pywin32`, `psutil`

**Example Commands**:
- `Get system information` → `get_system_info()`
- `Check system uptime` → `get_system_uptime()`
- `List startup programs` → `get_startup_programs()`
- `Check disk usage` → `get_disk_usage()`
- `List running services` → `get_running_services()`

**Functions**:
- `get_system_info()` - Get comprehensive system information
- `get_registry_value(key_path, value_name, registry_root=HKEY_LOCAL_MACHINE)` - Get registry value
- `set_registry_value(key_path, value_name, value, value_type=REG_SZ)` - Set registry value
- `create_shortcut(target_path, shortcut_path, description="", icon_path="")` - Create Windows shortcut
- `get_startup_programs()` - Get programs that start with Windows
- `get_installed_programs()` - Get list of installed programs
- `get_file_associations(extension)` - Get file association information
- `get_environment_variables()` - Get all environment variables
- `set_environment_variable(name, value, permanent=False)` - Set environment variable
- `get_service_status(service_name)` - Get Windows service status
- `start_service(service_name)` - Start Windows service
- `stop_service(service_name)` - Stop Windows service
- `get_disk_usage()` - Get disk usage for all drives
- `get_network_info()` - Get network interface information
- `get_running_services()` - Get all Windows services
- `get_system_uptime()` - Get system uptime information

## 🔧 Installation & Setup

### 1. Install Dependencies

The `install_deps_step_by_step.py` script automatically installs all required dependencies:

```powershell
pip install discord.py python-dotenv openai pillow pywin32 pyautogui psutil pygetwindow pywinauto keyboard mouse mss pytesseract opencv-python
```

### 2. Additional Setup

**Tesseract OCR**: Download and install Tesseract OCR from:
- https://github.com/UB-Mannheim/tesseract/wiki
- The tool will auto-detect the installation path

**Windows Services**: Some functions require administrator privileges for registry access and service management.

### 3. Environment Variables

Optional environment variables for customization:
- `UNITY_EXE` - Override Unity executable path
- `CURSOR_EXE` - Override Cursor executable path

## 🚀 Usage

### Through Discord Bot

All tools are accessible through the Discord bot using natural language commands:

```
User: "Take screenshot of Unity console"
Bot: [Takes screenshot and sends to Discord]

User: "Kill Unity task"
Bot: "Killed Unity processes: Unity.exe, Unity Hub.exe"

User: "Switch to Unity and type play"
Bot: "Switched to Unity window" + types play key
```

### Direct Function Calls

```python
from tools.process import kill_unity_process
from tools.screenshot import take_window_screenshot
from tools.ocr import extract_text_from_window

# Kill Unity processes
result = kill_unity_process()

# Take screenshot of Unity window
screenshot_path = take_window_screenshot("Unity")

# Extract text from Unity console
text_result = extract_text_from_window("Unity", preprocessing="unity")
print(f"Unity console text: {text_result['text']}")
```

### Unified Tool Interface

The `tool_manager.py` provides a unified interface that maintains backward compatibility:

```python
from tool_manager import run_program

# All tools accessible through single interface
result1 = run_program("screenshot", "Unity")  # Screenshot Unity window
result2 = run_program("kill_process", "Unity.exe")  # Kill Unity process
result3 = run_program("extract_text", "unity")  # Extract text from Unity
```

## 🧪 Testing

Run comprehensive tests for all tool modules:

```bash
# Run all tests
python tests/run_all_tests.py

# Run specific tool tests
python tests/test_process_tools.py
python tests/test_window_tools.py
python tests/test_input_tools.py
python tests/test_screenshot_tools.py
python tests/test_ocr_tools.py
python tests/test_windows_tools.py
```

## 🔄 Backward Compatibility

The new tool system maintains full backward compatibility with existing bot commands:

- All existing Discord commands continue to work
- The `run_program()` function interface is unchanged
- Legacy Unity automation functions are preserved
- Smart open functionality is enhanced but compatible

## 📊 Tool Availability

The system gracefully handles missing dependencies:

- Tools check for required libraries on import
- Missing tools are reported but don't break the bot
- Fallback implementations are provided where possible
- Version information shows tool availability status

## 🎯 Example Workflows

### Unity Development Workflow

```
1. "Open Escape Purgatory in Unity"
   → Opens Unity with correct version (2021.3.45f1)

2. "Take screenshot of Unity console"
   → Captures Unity window and sends to Discord

3. "Read errors from Unity console"
   → Extracts text from Unity console using OCR

4. "Switch to Unity and press play"
   → Focuses Unity window and presses play button

5. "Monitor Unity for 10 seconds"
   → Monitors Unity process resource usage
```

### System Administration Workflow

```
1. "Get system information"
   → Shows OS, memory, CPU, disk usage

2. "List running processes"
   → Shows top processes by memory usage

3. "Check disk usage"
   → Shows usage for all drives

4. "List startup programs"
   → Shows programs that start with Windows

5. "Get network information"
   → Shows network interfaces and statistics
```

## 🚨 Troubleshooting

### Common Issues

1. **"Module not found" errors**: Install missing dependencies with pip
2. **Tesseract not found**: Install Tesseract OCR and ensure it's in PATH
3. **Permission denied**: Some Windows functions require administrator privileges
4. **Screenshot failures**: Check if target windows are visible and accessible

### Debug Mode

Enable debug logging by setting the bot to `llm_only` mode and checking console output for detailed error messages.

## 🔮 Future Enhancements

Planned additions to the tool system:

- **Unity Tools**: Unity-specific automation and project management
- **Discord Tools**: Advanced Discord API integration
- **System Tools**: Cross-platform system utilities
- **AI Integration**: Direct integration with Cursor AI and other AI tools
- **Workflow Automation**: Complex multi-step automation sequences

---

*This tool system provides comprehensive automation capabilities for the Cuttle Discord assistant, enabling powerful desktop automation and system integration.*

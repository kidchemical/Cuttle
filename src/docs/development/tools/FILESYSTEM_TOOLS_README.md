# Windows Filesystem & Shell Tools for Cuttle

Comprehensive file system management and shell execution tools integrated into the Cuttle MCP server.

## Overview

This implementation adds full Windows filesystem capabilities to your project, including:

✅ **File Operations**: Read, write, copy, move, delete files  
✅ **Directory Management**: List, create, delete, search directories  
✅ **Shell Execution**: PowerShell, CMD, Python, and batch file execution  
✅ **Environment Control**: Manage environment variables  
✅ **Async Execution**: Run commands in background with process management  
✅ **File Analysis**: Hash calculation, size analysis, metadata extraction  
✅ **Compression**: ZIP file creation and extraction  

Everything Cursor can do with file system management is now available through your MCP server!

## Quick Start

### 1. Test the Installation

```bash
cd src
python test_filesystem_shell.py
```

This will run comprehensive tests to verify all tools are working correctly.

### 2. Start the MCP Server

```bash
python src/mcp_server.py
```

### 3. Use the Tools

#### Direct Python Usage

```python
from tools.filesystem import read_file, write_file, list_directory
from tools.shell import execute_powershell, execute_cmd, execute_python

# File operations
content = read_file("config.json")
write_file("output.txt", "Hello World!")

# Directory operations
files = list_directory("F:/Dev/Cuttle", pattern="*.py", recursive=True)

# Shell execution
result = execute_powershell("Get-Process | Select-Object -First 10")
result = execute_cmd("dir /b")
result = execute_python("print('Hello from Python!')")
```

#### Through MCP Server

All tools are exposed as MCP tools and can be called through the Model Context Protocol.

## Available Tools

### Filesystem Tools (17 tools)

| Tool | Description |
|------|-------------|
| `read_file` | Read file contents (text or binary) |
| `write_file` | Write content to a file |
| `append_file` | Append content to a file |
| `delete_file` | Delete a file |
| `copy_file` | Copy a file to another location |
| `move_file` | Move or rename a file |
| `file_exists` | Check if a file exists |
| `get_file_info` | Get detailed file metadata |
| `list_directory` | List directory contents with filtering |
| `create_directory` | Create a directory (with parents) |
| `delete_directory` | Delete a directory (recursively) |
| `search_files` | Search files by name/content |
| `get_directory_size` | Calculate total directory size |
| `get_file_hash` | Calculate file hash (MD5, SHA256, etc.) |
| `compress_file` | Compress files/directories to ZIP |
| `decompress_file` | Extract ZIP archives |

### Shell Execution Tools (12 tools)

| Tool | Description |
|------|-------------|
| `execute_command` | Execute any shell command |
| `execute_powershell` | Execute PowerShell commands |
| `execute_cmd` | Execute CMD/batch commands |
| `execute_python` | Execute Python code |
| `execute_python_script` | Run Python script files |
| `execute_batch_file` | Execute batch files |
| `get_shell_environment` | Get environment variables |
| `set_shell_environment` | Set environment variables |
| `get_command_output` | Get command output (simplified) |
| `run_async_command` | Start command in background |
| `kill_command_process` | Terminate async command |
| `get_running_commands` | List running async commands |

## Usage Examples

### File Management

```python
from tools.filesystem import *

# Read a configuration file
config = read_file("config.json")
if config["success"]:
    print(f"Config size: {config['size_human']}")
    content = config["content"]

# Write data to a file
write_file(
    "output/results.txt",
    "Processing complete!",
    create_dirs=True
)

# Search for Python files containing a specific function
matches = search_files(
    "F:/Dev/Cuttle/src",
    pattern="*.py",
    content_pattern="def main",
    recursive=True
)

print(f"Found {matches['count']} files with 'def main'")

# Get directory statistics
stats = get_directory_size("F:/Dev/Cuttle/src")
print(f"Total size: {stats['size_human']}")
print(f"Files: {stats['file_count']}")
```

### PowerShell Automation

```python
from tools.shell import execute_powershell

# Get system information
result = execute_powershell("""
    Get-ComputerInfo | Select-Object CsName, WindowsVersion, OsHardwareAbstractionLayer
""")
print(result["stdout"])

# Manage Windows services
result = execute_powershell("Get-Service | Where-Object Status -eq 'Running' | Select-Object -First 5")

# File operations
result = execute_powershell("""
    Get-ChildItem -Path C:\\Users -Recurse -Filter *.log -ErrorAction SilentlyContinue | 
    Measure-Object -Property Length -Sum
""")
```

### CMD Operations

```python
from tools.shell import execute_cmd

# Network diagnostics
result = execute_cmd("ipconfig /all")
print(result["stdout"])

# Directory operations
result = execute_cmd("dir /s /b *.py")

# System information
result = execute_cmd("systeminfo")
```

### Python Code Execution

```python
from tools.shell import execute_python

# Run Python code
code = """
import json
import sys

data = {
    "python_version": sys.version,
    "platform": sys.platform,
    "executable": sys.executable
}

print(json.dumps(data, indent=2))
"""

result = execute_python(code)
output = json.loads(result["stdout"])
print(f"Python: {output['python_version']}")
```

### Async Command Execution

```python
from tools.shell import run_async_command, get_running_commands, kill_command_process
import time

# Start a long-running task
task = run_async_command(
    "python long_running_script.py",
    shell="cmd",
    process_id="data_processor"
)

print(f"Started task with PID: {task['pid']}")

# Do other work...

# Check if still running
running = get_running_commands()
for cmd in running['processes']:
    print(f"Process {cmd['process_id']}: Running={cmd['running']}")

# Kill if needed
kill_command_process("data_processor")
```

### Environment Management

```python
from tools.shell import get_environment_variables, set_environment_variable

# Get all PATH variables
env = get_environment_variables(pattern="PATH*")
for name, value in env["variables"].items():
    print(f"{name}: {value[:50]}...")

# Set a temporary variable
set_environment_variable("MY_APP_CONFIG", "/path/to/config")

# Set a permanent user variable
set_environment_variable("MY_APP_HOME", "F:/Dev/Cuttle", permanent=True)
```

## Directory Structure

```
src/
├── tools/
│   ├── filesystem/
│   │   ├── __init__.py
│   │   ├── filesystem_manager.py  # Core filesystem operations
│   │   └── README.md
│   ├── shell/
│   │   ├── __init__.py
│   │   ├── shell_manager.py       # Shell execution tools
│   │   └── README.md
│   └── ...
├── mcp_server.py                   # Updated with new tools
├── test_filesystem_shell.py        # Test suite
└── ...
```

## Safety Features

### Filesystem
- Path normalization and validation
- Overwrite protection (optional)
- Automatic parent directory creation
- Binary file support
- Multiple encoding support

### Shell Execution
- Configurable timeouts (default 30s)
- Working directory control
- Environment variable isolation
- Stderr capture for error handling
- Process tracking for async execution
- PowerShell execution policy control

## Integration with MCP

All tools are automatically registered with your MCP server when it starts. The server will report availability status:

```
Filesystem tools: ✅ Available
Shell tools: ✅ Available
```

## Error Handling

All functions return a standardized dictionary format:

```python
{
    "success": True/False,
    "message": "Success message or data",
    "error": "Error message (if failed)",
    # Additional fields specific to each function
}
```

Always check the `success` field before using results:

```python
result = read_file("config.json")
if result["success"]:
    # Use result["content"]
    process_config(result["content"])
else:
    # Handle error
    print(f"Error: {result['error']}")
```

## Performance Considerations

- **Large Files**: Use `binary=True` for large binary files
- **Recursive Operations**: Set reasonable depth limits for recursive searches
- **Timeouts**: Increase timeouts for slow operations
- **Async Execution**: Use async commands for long-running tasks
- **Memory**: Large file operations are handled in chunks when possible

## Documentation

Detailed documentation available in:
- `src/tools/filesystem/README.md` - Filesystem tools documentation
- `src/tools/shell/README.md` - Shell execution documentation

## Troubleshooting

### "Filesystem tools not available"
- Check that `tools/filesystem/filesystem_manager.py` exists
- Verify all imports in `mcp_server.py`
- Run the test script to diagnose issues

### "Shell tools not available"
- Check that `tools/shell/shell_manager.py` exists  
- Verify Python path is correct
- Check PowerShell availability on your system

### PowerShell execution fails
- Check execution policy: `Get-ExecutionPolicy`
- Use `execution_policy="Bypass"` parameter
- Run as administrator if needed

### Timeout errors
- Increase timeout parameter
- Use async execution for long-running commands
- Check if command is actually hanging

## Examples in Action

Check out these example scripts:
- `test_filesystem_shell.py` - Comprehensive test suite
- `src/tools/filesystem/README.md` - More filesystem examples
- `src/tools/shell/README.md` - More shell examples

## Next Steps

1. ✅ Run the test suite: `python src/test_filesystem_shell.py`
2. ✅ Start the MCP server: `python src/mcp_server.py`
3. ✅ Try the examples above
4. ✅ Read the detailed documentation in each tool's README
5. ✅ Integrate into your existing workflows

## Support

For issues or questions:
1. Check the detailed README files in each tool directory
2. Run the test suite to diagnose problems
3. Check the MCP server logs for detailed error messages

---

**You now have full Windows filesystem and shell capabilities in your MCP server!** 🎉

Everything Cursor can do with file management, PowerShell, CMD, and Python execution is now available through your tools.


# Web API Integration for Filesystem & Shell Tools

## Issue Fixed

The "Failed to fetch" error was caused by the pipeline executor trying to call filesystem/shell tools that weren't registered in the web API.

## Solution

I've added handlers for all 29 new tools to the web API (`src/web_chat_api.py`).

## ⚠️  IMPORTANT: Restart the Web Server

For the changes to take effect, you MUST restart the web API server:

```bash
# Stop the current server (Ctrl+C in the terminal running it)

# Then restart it:
python src/web_chat_api.py
```

Or if using the launcher:
```bash
python src/start_web_chat.py
```

## Tool Naming Convention

### Filesystem Tools (prefix: `tool-fs-`)
- `tool-fs-read-file` - Read file contents
- `tool-fs-write-file` - Write to files
- `tool-fs-list-directory` - List directory contents
- `tool-fs-delete-file` - Delete files
- `tool-fs-copy-file` - Copy files
- `tool-fs-move-file` - Move/rename files
- `tool-fs-file-exists` - Check if file exists
- `tool-fs-get-file-info` - Get file metadata
- `tool-fs-create-directory` - Create directories
- `tool-fs-delete-directory` - Delete directories
- `tool-fs-search-files` - Search for files
- `tool-fs-get-directory-size` - Get directory size
- `tool-fs-get-file-hash` - Calculate file hash
- `tool-fs-compress-file` - Compress to ZIP
- `tool-fs-decompress-file` - Extract ZIP

### Shell Tools (prefix: `tool-shell-`)
- `tool-shell-execute-command` - Execute any shell command
- `tool-shell-execute-powershell` - Execute PowerShell
- `tool-shell-execute-cmd` - Execute CMD
- `tool-shell-execute-python` - Execute Python code
- `tool-shell-execute-python-script` - Run Python script
- `tool-shell-execute-batch-file` - Run batch file
- `tool-shell-get-environment` - Get environment variables
- `tool-shell-set-environment` - Set environment variables
- `tool-shell-get-command-output` - Get command output
- `tool-shell-run-async-command` - Run command async
- `tool-shell-kill-command-process` - Kill async process
- `tool-shell-get-running-commands` - List running commands

## Using Tools in Node Editor

### Example: Read a File

Create a tool node with:
- **Type**: `tool-fs-read-file`
- **Config**:
  ```json
  {
    "filePath": "F:/Dev/Cuttle/README.md",
    "encoding": "utf-8"
  }
  ```

### Example: Execute PowerShell

Create a tool node with:
- **Type**: `tool-shell-execute-powershell`
- **Config**:
  ```json
  {
    "command": "Get-Process | Select-Object -First 10 Name, CPU",
    "timeout": 30
  }
  ```

### Example: List Directory

Create a tool node with:
- **Type**: `tool-fs-list-directory`
- **Config**:
  ```json
  {
    "directoryPath": "F:/Dev/Cuttle/src",
    "pattern": "*.py",
    "recursive": true
  }
  ```

### Example: Execute CMD

Create a tool node with:
- **Type**: `tool-shell-execute-cmd`
- **Config**:
  ```json
  {
    "command": "dir /b *.py",
    "timeout": 10
  }
  ```

## Config Parameters by Tool

### Filesystem Tools

**tool-fs-read-file**
- `filePath` (required): Path to file
- `encoding`: Text encoding (default: 'utf-8')
- `binary`: Read as binary (default: false)

**tool-fs-write-file**
- `filePath` (required): Path to file
- `content` (required): Content to write
- `encoding`: Text encoding (default: 'utf-8')
- `createDirs`: Create parent directories (default: true)

**tool-fs-list-directory**
- `directoryPath` (required): Path to directory
- `pattern`: File pattern (default: '*')
- `recursive`: Search recursively (default: false)
- `filesOnly`: Only list files (default: false)
- `dirsOnly`: Only list directories (default: false)

**tool-fs-search-files**
- `directoryPath` (required): Path to search in
- `pattern`: Filename pattern
- `contentPattern`: Content to search for
- `recursive`: Search recursively (default: true)
- `caseSensitive`: Case-sensitive search (default: false)

### Shell Tools

**tool-shell-execute-powershell**
- `command` (required): PowerShell command
- `timeout`: Timeout in seconds (default: 30)
- `workingDir`: Working directory
- `executionPolicy`: Execution policy (default: 'Bypass')

**tool-shell-execute-cmd**
- `command` (required): CMD command
- `timeout`: Timeout in seconds (default: 30)
- `workingDir`: Working directory

**tool-shell-execute-python**
- `code` (required): Python code to execute
- `timeout`: Timeout in seconds (default: 30)
- `workingDir`: Working directory

## Response Format

All tools return a standardized response:

```json
{
  "success": true/false,
  "data": {
    // Tool-specific results
  }
}
```

For filesystem tools, the data includes:
- `success`: Boolean
- `path`: File/directory path
- Additional fields specific to the operation

For shell tools, the data includes:
- `success`: Boolean
- `command`: Command that was executed
- `stdout`: Standard output
- `stderr`: Standard error
- `return_code`: Exit code
- `duration`: Execution time in seconds

## Troubleshooting

### Still Getting "Failed to fetch"?

1. **Restart the web server** (most common fix)
2. Check the server logs for errors
3. Verify the tool type name matches the pattern exactly
4. Check that all required config parameters are provided
5. Ensure the web server is running on port 8080

### Check Server Status

```bash
# Windows
netstat -ano | findstr :8080

# Should show LISTENING on port 8080
```

### View Server Logs

The web server prints logs to the console. Look for:
- Tool execution errors
- Import errors
- Configuration errors

### Test a Tool Directly

You can test the API endpoint directly with curl or Postman:

```bash
curl -X POST http://localhost:8080/api/execute-tool \
  -H "Content-Type: application/json" \
  -d '{
    "nodeType": "tool-shell-execute-cmd",
    "config": {
      "command": "echo Hello"
    },
    "inputs": {}
  }'
```

Expected response:
```json
{
  "success": true,
  "data": {
    "success": true,
    "command": "echo Hello",
    "stdout": "Hello\n",
    "stderr": "",
    "return_code": 0,
    "duration": 0.02
  }
}
```

## Next Steps

1. ✅ **Restart the web server** 
2. ✅ Try your pipeline again
3. ✅ Check server logs if you still get errors
4. ✅ Use the examples above to create tool nodes

---

**The filesystem and shell tools are now fully integrated with the web API!**


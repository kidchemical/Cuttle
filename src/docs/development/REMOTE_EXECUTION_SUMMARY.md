# 🌐 Remote Execution - Feature Summary

## What's New

Cuttle now supports **distributed execution across multiple devices** on your local network! 

Your node editor and pipeline execution functionality can now work with remote environments, and yes - this works as nodes in the graph! 🎉

## Quick Overview

### What You Can Do

1. **Execute commands remotely** - Run any Cuttle command on another machine
2. **Run pipelines on remote devices** - Send entire workflows to other machines
3. **Auto-discover instances** - Find all Cuttle instances on your network automatically
4. **Monitor remote systems** - Get CPU, memory, and disk info from remote machines
5. **Distribute workloads** - Split processing across multiple devices

### Example: Screenshot Collection from Multiple Machines

```
[Manual Trigger] 
    ↓
[Discover Hosts] - Finds all Cuttle instances on network
    ↓
[Remote Execute: "take screenshot"] - Runs on each discovered host
    ↓
[Collect Results] - Gathers all screenshots
    ↓
[Log Output] - Saves collection report
```

## Files Created

### Core Implementation
- **`src/remote_executor.py`** - Python remote execution engine
  - Host management
  - Network discovery
  - Remote command/pipeline execution
  - System info retrieval

- **`src/web_chat_api.py`** - Updated with remote execution API endpoints
  - `/api/remote/execute` - Execute commands
  - `/api/remote/execute-pipeline` - Execute pipelines
  - `/api/remote/system-info` - Get system info
  - `/api/remote/hosts` - Manage hosts
  - `/api/remote/discover` - Network discovery

### Frontend Integration
- **`src/web/js/node_types.js`** - Added 4 new node types:
  - 🌐 Remote Execute - Execute commands remotely
  - 🚀 Remote Pipeline - Run pipelines on remote machines
  - 🔍 Discover Hosts - Auto-discover Cuttle instances
  - 💻 Remote System Info - Get remote system information

- **`src/web/js/pipeline_executor.js`** - Added execution logic for remote nodes
  - `executeRemoteExecute()` - Remote command execution
  - `executeRemotePipeline()` - Remote pipeline execution
  - `executeRemoteDiscover()` - Network discovery
  - `executeRemoteSystemInfo()` - System info retrieval

- **`src/web/node_editor.html`** - Added "Remote Execution" category to node palette

### Documentation & Tools
- **`src/REMOTE_EXECUTION_GUIDE.md`** - Comprehensive user guide
  - Quick start instructions
  - Node configuration details
  - API documentation
  - Troubleshooting guide
  - Security considerations

- **`src/setup_remote_execution.py`** - Setup and testing tool
  - Network configuration check
  - Server status verification
  - Remote connection testing
  - Firewall setup instructions
  - Interactive setup wizard

- **`src/pipelines/Remote_Execution_Example.json`** - Example pipeline
  - Demonstrates all remote execution nodes
  - Ready to import and use

- **`REMOTE_EXECUTION_SUMMARY.md`** - This file

## How It Works

### Network Architecture

```
┌──────────────────┐         ┌──────────────────┐
│   Main Machine   │         │  Remote Machine  │
│                  │         │                  │
│  Node Editor     │◄───────►│  Cuttle Server   │
│  (Browser)       │  HTTP   │  (Python)        │
│                  │         │                  │
│  Web Server      │         │  Tools/Resources │
│  :8080           │         │  :8080           │
└──────────────────┘         └──────────────────┘
       │                              │
       └──────────────┬───────────────┘
                      │
              Local Network (LAN)
```

### Execution Flow

1. **Node Editor** (Browser) creates a pipeline with remote nodes
2. **Pipeline Executor** (JavaScript) runs the pipeline
3. When it encounters a remote node:
   - Makes HTTP request to local server
   - Local server forwards request to remote host
   - Remote host executes command/pipeline
   - Result flows back through the chain
4. **Output Node** displays/saves the result

### API Architecture

```
Browser                    Local Server              Remote Server
  │                             │                          │
  ├─► Execute Pipeline          │                          │
  │        │                    │                          │
  │        └──► /api/execute    │                          │
  │                 │            │                          │
  │                 └──► Detect Remote Node                │
  │                          │                              │
  │                          └──► /api/remote/execute ─────►│
  │                                                          │
  │                                                    Execute
  │                                                          │
  │                                          Result ◄────────┤
  │                                              │           │
  │                    Update Node ◄─────────────┘           │
  │                       │                                  │
  │        Display ◄──────┘                                  │
```

## Getting Started

### 1. Run Setup Tool
```bash
cd src
python setup_remote_execution.py
```

This will:
- Show your network IP address
- Check if server is running
- Test connections
- Provide firewall setup instructions

### 2. Start Server (if not running)
```bash
python start_node_editor.py
```

Server will bind to `0.0.0.0:8080` (accessible on network)

### 3. Access from Another Device

Open browser on spare laptop:
```
http://192.168.1.100:8080/node_editor.html
```
(Replace with your actual IP)

### 4. Try the Example Pipeline

In the Node Editor:
1. Click **Open**
2. Select **Remote_Execution_Example.json**
3. Configure the remote host IP addresses
4. Click **Run** on any trigger!

## Node Types Reference

### 🌐 Remote Execute
Execute commands on remote machines.

**Config:**
- `host`: Remote IP:port (e.g., "192.168.1.100:8080")
- `command`: Command to run
- `timeout`: Seconds (default: 30)

**Inputs:**
- `trigger`: When to execute
- `command`: Override command

**Outputs:**
- `result`: Command result
- `success`: Boolean

### 🚀 Remote Pipeline
Run entire pipelines remotely.

**Config:**
- `host`: Remote IP:port
- `pipelinePath`: Pipeline to execute
- `timeout`: Seconds (default: 60)

**Inputs:**
- `trigger`: When to execute
- `pipeline`: Pipeline data

**Outputs:**
- `result`: Pipeline result
- `success`: Boolean

### 🔍 Discover Hosts
Find Cuttle instances on network.

**Config:**
- `port`: Port to scan (default: 8080)

**Outputs:**
- `hosts`: Array of IPs

### 💻 Remote System Info
Get remote machine stats.

**Config:**
- `host`: Remote IP:port

**Outputs:**
- `info`: System information object

## Python API

```python
from remote_executor import get_remote_executor

executor = get_remote_executor()

# Add host
executor.add_host("laptop", "192.168.1.100", 8080)

# Execute command
result = executor.execute_command("laptop", "take screenshot")

# Discover network
hosts = executor.discover_hosts()
print(f"Found {len(hosts)} hosts")

# Get system info
info = executor.get_system_info("laptop")
print(f"CPU: {info['cpu_percent']}%")

# Broadcast to all
results = executor.broadcast_command("get system info")
```

## REST API

### Execute Command
```bash
curl -X POST http://192.168.1.100:8080/api/remote/execute \
  -H "Content-Type: application/json" \
  -d '{"command": "take screenshot"}'
```

### Discover Hosts
```bash
curl -X POST http://localhost:8080/api/remote/discover \
  -H "Content-Type: application/json" \
  -d '{"port": 8080}'
```

### Get System Info
```bash
curl http://192.168.1.100:8080/api/remote/system-info
```

## Use Cases

### 1. Development Workflows
- Test on multiple OS versions simultaneously
- Deploy to test environments
- Collect logs from all instances

### 2. Monitoring & Operations
- Health checks across infrastructure
- Resource monitoring
- Automated maintenance tasks

### 3. Distributed Processing
- Split large datasets across machines
- Parallel processing
- Load distribution

### 4. Automation
- Scheduled tasks on multiple machines
- Network-wide updates
- Batch operations

## Security Notes

⚠️ **Current implementation is for trusted local networks only.**

For production:
- Add authentication (API keys, OAuth)
- Use HTTPS/TLS encryption
- Implement rate limiting
- Add IP whitelisting
- Use VPN for remote access

## Troubleshooting

### Can't Access from Other Devices

1. **Check firewall:**
   ```powershell
   # Windows
   netsh advfirewall firewall add rule name="Cuttle" dir=in action=allow protocol=TCP localport=8080
   ```

2. **Verify network:**
   ```bash
   ping 192.168.1.100
   ```

3. **Test local server:**
   ```bash
   curl http://localhost:8080/api/health
   ```

### Discovery Not Working

- Discovery can take 2-3 minutes for large networks
- Ensure Cuttle is running on target machines
- Check firewall on remote machines
- Verify same network/subnet

### Timeout Errors

- Increase timeout in node config
- Check network latency
- Verify remote machine isn't overloaded

## Technical Details

### Dependencies
- **Flask** - Web server
- **requests** - HTTP client
- **psutil** - System information

All already in your requirements.txt!

### Port Configuration
Default: 8080

To change, edit `start_node_editor.py`:
```python
app.run(host='0.0.0.0', port=9090)
```

### Network Discovery
- Scans local subnet (192.168.x.0/24)
- Checks for Cuttle health endpoint
- Returns list of active IPs
- Configurable port and range

## Next Steps

1. ✅ Run `python setup_remote_execution.py`
2. ✅ Start server on multiple machines
3. ✅ Open Node Editor and try example pipeline
4. ✅ Build your own distributed workflows!

## Example Workflows

### Multi-Machine Screenshot Collection
```
Discover → For Each Host → Remote Screenshot → Collect → Export
```

### System Health Dashboard
```
Schedule → Parallel System Info (3 hosts) → Merge → Check Alerts → Discord
```

### Distributed Data Processing
```
Load Data → Split → Remote Execute (5 hosts) → Merge Results → Output
```

### Network-Wide Command
```
Text Input → Broadcast → Collect Results → Summary Report
```

## Architecture Benefits

✨ **Advantages:**
- **Visual Workflows**: Build distributed systems in node editor
- **No SSH Required**: Pure HTTP/REST communication
- **Cross-Platform**: Works on Windows, Linux, Mac
- **Easy Setup**: No complex configuration
- **Real-Time**: See execution across all machines
- **Extensible**: Add more node types easily

## Future Enhancements

Possible additions:
- Authentication layer
- Encrypted communication
- Persistent host registry
- Remote debugging
- Resource scheduling
- Auto-scaling
- Remote log streaming
- File synchronization

## Support

Questions? Issues?
1. Check browser console (F12)
2. Check Python server logs
3. Review REMOTE_EXECUTION_GUIDE.md
4. Run setup_remote_execution.py for diagnostics

---

**You're all set for distributed computing with Cuttle!** 🚀

Try the Discover Hosts node to see all your Cuttle instances, or create a Remote Execute node to run commands on your spare laptop!


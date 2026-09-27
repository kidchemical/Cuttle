# Remote Execution Guide

## Overview

Cuttle now supports remote execution across multiple devices on your local network! This allows you to:

- **Execute commands** on remote machines
- **Run pipelines** on remote Cuttle instances  
- **Discover** Cuttle instances automatically
- **Monitor** remote system resources
- **Distribute** workloads across multiple machines

## Quick Start

### 1. Enable Network Access

Your Cuttle instance already runs on `0.0.0.0` which makes it accessible on your network!

Start the node editor:
```bash
python start_node_editor.py
```

This will:
- Bind to `0.0.0.0:8080` (accessible on your network)
- Open the node editor in your browser

### 2. Find Your IP Address

**Windows:**
```powershell
ipconfig
```
Look for "IPv4 Address" under your active network adapter.

**Linux/Mac:**
```bash
ifconfig
# or
ip addr show
```

Example: `192.168.1.100`

### 3. Access from Another Device

On your spare laptop or another machine on the same network:

1. Make sure it's on the same WiFi/network
2. Open a browser and navigate to:
   ```
   http://192.168.1.100:8080/node_editor.html
   ```
   (Replace with your actual IP address)

## Remote Execution Nodes

### 🌐 Remote Execute Node

Execute commands on a remote Cuttle instance.

**Configuration:**
- **Remote Host**: IP address or hostname with port (e.g., `192.168.1.100:8080`)
- **Command**: The command to execute
- **Timeout**: Execution timeout in seconds

**Inputs:**
- `trigger`: Execution trigger
- `command`: Command string (overrides config)

**Outputs:**
- `result`: Execution result
- `success`: Boolean success status

**Example Pipeline:**
```
[Manual Trigger] → [Remote Execute: "take screenshot"] → [Log Output]
```

### 🚀 Remote Pipeline Node

Execute an entire pipeline on a remote machine.

**Configuration:**
- **Remote Host**: Target machine IP:port
- **Pipeline Path**: Path to pipeline JSON or pipeline name
- **Timeout**: Pipeline execution timeout

**Inputs:**
- `trigger`: Execution trigger
- `pipeline`: Pipeline data object

**Outputs:**
- `result`: Pipeline execution result
- `success`: Boolean success status

**Use Cases:**
- Distribute compute-intensive tasks
- Run pipelines on machines with specific resources
- Parallel execution across multiple machines

### 🔍 Discover Hosts Node

Automatically discover Cuttle instances on your network.

**Configuration:**
- **Port**: Port to scan (default: 8080)

**Outputs:**
- `hosts`: Array of discovered host IPs

**Example:**
```
[Manual Trigger] → [Discover Hosts] → [Text Output]
```

This will scan your network and find all running Cuttle instances!

### 💻 Remote System Info Node

Get system information from a remote machine.

**Configuration:**
- **Remote Host**: Target machine IP:port

**Outputs:**
- `info`: System information object containing:
  - Hostname
  - Platform/OS
  - CPU count and usage
  - Memory usage
  - Disk usage

## Example Use Cases

### 1. Distributed Screenshot Collection

```
[Manual Trigger] 
    ↓
[Discover Hosts]
    ↓
[Remote Execute: "take screenshot"] (for each host)
    ↓
[Aggregate Results]
    ↓
[Log Output]
```

### 2. Multi-Machine Processing

```
[Input] → [Split Data]
    ├─→ [Remote Execute: Host 1]
    ├─→ [Remote Execute: Host 2]
    └─→ [Remote Execute: Host 3]
        ↓
    [Merge Results]
        ↓
    [Output]
```

### 3. Remote System Monitoring

```
[Schedule Trigger: Every 5 minutes]
    ↓
[Get Remote System Info]
    ↓
[Check Thresholds]
    ├─→ [If High CPU] → [Alert]
    └─→ [Otherwise] → [Log]
```

### 4. Network-Wide Command Execution

```
[Text Input: Command]
    ↓
[Discover Hosts]
    ↓
[Broadcast Command] (parallel execution)
    ↓
[Collect Results]
    ↓
[Summary Report]
```

## API Endpoints

The following REST API endpoints are available for remote execution:

### Execute Command
```http
POST http://<host>:8080/api/remote/execute
Content-Type: application/json

{
  "command": "take screenshot",
  "context": { "user": "admin" }
}
```

### Execute Pipeline
```http
POST http://<host>:8080/api/remote/execute-pipeline
Content-Type: application/json

{
  "nodes": [...],
  "connections": [...]
}
```

### Get System Info
```http
GET http://<host>:8080/api/remote/system-info
```

### Discover Hosts
```http
POST http://<host>:8080/api/remote/discover
Content-Type: application/json

{
  "port": 8080
}
```

### List Remote Hosts
```http
GET http://<host>:8080/api/remote/hosts
```

### Add Remote Host
```http
POST http://<host>:8080/api/remote/hosts
Content-Type: application/json

{
  "name": "laptop",
  "host": "192.168.1.100",
  "port": 8080
}
```

## Python API

You can also use the Python API directly:

```python
from remote_executor import get_remote_executor

# Get the executor
executor = get_remote_executor()

# Add a remote host
executor.add_host("laptop", "192.168.1.100", 8080)

# Execute a command
result = executor.execute_command("laptop", "take screenshot")
print(result)

# Execute a pipeline
pipeline_data = {...}
result = executor.execute_pipeline("laptop", pipeline_data)

# Discover hosts on the network
discovered = executor.discover_hosts(port=8080)
print(f"Found {len(discovered)} hosts: {discovered}")

# Get system info
info = executor.get_system_info("laptop")
print(info)

# Broadcast command to all hosts
results = executor.broadcast_command("get system info")
for host, result in results.items():
    print(f"{host}: {result}")
```

## Security Considerations

⚠️ **Important Security Notes:**

1. **Network Trust**: Only use this on **trusted local networks**. The current implementation has no authentication.

2. **Firewall**: Ensure your firewall allows connections on port 8080 (or your chosen port).

3. **Production Use**: For production environments, consider:
   - Adding authentication (API keys, OAuth)
   - Using HTTPS/TLS
   - Implementing rate limiting
   - Adding IP whitelisting

## Troubleshooting

### Can't Connect to Remote Host

1. **Check Firewall**: Make sure port 8080 is open
   ```powershell
   # Windows - Allow port through firewall
   netsh advfirewall firewall add rule name="Cuttle Web" dir=in action=allow protocol=TCP localport=8080
   ```

2. **Verify Network**: Ensure both devices are on the same network
   ```bash
   # Ping the remote host
   ping 192.168.1.100
   ```

3. **Check Server Status**: Verify the server is running
   ```bash
   # Test from the same machine
   curl http://localhost:8080/api/health
   ```

### Discovery Not Finding Hosts

- Discovery scans the network, which can take time (up to 2-3 minutes)
- Make sure Cuttle is running on the target machines
- Check that the correct port is specified

### Timeout Errors

- Increase the timeout value in node configuration
- Check network latency: `ping <remote-host>`
- Verify the remote machine isn't overloaded

## Advanced Configuration

### Custom Port

To run on a different port, modify `start_node_editor.py`:

```python
app.run(host='0.0.0.0', port=9090, debug=False)
```

### Network Discovery Optimization

For faster discovery on large networks, you can modify the scan range in `remote_executor.py`:

```python
# Scan only a specific range
for i in range(100, 110):  # Instead of range(1, 255)
    host = f"{network_prefix}.{i}"
    # ... rest of discovery code
```

## Integration Examples

### With Python Scripts

```python
import requests

# Execute command remotely
response = requests.post(
    'http://192.168.1.100:8080/api/remote/execute',
    json={'command': 'take screenshot'}
)
print(response.json())
```

### With curl

```bash
# Execute command
curl -X POST http://192.168.1.100:8080/api/remote/execute \
  -H "Content-Type: application/json" \
  -d '{"command": "take screenshot"}'

# Get system info
curl http://192.168.1.100:8080/api/remote/system-info

# Discover hosts
curl -X POST http://192.168.1.100:8080/api/remote/discover \
  -H "Content-Type: application/json" \
  -d '{"port": 8080}'
```

## Next Steps

1. **Try the Discovery Node**: Scan your network to find all Cuttle instances
2. **Create a Remote Pipeline**: Build a workflow that spans multiple machines
3. **Monitor Resources**: Use Remote System Info nodes to track machine health
4. **Automate Tasks**: Use Schedule triggers with remote execution for automation

## Support

For issues or questions:
1. Check the console logs in your browser (F12)
2. Check the Python server logs
3. Review the troubleshooting section above
4. Ensure all machines are on the same network

Happy distributed computing! 🚀


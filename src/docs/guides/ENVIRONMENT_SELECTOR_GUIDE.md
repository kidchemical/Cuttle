# Environment Selector Guide

## Overview

The Node Editor now includes an **Environment Selector** in the toolbar that lets you easily switch between different devices on your network. The selected environment is **automatically saved** in your pipeline file and **restored** when you open the pipeline.

## Features

🏠 **Defaults to Local** - Always starts with your current device  
🌐 **Network Discovery** - Automatically find Cuttle instances on your network  
💾 **Persistent Storage** - Selected environment is saved in the pipeline file  
⚙️ **Easy Management** - Add, test, and remove environments with one click  

## Quick Start

### 1. Select Environment

In the Node Editor toolbar, you'll see:

```
🌐 Environment: [Local (This Device)] [🔍] [⚙️]
```

- **Dropdown**: Shows current environment, click to switch
- **🔍 Button**: Discover devices on network
- **⚙️ Button**: Open environment management modal

### 2. Discover Devices

Click the **🔍** button to automatically find all Cuttle instances on your network.

The discovery process will:
1. Scan your local network (takes 1-3 minutes)
2. Find all running Cuttle instances
3. Retrieve system information (hostname, platform)
4. Add them to your environment list

Example output in console:
```
🔍 Discovering Cuttle instances on network...
✅ Found 2 device(s)
   • LAPTOP-PC (192.168.1.100)
   • DESKTOP-MAIN (192.168.1.105)
```

### 3. Add Environment Manually

Can't wait for discovery? Add devices manually:

1. Click **⚙️** to open Environment Management
2. Scroll to "Add Environment Manually"
3. Enter:
   - **Name**: e.g., "Spare Laptop", "Gaming PC"
   - **Host**: e.g., "192.168.1.100" or "laptop.local"
   - **Port**: Usually 8080 (default)
4. Click **➕ Add Environment**

### 4. Switch Environment

Simply select from the dropdown:

```
🌐 Environment: [Spare Laptop (192.168.1.100)]
```

The console will show:
```
🌐 Environment switched to: Spare Laptop (192.168.1.100)
```

### 5. Save Pipeline

When you save your pipeline (**💾 Save**):
- The current environment is **automatically saved**
- No extra steps needed!

### 6. Load Pipeline

When you open a pipeline:
- The saved environment is **automatically restored**
- If the environment doesn't exist, it falls back to local

Console output:
```
🌐 Environment restored: Spare Laptop (192.168.1.100)
```

Or if environment not found:
```
⚠️ Environment "old-laptop" not found, using local
```

## Environment Management Modal

Click **⚙️** to open the full management interface:

### Features

**Discover Devices**
- Click "🔍 Discover Devices on Network" button
- See scanning progress
- Devices are automatically added to your list

**Environment List**
- Shows all available environments
- Current environment is highlighted
- Each entry shows:
  - Icon (🏠 for local, 🌐 for remote)
  - Name and hostname
  - Host:Port
  - Test and Remove buttons

**Test Connection**
- Click **🔗** to test if a device is online
- Shows service version and status

**Remove Environment**
- Click **🗑️** to remove from list
- Cannot remove "Local" environment
- If removing current environment, automatically switches to local

**Add Manually**
- Fill in Name, Host, and Port
- Click **➕ Add Environment**
- Immediately available in dropdown

## How It Works

### Storage

**Environments** are stored in:
- **localStorage**: `cuttle-environments`
- **Pipeline JSON**: `metadata.environment`

This means:
- Environments persist across sessions
- Each pipeline remembers its environment
- Environment list is shared across all pipelines

### Data Structure

**Environment Object:**
```json
{
  "local": {
    "name": "Local (This Device)",
    "host": "localhost",
    "port": 8080,
    "isLocal": true
  },
  "192-168-1-100": {
    "name": "Spare Laptop (192.168.1.100)",
    "host": "192.168.1.100",
    "port": 8080,
    "hostname": "LAPTOP-PC",
    "platform": "Windows",
    "isLocal": false
  }
}
```

**Pipeline Metadata:**
```json
{
  "metadata": {
    "name": "My Pipeline",
    "description": "Processing workflow",
    "environment": "192-168-1-100"
  }
}
```

## Use Cases

### 1. Development Workflow

Set different environments for different pipeline stages:

- **Local**: Development and testing
- **Test Server**: Integration testing  
- **Production Server**: Final deployment

Each pipeline remembers which environment it should run on!

### 2. Multi-Device Processing

Create pipelines for different machines:

- **Pipeline A** → Gaming PC (GPU intensive tasks)
- **Pipeline B** → Laptop (lightweight tasks)
- **Pipeline C** → Server (24/7 processing)

### 3. Team Collaboration

Share pipelines with team members:

1. Save pipeline with environment name "shared-server"
2. Team members add "shared-server" to their environments
3. Everyone runs on the same server automatically

### 4. Remote Management

Manage remote machines from one interface:

1. Add all your devices as environments
2. Switch between them to monitor different machines
3. Run maintenance tasks on specific devices

## Integration with Remote Execution Nodes

The environment selector works **seamlessly** with remote execution nodes:

### Current Environment as Default

When you add a **Remote Execute** or **Remote System Info** node:
- It can **default to the current environment**
- Or you can override it with a specific host
- Makes it easy to target the current environment

### Future Enhancement

We could add an option to nodes:
```
Host: [Use Current Environment] or [Specify: 192.168.1.100]
```

This would make remote nodes automatically target the selected environment!

## Troubleshooting

### Discovery Not Finding Devices

**Problem**: No devices found during discovery

**Solutions**:
1. Ensure Cuttle is running on target devices
2. Check both devices are on same network
3. Verify firewall settings allow port 8080
4. Try adding manually with IP address

### Can't Connect to Environment

**Problem**: Selected environment but getting connection errors

**Solutions**:
1. Click **🔗 Test Connection** to verify status
2. Check if Cuttle server is running on remote device
3. Verify IP address hasn't changed (use DHCP reservation)
4. Ping the remote device: `ping 192.168.1.100`

### Environment Not Restored When Loading Pipeline

**Problem**: Pipeline loads but environment resets to local

**Solution**:
- The environment doesn't exist in your list
- Add it manually or run discovery
- Pipeline will use local until environment is added

### Remote Execution Not Using Selected Environment

**Problem**: Remote nodes still use hardcoded IP

**Explanation**:
- Current implementation: nodes have individual host settings
- Environment selector is for convenience and pipeline organization
- Future update will allow nodes to use "current environment"

## Tips & Best Practices

✅ **Use Discovery First** - Let automatic discovery find your devices  
✅ **Descriptive Names** - Use meaningful names like "Gaming-PC" not "pc1"  
✅ **Test After Adding** - Always test connection after adding manually  
✅ **One Pipeline Per Environment** - Keep pipelines focused on specific targets  
✅ **Document Environments** - Add notes about what each environment is for  

❌ **Don't Remove Without Checking** - Make sure no pipelines use an environment before removing  
❌ **Don't Rely on IPs** - Use DHCP reservations or hostnames for stability  
❌ **Don't Share Sensitive Environments** - Be careful with production environments  

## Examples

### Example 1: Quick Switch for Testing

```
1. Working on pipeline locally
2. Want to test on spare laptop
3. Click dropdown → Select "Spare Laptop"
4. Pipeline now targets spare laptop
5. Save pipeline - environment is saved
6. Later, open pipeline - auto-switches to Spare Laptop
```

### Example 2: Multi-Device Workflow

```
Pipeline: distributed_processing.json

Environment: "Main Server (192.168.1.200)"

Nodes:
- Load Data → Local
- Process → Remote Execute (current environment)
- Aggregate → Local  
- Output → Remote System (Server)

When you open this pipeline:
→ Automatically switches to Main Server
→ Ready to run on correct environment
```

### Example 3: Team Development

```
Team Member A:
1. Creates pipeline on "dev-server"
2. Saves and shares JSON

Team Member B:
1. Opens pipeline
2. Prompted: "Environment 'dev-server' not found"
3. Adds dev-server with correct IP
4. Pipeline now works correctly
```

## Future Enhancements

Possible future features:

- **Environment Groups**: Organize environments by project
- **Environment Variables**: Set per-environment configuration
- **SSH Tunneling**: Secure connections to remote environments
- **Cloud Integration**: Connect to cloud-based Cuttle instances
- **Load Balancing**: Automatically distribute across environments
- **Environment Health**: Real-time status monitoring
- **Quick Switch Hotkeys**: Keyboard shortcuts for environment switching

## Summary

The Environment Selector makes it **easy** to work with multiple Cuttle instances:

1. **🔍 Discover** devices on your network
2. **⚙️ Manage** your environments  
3. **🌐 Switch** between devices with one click
4. **💾 Save** environment with pipeline
5. **🔄 Restore** automatically when loading

**No more manually typing IP addresses!** 🎉

---

**Quick Reference:**

- **Dropdown** - Switch environment
- **🔍 Button** - Discover devices  
- **⚙️ Button** - Manage environments
- **Pipeline Saves** - Environment is saved
- **Pipeline Opens** - Environment restored
- **Local is Default** - Always available

Happy distributed computing! 🚀


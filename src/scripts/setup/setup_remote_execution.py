#!/usr/bin/env python3
"""
Remote Execution Setup Tool
Helps configure and test remote execution capabilities
"""

import socket
import sys
import platform
import subprocess
from pathlib import Path

def get_local_ip():
    """Get the local IP address"""
    try:
        # Create a socket to determine the local IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        return local_ip
    except Exception:
        return "Unable to determine"

def check_port_available(port=8080):
    """Check if the port is available"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex(('localhost', port))
        sock.close()
        return result != 0  # Port is available if connection fails
    except Exception:
        return False

def get_network_info():
    """Get network interface information"""
    hostname = socket.gethostname()
    local_ip = get_local_ip()
    
    return {
        'hostname': hostname,
        'local_ip': local_ip,
        'platform': platform.system()
    }

def test_remote_connection(host, port=8080):
    """Test connection to a remote host"""
    import requests
    try:
        response = requests.get(f"http://{host}:{port}/api/health", timeout=3)
        if response.status_code == 200:
            data = response.json()
            return True, data
        return False, "Server responded but health check failed"
    except requests.exceptions.ConnectionError:
        return False, "Cannot connect to host"
    except requests.exceptions.Timeout:
        return False, "Connection timeout"
    except Exception as e:
        return False, str(e)

def print_header(text):
    """Print a formatted header"""
    print("\n" + "=" * 70)
    print(f"  {text}")
    print("=" * 70)

def print_section(title):
    """Print a section title"""
    print(f"\n{title}")
    print("-" * 70)

def main():
    print_header("🌐 Cuttle Remote Execution Setup")
    
    # Get network info
    info = get_network_info()
    
    print_section("📡 Network Information")
    print(f"Hostname:    {info['hostname']}")
    print(f"Local IP:    {info['local_ip']}")
    print(f"Platform:    {info['platform']}")
    
    # Check if server is running
    print_section("🔍 Server Status Check")
    port_available = check_port_available(8080)
    
    if not port_available:
        print("✅ Server appears to be running on port 8080")
        
        # Test local connection
        success, result = test_remote_connection('localhost')
        if success:
            print(f"✅ Local health check passed")
            if isinstance(result, dict):
                print(f"   Service: {result.get('service', 'unknown')}")
                print(f"   Version: {result.get('version', 'unknown')}")
                print(f"   Hostname: {result.get('hostname', 'unknown')}")
        else:
            print(f"⚠️  Local health check failed: {result}")
    else:
        print("❌ Server is NOT running on port 8080")
        print("\n💡 To start the server, run:")
        print("   python start_node_editor.py")
    
    # Network access instructions
    print_section("🌐 Network Access")
    print(f"This machine can be accessed at:")
    print(f"   http://{info['local_ip']}:8080")
    print(f"\nOther devices on your network can access:")
    print(f"   • Node Editor:   http://{info['local_ip']}:8080/node_editor.html")
    print(f"   • Chat Page:     http://{info['local_ip']}:8080/chat_page.html")
    print(f"   • Control Panel: http://{info['local_ip']}:8080/control_panel.html")
    
    # Firewall instructions
    print_section("🛡️ Firewall Configuration")
    if info['platform'] == 'Windows':
        print("Windows Firewall:")
        print("   Run this command as Administrator to allow connections:")
        print(f"   netsh advfirewall firewall add rule name=\"Cuttle\" dir=in action=allow protocol=TCP localport=8080")
    elif info['platform'] == 'Linux':
        print("Linux Firewall (ufw):")
        print("   sudo ufw allow 8080/tcp")
    elif info['platform'] == 'Darwin':
        print("macOS Firewall:")
        print("   System Preferences > Security & Privacy > Firewall > Firewall Options")
        print("   Add Python or allow incoming connections")
    
    # Remote host testing
    print_section("🔗 Test Remote Connection")
    print("Enter the IP address of a remote Cuttle instance to test")
    print("(Press Enter to skip)")
    
    try:
        remote_ip = input("Remote IP: ").strip()
        if remote_ip:
            print(f"\nTesting connection to {remote_ip}:8080...")
            success, result = test_remote_connection(remote_ip)
            
            if success:
                print(f"✅ Successfully connected to {remote_ip}")
                if isinstance(result, dict):
                    print(f"   Remote Hostname: {result.get('hostname', 'unknown')}")
                    print(f"   Remote Service:  {result.get('service', 'unknown')}")
                    print(f"   Bot Available:   {result.get('bot_available', False)}")
                
                # Offer to add to hosts
                print("\n💡 You can add this host in the Node Editor using the")
                print("   'Remote Execute' or 'Remote System Info' nodes")
            else:
                print(f"❌ Connection failed: {result}")
                print("\n💡 Troubleshooting:")
                print("   1. Ensure Cuttle is running on the remote machine")
                print("   2. Check that both machines are on the same network")
                print("   3. Verify firewall settings on the remote machine")
                print("   4. Try pinging the remote machine: ping", remote_ip)
    except KeyboardInterrupt:
        print("\n\nSkipped.")
    
    # Discovery instructions
    print_section("🔍 Network Discovery")
    print("You can discover all Cuttle instances on your network using:")
    print("   1. In the Node Editor: Add a 'Discover Hosts' node")
    print("   2. Via Python API:")
    print("      from remote_executor import get_remote_executor")
    print("      executor = get_remote_executor()")
    print("      hosts = executor.discover_hosts()")
    print("   3. Via REST API:")
    print(f"      curl -X POST http://localhost:8080/api/remote/discover")
    
    # Quick start
    print_section("🚀 Quick Start Guide")
    print("1. Start the Node Editor:")
    print("   python start_node_editor.py")
    print("\n2. On another device, open:")
    print(f"   http://{info['local_ip']}:8080/node_editor.html")
    print("\n3. In the Node Editor:")
    print("   • Add a 'Manual Trigger' node")
    print("   • Add a 'Remote Execute' node")
    print("   • Configure the remote host (e.g., 192.168.1.100:8080)")
    print("   • Set a command (e.g., 'take screenshot')")
    print("   • Connect them and click Run!")
    
    # Additional resources
    print_section("📚 Documentation")
    print("For more information, see:")
    print("   • REMOTE_EXECUTION_GUIDE.md - Complete guide")
    print("   • Node Editor - Built-in help for each node type")
    print("   • API Documentation - /api/health endpoint")
    
    print_header("Setup Complete! 🎉")
    print("\n💡 Next steps:")
    print("   1. If server isn't running: python start_node_editor.py")
    print("   2. Configure firewall if needed (see above)")
    print(f"   3. Access from other devices: http://{info['local_ip']}:8080")
    print("   4. Try the 'Discover Hosts' node to find other Cuttle instances")
    print("\n")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nSetup cancelled.")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


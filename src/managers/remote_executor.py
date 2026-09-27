"""
Remote Pipeline Executor
Enables execution of commands and pipelines on remote machines via REST API
"""

import requests
import json
import time
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
import socket
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@dataclass
class RemoteHost:
    """Configuration for a remote host"""
    name: str
    host: str
    port: int = 8080
    timeout: int = 30
    
    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"
    
    def __str__(self):
        return f"{self.name} ({self.host}:{self.port})"


class RemoteExecutor:
    """Execute commands and pipelines on remote machines"""
    
    def __init__(self):
        self.hosts: Dict[str, RemoteHost] = {}
        self.last_results: Dict[str, Any] = {}
    
    def add_host(self, name: str, host: str, port: int = 8080, timeout: int = 30):
        """Add a remote host to the executor"""
        self.hosts[name] = RemoteHost(name, host, port, timeout)
        logger.info(f"Added remote host: {self.hosts[name]}")
    
    def remove_host(self, name: str):
        """Remove a remote host"""
        if name in self.hosts:
            del self.hosts[name]
            logger.info(f"Removed remote host: {name}")
    
    def list_hosts(self) -> List[Dict[str, Any]]:
        """List all configured hosts"""
        return [
            {
                'name': host.name,
                'host': host.host,
                'port': host.port,
                'status': self.check_host_status(name)
            }
            for name, host in self.hosts.items()
        ]
    
    def check_host_status(self, host_name: str) -> str:
        """Check if a remote host is online"""
        if host_name not in self.hosts:
            return "unknown"
        
        host = self.hosts[host_name]
        try:
            response = requests.get(
                f"{host.base_url}/api/health",
                timeout=5
            )
            if response.status_code == 200:
                return "online"
            return "error"
        except requests.exceptions.ConnectionError:
            return "offline"
        except requests.exceptions.Timeout:
            return "timeout"
        except Exception as e:
            logger.error(f"Error checking host {host_name}: {e}")
            return "error"
    
    def execute_command(
        self,
        host_name: str,
        command: str,
        context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Execute a command on a remote host
        
        Args:
            host_name: Name of the remote host
            command: Command string to execute
            context: Optional context data
            
        Returns:
            Dictionary containing execution result
        """
        if host_name not in self.hosts:
            return {
                'success': False,
                'error': f"Host '{host_name}' not found"
            }
        
        host = self.hosts[host_name]
        
        try:
            logger.info(f"Executing command on {host}: {command[:50]}...")
            
            response = requests.post(
                f"{host.base_url}/api/remote/execute",
                json={
                    'command': command,
                    'context': context or {}
                },
                timeout=host.timeout
            )
            
            result = response.json()
            self.last_results[host_name] = result
            
            logger.info(f"Command executed successfully on {host}")
            return result
            
        except requests.exceptions.ConnectionError:
            error_msg = f"Cannot connect to {host}"
            logger.error(error_msg)
            return {
                'success': False,
                'error': error_msg
            }
        except requests.exceptions.Timeout:
            error_msg = f"Request timeout for {host}"
            logger.error(error_msg)
            return {
                'success': False,
                'error': error_msg
            }
        except Exception as e:
            error_msg = f"Error executing command on {host}: {str(e)}"
            logger.error(error_msg)
            return {
                'success': False,
                'error': error_msg
            }
    
    def execute_pipeline(
        self,
        host_name: str,
        pipeline_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Execute a pipeline on a remote host
        
        Args:
            host_name: Name of the remote host
            pipeline_data: Pipeline JSON data
            
        Returns:
            Dictionary containing execution result
        """
        if host_name not in self.hosts:
            return {
                'success': False,
                'error': f"Host '{host_name}' not found"
            }
        
        host = self.hosts[host_name]
        
        try:
            logger.info(f"Executing pipeline on {host}...")
            
            response = requests.post(
                f"{host.base_url}/api/remote/execute-pipeline",
                json=pipeline_data,
                timeout=host.timeout
            )
            
            result = response.json()
            self.last_results[host_name] = result
            
            logger.info(f"Pipeline executed successfully on {host}")
            return result
            
        except requests.exceptions.ConnectionError:
            error_msg = f"Cannot connect to {host}"
            logger.error(error_msg)
            return {
                'success': False,
                'error': error_msg
            }
        except requests.exceptions.Timeout:
            error_msg = f"Request timeout for {host}"
            logger.error(error_msg)
            return {
                'success': False,
                'error': error_msg
            }
        except Exception as e:
            error_msg = f"Error executing pipeline on {host}: {str(e)}"
            logger.error(error_msg)
            return {
                'success': False,
                'error': error_msg
            }
    
    def get_system_info(self, host_name: str) -> Dict[str, Any]:
        """Get system information from a remote host"""
        if host_name not in self.hosts:
            return {
                'success': False,
                'error': f"Host '{host_name}' not found"
            }
        
        host = self.hosts[host_name]
        
        try:
            response = requests.get(
                f"{host.base_url}/api/remote/system-info",
                timeout=10
            )
            
            return response.json()
            
        except Exception as e:
            logger.error(f"Error getting system info from {host}: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def broadcast_command(
        self,
        command: str,
        context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Dict[str, Any]]:
        """
        Execute a command on all registered hosts
        
        Args:
            command: Command to execute
            context: Optional context data
            
        Returns:
            Dictionary mapping host names to results
        """
        results = {}
        
        for host_name in self.hosts.keys():
            results[host_name] = self.execute_command(host_name, command, context)
        
        return results
    
    def discover_hosts(self, port: int = 8080) -> List[str]:
        """
        Discover Cuttle instances on the local network
        
        Args:
            port: Port to scan for
            
        Returns:
            List of discovered host addresses
        """
        discovered = []
        
        # Get local network prefix
        try:
            hostname = socket.gethostname()
            local_ip = socket.gethostbyname(hostname)
            network_prefix = '.'.join(local_ip.split('.')[:-1])
            
            logger.info(f"Scanning network {network_prefix}.0/24 on port {port}...")
            
            # Scan common local network range
            for i in range(1, 255):
                host = f"{network_prefix}.{i}"
                try:
                    response = requests.get(
                        f"http://{host}:{port}/api/health",
                        timeout=1
                    )
                    if response.status_code == 200:
                        data = response.json()
                        if data.get('service') == 'cuttle':
                            discovered.append(host)
                            logger.info(f"✓ Found Cuttle instance at {host}")
                except:
                    pass
            
            logger.info(f"Discovery complete. Found {len(discovered)} hosts.")
            return discovered
            
        except Exception as e:
            logger.error(f"Error during network discovery: {e}")
            return []


# Global executor instance
remote_executor = RemoteExecutor()


def execute_remote(
    host: str,
    command: str,
    context: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Execute a command on a remote host (convenience function)
    
    Args:
        host: Host name or address
        command: Command to execute
        context: Optional context
        
    Returns:
        Execution result
    """
    # If host is an IP/hostname not in registry, add it temporarily
    if host not in remote_executor.hosts:
        # Check if it's an IP address or hostname
        if ':' in host:
            addr, port = host.split(':')
            remote_executor.add_host(host, addr, int(port))
        else:
            remote_executor.add_host(host, host)
    
    return remote_executor.execute_command(host, command, context)


def get_remote_executor() -> RemoteExecutor:
    """Get the global remote executor instance"""
    return remote_executor


if __name__ == "__main__":
    # Example usage
    print("Remote Executor Test")
    print("=" * 50)
    
    # Create executor
    executor = RemoteExecutor()
    
    # Add a host
    executor.add_host("laptop", "192.168.1.100", 8080)
    
    # List hosts
    print("\nConfigured hosts:")
    for host in executor.list_hosts():
        print(f"  • {host['name']} ({host['host']}:{host['port']}) - {host['status']}")
    
    # Check if you want to test discovery
    print("\nTo test network discovery, uncomment the following:")
    print("# discovered = executor.discover_hosts()")
    print("# print(f'Discovered {len(discovered)} hosts')")


"""
Optional mDNS discovery for Cuttle.
Advertises _cuttle._tcp on the LAN so other devices can discover the Cuttle backend.
Requires: pip install zeroconf (optional).
"""

import socket
import threading
from typing import Optional

_service: Optional[object] = None
_zeroconf: Optional[object] = None


def default_mdns_port() -> int:
    """Configured primary HTTPS port for mDNS advertisement."""
    from api.server_ports import resolve_with_env_file

    return resolve_with_env_file().https


def start_mdns(port: Optional[int] = None, name: str = "Cuttle") -> bool:
    """Start mDNS advertisement. Returns True if started, False if zeroconf not available."""
    if port is None:
        port = default_mdns_port()
    try:
        from zeroconf import ServiceInfo, Zeroconf
    except ImportError:
        print("[DISCOVERY] mDNS skipped: zeroconf not installed (pip install zeroconf)")
        return False
    global _service, _zeroconf
    try:
        hostname = socket.gethostname()
        local_ip = _get_local_ip()
        if not local_ip:
            return False
        info = ServiceInfo(
            "_cuttle._tcp.local.",
            f"{name}._cuttle._tcp.local.",
            addresses=[socket.inet_aton(local_ip)],
            port=port,
            properties={"path": "/", "version": "1.0"},
            server=f"{hostname}.local.",
        )
        _zeroconf = Zeroconf()
        _zeroconf.register_service(info)
        _service = info
        print(f"[DISCOVERY] mDNS advertising Cuttle at {local_ip}:{port}")
        return True
    except Exception as e:
        print(f"[DISCOVERY] mDNS error: {e}")
        return False


def stop_mdns() -> None:
    """Stop mDNS advertisement."""
    global _service, _zeroconf
    if _zeroconf and _service:
        try:
            _zeroconf.unregister_service(_service)
            _zeroconf.close()
        except Exception:
            pass
        _service = None
        _zeroconf = None


def _get_local_ip() -> Optional[str]:
    """Get a non-loopback local IP for mDNS advertisement."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        pass
    return None

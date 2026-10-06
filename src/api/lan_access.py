"""
LAN access helpers for Cuttle's web portal.

When enabled, Flask listens on all interfaces (0.0.0.0) so phones/tablets on the
same Wi‑Fi can open the UI. Inbound access is restricted on Windows to the Private
firewall profile and LocalSubnet only — not the public internet.

Enable via Settings → Devices (machine_settings.json after migration):
  "discovery": { "lan_access_enabled": true }
or env CUTTLE_LAN_ACCESS=1 (overrides settings).
"""

from __future__ import annotations

import ipaddress
import os
import socket
import subprocess
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

# Recent inbound LAN probes (for phone connectivity diagnosis).
_lan_probes: Deque[Dict[str, Any]] = deque(maxlen=30)

from api.server_ports import (
    DEFAULT_HTTP_PORT,
    DEFAULT_HTTPS_PORT,
    DEFAULT_PHONE_HTTPS_PORT,
    resolve_with_env_file,
)

_FIREWALL_RULE_HTTPS = "Cuttle LAN HTTPS (LocalSubnet)"
_FIREWALL_RULE_HTTP = "Cuttle LAN HTTP (LocalSubnet)"
_FIREWALL_RULE_HTTP_OPEN = "Cuttle LAN HTTP (Open LAN)"
_FIREWALL_RULE_HTTP_ALT = "Cuttle LAN HTTP alt (8000)"
# Legacy default snapshots (import compat only). Listener ports are owned by
# api.server_ports (env-only); use the get_*_port() helpers for live values.
LAN_PHONE_HTTPS_PORT = DEFAULT_PHONE_HTTPS_PORT  # HTTPS for phones
LAN_HTTP_FALLBACK_PORT = DEFAULT_HTTP_PORT  # plain HTTP fallback
LAN_HTTP_PORT = LAN_PHONE_HTTPS_PORT  # backwards compat
PRIMARY_HTTPS_PORT = DEFAULT_HTTPS_PORT


def get_primary_https_port() -> int:
    """Live primary HTTPS port (raises on malformed config, never defaults)."""
    return resolve_with_env_file().https


def get_http_fallback_port() -> int:
    """Live companion HTTP port (raises on malformed config, never defaults)."""
    return resolve_with_env_file().http


def get_phone_https_port() -> int:
    """Live phone HTTPS port (raises on malformed config, never defaults)."""
    return resolve_with_env_file().phone_https


def _loopback_cors_origins(ports) -> List[str]:
    # Electron desktop prefers the plain-HTTP companion (avoids Chromium HTTPS pool wedge).
    return [
        f"http://localhost:{ports.https}",
        f"http://127.0.0.1:{ports.https}",
        f"https://localhost:{ports.https}",
        f"https://127.0.0.1:{ports.https}",
        f"http://localhost:{ports.http}",
        f"http://127.0.0.1:{ports.http}",
    ]


_NON_PORT_CORS_ORIGINS = (
    "app://cuttle",
    # Capacitor mobile app (LAN client) — scheme://localhost with no port
    "https://localhost",
    "http://localhost",
    "capacitor://localhost",
    "ionic://localhost",
)


def record_lan_probe(remote_addr: str, user_agent: str = "", path: str = "") -> None:
    """Remember recent non-loopback hits (phone troubleshooting)."""
    addr = (remote_addr or "").strip()
    if not addr or addr in ("127.0.0.1", "::1"):
        return
    _lan_probes.appendleft(
        {
            "ip": addr,
            "at": time.time(),
            "path": path or "",
            "user_agent": (user_agent or "")[:200],
        }
    )


def get_recent_lan_probes() -> List[Dict[str, Any]]:
    return list(_lan_probes)


def get_pc_subnet_hint() -> Dict[str, Any]:
    """What IP range phones on the same Wi‑Fi should use."""
    ip = get_lan_ipv4()
    if not ip:
        return {}
    prefix = 24
    try:
        if sys.platform == "win32":
            check = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    f"(Get-NetIPAddress -AddressFamily IPv4 | Where-Object {{ $_.IPAddress -eq '{ip}' }} | "
                    f"Select-Object -First 1 -ExpandProperty PrefixLength)",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if check.returncode == 0 and (check.stdout or "").strip().isdigit():
                prefix = int(check.stdout.strip())
    except Exception:
        pass
    try:
        net = ipaddress.IPv4Network(f"{ip}/{prefix}", strict=False)
        return {
            "pc_ip": ip,
            "prefix_length": prefix,
            "network": str(net.network_address),
            "netmask": str(net.netmask),
            "phone_ip_must_be_in": str(net),
            "example_ok": f"{net.network_address + 10} (example)",
            "example_bad": "192.168.1.x if PC is 192.168.4.x — different network",
        }
    except Exception:
        return {"pc_ip": ip, "phone_ip_must_be_in": f"{'.'.join(ip.split('.')[:3])}.x"}


def get_lan_ipv4() -> Optional[str]:
    """Best-effort primary LAN IPv4 (not loopback)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127."):
                return ip
    except Exception:
        pass
    return None


def is_lan_access_enabled() -> bool:
    env = (os.getenv("CUTTLE_LAN_ACCESS") or "").strip().lower()
    if env in ("1", "true", "yes", "on"):
        return True
    if env in ("0", "false", "no", "off"):
        return False
    try:
        from managers.settings_manager import get_settings_manager

        discovery = get_settings_manager().get_setting("discovery") or {}
        return bool(discovery.get("lan_access_enabled"))
    except Exception:
        return False


def resolve_bind_host(lan_enabled: Optional[bool] = None) -> str:
    if lan_enabled is None:
        lan_enabled = is_lan_access_enabled()
    return "0.0.0.0" if lan_enabled else "127.0.0.1"


def build_cors_origins(lan_ip: Optional[str] = None) -> List[str]:
    ports = resolve_with_env_file()
    origins = _loopback_cors_origins(ports) + list(_NON_PORT_CORS_ORIGINS)
    if not is_lan_access_enabled():
        return origins
    ip = lan_ip or get_lan_ipv4()
    if ip:
        origins.extend(
            [
                f"https://{ip}:{ports.https}",
                f"http://{ip}:{ports.https}",
                f"https://{ip}:{ports.phone_https}",
                f"http://{ip}:{ports.http}",
            ]
        )
    return origins


def cert_needs_regeneration(cert_file: Path, lan_ip: Optional[str]) -> bool:
    """True if cert is missing or does not include required SAN entries."""
    if not cert_file.is_file():
        return True
    try:
        from cryptography import x509

        cert = x509.load_pem_x509_certificate(cert_file.read_bytes())
        ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        names = list(ext.value)
        has_localhost_dns = any(
            isinstance(n, x509.DNSName) and n.value == "localhost" for n in names
        )
        has_loopback = any(
            isinstance(n, x509.IPAddress) and str(n.value) == "127.0.0.1" for n in names
        )
        if not (has_localhost_dns and has_loopback):
            return True
        if lan_ip:
            has_lan = any(
                isinstance(n, x509.IPAddress) and str(n.value) == lan_ip for n in names
            )
            if not has_lan:
                return True
        return False
    except Exception:
        return True


def ensure_windows_lan_firewall_rule(port: Optional[int] = None) -> bool:
    """Allow inbound TCP from LocalSubnet (same Wi‑Fi/LAN only).

    Uses Private+Public profiles so home Wi‑Fi still works when Windows marks it Public.
    RemoteAddress LocalSubnet prevents wide-open internet exposure without router port-forward.
    ``None`` means the configured primary HTTPS port.
    """
    if port is None:
        port = get_primary_https_port()
    if sys.platform != "win32":
        return False
    rule_name = {
        get_primary_https_port(): _FIREWALL_RULE_HTTPS,
        get_phone_https_port(): _FIREWALL_RULE_HTTP,
        get_http_fallback_port(): _FIREWALL_RULE_HTTP_ALT,
    }.get(port, f"Cuttle LAN port {port}")
    try:
        check = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"(Get-NetFirewallRule -DisplayName '{rule_name}' -ErrorAction SilentlyContinue | "
                f"Where-Object {{ $_.Enabled -eq 'True' }} | Measure-Object).Count",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if check.returncode == 0 and (check.stdout or "").strip() not in ("", "0"):
            return True
        create = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"New-NetFirewallRule -DisplayName '{rule_name}' "
                f"-Direction Inbound -Protocol TCP -LocalPort {port} -Action Allow "
                f"-Profile Private,Public -RemoteAddress LocalSubnet",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if create.returncode != 0:
            print(f"[LAN] Firewall rule failed ({rule_name}): {(create.stderr or create.stdout or '').strip()}")
            return False
        print(f"[LAN] Firewall rule added: {rule_name} (LocalSubnet, ports {port})")
        return True
    except Exception as e:
        print(f"[LAN] Firewall rule skipped: {e}")
        return False


def ensure_windows_lan_firewall_rule_open(port: int = LAN_HTTP_PORT) -> bool:
    """Fallback: allow inbound HTTP from any source on LAN port.

    Still not reachable from the public internet without router port-forwarding.
    Helps when LocalSubnet classification blocks some phones/routers.
    """
    if sys.platform != "win32":
        return False
    rule_name = _FIREWALL_RULE_HTTP_OPEN
    try:
        check = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"(Get-NetFirewallRule -DisplayName '{rule_name}' -ErrorAction SilentlyContinue | "
                f"Where-Object {{ $_.Enabled -eq 'True' }} | Measure-Object).Count",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if check.returncode == 0 and (check.stdout or "").strip() not in ("", "0"):
            return True
        create = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"New-NetFirewallRule -DisplayName '{rule_name}' "
                f"-Direction Inbound -Protocol TCP -LocalPort {port} -Action Allow "
                f"-Profile Private,Public -RemoteAddress Any",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if create.returncode != 0:
            print(f"[LAN] Open firewall rule failed: {(create.stderr or create.stdout or '').strip()}")
            return False
        print(f"[LAN] Firewall rule added: {rule_name} (Any, port {port})")
        return True
    except Exception as e:
        print(f"[LAN] Open firewall rule skipped: {e}")
        return False


def ensure_all_lan_firewall_rules() -> bool:
    if sys.platform != "win32":
        # Desktop Linux typically has no Windows-style LAN firewall block.
        return True
    ok_https = ensure_windows_lan_firewall_rule(get_primary_https_port())
    ok_phone = ensure_windows_lan_firewall_rule(get_phone_https_port())
    ok_alt = ensure_windows_lan_firewall_rule(get_http_fallback_port())
    ensure_windows_lan_firewall_rule_open(get_phone_https_port())
    return ok_https and ok_phone and ok_alt


def windows_firewall_rule_active() -> bool:
    if sys.platform != "win32":
        return False
    try:
        names = (_FIREWALL_RULE_HTTPS, _FIREWALL_RULE_HTTP)
        for rule_name in names:
            check = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    f"(Get-NetFirewallRule -DisplayName '{rule_name}' -ErrorAction SilentlyContinue | "
                    f"Where-Object {{ $_.Enabled -eq 'True' }} | Measure-Object).Count",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if check.returncode != 0 or (check.stdout or "").strip() in ("", "0"):
                return False
        return True
    except Exception:
        return False


def wifi_network_category() -> Optional[str]:
    """Return 'Public', 'Private', 'Domain', or None."""
    if sys.platform != "win32":
        return None
    try:
        check = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-NetConnectionProfile | Where-Object { $_.IPv4Connectivity -eq 'Internet' } | Select-Object -First 1 -ExpandProperty NetworkCategory)",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        val = (check.stdout or "").strip()
        return val or None
    except Exception:
        return None


def lan_phone_portal_url(port: Optional[int] = None, lan_ip: Optional[str] = None) -> Optional[str]:
    """HTTPS URL for phones. ``None`` port means the configured phone port."""
    if not is_lan_access_enabled():
        return None
    if port is None:
        port = get_phone_https_port()
    ip = lan_ip or get_lan_ipv4()
    if not ip:
        return None
    return f"https://{ip}:{port}"


def lan_phone_http_fallback_url(lan_ip: Optional[str] = None) -> Optional[str]:
    """Plain HTTP fallback URL for phones (configured companion HTTP port)."""
    if not is_lan_access_enabled():
        return None
    ip = lan_ip or get_lan_ipv4()
    if not ip:
        return None
    return f"http://{ip}:{get_http_fallback_port()}"


def lan_portal_url(port: Optional[int] = None, lan_ip: Optional[str] = None) -> Optional[str]:
    """Primary-portal URL. ``None`` port means the configured primary HTTPS port."""
    if not is_lan_access_enabled():
        return None
    if port is None:
        port = get_primary_https_port()
    ip = lan_ip or get_lan_ipv4()
    if not ip:
        return None
    return f"https://{ip}:{port}"

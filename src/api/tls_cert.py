"""Host TLS certificate: the self-signed localhost/LAN cert and its key pin.

Owner of ``<home>/secrets/localhost.pem`` + ``localhost-key.pem``. Remote
desktop Clients pin the certificate's *public key* (trust on first use, see
``electron/tls-trust.js``), so a regeneration for a new LAN IP keeps the
existing private key: the SAN changes, the pin does not. Only a missing or
unreadable key produces a new one (Clients then ask the user to re-trust).

Python clients of a remote Host (client daemon, remote worker loop, SSH
approval transport) verify the same pin with :func:`urlopen`: loopback keeps
the local self-signed exception, a remote HTTPS Host must present the pinned
key, and there is no unverified fallback. (The Electron sidecar carries a
stdlib-only copy because it runs without this package.)

CLI (run on the Host, compare with the fingerprint a Client shows)::

    python -m api.tls_cert fingerprint
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.client
import ipaddress
import json
import os
import ssl
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Tuple

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from core.runtime_paths import secrets_dir


def cert_paths() -> Tuple[Path, Path]:
    d = secrets_dir()
    return d / "localhost.pem", d / "localhost-key.pem"


def _load_key(key_file: Path) -> Optional[rsa.RSAPrivateKey]:
    try:
        key = serialization.load_pem_private_key(key_file.read_bytes(), password=None)
    except Exception:
        return None
    return key if isinstance(key, rsa.RSAPrivateKey) else None


def _write_private(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    tmp.replace(path)


def ensure_certificate(lan_ip: Optional[str] = None, force_regenerate: bool = False) -> Tuple[str, str]:
    """Return (cert_path, key_path), (re)issuing the cert when its SAN is stale."""
    from api.lan_access import cert_needs_regeneration

    cert_file, key_file = cert_paths()
    cert_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not force_regenerate and key_file.is_file() and not cert_needs_regeneration(cert_file, lan_ip):
        print("[HTTPS] Using existing SSL certificate.")
        return str(cert_file), str(key_file)

    key = None if force_regenerate else _load_key(key_file)
    if key is None:
        print("[HTTPS] Generating new key and self-signed SSL certificate...")
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        _write_private(key_file, key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ))
    else:
        reason = "LAN IP changed" if lan_ip else "certificate SAN mismatch"
        print(f"[HTTPS] Re-issuing self-signed SSL certificate with the existing key ({reason})...")

    name = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Cuttle"),
        x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
    ])
    san = [x509.DNSName("localhost"), x509.IPAddress(ipaddress.IPv4Address("127.0.0.1"))]
    if lan_ip:
        try:
            san.append(x509.IPAddress(ipaddress.IPv4Address(lan_ip)))
        except ValueError:
            pass
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .sign(key, hashes.SHA256())
    )
    _write_private(cert_file, cert.public_bytes(serialization.Encoding.PEM))
    print(f"[HTTPS] Certificate: {cert_file} (key fingerprint {format_fingerprint(spki_sha256(cert))})")
    return str(cert_file), str(key_file)


def spki_sha256(cert: x509.Certificate) -> str:
    """Base64 SHA-256 of the DER SubjectPublicKeyInfo (the Client's pin value)."""
    spki = cert.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return base64.b64encode(hashlib.sha256(spki).digest()).decode("ascii")


def format_fingerprint(b64: str) -> str:
    """``AB:CD:…`` form shown by the desktop trust prompt."""
    raw = base64.b64decode(b64)
    return ":".join(f"{b:02X}" for b in raw)


# --- client-side pin verification ------------------------------------------------

PIN_ENV = "CUTTLE_COORDINATOR_TLS_SPKI_SHA256"


class TlsPinError(ssl.SSLError):
    """Remote HTTPS peer is unpinned or presented a different key."""


def peer_spki_sha256(cert_der: bytes) -> str:
    return spki_sha256(x509.load_der_x509_certificate(cert_der))


def is_loopback_url(url: str) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").strip("[]").lower()
    if host in ("localhost", "::1"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Checks the peer key against the pin before any request byte is sent."""

    expected_spki = ""

    def connect(self) -> None:
        super().connect()
        der = self.sock.getpeercert(binary_form=True) or b""
        try:
            actual = peer_spki_sha256(der) if der else ""
        except Exception:
            actual = ""
        if not actual or actual != self.expected_spki:
            self.sock.close()
            raise TlsPinError("remote Host certificate key does not match its pin")


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, expected_spki: str) -> None:
        ctx = ssl.create_default_context()
        # The key pin replaces chain/hostname checks (self-signed LAN Host).
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        super().__init__(context=ctx)
        self._expected_spki = expected_spki

    def https_open(self, req):
        conn = type("PinnedConnection", (_PinnedHTTPSConnection,), {"expected_spki": self._expected_spki})
        return self.do_open(conn, req, context=self._context)


def urlopen(req: urllib.request.Request, *, timeout: float, spki_pin: Optional[str] = None):
    """Open a request to a Cuttle Host.

    Plain HTTP is unchanged. Loopback HTTPS accepts the local self-signed
    certificate. Remote HTTPS requires ``spki_pin`` (default: the
    ``CUTTLE_COORDINATOR_TLS_SPKI_SHA256`` env the desktop app provides) and
    raises :class:`TlsPinError` without it — never an unverified fallback.
    """
    url = req.full_url
    if not url.lower().startswith("https://"):
        return urllib.request.urlopen(req, timeout=timeout)
    if is_loopback_url(url):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return urllib.request.urlopen(req, timeout=timeout, context=ctx)
    pin = (spki_pin if spki_pin is not None else os.environ.get(PIN_ENV, "")).strip()
    if not pin:
        raise TlsPinError(
            f"remote HTTPS Host {urllib.parse.urlsplit(url).netloc} has no pinned certificate key; "
            "connect the desktop app to it once to review and trust the key"
        )
    return urllib.request.build_opener(_PinnedHTTPSHandler(pin)).open(req, timeout=timeout)


def desktop_pin(pins: object, host: str, port: int) -> str:
    """Pin saved by the desktop app (desktop-config.json ``tlsPins``) for host:port."""
    if not isinstance(pins, dict):
        return ""
    entry = pins.get(f"{(host or '').strip('[]').lower()}:{int(port)}")
    spki = entry.get("spki") if isinstance(entry, dict) else ""
    return spki if isinstance(spki, str) else ""


def current_fingerprint() -> Optional[dict]:
    cert_file, _key = cert_paths()
    if not cert_file.is_file():
        return None
    cert = x509.load_pem_x509_certificate(cert_file.read_bytes())
    pin = spki_sha256(cert)
    return {
        "certificate": str(cert_file),
        "spki_sha256": pin,
        "fingerprint": format_fingerprint(pin),
        "not_after": cert.not_valid_after_utc.isoformat(),
    }


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m api.tls_cert")
    sub = parser.add_subparsers(dest="verb", required=True)
    fp = sub.add_parser("fingerprint", help="print the Host certificate key fingerprint")
    fp.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    info = current_fingerprint()
    if info is None:
        print("No Host certificate yet; it is created when Flask first starts.", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(info))
    else:
        print(info["fingerprint"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

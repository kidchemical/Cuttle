"""Host TLS certificate: the self-signed localhost/LAN cert and its key pin.

Owner of ``<home>/secrets/localhost.pem`` + ``localhost-key.pem``. Remote
desktop Clients pin the certificate's *public key* (trust on first use, see
``electron/tls-trust.js``), so a regeneration for a new LAN IP keeps the
existing private key: the SAN changes, the pin does not. Only a missing or
unreadable key produces a new one (Clients then ask the user to re-trust).

CLI (run on the Host, compare with the fingerprint a Client shows)::

    python -m api.tls_cert fingerprint
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import ipaddress
import json
import sys
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

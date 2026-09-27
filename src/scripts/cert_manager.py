"""
Self-signed TLS certificate manager for Cuttle's local HTTPS server.

Generates a 10-year RSA-2048 cert for localhost / 127.0.0.1 and stores it
under src/data/certs/.  Re-generates automatically if the cert is missing or
expires within 30 days.

Usage:
    from scripts.cert_manager import get_or_create_cert
    cert_file, key_file = get_or_create_cert()
    app.run(ssl_context=(cert_file, key_file), ...)
"""

import datetime
import ipaddress
import os
from pathlib import Path

CERT_DIR = Path(__file__).resolve().parent.parent / "data" / "certs"
CERT_FILE = CERT_DIR / "cuttle.crt"
KEY_FILE = CERT_DIR / "cuttle.key"
DAYS_VALID = 3650  # 10 years
RENEW_THRESHOLD_DAYS = 30


def get_or_create_cert() -> tuple[str, str]:
    """Return (cert_path, key_path), generating them if missing or near-expiry."""
    CERT_DIR.mkdir(parents=True, exist_ok=True)

    if CERT_FILE.exists() and KEY_FILE.exists():
        try:
            from cryptography import x509
            cert = x509.load_pem_x509_certificate(CERT_FILE.read_bytes())
            remaining = cert.not_valid_after_utc - datetime.datetime.now(datetime.timezone.utc)
            if remaining.days > RENEW_THRESHOLD_DAYS:
                return str(CERT_FILE), str(KEY_FILE)
            print(f"[CERT] Certificate expires in {remaining.days} days — regenerating.")
        except Exception as e:
            print(f"[CERT] Could not read existing cert ({e}) — regenerating.")

    _generate_cert()
    return str(CERT_FILE), str(KEY_FILE)


def _generate_cert():
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.datetime.now(datetime.timezone.utc)

    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Cuttle Local"),
        ]))
        .issuer_name(x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, "Cuttle Local CA"),
        ]))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=DAYS_VALID))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
            ]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    CERT_FILE.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    KEY_FILE.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ))
    try:
        os.chmod(KEY_FILE, 0o600)
    except OSError:
        pass  # Windows doesn't honour Unix permissions — acceptable for localhost

    print(f"[CERT] Generated self-signed TLS cert at {CERT_FILE}")


if __name__ == "__main__":
    c, k = get_or_create_cert()
    print(f"cert: {c}\nkey:  {k}")

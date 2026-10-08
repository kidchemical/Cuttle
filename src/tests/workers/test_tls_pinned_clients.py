"""Python clients of a remote Host verify its pinned key (api.tls_cert.urlopen).

Loopback HTTPS keeps the local self-signed exception; a remote HTTPS Host
must present the key the desktop app pinned, and an unpinned remote Host is
refused before any request is sent. Uses a throwaway local TLS server on a
non-loopback interface when one exists; never the network or a live Host.
"""

from __future__ import annotations

import http.server
import socket
import ssl
import threading
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from api import tls_cert


def _cert(tmp_path, name):
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / f"{name}.pem", tmp_path / f"{name}-key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ))
    return cert, cert_path, key_path


def _non_loopback_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # TEST-NET: no packet is sent for UDP connect
            ip = s.getsockname()[0]
    except OSError:
        return None
    return None if ip.startswith("127.") else ip


@pytest.fixture
def tls_server(tmp_path):
    ip = _non_loopback_ip()
    if not ip:
        pytest.skip("needs a non-loopback interface")
    cert, cert_path, key_path = _cert(tmp_path, "host")
    hits = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok": true}')

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer((ip, 0), Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert_path, key_path)
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"https://{ip}:{server.server_address[1]}", tls_cert.spki_sha256(cert), hits, tmp_path
    server.shutdown()


def test_remote_https_with_matching_pin(tls_server):
    base, pin, hits, _ = tls_server
    with tls_cert.urlopen(urllib.request.Request(base + "/x"), timeout=5, spki_pin=pin) as resp:
        assert resp.read() == b'{"ok": true}'
    assert hits == ["/x"]


def test_remote_https_wrong_pin_sends_nothing(tls_server):
    base, _pin, hits, tmp_path = tls_server
    other, _c, _k = _cert(tmp_path, "impostor")
    with pytest.raises(Exception) as exc:
        tls_cert.urlopen(urllib.request.Request(base + "/x"), timeout=5, spki_pin=tls_cert.spki_sha256(other))
    assert "pin" in str(exc.value).lower()
    assert hits == []


def test_remote_https_without_pin_is_refused(monkeypatch):
    monkeypatch.delenv(tls_cert.PIN_ENV, raising=False)
    with pytest.raises(tls_cert.TlsPinError):
        tls_cert.urlopen(urllib.request.Request("https://192.168.1.20:8443/api/workers/x"), timeout=1)


def test_worker_client_uses_env_pin(tls_server, monkeypatch):
    from api.device_workers.client import DeviceWorkerClient

    base, pin, hits, _ = tls_server
    monkeypatch.setenv(tls_cert.PIN_ENV, pin)
    assert DeviceWorkerClient(base_url=base, token="t")._request("GET", "/api/workers/ping") == {"ok": True}
    monkeypatch.setenv(tls_cert.PIN_ENV, "AAAA" + pin[4:])
    with pytest.raises(Exception):
        DeviceWorkerClient(base_url=base, token="t")._request("GET", "/api/workers/ping")
    assert hits == ["/api/workers/ping"]


def test_desktop_pin_lookup():
    pins = {"192.168.1.20:8443": {"spki": "abc"}, "fe80::1:8443": {"spki": "v6"}}
    assert tls_cert.desktop_pin(pins, "192.168.1.20", 8443) == "abc"
    assert tls_cert.desktop_pin(pins, "[FE80::1]", 8443) == "v6"
    assert tls_cert.desktop_pin(pins, "192.168.1.20", 8080) == ""
    assert tls_cert.desktop_pin(None, "h", 1) == ""


def test_loopback_detection():
    assert tls_cert.is_loopback_url("https://127.0.0.1:8080/")
    assert tls_cert.is_loopback_url("https://localhost:8080/")
    assert tls_cert.is_loopback_url("https://[::1]:8080/")
    assert not tls_cert.is_loopback_url("https://192.168.1.20:8080/")
    assert not tls_cert.is_loopback_url("https://localhost.evil.example/")

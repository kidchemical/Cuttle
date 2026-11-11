"""The HTTPS listeners must not let one stalled TLS client block the rest."""
import socket
import ssl
import threading
import time
import urllib.request

import pytest
from werkzeug.serving import make_server

from api.tls_cert import ensure_certificate
from api.tls_serving import server_ssl_context


def _app(environ, start_response):
    start_response("200 OK", [("Content-Type", "text/plain")])
    return [b"ok"]


@pytest.fixture()
def cert(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path))
    return ensure_certificate()


def _serve(ssl_context):
    server = make_server("127.0.0.1", 0, _app, threaded=True, ssl_context=ssl_context)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _get(port, timeout=5.0):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(f"https://127.0.0.1:{port}/", timeout=timeout, context=ctx) as resp:
        return resp.read()


def test_stalled_handshake_does_not_block_other_clients(cert):
    server = _serve(server_ssl_context(*cert))
    stalled = socket.create_connection(("127.0.0.1", server.server_port))
    try:
        time.sleep(0.2)  # the accept loop has picked the silent client up
        started = time.monotonic()
        assert _get(server.server_port) == b"ok"
        assert time.monotonic() - started < 3
    finally:
        stalled.close()
        server.shutdown()


def test_plain_werkzeug_context_blocks_which_is_why_this_exists(cert):
    """Characterizes the stock behaviour the helper replaces."""
    server = _serve(cert)
    stalled = socket.create_connection(("127.0.0.1", server.server_port))
    try:
        time.sleep(0.2)
        with pytest.raises(OSError):
            _get(server.server_port, timeout=1.0)
    finally:
        stalled.close()
        server.shutdown()


def test_handshake_timeout_and_garbage_end_the_connection_quietly(cert, capfd):
    server = _serve(server_ssl_context(*cert, handshake_timeout=0.3))
    try:
        silent = socket.create_connection(("127.0.0.1", server.server_port))
        silent.settimeout(3)
        assert silent.recv(1) == b""  # server gave up and closed it
        silent.close()
        junk = socket.create_connection(("127.0.0.1", server.server_port))
        junk.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")  # plain HTTP on the TLS port
        junk.settimeout(3)
        try:
            junk.recv(64)
        except OSError:
            pass
        junk.close()
        assert _get(server.server_port) == b"ok"
        time.sleep(0.2)
        assert "Traceback" not in capfd.readouterr().err
    finally:
        server.shutdown()

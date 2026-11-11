"""Connection reuse on the threaded listeners: when it happens and when it must not."""
import http.client
import logging
import socket
import ssl
import threading
import time

import pytest
from flask import Flask, Response, request
from werkzeug.serving import make_server

from api.keepalive_serving import KeepAliveRequestHandler, request_handler


def _flask_app():
    app = Flask(__name__)

    @app.get("/port")
    def port():
        return str(request.environ["REMOTE_PORT"])

    @app.post("/echo")
    def echo():
        return request.get_data()

    @app.post("/ignore-body")
    def ignore_body():
        return "ignored", 401

    @app.get("/stream")
    def stream():
        return Response((f"part{i};".encode() for i in range(5)), mimetype="text/plain")

    @app.get("/stream-fails")
    def stream_fails():
        def body():
            yield b"first;"
            raise RuntimeError("generator failed")
        return Response(body(), mimetype="text/plain")

    @app.get("/short")
    def short():
        def body():
            yield b"only"
        return Response(body(), headers={"Content-Length": "10"})

    @app.get("/empty")
    def empty():
        return "", 204

    @app.get("/app-close")
    def app_close():
        return Response("bye", headers={"Connection": "close"})

    @app.get("/boom")
    def boom():
        raise RuntimeError("handler failed")

    return app


class _Server:
    def __init__(self, handler=KeepAliveRequestHandler, ssl_context=None, app=None):
        self.server = make_server("127.0.0.1", 0, app or _flask_app(), threaded=True,
                                  request_handler=handler, ssl_context=ssl_context)
        self.port = self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    def connect(self):
        return http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)


def _ask(conn, method, path, body=None, headers=None):
    conn.request(method, path, body=body, headers=headers or {})
    resp = conn.getresponse()
    return resp, resp.read()


def _closed(conn):
    """True when the server has closed its side of the connection."""
    sock = conn.sock
    if sock is None:
        return True
    sock.settimeout(1.0)
    try:
        return sock.recv(1) == b""
    except (TimeoutError, socket.timeout):
        return False
    except OSError:
        return True


@pytest.fixture(autouse=True)
def quiet_werkzeug_log(caplog):
    caplog.set_level(logging.ERROR, logger="werkzeug")
    return caplog


def test_requests_share_one_connection():
    with _Server() as srv:
        conn = srv.connect()
        ports = set()
        for _ in range(5):
            resp, body = _ask(conn, "GET", "/port")
            assert resp.status == 200
            assert resp.getheader("Connection") is None
            ports.add(body)
        assert len(ports) == 1


def test_stock_handler_closes_every_connection_which_is_why_this_exists():
    with _Server(handler=None) as srv:
        conn = srv.connect()
        resp, _ = _ask(conn, "GET", "/port")
        assert resp.getheader("Connection") == "close"
        assert _closed(conn)


def test_body_that_was_read_keeps_the_connection():
    with _Server() as srv:
        conn = srv.connect()
        payload = b"x" * 200_000
        resp, body = _ask(conn, "POST", "/echo", body=payload)
        assert body == payload and resp.getheader("Connection") is None
        resp, body = _ask(conn, "POST", "/echo", body=b"second")
        assert body == b"second"


def test_unread_body_closes_instead_of_being_parsed_as_a_request():
    with _Server() as srv:
        conn = srv.connect()
        # A body that itself looks like a request must never be answered.
        smuggled = b"GET /port HTTP/1.1\r\nHost: x\r\n\r\n"
        resp, body = _ask(conn, "POST", "/ignore-body", body=smuggled)
        assert resp.status == 401 and body == b"ignored"
        assert resp.getheader("Connection") == "close"
        assert _closed(conn)


def test_streamed_response_is_followed_by_another_request():
    with _Server() as srv:
        conn = srv.connect()
        resp, body = _ask(conn, "GET", "/stream")
        assert resp.getheader("Transfer-Encoding") == "chunked"
        assert body == b"part0;part1;part2;part3;part4;"
        first = _ask(conn, "GET", "/port")[1]
        assert _ask(conn, "GET", "/port")[1] == first


def test_stream_that_fails_midway_closes_the_connection():
    with _Server() as srv:
        conn = srv.connect()
        conn.request("GET", "/stream-fails")
        resp = conn.getresponse()
        with pytest.raises(http.client.IncompleteRead):
            resp.read()
        assert _closed(conn)


def test_response_shorter_than_its_content_length_closes_the_connection():
    with _Server() as srv:
        conn = srv.connect()
        conn.request("GET", "/short")
        resp = conn.getresponse()
        with pytest.raises(http.client.IncompleteRead):
            resp.read()
        assert _closed(conn)


def test_handler_error_returns_500_and_keeps_serving():
    with _Server() as srv:
        conn = srv.connect()
        resp, _ = _ask(conn, "GET", "/boom")
        assert resp.status == 500
        assert _ask(conn, "GET", "/port")[0].status == 200


@pytest.mark.parametrize("method,path,status", [("HEAD", "/port", 200), ("GET", "/empty", 204)])
def test_bodyless_responses_keep_the_connection(method, path, status):
    with _Server() as srv:
        conn = srv.connect()
        resp, body = _ask(conn, method, path)
        assert resp.status == status and body == b""
        assert resp.getheader("Connection") is None
        assert _ask(conn, "GET", "/port")[0].status == 200


def test_client_or_application_asking_to_close_is_honoured():
    with _Server() as srv:
        conn = srv.connect()
        resp, _ = _ask(conn, "GET", "/port", headers={"Connection": "close"})
        assert resp.getheader("Connection") == "close" and _closed(conn)
        conn = srv.connect()
        resp, body = _ask(conn, "GET", "/app-close")
        assert body == b"bye" and resp.getheader("Connection") == "close" and _closed(conn)


def test_http_1_0_client_gets_a_closed_connection():
    with _Server() as srv:
        with socket.create_connection(("127.0.0.1", srv.port), timeout=5) as sock:
            sock.sendall(b"GET /empty HTTP/1.0\r\nHost: x\r\n\r\n")
            data = b""
            while chunk := sock.recv(4096):
                data += chunk
        assert b" 204 " in data.split(b"\r\n", 1)[0]
        assert b"connection: close" in data.lower()


def test_upgrade_request_is_never_reused():
    with _Server() as srv:
        conn = srv.connect()
        resp, _ = _ask(conn, "GET", "/port", headers={"Connection": "Upgrade", "Upgrade": "websocket"})
        assert resp.getheader("Connection") == "close" and _closed(conn)


def test_requests_sent_back_to_back_are_both_answered():
    """The next request can arrive before the server has finished the previous
    one. Werkzeug's end-of-request drain must not swallow it."""
    with _Server() as srv:
        with socket.create_connection(("127.0.0.1", srv.port), timeout=5) as sock:
            one = b"POST /echo HTTP/1.1\r\nHost: x\r\nContent-Length: 5\r\n\r\nfirst"
            two = b"POST /echo HTTP/1.1\r\nHost: x\r\nContent-Length: 6\r\nConnection: close\r\n\r\nsecond"
            sock.sendall(one + two)
            data = b""
            while chunk := sock.recv(4096):
                data += chunk
        assert data.count(b"HTTP/1.1 200 OK") == 2
        assert data.index(b"first") < data.index(b"second")


def test_idle_connection_closes_quietly(quiet_werkzeug_log):
    class Impatient(KeepAliveRequestHandler):
        idle_timeout = 0.3

    with _Server(handler=Impatient) as srv:
        conn = srv.connect()
        assert _ask(conn, "GET", "/port")[0].status == 200
        time.sleep(0.1)
        assert _ask(conn, "GET", "/port")[0].status == 200   # still inside the window
        time.sleep(0.8)
        assert _closed(conn)
    assert not [r for r in quiet_werkzeug_log.records if "timed out" in r.getMessage().lower()]


def test_reuse_over_tls_with_the_deferred_handshake_context(tmp_path, monkeypatch):
    from api.tls_cert import ensure_certificate
    from api.tls_serving import server_ssl_context

    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path))
    with _Server(ssl_context=server_ssl_context(*ensure_certificate())) as srv:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        conn = http.client.HTTPSConnection("127.0.0.1", srv.port, timeout=5, context=ctx)
        ports = {_ask(conn, "GET", "/port")[1] for _ in range(4)}
        assert len(ports) == 1
        assert _ask(conn, "POST", "/echo", body=b"tls body")[1] == b"tls body"
        assert _ask(conn, "GET", "/stream")[1].startswith(b"part0;")
        assert _ask(conn, "GET", "/port")[1] in ports


def test_tls_client_leaving_a_stream_is_not_logged_as_a_request_error(tmp_path, monkeypatch, quiet_werkzeug_log):
    from api.tls_cert import ensure_certificate
    from api.tls_serving import server_ssl_context

    app = _flask_app()
    finished = threading.Event()

    @app.get("/forever")
    def forever():
        def body():
            try:
                while True:
                    yield b"data: tick\n\n"
                    time.sleep(0.02)
            finally:
                finished.set()
        return Response(body(), mimetype="text/event-stream")

    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path))
    with _Server(app=app, ssl_context=server_ssl_context(*ensure_certificate())) as srv:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        raw = socket.create_connection(("127.0.0.1", srv.port), timeout=5)
        tls = ctx.wrap_socket(raw)
        tls.sendall(b"GET /forever HTTP/1.1\r\nHost: x\r\n\r\n")
        assert b"200 OK" in tls.recv(4096)
        tls.close()                      # no TLS close_notify: an abrupt drop
        assert finished.wait(5)
        time.sleep(0.2)
    assert not [r for r in quiet_werkzeug_log.records if "Error on request" in r.getMessage()]


def test_websocket_route_still_works():
    simple_websocket = pytest.importorskip("simple_websocket")
    flask_sock = pytest.importorskip("flask_sock")
    app = _flask_app()
    sock = flask_sock.Sock(app)

    @sock.route("/ws")
    def ws_echo(ws):
        ws.send("echo:" + ws.receive(timeout=5))

    with _Server(app=app) as srv:
        client = simple_websocket.Client.connect(f"ws://127.0.0.1:{srv.port}/ws")
        try:
            client.send("hello")
            assert client.receive(timeout=5) == "echo:hello"
        finally:
            try:
                client.close()
            except simple_websocket.ConnectionClosed:
                pass                     # the route returned first and closed it
        assert _ask(srv.connect(), "GET", "/port")[0].status == 200


def test_off_switch_falls_back_to_the_stock_handler():
    assert request_handler({}) is KeepAliveRequestHandler
    for value in ("0", "false", "off"):
        assert request_handler({"CUTTLE_HTTP_KEEPALIVE": value}) is None

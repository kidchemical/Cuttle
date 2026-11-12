"""Persistent HTTP/1.1 connections for Werkzeug's threaded server.

Werkzeug's request handler ends every response with ``Connection: close``, so
each request pays a new TCP connection and, on the HTTPS listeners, a new TLS
handshake. For a phone on Wi-Fi that is three network round trips per request
instead of one, for every poll and every asset.

:class:`KeepAliveRequestHandler` leaves Werkzeug's request handling in charge
and only decides, per response, whether the connection may stay open. It stays
open when all of these hold; otherwise the response closes it exactly as before:

- the client sent an HTTP/1.1 request and did not ask to close;
- the request is not a protocol upgrade (WebSocket handlers own their socket);
- the request body, sized by ``Content-Length``, was read to the end;
- the response was fully framed and fully written (its ``Content-Length``
  bytes, or the final chunk of a chunked body).

A reused connection waits ``idle_timeout`` seconds for its next request, then
closes quietly. The first request on a connection is handled as before.

Werkzeug and stdlib only; no Cuttle imports. Pass the class as
``request_handler`` to ``werkzeug.serving.make_server`` / ``app.run``. Remove
it by not passing it: nothing else depends on this module.
"""

from __future__ import annotations

import os
import ssl

from werkzeug.serving import WSGIRequestHandler

IDLE_TIMEOUT_SEC = 30.0
_CHUNK_END = b"0\r\n\r\n"
_BODYLESS_STATUS = frozenset({204, 304})


class _RequestBody:
    """The current request's body and nothing after it.

    ``wsgi.input`` used to be the raw connection stream. On a reused
    connection the bytes after this body are the next request, so reads stop
    at ``Content-Length``. ``left`` is what the application has not read.
    """

    def __init__(self, stream, length: int) -> None:
        self._stream = stream
        self.left = max(0, int(length))

    def _take(self, size, reader):
        if size is None or size < 0 or size > self.left:
            size = self.left
        if size <= 0:
            return b""
        try:
            data = reader(size)
        except ssl.SSLError as exc:
            raise ConnectionAbortedError(str(exc)) from exc
        if not data:
            return b""  # peer closed mid-body; ``left`` stays above zero
        self.left -= len(data)
        return data

    def read(self, size=-1):
        return self._take(size, self._stream.read)

    def read1(self, size=-1):
        return self._take(size, getattr(self._stream, "read1", self._stream.read))

    def readline(self, size=-1):
        return self._take(size, self._stream.readline)

    def readinto(self, buffer) -> int:
        data = self.read(len(buffer))
        buffer[: len(data)] = data
        return len(data)

    def readlines(self, hint=-1):
        return list(self)

    def __iter__(self):
        while True:
            line = self.readline()
            if not line:
                return
            yield line

    def readable(self) -> bool:
        return True

    def close(self) -> None:
        pass


class _ResponseWriter:
    """Counts body bytes so a truncated response is never followed by another."""

    def __init__(self, stream) -> None:
        self._stream = stream
        self.in_body = False
        self.body_bytes = 0
        self.last = b""

    def write(self, data):
        try:
            written = self._stream.write(data)
        except ssl.SSLError as exc:
            # A TLS peer that left mid-response (closed tab, ended stream) is a
            # dropped connection, not a request error worth a traceback.
            raise ConnectionAbortedError(str(exc)) from exc
        if self.in_body and data:
            self.body_bytes += len(data)
            self.last = bytes(data[-len(_CHUNK_END):]) if len(data) >= len(_CHUNK_END) else bytes(data)
        return written

    def flush(self) -> None:
        self._stream.flush()

    def __getattr__(self, name):
        return getattr(self._stream, name)


class KeepAliveRequestHandler(WSGIRequestHandler):
    protocol_version = "HTTP/1.1"
    idle_timeout = IDLE_TIMEOUT_SEC
    # Headers and body go out as separate small writes. On a long-lived
    # connection Nagle holds the second one until the client's delayed ACK
    # (about 40 ms per response); a fresh connection never showed that.
    disable_nagle_algorithm = True

    _served = 0
    _in_wsgi = False
    _reuse = False
    _body = None
    _writer = None
    _status = 0
    _declared = None
    _chunked = False
    _close_asked = 0

    def handle_one_request(self) -> None:
        if self._served:
            # Waiting for a follow-up request. An idle timeout, a reset or a
            # clean close all mean the client is done with this connection.
            try:
                self.connection.settimeout(self.idle_timeout)
                waiting = self.rfile.peek(1)
                self.connection.settimeout(self.timeout)
            except (OSError, ValueError, AttributeError):
                waiting = b""
            if not waiting:
                self.close_connection = True
                return
        self._served += 1
        super().handle_one_request()

    def run_wsgi(self) -> None:
        stream, out = self.rfile, self.wfile
        self._reuse, self._status, self._declared, self._chunked = False, 0, None, False
        self._close_asked = 0
        self._body = self._request_body(stream)
        self._writer = _ResponseWriter(out)
        if self._body is not None:
            self.rfile = self._body
        self.wfile = self._writer
        self._in_wsgi = True
        try:
            super().run_wsgi()
        finally:
            self._in_wsgi = False
            self.rfile, self.wfile = stream, out
            if not (self._reuse and self._response_complete()):
                self.close_connection = True

    def _request_body(self, stream):
        """Bounded body reader, or ``None`` when the body has no known length."""
        headers = self.headers
        if "chunked" in (headers.get("Transfer-Encoding") or "").lower():
            return None
        raw = headers.get("Content-Length")
        if raw is None:
            return _RequestBody(stream, 0)
        try:
            length = int(raw)
        except ValueError:
            return None
        return _RequestBody(stream, length) if length >= 0 else None

    def _may_reuse(self) -> bool:
        if not self._in_wsgi or self.close_connection or self.request_version != "HTTP/1.1":
            return False
        if self.headers.get("Upgrade") or "upgrade" in (self.headers.get("Connection") or "").lower():
            return False
        return self._body is not None and self._body.left == 0

    def _response_complete(self) -> bool:
        writer = self._writer
        if writer is None or not writer.in_body:
            return False
        if self._chunked:
            return writer.last == _CHUNK_END
        if self.command == "HEAD" or self._status in _BODYLESS_STATUS or 100 <= self._status < 200:
            return writer.body_bytes == 0
        return self._declared is not None and writer.body_bytes == self._declared

    def send_response(self, code, message=None) -> None:
        self._status = int(code)
        super().send_response(code, message)

    def send_header(self, keyword, value) -> None:
        name, text = keyword.lower(), str(value)
        if name == "connection" and text.lower() == "close" and self._in_wsgi:
            # Decided in end_headers(): Werkzeug always sends one; a second
            # one came from the application and is honoured.
            self._close_asked += 1
            return
        if name == "content-length":
            try:
                self._declared = int(text)
            except ValueError:
                self._declared = None
        elif name == "transfer-encoding" and "chunked" in text.lower():
            self._chunked = True
        super().send_header(keyword, value)

    def end_headers(self) -> None:
        if self._in_wsgi and self._close_asked:
            if self._close_asked == 1 and self._may_reuse():
                # HTTP/1.1 connections persist unless a side says otherwise.
                self._reuse = True
            else:
                super().send_header("Connection", "close")
            self._close_asked = 0
        super().end_headers()
        if self._writer is not None and not 100 <= self._status < 200:
            self._writer.in_body = True


def request_handler(environ=None):
    """The handler class for a listener, or ``None`` when reuse is switched off
    (``CUTTLE_HTTP_KEEPALIVE=0``) so the server falls back to Werkzeug's own."""
    env = os.environ if environ is None else environ
    if str(env.get("CUTTLE_HTTP_KEEPALIVE", "1")).strip().lower() in ("0", "false", "off", "no"):
        return None
    return KeepAliveRequestHandler

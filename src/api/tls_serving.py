"""Server-side TLS for the threaded listeners: handshake off the accept loop.

``ssl.SSLSocket.accept()`` on a wrapped listening socket performs the TLS
handshake inside ``accept()``, on the one thread that accepts connections. A
client that opens the port and then stalls (a phone on weak Wi-Fi, a
speculative browser connection, a port probe) therefore blocks every other
client of that listener until it finishes or goes away.

:func:`server_ssl_context` returns a context whose listening socket accepts
without handshaking. The handshake runs when the connection's own handler
thread opens its streams, bounded by ``handshake_timeout``. A failed or timed
out handshake ends that one connection quietly, like a refused ``accept()``.

Stdlib only; no Cuttle imports. Works with anything that takes an
``ssl.SSLContext`` and serves each connection on its own thread
(``socketserver.ThreadingMixIn``, Werkzeug's threaded server).
"""

from __future__ import annotations

import io
import ssl

HANDSHAKE_TIMEOUT_SEC = 15.0


class _DeferredHandshakeSocket(ssl.SSLSocket):
    """Accepted connection that handshakes on first ``makefile()``."""

    def _finish_handshake(self) -> bool:
        state = getattr(self, "_deferred_handshake", None)
        if state is not None:
            return state
        ok = False
        try:
            previous = self.gettimeout()
            self.settimeout(getattr(self.context, "handshake_timeout", HANDSHAKE_TIMEOUT_SEC))
            try:
                self.do_handshake()
            finally:
                self.settimeout(previous)
            ok = True
        except (OSError, ValueError):
            # Includes ssl.SSLError and the handshake timeout. The peer never
            # became a TLS client; drop it without a traceback.
            try:
                self.close()
            except OSError:
                pass
        self._deferred_handshake = ok
        return ok

    def makefile(self, mode="r", buffering=None, **kwargs):
        if not self.server_side or self._finish_handshake():
            return super().makefile(mode, buffering, **kwargs)
        # Handshake failed: hand the request handler empty streams so it reads
        # end-of-file and returns, instead of raising into the server's
        # error log for every scanner or cert-rejecting client.
        return io.BytesIO()


class _DeferredHandshakeContext(ssl.SSLContext):
    sslsocket_class = _DeferredHandshakeSocket
    handshake_timeout = HANDSHAKE_TIMEOUT_SEC

    def wrap_socket(self, sock, server_side=False, do_handshake_on_connect=True, **kwargs):
        # The listening socket passes this flag on to every accepted socket.
        return super().wrap_socket(
            sock, server_side=server_side,
            do_handshake_on_connect=False if server_side else do_handshake_on_connect,
            **kwargs,
        )


def server_ssl_context(certfile, keyfile, *, handshake_timeout: float = HANDSHAKE_TIMEOUT_SEC) -> ssl.SSLContext:
    """TLS server context that never handshakes inside ``accept()``."""
    context = _DeferredHandshakeContext(ssl.PROTOCOL_TLS_SERVER)
    context.handshake_timeout = float(handshake_timeout)
    context.load_cert_chain(str(certfile), str(keyfile))
    return context

"""Env-only startup port configuration owner (F09).

Single owner for the three listener ports. Everything that binds, probes,
advertises, or reports a listener port resolves through here:

- ``https`` — primary Flask HTTPS listener (default 8080)
- ``http`` — companion plain-HTTP listener, same Flask app (default 8000)
- ``phone_https`` — optional LAN phone HTTPS listener (default 8888)

Configuration is process env over the checkout's ``src/.env``::

    CUTTLE_HTTPS_PORT=8443
    CUTTLE_HTTP_PORT=8001
    CUTTLE_PHONE_HTTPS_PORT=8890

Set them in ``src/.env`` or the process environment (env wins). An empty
value means unset (the file, then the default, applies). A malformed
*effective* value raises :exc:`PortConfigError` — callers must fail closed,
never silently fall back to the defaults, because the defaults may still be
live on another instance. (A file value overridden by a valid env value for
the same key is ignored, not rejected.) Standalone CLIs that never load
``src/.env`` resolve through :func:`resolve_with_env_file`, which reads ONLY
these three keys (never mutates the environment, never exposes other entries).

Restart policy: changing these ports requires a **daemon cold restart**
(tray Exit, then start again). A Flask-only restart cannot help: the daemon
resolves the primary port for its health/conflict checks and spawns Flask as
a child, so both sides must boot from the same environment. The daemon
re-reads ``src/.env`` before resolving; children inherit the same triple
through the spawned environment.
"""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Mapping, NamedTuple, Optional, Union

from dotenv import dotenv_values

DEFAULT_HTTPS_PORT = 8080
DEFAULT_HTTP_PORT = 8000
DEFAULT_PHONE_HTTPS_PORT = 8888

ENV_HTTPS_PORT = "CUTTLE_HTTPS_PORT"
ENV_HTTP_PORT = "CUTTLE_HTTP_PORT"
ENV_PHONE_HTTPS_PORT = "CUTTLE_PHONE_HTTPS_PORT"

#: Ports that are always treated as live-instance ports (shadow denylist
#: keeps these even when custom ports are configured).
DEFAULT_PORTS = frozenset({DEFAULT_HTTPS_PORT, DEFAULT_HTTP_PORT, DEFAULT_PHONE_HTTPS_PORT})

_MIN_PORT = 1
_MAX_PORT = 65535


class PortConfigError(ValueError):
    """Malformed or conflicting port configuration. Callers fail closed."""


class ServerPorts(NamedTuple):
    https: int
    http: int
    phone_https: int


def _parse_port(raw: object, *, name: str, default: int) -> int:
    """Parse one env value. ``None``/empty means unset (default applies)."""
    if raw is None:
        return default
    if isinstance(raw, bool):
        raise PortConfigError(f"{name} must be a port 1-65535, got {raw!r}")
    text = str(raw).strip()
    if not text:
        return default
    if not text.isascii() or not text.isdigit():
        raise PortConfigError(f"{name} must be a port 1-65535, got {raw!r}")
    port = int(text, 10)
    if not _MIN_PORT <= port <= _MAX_PORT:
        raise PortConfigError(f"{name} must be a port 1-65535, got {raw!r}")
    return port


def resolve_server_ports(env: Optional[Mapping[str, str]] = None) -> ServerPorts:
    """Resolve the listener triple: env > defaults. Raises on bad config."""
    source = os.environ if env is None else env
    ports = ServerPorts(
        https=_parse_port(source.get(ENV_HTTPS_PORT), name=ENV_HTTPS_PORT, default=DEFAULT_HTTPS_PORT),
        http=_parse_port(source.get(ENV_HTTP_PORT), name=ENV_HTTP_PORT, default=DEFAULT_HTTP_PORT),
        phone_https=_parse_port(
            source.get(ENV_PHONE_HTTPS_PORT), name=ENV_PHONE_HTTPS_PORT, default=DEFAULT_PHONE_HTTPS_PORT
        ),
    )
    seen = {}
    for label, port in (("https", ports.https), ("http", ports.http), ("phone_https", ports.phone_https)):
        if port in seen:
            raise PortConfigError(
                f"duplicate listener port {port} for {seen[port]} and {label}; "
                "CUTTLE_HTTPS_PORT/CUTTLE_HTTP_PORT/CUTTLE_PHONE_HTTPS_PORT must be distinct"
            )
        seen[port] = label
    return ports


def reserved_live_ports(env: Optional[Mapping[str, str]] = None) -> frozenset:
    """Shadow-denylist union: defaults (still possibly live) + configured.

    Raises :exc:`PortConfigError` on malformed config instead of falling
    back, so a bad env can never look like a free-port verdict.
    """
    ports = resolve_server_ports(env)
    return frozenset(DEFAULT_PORTS | {ports.https, ports.http, ports.phone_https})


_PORT_KEYS = (ENV_HTTPS_PORT, ENV_HTTP_PORT, ENV_PHONE_HTTPS_PORT)


def default_env_file() -> Path:
    """The checkout's ``src/.env`` (package-relative, cwd-independent)."""
    return Path(__file__).resolve().parents[1] / ".env"


def read_ports_file(path: Union[str, Path]) -> dict:
    """Read ONLY the three port keys from a dotenv file. No side effects.

    Parsing is ``python-dotenv`` semantics (the same parser the daemon uses
    to load ``src/.env``): quotes, inline comments, ``export`` prefixes, and
    interpolation all behave identically — only the returned keys are
    filtered. Never mutates ``os.environ`` and never returns any other entry
    (no secret dumping). A missing file means no file values ({}). An
    existing-but-unreadable file (permissions, directory) or invalid UTF-8
    raises :exc:`PortConfigError` naming only the path, never the contents.
    """
    candidate = Path(path)
    if not candidate.exists():
        return {}
    try:
        text = candidate.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise PortConfigError(
            f"cannot read listener ports from {candidate} ({type(exc).__name__})"
        ) from exc
    try:
        values = dotenv_values(stream=io.StringIO(text)) or {}
    except (OSError, UnicodeError, ValueError) as exc:
        raise PortConfigError(
            f"cannot parse listener ports from {candidate} ({type(exc).__name__})"
        ) from exc
    return {key: val for key, val in values.items() if key in _PORT_KEYS and val is not None}


def resolve_with_env_file(
    env: Optional[Mapping[str, str]] = None,
    env_file: Union[str, Path, None] = None,
) -> ServerPorts:
    """Resolve with explicit seams: process env > dotenv file > defaults.

    ``env`` defaults to ``os.environ``; ``env_file`` defaults to the
    checkout's ``src/.env``. An empty env value counts as unset, so the file
    (then the default) applies. A malformed *effective* value raises
    :exc:`PortConfigError` — but a file value overridden by a valid env value
    for the same key is simply ignored, not rejected. Pure
    ``resolve_server_ports(env=...)`` stays available for callers that
    must never touch the filesystem.
    """
    source = os.environ if env is None else env
    merged = read_ports_file(default_env_file() if env_file is None else env_file)
    for key in _PORT_KEYS:
        try:
            raw = source.get(key)
        except AttributeError:
            raw = None
        if raw is None:
            continue
        if isinstance(raw, str) and not raw.strip():
            continue  # empty means unset; file/default applies
        merged[key] = raw
    return resolve_server_ports(merged)

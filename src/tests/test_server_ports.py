"""Env-only listener-port owner (F09): validation, consumer wiring, shadow safety.

Hermetic by construction:

- The checkout's real ``src/.env`` is never read: the fixture repoints the
  file seam at a nonexistent temp path, and every file test injects an
  explicit temp ``.env``.
- The daemon is loaded with a stubbed ``dotenv`` loader, so importing it
  cannot merge the real ``src/.env`` into the test process.
- No sockets are bound (fake socket class), no daemon is started, no server
  is spawned, and no subprocess imports the monolith: listener args are
  proven in-process through ``start_listener_servers`` with fake
  app/factory/mDNS/runner objects.
"""

from __future__ import annotations

import importlib.util
import socket
import sys
from pathlib import Path
from types import ModuleType

import pytest

from api.server_ports import (
    DEFAULT_HTTP_PORT,
    DEFAULT_HTTPS_PORT,
    DEFAULT_PHONE_HTTPS_PORT,
    PortConfigError,
    ServerPorts,
    default_env_file,
    read_ports_file,
    reserved_live_ports,
    resolve_server_ports,
    resolve_with_env_file,
)

PORT_VARS = ("CUTTLE_HTTPS_PORT", "CUTTLE_HTTP_PORT", "CUTTLE_PHONE_HTTPS_PORT")
CUSTOM = {
    "CUTTLE_HTTPS_PORT": "8443",
    "CUTTLE_HTTP_PORT": "8001",
    "CUTTLE_PHONE_HTTPS_PORT": "8890",
}

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def clean_port_env(monkeypatch, tmp_path):
    for var in PORT_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("CUTTLE_INTERNAL_API_BASE", raising=False)
    monkeypatch.delenv("CUTTLE_API_URL", raising=False)
    monkeypatch.delenv("OAUTH_REDIRECT_BASE", raising=False)
    monkeypatch.delenv("CUTTLE_DEVICE_WORKERS_COORDINATOR_URL", raising=False)
    monkeypatch.delenv("CUTTLE_FLASK_URL", raising=False)
    monkeypatch.delenv("CUTTLE_SHADOW_RESERVED_PORTS", raising=False)
    # Hermetic file seam: the real src/.env is never consulted unless a
    # test injects an explicit env_file.
    monkeypatch.setattr(
        "api.server_ports.default_env_file", lambda: tmp_path / "no-such.env"
    )


def _set_custom(monkeypatch):
    for var, val in CUSTOM.items():
        monkeypatch.setenv(var, val)


def _write_env(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


# --- owner: pure resolution (never touches the filesystem) ---------------------


def test_defaults_when_env_empty():
    assert resolve_server_ports() == (DEFAULT_HTTPS_PORT, DEFAULT_HTTP_PORT, DEFAULT_PHONE_HTTPS_PORT)
    assert (DEFAULT_HTTPS_PORT, DEFAULT_HTTP_PORT, DEFAULT_PHONE_HTTPS_PORT) == (8080, 8000, 8888)


def test_empty_string_means_unset(monkeypatch):
    for var in PORT_VARS:
        monkeypatch.setenv(var, "")
    assert resolve_server_ports() == (8080, 8000, 8888)


def test_whitespace_padded_accepted(monkeypatch):
    _set_custom(monkeypatch)
    monkeypatch.setenv("CUTTLE_HTTPS_PORT", "  8443\t")
    assert resolve_server_ports().https == 8443


def test_partial_override(monkeypatch):
    monkeypatch.setenv("CUTTLE_HTTPS_PORT", "8443")
    ports = resolve_server_ports()
    assert (ports.https, ports.http, ports.phone_https) == (8443, 8000, 8888)


def test_full_custom_triple(monkeypatch):
    _set_custom(monkeypatch)
    assert resolve_server_ports() == (8443, 8001, 8890)


@pytest.mark.parametrize("var", PORT_VARS)
@pytest.mark.parametrize(
    "bad",
    ["abc", "80.0", "0x50", "-1", "0", "65536", "99999", "true", "False", "yes", "84 43", "+8080", "8,080", "1_000"],
)
def test_malformed_values_rejected(monkeypatch, var, bad):
    monkeypatch.setenv(var, bad)
    with pytest.raises(PortConfigError):
        resolve_server_ports()


def test_bool_object_rejected():
    with pytest.raises(PortConfigError):
        resolve_server_ports({"CUTTLE_HTTPS_PORT": True})


def test_duplicate_with_default_rejected(monkeypatch):
    # 8000 is the default companion-HTTP port: pointing HTTPS at it collides.
    monkeypatch.setenv("CUTTLE_HTTPS_PORT", "8000")
    with pytest.raises(PortConfigError, match="duplicate"):
        resolve_server_ports()


def test_duplicate_custom_rejected(monkeypatch):
    monkeypatch.setenv("CUTTLE_HTTPS_PORT", "9000")
    monkeypatch.setenv("CUTTLE_HTTP_PORT", "9000")
    with pytest.raises(PortConfigError, match="duplicate"):
        resolve_server_ports()


def test_reserved_ports_default_is_triple():
    assert reserved_live_ports() == frozenset({8080, 8000, 8888})


def test_reserved_ports_keep_defaults_and_add_configured(monkeypatch):
    _set_custom(monkeypatch)
    assert reserved_live_ports() == frozenset({8080, 8000, 8888, 8443, 8001, 8890})


def test_reserved_ports_fail_closed_on_malformed(monkeypatch):
    monkeypatch.setenv("CUTTLE_HTTPS_PORT", "bogus")
    with pytest.raises(PortConfigError):
        reserved_live_ports()


# --- owner: ports-only file seam -------------------------------------------------


def test_default_env_file_is_checkout_src_env():
    # The seam is package-relative (cwd-independent); verified from the
    # module file itself so the fixture's hermetic override stays in place.
    import api.server_ports as sp

    assert Path(sp.__file__).resolve().parents[1] / ".env" == REPO / "src" / ".env"


def test_read_ports_file_returns_only_port_keys(tmp_path):
    env = _write_env(
        tmp_path / "t.env",
        "# comment\n"
        "CUTTLE_HTTPS_PORT=8443 # primary HTTPS\n"
        "DISCORD_TOKEN=secret-should-never-appear\n"
        "GOOGLE_CLIENT_SECRET=also-secret\n"
        'CUTTLE_HTTP_PORT="8001" # HTTP\n'
        "CUTTLE_PHONE_HTTPS_PORT='8890'\n"
        "EMPTY=\n",
    )
    assert read_ports_file(env) == {
        "CUTTLE_HTTPS_PORT": "8443",
        "CUTTLE_HTTP_PORT": "8001",
        "CUTTLE_PHONE_HTTPS_PORT": "8890",
    }


def test_reader_matches_daemon_dotenv_semantics(tmp_path):
    """Regression: inline comments/quotes parse exactly like the daemon's load."""
    from dotenv import dotenv_values

    env = _write_env(
        tmp_path / "t.env",
        "CUTTLE_HTTPS_PORT=8443 # primary HTTPS\n"
        'CUTTLE_HTTP_PORT="8001" # HTTP\n',
    )
    assert read_ports_file(env) == {
        k: v for k, v in dict(dotenv_values(env)).items() if k.startswith("CUTTLE_") and k.endswith("PORT")
    }
    assert resolve_with_env_file(env={}, env_file=env) == (8443, 8001, 8888)


def test_read_ports_file_missing_is_empty_and_unreadable_fails_closed(tmp_path):
    assert read_ports_file(tmp_path / "absent.env") == {}
    bad = tmp_path / "bad.env"
    bad.write_bytes(b"CUTTLE_HTTPS_PORT=8443\nDISCORD_TOKEN=s3cr3t-should-never-leak\n\xff\xfe\n")
    with pytest.raises(PortConfigError) as excinfo:
        read_ports_file(bad)
    assert "s3cr3t-should-never-leak" not in str(excinfo.value)
    with pytest.raises(PortConfigError):
        read_ports_file(tmp_path)  # a directory is not a readable file
    with pytest.raises(PortConfigError):
        resolve_with_env_file(env={}, env_file=bad)


def test_read_ports_file_last_wins_and_export(tmp_path):
    env = _write_env(
        tmp_path / "t.env",
        "CUTTLE_HTTPS_PORT=1111\n"
        "export CUTTLE_HTTPS_PORT=8443\n",
    )
    assert read_ports_file(env) == {"CUTTLE_HTTPS_PORT": "8443"}


def test_resolve_with_env_file_prefers_env(tmp_path, monkeypatch):
    env = _write_env(tmp_path / "t.env", "CUTTLE_HTTPS_PORT=9443\nCUTTLE_HTTP_PORT=9001\n")
    monkeypatch.setenv("CUTTLE_HTTPS_PORT", "8443")
    assert resolve_with_env_file(env_file=env) == (8443, 9001, 8888)


def test_resolve_with_env_file_empty_env_falls_to_file(tmp_path, monkeypatch):
    env = _write_env(tmp_path / "t.env", "CUTTLE_HTTPS_PORT=9443\n")
    monkeypatch.setenv("CUTTLE_HTTPS_PORT", "")
    assert resolve_with_env_file(env_file=env).https == 9443


def test_resolve_with_env_file_malformed_file_fails_closed(tmp_path):
    env = _write_env(tmp_path / "t.env", "CUTTLE_HTTPS_PORT=bogus\n")
    with pytest.raises(PortConfigError):
        resolve_with_env_file(env={}, env_file=env)


def test_resolve_with_env_file_malformed_env_beats_valid_file(tmp_path, monkeypatch):
    env = _write_env(tmp_path / "t.env", "CUTTLE_HTTPS_PORT=9443\n")
    monkeypatch.setenv("CUTTLE_HTTPS_PORT", "bogus")
    with pytest.raises(PortConfigError):
        resolve_with_env_file(env_file=env)


def test_valid_env_overrides_malformed_file_without_reject(tmp_path, monkeypatch):
    """Precedence is honest: an env-won key never rejects on the file value."""
    env = _write_env(tmp_path / "t.env", "CUTTLE_HTTPS_PORT=bogus\n")
    monkeypatch.setenv("CUTTLE_HTTPS_PORT", "8443")
    assert resolve_with_env_file(env_file=env).https == 8443


# --- consumers (env over injected temp file; real src/.env never read) ---------


def test_lan_access_getters_follow_env(monkeypatch):
    from api import lan_access

    assert lan_access.get_primary_https_port() == 8080
    assert lan_access.get_http_fallback_port() == 8000
    assert lan_access.get_phone_https_port() == 8888
    _set_custom(monkeypatch)
    assert lan_access.get_primary_https_port() == 8443
    assert lan_access.get_http_fallback_port() == 8001
    assert lan_access.get_phone_https_port() == 8890


def test_lan_access_getters_follow_env_file(tmp_path):
    from api import lan_access
    from api import server_ports

    env = _write_env(tmp_path / "t.env", "CUTTLE_HTTPS_PORT=9443\n")
    real_default = server_ports.default_env_file
    server_ports.default_env_file = lambda: env
    try:
        assert lan_access.get_primary_https_port() == 9443
    finally:
        server_ports.default_env_file = real_default


def test_cors_origins_follow_env(monkeypatch):
    from api import lan_access

    monkeypatch.setattr(lan_access, "is_lan_access_enabled", lambda: False)
    assert lan_access.build_cors_origins() == [
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "https://localhost:8080",
        "https://127.0.0.1:8080",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "app://cuttle",
        "https://localhost",
        "http://localhost",
        "capacitor://localhost",
        "ionic://localhost",
    ]
    _set_custom(monkeypatch)
    origins = lan_access.build_cors_origins()
    assert "https://127.0.0.1:8443" in origins
    assert "http://127.0.0.1:8001" in origins
    assert not [o for o in origins if o.endswith(":8080") or o.endswith(":8000")]


def test_cors_lan_entries_follow_env(monkeypatch):
    from api import lan_access

    _set_custom(monkeypatch)
    monkeypatch.setattr(lan_access, "is_lan_access_enabled", lambda: True)
    monkeypatch.setattr(lan_access, "get_lan_ipv4", lambda: "192.168.1.5")
    origins = lan_access.build_cors_origins()
    assert "https://192.168.1.5:8443" in origins
    assert "https://192.168.1.5:8890" in origins
    assert "http://192.168.1.5:8001" in origins


def test_internal_http_base_follows_env(monkeypatch):
    from api import internal_http

    assert internal_http._internal_api_base() == "https://127.0.0.1:8080"
    _set_custom(monkeypatch)
    assert internal_http._internal_api_base() == "https://127.0.0.1:8443"
    monkeypatch.setenv("CUTTLE_INTERNAL_API_BASE", "https://10.0.0.2:9443/")
    assert internal_http._internal_api_base() == "https://10.0.0.2:9443"


def test_mdns_default_port_follows_env(monkeypatch):
    from api import discovery_mdns

    assert discovery_mdns.default_mdns_port() == 8080
    _set_custom(monkeypatch)
    assert discovery_mdns.default_mdns_port() == 8443


def test_shadow_validate_port_refuses_defaults_and_configured(monkeypatch):
    from api.dev_instance import ShadowError, reserved_ports, validate_port

    for port in (8080, 8000, 8888):
        with pytest.raises(ShadowError):
            validate_port(port)
    assert validate_port(0) == 0
    assert validate_port(18080) == 18080
    _set_custom(monkeypatch)
    assert reserved_ports() == frozenset({8080, 8000, 8888, 8443, 8001, 8890})
    for port in (8443, 8001, 8890):
        with pytest.raises(ShadowError):
            validate_port(port)
    assert validate_port(18080) == 18080


def test_shadow_validate_port_fails_closed_on_malformed(monkeypatch):
    from api.dev_instance import ShadowError, validate_port

    monkeypatch.setenv("CUTTLE_HTTPS_PORT", "bogus")
    with pytest.raises(ShadowError):
        validate_port(18080)


def test_shadow_port_zero_resolves_config_first(monkeypatch):
    """Even ephemeral 0 refuses to boot beside a malformed listener config."""
    from api.dev_instance import ShadowError, validate_port

    assert validate_port(0) == 0
    monkeypatch.setenv("CUTTLE_HTTPS_PORT", "bogus")
    with pytest.raises(ShadowError):
        validate_port(0)


def _point_default_file_at(monkeypatch, path: Path):
    monkeypatch.setattr("api.server_ports.default_env_file", lambda: path)


def test_panes_cli_file_only_without_exported_vars(monkeypatch, tmp_path):
    """Standalone panes_cli honors a temp .env with no exported vars."""
    from api.panes_cli import cli

    env = _write_env(
        tmp_path / "t.env",
        "CUTTLE_HTTPS_PORT=8443\nCUTTLE_HTTP_PORT=8001\nCUTTLE_PHONE_HTTPS_PORT=8890\n",
    )
    _point_default_file_at(monkeypatch, env)
    assert cli._default_base() == "https://127.0.0.1:8443"


def test_shadow_file_only_reserves_configured_and_defaults(monkeypatch, tmp_path):
    """Standalone dev_instance honors a temp .env with no exported vars."""
    from api.dev_instance import ShadowError, reserved_ports, validate_port

    env = _write_env(
        tmp_path / "t.env",
        "CUTTLE_HTTPS_PORT=8443\nCUTTLE_HTTP_PORT=8001\nCUTTLE_PHONE_HTTPS_PORT=8890\n",
    )
    _point_default_file_at(monkeypatch, env)
    assert reserved_ports() == frozenset({8080, 8000, 8888, 8443, 8001, 8890})
    for port in (8080, 8000, 8888, 8443, 8001, 8890):
        with pytest.raises(ShadowError):
            validate_port(port)
    assert validate_port(0) == 0
    assert validate_port(18080) == 18080


def test_shadow_child_env_carries_denylist(monkeypatch, tmp_path):
    from api.dev_instance import build_child_env

    seed = {
        "data_dir": str(tmp_path / "data"),
        "app_dir": str(tmp_path / "app"),
        "manifest": str(tmp_path / "m.json"),
        "codehash": "abc",
        "fixture_project": str(tmp_path / "proj"),
        "anchor_hashes": {},
        "log": str(tmp_path / "l.log"),
    }
    env = build_child_env(seed, "blocked", 0)
    assert env["CUTTLE_SHADOW_RESERVED_PORTS"] == "8000,8080,8888"
    _set_custom(monkeypatch)
    env = build_child_env(seed, "blocked", 0)
    assert env["CUTTLE_SHADOW_RESERVED_PORTS"] == "8000,8001,8080,8443,8888,8890"


def _load_script_module(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_shadow_child_denylist_union():
    child = _load_script_module("cuttle_shadow_app_under_test", "src/scripts/cuttle_shadow_app.py")
    assert child._reserved_ports() == frozenset({8080, 8000, 8888})


def test_shadow_child_denylist_reads_parent_env(monkeypatch):
    child = _load_script_module("cuttle_shadow_app_under_test", "src/scripts/cuttle_shadow_app.py")
    monkeypatch.setenv("CUTTLE_SHADOW_RESERVED_PORTS", "8443, 8001,8890")
    assert child._reserved_ports() == frozenset({8080, 8000, 8888, 8443, 8001, 8890})


@pytest.mark.parametrize("bad", ["8443, bogus", "0", "99999"])
def test_shadow_child_denylist_malformed_refuses(monkeypatch, bad):
    child = _load_script_module("cuttle_shadow_app_under_test", "src/scripts/cuttle_shadow_app.py")
    monkeypatch.setenv("CUTTLE_SHADOW_RESERVED_PORTS", bad)
    assert child._reserved_ports() is None


def _load_daemon_isolated(monkeypatch):
    """Execute the daemon with a stubbed dotenv loader: no real .env merge."""
    stub = ModuleType("dotenv")
    stub.load_dotenv = lambda *args, **kwargs: False
    monkeypatch.setitem(sys.modules, "dotenv", stub)
    return _load_script_module("cuttle_daemon_iso_under_test", "src/scripts/cuttle_daemon.py")


def test_daemon_snapshot_defaults_without_env(monkeypatch):
    daemon = _load_daemon_isolated(monkeypatch)
    assert daemon.flask_port() == 8080
    assert daemon._flask_url("/api/status") == "https://127.0.0.1:8080/api/status"


def test_daemon_snapshot_custom_boot_env(monkeypatch):
    _set_custom(monkeypatch)
    daemon = _load_daemon_isolated(monkeypatch)
    assert daemon.flask_port() == 8443
    assert daemon._flask_url("/api/status") == "https://127.0.0.1:8443/api/status"


def test_daemon_import_refuses_malformed_boot_env(monkeypatch):
    # Eager snapshot: malformed boot config fails the daemon at import,
    # before any reload or spawn can run.
    monkeypatch.setenv("CUTTLE_HTTPS_PORT", "bogus")
    with pytest.raises(PortConfigError):
        _load_daemon_isolated(monkeypatch)


def test_daemon_snapshot_holds_across_env_change(monkeypatch):
    _set_custom(monkeypatch)
    daemon = _load_daemon_isolated(monkeypatch)
    assert daemon.flask_port() == 8443
    # A mid-life .env edit (simulated) cannot redirect probes or the child.
    monkeypatch.setenv("CUTTLE_HTTPS_PORT", "9443")
    monkeypatch.setenv("CUTTLE_HTTP_PORT", "9001")
    monkeypatch.setenv("CUTTLE_PHONE_HTTPS_PORT", "9002")
    assert daemon.flask_port() == 8443
    child_env = daemon._flask_child_env({})
    assert (child_env["CUTTLE_HTTPS_PORT"], child_env["CUTTLE_HTTP_PORT"]) == ("8443", "8001")
    assert child_env["CUTTLE_PHONE_HTTPS_PORT"] == "8890"
    assert daemon._flask_url("/api/status") == "https://127.0.0.1:8443/api/status"


def test_daemon_start_flask_pins_child_after_reload(monkeypatch, tmp_path):
    """Reload-then-pin order: the child keeps the boot triple, not the file's."""
    _set_custom(monkeypatch)
    daemon = _load_daemon_isolated(monkeypatch)
    assert "flask" not in daemon.processes
    assert daemon.flask_port() == 8443

    def fake_reload():
        # Simulate a mid-life src/.env edit picked up by the reload.
        monkeypatch.setenv("CUTTLE_HTTPS_PORT", "9443")
        monkeypatch.setenv("CUTTLE_HTTP_PORT", "9001")
        monkeypatch.setenv("CUTTLE_PHONE_HTTPS_PORT", "9002")

    monkeypatch.setattr(daemon, "_reload_env_file", fake_reload)
    monkeypatch.setattr(daemon, "_warn_primary_port_conflicts", lambda: None)
    monkeypatch.setattr(daemon, "LOGS_DIR", tmp_path)
    captured = {}

    class _Proc:
        def poll(self):
            return None

    def fake_popen(*args, **kwargs):
        captured["env"] = kwargs["env"]
        return _Proc()

    monkeypatch.setattr(daemon.subprocess, "Popen", fake_popen)
    assert daemon.start_flask() is True
    env = captured["env"]
    assert env["CUTTLE_HTTPS_PORT"] == "8443"
    assert env["CUTTLE_HTTP_PORT"] == "8001"
    assert env["CUTTLE_PHONE_HTTPS_PORT"] == "8890"
    # ...and the daemon's own probes stay with the boot triple too.
    assert daemon.flask_port() == 8443
    assert daemon._flask_url("/api/status") == "https://127.0.0.1:8443/api/status"


def test_daemon_reload_repins_ports_keeps_other_keys(monkeypatch, tmp_path):
    """Actual _reload_env_file against a temp file with a changed triple.

    Non-port keys reload; the triple is re-pinned to boot, so os.environ,
    in-process consumers, the Flask child env, and probes all agree.
    No live files, no network, no services.
    """
    _set_custom(monkeypatch)
    daemon = _load_daemon_isolated(monkeypatch)
    assert daemon.flask_port() == 8443
    monkeypatch.delenv("CUTTLE_RELOAD_PROBE", raising=False)

    changed = _write_env(
        tmp_path / ".env",
        "CUTTLE_HTTPS_PORT=9443\n"
        "CUTTLE_HTTP_PORT=9001\n"
        "CUTTLE_PHONE_HTTPS_PORT=9002\n"
        "CUTTLE_RELOAD_PROBE=hello\n",
    )
    monkeypatch.setattr(daemon, "_env_file", changed)
    # Let the reload routine use the real dotenv loader on the temp file.
    monkeypatch.delitem(sys.modules, "dotenv", raising=False)

    daemon._reload_env_file()

    import os as _os

    assert _os.environ["CUTTLE_HTTPS_PORT"] == "8443"
    assert _os.environ["CUTTLE_HTTP_PORT"] == "8001"
    assert _os.environ["CUTTLE_PHONE_HTTPS_PORT"] == "8890"
    assert _os.environ["CUTTLE_RELOAD_PROBE"] == "hello"
    from api import internal_http, lan_access

    assert internal_http._internal_api_base() == "https://127.0.0.1:8443"
    assert lan_access.get_phone_https_port() == 8890
    assert lan_access.get_primary_https_port() == 8443
    child_env = daemon._flask_child_env({})
    assert (child_env["CUTTLE_HTTPS_PORT"], child_env["CUTTLE_HTTP_PORT"]) == ("8443", "8001")
    assert daemon._flask_url("/api/status") == "https://127.0.0.1:8443/api/status"


class _FakeSocket:
    instances = []

    def __init__(self, *args, **kwargs):
        self.connected = None
        _FakeSocket.instances.append(self)

    def settimeout(self, timeout):
        pass

    def connect(self, addr):
        self.connected = addr
        if addr[1] == 9999:
            raise OSError("refused")

    def close(self):
        pass


def test_daemon_port_probe_uses_fake_socket_no_network(monkeypatch):
    _set_custom(monkeypatch)
    daemon = _load_daemon_isolated(monkeypatch)
    monkeypatch.setattr(socket, "socket", _FakeSocket)
    _FakeSocket.instances.clear()
    assert daemon._flask_port_open() is True
    assert _FakeSocket.instances[-1].connected == ("127.0.0.1", 8443)
    monkeypatch.setenv("CUTTLE_HTTPS_PORT", "9999")
    # Snapshot holds 8443: the probe never follows the mid-life edit.
    assert daemon._flask_port_open() is True
    assert _FakeSocket.instances[-1].connected == ("127.0.0.1", 8443)


def test_panes_cli_default_base_follows_env(monkeypatch):
    from api.panes_cli import cli

    assert cli._default_base() == "https://127.0.0.1:8080"
    _set_custom(monkeypatch)
    assert cli._default_base() == "https://127.0.0.1:8443"
    monkeypatch.setenv("CUTTLE_HTTPS_PORT", "bogus")
    with pytest.raises(SystemExit):
        cli._default_base()


def test_terminal_and_oauth_urls_follow_env(monkeypatch):
    from api import web_terminal
    from api.auth_api import _oauth_public_base

    assert web_terminal._terminal_local_url() == "https://127.0.0.1:8080/terminal_page.html"
    _set_custom(monkeypatch)
    assert web_terminal._terminal_local_url() == "https://127.0.0.1:8443/terminal_page.html"
    assert _oauth_public_base() == "https://localhost:8443"
    monkeypatch.setenv("CUTTLE_API_URL", "https://10.0.0.2:9443")
    assert _oauth_public_base() == "https://10.0.0.2:9443"


# --- listener args proven in-process with fakes (no bind/network/spawn) --------


class _FakeServer:
    def __init__(self):
        self.served = False

    def serve_forever(self):
        self.served = True


class _FakeApp:
    def __init__(self):
        self.run_kwargs = None

    def run(self, **kwargs):
        self.run_kwargs = kwargs


def _seam_fakes():
    calls = {"servers": [], "mdns": [], "runner": [], "logs": []}
    app = _FakeApp()

    def factory(host, port, wsgi_app, **kwargs):
        calls["servers"].append({"host": host, "port": port, "kwargs": kwargs, "app": wsgi_app})
        return _FakeServer()

    def mdns(port, name="Cuttle"):
        calls["mdns"].append({"port": port, "name": name})

    def runner(*, host, port):
        calls["runner"].append({"host": host, "port": port})

    return app, calls, factory, mdns, runner


def _custom_ports():
    return ServerPorts(https=8443, http=8001, phone_https=8890)


def _patch_lan_for_seam(monkeypatch):
    from api import lan_access

    monkeypatch.setattr(lan_access, "is_lan_access_enabled", lambda: True)
    monkeypatch.setattr(lan_access, "get_lan_ipv4", lambda: "192.168.1.5")


def test_listener_seam_lan_branch_exact_args(monkeypatch):
    from api.web_chat_api import start_listener_servers

    _set_custom(monkeypatch)
    _patch_lan_for_seam(monkeypatch)
    app, calls, factory, mdns, runner = _seam_fakes()
    plan = start_listener_servers(
        app,
        _custom_ports(),
        lan_enabled=True,
        lan_ip="192.168.1.5",
        bind_host="0.0.0.0",
        mdns_enabled=True,
        cert_files=("cert.pem", "key.pem"),
        use_reloader=False,
        make_server_factory=factory,
        mdns_starter=mdns,
        primary_runner=runner,
        log=calls["logs"].append,
    )
    assert plan == {
        "primary_port": 8443,
        "phone_https_port": 8890,
        "http_port": 8001,
        "mdns_port": 8443,
    }
    assert calls["mdns"] == [{"port": 8443, "name": "Cuttle"}]
    assert [(s["host"], s["port"]) for s in calls["servers"]] == [("0.0.0.0", 8890), ("0.0.0.0", 8001)]
    phone, fallback = calls["servers"]
    assert phone["kwargs"] == {"threaded": True, "ssl_context": ("cert.pem", "key.pem")}
    assert fallback["kwargs"] == {"threaded": True}
    assert phone["app"] is app and fallback["app"] is app
    assert calls["runner"] == [{"host": "0.0.0.0", "port": 8443}]
    assert any("https://0.0.0.0:8890" in line for line in calls["logs"])
    assert any("http://0.0.0.0:8001" in line for line in calls["logs"])


def test_listener_seam_default_runner_app_run_args(monkeypatch):
    """The real app.run call shape, captured from a fake app: no Flask needed."""
    from api.web_chat_api import start_listener_servers

    _set_custom(monkeypatch)
    _patch_lan_for_seam(monkeypatch)
    app, calls, factory, mdns, _ = _seam_fakes()
    start_listener_servers(
        app,
        _custom_ports(),
        lan_enabled=True,
        lan_ip="192.168.1.5",
        bind_host="0.0.0.0",
        mdns_enabled=False,
        cert_files=("cert.pem", "key.pem"),
        use_reloader=False,
        make_server_factory=factory,
        mdns_starter=mdns,
        primary_runner=None,
        log=calls["logs"].append,
    )
    assert calls["mdns"] == []
    assert app.run_kwargs == {
        "debug": False,
        "host": "0.0.0.0",
        "port": 8443,
        "use_reloader": False,
        "threaded": True,
        "ssl_context": ("cert.pem", "key.pem"),
    }


def test_listener_seam_loopback_branch(monkeypatch):
    from api.web_chat_api import start_listener_servers

    app, calls, factory, mdns, runner = _seam_fakes()
    plan = start_listener_servers(
        app,
        _custom_ports(),
        lan_enabled=False,
        lan_ip=None,
        bind_host="127.0.0.1",
        mdns_enabled=True,
        cert_files=("cert.pem", "key.pem"),
        make_server_factory=factory,
        mdns_starter=mdns,
        primary_runner=runner,
        log=calls["logs"].append,
    )
    assert plan["mdns_port"] is None
    assert calls["mdns"] == []
    assert [(s["host"], s["port"]) for s in calls["servers"]] == [("127.0.0.1", 8001)]
    assert calls["runner"] == [{"host": "127.0.0.1", "port": 8443}]


def test_listener_seam_companion_failure_never_aborts(monkeypatch):
    from api.web_chat_api import start_listener_servers

    _set_custom(monkeypatch)
    _patch_lan_for_seam(monkeypatch)
    app, calls, _, mdns, runner = _seam_fakes()

    def boom(*args, **kwargs):
        raise OSError("bind denied")

    plan = start_listener_servers(
        app,
        _custom_ports(),
        lan_enabled=True,
        lan_ip="192.168.1.5",
        bind_host="0.0.0.0",
        mdns_enabled=False,
        cert_files=("cert.pem", "key.pem"),
        make_server_factory=boom,
        mdns_starter=mdns,
        primary_runner=runner,
        log=calls["logs"].append,
    )
    assert plan["primary_port"] == 8443
    assert calls["runner"] == [{"host": "0.0.0.0", "port": 8443}]
    assert any("Phone LAN servers failed" in line for line in calls["logs"])

"""Tests for the shadow-app runner: static prep plus one bounded real
dual-boot gate (test_s1_two_shadows_dynamic). No vendors, no live stores;
owned children always reaped. One bounded bare interpreter probe exercises
the audit hook without importing the app.
"""

from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "linux",
    reason="Linux shadow-isolation validation only; "
    "other platforms not verified",
)

from api.dev_instance import (
    GUARD_FLAGS,
    ShadowError,
    _is_safe_member,
    _member_ancestors_ok,
    _valid_shadow_id,
    build_child_env,
    fingerprint_files,
    iter_snapshot_files,
    prepare_snapshot,
    refresh_seed_hashes,
    resolve_port,
    validate_port,
)
from api import dev_instance as di

CANDIDATE = Path(__file__).resolve().parents[3]


def _git(*args, cwd):
    env = {
        "PATH": "/usr/bin:/bin",
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
        "GIT_CONFIG_GLOBAL": __import__("os").devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    proc = subprocess.run(
        ["git", *args], cwd=str(cwd), env=env,
        capture_output=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr.decode()[:300]
    return proc


@pytest.fixture
def tiny_repo(tmp_path):
    """Isolated git repo with one modified tracked file (never the checkout)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    (repo / "tracked.txt").write_text("committed\n", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "mod.py").write_text("v1\n", encoding="utf-8")
    _git("add", "-A", cwd=repo)
    _git("commit", "-qm", "init", cwd=repo)
    (repo / "src" / "mod.py").write_text("working\n", encoding="utf-8")
    (repo / "temp").mkdir()
    return repo


def test_prepare_snapshot_uses_working_bytes(tiny_repo):
    """Snapshot freezes worktree bytes; refresh helper recomputes hashes."""
    import shutil
    import uuid

    seed = None
    try:
        seed = prepare_snapshot(tiny_repo, f"tiny-{uuid.uuid4().hex[:8]}")
        assert (Path(seed["app_dir"]) / "src" / "mod.py").read_bytes() == b"working\n"
        assert seed["anchor_hashes"] == {}
        assert (Path(seed["data_dir"]) / "vendor" / "hermes").is_dir()
        old_hash = seed["codehash"]
        target = Path(seed["app_dir"]) / "src" / "mod.py"
        target.write_text("working+\n", encoding="utf-8")
        refreshed = refresh_seed_hashes(seed)
        assert refreshed["codehash"] != old_hash
        assert refreshed["files"] >= 2
    finally:
        if seed is not None:
            shutil.rmtree(seed["root"], ignore_errors=True)


def test_prepare_refuses_nonempty_root(tiny_repo):
    root = tiny_repo / "temp" / "shadows" / "occupied"
    root.mkdir(parents=True)
    (root / "stale.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ShadowError, match="non-empty"):
        prepare_snapshot(tiny_repo, "occupied")


def test_shadow_id_validation():
    assert _valid_shadow_id("abc-123_X") == "abc-123_X"
    for bad in ("", "../x", "/abs", "a/b", "a b", "x" * 65, ".hidden"):
        with pytest.raises(ShadowError):
            _valid_shadow_id(bad)


def test_snapshot_excludes_personal_and_fails_symlink(tiny_repo):
    (tiny_repo / ".env").write_text("K=v\n", encoding="utf-8")
    (tiny_repo / "s.db").write_text("x", encoding="utf-8")
    (tiny_repo / "sub").mkdir()
    (tiny_repo / "sub" / "personal").mkdir()
    (tiny_repo / "sub" / "personal" / "note.md").write_text("x", encoding="utf-8")
    (tiny_repo / "src" / "actual").mkdir()
    (tiny_repo / "src" / "actual" / "f.txt").write_text("x", encoding="utf-8")
    try:
        (tiny_repo / "src" / "link").symlink_to(tiny_repo / "src" / "actual")
    except OSError:
        pytest.skip("symlinks unavailable")
    _git("add", "-A", cwd=tiny_repo)
    _git("commit", "-qm", "more", cwd=tiny_repo)
    (tiny_repo / "src" / "link" / "evil.txt").write_text("x", encoding="utf-8")
    _git("add", "-A", cwd=tiny_repo)
    _git("commit", "-qm", "evil", cwd=tiny_repo)
    assert not _member_ancestors_ok(tiny_repo, "src/link/evil.txt")
    with pytest.raises(ShadowError, match="unsafe tracked paths"):
        iter_snapshot_files(tiny_repo)


def test_snapshot_excludes_secrets(tiny_repo):
    (tiny_repo / ".env").write_text("K=v\n", encoding="utf-8")
    (tiny_repo / "s.db").write_text("x", encoding="utf-8")
    (tiny_repo / "runtime_config.json").write_text('{"sentinel": true}\n', encoding="utf-8")
    (tiny_repo / "bot_config.json").write_text('{"sentinel": true}\n',
                                                encoding="utf-8")
    _git("add", "-A", cwd=tiny_repo)
    _git("commit", "-qm", "more", cwd=tiny_repo)
    rels = iter_snapshot_files(tiny_repo)
    assert ".env" not in rels
    assert "s.db" not in rels
    assert "bot_config.json" not in rels
    assert "runtime_config.json" not in rels
    assert "src/mod.py" in rels
    assert "tracked.txt" in rels


def test_safe_member_rejects_escapes(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    link = tmp_path / "link"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    assert not _is_safe_member(tmp_path, "link")
    assert not _is_safe_member(tmp_path, "../outside.txt")
    assert not _is_safe_member(tmp_path, "/etc/hostname")
    inner = tmp_path / "inner.txt"
    inner.write_text("x", encoding="utf-8")
    assert _is_safe_member(tmp_path, "inner.txt")


def test_child_env_allowlist(tmp_path, monkeypatch):
    """No os.environ.copy(): sentinel secrets never reach the child."""
    import json

    monkeypatch.setenv("OPENAI_API_KEY", "sentinel-live-key")
    monkeypatch.setenv("CUTTLE_ROUTER_DB", "sentinel-live-db")
    monkeypatch.setenv("MUSE_MODEL", "sentinel-live-model")
    monkeypatch.setenv("HERMES_HOME", "sentinel-live-hermes")
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path / "sentinel-live-home"))
    # HOME/CODEX_HOME are never touched (not even in tests); the original
    # environment stays intact and neither may reach the child.
    seed = {
        "app_dir": "/tmp/shadow/app", "data_dir": "/tmp/shadow/data",
        "manifest": "/tmp/shadow/manifest.json", "log": "/tmp/shadow/x.log",
        "codehash": "abc", "anchor_hashes": {"a": "b"},
        "fixture_project": "/tmp/shadow/data/fixture_project",
    }
    env = build_child_env(seed, "blocked", 0)
    assert "OPENAI_API_KEY" not in env
    assert "CUTTLE_ROUTER_DB" not in env
    assert "HOME" not in env
    for key, value in GUARD_FLAGS.items():
        assert env[key] == value
    assert env["CUTTLE_SHADOW_SCENARIO"] == "blocked"
    assert env["CUTTLE_SHADOW_PORT"] == "0"
    assert "CUTTLE_ACTION_HMAC_SECRET" in env
    assert "CUTTLE_SHADOW_HMAC_SECRET" not in env
    # Exact vendor-fixture values; inherited overrides never leak in.
    assert env["MUSE_MODEL"] == "muse-spark-1.3"
    assert env["HERMES_HOME"] == "/tmp/shadow/data/vendor/hermes"
    # Every Cuttle store resolves inside the private instance, never the live home.
    assert env["CUTTLE_HOME"] == str(Path("/tmp/shadow/data/home"))
    assert "HOME" not in env and "CODEX_HOME" not in env
    assert json.loads(env["CUTTLE_SHADOW_ANCHORS"]) == {"a": "b"}
    env2 = build_child_env(seed, "blocked", 0)
    assert env["CUTTLE_ACTION_HMAC_SECRET"] != env2["CUTTLE_ACTION_HMAC_SECRET"]
    assert env["CUTTLE_SHADOW_NONCE"] != env2["CUTTLE_SHADOW_NONCE"]


def test_port_precedence(monkeypatch):
    """Explicit CLI 0 beats env; env beats the default."""
    assert validate_port(0) == 0
    assert validate_port(18080) == 18080
    for bad in (8080, 8000, 8888, 80, -1, 99999, "abc"):
        with pytest.raises(ShadowError):
            validate_port(bad)
    assert resolve_port(0) == 0
    monkeypatch.setenv("CUTTLE_SHADOW_PORT", "18081")
    assert resolve_port(0) == 0  # CLI 0 wins over env
    assert resolve_port(None) == 18081
    monkeypatch.delenv("CUTTLE_SHADOW_PORT")
    assert resolve_port(None) == 0


def test_bootstrap_import_has_no_side_effects():
    """Compare sys.modules before/after: no app import on bootstrap import."""
    before = set(sys.modules)
    import scripts.cuttle_shadow_app as boot

    assert "api.web_chat_api" not in set(sys.modules) - before
    assert "flask" not in set(sys.modules) - before
    assert boot.__name__ == "scripts.cuttle_shadow_app"


def test_guards_install_and_restore():
    """Socket/subprocess guards deny; uninstall_all restores everything."""
    import os
    import socket as _socket
    import subprocess as _sp

    import scripts.cuttle_shadow_app as boot

    boot.uninstall_all()
    real_popen, real_call, real_system = _sp.Popen, _sp.call, os.system
    real_connect = _socket.socket.connect
    try:
        boot.install_subprocess_guard()
        boot.install_socket_guard()
        with pytest.raises(boot.ShadowBlocked):
            _sp.Popen(["echo", "hi"])
        with pytest.raises(boot.ShadowBlocked):
            _sp.call(["echo", "hi"])
        with pytest.raises(boot.ShadowBlocked):
            os.system("echo hi")
        with pytest.raises(boot.ShadowBlocked):
            # Guard raises before any real connect attempt is made.
            _socket.create_connection(("127.0.0.1", 9), timeout=1)
        with pytest.raises(boot.ShadowBlocked):
            _socket.getaddrinfo("example.com", 80)
        parent, child = _socket.socketpair()
        parent.close()
        child.close()
    finally:
        boot.uninstall_all()
    assert _sp.Popen is real_popen
    assert _sp.call is real_call
    assert os.system is real_system
    assert _socket.socket.connect is real_connect


def test_unix_bind_denied_no_socket_created(tmp_path):
    """AF_UNIX bind denied: no socket path file appears, counter noted."""
    import socket as _socket

    import scripts.cuttle_shadow_app as boot

    boot.uninstall_all()
    real_bind = _socket.socket.bind
    sock_path = str(tmp_path / "shadow-unix.sock")
    try:
        boot.install_socket_guard()
        srv = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
        try:
            with pytest.raises(boot.ShadowBlocked):
                srv.bind(sock_path)
        finally:
            srv.close()
        assert not Path(sock_path).exists(), "no socket path created"
        assert boot._blocked_counts["socket"] >= 1
    finally:
        boot.uninstall_all()
    assert _socket.socket.bind is real_bind


def _roots(tmp_path):
    import scripts.cuttle_shadow_app as boot  # noqa: F401 (constants only)

    snap = tmp_path / "snap"
    data = tmp_path / "data"
    snap.mkdir(exist_ok=True)
    data.mkdir(exist_ok=True)
    app_db = snap / "src" / "data" / "db"
    app_db.mkdir(parents=True, exist_ok=True)
    manifest = tmp_path / "m.json"
    return {
        "read": [str(snap), str(data)],
        "write": [str(tmp_path)],
        "exact": {str(manifest)},
        "snap": snap, "data": data, "manifest": manifest,
    }


def test_file_policy_unit(tmp_path):
    """Pure policy: private writes ok (incl. app tree), outside denied."""
    import scripts.cuttle_shadow_app as boot

    r = _roots(tmp_path)
    kw = {"read_roots": r["read"], "write_roots": r["write"],
          "exact_write": r["exact"]}
    # Private app-relative writes allowed (trial finding: an import once
    # created app/src/data/db); manifest.tmp -> manifest rename allowed.
    boot.check_file_event(
        "open", (str(r["snap"] / "src" / "data" / "db" / "a.db"), "w", 0),
        **kw)
    (r["snap"] / "m.tmp").write_text("x", encoding="utf-8")
    boot.check_file_event(
        "os.rename", (str(r["snap"] / "m.tmp"), str(r["manifest"])), **kw)
    with pytest.raises(boot.ShadowBlocked):
        boot.check_file_event(
            "open", (str(tmp_path.parent / "outside.txt"), "w", 0), **kw)
    with pytest.raises(boot.ShadowBlocked):
        boot.check_file_event("open", ("/etc/hostname", "r", 0), **kw)
    boot.check_file_event(
        "open", (str(r["snap"] / "a.py"), "r", 0), **kw)
    # os.open with mode None must not TypeError.
    boot.check_file_event("open", (str(r["snap"] / "a.py"), None, 0), **kw)
    with pytest.raises(boot.ShadowBlocked):
        boot.check_file_event("os.symlink", ("a", "b"), **kw)
    with pytest.raises(boot.ShadowBlocked):
        boot.check_file_event("os.kill", (1234, 15), **kw)
    with pytest.raises(boot.ShadowBlocked):
        boot.check_file_event("os.mkdir", (str(r["data"] / "d"), 511, 7),
                              **kw)  # non-default dir_fd denied
    boot.check_file_event("os.mkdir", (str(r["data"] / "d"), 511, -1),
                          **kw)  # default dir_fd allowed
    with pytest.raises(boot.ShadowBlocked):
        boot.check_file_event("os.remove", (str(r["data"] / "d"), 7),
                              **kw)  # remove fd slot enforced (was dropped)
    boot.check_file_event("os.remove", (str(r["data"] / "d"), -1), **kw)
    with pytest.raises(boot.ShadowBlocked):
        boot.check_file_event(
            "os.rename", (str(r["data"] / "a"), str(r["data"] / "b"),
                         -1, 7), **kw)  # dst_dir_fd enforced
    with pytest.raises(boot.ShadowBlocked):
        boot.check_file_event(
            "sqlite3.connect", ("/tmp/nope.db",), **kw)
    with pytest.raises(boot.ShadowBlocked):
        boot.check_file_event(
            "sqlite3.connect", ("file:/tmp/nope.db?mode=ro",), **kw)
    boot.check_file_event("sqlite3.connect", (":memory:",), **kw)


def test_audit_hook_probe(tmp_path):
    """Bounded bare-interpreter probe: native audit events block, no app.

    Negative targets stay under pytest project temp (outside the instance),
    never drive-root paths. Exercises the sqlite3.dbapi2 alias too.
    """
    import json

    import scripts.cuttle_shadow_app as boot  # noqa: F401 (path only)

    src_dir = str(Path(boot.__file__).resolve().parents[1])
    inst = tmp_path / "inst"
    (inst / "data").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    import sys as _sys
    cfg = {"read": [str(inst), _sys.prefix, _sys.base_prefix],
           "write": [str(inst / "data")], "outside": str(outside)}
    probe = "\n".join([
        "import json, os, sqlite3, sys",
        "sys.path.insert(0, sys.argv[1])",
        "from scripts.cuttle_shadow_app import (ShadowBlocked, install_audit_hook)",
        "cfg = json.loads(sys.argv[2])",
        "install_audit_hook(cfg['read'][0], cfg['write'], set())",
        "assert 'api.web_chat_api' not in sys.modules",
        "open(cfg['write'][0] + '/ok.txt', 'w').write('x')",
        "import sqlite3.dbapi2 as alias",
        "alias.connect(':memory:').execute('select 1').fetchall()",
        "blocked = 0",
        "try:",
        "    open(cfg['outside'] + '/nope.txt', 'w')",
        "except ShadowBlocked:",
        "    blocked += 1",
        "try:",
        "    alias.connect(cfg['outside'] + '/nope.db')",
        "except ShadowBlocked:",
        "    blocked += 1",
        "try:",
        "    os.mkdir(cfg['outside'] + '/nodir')",
        "except ShadowBlocked:",
        "    blocked += 1",
        "assert blocked == 3, blocked",
        "print('PROBE-OK')",
    ])
    proc = subprocess.run(
        [sys.executable, "-c", probe, src_dir, json.dumps(cfg)],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert "PROBE-OK" in proc.stdout, proc.stderr[-2000:]


def test_route_allowlist_contract():
    """Real auth + B1 paths in; execution/effect paths out."""
    import scripts.cuttle_shadow_app as boot

    assert ("POST", "/api/chat") in boot.ALLOW_EXACT
    assert ("POST", "/api/chat-cancel") in boot.ALLOW_EXACT
    assert ("GET", "/api/chat-pending-result") in boot.ALLOW_EXACT
    assert ("POST", "/api/sessions/send") in boot.ALLOW_EXACT
    assert ("POST", "/api/auth/register") in boot.ALLOW_EXACT
    assert ("POST", "/api/auth/login") in boot.ALLOW_EXACT
    assert ("GET", "/api/auth/me") in boot.ALLOW_EXACT
    assert ("GET", "/api/auth/sessions") in boot.ALLOW_EXACT
    assert ("POST", "/api/auth/sessions") in boot.ALLOW_EXACT
    assert boot.DENY_CHAT_PREFIXES == ("/restart", "/cmd", "[button", "[action-form")
    assert not any("restart" in path for _, path in boot.ALLOW_EXACT)
    assert not any("action-form" in path for _, path in boot.ALLOW_EXACT)
    assert not any("output" in path for _, path in boot.ALLOW_EXACT)
    assert boot.ALLOW_GET_PREFIXES == (
        "/static/", "/js/", "/css/", "/img/", "/sounds/",
        "/api/auth/sessions/", "/api/sessions/", "/api/shell/panes/")


def _poll_status(boot, call_id, timeout=10.0):
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = boot._STATUSES.get(call_id)
        if status not in (None, "started"):
            return status
        time.sleep(0.05)
    return boot._STATUSES.get(call_id)


def test_control_state_retains_full_log():
    """Full retained log past 50 entries + all five counters, no ledger."""
    import scripts.cuttle_shadow_app as boot

    saved = list(boot._block_log)
    saved_counts = dict(boot._blocked_counts)
    try:
        boot._block_log.clear()
        for i in range(60):
            boot._block_log.append(f"[shadow-guard] blocked route: GET /deny-{i}")
        boot._block_log.insert(0, "[shadow-guard] blocked file: read /native/x")
        boot._blocked_counts["route"] = 60
        boot._blocked_counts["file"] = 1
        state = boot._control_state()
        assert len(state["blocked_attempts"]) == 61
        assert state["blocked_attempts"][0].endswith("/native/x")
        assert state["blocked_attempts"][-1].endswith("/deny-59")
        assert state["blocked_counts"]["route"] == 60
        assert state["blocked_counts"]["file"] == 1
        assert set(state["blocked_counts"]) == {
            "subprocess", "socket", "executor", "file", "route"}
    finally:
        boot._block_log[:] = saved
        boot._blocked_counts.update(saved_counts)


def test_browser_profile_allowlist():
    """Approved minimum profile: settings/projects/widgets reads, asset
    prefixes, PATCH-only numeric session; vendor/probe routes stay denied."""
    import scripts.cuttle_shadow_app as boot

    for entry in (
            ("GET", "/api/settings/video-background"),
            ("GET", "/api/settings/chat-tts"),
            ("GET", "/api/settings/starred-project"),
            ("GET", "/api/settings/starred-slash"),
            ("GET", "/api/projects"),
            ("GET", "/api/gizmos/tasks")):
        assert entry in boot.ALLOW_EXACT, entry
    for prefix in ("/img/", "/sounds/"):
        assert prefix in boot.ALLOW_GET_PREFIXES
    assert boot.ALLOW_PATCH_SESSION_RE.match("/api/auth/sessions/12")
    assert not boot.ALLOW_PATCH_SESSION_RE.match("/api/auth/sessions/12/x")
    assert not boot.ALLOW_PATCH_SESSION_RE.match("/api/auth/sessions/abc")
    for path in ("/api/agents", "/api/cursor-agent/models",
                 "/api/muse/models", "/api/doctor", "/api/terminal/status",
                 "/api/agent-context", "/api/health",
                 "/api/project-commands", "/api/git/pending-changes"):
        assert ("GET", path) not in boot.ALLOW_EXACT
    assert not any("settings" in p and m != "GET"
                   for m, p in boot.ALLOW_EXACT if "settings" in p)
    # POST-only durable system-notice persist: exact numeric id + suffix.
    assert boot.ALLOW_POST_SESSION_MESSAGE_RE.match(
        "/api/auth/sessions/12/messages")
    assert not boot.ALLOW_POST_SESSION_MESSAGE_RE.match(
        "/api/auth/sessions/12/messages/extra")
    assert not boot.ALLOW_POST_SESSION_MESSAGE_RE.match(
        "/api/auth/sessions/abc/messages")
    assert not boot.ALLOW_POST_SESSION_MESSAGE_RE.match(
        "/api/auth/sessions/12/other")


def test_queue_hold_release_independent():
    """Two holds release independently (old then new)."""
    import threading

    import scripts.cuttle_shadow_app as boot

    boot._QUEUE.clear()
    boot._STATUSES.clear()
    boot._RELEASES.clear()
    boot._STARTED.clear()
    boot._COMPLETED.clear()
    try:
        boot.install_executor_fakes("blocked")
        import api.agent_harness.kernel as kernel

        boot.enqueue_item({"id": "old", "outcome": "success",
                           "hold": True, "response": "first"})
        boot.enqueue_item({"id": "new", "outcome": "success",
                           "hold": True, "response": "second"})
        with pytest.raises(boot.ShadowError, match="duplicate"):
            boot.enqueue_item({"id": "old", "outcome": "success"})
        results = {}

        def _call(key):
            results[key] = kernel.run_agent_web_command("x", "p", 1)
        t1 = threading.Thread(target=_call, args=("old",))
        t2 = threading.Thread(target=_call, args=("new",))
        t1.start()
        assert _poll_status(boot, "old") == "held"
        t2.start()
        assert _poll_status(boot, "new") == "held"
        boot._RELEASES["old"].set()
        t1.join(timeout=15)
        assert results["old"]["response"] == "first"
        boot._RELEASES["new"].set()
        t2.join(timeout=15)
        assert results["new"]["response"] == "second"
    finally:
        boot.uninstall_all()
        boot._QUEUE.clear()
        boot._RELEASES.clear()
    assert boot._COMPLETED == {"old": 1, "new": 1}
    assert boot._STARTED == {"old": 1, "new": 1}


def test_queue_capture_is_immutable():
    """Entry-time capture: later mutation of the queued dict is ignored."""
    import scripts.cuttle_shadow_app as boot

    boot._QUEUE.clear()
    boot._STATUSES.clear()
    boot._ID_COUNTER["n"] = 0
    boot._QUEUE.append({"id": "cap", "outcome": "success",
                        "response": "original"})
    taken = boot._take_queued()
    assert taken["response"] == "original"
    boot._QUEUE.clear()
    boot._STATUSES.clear()


def test_supervised_markers_separated(monkeypatch, tmp_path):
    """Supervised: real row id captured; skip vs coord markers exclusive."""
    import scripts.cuttle_shadow_app as boot
    from api.auth_db import AuthDatabase

    db = AuthDatabase(tmp_path / "sup.db")
    owner = db.create_user("o@l", "O", "local", password="x")
    sid = db.create_chat_session(owner, "s")
    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)
    boot._QUEUE.clear()
    boot._STATUSES.clear()
    boot._COMPLETED.clear()
    boot._CANONICAL_IDS.clear()
    try:
        boot.install_executor_fakes("blocked")
        import api.agent_harness.kernel as kernel

        boot.enqueue_item({"id": "s1", "outcome": "supervised",
                           "supervised_marker": "skip"})
        out = kernel.run_agent_web_command("x", "p", sid)
        assert out["skip_history_persist"] is True
        assert "coordinator_response_message_id" not in out
        boot.enqueue_item({"id": "s2", "outcome": "supervised",
                           "supervised_marker": "coord"})
        out2 = kernel.run_agent_web_command("x", "p", sid)
        assert "skip_history_persist" not in out2
        assert out2["coordinator_response_message_id"] == boot._CANONICAL_IDS["s2"]
        rows = [m for m in db.get_messages(sid) if m["role"] == "assistant"]
        assert len(rows) == 2
        assert {boot._CANONICAL_IDS["s1"], boot._CANONICAL_IDS["s2"]} == {
            rows[0]["id"], rows[1]["id"]}
    finally:
        boot.uninstall_all()
        boot._QUEUE.clear()


def test_s1_two_shadows_dynamic():
    """Bounded real-boot S1 gate: two simultaneous shadows, isolated auth,
    deny probes, occupied-port refusal. Whole test must finish < 120s;
    owned handles always reaped. First unexpected failure returns."""
    import http.cookiejar
    import shutil
    import time
    import urllib.error
    import urllib.request
    import uuid

    from api import dev_instance as di

    t0 = time.time()

    def _opener(jar):
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(jar))

    def _api(op, origin, path, payload=None, headers=None, timeout=8):
        data = None
        hds = dict(headers or {})
        if payload is not None:
            data = json_dumps(payload).encode()
            hds["Content-Type"] = "application/json"
        req = urllib.request.Request(origin + path, data=data,
                                     headers=hds, method="POST"
                                     if payload is not None else "GET")
        try:
            with op.open(req, timeout=timeout) as resp:
                return resp.status, json_loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode()
            try:
                return exc.code, json_loads(raw)
            except ValueError:
                return exc.code, {"raw": raw}

    def json_dumps(payload):
        import json as _j
        return _j.dumps(payload)

    def json_loads(text):
        import json as _j
        return _j.loads(text)

    tag = uuid.uuid4().hex[:8]
    seed1 = seed2 = None
    child1 = child2 = None
    try:
        seed1 = di.prepare_snapshot(CANDIDATE, f"s1a-{tag}")
        child1, m1 = di.launch(seed1, "blocked", 0, timeout=60)
        try:
            seed2 = di.prepare_snapshot(CANDIDATE, f"s1b-{tag}")
            child2, m2 = di.launch(seed2, "blocked", 0, timeout=60)
            assert child1.pid != child2.pid
            assert m1["port"] != m2["port"]
            assert m1["nonce"] != m2["nonce"]
            assert seed1["data_dir"] != seed2["data_dir"]
            o1, o2 = m1["origin"], m2["origin"]
            jar1, jar2 = http.cookiejar.CookieJar(), http.cookiejar.CookieJar()
            op1, op2 = _opener(jar1), _opener(jar2)

            # Clean boot: zero unexpected blocked attempts.
            st, state = _api(op1, o1, "/__shadow/control",
                             headers={"X-Shadow-Nonce": m1["nonce"]})
            assert st == 200 and state["success"] is True
            assert state["blocked_attempts"] == [], state["blocked_attempts"]
            assert state["blocked_counts"] == {"subprocess": 0, "socket": 0,
                "executor": 0, "file": 0, "route": 0}, state["blocked_counts"]

            # Real register + session on shadow 1 (private cookie jar).
            st, reg = _api(op1, o1, "/api/auth/register",
                           {"username": f"sh1_{tag}", "password": "shadowpass1"})
            assert st == 200, reg
            assert reg["success"] is True
            assert any(c.name == "session_token" for c in jar1), "cookie set"
            st, created = _api(op1, o1, "/api/auth/sessions",
                               {"session_name": "s1"})
            assert st == 200, created
            sid1 = created["session_id"]
            st, hist = _api(op1, o1, f"/api/auth/sessions/{sid1}/messages")
            assert st == 200, hist
            # Durable system-notice persist: real endpoint appends a SYSTEM
            # row; role=assistant is rejected 400 by production validation.
            notice = {"content": "s1 stop notice", "role": "system"}
            st, posted = _api(op1, o1,
                              f"/api/auth/sessions/{sid1}/messages", notice)
            assert st == 200, (st, posted)
            st, hist = _api(op1, o1, f"/api/auth/sessions/{sid1}/messages")
            assert st == 200, hist
            rows = hist.get("messages") or []
            assert any(r.get("role") == "system" and
                       "s1 stop notice" in (r.get("content") or "")
                       for r in rows), rows
            st, bad = _api(op1, o1, f"/api/auth/sessions/{sid1}/messages",
                           {"content": "x", "role": "assistant"})
            assert st == 400, (st, bad)

            # Shadow 2 isolated: fresh register, empty history, no jar leak.
            st, reg2 = _api(op2, o2, "/api/auth/register",
                            {"username": f"sh2_{tag}", "password": "shadowpass2"})
            assert st == 200 and reg2["success"] is True
            st, created2 = _api(op2, o2, "/api/auth/sessions",
                                {"session_name": "s2"})
            assert st == 200, created2
            sid2 = created2["session_id"]
            # Separate private DBs reuse the same id sequence: isolation is
            # proved by rows, not id inequality. Shadow 1's account must not
            # exist in shadow 2 (registration there is closed once its owner
            # exists, so prove it by login). Own jar keeps jar2 the s2 owner.
            op_dup = _opener(http.cookiejar.CookieJar())
            st, dup = _api(op_dup, o2, "/api/auth/login",
                           {"username": f"sh1_{tag}", "password": "shadowpass1"})
            assert st == 401 and not dup.get("success"), \
                "shadow 1's account is absent from shadow 2's DB"
            tok1 = next(c.value for c in jar1 if c.name == "session_token")
            tok2 = next(c.value for c in jar2 if c.name == "session_token")
            assert tok1 and tok2 and tok1 != tok2, "separate private cookies"
            st, hist2 = _api(op2, o2, f"/api/auth/sessions/{sid2}/messages")
            assert st == 200, hist2
            assert not hist2.get("messages"), "fresh history empty"
            st, cross = _api(op2, o2, f"/api/auth/sessions/{sid1 + 100000}/messages")
            assert st == 404, (st, cross)  # unknown sid denied
            st, lst2 = _api(op2, o2, "/api/auth/sessions")
            assert st == 200, lst2
            # Empty sessions are hidden from lists, so no count is
            # asserted; row isolation rests on independent registration,
            # distinct tokens, and per-shadow histories above.
            # Cross-auth proof: jar1's cookie is meaningless on shadow 2.
            st, me = _api(op1, o2, "/api/auth/me")
            assert st == 401, (st, me)
            st, me2 = _api(op2, o2, "/api/auth/me")
            assert st == 200 and me2.get("success") is True, me2

            # Invalid nonce -> 403 (read and write).
            st, _ = _api(op1, o1, "/__shadow/control",
                         headers={"X-Shadow-Nonce": "wrong"})
            assert st == 403, st
            st, _ = _api(op1, o1, "/__shadow/control",
                         {"queue": []}, headers={"X-Shadow-Nonce": "wrong"})
            assert st == 403, st

            # Forbidden routes -> 403 at the route gate only.
            for method_path in (
                    ("GET", "/api/health"),
                    ("POST", "/api/flask/restart/status"),
                    ("POST", "/api/action-form/run"),
            ):
                method, path = method_path
                payload = {} if method == "POST" else None
                st, body = _api(op1, o1, path, payload)
                assert st == 403, (path, st, body)
            st, state = _api(op1, o1, "/__shadow/control",
                             headers={"X-Shadow-Nonce": m1["nonce"]})
            assert st == 200, state
            assert state["blocked_attempts"], "denies must be logged"
            assert all("blocked route:" in entry
                       for entry in state["blocked_attempts"]), \
                "route-level blocks only, no vendor/process/network probe"
            assert len(state["blocked_attempts"]) == 3, \
                state["blocked_attempts"]
            assert state["blocked_counts"] == {
                "subprocess": 0, "socket": 0, "executor": 0,
                "file": 0, "route": 3}, state["blocked_counts"]

            # Occupied port refused; existing owned listener unaffected.
            seed3 = di.prepare_snapshot(CANDIDATE, f"s1c-{tag}")
            try:
                with pytest.raises(di.ShadowError):
                    di.launch(seed3, "blocked", m1["port"], timeout=20)
            finally:
                shutil.rmtree(seed3["root"], ignore_errors=True)
            assert child1.proc.poll() is None, "owned listener survived"
        finally:
            # Unconditional: every owned child stops even if the second
            # launch failed. stop() is idempotent; no duplicate stops.
            if child2 is not None:
                child2.stop()
            if child1 is not None:
                child1.stop()
    finally:
        for seed in (seed1, seed2):
            if seed is not None:
                shutil.rmtree(seed["root"], ignore_errors=True)
    elapsed = time.time() - t0
    assert elapsed < 120, f"S1 gate took {elapsed:.1f}s"


def test_default_scenario_path():
    """Default (non-blocked) scenario: unique ids, starts/completions."""
    import scripts.cuttle_shadow_app as boot

    boot._QUEUE.clear()
    boot._STATUSES.clear()
    boot._STARTED.clear()
    boot._COMPLETED.clear()
    boot._ID_COUNTER["n"] = 0
    try:
        boot.install_executor_fakes("success")
        import api.agent_harness.kernel as kernel

        r1 = kernel.run_agent_web_command("x", "p1", 1)
        r2 = kernel.run_agent_web_command("x", "p2", 1)
        assert r1["success"] is True and r2["success"] is True
        assert r1["response"] != r2["response"]
        assert boot._STARTED == {"d0": 1, "d1": 1}
        assert boot._COMPLETED == {"d0": 1, "d1": 1}
    finally:
        boot.uninstall_all()
        boot._QUEUE.clear()
    with pytest.raises(boot.ShadowError, match="hold"):
        boot.install_executor_fakes("hold")
    boot.uninstall_all()


def test_scenario_shapes_and_seams():
    """Deterministic seam results; seams resolve to real owner modules."""
    import importlib

    import scripts.cuttle_shadow_app as boot

    assert "supervised" in boot.SCENARIOS and "hold" in boot.SCENARIOS
    assert boot._result_shape("success", "p")["success"] is True
    fail = boot._result_shape("usefulfailure", "p")
    assert fail["success"] is False and fail["response"]
    assert boot._result_shape("emptyfailure", "p")["response"] == ""
    assert boot._result_shape("cancelled", "p")["cancelled"] is True
    assert boot._result_shape("system", "p")["ui"] == "system"
    with pytest.raises(boot.ShadowError):
        boot._result_shape("nonexistent", "p")
    for mod_name, attr in boot.SEAMS:
        assert hasattr(importlib.import_module(mod_name), attr), mod_name


def test_discard_snapshot_removes_only_shadow_roots(tiny_repo, tmp_path):
    seed = prepare_snapshot(tiny_repo, f"disc-{uuid.uuid4().hex[:8]}")
    assert Path(seed["root"]).is_dir()
    assert di.discard_snapshot(seed) is True
    assert not Path(seed["root"]).exists()
    # Anything that is not temp/shadows/<id> is refused untouched.
    outsider = tmp_path / "keep-me"
    outsider.mkdir()
    assert di.discard_snapshot({"root": str(outsider)}) is False
    assert di.discard_snapshot({"root": str(tiny_repo / "temp")}) is False
    assert outsider.is_dir() and (tiny_repo / "temp").is_dir()


def test_prune_keeps_fresh_and_live_snapshots(tiny_repo):
    import json
    import os

    old = prepare_snapshot(tiny_repo, "old-dead")
    live = prepare_snapshot(tiny_repo, "old-live")
    fresh = prepare_snapshot(tiny_repo, "fresh")
    Path(live["manifest"]).write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    Path(old["manifest"]).write_text(json.dumps({"pid": 0}), encoding="utf-8")
    day_ago = __import__("time").time() - 86400
    for seed in (old, live):
        os.utime(seed["root"], (day_ago, day_ago))

    assert di.prune_snapshots(tiny_repo, 3600) == ["old-dead"]
    assert not Path(old["root"]).exists()
    assert Path(live["root"]).is_dir(), "a running child's snapshot is kept"
    assert Path(fresh["root"]).is_dir(), "recent snapshots are kept"


def test_up_cli_discards_snapshot_after_stop(tiny_repo, monkeypatch):
    seeds = []

    class FakeProc:
        pid = 4242

        def wait(self):
            return 0

    class FakeChild:
        proc = FakeProc()
        pid = 4242

        def stop(self):
            pass

    def fake_launch(seed, scenario, port, timeout):
        seeds.append(seed)
        return FakeChild(), {"port": 1}

    monkeypatch.setattr(di, "launch", fake_launch)
    monkeypatch.setattr(di, "resolve_port", lambda port: 0)
    assert di.main(["up", "--candidate", str(tiny_repo)]) == 0
    assert seeds and not Path(seeds[0]["root"]).exists()

    assert di.main(["up", "--candidate", str(tiny_repo), "--keep"]) == 0
    assert Path(seeds[1]["root"]).is_dir()

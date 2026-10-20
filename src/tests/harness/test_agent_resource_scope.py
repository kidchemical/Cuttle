"""Agent CLIs get their own systemd user scope; failures leave them running natively."""
import subprocess
import sys
import time

import pytest

from api.agent_harness import resource_scope as rs
from scripts.utilities import agent_process

GiB = 1 << 30


def test_parse_size_units_and_percent():
    assert rs.parse_size("6G") == 6 * GiB
    assert rs.parse_size("512M") == 512 << 20
    assert rs.parse_size(4096) == 4096
    assert rs.parse_size("60%", total=10 * GiB) == 6 * GiB
    assert rs.parse_size("") is None and rs.parse_size("infinity") is None
    with pytest.raises(ValueError):
        rs.parse_size("lots")


def test_resolve_limits_defaults_overrides_and_off_switches():
    assert rs.resolve_limits(None, env={}) is None
    assert rs.resolve_limits({"enabled": "false"}, env={}) is None
    limits = rs.resolve_limits({"enabled": True}, env={}, total_memory=32 * GiB)
    assert limits["memory_max"] == 8 * GiB and limits["memory_high"] == 6 * GiB
    assert limits["slice_memory_high"] == int(32 * GiB * 0.6)
    assert rs.resolve_limits({"enabled": True, "memory_max": "12G", "memory_swap_max": ""}, env={})["memory_max"] == 12 * GiB
    assert rs.resolve_limits({"enabled": True, "memory_swap_max": ""}, env={})["memory_swap_max"] is None
    assert rs.resolve_limits({"enabled": False}, env={}) is None
    assert rs.resolve_limits({"enabled": True}, env={"CUTTLE_AGENT_SCOPES": "0"}) is None


def test_start_scope_argv_is_a_well_formed_transient_unit_call():
    limits = rs.resolve_limits({"enabled": True}, env={}, total_memory=32 * GiB)
    argv = rs.start_scope_argv("cuttle-agent-claude-42.scope", [42, 43], limits, "Cuttle agent CLI claude")
    assert argv[:9] == ["busctl", "--user", "call", "org.freedesktop.systemd1", "/org/freedesktop/systemd1",
                        "org.freedesktop.systemd1.Manager", "StartTransientUnit", "ssa(sv)a(sa(sv))",
                        "cuttle-agent-claude-42.scope"]
    assert argv[9] == "fail" and argv[-1] == "0"
    body = argv[11:-1]
    assert body[:5] == ["PIDs", "au", "2", "42", "43"]
    props = {body[i]: (body[i + 1], body[i + 2]) for i in range(5, len(body), 3)}
    assert int(argv[10]) == len(props) + 1
    assert props["Slice"] == ("s", rs.SLICE)
    assert props["OOMPolicy"] == ("s", "kill")
    assert props["MemoryMax"] == ("t", str(8 * GiB))
    assert props["TasksMax"] == ("t", "4096")


def test_unit_name_is_sanitized():
    assert rs.unit_name("node (claude)", 7) == "cuttle-agent-node-claude-7.scope"
    assert rs.unit_name("", 7) == "cuttle-agent-cli-7.scope"


def test_slice_has_aggregate_soft_and_hard_limits():
    limits = rs.resolve_limits({"enabled": True}, env={}, total_memory=32 * GiB)
    argv = rs.slice_argv(limits)
    assert argv[-7:] == ["2", "MemoryHigh", "t", str(int(32 * GiB * .6)),
                         "MemoryMax", "t", str(int(32 * GiB * .7))]


def test_scope_attachment_finishes_before_returning(monkeypatch):
    monkeypatch.setattr(rs.sys, "platform", "linux")
    monkeypatch.setattr(rs.os.path, "isfile", lambda _: True)
    monkeypatch.setattr(rs.shutil, "which", lambda _: "/usr/bin/busctl")
    monkeypatch.setattr(rs, "_load_limits", lambda: {"enabled": True})
    completed = []
    monkeypatch.setattr(rs, "_confine", lambda pid: completed.append(pid) or True)
    proc = type("P", (), {"pid": 1234})()
    try:
        assert rs.confine(proc) is True
        assert completed == [1234]
    finally:
        rs._confined.discard(1234)


def test_disabled_scopes_never_contact_systemd(monkeypatch):
    monkeypatch.setattr(rs.sys, "platform", "linux")
    monkeypatch.setattr(rs.os.path, "isfile", lambda _: True)
    monkeypatch.setattr(rs.shutil, "which", lambda _: "/usr/bin/busctl")
    monkeypatch.setattr(rs, "_load_limits", lambda: None)
    monkeypatch.setattr(rs, "_run", lambda _: pytest.fail("systemd contacted while disabled"))
    assert rs.confine(type("P", (), {"pid": 1234})()) is False


def test_aggregate_limit_failure_is_reported_and_retried(monkeypatch):
    limits = rs.resolve_limits({"enabled": True}, env={}, total_memory=32 * GiB)
    monkeypatch.setattr(rs, "_load_limits", lambda: limits)
    monkeypatch.setattr(rs, "_comm", lambda _: "fake-cli")
    checks = iter([False, True, False, True])
    monkeypatch.setattr(rs, "_already_scoped", lambda *args: next(checks))
    monkeypatch.setattr(rs, "_descendants", lambda _: [])
    monkeypatch.setattr(rs, "_slice_limits", None)
    calls = []
    def run(argv):
        calls.append(argv)
        return (False, "unavailable") if argv[6] == "SetUnitProperties" else (True, "")
    monkeypatch.setattr(rs, "_run", run)
    assert rs._confine_once(1234) is False
    assert rs._confine_once(1235) is False
    assert len([a for a in calls if a[6] == "SetUnitProperties"]) == 2


def test_attach_confines_even_without_a_chat_session(monkeypatch):
    seen = []
    monkeypatch.setattr(rs, "confine", seen.append)
    proc = type("P", (), {"pid": 1234})()
    agent_process.attach_to_chat_run(None, proc)
    assert seen == [proc]


def test_confine_failure_leaves_cli_running_unconfined(monkeypatch):
    if not sys.platform.startswith("linux"):
        pytest.skip("Linux user scopes only")
    calls = []

    def fake_run(argv, **_kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1, "", "Failed to connect to bus")

    monkeypatch.setattr(rs.subprocess, "run", fake_run)
    monkeypatch.setattr(rs.shutil, "which", lambda _name: "/usr/bin/busctl")
    monkeypatch.setattr(rs, "_load_limits", lambda: rs.resolve_limits({"enabled": True}, env={}, total_memory=32 * GiB))
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(2)"])
    try:
        rs.confine(proc)
        deadline = time.time() + 5
        while not calls and time.time() < deadline:
            time.sleep(0.02)
        assert calls and calls[0][6] == "StartTransientUnit"
        assert str(proc.pid) in calls[0]
        assert proc.poll() is None  # still running natively
    finally:
        proc.kill()
        proc.wait()


def test_non_linux_never_loads_settings_or_contacts_systemd(monkeypatch):
    monkeypatch.setattr(rs, "_load_limits", lambda: pytest.fail("settings read on unsupported platform"))
    for platform in ("win32", "darwin"):
        monkeypatch.setattr(rs.sys, "platform", platform)
        assert rs.confine(type("P", (), {"pid": 1234})()) is False


def test_legacy_cgroups_never_contact_systemd(monkeypatch):
    monkeypatch.setattr(rs.sys, "platform", "linux")
    monkeypatch.setattr(rs.os.path, "isfile", lambda _: False)
    monkeypatch.setattr(rs, "_load_limits", lambda: pytest.fail("settings loaded without cgroup v2"))
    assert rs.confine(type("P", (), {"pid": 1234})()) is False


def test_removing_aggregate_caps_clears_previous_limits():
    limits = rs.resolve_limits({"enabled": True, "slice_memory_high": "", "slice_memory_max": ""}, env={})
    argv = rs.slice_argv(limits)
    assert argv[-6:] == ["MemoryHigh", "t", str((1 << 64) - 1), "MemoryMax", "t", str((1 << 64) - 1)]


def test_host_process_cannot_be_moved_into_agent_scope(monkeypatch):
    monkeypatch.setattr(rs.sys, "platform", "linux")
    monkeypatch.setattr(rs.os.path, "isfile", lambda _: True)
    monkeypatch.setattr(rs.shutil, "which", lambda _: "/usr/bin/busctl")
    monkeypatch.setattr(rs, "_load_limits", lambda: {"enabled": True})
    monkeypatch.setattr(rs, "_confine", lambda _: pytest.fail("host process moved"))
    assert rs.confine(type("P", (), {"pid": rs.os.getpid()})()) is False

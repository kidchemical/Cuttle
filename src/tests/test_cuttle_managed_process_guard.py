"""Tests for Cuttle-managed process kill guard + restart-safety harness coverage."""

from __future__ import annotations

import pytest

from api.cuttle_managed_process_guard import (
    DENY_MESSAGE,
    evaluate_shell_command,
)
from api import restart_safety_policy as rsp
from api.cuttle_ui_capabilities import CUTTLE_UI_CAPABILITIES_TEXT, with_cuttle_ui_capabilities


MANAGED = {27056: "python.exe C:\\Projects\\Cuttle\\src\\api\\web_chat_api.py"}
DAEMON = {6140: "python.exe src\\scripts\\cuttle_daemon.py"}


def test_block_taskkill_managed_flask_pid():
    r = evaluate_shell_command(
        "taskkill /F /PID 27056",
        managed_pids={27056},
        managed_cmdlines=MANAGED,
    )
    assert r["allow"] is False
    assert "graceful" in (r.get("denial") or "").lower()
    assert DENY_MESSAGE.splitlines()[0] in r["denial"]


def test_block_taskkill_daemon_pid():
    r = evaluate_shell_command(
        "taskkill /F /T /PID 6140",
        managed_pids={6140},
        managed_cmdlines=DAEMON,
    )
    assert r["allow"] is False
    assert r["reason"] == "managed_pid_kill"


def test_allow_unrelated_pid_kill():
    r = evaluate_shell_command(
        "taskkill /F /PID 99999",
        managed_pids={27056, 6140},
        managed_cmdlines={**MANAGED, **DAEMON},
    )
    assert r["allow"] is True


def test_block_cmdline_filter_kill_without_pid():
    cmd = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
        "Where-Object { $_.CommandLine -like '*web_chat_api*' } | "
        "ForEach-Object { taskkill /F /PID $_.ProcessId }"
    )
    r = evaluate_shell_command(cmd, managed_pids=set(), managed_cmdlines={})
    assert r["allow"] is False
    assert "graceful" in r["denial"]


def test_authorized_daemon_path_allowed():
    """Daemon does not use env auth — this test documents removal of that path.

    Explicit authorize= was removed; spoofed env must not allow kills.
    """
    r = evaluate_shell_command(
        "taskkill /F /PID 27056",
        managed_pids={27056},
        env={"CUTTLE_INTERNAL_RESTART": "1"},
    )
    assert r["allow"] is False
    assert "graceful" in (r.get("denial") or "").lower()


def test_spoofed_env_var_cannot_bypass_pid_kill(monkeypatch):
    """Agent/user setting CUTTLE_INTERNAL_RESTART must not authorize."""
    monkeypatch.setenv("CUTTLE_INTERNAL_RESTART", "1")
    r = evaluate_shell_command(
        "taskkill /F /PID 27056",
        managed_pids={27056},
        managed_cmdlines=MANAGED,
        env={"CUTTLE_INTERNAL_RESTART": "1"},
    )
    assert r["allow"] is False
    assert r.get("reason") == "managed_pid_kill"


def test_spoofed_env_in_command_string_still_blocked():
    cmd = (
        "$env:CUTTLE_INTERNAL_RESTART='1'; "
        "taskkill /F /PID 27056"
    )
    r = evaluate_shell_command(cmd, managed_pids={27056}, managed_cmdlines=MANAGED)
    assert r["allow"] is False


def test_cmdline_filter_daemon_kill_ignores_spoof_env_in_os(monkeypatch):
    monkeypatch.setenv("CUTTLE_INTERNAL_RESTART", "1")
    cmd = (
        "$env:CUTTLE_INTERNAL_RESTART='1'; "
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.CommandLine -like '*cuttle_daemon*' } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
    )
    r = evaluate_shell_command(cmd, managed_pids=set(), managed_cmdlines={})
    assert r["allow"] is False
    assert "/restart graceful" in r["denial"]


def test_cursor_policy_in_capabilities_and_agents_md():
    text = CUTTLE_UI_CAPABILITIES_TEXT.lower()
    assert "restart" in text
    assert "action-forms.md" in CUTTLE_UI_CAPABILITIES_TEXT
    injected = with_cuttle_ui_capabilities("hello")
    assert "action-forms.md" in injected

    from pathlib import Path

    agents = Path(__file__).resolve().parents[2] / "AGENTS.md"
    assert agents.is_file()
    ag = agents.read_text(encoding="utf-8")
    assert "/restart graceful" in ag
    assert "web_chat_api" in ag.lower() or "taskkill" in ag.lower()


def test_codex_and_hermes_coverage_declared_advisory_not_hard():
    codex = rsp.assert_harness_not_silently_protected("codex")
    assert codex["hard_enforcement"] is None
    hermes = rsp.assert_harness_not_silently_protected("hermes")
    assert hermes["hard_enforcement"] is None
    deepseek = rsp.assert_harness_not_silently_protected("deepseek")
    assert deepseek["hard_enforcement"] is None
    cursor = rsp.assert_harness_not_silently_protected("cursor")
    assert cursor["hard_enforcement"] is None


def test_unknown_harness_not_silently_protected():
    with pytest.raises(KeyError, match="not listed"):
        rsp.assert_harness_not_silently_protected("totally-new-agent")

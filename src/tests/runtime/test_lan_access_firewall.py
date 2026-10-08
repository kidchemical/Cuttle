"""Windows LAN firewall rules stay scoped: Private profile + LocalSubnet only.

The removed ``ensure_windows_lan_firewall_rule_open`` fallback created a
``-RemoteAddress Any`` rule; runtime rules must never do that. Existing
installs may still carry that legacy rule or an older broad (Private,Public)
same-named rule, so ensure treats a rule as OK only when every enabled copy
is Private + LocalSubnet (replace, never extend) and always deletes the
legacy Open LAN rule. These tests mock subprocess/platform and assert the
exact PowerShell args.
"""

import re
import sys
from types import SimpleNamespace

from api import lan_access


def _ok(stdout="0"):
    return SimpleNamespace(returncode=0, stdout=stdout, stderr="")


def _fail():
    return SimpleNamespace(returncode=1, stdout="", stderr="boom")


def _run_win(monkeypatch, results):
    """Pretend to be Windows; feed canned subprocess results, record commands."""
    monkeypatch.setattr(sys, "platform", "win32")
    commands = []

    def fake_run(cmd, **kwargs):
        commands.append(cmd)
        return results.pop(0)

    monkeypatch.setattr(lan_access.subprocess, "run", fake_run)
    return commands


def _cmds(commands, verb):
    return [c for c in commands if verb in c[-1]]


def _mock_ports(monkeypatch, https=8080, phone=8888, http=8000):
    monkeypatch.setattr(lan_access, "get_primary_https_port", lambda: https)
    monkeypatch.setattr(lan_access, "get_phone_https_port", lambda: phone)
    monkeypatch.setattr(lan_access, "get_http_fallback_port", lambda: http)


def test_open_variant_is_gone():
    assert not hasattr(lan_access, "ensure_windows_lan_firewall_rule_open")
    assert not hasattr(lan_access, "_FIREWALL_RULE_HTTP_OPEN")


def test_rule_creation_is_private_and_local_subnet_only(monkeypatch):
    commands = _run_win(monkeypatch, [_ok("0"), _ok("0"), _ok(""), _ok("created")])
    _mock_ports(monkeypatch)

    assert lan_access.ensure_windows_lan_firewall_rule(8443) is True

    creates = _cmds(commands, "New-NetFirewallRule")
    assert len(creates) == 1, commands
    cmd = creates[0][-1]
    assert "-LocalPort 8443" in cmd
    assert "-RemoteAddress LocalSubnet" in cmd
    assert re.search(r"-Profile Private(\s|$)", cmd), cmd
    assert "Public" not in cmd, cmd
    assert "Any" not in cmd, cmd


def test_rule_creation_exact_args(monkeypatch):
    commands = _run_win(monkeypatch, [_ok("0"), _ok("0"), _ok(""), _ok("created")])
    _mock_ports(monkeypatch)

    lan_access.ensure_windows_lan_firewall_rule(8080)

    creates = _cmds(commands, "New-NetFirewallRule")
    assert len(creates) == 1, commands
    assert (
        "-Direction Inbound -Protocol TCP -LocalPort 8080 -Action Allow "
        "-Profile Private -RemoteAddress LocalSubnet"
    ) in creates[0][-1], creates[0][-1]


def test_scoped_probe_checks_profile_and_address_filter(monkeypatch):
    commands = _run_win(monkeypatch, [_ok("1"), _ok("1")])
    _mock_ports(monkeypatch)

    assert lan_access.ensure_windows_lan_firewall_rule(8080) is True
    assert len(commands) == 2, commands
    scoped_probe = commands[1][-1]
    assert "Get-NetFirewallAddressFilter" in scoped_probe, scoped_probe
    assert "$_.Profile -eq 'Private'" in scoped_probe, scoped_probe
    assert "$_.RemoteAddress -eq 'LocalSubnet'" in scoped_probe, scoped_probe


def test_scoped_existing_rule_creates_nothing(monkeypatch):
    commands = _run_win(monkeypatch, [_ok("1"), _ok("1")])
    _mock_ports(monkeypatch)

    assert lan_access.ensure_windows_lan_firewall_rule(8080) is True
    assert len(commands) == 2, commands
    assert _cmds(commands, "New-NetFirewallRule") == [], commands
    assert _cmds(commands, "Remove-NetFirewallRule") == [], commands


def test_broad_existing_rule_is_removed_and_recreated_scoped(monkeypatch):
    commands = _run_win(monkeypatch, [_ok("1"), _ok("0"), _ok(""), _ok("created")])
    _mock_ports(monkeypatch)

    assert lan_access.ensure_windows_lan_firewall_rule(8080) is True

    removes = _cmds(commands, "Remove-NetFirewallRule")
    assert len(removes) == 1, commands
    assert "Cuttle LAN HTTPS (LocalSubnet)" in removes[0][-1], removes[0][-1]
    creates = _cmds(commands, "New-NetFirewallRule")
    assert len(creates) == 1, commands
    cmd = creates[0][-1]
    assert "-RemoteAddress LocalSubnet" in cmd, cmd
    assert re.search(r"-Profile Private(\s|$)", cmd), cmd
    assert "Public" not in cmd and "Any" not in cmd, cmd


def test_mixed_scoped_and_broad_rules_are_replaced(monkeypatch):
    commands = _run_win(monkeypatch, [_ok("2"), _ok("1"), _ok(""), _ok("created")])
    _mock_ports(monkeypatch)

    assert lan_access.ensure_windows_lan_firewall_rule(8080) is True
    assert len(_cmds(commands, "Remove-NetFirewallRule")) == 1, commands
    assert len(_cmds(commands, "New-NetFirewallRule")) == 1, commands


def test_unreadable_probe_recreates_scoped(monkeypatch):
    commands = _run_win(monkeypatch, [_fail(), _fail(), _ok(""), _ok("created")])
    _mock_ports(monkeypatch)

    assert lan_access.ensure_windows_lan_firewall_rule(8080) is True
    assert len(_cmds(commands, "New-NetFirewallRule")) == 1, commands


def test_non_windows_creates_no_rule(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    called = []
    monkeypatch.setattr(
        lan_access.subprocess, "run", lambda *a, **k: called.append(a) or _ok()
    )
    assert lan_access.ensure_windows_lan_firewall_rule(8080) is False
    assert called == []


def test_ensure_all_removes_legacy_open_rule(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(lan_access, "get_primary_https_port", lambda: 8443)
    monkeypatch.setattr(lan_access, "get_phone_https_port", lambda: 8890)
    monkeypatch.setattr(lan_access, "get_http_fallback_port", lambda: 8001)
    seen = []
    monkeypatch.setattr(
        lan_access,
        "ensure_windows_lan_firewall_rule",
        lambda port=None: seen.append(port) or True,
    )
    commands = []
    monkeypatch.setattr(
        lan_access.subprocess,
        "run",
        lambda cmd, **k: commands.append(cmd) or _ok(""),
    )

    assert lan_access.ensure_all_lan_firewall_rules() is True
    assert seen == [8443, 8890, 8001], seen
    removes = _cmds(commands, "Remove-NetFirewallRule")
    assert any("Cuttle LAN HTTP (Open LAN)" in c[-1] for c in removes), commands
    assert _cmds(commands, "New-NetFirewallRule") == [], commands

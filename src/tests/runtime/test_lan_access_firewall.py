"""Windows LAN firewall rules stay scoped: Private profile + LocalSubnet only.

The removed ``ensure_windows_lan_firewall_rule_open`` fallback created a
``-RemoteAddress Any`` rule; runtime rules must never do that. These tests
mock subprocess/platform and assert the exact ``New-NetFirewallRule`` args.
"""

import re
import sys
from types import SimpleNamespace

from api import lan_access


def _ok(stdout="0"):
    return SimpleNamespace(returncode=0, stdout=stdout, stderr="")


def _run_win(monkeypatch, results):
    """Pretend to be Windows; feed canned subprocess results, record commands."""
    monkeypatch.setattr(sys, "platform", "win32")
    commands = []

    def fake_run(cmd, **kwargs):
        commands.append(cmd)
        return results.pop(0)

    monkeypatch.setattr(lan_access.subprocess, "run", fake_run)
    return commands


def _new_rule_cmd(commands):
    creates = [c for c in commands if "New-NetFirewallRule" in c[-1]]
    assert len(creates) == 1, f"expected one rule creation, got: {commands!r}"
    return creates[0][-1]


def test_open_variant_is_gone():
    assert not hasattr(lan_access, "ensure_windows_lan_firewall_rule_open")
    assert not hasattr(lan_access, "_FIREWALL_RULE_HTTP_OPEN")


def test_rule_creation_is_private_and_local_subnet_only(monkeypatch):
    commands = _run_win(monkeypatch, [_ok("0"), _ok("created")])
    monkeypatch.setattr(lan_access, "get_primary_https_port", lambda: 8080)
    monkeypatch.setattr(lan_access, "get_phone_https_port", lambda: 8888)
    monkeypatch.setattr(lan_access, "get_http_fallback_port", lambda: 8000)

    assert lan_access.ensure_windows_lan_firewall_rule(8443) is True

    cmd = _new_rule_cmd(commands)
    assert "-LocalPort 8443" in cmd
    assert "-RemoteAddress LocalSubnet" in cmd
    assert re.search(r"-Profile Private(\s|$)", cmd), cmd
    assert "Public" not in cmd, cmd
    assert "Any" not in cmd, cmd


def test_rule_creation_exact_args(monkeypatch):
    commands = _run_win(monkeypatch, [_ok("0"), _ok("created")])
    monkeypatch.setattr(lan_access, "get_primary_https_port", lambda: 8080)
    monkeypatch.setattr(lan_access, "get_phone_https_port", lambda: 8888)
    monkeypatch.setattr(lan_access, "get_http_fallback_port", lambda: 8000)

    lan_access.ensure_windows_lan_firewall_rule(8080)

    cmd = _new_rule_cmd(commands)
    assert (
        "-Direction Inbound -Protocol TCP -LocalPort 8080 -Action Allow "
        "-Profile Private -RemoteAddress LocalSubnet"
    ) in cmd, cmd


def test_existing_rule_creates_nothing(monkeypatch):
    commands = _run_win(monkeypatch, [_ok("1")])
    monkeypatch.setattr(lan_access, "get_primary_https_port", lambda: 8080)
    monkeypatch.setattr(lan_access, "get_phone_https_port", lambda: 8888)
    monkeypatch.setattr(lan_access, "get_http_fallback_port", lambda: 8000)

    assert lan_access.ensure_windows_lan_firewall_rule(8080) is True
    assert len(commands) == 1, commands


def test_non_windows_creates_no_rule(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    called = []
    monkeypatch.setattr(
        lan_access.subprocess, "run", lambda *a, **k: called.append(a) or _ok()
    )
    assert lan_access.ensure_windows_lan_firewall_rule(8080) is False
    assert called == []


def test_ensure_all_uses_only_scoped_rule(monkeypatch):
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
    # No subprocess at all: the open-variant fallback is gone, so nothing
    # outside the scoped helper can shell out to New-NetFirewallRule.
    monkeypatch.setattr(
        lan_access.subprocess,
        "run",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not shell out")),
    )

    assert lan_access.ensure_all_lan_firewall_rules() is True
    assert seen == [8443, 8890, 8001], seen

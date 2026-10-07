"""The Windows LAN firewall helper only ever opens Cuttle to the local subnet."""

import re
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "enable_lan_firewall.ps1"


def _rule_calls(text):
    # New-NetFirewallRule invocations, joined across backtick continuations.
    joined = re.sub(r"`\s*\n\s*", " ", text)
    return [line for line in joined.splitlines() if "New-NetFirewallRule" in line]


def test_every_rule_is_local_subnet_and_private_only():
    calls = _rule_calls(SCRIPT.read_text(encoding="utf-8-sig"))
    assert calls, "script must create rules"
    for call in calls:
        assert "-RemoteAddress LocalSubnet" in call, call
        assert re.search(r"-Profile Private(\s|$)", call), call
        assert "Public" not in call and "'Any'" not in call and " Any" not in call, call


def test_old_open_rules_are_replaced_and_discovery_untouched():
    text = SCRIPT.read_text(encoding="utf-8-sig")
    assert "Remove-NetFirewallRule" in text and "'Cuttle LAN*'" in text
    assert "Network Discovery" not in text
    assert "Python311" not in text
    assert "CUTTLE_HTTPS_PORT" in text and "CUTTLE_PHONE_HTTPS_PORT" in text


def test_phone_isolation_test_is_scoped_and_cleans_up():
    text = (SCRIPT.parent / "diagnose_phone_connectivity.ps1").read_text(encoding="utf-8-sig")
    for call in _rule_calls(text):
        assert "-RemoteAddress LocalSubnet" in call and re.search(r"-Profile Private(\s|$)", call), call
    assert "user=Everyone" not in text
    body = text.split("finally {", 1)[1]
    assert "Remove-NetFirewallRule" in body and "delete urlacl" in body

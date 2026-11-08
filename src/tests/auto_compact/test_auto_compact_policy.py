"""Pure auto-compact policy (``api.auto_compact.policy``): settings, layering, decide."""

from __future__ import annotations

import pytest

from api.auto_compact import policy as pol


def snap(**over):
    base = {"supported": True, "percent": 75.0, "used_tokens": 150_000, "limit_tokens": 200_000,
            "token_source": "stream", "native_available": True, "fresh_available": True,
            "busy": False, "live_cli": False, "turns_since_compact": None, "turns_since_attempt": None,
            "fail_streak": 0, "ineffective": 0, "milestone": False}
    base.update(over)
    return base


def eff(managed=False, override=None, **settings):
    return pol.effective(settings, managed=managed, override=override)


def test_defaults_and_scopes():
    chats, managed = eff(), eff(managed=True)
    assert (chats["scope"], chats["percent"], chats["max_tokens"]) == ("chats", 70, 300_000)
    assert (managed["scope"], managed["percent"]) == ("managed", 60)
    assert chats["milestone"] and chats["milestone_percent"] == 40


def test_chat_override_wins_and_is_labelled():
    p = eff(override={"enabled": False, "percent": 50, "milestone": None})
    assert p["enabled"] is False and p["percent"] == 50 and p["milestone"] is True
    assert p["source"] == {"enabled": "chat", "percent": "chat", "milestone": "milestone"}


def test_bad_stored_values_fall_back_field_by_field():
    merged = pol.merge_settings({"chats": {"percent": 5, "max_tokens": 120_000}, "mode": "weird", "junk": 1})
    assert merged["chats"]["percent"] == 70 and merged["chats"]["max_tokens"] == 120_000
    assert merged["mode"] == "auto"


@pytest.mark.parametrize("patch", [
    {"chats": {"percent": 101}}, {"managed": {"enabled": "yes"}}, {"mode": "summarize"},
    {"quiet_seconds": -1}, {"chats": {"nope": 1}}, {"other": {}},
])
def test_validate_patch_rejects(patch):
    with pytest.raises(pol.PolicyError):
        pol.validate_patch(patch)


def test_validate_override():
    assert pol.validate_override({"enabled": True, "percent": None}) == {"enabled": True}
    with pytest.raises(pol.PolicyError):
        pol.validate_override({"max_tokens": 5})


def test_compacts_over_percent_or_token_cap():
    p = eff()
    assert pol.decide(p, snap())["compact"] is True
    below = pol.decide(p, snap(percent=40.0, used_tokens=80_000))
    assert below == {"compact": False, "reason": "below_threshold", "mode": None, "trigger": None}
    # A 1M window: 32% is far under the percentage but over the absolute cap.
    capped = pol.decide(p, snap(percent=32.0, used_tokens=320_000, limit_tokens=1_000_000))
    assert capped["compact"] and capped["trigger"] == "threshold"


def test_milestone_uses_lower_threshold_only_when_flagged():
    p = eff()
    s = snap(percent=45.0, used_tokens=90_000)
    assert pol.decide(p, s)["reason"] == "below_threshold"
    hit = pol.decide(p, dict(s, milestone=True))
    assert hit["compact"] and hit["trigger"] == "milestone"
    assert pol.decide(eff(override={"milestone": False}), dict(s, milestone=True))["compact"] is False


@pytest.mark.parametrize("over,reason", [
    ({"supported": False}, "unsupported"),
    ({"used_tokens": 0}, "no_reading"),
    ({"token_source": "aggregated"}, "no_reading"),
    ({"busy": True}, "busy"),
    ({"live_cli": True}, "live_cli"),
    ({"turns_since_compact": 0}, "just_compacted"),
    ({"fail_streak": pol.MAX_FAILURES}, "failing"),
    ({"fail_streak": 2, "turns_since_attempt": 1}, "backoff"),
])
def test_gates(over, reason):
    assert pol.decide(eff(), snap(**over))["reason"] == reason


def test_disabled():
    assert pol.decide(eff(override={"enabled": False}), snap())["reason"] == "disabled"


def test_backoff_doubles():
    assert [pol.retry_wait_turns(n) for n in range(6)] == [0, 1, 2, 4, 8, 8]
    assert pol.decide(eff(), snap(fail_streak=2, turns_since_attempt=2))["compact"] is True


def test_mode_selection():
    p = eff()
    assert pol.decide(p, snap())["mode"] == "native"
    # The summary left the window over the threshold: escalate to a fresh session.
    assert pol.decide(p, snap(ineffective=1))["mode"] == "fresh"
    assert pol.decide(p, snap(ineffective=1, fresh_available=False))["reason"] == "ineffective"
    # No headless compact (Antigravity): auto leaves the CLI to manage itself.
    assert pol.decide(p, snap(native_available=False))["reason"] == "no_method"
    assert pol.decide(eff(mode="fresh"), snap(native_available=False))["mode"] == "fresh"
    assert pol.decide(eff(mode="native"), snap(native_available=False))["reason"] == "no_method"

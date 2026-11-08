"""Claude Code: subscription first, Providers ANTHROPIC_API_KEY as the fallback."""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from api.agent_harness.agents.claude import api_fallback as fb

SESSION_LIMIT = "You've hit your session limit · resets 3pm (UTC)"


@pytest.fixture
def cfg(monkeypatch, tmp_path):
    import managers.settings_manager as sm

    settings = {"agent_billing_fallback": {"claude": {"enabled": True, "monthly_budget_usd": 200}}}

    class _SM:
        def get_setting(self, key, default=None):
            return settings.get(key, default)

    monkeypatch.setattr(sm, "get_settings_manager", lambda: _SM())
    monkeypatch.setattr("core.runtime_paths.data_db_dir", lambda: tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-test")
    monkeypatch.delenv("CUTTLE_CLAUDE_API_FALLBACK", raising=False)
    fb.clear_exhausted()
    yield settings
    fb.clear_exhausted()


# ── pure parsing ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    SESSION_LIMIT,
    "You've hit your weekly limit · resets Nov 10, 9am",
    "You’ve reached your Opus limit",
    "You're out of usage credits — buy more below to keep going.",
    "Claude AI usage limit reached|1900000000",
])
def test_detects_claude_subscription_limits(text):
    assert fb.is_subscription_limit(text)


@pytest.mark.parametrize("text", ["Invalid API key · Fix external API key", "Turn limit reached",
                                  "context limit reached", ""])
def test_ignores_other_failures(text):
    assert not fb.is_subscription_limit(text)


def test_reset_times():
    now = datetime(2026, 11, 8, 13, 0).timestamp()
    assert fb.reset_epoch("You've hit your session limit · resets 3pm", now) == datetime(2026, 11, 8, 15, 0).timestamp()
    # A time already past today means tomorrow.
    assert fb.reset_epoch("resets 9:30am", now) == datetime(2026, 11, 9, 9, 30).timestamp()
    assert fb.reset_epoch("weekly limit · resets Nov 10, 9am", now) == datetime(2026, 11, 10, 9, 0).timestamp()
    assert fb.reset_epoch("limit · resets in 2h 30m", now) == now + 9000
    assert fb.reset_epoch("Claude AI usage limit reached|1794180000", now) == 1794180000
    assert fb.reset_epoch("You've hit your limit", now) is None


def test_mark_exhausted_defaults_to_short_cooldown(cfg):
    now = 1_800_000_000.0
    assert fb.mark_exhausted("You've hit your limit", now) == now + fb.DEFAULT_COOLDOWN_S


# ── ledger, budget, Claude meter row ────────────────────────────────────────

def _sub(blocked=False):
    return {"agent": "claude", "label": "Claude Code", "plan": "Pro", "blocked": blocked, "unblock_at": 1 if blocked else None,
            "windows": [{"id": "weekly", "label": "Weekly", "used_percent": 100.0 if blocked else 40.0,
                         "remaining_percent": 0.0 if blocked else 60.0, "reset_at": 2_000_000_000}], "extras": {}}


def test_budget_and_api_credit_row(cfg):
    assert fb.available() == ""
    fb.record_spend(150.0, chat_session_id="1", model="opus")
    fb.record_spend(0, chat_session_id="1", model="opus")  # nothing billed: not a row
    snap = fb.merge_claude_usage(_sub())
    row = snap["windows"][-1]
    assert row["id"] == "api_credit" and row["used_percent"] == 75.0 and row["value_text"] == "$50.00 left"
    assert "$150.00 of $200 this month (1 turns)" in snap["extras"]["api_credit"]
    assert "primary_window" not in snap and not snap["blocked"]
    fb.record_spend(60.0, chat_session_id="2", model="opus")
    assert fb.available() == "over_budget"


def test_exhausted_subscription_is_not_blocked_while_the_key_covers_it(cfg):
    snap = fb.merge_claude_usage(_sub(blocked=True))
    assert snap["blocked"] is False and snap["primary_window"] == "api_credit"
    cfg["agent_billing_fallback"]["claude"]["enabled"] = False
    snap = fb.merge_claude_usage(_sub(blocked=True))
    assert snap["blocked"] is True and "fallback off" in snap["extras"]["api_credit"]


def test_off_by_default_and_kill_switch(cfg, monkeypatch):
    cfg.clear()
    assert fb.available() == "disabled"
    cfg["agent_billing_fallback"] = {"claude": {"enabled": True}}
    monkeypatch.setenv("CUTTLE_CLAUDE_API_FALLBACK", "0")
    assert fb.available() == "disabled"


def test_claude_meter_api_credit_toggle(cfg, monkeypatch):
    from api.gizmos import catalog, usage

    meter = catalog.get("usage_meter")
    on = meter.normalize_config({"agent": "claude", "api_credit": True})
    assert on["api_credit"] is True
    assert "api_credit" not in meter.normalize_config({"agent": "codex", "api_credit": True})
    assert "api_credit" not in meter.normalize_config({"api_credit": False}, on)
    usage.set_snapshot("claude", _sub())
    assert meter.resolve_data(on, False)["windows"][-1]["id"] == "api_credit"
    assert all(w["id"] != "api_credit" for w in meter.resolve_data({"agent": "claude"}, False)["windows"])
    assert "claude_api" not in [p["agent"] for p in usage.providers()]


# ── adapter flow ─────────────────────────────────────────────────────────────

def _run_adapter(monkeypatch, tmp_path, replies):
    import scripts.utilities.claude_cli_tool as mod
    from api.agent_harness.agents.claude.adapter import Adapter

    calls = []

    async def _fake(self, prompt, **kw):
        calls.append({"prompt": prompt, "resume": kw.get("resume"), "env": kw.get("extra_env")})
        return replies[len(calls) - 1]

    monkeypatch.setattr(mod.ClaudeCliTool, "execute_prompt", _fake)
    monkeypatch.setattr("api.agent_harness.steer.steer_enabled", lambda _a: False)
    out = asyncio.run(Adapter().execute("fix it", cwd=str(tmp_path), resume="sess-1", model=None,
                                        chat_session_id="7001"))
    return out, calls


def _limit(tokens=0):
    usage = {"completion_tokens": tokens} if tokens else {}
    return {"success": False, "error": SESSION_LIMIT, "output": SESSION_LIMIT, "usage": usage,
            "claude_session_id": "sess-1"}


def _ok(cost=0.5):
    return {"success": True, "output": "done", "usage": {"cost": cost}, "claude_session_id": "sess-1"}


def test_limit_reruns_on_api_key_and_records_spend(cfg, monkeypatch, tmp_path):
    out, calls = _run_adapter(monkeypatch, tmp_path, [_limit(), _ok(0.5)])
    assert out.success
    assert calls[0]["env"] is None
    assert calls[1] == {"prompt": "fix it", "resume": "sess-1", "env": {"ANTHROPIC_API_KEY": "sk-ant-api03-test"}}
    assert out.meta["billing"]["source"] == "anthropic_api"
    assert "Billed to Claude API credit: $0.50" in out.output
    assert fb.month_spend()["usd"] == 0.5
    # Until the reset, the next turn goes straight to the key (no probe).
    out2, calls2 = _run_adapter(monkeypatch, tmp_path, [_ok(0.25)])
    assert len(calls2) == 1 and calls2[0]["env"] == {"ANTHROPIC_API_KEY": "sk-ant-api03-test"}
    assert fb.month_spend()["usd"] == 0.75


def test_mid_turn_limit_continues_on_the_resumed_transcript(cfg, monkeypatch, tmp_path):
    out, calls = _run_adapter(monkeypatch, tmp_path, [_limit(tokens=400), _ok()])
    assert out.success and calls[1]["resume"] == "sess-1"
    assert "cut off by a usage limit" in calls[1]["prompt"]


def test_no_fallback_when_disabled_or_over_budget(cfg, monkeypatch, tmp_path):
    cfg["agent_billing_fallback"]["claude"]["enabled"] = False
    out, calls = _run_adapter(monkeypatch, tmp_path, [_limit()])
    assert not out.success and len(calls) == 1 and "billing" not in out.meta

    cfg["agent_billing_fallback"]["claude"]["enabled"] = True
    fb.clear_exhausted()
    fb.record_spend(200.0, chat_session_id="x", model="opus")
    out, calls = _run_adapter(monkeypatch, tmp_path, [_limit()])
    assert len(calls) == 1 and "monthly budget is spent" in out.output


def test_ordinary_failures_never_fall_back(cfg, monkeypatch, tmp_path):
    out, calls = _run_adapter(monkeypatch, tmp_path, [{"success": False, "error": "Turn limit reached",
                                                       "output": "", "usage": {}}])
    assert len(calls) == 1 and not out.success


def test_router_treats_claude_limits_as_quota():
    from api.agent_router.policy import is_quota_failure

    assert is_quota_failure(SESSION_LIMIT)
    assert is_quota_failure("You've hit your weekly limit · resets Nov 10, 9am")

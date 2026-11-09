"""Claude Code billing order: subscription first (default) or API first.

Settings default → per-chat pin → one message (`/use-api` / `/use-subscription`).
"""

from __future__ import annotations

import asyncio

import pytest

from api.agent_harness.agents.claude import api_fallback as fb

KEY_ENV = {"ANTHROPIC_API_KEY": "sk-ant-api03-test"}


@pytest.fixture
def cfg(monkeypatch, tmp_path):
    import managers.settings_manager as sm

    settings = {"agent_billing_fallback": {"claude": {"enabled": False, "monthly_budget_usd": 200}}}

    class _SM:
        def get_setting(self, key, default=None):
            return settings.get(key, default)

        def set_setting(self, key, value):
            settings[key] = value
            return True

    monkeypatch.setattr(sm, "get_settings_manager", lambda: _SM())
    monkeypatch.setattr("core.runtime_paths.data_db_dir", lambda: tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY_ENV["ANTHROPIC_API_KEY"])
    monkeypatch.delenv("CUTTLE_CLAUDE_API_FALLBACK", raising=False)
    pins = {}
    monkeypatch.setattr("scripts.utilities.claude_cli_session_store.load_claude_billing",
                        lambda sid: pins.get(str(sid)))
    fb.clear_exhausted()
    yield settings, pins
    fb.clear_exhausted()


# ── pure ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,mode,rest", [
    ("/use-api fix it", "api", "fix it"),
    ("  /USE-SUBSCRIPTION\nfix it", "subscription", "fix it"),
    ("/use-api", None, "/use-api"),             # bare token is not a turn
    ("/use-apis fix", None, "/use-apis fix"),
    ("please /use-api", None, "please /use-api"),
])
def test_parse_billing_prefix(text, mode, rest):
    assert fb.parse_billing_prefix(text) == (mode, rest)


def test_resolve_order_message_beats_chat_beats_default(cfg):
    settings, pins = cfg
    assert fb.resolve_billing("1") == ("subscription", "default")
    settings["agent_billing_fallback"]["claude"]["prefer"] = "api"
    assert fb.resolve_billing("1") == ("api", "default")
    pins["1"] = "subscription"
    assert fb.resolve_billing("1") == ("subscription", "chat")
    assert fb.resolve_billing("1", "api") == ("api", "message")


def test_save_settings_validates_and_merges(cfg):
    settings, _ = cfg
    view = fb.save_settings({"prefer": "api", "fallback_enabled": True, "monthly_budget_usd": "50"})
    assert view["prefer"] == "api" and view["fallback_enabled"] and view["monthly_budget_usd"] == 50
    assert settings["agent_billing_fallback"]["claude"] == {"enabled": True, "monthly_budget_usd": 50.0, "prefer": "api"}
    assert fb.save_settings({"monthly_budget_usd": ""})["monthly_budget_usd"] is None
    for bad in ({"prefer": "cash"}, {"monthly_budget_usd": "lots"}, {"monthly_budget_usd": -1}):
        with pytest.raises(ValueError):
            fb.save_settings(bad)


def test_api_first_needs_key_and_budget_but_not_the_fallback_toggle(cfg, monkeypatch):
    assert fb.available() == "disabled" and fb.api_ready() == ""
    fb.record_spend(200.0, chat_session_id="x", model="opus")
    assert fb.api_ready() == "over_budget"
    monkeypatch.setenv("CUTTLE_CLAUDE_API_FALLBACK", "0")
    assert fb.api_ready() == "disabled"


# ── adapter ─────────────────────────────────────────────────────────────────

def _run(monkeypatch, tmp_path, replies, prompt="fix it", **kw):
    import scripts.utilities.claude_cli_tool as mod
    from api.agent_harness.agents.claude.adapter import Adapter

    calls = []

    async def _fake(self, text, **k):
        calls.append({"prompt": text, "env": k.get("extra_env")})
        return replies[len(calls) - 1]

    monkeypatch.setattr(mod.ClaudeCliTool, "execute_prompt", _fake)
    monkeypatch.setattr("api.agent_harness.steer.steer_enabled", lambda _a: False)
    adapter = Adapter()
    text, opts = adapter.peel_turn_options(prompt)
    out = asyncio.run(adapter.execute(text, cwd=str(tmp_path), resume="sess-1", model=None,
                                      chat_session_id="7001", **{**opts, **kw}))
    return out, calls


def _ok(cost=0.5):
    return {"success": True, "output": "done", "usage": {"cost": cost}, "claude_session_id": "sess-1"}


def test_use_api_message_runs_on_the_key_and_strips_the_token(cfg, monkeypatch, tmp_path):
    out, calls = _run(monkeypatch, tmp_path, [_ok(0.5)], prompt="/use-api fix it")
    assert calls == [{"prompt": "fix it", "env": KEY_ENV}]
    assert out.meta["billing"]["order"] == "api" and out.meta["billing"]["order_source"] == "message"
    assert "Billed to Claude API credit: $0.50" in out.output
    assert fb.month_spend()["usd"] == 0.5


def test_chat_pin_api_first_and_use_subscription_overrides_it(cfg, monkeypatch, tmp_path):
    _, pins = cfg
    pins["7001"] = "api"
    out, calls = _run(monkeypatch, tmp_path, [_ok()])
    assert calls[0]["env"] == KEY_ENV and out.meta["billing"]["order_source"] == "chat"
    out, calls = _run(monkeypatch, tmp_path, [_ok()], prompt="/use-subscription fix it")
    assert calls == [{"prompt": "fix it", "env": None}] and "billing" not in out.meta


def test_api_first_without_key_runs_on_the_subscription(cfg, monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.setattr(fb, "api_key", lambda: "")
    out, calls = _run(monkeypatch, tmp_path, [_ok()], billing="api")
    assert calls[0]["env"] is None and "no Anthropic key" in out.output
    assert "billing" not in out.meta


def test_api_refusal_reruns_on_the_subscription(cfg, monkeypatch, tmp_path):
    refused = {"success": False, "error": "Credit balance is too low", "output": "", "usage": {}}
    out, calls = _run(monkeypatch, tmp_path, [refused, _ok()], billing="api")
    assert [c["env"] for c in calls] == [KEY_ENV, None]
    assert out.success and "refused this turn" in out.output and "billing" not in out.meta


# ── HTTP pins ───────────────────────────────────────────────────────────────

def test_first_turn_pins_save_billing(monkeypatch):
    from api.agent_harness.agents.claude import routes

    saved = {}
    monkeypatch.setattr(routes, "save_claude_model", lambda s, v: saved.__setitem__("model", v))
    monkeypatch.setattr(routes, "save_claude_effort", lambda s, v: saved.__setitem__("effort", v))
    monkeypatch.setattr(routes, "save_claude_billing", lambda s, v: saved.__setitem__("billing", v))
    routes.apply_request_pins("9", {"model": "opus", "effort": "default", "billing": "api"})
    assert saved == {"model": "opus", "billing": "api"}
    saved.clear()
    routes.apply_request_pins("9", {"billing": "cash"})
    assert saved == {}


def test_billing_pin_round_trip(tmp_path, monkeypatch):
    from scripts.utilities import claude_cli_session_store as store

    monkeypatch.setattr(store, "_map_file", lambda: tmp_path / "map.json")
    assert store.load_claude_billing("5") is None
    assert store.save_claude_billing("5", "api") == "api"
    assert store.load_claude_billing("5") == "api"
    assert store.save_claude_billing("5", "default") is None
    assert store.load_claude_billing("5") is None

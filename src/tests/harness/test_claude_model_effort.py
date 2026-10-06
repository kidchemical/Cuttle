"""Claude Code model catalog, per-chat model/effort pins, and ``--effort`` argv."""

from __future__ import annotations

import asyncio
import json
import subprocess

import pytest
from flask import Flask

from api.agent_harness.agents.claude import adapter as claude_adapter
from api.agent_harness.agents.claude import model_catalog as cat
from scripts.utilities import claude_cli_session_store as store

_INIT_MODELS = [
    {"value": "default", "resolvedModel": "claude-opus-5-5", "displayName": "Default (recommended)",
     "supportsEffort": True, "supportedEffortLevels": ["low", "medium", "high", "xhigh", "max"]},
    {"value": "opus", "resolvedModel": "claude-opus-5-5", "displayName": "Opus 5.5",
     "description": "For complex work", "supportsEffort": True,
     "supportedEffortLevels": ["max", "low", "high", "medium", "xhigh"]},
    {"value": "haiku", "resolvedModel": "claude-haiku-4-5", "displayName": "Haiku 4.5"},
    {"value": "claude-sonnet-4-6", "displayName": "Sonnet 4.6", "supportsEffort": True,
     "supportedEffortLevels": ["low", "medium", "high", "max"]},
]


def _init_stdout(models):
    return "\n".join([
        json.dumps({"type": "system", "subtype": "init"}),
        json.dumps({"type": "control_response", "response": {
            "subtype": "success", "request_id": "cuttle-models",
            "response": {"models": models, "commands": []},
        }}),
    ])


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "_map_file", lambda: tmp_path / "claude_session_map.json")
    monkeypatch.setattr("scripts.utilities.claude_cli_tool.claude_executable", lambda: "/fake/claude")
    import api.agent_harness.agent_defaults as ad
    monkeypatch.setattr(ad, "get_starred_model", lambda aid: None)
    monkeypatch.setattr(ad, "get_starred_effort", lambda aid: None)
    cat.clear_claude_catalog_cache()
    yield
    cat.clear_claude_catalog_cache()


@pytest.fixture
def cli_models(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout=_init_stdout(_INIT_MODELS), stderr="")

    monkeypatch.setattr(cat.subprocess, "run", fake_run)
    return calls


def test_catalog_reads_initialize_without_a_user_turn(cli_models):
    result = cat.list_claude_catalog_models()
    assert result["source"] == "cli"
    assert [m["id"] for m in result["models"]] == ["opus", "haiku", "claude-sonnet-4-6"]
    assert result["models"][0]["efforts"] == ["low", "medium", "high", "xhigh", "max"]
    assert result["models"][1]["efforts"] == []
    assert result["default_efforts"] == ["low", "medium", "high", "xhigh", "max"]
    argv, kwargs = cli_models[0]
    assert "--input-format" in argv and "stream-json" in argv
    request = json.loads(kwargs["input"])
    assert request["request"] == {"subtype": "initialize"}
    # Cached: a second load does not spawn the CLI again; refresh does.
    cat.list_claude_catalog_models()
    assert len(cli_models) == 1
    cat.refresh_claude_catalog()
    assert len(cli_models) == 2


def test_catalog_falls_back_to_manifest_without_leaking_stderr(monkeypatch):
    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="token sk-secret expired")

    monkeypatch.setattr(cat.subprocess, "run", fake_run)
    result = cat.list_claude_catalog_models()
    assert result["source"] == "static_fallback"
    assert "opus" in [m["id"] for m in result["models"]]
    assert "sk-secret" not in str(result["error"])


def test_efforts_match_alias_and_resolved_model(cli_models):
    assert cat.claude_efforts_for_model("haiku") == []
    assert cat.claude_efforts_for_model("claude-sonnet-4-6") == ["low", "medium", "high", "max"]
    assert cat.claude_efforts_for_model("claude-opus-5-5") == ["low", "medium", "high", "xhigh", "max"]
    assert cat.claude_efforts_for_model(None) == ["low", "medium", "high", "xhigh", "max"]
    assert cat.claude_model_label("claude-opus-5-5") == "Opus 5.5"


def test_model_refresh_list_pin_and_reset(cli_models):
    a = claude_adapter.build_adapter()
    out = a.handle_meta("/model refresh", cwd=".", chat_session_id="7")
    assert out.success and "3 models" in out.output
    out = a.handle_meta("model", cwd=".", chat_session_id="7")
    assert "`claude-sonnet-4-6`" in out.output and "`xhigh`" in out.output
    out = a.handle_meta("model opus", cwd=".", chat_session_id="7")
    assert out.model == "opus" and store.load_claude_model("7") == "opus"
    out = a.handle_meta("model default", cwd=".", chat_session_id="7")
    assert store.load_claude_model("7") is None
    # A task that merely starts with "model" is not a pin.
    assert a.handle_meta("model the login flow", cwd=".", chat_session_id="7") is None


def test_effort_pin_validates_against_the_pinned_model(cli_models):
    a = claude_adapter.build_adapter()
    out = a.handle_meta("effort xhigh", cwd=".", chat_session_id="8")
    assert store.load_claude_effort("8") == "xhigh"
    assert out.meta["agent_effort"] == "xhigh"
    store.save_claude_model("8", "claude-sonnet-4-6")
    store.save_claude_effort("8", None)
    out = a.handle_meta("/effort xhigh", cwd=".", chat_session_id="8")
    assert "not supported" in out.output and store.load_claude_effort("8") is None
    assert a.handle_meta("effort estimate for the migration", cwd=".", chat_session_id="8") is None


def test_execute_passes_effort_flag_and_refuses_unsupported(cli_models, monkeypatch):
    seen = {}

    class FakeTool:
        def __init__(self, model=None, reasoning_effort=None):
            seen.update(model=model, effort=reasoning_effort)

        async def execute_prompt(self, prompt, **kw):
            return {"success": True, "output": "ok", "usage": {}}

    monkeypatch.setattr(claude_adapter, "ClaudeCliTool", FakeTool)
    a = claude_adapter.build_adapter()
    store.save_claude_effort("9", "max")
    res = asyncio.run(a.execute("hi", cwd=".", resume=None, model=None, chat_session_id="9"))
    assert res.success and seen == {"model": None, "effort": "max"}
    assert res.meta["agent_effort"] == "max"

    seen.clear()
    store.save_claude_model("9", "haiku")
    res = asyncio.run(a.execute("hi", cwd=".", resume=None, model=None, chat_session_id="9"))
    assert "was not started" in res.output and not seen


def test_cli_tool_maps_effort_to_flag(monkeypatch):
    from scripts.utilities import claude_cli_tool as tool_mod

    argv_seen = []

    async def fake_exec(*argv, **kwargs):
        argv_seen.extend(argv)
        raise OSError("stop before spawning")

    monkeypatch.setattr(tool_mod.asyncio, "create_subprocess_exec", fake_exec)
    tool = tool_mod.ClaudeCliTool(model="opus", reasoning_effort="HIGH")
    asyncio.run(tool.execute_prompt("hi", cwd="."))
    i = argv_seen.index("--effort")
    assert argv_seen[i + 1] == "high"
    assert argv_seen[argv_seen.index("--model") + 1] == "opus"


@pytest.fixture
def client(monkeypatch, cli_models):
    from api.agent_harness.agents.claude import routes
    import api.http_authz as authz

    monkeypatch.setattr(authz, "current_user", lambda: {"id": 1, "username": "t"})
    monkeypatch.setattr(routes, "require_session_actor", lambda sid: ({"id": 1}, sid, None))
    app = Flask(__name__)
    app.register_blueprint(routes.claude_bp)
    return app.test_client()


def test_routes_round_trip_model_and_effort(client):
    body = client.get("/api/claude/models?session=11").get_json()
    assert body["success"] and body["source"] == "cli"
    assert body["commonEfforts"] == ["low", "medium", "high", "xhigh", "max"]
    assert client.post("/api/claude/model", json={"session": "11", "model": "claude-sonnet-4-6"}).get_json()["success"]
    levels = client.get("/api/claude/effort?session=11").get_json()["levels"]
    assert [lv["id"] for lv in levels] == ["low", "medium", "high", "max"]
    bad = client.post("/api/claude/effort", json={"session": "11", "effort": "xhigh"})
    assert bad.status_code == 400
    ok = client.post("/api/claude/effort", json={"session": "11", "effort": "max"}).get_json()
    assert ok["preferredEffort"] == "max" and store.load_claude_effort("11") == "max"
    current = [m for m in client.get("/api/claude/models?session=11").get_json()["models"] if m["current"]]
    assert [m["id"] for m in current] == ["claude-sonnet-4-6"]


def test_user_badge_snapshots_claude_model_and_effort(cli_models):
    from api.chat_metadata import user_badge_metadata

    store.save_claude_model("12", "opus")
    store.save_claude_effort("12", "high")
    # Cold process: the send path never spawns the CLI just for a label.
    chip = user_badge_metadata("/claude fix it", "12")["slash_command"]["chips"][0]
    assert chip["label"] == "Claude Code - Opus · high"
    assert chip["meta"] == "/claude · model opus · effort high"
    assert chip["category"] == "claude"
    assert not cli_models
    # Once the palette has loaded the live catalog, its display name wins.
    cat.list_claude_catalog_models()
    chip = user_badge_metadata("/claude fix it", "12")["slash_command"]["chips"][0]
    assert chip["label"] == "Claude Code - Opus 5.5 · high"

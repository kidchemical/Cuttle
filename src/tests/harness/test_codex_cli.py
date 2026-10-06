"""Codex CLI slash command — JSONL parse + session resume map."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scripts.utilities.codex_cli_tool import _parse_codex_jsonl, usage_for_query_report
from scripts.utilities import codex_cli_session_store as store


SAMPLE_JSONL = """
{"type":"thread.started","thread_id":"01a006a4-6f5e-7750-a333-27261e4ebdce"}
{"type":"turn.started"}
{"type":"item.completed","item":{"id":"item_3","type":"agent_message","text":"Repo looks healthy."}}
{"type":"turn.completed","usage":{"input_tokens":100,"output_tokens":12,"reasoning_output_tokens":0}}
""".strip()


@pytest.fixture(autouse=True)
def _fresh_codex_catalog_cache():
    """Fake catalogs cached here must not leak into later files' effort checks."""
    from api.agent_harness.agents.codex import model_catalog as mc

    mc.clear_codex_catalog_cache()
    yield
    mc.clear_codex_catalog_cache()


def test_parse_codex_jsonl_extracts_thread_message_usage():
    parsed = _parse_codex_jsonl(SAMPLE_JSONL)
    assert parsed["thread_id"] == "01a006a4-6f5e-7750-a333-27261e4ebdce"
    assert parsed["output"] == "Repo looks healthy."
    assert parsed["usage"]["input_tokens"] == 100
    assert parsed["usage"]["output_tokens"] == 12
    assert parsed["errors"] == []


def test_parse_codex_jsonl_turn_failed():
    raw = (
        '{"type":"thread.started","thread_id":"aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"}\n'
        '{"type":"turn.failed","error":{"message":"401 Unauthorized"}}\n'
    )
    parsed = _parse_codex_jsonl(raw)
    assert parsed["thread_id"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert "401" in parsed["errors"][0]


def test_usage_for_query_report():
    u = usage_for_query_report(
        {
            "input_tokens": 10,
            "output_tokens": 5,
            "cached_input_tokens": 8,
        },
        "gpt-5",
    )
    assert u["input_tokens"] == 10
    assert u["output_tokens"] == 5
    assert u["total_tokens"] == 15
    assert u["model"] == "gpt-5"
    assert u["cache_read_tokens"] == 8


def test_parse_codex_jsonl_preserves_cached_input():
    raw = (
        '{"type":"thread.started","thread_id":"aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"}\n'
        '{"type":"item.completed","item":{"id":"i1","type":"agent_message","text":"ok"}}\n'
        '{"type":"turn.completed","usage":{"input_tokens":1000,"cached_input_tokens":800,'
        '"output_tokens":12,"reasoning_output_tokens":0}}\n'
    )
    parsed = _parse_codex_jsonl(raw)
    assert parsed["usage"]["input_tokens"] == 1000
    assert parsed["usage"]["cached_input_tokens"] == 800
    uq = usage_for_query_report(parsed["usage"], "gpt-5.6-sol")
    assert uq["cache_read_tokens"] == 800


def test_codex_session_store_roundtrip(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(store, "_repo_root", lambda: tmp_path)
    cwd = str(tmp_path / "proj")
    Path(cwd).mkdir()
    sid = "db_session_42"
    assert store.load_codex_resume_id(cwd, sid) is None
    store.save_codex_resume_id(cwd, sid, "01a006a4-6f5e-7750-a333-27261e4ebdce")
    assert store.load_codex_resume_id(cwd, sid) == "01a006a4-6f5e-7750-a333-27261e4ebdce"
    store.clear_codex_resume_id(cwd, sid)
    assert store.load_codex_resume_id(cwd, sid) is None


def test_codex_session_store_rejects_garbage(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(store, "_repo_root", lambda: tmp_path)
    cwd = str(tmp_path / "proj")
    Path(cwd).mkdir()
    store.save_codex_resume_id(cwd, "s1", "not-a-uuid")
    assert store.load_codex_resume_id(cwd, "s1") is None
    store.save_codex_resume_id(cwd, "s1", "th_abc123")
    assert store.load_codex_resume_id(cwd, "s1") == "th_abc123"


@pytest.mark.asyncio
async def test_execute_prompt_resume_argv(tmp_path: Path):
    from scripts.utilities.codex_cli_tool import CodexCliTool

    cwd = tmp_path / "ws"
    cwd.mkdir()
    captured = {}

    class FakeProc:
        returncode = 0
        stdout = None
        stderr = None
        stdin = None

        def __init__(self):
            self._lines = [
                b'{"type":"thread.started","thread_id":"aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"}\n',
                b'{"type":"item.completed","item":{"id":"i1","type":"agent_message","text":"hi"}}\n',
                b'{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}\n',
            ]
            self._i = 0

        async def wait(self):
            return 0

    fake = FakeProc()

    async def fake_readline():
        if fake._i >= len(fake._lines):
            return b""
        line = fake._lines[fake._i]
        fake._i += 1
        return line

    class Out:
        async def readline(self):
            return await fake_readline()

    class Err:
        async def read(self):
            return b""

    fake.stdout = Out()
    fake.stderr = Err()

    async def fake_exec(*cmd, **kwargs):
        captured["cmd"] = list(cmd)
        captured["kwargs"] = kwargs
        return fake

    with patch("scripts.utilities.codex_cli_tool.codex_executable", return_value="codex.exe"), patch(
        "asyncio.create_subprocess_exec", side_effect=fake_exec
    ):
        tool = CodexCliTool()
        result = await tool.execute_prompt(
            "follow up",
            cwd=str(cwd),
            resume="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            timeout=30.0,
        )

    assert result["success"] is True
    assert result["output"] == "hi"
    assert result["codex_session_id"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    cmd = captured["cmd"]
    assert cmd[:4] == ["codex.exe", "exec", "resume", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"]
    assert "--json" in cmd
    assert "-C" not in cmd
    assert not any("unityMCP" in str(part) for part in cmd)


def test_codex_activity_maps_cursor_style_lines():
    from scripts.utilities.codex_cli_tool import _codex_activity_for_event

    state: dict = {"tool_count": 0}
    assert _codex_activity_for_event({"type": "turn.started"}, state) == (
        "Codex is thinking…"
    )
    assert (
        _codex_activity_for_event(
            {
                "type": "item.started",
                "item": {"type": "command_execution", "command": "ls -la src"},
            },
            state,
        )
        == "tool 1: ls -la src"
    )
    assert (
        _codex_activity_for_event(
            {
                "type": "item.started",
                "item": {
                    "type": "file_change",
                    "changes": [{"path": "src/foo.py"}, {"path": "src/bar.py"}],
                },
            },
            state,
        )
        == "tool 2: edit foo.py, bar.py"
    )
    assert (
        _codex_activity_for_event(
            {
                "type": "item.started",
                "item": {"type": "mcp_tool_call", "tool": "blender.get_scene_info"},
            },
            state,
        )
        == "tool 3: mcp blender.get_scene_info"
    )
    assert (
        _codex_activity_for_event(
            {
                "type": "item.started",
                "item": {
                    "type": "reasoning",
                    "text": "Inspect the logo animation first.",
                },
            },
            state,
        )
        == "thinking: Inspect the logo animation first.…"
    )
    assert (
        _codex_activity_for_event(
            {
                "type": "item.completed",
                "item": {
                    "type": "agent_message",
                    "text": "Here is the plan for the slime-mold logo.",
                },
            },
            state,
        )
        == "writing: Here is the plan for the slime-mold logo."
    )
    assert state["tool_count"] == 3


def test_codex_activity_transport_recovery_is_not_labeled_error():
    """CH-000504: long-thread WS reconnect/fallback must not look like a hard fail."""
    from scripts.utilities.codex_cli_tool import _codex_activity_for_event

    state: dict = {"tool_count": 0}
    reconnect = (
        "Reconnecting... 3/5 (stream disconnected before completion: "
        "websocket closed by server before response.completed)"
    )
    fallback = (
        "Falling back from WebSockets to HTTPS transport. "
        "stream disconnected before completion: websocket closed by server "
        "before response.completed"
    )
    out = _codex_activity_for_event({"type": "error", "message": reconnect}, state)
    assert out is not None and out.startswith("Codex reconnecting:")
    assert "Codex error:" not in out
    assert "tool failed:" not in out

    out2 = _codex_activity_for_event(
        {
            "type": "item.completed",
            "item": {"type": "error", "message": fallback},
        },
        state,
    )
    assert out2 is not None and out2.startswith("Codex reconnecting:")
    assert "tool failed:" not in out2

    # Real hard failures still use the error / tool-failed labels.
    assert (
        _codex_activity_for_event(
            {"type": "error", "message": "authentication required"},
            state,
        )
        == "Codex error: authentication required"
    )

    import queue

    from api.agent_harness.activity import ActivityEmitter

    q: queue.Queue = queue.Queue()
    em = ActivityEmitter(q, agent_label="Codex", heartbeat_sec=15.0, throttle_sec=1.2)
    assert em.emit("tool 1: ls") is True
    assert em.emit("tool 1: ls") is False  # duplicate suppressed
    assert em.emit("thinking: about vines…") is True
    msgs = []
    while not q.empty():
        kind, text = q.get_nowait()
        assert kind == "status"
        msgs.append(text)
    assert msgs == ["tool 1: ls", "thinking: about vines…"]
    assert em.last_activity.startswith("thinking:")


def test_starred_slash_allows_codex():
    from api.starred_slash import normalize_sticky_prefix, sticky_prefix_from_text

    assert normalize_sticky_prefix("/codex") == "/codex "
    assert sticky_prefix_from_text("/codex fix it") == "/codex "


def test_codex_model_session_pins(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(store, "_repo_root", lambda: tmp_path)
    assert store.load_codex_model("42") is None
    assert store.save_codex_model("42", "gpt-5.6-sol") == "gpt-5.6-sol"
    assert store.load_codex_model("42") == "gpt-5.6-sol"
    assert store.save_codex_effort("42", "high") == "high"
    assert store.load_codex_effort("42") == "high"
    assert store.save_codex_model("42", "") is None
    assert store.load_codex_model("42") is None


def test_codex_catalog_static_fallback(monkeypatch):
    from api.agent_harness.agents.codex import model_catalog as mc

    mc.clear_codex_catalog_cache()
    monkeypatch.setattr(mc, "_run_codex_debug_models", lambda *, refresh=False: ([], "CLI missing"))
    out = mc.list_codex_catalog_models(refresh=True)
    assert out["source"] == "static_fallback"
    assert out["count"] >= 5
    assert {m["id"] for m in out["models"]} >= {"gpt-5.6-sol", "gpt-5.5"}


def test_codex_catalog_from_cli(monkeypatch):
    from api.agent_harness.agents.codex import model_catalog as mc

    mc.clear_codex_catalog_cache()
    monkeypatch.setattr(
        mc,
        "_run_codex_debug_models",
        lambda *, refresh=False: (
            [
                {"id": "gpt-5.6-sol", "label": "GPT-5.6-Sol", "description": "frontier"},
                {"id": "gpt-5.6-luna", "label": "GPT-5.6-Luna", "description": "luna"},
            ],
            None,
        ),
    )
    out = mc.list_codex_catalog_models(refresh=True)
    assert out["source"] == "cli_refresh"
    assert out["count"] == 2
    assert out["models"][0]["id"] == "gpt-5.6-sol"


def test_codex_efforts_for_cli_only_model_after_cache_expiry(monkeypatch):
    """A model newer than the manifest must not be refused once the cache expires.

    CH-000840: a `gpt-6.1-sol · low` pin was refused ("no verified effort
    list") whenever the 120s live catalog had lapsed, because only the
    manifest snapshot was consulted.
    """
    from api.agent_harness.agents.codex import model_catalog as mc

    calls = []

    def fake_debug_models(*, refresh=False):
        calls.append(refresh)
        return (
            [{"id": "gpt-9-future", "label": "GPT-9-Future", "efforts": ["low", "high"]}],
            None,
        )

    monkeypatch.setattr(mc, "_run_codex_debug_models", fake_debug_models)
    assert mc.codex_efforts_for_model("gpt-9-future") == ["low", "high"]
    assert calls == [False]  # bundled catalog, not a remote refresh

    # Manifest models stay CLI-free when nothing is cached.
    mc.clear_codex_catalog_cache()
    calls.clear()
    assert "low" in mc.codex_efforts_for_model("gpt-5.5")
    assert calls == []

    # Ids the CLI does not list stay empty (guard still refuses them).
    mc.clear_codex_catalog_cache()
    assert mc.codex_efforts_for_model("not-a-model") == []


def test_codex_model_slash_refresh(monkeypatch):
    from api.agent_harness.agents.codex.adapter import _handle_codex_model_slash

    monkeypatch.setattr(
        "api.agent_harness.agents.codex.adapter.refresh_codex_catalog",
        lambda: {"count": 8, "source": "cli_refresh", "error": None, "models": []},
    )
    result = _handle_codex_model_slash("model refresh", "99", "gpt-5.6-sol")
    assert result is not None
    assert result.success is True
    assert "8" in result.output
    assert "palette" in result.output.lower()


def test_codex_model_palette_wired_into_chat_page():
    js = Path(__file__).resolve().parents[2] / "web" / "js" / "chat/chat_page.js"
    text = js.read_text(encoding="utf-8")
    assert "function buildCodexModelPaletteItems" in text
    assert ".concat(codexModels)" in text
    assert "cmd.category === 'codex-model'" in text
    assert "persistCodexModelSelection" in text
    assert "'/api/codex/model'" in text or '"/api/codex/model"' in text
    # Staged `/model refresh` under Codex sticky (no instant codexRefresh).
    assert "prefix: '/model refresh'" in text
    assert "category: 'codex-cmd'" in text
    assert "codexRefresh: true" not in text
    assert "params.set('refresh', '1')" in text
    assert "category: 'codex'" in text


def test_codex_models_endpoints(tmp_path: Path, monkeypatch):
    from api import web_chat_api as w
    from api.agent_harness.agents.codex import model_catalog as mc

    monkeypatch.setattr(store, "_repo_root", lambda: tmp_path)
    mc.clear_codex_catalog_cache()
    monkeypatch.setattr(
        mc,
        "_run_codex_debug_models",
        lambda *, refresh=False: (
            [{"id": "gpt-5.6-sol", "label": "GPT-5.6-Sol", "description": ""}],
            None,
        ),
    )
    monkeypatch.setattr(
        "api.agent_harness.agent_defaults.get_starred_model",
        lambda _aid: None,
    )

    with w.app.test_client() as client:
        denied = client.post(
            "/api/codex/model", json={"session": "api-sess", "model": "gpt-5.6-luna"}
        )
        assert denied.status_code == 401  # pins require a signed-in caller

        monkeypatch.setattr(
            w, "_require_session_actor",
            lambda session_id: ({"id": 1}, session_id, None),
        )
        listed = client.get("/api/codex/models?session=api-sess").get_json()
        assert listed["success"] is True
        assert {m["id"] for m in listed["models"]} >= {"gpt-5.6-sol"}

        saved = client.post(
            "/api/codex/model", json={"session": "api-sess", "model": "gpt-5.6-luna"}
        ).get_json()
        assert saved["success"] is True
        assert saved["preferredModel"] == "gpt-5.6-luna"

        again = client.get("/api/codex/models?session=api-sess").get_json()
        assert again["preferredModel"] == "gpt-5.6-luna"

        refreshed = client.get("/api/codex/models?refresh=1&session=api-sess").get_json()
        assert refreshed["success"] is True
        assert refreshed.get("source") in ("cli_refresh", "static_fallback", "cli_bundled")

        missing = client.post("/api/codex/model", json={"model": "gpt-5.6-sol"})
        assert missing.status_code == 400

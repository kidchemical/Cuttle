"""Offline tests for Antigravity ``agy acp`` turns and mid-turn steering."""

from __future__ import annotations

import asyncio
import json
import os
import queue
import stat
import sys
import threading
import time

import pytest

from api.agent_harness import steer
from api.agent_harness.agents.antigravity import adapter as agy
from api.agent_harness.agents.antigravity import model_catalog as mc
from api.agent_harness.agents.antigravity import session_store as store

AGY_MODELS = (
    "gemini-4-neon-high\tGemini 4 Neon (High)\n"
    "gemini-3.8-flash-low\tGemini 3.8 Flash (Low)\n"
    "gemini-3.8-flash-high\tGemini 3.8 Flash (High)\n"
)


@pytest.fixture
def agy_acp_env(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_map_file", lambda: tmp_path / "agy_map.json")
    conversations = tmp_path / "agy-conversations"
    conversations.mkdir()
    monkeypatch.setenv("CUTTLE_AGY_CONVERSATIONS_DIR", str(conversations))
    monkeypatch.setattr(mc, "_run_agy_models", lambda: (mc.parse_agy_models(AGY_MODELS), None))
    mc.clear_antigravity_catalog_cache()
    monkeypatch.setattr(steer, "steer_enabled", lambda agent: agent == "antigravity")

    async def _signed_in(*_a):
        return None

    monkeypatch.setattr(agy, "_authentication_preflight", _signed_in)
    yield conversations
    mc.clear_antigravity_catalog_cache()


def _write_fake_acp_agy(tmp_path, conversations, *, mode: str = "normal"):
    log = tmp_path / "acp_calls.jsonl"
    script = tmp_path / "agy"
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys, time\n"
        f"MODE = {mode!r}\n"
        f"LOG = {str(log)!r}\n"
        f"CONVS = {str(conversations)!r}\n"
        "argv = sys.argv[1:]\n"
        "def record(obj): open(LOG, 'a', encoding='utf-8').write(json.dumps(obj) + '\\n')\n"
        "def emit(obj): print(json.dumps(obj), flush=True)\n"
        "if argv and argv[0] == '-p':\n"
        "    stdin = sys.stdin.read()\n"
        "    record({'mode': 'print', 'argv': argv, 'stdin': stdin})\n"
        "    cid = 'conv-print-1'\n"
        "    open(os.path.join(CONVS, cid + '.db'), 'w').close()\n"
        "    emit({'event': 'init', 'conversation_id': cid, 'init': {'model': 'gemini-3.8-flash'}})\n"
        "    emit({'event': 'result', 'result': {'conversation_id': cid, 'status': 'SUCCESS', 'response': 'fallback pong'}})\n"
        "    sys.exit(0)\n"
        "if MODE == 'fail_init':\n"
        "    for line in sys.stdin:\n"
        "        msg = json.loads(line)\n"
        "        record({'mode': 'acp', 'msg': msg})\n"
        "        emit({'jsonrpc': '2.0', 'id': msg['id'], 'error': {'code': -32000, 'message': 'acp disabled'}})\n"
        "        sys.exit(1)\n"
        "sid = 'acp-conv-1'\n"
        "active_prompt_id = None\n"
        "for line in sys.stdin:\n"
        "    line = line.strip()\n"
        "    if not line:\n"
        "        continue\n"
        "    msg = json.loads(line)\n"
        "    record({'mode': 'acp', 'msg': msg})\n"
        "    method = msg.get('method')\n"
        "    mid = msg.get('id')\n"
        "    params = msg.get('params') or {}\n"
        "    if method == 'initialize':\n"
        "        emit({'jsonrpc': '2.0', 'id': mid, 'result': {'protocolVersion': 1, 'agentCapabilities': {'sessionCapabilities': {'resume': {}}}}})\n"
        "    elif method in ('session/new', 'session/resume'):\n"
        "        sid = params.get('sessionId') or sid\n"
        "        open(os.path.join(CONVS, sid + '.db'), 'w').close()\n"
        "        emit({'jsonrpc': '2.0', 'id': mid, 'result': {\n"
        "            'sessionId': sid,\n"
        "            'models': {'currentModelId': 'gemini-3.8-flash-low'},\n"
        "            'configOptions': [\n"
        "                {'id': 'model', 'options': [{'value': 'gemini-3.8-flash'}, {'value': 'gemini-4-neon'}]},\n"
        "                {'id': 'thought_level', 'options': [{'value': 'low'}, {'value': 'high'}]},\n"
        "            ],\n"
        "        }})\n"
        "    elif method == 'session/set_config_option':\n"
        "        emit({'jsonrpc': '2.0', 'id': mid, 'result': {'configOptions': []}})\n"
        "    elif method == 'session/prompt':\n"
        "        text = (params.get('prompt') or [{}])[0].get('text', '')\n"
        "        if active_prompt_id is None:\n"
        "            active_prompt_id = mid\n"
        "            emit({'jsonrpc': '2.0', 'method': 'session/update', 'params': {'sessionId': sid, 'update': {\n"
        "                'sessionUpdate': 'user_message_chunk', 'content': {'type': 'text', 'text': text}}}})\n"
        "            emit({'jsonrpc': '2.0', 'method': 'session/update', 'params': {'sessionId': sid, 'update': {\n"
        "                'sessionUpdate': 'usage_update', 'used': 5000, 'size': 1048576}}})\n"
        "            emit({'jsonrpc': '2.0', 'method': 'session/update', 'params': {'sessionId': sid, 'update': {\n"
        "                'sessionUpdate': 'tool_call', 'toolCallId': 'call_1', 'name': 'run_command',\n"
        "                'args': {'CommandLine': 'echo READY'}, 'status': 'in_progress'}}})\n"
        "            if MODE == 'normal':\n"
        "                emit({'jsonrpc': '2.0', 'method': 'session/update', 'params': {'sessionId': sid, 'update': {\n"
        "                    'sessionUpdate': 'tool_call_update', 'toolCallId': 'call_1', 'name': 'run_command',\n"
        "                    'status': 'completed', 'rawOutput': 'READY'}}})\n"
        "                emit({'jsonrpc': '2.0', 'method': 'session/update', 'params': {'sessionId': sid, 'update': {\n"
        "                    'sessionUpdate': 'agent_message_chunk', 'content': {'type': 'text', 'text': 'READY'}}}})\n"
        "                emit({'jsonrpc': '2.0', 'method': 'session/update', 'params': {'sessionId': sid, 'update': {\n"
        "                    'sessionUpdate': 'usage_update', 'used': 6200, 'size': 1048576}}})\n"
        "                emit({'jsonrpc': '2.0', 'id': mid, 'result': {'stopReason': 'end_turn', 'usage': {\n"
        "                    'inputTokens': 5000, 'outputTokens': 25, 'cachedReadTokens': 1000,\n"
        "                    'thoughtTokens': 10, 'totalTokens': 5025}}})\n"
        "                active_prompt_id = None\n"
        "        else:\n"
        "            # Mid-turn steer: immediately resolve the superseded prompt with partial usage.\n"
        "            emit({'jsonrpc': '2.0', 'id': active_prompt_id, 'result': {'stopReason': 'end_turn', 'usage': {\n"
        "                'inputTokens': 4000, 'outputTokens': 20, 'cachedReadTokens': 0,\n"
        "                'thoughtTokens': 5, 'totalTokens': 4020}}})\n"
        "            emit({'jsonrpc': '2.0', 'method': 'session/update', 'params': {'sessionId': sid, 'update': {\n"
        "                'sessionUpdate': 'tool_call_update', 'toolCallId': 'call_1', 'name': 'run_command',\n"
        "                'status': 'completed', 'rawOutput': 'READY'}}})\n"
        "            if not MODE.startswith('no_echo'):\n"
        "                emit({'jsonrpc': '2.0', 'method': 'session/update', 'params': {'sessionId': sid, 'update': {\n"
        "                    'sessionUpdate': 'user_message_chunk', 'content': {'type': 'text', 'text': text}}}})\n"
        "            reply = 'READY' if MODE == 'no_echo_cancelled' else 'READY MARIGOLD'\n"
        "            stop = 'cancelled' if MODE == 'no_echo_cancelled' else 'end_turn'\n"
        "            emit({'jsonrpc': '2.0', 'method': 'session/update', 'params': {'sessionId': sid, 'update': {\n"
        "                'sessionUpdate': 'agent_message_chunk', 'content': {'type': 'text', 'text': reply}}}})\n"
        "            emit({'jsonrpc': '2.0', 'method': 'session/update', 'params': {'sessionId': sid, 'update': {\n"
        "                'sessionUpdate': 'usage_update', 'used': 7500, 'size': 1048576}}})\n"
        "            emit({'jsonrpc': '2.0', 'id': mid, 'result': {'stopReason': stop, 'usage': {\n"
        "                'inputTokens': 1200, 'outputTokens': 30, 'cachedReadTokens': 3800,\n"
        "                'thoughtTokens': 15, 'totalTokens': 1230}}})\n"
        "            active_prompt_id = None\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)

    def calls():
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]

    return str(script), calls


def test_antigravity_is_registered_steerable_agent(monkeypatch):
    assert "antigravity" in steer.STEERABLE_AGENTS
    monkeypatch.delenv("CUTTLE_AGENT_STEER", raising=False)

    class _FakeMgr:
        def __init__(self, cfg):
            self.cfg = cfg

        def get_setting(self, key, default=None):
            return self.cfg if key == "agent_steer" else default

    monkeypatch.setattr("managers.settings_manager.get_settings_manager", lambda: _FakeMgr({}))
    assert steer.steer_enabled("antigravity") is True
    monkeypatch.setattr("managers.settings_manager.get_settings_manager", lambda: _FakeMgr({"antigravity": False}))
    assert steer.steer_enabled("antigravity") is False
    monkeypatch.setenv("CUTTLE_AGENT_STEER", "0")
    monkeypatch.setattr("managers.settings_manager.get_settings_manager", lambda: _FakeMgr({"antigravity": True}))
    assert steer.steer_enabled("antigravity") is False


@pytest.mark.skipif(os.name == "nt", reason="fake CLI uses a shebang")
def test_acp_turn_sets_model_effort_usage_and_resume(agy_acp_env, tmp_path, monkeypatch):
    exe, calls = _write_fake_acp_agy(tmp_path, agy_acp_env, mode="normal")
    monkeypatch.setattr(agy, "antigravity_executable", lambda: exe)
    chat = 8801
    store.save_antigravity_model(chat, "gemini-3.8-flash")
    store.save_antigravity_effort(chat, "low")

    adapter = agy.Adapter()
    res1 = asyncio.run(adapter.execute(
        "say ready", cwd=str(tmp_path), resume=None, model=None,
        status_queue=queue.Queue(), chat_session_id=str(chat), timeout=15,
    ))
    assert res1.success and res1.output == "READY"
    assert res1.session_id == "acp-conv-1"
    assert res1.usage["prompt_tokens"] == 5000
    assert res1.usage["completion_tokens"] == 25
    assert res1.usage["cache_read_tokens"] == 1000
    assert res1.usage["reasoning_tokens"] == 10
    assert res1.usage["context_tokens"] == 6200
    assert store.load_antigravity_context(str(chat))["context_tokens"] == 6200

    methods = [c["msg"]["method"] for c in calls() if c["mode"] == "acp"]
    assert methods[:5] == [
        "initialize",
        "session/new",
        "session/set_config_option",
        "session/set_config_option",
        "session/prompt",
    ]

    # Second turn resumes via session/resume
    res2 = asyncio.run(adapter.execute(
        "follow up", cwd=str(tmp_path), resume=adapter.load_resume(str(tmp_path), str(chat)),
        model=None, status_queue=queue.Queue(), chat_session_id=str(chat), timeout=15,
    ))
    assert res2.success and res2.output == "READY"
    methods2 = [c["msg"]["method"] for c in calls()[5:] if c["mode"] == "acp"]
    assert "session/resume" in methods2 and "session/new" not in methods2


@pytest.mark.skipif(os.name == "nt", reason="fake CLI uses a shebang")
def test_acp_mid_turn_steer_delivers_echo_and_accumulates_usage(agy_acp_env, tmp_path, monkeypatch):
    exe, calls = _write_fake_acp_agy(tmp_path, agy_acp_env, mode="steer")
    monkeypatch.setattr(agy, "antigravity_executable", lambda: exe)
    chat = "8802"
    steer_result = {}

    def _send_steer_once_live():
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if steer.active_agent(chat) == "antigravity":
                steer_result.update(steer.steer(chat, "/antigravity Also include MARIGOLD"))
                return
            time.sleep(0.02)

    t = threading.Thread(target=_send_steer_once_live, daemon=True)
    t.start()

    res = asyncio.run(agy.Adapter().execute(
        "run sleep and say READY", cwd=str(tmp_path), resume=None, model=None,
        status_queue=queue.Queue(), chat_session_id=chat, timeout=15,
    ))
    t.join(timeout=5.0)

    assert steer_result.get("steered") is True
    assert steer_result.get("agent") == "antigravity"
    assert steer_result.get("text") == "Also include MARIGOLD"
    assert res.success is True
    assert "READY MARIGOLD" in res.output
    assert res.meta.get("steered") == 1
    assert not res.meta.get("undelivered_steers")
    # Sums both prompts (4000+1200 input, 20+30 output, 0+3800 cache, 5+15 thinking)
    assert res.usage["prompt_tokens"] == 5200
    assert res.usage["completion_tokens"] == 50
    assert res.usage["cache_read_tokens"] == 3800
    assert res.usage["reasoning_tokens"] == 20
    assert res.usage["context_tokens"] == 7500


def _run_with_one_steer(tmp_path, monkeypatch, conversations, mode, chat, text):
    exe, _calls = _write_fake_acp_agy(tmp_path, conversations, mode=mode)
    monkeypatch.setattr(agy, "antigravity_executable", lambda: exe)
    steer_result = {}

    def _send_steer_once_live():
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if steer.active_agent(chat) == "antigravity":
                steer_result.update(steer.steer(chat, text))
                return
            time.sleep(0.02)

    t = threading.Thread(target=_send_steer_once_live, daemon=True)
    t.start()
    res = asyncio.run(agy.Adapter().execute(
        "run sleep", cwd=str(tmp_path), resume=None, model=None,
        status_queue=queue.Queue(), chat_session_id=chat, timeout=15,
    ))
    t.join(timeout=5.0)
    return steer_result, res


@pytest.mark.skipif(os.name == "nt", reason="fake CLI uses a shebang")
def test_acp_steer_without_echo_is_confirmed_by_its_own_prompt_reply(agy_acp_env, tmp_path, monkeypatch):
    # Real agy acp never echoes a live session/prompt (probed 2026-11-24).
    steer_result, res = _run_with_one_steer(
        tmp_path, monkeypatch, agy_acp_env, "no_echo", "8803", "Also include MARIGOLD")

    assert steer_result.get("steered") is True
    assert res.success is True
    assert "READY MARIGOLD" in res.output
    assert res.meta.get("steered") == 1
    assert not res.meta.get("undelivered_steers")
    assert "without confirming it read" not in res.output


@pytest.mark.skipif(os.name == "nt", reason="fake CLI uses a shebang")
def test_acp_steer_prompt_cancelled_adds_closing_notice(agy_acp_env, tmp_path, monkeypatch):
    steer_result, res = _run_with_one_steer(
        tmp_path, monkeypatch, agy_acp_env, "no_echo_cancelled", "8805", "unconfirmed follow-up")

    assert steer_result.get("steered") is True
    assert res.meta.get("undelivered_steers") == ["unconfirmed follow-up"]
    assert "without confirming it read" in res.output


@pytest.mark.skipif(os.name == "nt", reason="fake CLI uses a shebang")
def test_acp_setup_failure_falls_back_to_print_mode(agy_acp_env, tmp_path, monkeypatch):
    exe, calls = _write_fake_acp_agy(tmp_path, agy_acp_env, mode="fail_init")
    monkeypatch.setattr(agy, "antigravity_executable", lambda: exe)

    res = asyncio.run(agy.Adapter().execute(
        "hello", cwd=str(tmp_path), resume=None, model=None,
        status_queue=queue.Queue(), chat_session_id="8804", timeout=15,
    ))
    assert res.success is True
    assert res.output == "fallback pong"
    modes = [c["mode"] for c in calls()]
    assert "acp" in modes and "print" in modes

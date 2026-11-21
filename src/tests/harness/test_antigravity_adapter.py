"""Antigravity (`agy`) connector: parity contract with Codex / Claude, offline.

Fixtures are recorded from `agy 1.5.13` (`agy models`, headless `/usage` +
`/credits` JSON, stream-json turns). A fake `agy` records argv + stdin so the
transport, permission, resume and model/effort flags are asserted at argv level.
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import stat
import sys

import pytest

from api.agent_harness.agents.antigravity import adapter as agy
from api.agent_harness.agents.antigravity import model_catalog as mc
from api.agent_harness.agents.antigravity import session_store as store
from api.agent_harness.agents.antigravity import usage as agy_usage

AGY_MODELS = (
    "Fetching available models...\n"
    "gemini-4-neon-high\tGemini 4 Neon (High)\n"
    "gemini-4-neon-xhigh\tGemini 4 Neon (XHigh)\n"
    "gemini-4-neon-low\tGemini 4 Neon (Low)\n"
    "gemini-pro-orchestrator-neon\tOrchestrator (Neon)\n"
    "gemini-3.8-flash-high\tGemini 3.8 Flash (High)\n"
    "gemini-3.8-flash-low\tGemini 3.8 Flash (Low)\n"
    "gpt-oss-120b-medium\tGPT-OSS 120B (Medium)\n"
)

USAGE_DATA = {"groups": [
    {"name": "Gemini Models", "description": "Models within this group: Gemini 4 Neon, Gemini 3.8 Flash",
     "buckets": [
         {"id": "gemini-weekly", "name": "Weekly Limit Remaining", "window": "weekly",
          "remaining_fraction": 0.5, "reset_time": "2026-11-28T00:42:55Z"},
         {"id": "gemini-5h", "name": "Five Hour Limit Remaining", "window": "5h",
          "remaining_fraction": 0, "reset_time": "2026-11-21T05:42:55Z"}]},
    {"name": "Claude and GPT models", "description": "Models within this group: Claude Opus 5.5",
     "buckets": [
         {"id": "3p-weekly", "name": "Weekly Limit Remaining", "window": "weekly",
          "remaining_fraction": 1, "reset_time": "2026-11-28T00:42:55Z"}]},
]}

SENTINEL = "END-OF-PROMPT-SENTINEL-7731"


@pytest.fixture
def agy_state(tmp_path, monkeypatch):
    """Isolated resume/pin map, empty agy conversation store, cold catalog."""
    monkeypatch.setattr(store, "_map_file", lambda: tmp_path / "agy_map.json")
    conversations = tmp_path / "agy-conversations"
    conversations.mkdir()
    monkeypatch.setenv("CUTTLE_AGY_CONVERSATIONS_DIR", str(conversations))
    monkeypatch.setattr(mc, "_run_agy_models", lambda: (mc.parse_agy_models(AGY_MODELS), None))
    mc.clear_antigravity_catalog_cache()

    async def _signed_in(*_a):
        return None

    monkeypatch.setattr(agy, "_authentication_preflight", _signed_in)
    yield conversations
    mc.clear_antigravity_catalog_cache()


@pytest.fixture
def fake_agy(tmp_path, monkeypatch, agy_state):
    """Shebang `agy` that logs argv + stdin and answers one stream-json turn."""
    log = tmp_path / "agy_calls.jsonl"
    script = tmp_path / "agy"
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "argv = sys.argv[1:]\n"
        "stdin = sys.stdin.read()\n"
        f"open({str(log)!r}, 'a').write(json.dumps({{'argv': argv, 'stdin': stdin}}) + '\\n')\n"
        "cid = argv[argv.index('--conversation') + 1] if '--conversation' in argv else 'conv-new-1'\n"
        f"open(os.path.join({str(agy_state)!r}, cid + '.db'), 'w').close()\n"
        "def emit(o): print(json.dumps(o), flush=True)\n"
        "emit({'event': 'init', 'conversation_id': cid, 'init': {'model': 'gemini-3.8-flash'}})\n"
        "emit({'event': 'step_update', 'step_update': {'conversation_id': cid, 'step_index': 1, 'state': 'DONE',\n"
        "      'step_type': 'tool', 'tool_name': 'run_command', 'tool_info': {'name': 'run_command',\n"
        "      'parameters': {'CommandLine': 'ls'}, 'output': 'a.txt'},\n"
        "      'usage': {'input_tokens': 9000, 'output_tokens': 5, 'cache_read_tokens': 0}}})\n"
        "emit({'event': 'step_update', 'step_update': {'conversation_id': cid, 'step_index': 2, 'state': 'DONE',\n"
        "      'step_type': 'agent_response', 'text_delta': 'pong',\n"
        "      'usage': {'input_tokens': 2000, 'output_tokens': 21, 'thinking_tokens': 19, 'cache_read_tokens': 7000}}})\n"
        "emit({'event': 'result', 'result': {'conversation_id': cid, 'status': 'SUCCESS', 'response': 'pong\\n',\n"
        "      'usage': {'input_tokens': 11000, 'output_tokens': 26, 'thinking_tokens': 19,\n"
        "                'cache_read_tokens': 7000, 'total_tokens': 11026}}})\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(agy, "antigravity_executable", lambda: str(script))

    def calls():
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]

    return calls


def _run(adapter, prompt, *, cwd, chat, model=None, effort=None):
    resume = adapter.load_resume(cwd, chat)
    result = asyncio.run(adapter.execute(
        prompt, cwd=cwd, resume=resume, model=model, status_queue=queue.Queue(),
        chat_session_id=chat, timeout=30, reasoning_effort=effort,
    ))
    if result.session_id:
        adapter.save_resume(cwd, chat, result.session_id)
    return result


# ── catalog ─────────────────────────────────────────────────────────────────

def test_agy_models_group_into_families_with_effort_levels():
    rows = {r["id"]: r for r in mc.parse_agy_models(AGY_MODELS)}
    assert rows["gemini-4-neon"] == {"id": "gemini-4-neon", "label": "Gemini 4 Neon",
                                     "efforts": ["low", "high", "xhigh"]}
    assert rows["gemini-pro-orchestrator-neon"]["efforts"] == []  # fixed variant
    assert rows["gpt-oss-120b"]["efforts"] == ["medium"]
    assert "Fetching" not in json.dumps(rows)


def test_suffixed_slug_never_gets_a_conflicting_effort_flag(agy_state):
    # agy rejects `--model gemini-3.8-flash-low --effort high` before any turn.
    plan = agy.resolve_turn_model_effort(None, "gemini-3.8-flash-low", "high")
    assert plan["model"] == "gemini-3.8-flash-low" and plan["flag_effort"] is None
    assert plan["effort"] == "low"
    assert mc.antigravity_efforts_for_model("gemini-3.8-flash") == ["low", "high"]
    assert mc.antigravity_efforts_for_model("gemini-3.8-flash-low") == []
    assert mc.antigravity_model_label("gemini-4-neon-high") == "Gemini 4 Neon (High)"


def test_model_switch_clears_an_effort_the_new_model_lacks(agy_state):
    store.save_antigravity_effort(8, "xhigh")
    res = agy.Adapter().handle_meta("/model gemini-4-neon", chat_session_id="8", model=None)
    assert store.load_antigravity_effort(8) == "xhigh" and "cleared" not in res.output
    res = agy.Adapter().handle_meta("/model gemini-3.8-flash", chat_session_id="8", model=None)
    assert store.load_antigravity_effort(8) is None and "`xhigh` cleared" in res.output


def test_signed_out_models_output_is_not_a_catalog(monkeypatch):
    class _Proc:
        returncode = 0  # agy models exits 0 while signed out
        stdout = "Fetching available models...\n"
        stderr = "Error: Please sign in to view available models. Launch the CLI without arguments to sign in.\n"

    monkeypatch.setattr(agy, "antigravity_executable", lambda: "/fake/agy")
    monkeypatch.setattr(mc.subprocess, "run", lambda *a, **k: _Proc())
    rows, err = mc._run_agy_models()
    assert rows == [] and "not signed in" in err


def test_auth_preflight_reads_text_not_exit_code(monkeypatch, tmp_path):
    class _Proc:
        returncode = 0

        async def communicate(self):
            return b"Fetching available models...\n", b"Error: Please sign in to view available models.\n"

    async def _spawn(*_a, **_k):
        return _Proc()

    monkeypatch.setattr(agy, "_auth_ok_at", 0.0)
    monkeypatch.setattr(agy.asyncio, "create_subprocess_exec", _spawn)
    msg = asyncio.run(agy._authentication_preflight("/fake/agy", str(tmp_path)))
    assert msg and "not signed in" in msg.lower() and "`agy`" in msg and "\n" not in msg


# ── turns: transport, permissions, pins, usage, resume ──────────────────────

@pytest.mark.skipif(os.name == "nt", reason="fake CLI uses a shebang")
def test_turn_sends_prompt_on_stdin_with_pins_and_bypassed_approvals(fake_agy, tmp_path):
    chat = 4242  # raw int, as an authenticated web chat sends it
    store.save_antigravity_model(chat, "gemini-3.8-flash")
    store.save_antigravity_effort(chat, "low")
    prompt = ("x" * 40_000) + SENTINEL
    result = _run(agy.Adapter(), prompt, cwd=str(tmp_path), chat=chat)

    assert result.success and result.output == "pong"
    (call,) = fake_agy()
    argv = call["argv"]
    assert SENTINEL not in " ".join(argv), "prompt must travel on stdin, never argv"
    event = json.loads(call["stdin"])
    assert event["event"] == "user" and event["message"]["content"].endswith(SENTINEL)
    assert argv[:2] == ["-p", ""]
    assert argv[argv.index("--input-format") + 1] == "stream-json"
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert "--dangerously-skip-permissions" in argv
    assert argv[argv.index("--model") + 1] == "gemini-3.8-flash"
    assert argv[argv.index("--effort") + 1] == "low"
    assert "--conversation" not in argv

    assert result.meta["agent_model"] == "gemini-3.8-flash" and result.meta["model_source"] == "session"
    assert result.meta["agent_effort"] == "low" and result.meta["effort_source"] == "session"
    usage = result.usage
    assert usage["prompt_tokens"] == 11000 and usage["cache_read_tokens"] == 7000
    assert usage["cache_inclusive"] is False  # agy input excludes cache reads
    assert usage["reasoning_tokens"] == 19
    # Window fill = last model call (2000 input + 7000 cached), not summed billing.
    assert usage["context_tokens"] == 9000
    assert store.load_antigravity_context(chat)["context_tokens"] == 9000


@pytest.mark.skipif(os.name == "nt", reason="fake CLI uses a shebang")
def test_second_turn_resumes_the_saved_conversation(fake_agy, tmp_path):
    adapter = agy.Adapter()
    chat, cwd = 147, str(tmp_path)
    _run(adapter, "remember banana", cwd=cwd, chat=chat)
    assert store.load_antigravity_resume_id(cwd, chat) == "conv-new-1"
    _run(adapter, "what word?", cwd=cwd, chat=chat)
    first, second = fake_agy()
    assert "--conversation" not in first["argv"]
    assert second["argv"][second["argv"].index("--conversation") + 1] == "conv-new-1"
    # Another chat never inherits it.
    _run(adapter, "fresh chat", cwd=cwd, chat=155)
    assert "--conversation" not in fake_agy()[2]["argv"]


def test_stale_conversation_is_dropped_before_the_briefing_is_skipped(agy_state, tmp_path):
    # agy silently starts a new conversation for an unknown id, so the kernel
    # must see "no resume" and send the full briefing instead.
    adapter, cwd = agy.Adapter(), str(tmp_path)
    store.save_antigravity_resume_id(cwd, 9, "gone-conv")
    assert adapter.load_resume(cwd, 9) is None
    assert store.load_antigravity_resume_id(cwd, 9) is None
    (agy_state / "kept-conv.db").write_text("")
    store.save_antigravity_resume_id(cwd, 9, "kept-conv")
    assert adapter.load_resume(cwd, 9) == "kept-conv"


def test_pins_never_masquerade_as_resume_ids(agy_state, tmp_path):
    store.save_antigravity_model(77, "gemini-4-neon")
    store.save_antigravity_effort(77, "HIGH")
    assert store.load_antigravity_resume_id(str(tmp_path), 77) is None
    assert store.load_antigravity_model("77") == "gemini-4-neon"
    assert store.load_antigravity_effort(77) == "high"
    store.save_antigravity_model(77, "")
    assert store.load_antigravity_model(77) is None


def test_unsupported_effort_stops_before_spending(agy_state, monkeypatch):
    monkeypatch.setattr(agy, "antigravity_executable", lambda: "/fake/agy")
    store.save_antigravity_model(5, "gemini-3.8-flash")
    store.save_antigravity_effort(5, "xhigh")
    result = asyncio.run(agy.Adapter().execute("hi", cwd="/tmp", resume=None, model=None, chat_session_id=5))
    assert "was not started" in result.output and "`low`" in result.output


# ── meta commands (no CLI turn) ─────────────────────────────────────────────

def test_model_and_effort_slash_pin_per_chat(agy_state):
    adapter = agy.Adapter()
    res = adapter.handle_meta("/model gemini-4-neon", chat_session_id="31", model=None)
    assert res.success and store.load_antigravity_model(31) == "gemini-4-neon"
    assert res.meta["model_source"] == "session"
    listing = adapter.handle_meta("/model", chat_session_id="31", model=None)
    assert "`gemini-4-neon` — Gemini 4 Neon ✅ current" in listing.output
    bad = adapter.handle_meta("/effort max", chat_session_id="31", model=None)
    assert "not supported" in bad.output and store.load_antigravity_effort(31) is None
    ok = adapter.handle_meta("/effort xhigh", chat_session_id="31", model=None)
    assert ok.meta["agent_effort"] == "xhigh" and store.load_antigravity_effort(31) == "xhigh"
    assert adapter.handle_meta("model the login flow", chat_session_id="31", model=None) is None
    assert adapter.handle_meta("effort estimate for X", chat_session_id="31", model=None) is None
    cleared = adapter.handle_meta("/model default", chat_session_id="31", model=None)
    assert "cleared" in cleared.output and store.load_antigravity_model(31) is None


def test_usage_slash_renders_quota_meters_without_a_turn(agy_state, monkeypatch):
    monkeypatch.setattr(agy_usage, "fetch_antigravity_quota", lambda **_k: {
        "success": True, "groups": agy_usage.parse_quota_groups(USAGE_DATA),
        "credits": {"remaining": 0, "upgrade_uri": "https://antigravity.google/g1-upgrade"}})
    res = agy.Adapter().handle_meta("/usage", chat_session_id="1", model=None)
    assert res.success and res.output.startswith("**Antigravity — usage**")
    meters = json.loads(res.output.split("<cuttle_meters>\n", 1)[1].split("\n</cuttle_meters>", 1)[0])
    labels = {r["label"]: r for r in meters["rows"]}
    assert labels["Gemini Models · 5-hour"]["pct"] == 0
    assert labels["Gemini Models · Weekly"]["pct"] == 50
    assert labels["AI credits"]["disabled"] is True


# ── quota → gizmo meter + router budget ─────────────────────────────────────

def test_quota_groups_parse_from_recorded_usage_json():
    groups = agy_usage.parse_quota_groups(USAGE_DATA)
    assert [g["name"] for g in groups] == ["Gemini Models", "Claude and GPT models"]
    assert groups[0]["models"] == "Gemini 4 Neon, Gemini 3.8 Flash"
    five = groups[0]["buckets"][1]
    assert five["label"] == "5-hour" and five["remaining_percent"] == 0 and five["reset_at"] > 0


def test_one_spent_group_is_not_an_account_block():
    from api.agent_router.budget import parse_antigravity
    from api.gizmos.usage import finalize, normalize_antigravity

    data = {"success": True, "groups": agy_usage.parse_quota_groups(USAGE_DATA), "credits": {"remaining": 0}}
    body = normalize_antigravity(data, 0)
    assert not body["blocked"] and "Gemini Models" in body["extras"]["note"]
    assert {w["id"] for w in finalize(body, 0)["windows"]} >= {"gemini_models_5h", "claude_and_gpt_models_weekly"}
    acc = parse_antigravity(data, 0)
    assert acc.blocked is False and acc.headroom == 1.0

    spent = json.loads(json.dumps(USAGE_DATA))
    spent["groups"][1]["buckets"][0]["remaining_fraction"] = 0
    data = {"success": True, "groups": agy_usage.parse_quota_groups(spent), "credits": {"remaining": 0}}
    assert normalize_antigravity(data, 0)["blocked"] is True
    acc = parse_antigravity(data, 0)
    assert acc.blocked is True and acc.headroom == 0.0 and acc.reset_at


def test_usage_provider_is_registered_for_meters():
    from api.gizmos import usage as gizmo_usage

    assert {"agent": "antigravity", "label": "Antigravity"} in gizmo_usage.providers()


# ── error shaping ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, needle", [
    ("NOT_FOUND (code 404): Requested entity was not found.", "/antigravity model"),
    ('invalid model selection (--model "gemini-3.8-flash-low" --effort "high"): conflicts with --effort=high',
     "/antigravity effort"),
    ("RESOURCE_EXHAUSTED: quota exceeded for this window", "/antigravity usage"),
])
def test_failures_shape_to_one_actionable_line(raw, needle):
    msg = agy.summarize_antigravity_error(raw + "\nAGY_ERROR: {\"error_id\": \"x\"}", 1, model="gemini-4-hydrogen")
    assert needle in msg and "\n" not in msg and "AGY_ERROR" not in msg


def test_compact_refuses_a_missing_conversation(agy_state, monkeypatch):
    monkeypatch.setattr(agy, "antigravity_executable", lambda: "/fake/agy")
    out = agy.compact_conversation("/tmp", "gone-conv", 3)
    assert out["success"] is False and "not found" in out["error"]

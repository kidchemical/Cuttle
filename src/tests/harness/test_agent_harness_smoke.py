"""Smoke + error-shaping tests for the Agent Harness.

Two layers:

* Offline (always runs): the error summarizer must turn raw CLI stderr into a single
  actionable line, and adapters must expose a well-formed contract. This is the gate that
  would have caught the CH-000148 issues before the user tested them.
* Live (opt-in via ``CUTTLE_AGENT_SMOKE=1``): for each *available* agent, run a real one-shot
  prompt and assert the reply is a genuine answer, not an error banner. Run this before handing
  a new agent to the user. See ``src/api/agent_harness/ADDING_AN_AGENT.md``.
  WARNING: the live layer sends REAL prompts and SPENDS REAL TOKENS. Never set
  ``CUTTLE_AGENT_SMOKE=1`` without asking the user first (confirm model + scope).
"""

from __future__ import annotations

import os
import re

import pytest

from api.agent_harness.catalog import list_agents, get_agent, reload_catalog
from api.agent_harness.smoke_policy import (
    live_model,
    parametrize_ids,
    scope_enabled,
)


def setup_function(_fn=None):
    reload_catalog()




def test_summarize_antigravity_auth_wall():
    from api.agent_harness.agents.antigravity.adapter import summarize_antigravity_error

    raw = "authentication required: no cached credential profile is available"
    msg = summarize_antigravity_error(raw, returncode=1)
    assert "authentication is required" in msg.lower()
    assert "agy" in msg
    assert "\n" not in msg


def test_summarize_opencode_auth_wall():
    from api.agent_harness.agents.opencode.adapter import summarize_opencode_error

    msg = summarize_opencode_error("Error: unauthorized; run opencode auth login", 1)
    assert "not authenticated" in msg.lower()
    assert "opencode auth login" in msg
    assert "\n" not in msg


def test_summarize_opencode_credit_wall():
    from api.agent_harness.agents.opencode.adapter import summarize_opencode_error

    msg = summarize_opencode_error(
        '{"message":"Your credit balance is too low. Go to Plans & Billing."}',
        1,
    )
    assert "credits" in msg.lower() or "quota" in msg.lower()
    assert "openai" in msg.lower() or "sync" in msg.lower()
    assert "\n" not in msg


def test_opencode_jsonl_activity_matches_cursor_style():
    from api.agent_harness.agents.opencode.adapter import _opencode_activity_for_event

    state: dict = {}
    assert _opencode_activity_for_event({"type": "step_start", "part": {}}, state) == (
        "OpenCode is thinking…"
    )
    tool_evt = {
        "type": "tool_use",
        "part": {
            "tool": "read",
            "state": {"title": "Read AGENTS.md", "status": "completed"},
        },
    }
    assert _opencode_activity_for_event(tool_evt, state) == "tool 1: Read AGENTS.md"
    assert _opencode_activity_for_event(
        {"type": "text", "part": {"text": "Here is the summary"}},
        state,
    ) == "writing: Here is the summary"  # ellipsis only when shortened
    assert _opencode_activity_for_event(
        {
            "type": "error",
            "error": {"name": "APIError", "data": {"message": "Rate limit exceeded"}},
        },
        state,
    ) == "OpenCode error: Rate limit exceeded"


def test_opencode_step_finish_usage_is_accumulated():
    from api.agent_harness.agents.opencode.adapter import _parse_opencode_stdout

    raw = "\n".join(
        [
            '{"type":"step_start","sessionID":"ses_abc","part":{"type":"step-start"}}',
            '{"type":"tool_use","part":{"tool":"bash","state":{"title":"List files"}}}',
            '{"type":"text","part":{"type":"text","text":"Done."}}',
            (
                '{"type":"step_finish","sessionID":"ses_abc","part":{"type":"step-finish",'
                '"reason":"tool-calls","cost":0,"tokens":{"input":1200,"output":40}}}'
            ),
            (
                '{"type":"step_finish","sessionID":"ses_abc","part":{"type":"step-finish",'
                '"reason":"stop","cost":0.0012,"tokens":{"input":671,"output":8}}}'
            ),
        ]
    )
    text, sid, usage, _detected = _parse_opencode_stdout(raw)
    assert text == "Done."
    assert sid == "ses_abc"
    assert usage["prompt_tokens"] == 1871
    assert usage["completion_tokens"] == 48
    assert usage["total_tokens"] == 1919
    assert usage["cost"] == pytest.approx(0.0012)
    # Billing sums steps; context fill uses last step input (and peak of steps).
    assert usage["context_tokens"] == 671
    assert usage["peak_context_tokens"] == 1200


def test_opencode_cache_read_survives_to_outcome_store(tmp_path):
    """GLM-style step_finish cache.read must reach cached_tokens (not dropped)."""
    from api.agent_harness.agents.opencode.adapter import _parse_opencode_stdout
    from api.agent_harness.kernel import _usage_from_result
    from api.agent_router.outcomes import all_outcomes
    from api.agent_router.pinned_outcomes import record_pinned_turn

    raw = "\n".join(
        [
            '{"type":"step_finish","part":{"type":"step-finish","reason":"stop",'
            '"cost":0.0089,"tokens":{"input":10358,"output":570,'
            '"cache":{"write":0,"read":203904}}}}',
            '{"type":"step_finish","part":{"type":"step-finish","reason":"stop",'
            '"cost":0.0094,"tokens":{"input":7856,"output":226,'
            '"cache":{"write":0,"read":199000}}}}',
        ]
    )
    _, _, adapter_usage, _ = _parse_opencode_stdout(raw)
    assert adapter_usage["cache_read_tokens"] == 402904
    body = {
        "agent_id": "opencode", "query_id": "cache-q", "response": "hi",
        "type": "opencode_command", "success": True,
        "usage": _usage_from_result(adapter_usage),
    }
    assert record_pinned_turn("opencode", body, latency_ms=5, db_path=tmp_path / "o.db")
    stored = all_outcomes(db_path=tmp_path / "o.db")[0]
    assert stored["cached_tokens"] == 402904


def test_opencode_context_refreshes_fully_cached_steps():
    import json
    from api.agent_harness.agents.opencode.adapter import _parse_opencode_stdout

    steps = [
        {"input": 100, "output": 5, "cache": {"read": 800, "write": 200}},
        {"input": 0, "output": 2, "cache": {"read": 600, "write": 100}},
    ]
    raw = "\n".join(json.dumps({"type": "step_finish", "part": {"tokens": step}}) for step in steps)
    _, _, usage, _ = _parse_opencode_stdout(raw)
    assert usage["prompt_tokens"] == 100
    assert usage["completion_tokens"] == 7
    assert usage["cache_read_tokens"] == 1400
    assert usage["cache_write_tokens"] == 300
    assert usage["context_tokens"] == 700
    assert usage["peak_context_tokens"] == 1100
    assert usage["cache_inclusive"] is False


@pytest.mark.asyncio
async def test_opencode_exec_emits_jsonl_activity(monkeypatch, tmp_path):
    import asyncio
    import queue
    from api.agent_harness.agents.opencode import adapter as oc
    from tests.harness.test_agent_harness_timeouts import _patch_spawn, _Proc

    lines = [
        b'{"type":"step_start","part":{"type":"step-start"}}\n',
        b'{"type":"tool_use","part":{"tool":"bash","state":{"title":"List files","status":"completed"}}}\n',
        b'{"type":"text","part":{"text":"Done."}}\n',
    ]
    proc = _Proc(lines)
    _patch_spawn(monkeypatch, oc, proc)

    statuses = queue.Queue()
    result = await oc.Adapter().execute(
        "task",
        cwd=str(tmp_path),
        resume=None,
        model=None,
        status_queue=statuses,
        timeout=30.0,
    )
    assert result.success is True
    activity = [statuses.get_nowait()[1] for _ in range(statuses.qsize())]
    assert activity == [
        "Calling OpenCode…",
        "OpenCode is thinking…",
        "tool 1: List files",
        "writing: Done.",
    ]


def test_summarize_deepseek_missing_credential():
    from api.agent_harness.agents.deepseek.adapter import summarize_deepseek_error

    msg = summarize_deepseek_error("MISSING_CREDENTIAL: DEEPSEEK_API_KEY is not set", 1)
    # CLI auth stays with the DeepSeek CLI; never point users at Cuttle's .env.
    assert "deepseek cli" in msg.lower()
    assert ".env" not in msg.lower()
    assert "\n" not in msg
    assert "MISSING_CREDENTIAL" not in msg


def test_opencode_transports_long_prompt_losslessly_over_stdin(monkeypatch, tmp_path):
    import asyncio
    from api.agent_harness.agents.opencode import adapter as oc

    prompt = ("context-padding-" * 400) + "\nEND-SENTINEL-DO-NOT-DROP"
    captured = {}

    class _Stdin:
        def write(self, data):
            captured["stdin"] = data

        async def drain(self):
            return None

        def close(self):
            captured["stdin_closed"] = True

    class _Reader:
        def __init__(self, lines):
            self._lines = list(lines)

        async def readline(self):
            return self._lines.pop(0) if self._lines else b""

    class _Process:
        returncode = 0

        def __init__(self):
            self.stdin = _Stdin()
            self.stdout = _Reader([b'{"text":"pong"}\n'])
            self.stderr = _Reader([])

        async def wait(self):
            return 0

    async def _spawn(*args, **kwargs):
        captured["argv"] = args
        captured["stdin_mode"] = kwargs.get("stdin")
        return _Process()

    async def _no_kill(proc):
        return None

    monkeypatch.setattr(oc, "opencode_executable", lambda: "opencode.exe")
    monkeypatch.setattr(oc.asyncio, "create_subprocess_exec", _spawn)
    monkeypatch.setattr(oc, "attach_to_chat_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(oc, "kill_process_tree", _no_kill)

    result = asyncio.run(
        oc.Adapter().execute(
            prompt,
            cwd=str(tmp_path),
            resume=None,
            model=None,
            timeout=5,
        )
    )

    assert result.success is True
    assert captured["stdin_mode"] == asyncio.subprocess.PIPE
    assert captured["stdin"] == prompt.encode("utf-8")
    assert captured["stdin_closed"] is True
    assert prompt not in captured["argv"]
    assert not any("END-SENTINEL-DO-NOT-DROP" in str(arg) for arg in captured["argv"])


def test_opencode_prefers_packaged_exe_over_npm_cmd_shim(tmp_path):
    from api.agent_harness.agents.opencode.adapter import _prefer_native_binary

    shim = tmp_path / "opencode.cmd"
    native = tmp_path / "node_modules" / "opencode-ai" / "bin" / "opencode.exe"
    shim.write_text("@echo off\n", encoding="utf-8")
    native.parent.mkdir(parents=True)
    native.write_bytes(b"native")

    assert _prefer_native_binary(str(shim), is_windows=True) == str(native)


def test_kernel_resume_round_trip_passes_session_to_second_execute(monkeypatch, tmp_path):
    """Offline resume smoke: turn 2 must receive the CLI session saved on turn 1."""
    from api.agent_harness import kernel
    from api.agent_harness.types import AgentManifest, AgentResult
    from api.cuttle_brain import handoff as ho

    monkeypatch.setattr(ho, "_map_file", lambda: tmp_path / "last_agent.json")
    store = {"sid": None}
    seen_resumes = []

    class _FakeAdapter:
        def available(self):
            return True

        def resolve_cwd(self, project_path: str) -> str:
            return str(tmp_path)

        def load_resume(self, cwd, chat_session_id):
            return store["sid"]

        def save_resume(self, cwd, chat_session_id, cli_session_id):
            store["sid"] = cli_session_id

        def clear_resume(self, cwd, chat_session_id):
            store["sid"] = None

        async def execute(self, prompt, **kwargs):
            seen_resumes.append(kwargs.get("resume"))
            return AgentResult(
                success=True,
                output="pong" if kwargs.get("resume") else "seeded",
                session_id="cli-resume-abc",
            )

    manifest = AgentManifest(
        id="resumebot",
        label="ResumeBot",
        slash="/resumebot",
        resume=True,
        capabilities_inject="never",
    )
    monkeypatch.setattr(
        kernel, "get_agent", lambda aid, project_path=None: (manifest, _FakeAdapter())
    )
    monkeypatch.setattr(
        "api.query_tracker.start_query_tracking", lambda *a, **k: "qid"
    )
    monkeypatch.setattr(
        "api.query_tracker.finish_query_tracking", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "api.query_tracker.get_query_tracker", lambda *a, **k: None
    )
    monkeypatch.setattr("api.active_executions.register_execution", lambda *a, **k: None)
    monkeypatch.setattr("api.active_executions.unregister_execution", lambda *a, **k: None)

    r1 = kernel.run_agent_web_command("resumebot", "remember X", 501, project_path=str(tmp_path))
    r2 = kernel.run_agent_web_command("resumebot", "what is X?", 501, project_path=str(tmp_path))
    assert r1.get("response") == "seeded"
    assert r2.get("response") == "pong"
    assert seen_resumes[0] is None
    assert seen_resumes[1] == "cli-resume-abc"
    assert ho.get_last_agent("501") == "resumebot"


# --------------------------------------------------------------------------- #
# Offline: adapter contract for every discovered agent
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("agent_id", list_agents() or ["cursor"])
def test_adapter_contract(agent_id, isolated_resume_stores, tmp_path):
    pair = get_agent(agent_id)
    assert pair is not None, f"{agent_id} did not load"
    manifest, adapter = pair
    # available() must never raise, always return a bool.
    assert isinstance(adapter.available(), bool)
    if not manifest.resume:
        return

    # The resume store must round-trip the raw int DB id the kernel passes. Testing
    # only load/clear missed ERR-20260816-009, where *save* was the broken direction
    # and every turn silently became a memory-less CLI session.
    cwd = adapter.resolve_cwd(".")
    assert adapter.load_resume(cwd, 148) is None
    adapter.save_resume(cwd, 148, "e941f922-fd0f-466d-98d1-0a729e62b2bd")
    assert adapter.load_resume(cwd, 148) == "e941f922-fd0f-466d-98d1-0a729e62b2bd", (
        f"{agent_id}: an int chat id did not survive save→load, so turn 2 would start "
        "a fresh CLI session with no memory of the chat"
    )
    adapter.clear_resume(cwd, 148)
    assert adapter.load_resume(cwd, 148) is None


# --------------------------------------------------------------------------- #
# Live smoke (opt-in): real CLI turns — ask model + scope first (ADDING_AN_AGENT.md)
# --------------------------------------------------------------------------- #

_ERROR_MARKERS = ("❌", "not found", "auth failed", "not authenticated", "quota", "failed (exit")
_LIVE_IDS = parametrize_ids(list_agents() or ["cursor"])


def _prepare_and_model(agent_id: str, session_id: int, manifest) -> str:
    mid = live_model(agent_id, manifest) or (manifest.default_model or None)
    if agent_id == "muse" and mid:
        from scripts.utilities.muse_cli_session_store import save_muse_model

        save_muse_model(str(session_id), mid)
    return mid or ""


def _assert_live_model(agent_id: str, result: dict, expected: str) -> None:
    if not expected:
        return
    got = (result.get("agent_model") or result.get("model") or "").strip()
    if got:
        assert got == expected, (
            f"{agent_id} live smoke used `{got}` instead of budgeted `{expected}`. "
            "Set CUTTLE_AGENT_SMOKE_MODEL / manifest.smoke_model, or pick a cheaper id."
        )


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("CUTTLE_AGENT_SMOKE") != "1",
    reason="LIVE smoke that SPENDS REAL TOKENS; set CUTTLE_AGENT_SMOKE=1 only after asking the user (model + scope)",
)
@pytest.mark.skipif(not scope_enabled("one_shot"), reason="CUTTLE_AGENT_SMOKE_SCOPE excludes one_shot")
@pytest.mark.parametrize("agent_id", _LIVE_IDS)
def test_live_one_shot(agent_id):
    """Cheap default live gate: one short reply. Not a long-pad integrity run."""
    pair = get_agent(agent_id)
    assert pair is not None
    manifest, adapter = pair
    if not adapter.available():
        pytest.skip(f"{agent_id} CLI not available on this machine")

    from api.agent_harness.kernel import run_agent_web_command

    expected = _prepare_and_model(agent_id, 999148, manifest)
    result = run_agent_web_command(
        agent_id,
        "Reply with exactly the word: pong",
        chat_session_id=999148,
        timeout=120.0,
        model_override=expected or None,
    )
    _assert_live_model(agent_id, result, expected)
    resp = (result.get("response") or "").strip()
    assert resp, f"{agent_id} returned empty response"
    low = resp.lower()
    assert not any(m in low for m in _ERROR_MARKERS), (
        f"{agent_id} live smoke returned an error, not an answer:\n{resp}"
    )
    visible = re.sub(r"<think>[\s\S]*?</think>", "", low, flags=re.I).strip()
    visible = re.sub(r"\s+", " ", visible)
    assert "pong" in visible, (
        f"{agent_id} did not follow the short one-shot instruction:\n{resp}"
    )


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("CUTTLE_AGENT_SMOKE") != "1",
    reason="LIVE smoke that SPENDS REAL TOKENS; set CUTTLE_AGENT_SMOKE=1 only after asking the user (model + scope)",
)
@pytest.mark.skipif(
    not scope_enabled("long_pad"),
    reason="long_pad is opt-in (CUTTLE_AGENT_SMOKE_SCOPE=long_pad or full)",
)
@pytest.mark.parametrize("agent_id", _LIVE_IDS)
def test_live_long_pad(agent_id):
    """Optional: prove a >2800-char prompt is not truncated. One prompt, not thousands."""
    pair = get_agent(agent_id)
    assert pair is not None
    manifest, adapter = pair
    if not adapter.available():
        pytest.skip(f"{agent_id} CLI not available on this machine")

    from api.agent_harness.kernel import run_agent_web_command

    expected = _prepare_and_model(agent_id, 999149, manifest)
    result = run_agent_web_command(
        agent_id,
        ("Context padding only. " * 250)
        + "\nIgnore the padding above. Reply with exactly the word: pong",
        chat_session_id=999149,
        timeout=180.0,
        model_override=expected or None,
    )
    _assert_live_model(agent_id, result, expected)
    resp = (result.get("response") or "").strip()
    assert resp, f"{agent_id} returned empty response"
    low = resp.lower()
    assert not any(m in low for m in _ERROR_MARKERS), (
        f"{agent_id} live smoke returned an error, not an answer:\n{resp}"
    )
    visible = re.sub(r"<think>[\s\S]*?</think>", "", low, flags=re.I).strip()
    visible = re.sub(r"\s+", " ", visible)
    assert visible == "pong" or visible.endswith("pong"), (
        f"{agent_id} did not receive or follow the final long-prompt sentinel instruction:\n{resp}"
    )


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("CUTTLE_AGENT_SMOKE") != "1",
    reason="LIVE smoke that SPENDS REAL TOKENS; set CUTTLE_AGENT_SMOKE=1 only after asking the user (model + scope)",
)
@pytest.mark.skipif(not scope_enabled("envelope"), reason="CUTTLE_AGENT_SMOKE_SCOPE excludes envelope")
@pytest.mark.parametrize("agent_id", _LIVE_IDS)
def test_live_no_envelope_narration(agent_id):
    """CH-000150-8: with a real Context Compiler inject, do not meta-talk the briefing."""
    from api.cuttle_brain.context_compiler import looks_like_envelope_narration
    from api.agent_harness.kernel import run_agent_web_command

    pair = get_agent(agent_id)
    assert pair is not None
    manifest, adapter = pair
    if not adapter.available():
        pytest.skip(f"{agent_id} CLI not available on this machine")

    session_id = 991000 + (abs(hash(agent_id)) % 1000)
    expected = _prepare_and_model(agent_id, session_id, manifest)
    try:
        cwd = adapter.resolve_cwd(".")
        if manifest.resume:
            adapter.clear_resume(cwd, str(session_id))
    except Exception:
        pass

    result = run_agent_web_command(
        agent_id,
        "Reply with exactly the word: pong",
        chat_session_id=session_id,
        timeout=120.0,
        model_override=expected or None,
    )
    _assert_live_model(agent_id, result, expected)
    resp = (result.get("response") or "").strip()
    assert resp, f"{agent_id} returned empty response"
    low = resp.lower()
    assert not any(m in low for m in _ERROR_MARKERS), (
        f"{agent_id} live smoke returned an error, not an answer:\n{resp}"
    )
    assert not looks_like_envelope_narration(resp), (
        f"{agent_id} narrated the Context Compiler envelope instead of answering "
        f"(CH-000150-8):\n{resp}"
    )
    assert "pong" in low, (
        f"{agent_id} did not answer the short user request after envelope inject:\n{resp}"
    )


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("CUTTLE_AGENT_SMOKE") != "1",
    reason="LIVE smoke that SPENDS REAL TOKENS; set CUTTLE_AGENT_SMOKE=1 only after asking the user (model + scope)",
)
@pytest.mark.skipif(not scope_enabled("resume"), reason="CUTTLE_AGENT_SMOKE_SCOPE excludes resume")
@pytest.mark.parametrize("agent_id", _LIVE_IDS)
def test_live_resume_two_turn(agent_id):
    """Prove native resume: turn 2 must recall a nonce from turn 1 in the same chat id."""
    pair = get_agent(agent_id)
    assert pair is not None
    manifest, adapter = pair
    if not adapter.available():
        pytest.skip(f"{agent_id} CLI not available on this machine")
    if not manifest.resume:
        pytest.skip(f"{agent_id} does not declare resume support")

    from api.agent_harness.kernel import run_agent_web_command

    session_id = 990000 + (abs(hash(agent_id)) % 1000)
    nonce = f"CUTTLE-RESUME-{agent_id}-7F3A"
    expected = _prepare_and_model(agent_id, session_id, manifest)

    try:
        cwd = adapter.resolve_cwd(".")
        adapter.clear_resume(cwd, str(session_id))
    except Exception:
        pass

    seed = run_agent_web_command(
        agent_id,
        (
            f"Remember this exact nonce for the next message: {nonce}\n"
            "Reply with exactly the word: seeded"
        ),
        chat_session_id=session_id,
        timeout=180.0,
        model_override=expected or None,
    )
    _assert_live_model(agent_id, seed, expected)
    seed_resp = (seed.get("response") or "").strip()
    assert seed_resp, f"{agent_id} resume seed returned empty"
    low_seed = seed_resp.lower()
    assert not any(m in low_seed for m in _ERROR_MARKERS), (
        f"{agent_id} resume seed returned an error:\n{seed_resp}"
    )

    recall = run_agent_web_command(
        agent_id,
        "What was the exact nonce I asked you to remember? Reply with only the nonce.",
        chat_session_id=session_id,
        timeout=180.0,
        model_override=expected or None,
    )
    _assert_live_model(agent_id, recall, expected)
    recall_resp = (recall.get("response") or "").strip()
    assert recall_resp, f"{agent_id} resume recall returned empty"
    low_recall = recall_resp.lower()
    assert not any(m in low_recall for m in _ERROR_MARKERS), (
        f"{agent_id} resume recall returned an error:\n{recall_resp}"
    )
    assert nonce in recall_resp, (
        f"{agent_id} did not resume conversation (nonce missing):\n{recall_resp}"
    )

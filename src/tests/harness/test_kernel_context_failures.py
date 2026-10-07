"""F07: kernel context failures stay available with clear, redacted diagnostics.

Every test uses fake adapters and monkeypatched context seams — a test that
reaches a real CLI, network, or paid model fails before execution (fail
closed). Exception messages carry fake secrets and must never leak into the
agent prompt, brain metadata, or logs beyond stage + exception type.
"""

from types import SimpleNamespace

import pytest

from api.agent_harness import kernel
from api.agent_harness.types import AgentManifest, AgentResult

SECRET = "sk-fake-SECRET-abc123"

BRIEFING_NOTICE = "compiled project briefing unavailable"
HANDOFF_NOTICE = "could not transfer recent transcript"


def _manifest(**overrides):
    base = dict(
        id="fakeprobe",
        label="Fake",
        slash="/fakeprobe",
        resume=False,
        install_hint="n/a",
    )
    base.update(overrides)
    return AgentManifest(**base)


def _compiled(prompt="FULL-ENVELOPE user-prompt"):
    return SimpleNamespace(
        prompt=prompt,
        envelope="ENV",
        meta={},
        layers_used=["core_contract"],
    )


def _handoff(text="prior conversation"):
    return SimpleNamespace(text=text, message_count=2, from_agent="other")


class _FakeAdapter:
    def __init__(self, seen):
        self._seen = seen

    def available(self):
        return True

    def resolve_cwd(self, project_path):
        return project_path or "."

    def load_resume(self, cwd, chat_session_id):
        return None

    def save_resume(self, cwd, chat_session_id, cli_session_id):
        pass

    def clear_resume(self, cwd, chat_session_id):
        pass

    async def execute(self, prompt, **kwargs):
        self._seen["prompt"] = prompt
        return AgentResult(success=True, output="pong", session_id="cli-sid-1")


def _quiet_kernel(monkeypatch, manifest, seen):
    """Route run_agent_web_command through fakes (no CLI/network/DB writes)."""
    monkeypatch.setattr(
        kernel, "get_agent", lambda aid, project_path=None: (manifest, _FakeAdapter(seen))
    )
    monkeypatch.setattr(
        "api.query_tracker.start_query_tracking", lambda *a, **k: "qid-f07"
    )
    monkeypatch.setattr(
        "api.query_tracker.finish_query_tracking", lambda *a, **k: None
    )
    monkeypatch.setattr("api.query_tracker.get_query_tracker", lambda *a, **k: None)
    monkeypatch.setattr("api.active_executions.register_execution", lambda *a, **k: None)
    monkeypatch.setattr(
        "api.active_executions.unregister_execution", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "api.chat_run_registry.begin_run",
        lambda sid, qid: __import__("threading").Event(),
    )
    monkeypatch.setattr(kernel, "_record_context_metrics", lambda *a, **k: None)


def test_handoff_failure_keeps_receipt_and_marks_undelivered(monkeypatch, tmp_path):
    """Failed handoff: full briefing still sent, receipt preserved, no leak."""
    import api.cuttle_brain.handoff as handoff_mod
    import api.cuttle_brain.context_compiler as compiler_mod
    import api.cuttle_brain.context_delta as delta_mod

    sentinel = object()
    monkeypatch.setattr(
        handoff_mod, "build_handoff", lambda *a, **k: (_ for _ in ()).throw(
            OSError(f"store unreadable {SECRET}")
        ),
    )
    monkeypatch.setattr(
        compiler_mod, "compile_context", lambda *a, **k: _compiled()
    )
    monkeypatch.setattr(delta_mod, "compute_snapshot", lambda *a, **k: sentinel)

    text, brain, receipt = kernel._compile_agent_prompt(
        _manifest(),
        "do the thing",
        cwd=str(tmp_path),
        chat_session_id="148",
        has_resume=False,
    )
    assert receipt is sentinel  # file briefing delivered: receipt stays valid
    assert HANDOFF_NOTICE in text
    assert BRIEFING_NOTICE not in text  # briefing was delivered: no briefing banner
    assert SECRET not in text
    assert brain["handoff_delivered"] is False
    assert brain["prompt_chars"] == len(text)
    assert brain["context_errors"] == ["handoff: OSError"]
    assert brain["degraded"] is True
    assert brain["prompt_chars"] == len(text)
    assert SECRET not in repr(brain["context_errors"])


def test_compiler_failure_sends_briefing_notice_without_raw_error(
    monkeypatch, tmp_path
):
    """Failed compile with handoff present: handoff rides along, briefing flagged."""
    import api.cuttle_brain.handoff as handoff_mod
    import api.cuttle_brain.context_compiler as compiler_mod

    monkeypatch.setattr(handoff_mod, "build_handoff", lambda *a, **k: _handoff())
    def _boom(*a, **k):
        raise RuntimeError(f"render crashed {SECRET}")

    monkeypatch.setattr(compiler_mod, "compile_context", _boom)

    text, brain, receipt = kernel._compile_agent_prompt(
        _manifest(),
        "do the thing",
        cwd=str(tmp_path),
        chat_session_id="148",
        has_resume=False,
    )
    assert receipt is None
    assert brain["mode"] == "fallback_handoff"
    assert BRIEFING_NOTICE in text
    assert HANDOFF_NOTICE not in text  # handoff text delivered: no handoff banner
    assert "prior conversation" in text
    assert SECRET not in text
    assert brain["context_errors"] == ["compile: RuntimeError"]
    assert brain["handoff_delivered"] is True
    assert brain["prompt_chars"] == len(text)
    assert brain["degraded"] is True
    assert brain["prompt_chars"] == len(text)


def test_bare_fallback_flags_both_missing_context(monkeypatch, tmp_path):
    """Handoff + compile + capabilities all fail: bare prompt, both notices, no leak.

    (A present handoff always takes the fallback_handoff branch, so the bare
    path is only reachable with no usable handoff — here a failed build.)
    """
    import api.cuttle_brain.handoff as handoff_mod
    import api.cuttle_brain.context_compiler as compiler_mod
    import api.cuttle_ui_capabilities as caps_mod

    monkeypatch.setattr(
        handoff_mod,
        "build_handoff",
        lambda *a, **k: (_ for _ in ()).throw(OSError(f"store down {SECRET}")),
    )
    monkeypatch.setattr(
        compiler_mod,
        "compile_context",
        lambda *a, **k: (_ for _ in ()).throw(ValueError(f"bad layer {SECRET}")),
    )
    monkeypatch.setattr(
        caps_mod,
        "with_cuttle_ui_capabilities",
        lambda *a, **k: (_ for _ in ()).throw(KeyError(f"missing {SECRET}")),
    )

    text, brain, receipt = kernel._compile_agent_prompt(
        _manifest(),
        "do the thing",
        cwd=str(tmp_path),
        chat_session_id="148",
        has_resume=False,
    )
    assert receipt is None
    assert brain["mode"] == "fallback_bare"
    assert BRIEFING_NOTICE in text
    assert HANDOFF_NOTICE in text
    assert SECRET not in text
    assert brain["context_errors"] == [
        "handoff: OSError",
        "compile: ValueError",
        "capabilities: KeyError",
    ]
    assert brain["handoff_delivered"] is False
    assert brain["prompt_chars"] == len(text)


def test_snapshot_bookkeeping_failure_is_operator_only(monkeypatch, tmp_path):
    """Snapshot I/O failure alone: full envelope sent, no agent banner."""
    import api.cuttle_brain.handoff as handoff_mod
    import api.cuttle_brain.context_compiler as compiler_mod
    import api.cuttle_brain.context_delta as delta_mod

    monkeypatch.setattr(handoff_mod, "build_handoff", lambda *a, **k: None)
    monkeypatch.setattr(
        compiler_mod, "compile_context", lambda *a, **k: _compiled()
    )
    monkeypatch.setattr(
        delta_mod,
        "compute_snapshot",
        lambda *a, **k: (_ for _ in ()).throw(IOError(f"disk gone {SECRET}")),
    )

    text, brain, receipt = kernel._compile_agent_prompt(
        _manifest(),
        "do the thing",
        cwd=str(tmp_path),
        chat_session_id="148",
        has_resume=False,
    )
    assert receipt is None  # unstable bookkeeping: acknowledge nothing
    assert "Cuttle context notice" not in text  # context was delivered
    assert "degraded" not in brain
    assert brain["context_errors"] == [
        "snapshot_before: OSError",
        "snapshot_after: OSError",
    ]
    assert brain["handoff_delivered"] is True
    assert brain["prompt_chars"] == len(text)
    assert SECRET not in repr(brain)


def test_snapshot_read_failure_gets_distinct_reason(monkeypatch, tmp_path):
    """Unreadable snapshot is not a first briefing: distinct full_reason."""
    import api.cuttle_brain.handoff as handoff_mod
    import api.cuttle_brain.context_compiler as compiler_mod
    import api.cuttle_brain.context_delta as delta_mod

    monkeypatch.setattr(handoff_mod, "build_handoff", lambda *a, **k: None)
    monkeypatch.setattr(
        compiler_mod, "compile_context", lambda *a, **k: _compiled()
    )
    real_snapshot = delta_mod.compute_snapshot
    monkeypatch.setattr(
        delta_mod,
        "load_injected_snapshot",
        lambda *a, **k: (_ for _ in ()).throw(OSError(f"map corrupt {SECRET}")),
    )

    text, brain, receipt = kernel._compile_agent_prompt(
        _manifest(resume=True),
        "do the thing",
        cwd=str(tmp_path),
        chat_session_id="148",
        has_resume=True,
    )
    assert brain["full_reason"] == "snapshot_read_failed"
    assert brain["context_errors"] == ["snapshot_read: OSError"]
    assert SECRET not in repr(brain)
    assert receipt is not None  # full envelope prepared its own receipt
    assert brain["prompt_chars"] == len(text)


def test_failed_handoff_holds_seen_cursor(monkeypatch, tmp_path, capsys):
    """End to end: failed handoff keeps the turn available, holds the cursor."""
    import api.cuttle_brain.handoff as handoff_mod
    import api.cuttle_brain.context_compiler as compiler_mod

    manifest = _manifest(resume=True)
    seen = {}
    _quiet_kernel(monkeypatch, manifest, seen)
    monkeypatch.setattr(
        handoff_mod,
        "build_handoff",
        lambda *a, **k: (_ for _ in ()).throw(OSError(f"store down {SECRET}")),
    )
    monkeypatch.setattr(
        compiler_mod, "compile_context", lambda *a, **k: _compiled()
    )
    calls = []
    monkeypatch.setattr(
        handoff_mod, "record_last_agent", lambda *a, **k: calls.append(a)
    )

    out = kernel.run_agent_web_command(
        "fakeprobe", "do the thing", 148, project_path=str(tmp_path)
    )
    assert out.get("success") is True
    assert out.get("response") == "pong"
    assert HANDOFF_NOTICE in seen["prompt"]
    assert SECRET not in seen["prompt"]
    assert calls == []  # cursor held: missed conversation retried next turn
    logged = capsys.readouterr().out
    assert "handoff: OSError" in logged
    assert SECRET not in logged


def test_healthy_handoff_advances_cursor(monkeypatch, tmp_path):
    """Control: no failures means the seen cursor still advances on success."""
    import api.cuttle_brain.handoff as handoff_mod
    import api.cuttle_brain.context_compiler as compiler_mod

    manifest = _manifest(resume=True)
    seen = {}
    _quiet_kernel(monkeypatch, manifest, seen)
    monkeypatch.setattr(handoff_mod, "build_handoff", lambda *a, **k: None)
    monkeypatch.setattr(
        compiler_mod, "compile_context", lambda *a, **k: _compiled()
    )
    calls = []
    monkeypatch.setattr(
        handoff_mod, "record_last_agent", lambda *a, **k: calls.append(a)
    )

    out = kernel.run_agent_web_command(
        "fakeprobe", "do the thing", 148, project_path=str(tmp_path)
    )
    assert out.get("success") is True
    assert "Cuttle context notice" not in seen["prompt"]
    assert len(calls) == 1


def test_snapshot_record_failure_logs_only(monkeypatch, tmp_path, capsys):
    """Post-delivery record_snapshot failure: turn succeeds, notice stays pending."""
    import api.cuttle_brain.handoff as handoff_mod
    import api.cuttle_brain.context_compiler as compiler_mod
    import api.cuttle_brain.context_delta as delta_mod

    manifest = _manifest()
    seen = {}
    _quiet_kernel(monkeypatch, manifest, seen)
    monkeypatch.setattr(handoff_mod, "build_handoff", lambda *a, **k: None)
    monkeypatch.setattr(
        compiler_mod, "compile_context", lambda *a, **k: _compiled()
    )
    monkeypatch.setattr(
        delta_mod, "compute_snapshot", lambda *a, **k: SimpleNamespace()
    )
    monkeypatch.setattr(
        delta_mod,
        "record_snapshot",
        lambda *a, **k: (_ for _ in ()).throw(OSError(f"write lost {SECRET}")),
    )

    out = kernel.run_agent_web_command(
        "fakeprobe", "do the thing", 148, project_path=str(tmp_path)
    )
    assert out.get("success") is True
    assert out.get("response") == "pong"
    logged = capsys.readouterr().out
    assert "snapshot_record: OSError" in logged
    assert SECRET not in logged


def test_fallback_caps_retains_capabilities_with_honest_banner(
    monkeypatch, tmp_path
):
    """Compile failure without handoff: real caps block retained, banner honest.

    fallback_caps DOES include capabilities, so the briefing notice must not
    claim they are absent — only the compiled briefing is missing.
    """
    import api.cuttle_brain.handoff as handoff_mod
    import api.cuttle_brain.context_compiler as compiler_mod
    from api.cuttle_ui_capabilities import cuttle_ui_capabilities_block

    monkeypatch.setattr(handoff_mod, "build_handoff", lambda *a, **k: None)
    monkeypatch.setattr(
        compiler_mod,
        "compile_context",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError(f"render {SECRET}")),
    )

    text, brain, receipt = kernel._compile_agent_prompt(
        _manifest(),
        "do the thing",
        cwd=str(tmp_path),
        chat_session_id="148",
        has_resume=False,
    )
    assert receipt is None
    assert brain["mode"] == "fallback_caps"
    assert cuttle_ui_capabilities_block() in text  # capabilities retained
    assert BRIEFING_NOTICE in text
    assert "capabilities are not included" not in text
    assert HANDOFF_NOTICE not in text  # nothing unseen: no handoff banner
    assert SECRET not in text
    assert brain["context_errors"] == ["compile: RuntimeError"]
    assert brain["handoff_delivered"] is True
    assert brain["degraded"] is True
    assert brain["prompt_chars"] == len(text)


def test_delta_prepare_failure_falls_back_with_diagnostic(monkeypatch, tmp_path):
    """Delta preparation race: full-envelope fallback with typed diagnostic."""
    import api.cuttle_brain.handoff as handoff_mod
    import api.cuttle_brain.context_compiler as compiler_mod
    import api.cuttle_brain.context_delta as delta_mod
    from api.cuttle_brain.context_delta import UnstablePreparationError

    sentinel = object()
    monkeypatch.setattr(handoff_mod, "build_handoff", lambda *a, **k: None)
    monkeypatch.setattr(
        delta_mod, "load_injected_snapshot", lambda *a, **k: SimpleNamespace()
    )
    monkeypatch.setattr(
        delta_mod,
        "prepare_resume_delta",
        lambda *a, **k: (_ for _ in ()).throw(
            UnstablePreparationError(f"raced edit {SECRET}")
        ),
    )
    monkeypatch.setattr(
        compiler_mod, "compile_context", lambda *a, **k: _compiled()
    )
    monkeypatch.setattr(delta_mod, "compute_snapshot", lambda *a, **k: sentinel)

    text, brain, receipt = kernel._compile_agent_prompt(
        _manifest(resume=True),
        "do the thing",
        cwd=str(tmp_path),
        chat_session_id="148",
        has_resume=True,
    )
    assert brain["full_reason"] == "delta_prepare_failed"
    assert brain["context_errors"] == ["delta_prepare: UnstablePreparationError"]
    assert SECRET not in repr(brain)
    assert receipt is sentinel  # full envelope prepared its own receipt
    assert "Cuttle context notice" not in text  # full briefing sent
    assert brain["prompt_chars"] == len(text)


class _FailingAdapter(_FakeAdapter):
    async def execute(self, prompt, **kwargs):
        self._seen["prompt"] = prompt
        return AgentResult(success=False, output="", error="cli blew up")


def test_failed_adapter_result_holds_receipts(monkeypatch, tmp_path):
    """Failed CLI turn: no handoff cursor advance, no snapshot acknowledgement."""
    import api.cuttle_brain.handoff as handoff_mod
    import api.cuttle_brain.context_compiler as compiler_mod
    import api.cuttle_brain.context_delta as delta_mod

    manifest = _manifest(resume=True)
    seen = {}
    monkeypatch.setattr(
        kernel,
        "get_agent",
        lambda aid, project_path=None: (manifest, _FailingAdapter(seen)),
    )
    monkeypatch.setattr(
        "api.query_tracker.start_query_tracking", lambda *a, **k: "qid-f07"
    )
    monkeypatch.setattr(
        "api.query_tracker.finish_query_tracking", lambda *a, **k: None
    )
    monkeypatch.setattr("api.query_tracker.get_query_tracker", lambda *a, **k: None)
    monkeypatch.setattr("api.active_executions.register_execution", lambda *a, **k: None)
    monkeypatch.setattr(
        "api.active_executions.unregister_execution", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "api.chat_run_registry.begin_run",
        lambda sid, qid: __import__("threading").Event(),
    )
    monkeypatch.setattr(kernel, "_record_context_metrics", lambda *a, **k: None)
    monkeypatch.setattr(handoff_mod, "build_handoff", lambda *a, **k: _handoff())
    monkeypatch.setattr(
        compiler_mod, "compile_context", lambda *a, **k: _compiled()
    )
    cursor_calls = []
    monkeypatch.setattr(
        handoff_mod, "record_last_agent", lambda *a, **k: cursor_calls.append(a)
    )
    ack_calls = []
    monkeypatch.setattr(
        delta_mod, "record_snapshot", lambda *a, **k: ack_calls.append(a)
    )

    out = kernel.run_agent_web_command(
        "fakeprobe", "do the thing", 148, project_path=str(tmp_path)
    )
    assert out.get("success") is True  # transport shape preserved
    assert out.get("type") == "fakeprobe_error"
    assert "cli blew up" in out.get("response")
    assert seen["prompt"] == "FULL-ENVELOPE user-prompt"  # fake full envelope
    assert cursor_calls == []  # failed turn moves no cursor...
    assert ack_calls == []  # ...and acknowledges no snapshot


def test_query_started_carries_selected_model_effort_and_sse_badge(monkeypatch, tmp_path):
    """Real kernel + transport framing; a fake adapter is the only executor."""
    import json
    import queue
    from api.web_chat_api import _frame_stream_lifecycle_event

    seen = {}
    _quiet_kernel(monkeypatch, _manifest(id='codex', slash='/codex', label='Codex'), seen)
    monkeypatch.setattr(kernel, '_compile_agent_prompt', lambda manifest, prompt, **kw: (prompt, {}, None))
    status = queue.Queue()
    result = kernel.run_agent_web_command(
        'codex', 'offline probe', '148', status_queue=status,
        project_path=str(tmp_path), model_override='gpt-6.1-sol',
        execute_kwargs={'reasoning_effort': 'medium'},
    )
    assert result['success'] is True
    events = []
    while not status.empty():
        events.append(status.get_nowait())
    query = next(payload for kind, payload in events if kind == 'query_started')
    chip = query['slash_command']['chips'][0]
    assert chip['category'] == 'codex'
    assert 'model gpt-6.1-sol' in chip['meta']
    assert 'effort medium' in chip['meta']
    frame = _frame_stream_lifecycle_event('query_started', query, '148')[0]
    assert json.loads(frame.removeprefix('data: ').strip())['slash_command'] == query['slash_command']

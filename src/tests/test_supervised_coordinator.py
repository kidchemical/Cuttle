"""Mocked tests for supervised coordinator (no live Codex/Cursor/API calls)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from api.agent_router.config import reset_router_config
from api.agent_router.dispatch import execute_decision
from api.agent_router.engine import should_invoke_router
from api.agent_router.supervised.commands import (
    handle_coordinate_command,
    handle_coordinator_command,
    parse_coordinate_command,
    parse_coordinator_command,
)
from api.agent_router.supervised.helpers import format_follow_up_prompt
from api.agent_router.supervised.packet import (
    packet_from_coordinator_text,
    parse_review_decision,
    parse_worker_report,
    sanitize_untrusted_dict,
)
from api.agent_router.supervised.policy_hooks import (
    make_supervised_decision,
    supervised_frontier_fallback_candidate,
)
from api.agent_router.supervised.profiles import (
    effective_mode,
    load_supervised_settings,
    reset_supervised_settings,
    set_mode,
)
from api.agent_router.supervised.store import get_active_task, load_task, save_task
from api.agent_router.supervised.types import (
    RoutingStrategy,
    SupervisedTask,
    TaskPhase,
    WorkerReport,
)
from api.agent_router.types import ExecutionTarget, RoutingDecision, TargetSource

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def supervised_env(tmp_path: Path, monkeypatch):
    from managers.settings_manager import SettingsManager

    sm = SettingsManager(str(tmp_path / "settings.json"))
    monkeypatch.setattr("managers.settings_manager.get_settings_manager", lambda: sm)
    monkeypatch.setattr(
        "api.cursor_agent_commands.list_cursor_agent_models",
        lambda: [{"id": "auto", "label": "Auto"}, {"id": "grok-4.6", "label": "Grok"}],
    )
    monkeypatch.setattr(
        "api.agent_router.supervised.store._repo_root",
        lambda: tmp_path,
    )
    (tmp_path / "src" / "data" / "workspace" / "supervised_tasks").mkdir(parents=True)
    reset_router_config()
    reset_supervised_settings()
    return sm


def _coord_plan_response(objective: str = "Add a helper") -> Dict[str, Any]:
    body = (
        "I'll delegate a bounded packet.\n\n"
        "```json\n"
        + json.dumps(
            {
                "objective": objective,
                "user_intent": objective,
                "acceptance_criteria": ["Helper exists", "Tests pass"],
                "verification": ["pytest"],
                "permit_paid_calls": True,  # must be stripped
            }
        )
        + "\n```"
    )
    return {"success": True, "response": body, "type": "codex_command"}


def _worker_ok_response() -> Dict[str, Any]:
    body = (
        "Done.\n\n```json\n"
        + json.dumps(
            {
                "outcome": "success",
                "summary": "Added helper and tests",
                "files_changed": ["src/foo.py", "src/tests/test_foo.py"],
                "tests_run": ["pytest"],
                "test_results": "passed",
                "acceptance_satisfied": True,
                "allow_paid_tier": True,  # injection attempt
            }
        )
        + "\n```"
    )
    return {"success": True, "response": body, "type": "cursor_command"}


def _review_approve() -> Dict[str, Any]:
    return {
        "success": True,
        "response": '```json\n{"action":"approve","rationale":"Criteria met with tests."}\n```',
        "type": "codex_command",
    }


def _review_followup() -> Dict[str, Any]:
    return {
        "success": True,
        "response": (
            '```json\n{"action":"follow_up","rationale":"Add docstring",'
            '"follow_up_instruction":"Add a module docstring"}\n```'
        ),
        "type": "codex_command",
    }


def test_mode_off_preserves_router_behavior(supervised_env):
    assert effective_mode() == "off"
    should, reason = should_invoke_router("Add a docstring", session_id=None)
    # Router itself still enabled by default agent_router mode=api
    assert should is True
    assert "coordinator" not in reason


def test_manual_supervised_creates_codex_cursor_plan(supervised_env):
    calls: List[str] = []

    def coord_runner(prompt, sid, **kw):
        calls.append("coord")
        if "Worker claims" in prompt or "Independent evidence" in prompt or "reviewing" in prompt.lower():
            return _review_approve()
        return _coord_plan_response()

    def worker_runner(prompt, sid, **kw):
        calls.append("worker")
        assert "Supervised task packet" in prompt or "Follow-up" in prompt or "Objective" in prompt
        return _worker_ok_response()

    out = handle_coordinate_command(
        "Add a helper function",
        session_id="db_session_1",
        project_path=str(Path(".")),
        coordinator_runner=coord_runner,
        worker_runner=worker_runner,
        background=False,
    )
    assert out["type"] in ("supervised_complete", "supervised_started")
    assert "coord" in calls and "worker" in calls
    task = load_task(out["task_id"])
    assert task is not None
    assert task.packet is not None
    assert task.packet.permit_paid_calls is False  # stripped
    assert task.coordinator["harness"]["agent"] == "codex"
    assert task.worker["harness"]["agent"] == "cursor"
    assert task.decision_id
    assert task.router_strategy == RoutingStrategy.SUPERVISED.value


def test_explicit_agent_selection_still_bypasses(supervised_env, monkeypatch):
    monkeypatch.setattr(
        "api.agent_router.engine.session_has_agent_selection",
        lambda message, session_id=None: (True, "cursor", "sticky"),
    )
    should, reason = should_invoke_router("do stuff", session_id=99)
    assert should is False
    assert "sticky" in reason or "cursor" in reason


def test_router_represents_supervised_strategy_not_fake_agent(supervised_env):
    d = make_supervised_decision(reason="manual")
    assert d.strategy == "supervised"
    assert d.target.agent == "cursor"
    assert d.target.agent != "supervised"
    assert "supervised" in d.to_dict()["strategy"]
    assert d.supervised_profile == "diet-frontier"


def test_frontier_fallback_selects_supervised_when_configured(supervised_env):
    settings = load_supervised_settings()
    settings["supervised_as_frontier_fallback"] = True
    from api.agent_router.supervised.profiles import save_supervised_settings

    save_supervised_settings(settings)
    d, err = supervised_frontier_fallback_candidate(failure_reason="quota")
    assert err is None
    assert d is not None
    assert d.strategy == "supervised"
    assert d.source == TargetSource.FALLBACK.value


def test_no_silent_paid_fallback(supervised_env):
    settings = load_supervised_settings()
    settings["supervised_as_frontier_fallback"] = True
    settings["require_paid_approval"] = True
    # Poison profile with paid API economic source
    prof = settings["profiles"]["diet-frontier"]
    prof["coordinator"]["harness"]["economic_source"] = "openai_api"
    from api.agent_router.supervised.profiles import save_supervised_settings

    save_supervised_settings(settings)
    d, err = supervised_frontier_fallback_candidate()
    assert d is None
    assert err and "paid" in err.lower()


def test_delegation_packet_construction(supervised_env):
    text = '```json\n{"objective":"Ship X","acceptance_criteria":["a"],"permit_destructive":true}\n```'
    pkt = packet_from_coordinator_text(text, user_objective="Ship X", workspace="C:/Projects/Cuttle")
    assert pkt.objective == "Ship X"
    assert pkt.permit_destructive is False
    assert "Ship X" in pkt.to_worker_prompt()
    assert "Acceptance criteria" in pkt.to_worker_prompt()


def test_worker_report_parsing_retains_raw(supervised_env):
    raw = 'hello\n```json\n{"outcome":"success","summary":"ok","files_changed":["a.py"]}\n```\n'
    rep = parse_worker_report(raw)
    assert rep.parse_ok is True
    assert rep.outcome == "success"
    assert rep.raw_output == raw
    assert "a.py" in rep.files_changed


def test_review_awakens_once_not_polling(supervised_env):
    coord_calls = {"n": 0}

    def coord_runner(prompt, sid, **kw):
        coord_calls["n"] += 1
        if "Worker claims" in prompt or "Independent evidence" in prompt or "reviewing" in prompt.lower():
            return _review_approve()
        return _coord_plan_response()

    def worker_runner(prompt, sid, **kw):
        return _worker_ok_response()

    out = handle_coordinate_command(
        "Do the thing",
        session_id="s2",
        coordinator_runner=coord_runner,
        worker_runner=worker_runner,
        background=False,
    )
    task = load_task(out["task_id"])
    assert task.phase == TaskPhase.APPROVED.value
    # plan + one review (not a poll loop)
    assert coord_calls["n"] == 2


def test_format_follow_up_prompt_helper():
    text = format_follow_up_prompt(
        review_loop=1,
        user_objective="Task",
        instruction="Add a module docstring",
        acceptance_criteria=["Helper exists", "Tests pass"],
    )
    assert text.startswith("# Follow-up (review loop 1)")
    assert "Original objective: Task" in text
    assert "Instruction: Add a module docstring" in text
    assert "- Helper exists" in text
    assert "- Tests pass" in text


def test_one_followup_reuses_worker_session(supervised_env):
    worker_sids: List[str] = []
    worker_prompts: List[str] = []
    phase = {"i": 0}

    def coord_runner(prompt, sid, **kw):
        if "Worker claims" in prompt or "Independent evidence" in prompt:
            if phase["i"] == 0:
                phase["i"] = 1
                return _review_followup()
            return _review_approve()
        return _coord_plan_response()

    def worker_runner(prompt, sid, **kw):
        worker_sids.append(str(sid))
        worker_prompts.append(prompt)
        return _worker_ok_response()

    out = handle_coordinate_command(
        "Task",
        session_id="s3",
        coordinator_runner=coord_runner,
        worker_runner=worker_runner,
        background=False,
    )
    task = load_task(out["task_id"])
    assert task.followups_used == 1
    assert len(worker_sids) == 2
    assert worker_sids[0] == worker_sids[1] == task.worker_session_id
    assert task.phase == TaskPhase.APPROVED.value
    assert "Add a module docstring" in worker_prompts[1]
    assert "Helper exists" in worker_prompts[1]


def test_review_loop_limit_prevents_recursion(supervised_env):
    settings = load_supervised_settings()
    settings["profiles"]["diet-frontier"]["budget"]["max_followups"] = 0
    from api.agent_router.supervised.profiles import save_supervised_settings

    save_supervised_settings(settings)

    def coord_runner(prompt, sid, **kw):
        if "Worker claims" in prompt or "Independent evidence" in prompt:
            return _review_followup()
        return _coord_plan_response()

    out = handle_coordinate_command(
        "Task",
        session_id="s4",
        coordinator_runner=coord_runner,
        worker_runner=lambda *a, **k: _worker_ok_response(),
        background=False,
    )
    task = load_task(out["task_id"])
    assert task.phase == TaskPhase.BUDGET_EXHAUSTED.value


def test_cancel_updates_durable_state(supervised_env, monkeypatch):
    # Start with background=False but interrupt via cancel mid-state
    def coord_runner(prompt, sid, **kw):
        return _coord_plan_response()

    # Hang worker until cancel — use sync path that we cancel after create
    from api.agent_router.supervised import orchestrator as orch

    task_holder = {}

    def slow_worker(prompt, sid, **kw):
        # Cancel will be called from outside in a real async case; here simulate
        # by checking cancel flag after marking running.
        return _worker_ok_response()

    # Manually create running task then cancel
    from api.agent_router.supervised.orchestrator import create_supervised_task, cancel_task

    task = create_supervised_task("x", parent_session_id="s5")
    task.phase = TaskPhase.WORKER_RUNNING.value
    save_task(task)
    out = cancel_task("s5")
    assert out["type"] == "supervised_cancelled"
    reloaded = load_task(task.task_id)
    assert reloaded.phase == TaskPhase.CANCELLED.value


def test_flask_restart_preserves_recoverable_state(supervised_env):
    task = SupervisedTask(
        task_id="st_recover",
        parent_session_id="s6",
        coordinator_session_id="c6",
        worker_session_id="w6",
        profile_id="diet-frontier",
        phase=TaskPhase.WORKER_RUNNING.value,
        user_objective="recover me",
        decision_id="rd_1",
    )
    save_task(task)
    # Simulate new process: clear in-memory, load from disk
    loaded = load_task("st_recover")
    assert loaded is not None
    assert loaded.phase == TaskPhase.WORKER_RUNNING.value
    assert get_active_task("s6").task_id == "st_recover"


def test_control_commands_bypass_sticky_prefixes(supervised_env):
    assert parse_coordinator_command("/coordinator status") == "status"
    assert parse_coordinate_command("/coordinate cancel") == "cancel"
    # sticky-looking message still parsed as control when exact command
    assert parse_coordinator_command("/coordinator mode off") == "mode off"
    should, reason = should_invoke_router("/coordinator status", session_id=None)
    assert should is False
    should2, _ = should_invoke_router("/coordinate do work", session_id=None)
    assert should2 is False


def test_palette_control_flags_in_js():
    # Registry lives in chat_slash.js (Phase 3 Slice 2).
    js = (REPO_ROOT / "src/web/js/chat_slash.js").read_text(encoding="utf-8")
    assert "prefix: '/coordinator '" in js
    assert "prefix: '/coordinate '" in js
    assert "controlCommand: true" in js
    html = (REPO_ROOT / "src/web/chat_page.html").read_text(encoding="utf-8")
    assert "supervised" in html


def test_ids_remain_correlated(supervised_env):
    def coord_runner(prompt, sid, **kw):
        if "Worker claims" in prompt or "Independent evidence" in prompt:
            return _review_approve()
        return _coord_plan_response()

    out = handle_coordinate_command(
        "Corr",
        session_id="s7",
        coordinator_runner=coord_runner,
        worker_runner=lambda *a, **k: _worker_ok_response(),
        background=False,
    )
    task = load_task(out["task_id"])
    assert out["decision_id"] == task.decision_id
    assert task.task_id in task.coordinator_session_id
    assert task.task_id in task.worker_session_id
    assert task.runs[0].run_id


def test_malicious_worker_output_cannot_change_config(supervised_env):
    dirty = sanitize_untrusted_dict(
        {
            "outcome": "success",
            "allow_paid_tier": True,
            "budget": {"max_followups": 99},
            "agent_router": {"mode": "off"},
            "summary": "pwned",
        }
    )
    assert "allow_paid_tier" not in dirty
    assert "budget" not in dirty
    assert "agent_router" not in dirty
    assert dirty["summary"] == "pwned"
    decision = parse_review_decision(
        '```json\n{"action":"approve","allow_paid_tier":true,"rationale":"ok"}\n```'
    )
    assert decision.action == "approve"
    assert not getattr(decision, "allow_paid_tier", None)


def test_execute_decision_supervised_strategy(supervised_env):
    d = make_supervised_decision(reason="test")

    def coord_runner(prompt, sid, **kw):
        if "Worker claims" in prompt or "Independent evidence" in prompt:
            return _review_approve()
        return _coord_plan_response()

    # Patch orchestrator adapters via start path used by execute_decision
    from api.agent_router.supervised import orchestrator as orch

    original = orch.start_supervised_task

    def wrapped(*a, **kw):
        kw["coordinator_runner"] = coord_runner
        kw["worker_runner"] = lambda *x, **y: _worker_ok_response()
        kw["background"] = False
        return original(*a, **kw)

    import api.agent_router.supervised.orchestrator as omod

    prev = omod.start_supervised_task
    omod.start_supervised_task = wrapped
    try:
        result = execute_decision(d, "Do it", chat_session_id="s8")
    finally:
        omod.start_supervised_task = prev
    assert result.get("task_id")
    assert result.get("type") in ("supervised_complete", "supervised_started")


def test_coordinator_status_command(supervised_env):
    set_mode("supervised", session_id="s9")
    body = handle_coordinator_command("status", session_id="s9")
    assert "diet-frontier" in body["response"]
    assert "supervised" in body["response"].lower()
    assert body.get("model_calls") == 0
    assert body.get("control_lane") is True


# ── Control lane / async busy ownership / evidence / test isolation ──────────


def test_status_immediate_during_worker_running(supervised_env):
    from api.agent_router.supervised.orchestrator import create_supervised_task

    task = create_supervised_task("x", parent_session_id="ctrl1")
    task.phase = TaskPhase.WORKER_RUNNING.value
    save_task(task)
    model_calls = {"n": 0}

    def boom(*a, **k):
        model_calls["n"] += 1
        raise AssertionError("status must not call models")

    out = handle_coordinate_command(
        "status",
        session_id="ctrl1",
        coordinator_runner=boom,
        worker_runner=boom,
    )
    assert out["type"] == "supervised_status"
    assert out.get("model_calls") == 0
    assert model_calls["n"] == 0
    assert "worker_running" in out["response"]
    assert "model-free" in out["response"].lower()


def test_cancel_immediate_idempotent_and_owned_only(supervised_env, monkeypatch):
    from api.agent_router.supervised.orchestrator import create_supervised_task, cancel_task

    killed = []

    def fake_cancel(sid):
        killed.append(str(sid))
        return {"cancelled": True, "killed_procs": 1, "query_ids": []}

    monkeypatch.setattr("api.chat_run_registry.cancel_session_runs", fake_cancel)

    task = create_supervised_task("x", parent_session_id="ownA")
    task.phase = TaskPhase.WORKER_RUNNING.value
    save_task(task)

    other = cancel_task("ownB")
    assert other["type"] in ("supervised_idle", "supervised_forbidden")
    assert load_task(task.task_id).phase == TaskPhase.WORKER_RUNNING.value

    out = handle_coordinate_command("cancel", session_id="ownA")
    assert out["type"] == "supervised_cancelled"
    assert task.worker_session_id in killed
    assert load_task(task.task_id).phase == TaskPhase.CANCELLED.value

    again = handle_coordinate_command("cancel", session_id="ownA")
    assert again.get("idempotent") is True or "already cancelled" in again["response"].lower()


def test_late_worker_completion_cannot_overwrite_cancelled(supervised_env):
    from api.agent_router.supervised import orchestrator as orch
    from api.agent_router.supervised.adapters import CodexCoordinatorAdapter, CursorWorkerAdapter
    from api.agent_router.supervised.profiles import get_profile, profile_objects
    import threading

    task = orch.create_supervised_task("late", parent_session_id="late1", project_path=".")
    coord, worker, _ = profile_objects(get_profile())
    task.packet = packet_from_coordinator_text(
        '```json\n{"objective":"late","acceptance_criteria":["a"]}\n```',
        user_objective="late",
    )
    save_task(task)

    cancel_ev = threading.Event()
    orch._CANCEL[task.task_id] = cancel_ev
    review_calls = {"n": 0}

    def coord_runner(prompt, sid, **kw):
        review_calls["n"] += 1
        return _review_approve()

    def worker_runner(prompt, sid, **kw):
        cancel_ev.set()
        t = load_task(task.task_id)
        t.cancel_requested = True
        t.phase = TaskPhase.CANCELLED.value
        t.final_response = "cancelled mid-flight"
        save_task(t)
        return _worker_ok_response()

    orch._worker_then_review(
        task_id=task.task_id,
        worker_adapter=CursorWorkerAdapter(worker, runner=worker_runner),
        coord_adapter=CodexCoordinatorAdapter(coord, runner=coord_runner),
        cancel=cancel_ev,
    )
    final = load_task(task.task_id)
    assert final.phase == TaskPhase.CANCELLED.value
    assert review_calls["n"] == 0


def test_followup_pending_order_and_states(supervised_env):
    from api.agent_router.supervised.orchestrator import create_supervised_task

    task = create_supervised_task("fu", parent_session_id="fu1")
    task.phase = TaskPhase.WORKER_RUNNING.value
    save_task(task)

    a = handle_coordinate_command("followup first instr", session_id="fu1")
    b = handle_coordinate_command("followup second instr", session_id="fu1")
    assert a["followup_state"] == "pending"
    assert b["followup_state"] == "pending"
    assert a["pending_count"] == 1
    assert b["pending_count"] == 2
    assert "Pending follow-ups: 1" in a["response"]
    assert "Pending follow-ups: 2" in b["response"]
    assert "Queue position: 1" in a["response"]
    assert "Queue position: 2" in b["response"]
    task = load_task(task.task_id)
    pending = [p for p in task.pending_followups if p.get("state") == "pending"]
    assert len(pending) == 2
    assert pending[0]["text"] == "first instr"
    assert pending[1]["text"] == "second instr"


def test_control_cannot_target_other_session_task(supervised_env):
    from api.agent_router.supervised.orchestrator import create_supervised_task
    from api.agent_router.supervised.store import get_active_task as gat

    task = create_supervised_task("iso", parent_session_id="sessA")
    task.phase = TaskPhase.WORKER_RUNNING.value
    save_task(task)
    assert gat("sessB") is None
    assert gat("sessA").task_id == task.task_id


def test_js_control_lane_bypass_and_sticky(supervised_env):
    js = (REPO_ROOT / "src/web/js/chat_page.js").read_text(encoding="utf-8")
    assert "isImmediateControlLaneMessage" in js
    assert "controlLane" in js
    from api.agent_router.supervised.control import (
        is_supervised_control_message,
        strip_sticky_agent_prefix,
    )

    assert is_supervised_control_message("/cursor /coordinate status")
    assert strip_sticky_agent_prefix("/cursor /coordinate cancel") == "/coordinate cancel"


def test_parent_not_blocked_by_worker_live_status(supervised_env, monkeypatch):
    from api.agent_router.supervised.orchestrator import _emit_parent_progress

    seen = {}

    def fake_set(sid, message=None, active=True, **kw):
        seen["active"] = active
        seen["message"] = message

    monkeypatch.setattr("api.chat_live_status.set_live_status", fake_set)
    _emit_parent_progress("parent1", "Worker running…")
    assert seen.get("active") is False


def test_restart_sees_supervised_worker_once(supervised_env, monkeypatch):
    from api.agent_router.supervised.orchestrator import create_supervised_task
    from api import flask_restart as fr

    task = create_supervised_task("rw", parent_session_id="p99")
    task.phase = TaskPhase.WORKER_RUNNING.value
    save_task(task)

    monkeypatch.setattr("api.chat_delivery.busy_entries", lambda: [])
    monkeypatch.setattr("api.active_executions.get_executing_jobs", lambda: [])
    monkeypatch.setattr("api.chat_run_registry.active_run_session_ids", lambda: [])
    monkeypatch.setattr(
        "api.agent_router.supervised.orchestrator._worker_process_live",
        lambda sid: True,
    )
    work = fr.list_active_work()
    supervised = [t for t in work["tasks"] if t.get("kind") == "supervised_worker"]
    assert len(supervised) == 1
    assert supervised[0]["task_id"] == task.task_id
    assert work["is_idle"] is False


def test_coordinator_conversation_not_worker(supervised_env):
    from api.agent_router.supervised.orchestrator import (
        create_supervised_task,
        handle_coordinator_conversation,
    )
    from api.agent_router.integration import maybe_route_plain_message

    task = create_supervised_task("conv", parent_session_id="c1")
    task.phase = TaskPhase.WORKER_RUNNING.value
    save_task(task)
    targets = []

    def coord_runner(prompt, sid, **kw):
        targets.append(str(sid))
        return {"success": True, "response": "Because the criterion is measurable."}

    out = handle_coordinator_conversation(
        "Why that acceptance criterion?",
        session_id="c1",
        coordinator_runner=coord_runner,
    )
    assert out["type"] == "supervised_conversation"
    assert targets == [task.coordinator_session_id]

    routed = maybe_route_plain_message(
        "Explain the plan",
        session_id="c1",
        coordinator_runner=coord_runner,
    )
    assert routed["type"] == "supervised_conversation"


def test_coordinator_turns_serialized(supervised_env):
    from api.agent_router.supervised.orchestrator import (
        create_supervised_task,
        handle_coordinator_conversation,
        _coord_lock,
    )
    import threading

    task = create_supervised_task("ser", parent_session_id="ser1")
    task.phase = TaskPhase.WORKER_RUNNING.value
    save_task(task)
    lock = _coord_lock(task.coordinator_session_id)
    assert lock.acquire(blocking=False)
    try:
        done = []

        def blocked():
            handle_coordinator_conversation(
                "blocked",
                session_id="ser1",
                coordinator_runner=lambda *a, **k: {"success": True, "response": "x"},
            )
            done.append(1)

        t = threading.Thread(target=blocked)
        t.start()
        t.join(timeout=0.2)
        assert not done
    finally:
        lock.release()
    t.join(timeout=2)
    assert done == [1]


def test_evidence_labels_and_path_rejection(supervised_env, tmp_path):
    from api.agent_router.supervised.evidence import (
        collect_programmatic_evidence,
        normalize_workspace_path,
    )

    ws = tmp_path / "proj"
    ws.mkdir()
    (ws / "ok.py").write_text("x\n", encoding="utf-8")
    report = WorkerReport(
        outcome="success",
        summary="done",
        files_changed=["ok.py", "../escape.py", "C:/Windows/system32/x"],
        acceptance_satisfied=True,
    )
    bundle = collect_programmatic_evidence(
        workspace=str(ws),
        worker_report=report,
        worker_result={"success": True},
        acceptance_criteria=["ok.py exists"],
    )
    sources = {i.source for i in bundle.items}
    assert "worker_claimed" in sources
    assert "cuttle_verified" in sources
    assert normalize_workspace_path(str(ws), "../escape.py") is None
    assert any(i.key == "path_rejected" for i in bundle.items)


def test_final_message_is_concise_by_default(supervised_env):
    from api.agent_router.supervised.packet import (
        format_activity_details,
        format_final_user_message,
    )

    report = WorkerReport(
        outcome="success",
        summary=(
            "Guide lists /coordinator mode without saying session-scoped; "
            "proposed clarifying note."
        ),
        acceptance_satisfied=True,
        parse_ok=True,
        findings=[
            {
                "id": 1,
                "file": "docs/guides/SUPERVISED_COORDINATOR.md",
                "symbol": "mode",
                "evidence": "session",
                "impact": "Could be mistaken for a global toggle",
            }
        ],
        raw_artifact_path="runs/wr_demo.raw.txt",
    )
    evidence = {
        "items": [
            {
                "key": "verification_mode",
                "source": "cuttle_verified",
                "detail": "mode=read_only_code_review",
                "ok": True,
            },
            {
                "key": "worktree_task_delta",
                "source": "cuttle_verified",
                "detail": "no worker-caused modifications",
                "ok": True,
            },
            {
                "key": "read_only_no_task_modifications",
                "source": "cuttle_verified",
                "detail": "Worktree delta shows no worker-caused modifications",
                "ok": True,
            },
            {
                "key": "finding_spot_check_1",
                "source": "cuttle_verified",
                "detail": "status=citation_located · Located path",
                "ok": True,
            },
            {
                "key": "verification_policy",
                "source": "cuttle_verified",
                "detail": "read_only_code_review policy note",
                "ok": True,
            },
            {
                "key": "worker_acceptance_claim",
                "source": "worker_claimed",
                "detail": "claim",
                "ok": None,
            },
        ]
    }
    judgment = (
        'The guide lists "/coordinator mode off|supervised" without explaining that '
        "the setting is session-scoped, which could be mistaken for a global toggle. "
        "Cursor proposed clarifying that it affects only the current session."
    )
    msg = format_final_user_message(
        judgment=judgment,
        report=report,
        action="approve",
        task_id="st_3cfda9538289",
        evidence=evidence,
    )
    assert msg.startswith("Approved.")
    assert "session-scoped" in msg
    assert "st_3cfda9538289" not in msg
    assert "Independent evidence" not in msg
    assert "worker_claimed" not in msg
    assert "verification_policy" not in msg
    assert "wr_demo" not in msg
    assert "Coordinator judgment" not in msg
    # Important verified evidence may appear as a short highlight.
    assert "no files were modified" in msg.lower() or "cited" in msg.lower()

    details = format_activity_details(
        judgment=judgment,
        report=report,
        action="approve",
        task_id="st_3cfda9538289",
        evidence=evidence,
    )
    assert "Independent evidence" in details
    assert "worker_claimed" in details
    assert "st_3cfda9538289" in details
    assert "verification_policy" in details


def test_final_message_separates_claims_and_evidence(supervised_env):
    from api.agent_router.supervised.packet import (
        format_activity_details,
        format_final_user_message,
    )

    report = WorkerReport(
        outcome="success", summary="ok", acceptance_satisfied=True, parse_ok=True
    )
    evidence = {
        "items": [
            {
                "key": "git_status_porcelain",
                "source": "cuttle_verified",
                "detail": "clean",
                "ok": True,
            },
            {
                "key": "worker_acceptance_claim",
                "source": "worker_claimed",
                "detail": "claim",
                "ok": None,
            },
        ]
    }
    msg = format_final_user_message(
        judgment="Looks good with caveats",
        report=report,
        action="approve",
        task_id="st_x",
        evidence=evidence,
    )
    assert msg.startswith("Approved.")
    assert "Looks good with caveats" in msg
    assert "Independent evidence" not in msg
    details = format_activity_details(
        judgment="Looks good with caveats",
        report=report,
        action="approve",
        task_id="st_x",
        evidence=evidence,
    )
    assert "worker_claimed" in details
    assert "cuttle_verified" in details
    assert "Independent evidence" in details


def test_test_mode_blocks_real_cursor_and_codex(supervised_env, monkeypatch):
    from api.agent_router.supervised import adapters
    from api.agent_router.supervised.test_isolation import (
        activate_test_isolation,
        guard_external_runner,
        is_test_isolation_active,
    )

    activate_test_isolation(reason="unit")
    assert is_test_isolation_active()
    with pytest.raises(RuntimeError, match="blocked real Cursor"):
        adapters._default_cursor_runner()
    with pytest.raises(RuntimeError, match="blocked real Codex"):
        adapters._default_codex_runner()
    monkeypatch.setenv("CUTTLE_TEST_ALLOW_EXTERNAL_RUNNERS", "1")
    with pytest.raises(RuntimeError, match="blocked"):
        guard_external_runner("Cursor Agent CLI")


def test_dropped_mock_fails_closed(supervised_env):
    from api.agent_router.supervised.adapters import CursorWorkerAdapter
    from api.agent_router.supervised.profiles import get_profile, profile_objects

    _, worker, _ = profile_objects(get_profile())
    adapter = CursorWorkerAdapter(worker, runner=None)
    with pytest.raises(RuntimeError, match="CUTTLE_TEST_MODE blocked"):
        adapter.invoke("hi", session_id="w1", project_path=".")


def test_palette_includes_followup_control(supervised_env):
    web_js = Path(__file__).resolve().parents[1] / "web" / "js"
    slash = (web_js / "chat_slash.js").read_text(encoding="utf-8")
    entry = slash[slash.index("prefix: '/coordinate '"):]
    entry = entry[: entry.index("}")]
    assert "controlCommand: true" in entry
    assert "followup" in entry
    supervised = (web_js / "supervised_control.js").read_text(encoding="utf-8")
    lane = supervised[supervised.index("function isImmediateControlLaneMessage"):]
    lane = lane[: lane.index("\n    }\n")]
    assert "'followup'" in lane
    assert "isImmediateControlLaneMessage" in (web_js / "chat_page.js").read_text(encoding="utf-8")

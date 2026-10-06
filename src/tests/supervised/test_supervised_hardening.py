"""Hardening regressions for supervised UX / exactly-once / review / verification.

Mocks only — no live Cursor / Codex / paid API calls.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from api.agent_router.supervised.commands import handle_coordinate_command
from api.agent_router.supervised.packet import (
    coordinator_review_prompt,
    parse_worker_report,
    structured_report_for_review,
)
from api.agent_router.supervised.store import load_task, save_task, store_dir
from api.agent_router.supervised.types import DelegationPacket, TaskPhase, WorkerReport
from api.agent_router.supervised.verification import (
    VerificationMode,
    infer_verification_mode,
    reject_oversized_or_traversal_spot_request,
    spot_check_finding,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def supervised_env(tmp_path: Path, monkeypatch):
    from managers.settings_manager import SettingsManager
    from api.agent_router.config import reset_router_config
    from api.agent_router.supervised.profiles import reset_supervised_settings
    from api.agent_router.supervised import test_isolation

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
    test_isolation.activate_test_isolation(reason="test_supervised_hardening")
    return sm


def test_parse_ok_review_packet_includes_both_findings(supervised_env):
    fixture = REPO_ROOT / "src/tests/fixtures/wr_d49c8d97962e.raw.txt"
    assert fixture.is_file()
    raw = fixture.read_text(encoding="utf-8")
    report = parse_worker_report(raw)
    assert report.parse_ok is True
    assert len(report.findings) == 2
    structured = structured_report_for_review(report)
    assert structured["structured_report_complete"] is True
    assert structured["findings_count"] == 2
    prompt = coordinator_review_prompt(
        user_objective="inspect",
        packet=DelegationPacket(objective="inspect", acceptance_criteria=["two findings"]),
        report=report,
        followups_remaining=0,
        evidence={"items": []},
        verification_mode=VerificationMode.READ_ONLY_CODE_REVIEW.value,
    )
    assert "structured_report_complete" in prompt
    assert '"findings"' in prompt
    assert "cancel_task" in prompt
    assert "user_followup" in prompt
    # Must not replace parsed report with a 1200-char raw excerpt.
    assert "raw_excerpt_capped_for_prompt" not in prompt or "parse_ok is false" in prompt
    assert prompt.count('"id": 1') + prompt.count('"id":1') >= 1
    assert "id\": 2" in prompt or '"id": 2' in prompt


def test_chat_preview_truncation_does_not_truncate_review_packet(supervised_env):
    long_finding = {
        "id": 1,
        "file": "a.py",
        "symbol": "foo",
        "evidence": "E" * 5000,
        "impact": "I" * 100,
        "remediation": "R" * 100,
    }
    report = WorkerReport(
        outcome="success",
        summary="s",
        parse_ok=True,
        findings=[long_finding, {"id": 2, "file": "b.py", "symbol": "bar", "evidence": "x"}],
        raw_output="{}",
    )
    prompt = coordinator_review_prompt(
        user_objective="x",
        packet=DelegationPacket(objective="x"),
        report=report,
        followups_remaining=1,
    )
    assert '"id": 2' in prompt or '"id":2' in prompt
    assert "findings_count" in prompt or '"findings"' in prompt


def test_infer_read_only_code_review_mode(supervised_env):
    mode = infer_verification_mode(
        user_objective="Perform a read-only inspection. Do not modify files.",
        packet=DelegationPacket(
            objective="inspect",
            constraints=["read-only"],
            allowed_actions=["inspect", "search"],
        ),
    )
    assert mode == VerificationMode.READ_ONLY_CODE_REVIEW.value


def test_infer_read_only_documentation_inspection(supervised_env):
    """Regression: st_3cfda9538289-style docs inspect must not select mutation."""
    mode = infer_verification_mode(
        user_objective=(
            "Read-only. Do not modify files. Inspect the supervised coordinator guide "
            "and identify whether /coordinator mode could be mistaken for a global toggle."
        ),
        packet=DelegationPacket(
            objective="Review SUPERVISED_COORDINATOR.md for session-scoped wording gaps",
            constraints=["read-only", "do not modify files"],
            acceptance_criteria=["identify clarity issues", "cite guide paths"],
            allowed_actions=["inspect", "search", "read"],
            # Packet text may mention "fixed"/"prefix" without edit intent.
            non_goals=["do not implement a fix", "do not edit the repo"],
        ),
    )
    assert mode == VerificationMode.READ_ONLY_CODE_REVIEW.value


def test_infer_read_only_code_review_explicit(supervised_env):
    mode = infer_verification_mode(
        user_objective="Code review only — inspect cancel_task for race conditions. Do not edit.",
        packet=None,
    )
    assert mode == VerificationMode.READ_ONLY_CODE_REVIEW.value


def test_infer_mutation_for_implementation_task(supervised_env):
    mode = infer_verification_mode(
        user_objective="Implement a one-line comment in README explaining supervised mode.",
        packet=DelegationPacket(
            objective="Add the comment",
            allowed_actions=["edit", "write"],
            acceptance_criteria=["README updated"],
        ),
    )
    assert mode == VerificationMode.MUTATION.value


def test_infer_ambiguous_without_modification_intent_is_general(supervised_env):
    mode = infer_verification_mode(
        user_objective="What does the supervised coordinator do?",
        packet=DelegationPacket(
            objective="Explain supervised coordinator",
            allowed_actions=["search", "answer"],
        ),
    )
    assert mode == VerificationMode.GENERAL.value
    assert mode != VerificationMode.MUTATION.value


def test_spot_check_validates_path_and_rejects_traversal(supervised_env, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "mod.py").write_text("def cancel_task():\n    return 1\n", encoding="utf-8")
    ok = spot_check_finding(
        str(ws),
        {"file": "mod.py", "symbol": "cancel_task", "evidence": "def cancel_task"},
    )
    assert ok["status"] == "citation_located"
    bad = spot_check_finding(str(ws), {"file": "../secrets.txt", "symbol": "x"})
    assert bad["status"] == "contradicted"
    rejected, reason = reject_oversized_or_traversal_spot_request(str(ws), "../../etc/passwd")
    assert rejected is False
    assert "path_rejected" in reason


def test_followup_during_reviewing_queues_only(supervised_env, monkeypatch):
    from api.agent_router.supervised.orchestrator import create_supervised_task, user_followup

    task = create_supervised_task("rev", parent_session_id="rev1")
    task.phase = TaskPhase.REVIEWING.value
    task.report_generation = 3
    save_task(task)

    started = []

    def boom_thread(*a, **k):
        started.append(True)
        raise AssertionError("must not start worker during REVIEWING")

    monkeypatch.setattr("threading.Thread", boom_thread)
    out = user_followup("rev1", "extra instruction", control_request_id="cr_rev_1")
    assert out["followup_state"] == "pending"
    assert out.get("pending_count") == 1
    assert not started
    task = load_task(task.task_id)
    assert task.phase == TaskPhase.REVIEWING.value
    assert len([p for p in task.pending_followups if p.get("state") == "pending"]) == 1


def test_review_superseded_by_pending_followup(supervised_env, monkeypatch):
    from api.agent_router.supervised import orchestrator as orch
    from api.agent_router.supervised.orchestrator import create_supervised_task
    from api.agent_router.supervised.types import WorkerRun

    task = create_supervised_task("cas", parent_session_id="cas1")
    task.phase = TaskPhase.REVIEWING.value
    task.report_generation = 1
    task.packet = DelegationPacket(objective="o", acceptance_criteria=["a"])
    run = WorkerRun(
        run_id="wr_test",
        attempt=1,
        review_loop=0,
        phase=TaskPhase.WORKER_RUNNING.value,
        worker_session_id=task.worker_session_id,
        prompt="p",
        report=WorkerReport(outcome="success", summary="ok", parse_ok=True, findings=[{"id": 1}]),
        result={"success": True},
        evidence={"items": []},
    )
    task.runs = [run]
    task.pending_followups = [
        {"id": "fu_x", "text": "more", "state": "pending", "accepted_at": "", "delivered_at": ""}
    ]
    save_task(task)

    gate = threading.Event()
    applied = []

    class FakeCoord:
        def invoke(self, *a, **k):
            gate.wait(timeout=2)
            return {
                "success": True,
                "response": json.dumps({"action": "approve", "rationale": "stale"}),
            }

    def fake_worker_then_review(**kwargs):
        applied.append(kwargs.get("follow_up_prompt") or "")

    monkeypatch.setattr(orch, "_worker_then_review", fake_worker_then_review)
    # Release after review would start — pending already present before invoke returns.
    gate.set()
    orch._run_review(task.task_id, coord_adapter=FakeCoord(), worker_adapter=MagicMock())
    task = load_task(task.task_id)
    assert task.phase != TaskPhase.APPROVED.value
    assert applied, "pending follow-up must supersede stale approve"
    assert task.phase == TaskPhase.FOLLOW_UP.value


def test_terminal_rejects_late_followup(supervised_env):
    from api.agent_router.supervised.orchestrator import create_supervised_task, user_followup

    task = create_supervised_task("term", parent_session_id="term1")
    task.phase = TaskPhase.ESCALATED.value
    task.final_response = "done"
    save_task(task)
    out = user_followup("term1", "too late")
    assert out["followup_state"] == "rejected_terminal"


def test_cancel_then_reconcile_single_terminal(supervised_env, monkeypatch):
    from api.agent_router.supervised.orchestrator import cancel_task, create_supervised_task
    from api.agent_router.supervised.delivery import reconcile_session_delivery

    task = create_supervised_task("can", parent_session_id="99")
    task.phase = TaskPhase.WORKER_RUNNING.value
    save_task(task)

    history: List[Dict[str, Any]] = []

    class FakeDB:
        def add_message(self, sid, role, content, metadata=None):
            mid = len(history) + 1
            history.append(
                {"id": mid, "role": role, "content": content, "metadata": metadata or {}}
            )
            return mid

        def get_messages(self, sid, limit=80):
            return list(history)[-limit:]

    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: FakeDB())
    monkeypatch.setattr(
        "api.chat_run_registry.cancel_session_runs",
        lambda *a, **k: {"cancelled": True, "killed_procs": 0},
    )
    monkeypatch.setattr("api.chat_delivery.store_result", lambda *a, **k: None)
    monkeypatch.setattr("api.chat_delivery.peek_result", lambda *a, **k: None)

    out = cancel_task("99", control_request_id="cr_cancel_1")
    assert out.get("delivery_handled") is True
    assert out.get("skip_history_persist") is True
    assert len([h for h in history if h["role"] == "assistant"]) == 1
    reconcile_session_delivery("99")
    reconcile_session_delivery("99")
    assert len([h for h in history if h["role"] == "assistant"]) == 1


def test_control_lane_idempotent_skips_second_persist(supervised_env, monkeypatch, owner_session):
    from api import web_chat_api as wca
    from api.agent_router.supervised.orchestrator import create_supervised_task

    task = create_supervised_task("idemp", parent_session_id="42")
    task.phase = TaskPhase.WORKER_RUNNING.value
    save_task(task)

    persisted = []

    class FakeDB:
        def add_message(self, *a, **k):
            persisted.append((a, k))
            return len(persisted)

        def get_chat_session(self, *a, **k):
            return {"id": 42}

    monkeypatch.setattr(wca, "_resolve_auth_chat_session", lambda sid: ({"id": 1}, 42, False))
    monkeypatch.setattr(wca, "_resolve_request_project_path", lambda data: ".")
    monkeypatch.setattr(wca, "_strip_invisible_leading", lambda s: s or "")
    monkeypatch.setattr(wca, "get_auth_db", lambda: FakeDB())

    client = owner_session.sign_in(wca.app.test_client())
    body = {
        "message": "/coordinate followup once please",
        "session_id": 42,
        "stream": False,
        "control_request_id": "cr_persist_once",
    }
    r1 = client.post("/api/chat", json=body)
    assert r1.status_code == 200
    n1 = len(persisted)
    assert n1 >= 2  # user + assistant
    r2 = client.post("/api/chat", json=body)
    assert r2.status_code == 200
    j2 = r2.get_json()
    assert j2.get("idempotent") or j2.get("reconcile_only") or j2.get("skip_history_persist")
    assert len(persisted) == n1  # no second exchange


def test_raw_artifact_viewer_enforces_ownership(supervised_env, monkeypatch):
    from api import web_chat_api as wca
    from api.agent_router.supervised.orchestrator import create_supervised_task
    from api.agent_router.supervised.store import persist_raw_worker_output

    task = create_supervised_task("art", parent_session_id="55")
    save_task(task)
    persist_raw_worker_output(task.task_id, "wr_abc", "hello raw")

    class FakeDB:
        def verify_auth_session(self, token):
            return {"id": 7} if token == "good" else None

        def get_chat_session(self, sid, uid):
            return {"id": sid} if sid == 55 and uid == 7 else None

    monkeypatch.setattr(wca, "get_auth_db", lambda: FakeDB())
    client = wca.app.test_client()
    url = f"/api/supervised/tasks/{task.task_id}/runs/wr_abc/raw"
    assert client.get(url).status_code == 401
    client.set_cookie("session_token", "good")
    ok = client.get(url)
    assert ok.status_code == 200
    assert b"hello raw" in ok.data or "hello raw" in (ok.get_json() or {}).get("content", "")
    # Traversal
    bad = client.get(f"/api/supervised/tasks/{task.task_id}/runs/../wr_abc/raw")
    assert bad.status_code in (400, 404)


def test_js_activity_card_not_pending_queue(supervised_env):
    js = (REPO_ROOT / "src/web/js/chat/chat_page.js").read_text(encoding="utf-8")
    html = (REPO_ROOT / "src/web/chat_page.html").read_text(encoding="utf-8")
    assert "upsertSupervisedActivityCard" in js
    assert "dispatchSupervisedControlMessage" in js
    assert "supervised-activity-card" in js or "buildActivityCardHtml" in Path(
        "src/web/js/chat/supervised_control.js"
    ).read_text(encoding="utf-8")
    assert "followupQueue" in html
    assert "Pending-prompt queue only" in html or "never used for supervised" in html
    # Control lane forces non-stream
    assert "controlLane ? false : true" in js or "controlLane ? false" in js


def test_node_supervised_control_helpers(supervised_env):
    import subprocess
    import sys

    script = REPO_ROOT / "src/web/js/chat/supervised_control.js"
    node = subprocess.run(
        [
            "node",
            "-e",
            f"""
const m = require({json.dumps(str(script.resolve()).replace(chr(92), '/'))});
const assert = require('assert');
assert.strictEqual(m.isImmediateControlLaneMessage('/coordinate followup x'), true);
assert.strictEqual(m.isImmediateControlLaneMessage('hello'), false);
const opts = m.controlLaneFetchOptions('/coordinate status', 'cr_1');
assert.strictEqual(opts.stream, false);
assert.strictEqual(opts.terminateDispatch, true);
assert.strictEqual(m.shouldPersistControlExchange({{idempotent:true}}), false);
assert.strictEqual(m.shouldPersistControlExchange({{response:'ok'}}), true);
const seen = new Set(['cr_1']);
assert.strictEqual(m.shouldAppendControlAssistantBubble({{control_request_id:'cr_1',idempotent:true}}, seen), false);
const st = m.activityCardStateFromTask({{task_id:'st_1',phase:'worker_running',pending_followup_count:1}});
assert.ok(st);
const html = m.buildActivityCardHtml(st);
assert.ok(html.includes('supervised-activity-card'));
assert.ok(html.includes('data-supervised-action'));
console.log('ok');
""",
        ],
        capture_output=True,
        text=True, encoding="utf-8",
        cwd=str(Path(".").resolve()),
    )
    assert node.returncode == 0, node.stdout + node.stderr
    assert "ok" in node.stdout

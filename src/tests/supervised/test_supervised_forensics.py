"""Forensic regression tests for supervised coordinator hardening.

Mocks only — no live Cursor / Codex / paid API calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from api.agent_router.supervised.commands import (
    handle_coordinate_command,
    handle_coordinator_command,
)
from api.agent_router.supervised.control import is_supervised_control_message
from api.agent_router.supervised.evidence import (
    compare_worktree_snapshots,
    snapshot_worktree,
)
from api.agent_router.supervised.packet import parse_worker_report
from api.agent_router.supervised.profiles import effective_mode, set_mode
from api.agent_router.supervised.store import (
    get_active_task,
    load_task,
    persist_raw_worker_output,
    save_task,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
from api.agent_router.supervised.types import TaskPhase, WorkerReport


@pytest.fixture
def supervised_env(tmp_path: Path, monkeypatch):
    from managers.settings_manager import SettingsManager
    from api.agent_router.config import reset_router_config
    from api.agent_router.supervised.profiles import reset_supervised_settings

    sm = SettingsManager(str(tmp_path / "settings.json"))
    monkeypatch.setattr("managers.settings_manager.get_settings_manager", lambda: sm)
    monkeypatch.setattr(
        "api.cursor_agent_commands.list_cursor_agent_models",
        lambda: [{"id": "auto", "label": "Auto"}, {"id": "grok-4.6", "label": "Grok"}],
    )
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path))
    reset_router_config()
    reset_supervised_settings()
    return sm


# ── 1–4: control lane / auth / fallthrough / idempotency ─────────────────────


def test_auth_user_exists_in_real_flask_control_route(supervised_env, monkeypatch, owner_session):
    """Reproduce NameError fallthrough: control must resolve auth before use."""
    from api import web_chat_api as wca
    from api.agent_router.supervised.orchestrator import create_supervised_task

    task = create_supervised_task("x", parent_session_id="42")
    task.phase = TaskPhase.WORKER_RUNNING.value
    save_task(task)

    monkeypatch.setattr(
        wca,
        "_resolve_auth_chat_session",
        lambda sid: (None, sid if sid is not None else 42, False),
    )
    monkeypatch.setattr(wca, "_resolve_request_project_path", lambda data: ".")
    monkeypatch.setattr(wca, "_strip_invisible_leading", lambda s: s or "")

    client = owner_session.sign_in(wca.app.test_client())
    resp = client.post(
        "/api/chat",
        json={
            "message": "/coordinate followup check panes",
            "session_id": 42,
            "stream": False,
            "control_request_id": "cr_test_auth_1",
        },
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body.get("control_lane") is True
    assert body.get("control_handled") is True
    assert body.get("type") != "supervised_control_error"
    assert "NameError" not in (body.get("response") or "")
    assert "Pending follow-ups: 1" in (body.get("response") or "")
    # Second identical request with same control id must not double-queue.
    resp2 = client.post(
        "/api/chat",
        json={
            "message": "/coordinate followup check panes",
            "session_id": 42,
            "stream": False,
            "control_request_id": "cr_test_auth_1",
        },
    )
    assert resp2.status_code == 200
    task = load_task(task.task_id)
    pending = [p for p in task.pending_followups if p.get("state") == "pending"]
    assert len(pending) == 1


def test_recognized_control_never_falls_through(supervised_env, monkeypatch, owner_session):
    from api import web_chat_api as wca

    monkeypatch.setattr(
        wca,
        "_resolve_auth_chat_session",
        lambda sid: (None, 7, False),
    )
    monkeypatch.setattr(wca, "_resolve_request_project_path", lambda data: ".")
    monkeypatch.setattr(wca, "_strip_invisible_leading", lambda s: s or "")

    def boom(*a, **k):
        raise RuntimeError("mutate-ok-then-boom")

    monkeypatch.setattr(
        "api.agent_router.supervised.commands.handle_coordinate_command",
        boom,
    )
    client = owner_session.sign_in(wca.app.test_client())
    resp = client.post(
        "/api/chat",
        json={"message": "/coordinate status", "session_id": 7, "stream": False},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body.get("type") == "supervised_control_error"
    assert body.get("control_handled") is True
    assert "fallthrough" in (body.get("response") or "").lower() or "failed" in (
        body.get("response") or ""
    ).lower()


def test_exception_after_mutation_cannot_double_apply(supervised_env, monkeypatch):
    from api.agent_router.supervised.orchestrator import create_supervised_task

    task = create_supervised_task("x", parent_session_id="s_mut")
    task.phase = TaskPhase.WORKER_RUNNING.value
    save_task(task)

    a = handle_coordinate_command(
        "followup same text",
        session_id="s_mut",
        control_request_id="cr_once",
    )
    assert a["pending_count"] == 1
    b = handle_coordinate_command(
        "followup same text",
        session_id="s_mut",
        control_request_id="cr_once",
    )
    assert b.get("idempotent") is True
    task = load_task(task.task_id)
    pending = [p for p in task.pending_followups if p.get("state") == "pending"]
    assert len(pending) == 1


def test_duplicate_control_request_ids_idempotent(supervised_env):
    from api.agent_router.supervised.orchestrator import create_supervised_task

    task = create_supervised_task("x", parent_session_id="s_idemp")
    task.phase = TaskPhase.WORKER_RUNNING.value
    save_task(task)
    first = handle_coordinate_command(
        "followup hello",
        session_id="s_idemp",
        control_request_id="cr_dup",
    )
    second = handle_coordinate_command(
        "followup hello",
        session_id="s_idemp",
        control_request_id="cr_dup",
    )
    assert first["followup_id"]
    assert second.get("idempotent") is True
    assert load_task(task.task_id).pending_followups
    assert (
        len([p for p in load_task(task.task_id).pending_followups if p["state"] == "pending"])
        == 1
    )


def test_one_followup_pending_count_exactly_one(supervised_env):
    from api.agent_router.supervised.orchestrator import create_supervised_task

    task = create_supervised_task("fu", parent_session_id="fu_one")
    task.phase = TaskPhase.WORKER_RUNNING.value
    save_task(task)
    out = handle_coordinate_command("followup only one", session_id="fu_one")
    assert out["pending_count"] == 1
    assert out["queue_position"] == 1
    assert "Pending follow-ups: 1" in out["response"]
    assert "#2 pending" not in out["response"]
    assert "queued (#" not in out["response"]


# ── 6–8: raw output / truncation / malformed links ─────────────────────────────


def test_raw_output_survives_structured_parse_failure(supervised_env, tmp_path):
    prose = (
        "**Verdict:** panes are session-isolated.\n\n"
        "### Findings\n\n"
        "**[P1] control lane** — "
        "[file:///C:/Projects/Cuttle/src/api/web_chat_api.py]"
        "(file:///C:/Projects/Cuttle/src/api/web_chat_api.py) / "
        "[vscode://file/C:/Projects/Cuttle/src/api/web_chat_api.py:5219]"
        "(vscode://file/C:/Projects/Cuttle/src/api/web_chat_api.py:5219)\n\n"
        + ("detail " * 200)
    )
    report = parse_worker_report(prose)
    assert report.parse_ok is False
    assert report.parse_error
    assert report.raw_output == prose
    assert report.raw_output_chars == len(prose)
    assert report.summary_truncated is True
    assert len(report.summary) < len(prose)

    meta = persist_raw_worker_output("st_test", "wr_test", prose)
    path = Path(meta["path"])
    assert path.is_file()
    assert path.read_text(encoding="utf-8") == prose
    assert meta["truncated"] is False
    assert meta["chars"] == len(prose)


def test_truncated_rendering_does_not_truncate_durable_raw(supervised_env):
    long = "x" * 5000
    report = parse_worker_report(long)
    assert report.summary_truncated
    assert len(report.raw_output) == 5000
    d = report.to_dict()
    assert len(d["raw_output"]) == 5000
    assert d["raw_output_chars"] == 5000


def test_malformed_markdown_links_do_not_break_report_preservation(supervised_env):
    blob = (
        "See [broken](file:///E:/a.py) and "
        "[vscode://file/E:/a.py:1](vscode://file/E:/a.py:1)\n\n"
        '```json\n{"outcome":"success","summary":"ok","acceptance_satisfied":true}\n```'
    )
    report = parse_worker_report(blob)
    assert report.parse_ok is True
    assert report.outcome == "success"
    assert report.raw_output == blob


# ── 9–13: background indicator + delivery reconciliation ─────────────────────


def test_background_indicator_survives_status_response(supervised_env):
    from api.agent_router.supervised.delivery import public_indicator
    from api.agent_router.supervised.orchestrator import create_supervised_task

    task = create_supervised_task("bg", parent_session_id="bg1")
    task.phase = TaskPhase.WORKER_RUNNING.value
    save_task(task)
    status = handle_coordinate_command("status", session_id="bg1")
    assert status["type"] == "supervised_status"
    ind = public_indicator(load_task(task.task_id))
    assert ind is not None
    assert ind["phase"] == "worker_running"
    assert ind["terminal"] is False


def test_background_indicator_survives_coordinator_conversation(supervised_env):
    from api.agent_router.supervised.delivery import public_indicator
    from api.agent_router.supervised.orchestrator import (
        create_supervised_task,
        handle_coordinator_conversation,
    )

    task = create_supervised_task("bg2", parent_session_id="bg2")
    task.phase = TaskPhase.WORKER_RUNNING.value
    save_task(task)
    handle_coordinator_conversation(
        "why this plan?",
        session_id="bg2",
        coordinator_runner=lambda *a, **k: {"success": True, "response": "because"},
    )
    ind = public_indicator(load_task(task.task_id))
    assert ind["phase"] == "worker_running"


def test_background_state_reconstructs_after_refresh(supervised_env):
    from api.agent_router.supervised.delivery import public_indicator
    from api.agent_router.supervised.orchestrator import create_supervised_task

    task = create_supervised_task("rf", parent_session_id="rf1")
    task.phase = TaskPhase.REVIEWING.value
    save_task(task)
    # Simulate refresh: reload from durable store only
    reloaded = load_task(task.task_id)
    ind = public_indicator(reloaded)
    assert ind["task_id"] == task.task_id
    assert ind["phase"] == "reviewing"


def test_missing_terminal_events_reconcile_without_duplicate(supervised_env, monkeypatch):
    from api.agent_router.supervised.delivery import (
        deliver_terminal_to_parent,
        reconcile_session_delivery,
    )
    from api.agent_router.supervised.orchestrator import create_supervised_task

    task = create_supervised_task("dl", parent_session_id="99")
    task.phase = TaskPhase.ESCALATED.value
    task.final_response = "final judgment"
    save_task(task)

    msgs = []

    class FakeDB:
        def add_message(self, sid, role, content, metadata=None):
            mid = len(msgs) + 1
            msgs.append(
                {
                    "id": mid,
                    "role": role,
                    "content": content,
                    "metadata": metadata or {},
                }
            )
            return mid

        def get_messages(self, sid, limit=None):
            return list(msgs)

    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: FakeDB())
    parked = {}

    def store_result(sid, result):
        parked[str(sid)] = result

    def peek_result(sid):
        return parked.get(str(sid))

    monkeypatch.setattr("api.chat_delivery.store_result", store_result)
    monkeypatch.setattr("api.chat_delivery.peek_result", peek_result)

    r1 = deliver_terminal_to_parent(task)
    assert r1["ok"]
    assert len(msgs) == 1
    r2 = reconcile_session_delivery("99")
    assert len(msgs) == 1  # no duplicate history
    assert any(x.get("task_id") == task.task_id for x in r2["reconciled"])


# ── 14–15: worktree baseline / concurrent attribution ────────────────────────


def test_start_end_snapshots_distinguish_preexisting(supervised_env, tmp_path):
    ws = tmp_path / "repo"
    ws.mkdir()
    (ws / "a.txt").write_text("one\n", encoding="utf-8")
    # Fake porcelain via monkeypatch rather than requiring git init
    from api.agent_router.supervised import evidence as ev

    baseline = {
        "ok": True,
        "paths": {"a.txt": {"status": " M", "digest": "aaa"}, "b.txt": {"status": "??", "digest": "bbb"}},
        "path_count": 2,
    }
    ending = {
        "ok": True,
        "paths": {
            "a.txt": {"status": " M", "digest": "aaa"},
            "b.txt": {"status": "??", "digest": "bbb"},
            "c.txt": {"status": "??", "digest": "ccc"},
        },
        "path_count": 3,
    }
    delta = compare_worktree_snapshots(baseline, ending)
    assert delta["pre_existing_dirty_paths"] == 2
    assert delta["new_paths_during_task"] == 1
    assert delta["existing_paths_further_changed"] == 0
    assert delta["concurrent_attribution"] == "not_provable"
    assert "c.txt" in delta["new_paths"]


def test_concurrent_attribution_labeled_uncertain(supervised_env):
    baseline = {
        "paths": {"x.py": {"status": " M", "digest": "1"}},
        "path_count": 1,
    }
    ending = {
        "paths": {"x.py": {"status": " M", "digest": "2"}},
        "path_count": 1,
    }
    delta = compare_worktree_snapshots(baseline, ending)
    assert delta["existing_paths_further_changed"] == 1
    assert delta["concurrent_attribution"] == "not_provable"
    assert "not provable" in delta["summary_text"].lower()


# ── 16–18: session vs global ─────────────────────────────────────────────────


def test_different_sessions_cannot_control_each_other(supervised_env):
    from api.agent_router.supervised.orchestrator import create_supervised_task

    task = create_supervised_task("iso", parent_session_id="A")
    task.phase = TaskPhase.WORKER_RUNNING.value
    save_task(task)
    out = handle_coordinate_command("cancel", session_id="B")
    assert out["type"] in ("supervised_idle", "supervised_forbidden")
    assert load_task(task.task_id).phase == TaskPhase.WORKER_RUNNING.value


def test_session_coordinator_mode_does_not_activate_globally(supervised_env):
    set_mode("supervised", session_id="sess_local")
    assert effective_mode(session_id="sess_local") == "supervised"
    assert effective_mode(session_id="other") == "off"
    assert effective_mode() == "off"


def test_global_default_changes_explicitly_identified(supervised_env):
    body = handle_coordinator_command("profile diet-frontier", session_id="s1")
    assert "Global default" in body["response"]
    assert body.get("scope") == "global_default"
    status = handle_coordinator_command("status", session_id="s1")
    assert "global default" in status["response"].lower() or "Global default" in status["response"]
    assert "session" in status["response"].lower()


def test_js_uses_canonical_bubble_without_composer_worker_strip(supervised_env):
    js = (REPO_ROOT / "src/web/js/chat/chat_page.js").read_text(encoding="utf-8")
    helper = (REPO_ROOT / "src/web/js/chat/supervised_control.js").read_text(encoding="utf-8")
    assert "updateSupervisedTaskIndicator" in js
    assert "upsertSupervisedActivityCard" in js
    assert "supervised_task" in js
    assert "control_request_id" in js
    assert "buildActivityDisclosureHtml" in helper
    assert "buildActivityCardHtml" not in helper
    html = (REPO_ROOT / "src/web/chat_page.html").read_text(encoding="utf-8")
    assert "supervisedTaskIndicator" not in html
    assert "Pending-prompt queue only" in html
    assert "supervised_control.js" in html


def test_forensic_fixture_links_parse_resilient(supervised_env):
    """Fixture mirrors the truncated Markdown/link content from st_96bc27927bf3."""
    fixture = REPO_ROOT / "src/tests/fixtures/supervised_truncated_worker_report.md"
    text = fixture.read_text(encoding="utf-8")
    report = parse_worker_report(text)
    assert report.parse_ok is False
    assert report.raw_output == text
    assert "_auth_user" in report.raw_output

"""Behavioral tests for supervised coordinator presentation (one bubble)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from api.agent_router.supervised.bubble import (
    build_bubble_content,
    jump_notification_label,
    visible_phase_label,
)
from api.agent_router.supervised.delivery import deliver_terminal_to_parent, public_indicator
from api.agent_router.supervised.orchestrator import create_supervised_task
from api.agent_router.supervised.store import load_task, save_task
from api.agent_router.supervised.types import TaskPhase

REPO = Path(__file__).resolve().parents[3]
HELPER = REPO / "src" / "web" / "js" / "chat/supervised_control.js"
CHAT_JS = REPO / "src" / "web" / "js" / "chat/chat_page.js"
CHAT_HTML = REPO / "src" / "web" / "chat_page.html"
CHAT_CSS = REPO / "src" / "web" / "css" / "chat_page.css"

pytestmark_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not available")


@pytest.fixture
def supervised_env(tmp_path, monkeypatch):
    from api.agent_router.supervised.profiles import reset_supervised_settings

    monkeypatch.setattr(
        "api.agent_router.supervised.store._repo_root",
        lambda: tmp_path,
    )
    (tmp_path / "src" / "data" / "workspace" / "supervised_tasks").mkdir(parents=True)
    reset_supervised_settings()
    yield tmp_path


def test_visible_phase_labels_are_quiet():
    assert visible_phase_label("coordinating") == "Planning task…"
    assert visible_phase_label("worker_running") == "Working…"
    assert visible_phase_label("reviewing") == "Reviewing results…"
    assert "Cursor" not in visible_phase_label("worker_running")


def test_bubble_content_hides_ids_by_default(supervised_env):
    task = create_supervised_task("Implement feature", parent_session_id="s_bubble")
    task.phase = TaskPhase.WORKER_RUNNING.value
    save_task(task)
    body = build_bubble_content(task)
    assert "cuttle_supervised_activity" in body
    # In-flight status is rendered as the standard activity indicator from the
    # Activity payload — the bubble must not carry it as plain body text.
    assert body.split("<cuttle_supervised_activity>")[0].strip() == ""
    payload = json.loads(
        body.split("<cuttle_supervised_activity>")[1]
        .split("</cuttle_supervised_activity>")[0]
    )
    assert payload["phase_label"] == "Working…"
    assert payload["status_text"] == "Working with a subagent…"
    assert payload["terminal"] is False


def test_terminal_bubble_still_carries_final_text(supervised_env):
    task = create_supervised_task("Implement feature", parent_session_id="s_term")
    task.phase = TaskPhase.APPROVED.value
    task.final_response = "All done."
    save_task(task)
    body = build_bubble_content(task)
    assert body.split("<cuttle_supervised_activity>")[0].strip() == "All done."


def test_terminal_bubble_keeps_evidence_in_activity(supervised_env):
    """Default bubble stays concise; Activity carries the evidence ledger."""
    from api.agent_router.supervised.packet import format_final_user_message
    from api.agent_router.supervised.types import WorkerReport, WorkerRun

    task = create_supervised_task(
        "Read-only. Do not modify files. Inspect the guide.",
        parent_session_id="s_concise",
    )
    task.phase = TaskPhase.APPROVED.value
    task.verification_mode = "read_only_code_review"
    task.reviews = [
        {
            "action": "approve",
            "rationale": (
                'The guide lists "/coordinator mode off|supervised" without explaining '
                "that the setting is session-scoped, which could be mistaken for a "
                "global toggle. Cursor proposed clarifying that it affects only the "
                "current session."
            ),
        }
    ]
    report = WorkerReport(
        outcome="success",
        summary="session-scoped gap found",
        parse_ok=True,
        findings=[
            {
                "id": 1,
                "file": "docs/guides/SUPERVISED_COORDINATOR.md",
                "symbol": "mode",
                "impact": "Could look global",
            }
        ],
        raw_artifact_path="runs/wr_demo.raw.txt",
    )
    evidence = {
        "items": [
            {
                "key": "worktree_task_delta",
                "source": "cuttle_verified",
                "detail": "clean",
                "ok": True,
            },
            {
                "key": "read_only_no_task_modifications",
                "source": "cuttle_verified",
                "detail": "no mods",
                "ok": True,
            },
            {
                "key": "finding_spot_check_1",
                "source": "cuttle_verified",
                "detail": "status=citation_located",
                "ok": True,
            },
            {
                "key": "verification_policy",
                "source": "cuttle_verified",
                "detail": "policy",
                "ok": True,
            },
        ]
    }
    task.final_response = format_final_user_message(
        judgment=task.reviews[0]["rationale"],
        report=report,
        action="approve",
        task_id=task.task_id,
        evidence=evidence,
    )
    task.runs = [
        WorkerRun(
            run_id="wr_demo",
            attempt=1,
            review_loop=0,
            phase=TaskPhase.APPROVED.value,
            worker_session_id=task.worker_session_id,
            prompt="p",
            report=report,
            evidence=evidence,
        )
    ]
    save_task(task)
    body = build_bubble_content(task)
    visible = body.split("<cuttle_supervised_activity>")[0].strip()
    assert visible.startswith("Approved.")
    assert "session-scoped" in visible
    assert "Independent evidence" not in visible
    assert "verification_policy" not in visible
    payload = json.loads(
        body.split("<cuttle_supervised_activity>")[1]
        .split("</cuttle_supervised_activity>")[0]
    )
    assert payload["terminal"] is True
    assert payload.get("details_markdown")
    assert "Independent evidence" in payload["details_markdown"]
    assert payload["details"]["evidence_items"]
    assert payload["verification_mode"] == "read_only_code_review"


def test_public_indicator_disables_worker_card(supervised_env):
    task = create_supervised_task("x", parent_session_id="s_ind")
    task.phase = TaskPhase.WORKER_RUNNING.value
    save_task(task)
    ind = public_indicator(task)
    assert ind["show_worker_card"] is False
    assert ind["canonical_bubble"] is True
    assert ind["phase_label"] == "Working…"


def test_terminal_delivery_updates_canonical_not_duplicate(supervised_env, monkeypatch):
    class FakeDB:
        def __init__(self):
            self.messages = {}
            self.next_id = 1
            self.updates = []

        def add_message(self, sid, role, content, metadata=None):
            mid = self.next_id
            self.next_id += 1
            self.messages[mid] = {
                "id": mid,
                "role": role,
                "content": content,
                "metadata": metadata,
            }
            return mid

        def update_message_content(self, mid, content, metadata=None):
            self.updates.append((mid, content, metadata))
            if mid in self.messages:
                self.messages[mid]["content"] = content
                if metadata is not None:
                    self.messages[mid]["metadata"] = metadata
                return True
            return False

        def get_messages(self, sid, limit=80):
            return list(self.messages.values())

    db = FakeDB()
    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)

    task = create_supervised_task("done", parent_session_id="42")
    uid = db.add_message(42, "user", "/coordinate done")
    aid = db.add_message(42, "assistant", "Working…")
    task.parent_user_message_id = uid
    task.coordinator_response_message_id = aid
    task.phase = TaskPhase.APPROVED.value
    task.final_response = "All good."
    save_task(task)

    from api.agent_router.supervised.delivery import mark_terminal_persisted

    mark_terminal_persisted(task)
    out = deliver_terminal_to_parent(task)
    assert out["ok"] is True
    assert len(db.updates) >= 1
    assert db.updates[-1][0] == aid
    assert "All good." in db.updates[-1][1]
    # Exactly one assistant row.
    asst = [m for m in db.messages.values() if m["role"] == "assistant"]
    assert len(asst) == 1


def test_jump_wording():
    assert "completed" in jump_notification_label("approved").lower()
    assert "cancelled" in jump_notification_label("cancelled").lower()
    assert "failed" in jump_notification_label("failed").lower()


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_node_presentation_helpers():
    script = HELPER.resolve().as_posix()
    node = subprocess.run(
        [
            "node",
            "-e",
            f"""
const m = require({json.dumps(script)});
const assert = require('assert');
assert.strictEqual(m.phaseLabel('worker_running'), 'Working…');
assert.strictEqual(m.showWorkerCardByDefault, false);
assert.strictEqual(
  m.shouldAppendControlAssistantBubble({{canonical_bubble:true,response:'x'}}, new Set()),
  false
);
const st = m.activityStateFromTask({{
  task_id:'st_1', phase:'worker_running', pending_followup_count:0,
  worker_label:'Cursor Auto', coordinator_label:'Codex', terminal:false
}});
const html = m.buildActivityDisclosureHtml(st);
assert.ok(html.includes('Activity'));
assert.ok(html.includes('data-supervised-action=\"status\"'));
assert.ok(!html.includes('Cursor Agent Auto'));
const work = m.buildWorkingBubbleHtml(st);
// In-flight bubbles animate with the standard chat activity indicator.
assert.ok(work.includes('typing-indicator'));
assert.ok(work.includes('typing-orbit'));
assert.ok(work.includes('typing-status'));
assert.ok(work.includes('Working with a subagent'));
assert.strictEqual(m.activityStatusText({{phase:'reviewing', phase_label:'Reviewing results…'}}), 'Reviewing results…');
assert.strictEqual(m.buildLiveIndicatorHtml({{phase:'approved', terminal:true}}), '');
assert.ok(m.buildRestartFormHtml().includes('__native_restart__'));
const ack = new Set(['de_1']);
assert.strictEqual(
  m.shouldShowJumpNotification({{terminal:true, delivery:{{terminal_event_id:'de_1'}}}}, ack),
  false
);
assert.strictEqual(
  m.shouldShowJumpNotification({{terminal:true, delivery:{{terminal_event_id:'de_2'}}}}, ack),
  true
);
// Copy Text strips Activity transport / UI chrome; keeps prose + user XML fences.
const answer =
  'Approved. Session-scoped wording gap.\\n\\n'
  + '<cuttle_supervised_activity>\\n'
  + JSON.stringify({{
      task_id:'st_3cfda9538289',
      timeline:[{{type:'review_decision', at:'2026-08-16'}}],
      details_markdown:'ledger'
    }})
  + '\\n</cuttle_supervised_activity>\\n'
  + '<cuttle_action_form>\\n'
  + JSON.stringify({{mode:'choice', title:'Restart', options:[{{id:'g', label:'Go'}}]}})
  + '\\n</cuttle_action_form>\\n'
  + '<cuttle_confirm action="discord.post">Keep confirm body</cuttle_confirm>\\n'
  + '```xml\\n<user-authored>keep me</user-authored>\\n```';
const copied = m.stripCuttleCopyMetadata(answer);
assert.ok(!copied.includes('cuttle_supervised_activity'));
assert.ok(!copied.includes('st_3cfda9538289'));
assert.ok(!copied.includes('timeline'));
assert.ok(!copied.includes('cuttle_action_form'));
assert.ok(!copied.includes('Restart'));
assert.ok(!copied.includes('cuttle_confirm'));
assert.ok(copied.includes('Keep confirm body'));
assert.ok(copied.includes('Approved. Session-scoped wording gap.'));
assert.ok(copied.includes('<user-authored>keep me</user-authored>'));
const termState = m.activityStateFromTask({{
  task_id:'st_1', phase:'approved', terminal:true,
  worker_label:'Cursor Auto', coordinator_label:'Codex',
  details_markdown:'**Independent evidence**\\n- worktree',
  details:{{evidence_items:[{{key:'worktree_task_delta', source:'cuttle_verified', ok:true, detail:'clean'}}]}}
}});
const termHtml = m.buildActivityDisclosureHtml(termState);
assert.ok(termHtml.includes('Copy details'));
assert.ok(termHtml.includes('Evidence ledger'));
assert.ok(termHtml.includes('supervised-activity-details-md'));
console.log('ok');
""",
        ],
        capture_output=True,
        text=True, encoding="utf-8",
        cwd=str(REPO),
    )
    assert node.returncode == 0, node.stdout + node.stderr
    assert "ok" in node.stdout


def test_js_has_no_default_worker_participant_card():
    js = CHAT_JS.read_text(encoding="utf-8")
    css = CHAT_CSS.read_text(encoding="utf-8")
    html = CHAT_HTML.read_text(encoding="utf-8")
    assert "supervised-activity-row" in css
    assert "display: none !important" in css
    assert "showSupervisedResultNotification" in js or "showSupervisedJumpNotification" in js
    assert "updateServerMessageInPlace" in js
    assert "followupQueue" in html
    assert "Pending-prompt queue only" in html or "never used for supervised" in html
    assert "supervised-activity-row" in js  # only removed / hidden, not mounted by default
    assert "canonical_bubble" in HELPER.read_text(encoding="utf-8") or "showWorkerCardByDefault" in HELPER.read_text(encoding="utf-8")


def test_restart_form_uses_native_controller():
    from api.agent_router.supervised.bubble import build_restart_form_tag

    tag = build_restart_form_tag()
    assert "__native_restart__" in tag
    assert "graceful" in tag
    assert "when-idle" in tag

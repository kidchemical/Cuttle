"""``python -m api.gizmos tasks patch`` owner / working flags."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from api.auth_db import AuthDatabase
from api.gizmos.__main__ import main


@pytest.fixture(autouse=True)
def isolated_task_database(monkeypatch, tmp_path):
    from api.gizmos import tasks
    monkeypatch.delenv("CUTTLE_CHAT_SESSION_ID", raising=False)
    monkeypatch.setattr(tasks, "database", lambda db=None: db or AuthDatabase(tmp_path / "cuttle_auth.db"))


def _seed(tmp_path: Path):
    db = AuthDatabase(tmp_path / "cuttle_auth.db")
    owner = db.create_user("owner@local", "Owner", "local", password="x")
    sid = db.create_chat_session(owner, "planner")
    worker = db.create_chat_session(owner, "codex worker")
    db.upsert_chat_widget(
        widget_id="plan", user_id=owner, wtype="tasks", title="Tasks", scope="session",
        session_id=sid, project_path="",
        payload={"items": [
            {"id": "1", "text": "Scaffold", "done": False, "children": []},
            {"id": "2", "text": "Tests", "done": False, "children": []},
        ]},
    )
    return db, owner, sid, worker


def _run(argv: list[str]) -> tuple[int, dict]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = main(["tasks", *argv])
    return code, json.loads(buf.getvalue())


def _items(body):
    return {i["id"]: i for i in body["gizmo"]["payload"]["items"]}


def test_owner_user_and_working_flags_round_trip(tmp_path: Path):
    _seed(tmp_path)
    code, body = _run(["patch", "plan", "--owner", "1=user", "--working", "1"])
    assert code == 0, body
    item = _items(body)["1"]
    assert item["owner"] == {"kind": "user", "label": "You"}
    assert item["in_progress"] is True
    code, body = _run(["patch", "plan", "--not-working", "1"])
    assert code == 0
    item = _items(body)["1"]
    assert "in_progress" not in item and item["owner"]["kind"] == "user"


def test_owner_chat_handle_is_canonical_and_checked(tmp_path: Path):
    _db, _owner, _sid, worker = _seed(tmp_path)
    code, body = _run(["patch", "plan", "--owner", f"2=CH-{worker:06d}", "--working", "2"])
    assert code == 0, body
    assert _items(body)["2"]["owner"] == {"kind": "chat", "id": f"CH-{worker:06d}", "label": "codex worker"}
    code, body = _run(["patch", "plan", "--owner", "2=none"])
    assert code == 0
    assert "owner" not in _items(body)["2"]
    assert _items(body)["2"]["in_progress"] is True


def test_owner_agent_spelling(tmp_path: Path):
    _seed(tmp_path)
    code, body = _run(["patch", "plan", "--owner", "1=agent:Claude"])
    assert code == 0, body
    assert _items(body)["1"]["owner"] == {"kind": "agent", "agent": "claude", "label": "claude"}


def test_comma_separated_working_items_and_ops_json_merge(tmp_path: Path):
    _seed(tmp_path)
    code, body = _run(["patch", "plan", "--working", "1,2",
                       "--ops", json.dumps({"set_owner": {"1": {"kind": "user", "label": "You"}}}),
                       "--owner", "2=user"])
    assert code == 0, body
    items = _items(body)
    assert items["1"]["in_progress"] and items["2"]["in_progress"]
    assert items["1"]["owner"]["label"] == "You" and items["2"]["owner"]["kind"] == "user"


def test_bad_owner_arguments_fail_without_writing(tmp_path: Path):
    _seed(tmp_path)
    for argv in (["--owner", "1"], ["--owner", "1=robot"], ["--owner", "1=agent:"],
                 ["--owner", "1=CH-999999"]):
        code, body = _run(["patch", "plan", *argv])
        assert code == 2 and body["success"] is False, argv
    code, body = _run(["get", "plan"])
    assert "owner" not in _items(body)["1"]


def test_owner_and_working_changes_are_recorded_in_history(tmp_path: Path):
    _seed(tmp_path)
    _run(["patch", "plan", "--owner", "1=user", "--working", "1"])
    code, body = _run(["history", "plan"])
    assert code == 0
    writes = [e for e in body["events"] if e["after_state"]]
    latest = writes[0]["after_state"]["payload"]["items"][0]
    assert latest["owner"]["kind"] == "user" and latest["in_progress"] is True
    assert writes[0]["operation"] == "patch"

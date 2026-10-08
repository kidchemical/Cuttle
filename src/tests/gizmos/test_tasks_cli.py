"""Tests for ``python -m api.gizmos``."""

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
    db_path = tmp_path / "cuttle_auth.db"
    db = AuthDatabase(db_path)
    owner = db.create_user("owner@local", "Owner", "local", password="x")
    sid = db.create_chat_session(owner, "widgets cli")
    db.upsert_chat_widget(
        widget_id="plan",
        user_id=owner,
        wtype="tasks",
        title="Tasks",
        scope="session",
        session_id=sid,
        project_path="",
        payload={
            "items": [
                {"id": "1", "text": "Scaffold", "done": False, "children": []},
                {"id": "2", "text": "Tests", "done": False, "children": []},
            ]
        },
        description="Ship the widgets CLI.",
    )
    return db, db_path, owner, sid


def _run(argv: list[str]) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = main(["tasks", *argv])
    return code, buf.getvalue()


def test_list_and_get(tmp_path: Path):
    _db, db_path, _owner, sid = _seed(tmp_path)
    code, out = _run(
        ["list", "--session", f"CH-{sid:06d}"]
    )
    assert code == 0
    payload = json.loads(out)
    assert len(payload["gizmos"]) == 1
    assert payload["gizmos"][0]["id"] == "plan"
    assert payload["gizmos"][0]["description"] == "Ship the widgets CLI."

    code, out = _run(["get", "plan"])
    assert code == 0
    widget = json.loads(out)["gizmo"]
    assert [i["id"] for i in widget["payload"]["items"]] == ["1", "2"]


def test_patch_set_done_and_description(tmp_path: Path):
    _db, db_path, _owner, sid = _seed(tmp_path)
    code, out = _run(
        [
            "patch",
            "plan",
            "--set-done",
            "1",
            "--description",
            "Only tests left.",
        ]
    )
    assert code == 0
    widget = json.loads(out)["gizmo"]
    items = {i["id"]: i for i in widget["payload"]["items"]}
    assert items["1"]["done"] is True
    assert items["2"]["done"] is False
    assert widget["description"] == "Only tests left."
    assert int(widget["revision"]) >= 2


def test_patch_ops_json(tmp_path: Path):
    _db, db_path, _owner, _sid = _seed(tmp_path)
    code, out = _run(
        [
            "patch",
            "plan",
            "--ops",
            json.dumps({"set_done": ["2"], "add": [{"item": {"id": "3", "text": "Docs"}}]}),
        ]
    )
    assert code == 0
    ids = [i["id"] for i in json.loads(out)["gizmo"]["payload"]["items"]]
    assert ids == ["1", "2", "3"]


def test_missing_widget(tmp_path: Path):
    _db, db_path, _owner, _sid = _seed(tmp_path)
    code, out = _run(["get", "nope"])
    assert code == 2
    assert json.loads(out)["error"] == "Tasks gizmo not found"


def test_help_lists_verbs():
    with pytest.raises(SystemExit) as ei:
        main(["--help"])
    assert ei.value.code == 0


def test_patch_missing_ops(tmp_path: Path):
    _db, db_path, _owner, _sid = _seed(tmp_path)
    code, out = _run(["patch", "plan"])
    assert code == 2
    assert json.loads(out)["success"] is False

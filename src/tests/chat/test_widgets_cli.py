"""Tests for ``python -m api.widgets_cli``."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from api.auth_db import AuthDatabase
from api.widgets_cli.cli import main


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
        code = main(argv)
    return code, buf.getvalue()


def test_list_and_get(tmp_path: Path):
    _db, db_path, _owner, sid = _seed(tmp_path)
    code, out = _run(
        ["list", "--session", f"CH-{sid:06d}", "--db", str(db_path), "--json"]
    )
    assert code == 0
    payload = json.loads(out)
    assert payload["count"] == 1
    assert payload["widgets"][0]["id"] == "plan"
    assert payload["widgets"][0]["description"] == "Ship the widgets CLI."

    code, out = _run(["get", "plan", "--db", str(db_path), "--json"])
    assert code == 0
    widget = json.loads(out)["widget"]
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
            "--db",
            str(db_path),
            "--json",
        ]
    )
    assert code == 0
    widget = json.loads(out)["widget"]
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
            "--db",
            str(db_path),
            "--json",
        ]
    )
    assert code == 0
    ids = [i["id"] for i in json.loads(out)["widget"]["payload"]["items"]]
    assert ids == ["1", "2", "3"]


def test_missing_widget(tmp_path: Path):
    _db, db_path, _owner, _sid = _seed(tmp_path)
    code, out = _run(["get", "nope", "--db", str(db_path), "--json"])
    assert code == 2
    assert json.loads(out)["error"] == "not_found"


def test_help_lists_verbs():
    with pytest.raises(SystemExit) as ei:
        main(["--help"])
    assert ei.value.code == 0


def test_patch_missing_ops(tmp_path: Path):
    _db, db_path, _owner, _sid = _seed(tmp_path)
    code, out = _run(["patch", "plan", "--db", str(db_path), "--json"])
    assert code == 2
    assert json.loads(out)["ok"] is False

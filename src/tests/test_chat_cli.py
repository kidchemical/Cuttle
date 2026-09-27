"""Tests for ``python -m api.chat_cli`` (agent chat-history ops)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from api.auth_db import AuthDatabase
from api.chat_cli.cli import main


STOP = "⏹ Stopped generating."


def _seed(tmp_path: Path) -> tuple[AuthDatabase, Path, int, int]:
    db_path = tmp_path / "cuttle_auth.db"
    db = AuthDatabase(db_path)
    owner = db.create_user("owner@local", "Owner", "local", password="x")
    sid = db.create_chat_session(owner, "CLI demo")
    db.add_message(sid, "user", "hello")
    db.add_message(sid, "assistant", "hi there")
    db.add_message(sid, "system", STOP, metadata={"kind": "generation-stop"})
    db.add_message(sid, "user", "target bubble")
    db.add_message(sid, "assistant", "reply after stop")
    return db, db_path, owner, sid


def _run(argv: list[str]) -> tuple[int, str, str]:
    import io
    from contextlib import redirect_stderr, redirect_stdout

    out_buf = io.StringIO()
    err_buf = io.StringIO()
    with redirect_stdout(out_buf), redirect_stderr(err_buf):
        code = main(argv)
    return code, out_buf.getvalue(), err_buf.getvalue()


def test_auth_db_module_does_not_require_bcrypt_at_import():
    """Regression: agents without bcrypt used to die on `import api.auth_db`."""
    import api.auth_db as adb

    assert adb.__dict__.get("bcrypt") is None
    assert callable(getattr(adb, "_bcrypt_mod", None))


def test_get_includes_timestamp(tmp_path: Path):
    _db, db_path, _owner, sid = _seed(tmp_path)
    handle = f"CH-{sid:06d}-3"
    code, out, err = _run(["get", handle, "--db", str(db_path), "--json"])
    assert code == 0, err
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["share_index"] == 3
    assert payload["content"] == "target bubble"
    assert payload.get("timestamp"), "agents need bubble clock time in JSON"
    assert payload.get("id") is not None


def test_get_works_when_bcrypt_import_would_fail(tmp_path: Path, monkeypatch):
    """Read path must not touch bcrypt after the DB is seeded."""
    import api.auth_db as adb

    _db, db_path, _owner, sid = _seed(tmp_path)
    handle = f"CH-{sid:06d}-3"

    def _bcrypt_boom():
        raise RuntimeError("bcrypt should not be required for chat_cli get")

    monkeypatch.setattr(adb, "_bcrypt_mod", _bcrypt_boom)
    code, out, err = _run(["get", handle, "--db", str(db_path), "--json"])
    assert code == 0, err or out
    payload = json.loads(out)
    assert payload["content"] == "target bubble"
    assert payload["timestamp"]


def test_get_share_index_skips_system(tmp_path: Path):
    _db, db_path, _owner, sid = _seed(tmp_path)
    handle = f"CH-{sid:06d}-3"
    code, out, _err = _run(["get", handle, "--db", str(db_path), "--json"])
    assert code == 0
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["share_index"] == 3
    assert payload["role"] == "user"
    assert payload["content"] == "target bubble"
    assert payload["handle"] == handle


def test_get_missing_bubble(tmp_path: Path):
    _db, db_path, _owner, sid = _seed(tmp_path)
    code, out, _err = _run(["get", f"CH-{sid:06d}-99", "--db", str(db_path), "--json"])
    assert code == 2
    payload = json.loads(out)
    assert payload["ok"] is False
    assert payload["error"] == "not_found"


def test_get_session_summary(tmp_path: Path):
    _db, db_path, _owner, sid = _seed(tmp_path)
    code, out, _err = _run(["get", f"CH-{sid:06d}", "--db", str(db_path), "--json"])
    assert code == 0
    payload = json.loads(out)
    assert payload["share_count"] == 4  # user+assistant only
    assert payload["message_count"] == 5  # includes stop
    assert payload["session"]["session_name"] == "CLI demo"


def test_session_assigns_absolute_share_indices(tmp_path: Path):
    _db, db_path, _owner, sid = _seed(tmp_path)
    code, out, _err = _run(
        ["session", f"CH-{sid:06d}", "--all", "--db", str(db_path), "--json"]
    )
    assert code == 0
    payload = json.loads(out)
    shares = [m["share_index"] for m in payload["messages"]]
    contents = [m["content"] for m in payload["messages"]]
    assert shares == [1, 2, 3, 4]
    assert "target bubble" in contents
    assert STOP not in contents
    for m in payload["messages"]:
        assert m.get("timestamp"), m


def test_session_include_system(tmp_path: Path):
    _db, db_path, _owner, sid = _seed(tmp_path)
    code, out, _err = _run(
        [
            "session",
            f"CH-{sid:06d}",
            "--all",
            "--include-system",
            "--db",
            str(db_path),
            "--json",
        ]
    )
    assert code == 0
    payload = json.loads(out)
    roles = [m["role"] for m in payload["messages"]]
    assert "system" in roles
    sys_msg = next(m for m in payload["messages"] if m["role"] == "system")
    assert sys_msg["share_index"] is None


def test_parse():
    code, out, _err = _run(["parse", "CH-000430-99", "--json"])
    assert code == 0
    payload = json.loads(out)
    assert payload["session_id"] == 430
    assert payload["message_index"] == 99


def test_search_from_chat(tmp_path: Path):
    db, db_path, owner, sid = _seed(tmp_path)
    other = db.create_user("other@local", "Other", "local", password="x")
    other_sid = db.create_chat_session(other, "other flask")
    db.add_message(other_sid, "user", "secret flask note")

    code, out, _err = _run(
        [
            "search",
            "target bubble",
            "--from-chat",
            f"CH-{sid:06d}",
            "--db",
            str(db_path),
            "--json",
        ]
    )
    assert code == 0
    payload = json.loads(out)
    assert payload["user_id"] == owner
    ids = [r["id"] for r in payload["results"]]
    assert sid in ids
    assert other_sid not in ids


def test_help_lists_verbs():
    with pytest.raises(SystemExit) as ei:
        main(["--help"])
    assert ei.value.code == 0

"""Agents set a chat's active worktree: api.worktrees.chat_binding + CLI verbs."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from api.worktrees import __main__ as cli
from api.worktrees import chat_binding, registry


def _git(*args, cwd):
    base = ["git", "-c", "user.name=T", "-c", "user.email=t@t",
            "-c", "init.defaultBranch=main", "-c", "commit.gpgsign=false"]
    return subprocess.run(base + list(args), cwd=cwd, capture_output=True,
                          text=True, encoding="utf-8", timeout=60)


@pytest.fixture
def repo(monkeypatch, tmp_path):
    path = tmp_path / "primary"
    path.mkdir()
    _git("init", cwd=path)
    (path / "a.txt").write_text("one\n", encoding="utf-8")
    _git("add", ".", cwd=path)
    _git("commit", "-m", "seed", cwd=path)
    monkeypatch.setattr(
        cli, "_resolve_project",
        lambda pid: ({"id": int(pid), "name": "Demo", "default_branch": "main"}, str(path), None),
    )
    monkeypatch.setattr(cli, "_settings_block", lambda: {"root": str(tmp_path / "wt")})
    monkeypatch.delenv("CUTTLE_CHAT_SESSION_ID", raising=False)
    return path


def _run(capsys, argv):
    code = cli.main(argv)
    captured = capsys.readouterr()
    return code, json.loads(captured.out), captured.err


def _chat(db, uid, project_id=1, name="chat"):
    sid = db.create_chat_session(uid, name)
    if project_id is not None:
        db.set_session_project(sid, uid, project_id=project_id,
                               project_name="Demo", project_path="/demo")
    return sid


def test_create_bind_sets_calling_chat_without_warning(repo, owner_session, capsys, monkeypatch):
    db, uid = owner_session.db, owner_session.user_id
    sid = _chat(db, uid)
    monkeypatch.setenv("CUTTLE_CHAT_SESSION_ID", str(sid))

    code, out, err = _run(capsys, ["create", "--project-id", "1", "--branch", "dev/x", "--bind"])

    assert code == 0, out
    uid_ = out["worktree"]["checkout_uid"]
    assert db.get_session_project(sid)["checkout_uid"] == uid_
    assert out["binding"]["relation"] == "self" and "warning" not in out["binding"]
    assert "WARNING" not in err
    notes = [m for m in db.get_messages(sid) if m["role"] == "system"]
    assert notes and "dev/x" in notes[-1]["content"]


def test_bind_foreign_chat_warns_but_succeeds(repo, owner_session, capsys):
    db, uid = owner_session.db, owner_session.user_id
    mine, other = _chat(db, uid, name="mine"), _chat(db, uid, name="other")
    _code, created, _ = _run(capsys, ["create", "--project-id", "1", "--branch", "dev/y"])
    checkout = created["worktree"]["checkout_uid"]

    code, out, err = _run(capsys, ["bind", checkout, "--chat", f"CH-{other:06d}",
                                   "--as", f"CH-{mine:06d}"])

    assert code == 0
    assert out["binding"]["relation"] == "foreign"
    assert "not your chat" in out["binding"]["warning"] and "WARNING" in err
    assert db.get_session_project(other)["checkout_uid"] == checkout


def test_bind_descendant_has_no_warning(repo, owner_session):
    db, uid = owner_session.db, owner_session.user_id
    parent = _chat(db, uid, name="parent")
    child = db.create_subagent_chat_session(uid, parent_session_id=parent, session_name="c",
                                            project_id=1, project_name="Demo")
    grandchild = db.create_subagent_chat_session(uid, parent_session_id=child, session_name="g",
                                                 project_id=1, project_name="Demo")
    assert chat_binding.relation(parent, grandchild, db=db) == "descendant"
    result = chat_binding.bind_chat(grandchild, None, actor=parent, db=db)
    assert result["relation"] == "descendant" and "warning" not in result


def test_bind_without_actor_warns_unknown(repo, owner_session):
    db, uid = owner_session.db, owner_session.user_id
    sid = _chat(db, uid)
    result = chat_binding.bind_chat(sid, None, db=db)
    assert result["relation"] == "unknown" and "warning" in result


def test_bind_refuses_other_project_and_unknown_checkout(repo, owner_session, capsys):
    db, uid = owner_session.db, owner_session.user_id
    sid = _chat(db, uid, project_id=2)
    _code, created, _ = _run(capsys, ["create", "--project-id", "1", "--branch", "dev/z"])
    with pytest.raises(chat_binding.ChatBindingError, match="switch the chat's project"):
        chat_binding.bind_chat(sid, created["worktree"]["checkout_uid"], actor=sid, db=db)
    with pytest.raises(chat_binding.ChatBindingError, match="unknown or not ready"):
        chat_binding.bind_chat(sid, "nope", actor=sid, db=db)


def test_projectless_chat_adopts_checkout_project(repo, owner_session, capsys):
    db, uid = owner_session.db, owner_session.user_id
    sid = _chat(db, uid, project_id=None)
    _code, created, _ = _run(capsys, ["create", "--project-id", "1", "--branch", "dev/p"])
    chat_binding.bind_chat(
        sid, created["worktree"]["checkout_uid"], actor=sid, db=db,
        project_lookup=lambda pid: {"name": "Demo", "path": "/demo"},
    )
    row = db.get_session_project(sid)
    assert row["project_id"] == 1 and row["project_name"] == "Demo"


def test_bind_local_and_show(repo, owner_session, capsys, monkeypatch):
    db, uid = owner_session.db, owner_session.user_id
    sid = _chat(db, uid)
    monkeypatch.setenv("CUTTLE_CHAT_SESSION_ID", str(sid))
    _run(capsys, ["create", "--project-id", "1", "--branch", "dev/l", "--bind"])

    _code, shown, _ = _run(capsys, ["show"])
    assert shown["checkout"]["branch"] == "dev/l"

    code, out, _ = _run(capsys, ["bind", "--local"])
    assert code == 0 and out["binding"]["checkout"] is None
    assert db.get_session_project(sid)["checkout_uid"] is None
    assert db.get_session_project(sid)["project_id"] == 1


def test_adopt_registers_plain_git_worktree(repo, owner_session, capsys, tmp_path, monkeypatch):
    db, uid = owner_session.db, owner_session.user_id
    sid = _chat(db, uid)
    monkeypatch.setenv("CUTTLE_CHAT_SESSION_ID", str(sid))
    plain = repo / "temp" / "worktrees" / "reload"
    assert _git("worktree", "add", "-b", "dev/reload", str(plain), cwd=repo).returncode == 0

    code, out, _ = _run(capsys, ["adopt", str(plain), "--project-id", "1", "--bind"])
    assert code == 0, out
    assert out["worktree"]["branch"] == "dev/reload"
    assert registry.checkout_for_path(str(plain)) is not None
    assert db.get_session_project(sid)["checkout_uid"] == out["worktree"]["checkout_uid"]

    code, again, _ = _run(capsys, ["adopt", str(plain), "--project-id", "1"])
    assert code == 0 and again["worktree"]["checkout_uid"] == out["worktree"]["checkout_uid"]


def test_adopt_refuses_primary_and_strangers(repo, capsys, tmp_path):
    code, out, _ = _run(capsys, ["adopt", str(repo), "--project-id", "1"])
    assert code == 1 and "primary" in out["error"]
    stranger = tmp_path / "elsewhere"
    stranger.mkdir()
    code, out, _ = _run(capsys, ["adopt", str(stranger), "--project-id", "1"])
    assert code == 1 and "Not a git worktree" in out["error"]

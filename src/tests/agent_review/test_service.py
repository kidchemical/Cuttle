"""Agent review service against a real git repo and real turn snapshots.

Turns are driven through the same seams the harness kernel uses
(``agent_events.snapshots.begin/finish`` + ``agent_review.service`` hooks);
no agent CLI runs.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from api.agent_events.snapshots import begin, finish, record_step
from api.agent_review import service
from api.agent_review.store import ReviewStore
from api.experimental import flags

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

ORIGINAL = "one\ntwo\nthree\nfour\nfive\n"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CUTTLE_EXPERIMENTAL", raising=False)
    flags.set_enabled("agent_review", True)
    root = tmp_path / "proj"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "T")
    _git(root, "config", "user.email", "t@example.test")
    (root / "app.py").write_text(ORIGINAL)
    (root / ".gitignore").write_text("build/\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    return root.resolve()


def start(repo: Path, qid: str):
    snap = begin(str(repo), qid)
    service.turn_started(snap, query_id=qid, chat_session_id="42", agent_id="claude", model="m")
    return snap


def end(snap, qid: str) -> None:
    service.turn_finished(qid, finish(snap))


def files(repo):
    return {f["path"]: f for f in service.list_reviews(repo)["files"]}


def review(repo, path="app.py"):
    return service.file_review(repo, path)["review"]


def test_live_native_edit_is_reviewable_before_the_turn_ends(repo):
    snap = start(repo, "q1")
    (repo / "app.py").write_text("one\nTWO\nthree\nfour\nfive\nsix\n")
    service.note_agent_edit("q1", str(repo / "app.py"))
    r = review(repo)
    assert r["live"] is True and r["baseline"] == ORIGINAL and r["hunks"] == 2
    assert r["agent_id"] == "claude" and r["chat_session_id"] == "42" and r["query_ids"] == ["q1"]
    end(snap, "q1")
    assert review(repo)["live"] is False


def test_keep_and_undo_per_hunk_then_review_closes(repo):
    snap = start(repo, "q1")
    (repo / "app.py").write_text("one\nTWO\nthree\nfour\nfive\nsix\n")
    end(snap, "q1")
    rename, append = [h["id"] for h in review(repo)["hunk_list"]]
    service.keep(repo, ["app.py"], [rename])
    assert (repo / "app.py").read_text() == "one\nTWO\nthree\nfour\nfive\nsix\n"  # keep never touches disk
    assert [h["id"] for h in review(repo)["hunk_list"]] == [append]
    out = service.undo(repo, ["app.py"], [append])
    assert out["files"] == [{"path": "app.py", "closed": True}]
    assert (repo / "app.py").read_text() == "one\nTWO\nthree\nfour\nfive\n"
    assert review(repo) is None and files(repo) == {}


def test_unreported_writes_are_caught_at_turn_end_ignored_and_preexisting_are_not(repo):
    (repo / "app.py").write_text(ORIGINAL + "mine\n")  # user's uncommitted edit before the turn
    snap = start(repo, "q1")
    (repo / "build").mkdir()
    (repo / "build" / "out.txt").write_text("artifact\n")
    (repo / "notes.md").write_text("by a shell command\n")
    end(snap, "q1")
    found = files(repo)
    assert set(found) == {"notes.md"} and found["notes.md"]["change"] == "added"


def test_user_edits_between_turns_never_become_agent_hunks(repo):
    snap = start(repo, "q1")
    (repo / "app.py").write_text("one\nTWO\nthree\nfour\nfive\n")
    end(snap, "q1")
    # The user edits a distant line in their own editor.
    (repo / "app.py").write_text("one\nTWO\nthree\nfour\nFIVE (mine)\n")
    r = review(repo)
    assert r["hunks"] == 1 and r["hunk_list"][0]["added"] == ["TWO\n"]
    assert r["baseline"] == "one\ntwo\nthree\nfour\nFIVE (mine)\n" and r["mixed"] is False
    # Undoing the agent hunk keeps the user's edit.
    service.undo(repo, ["app.py"])
    assert (repo / "app.py").read_text() == "one\ntwo\nthree\nfour\nFIVE (mine)\n"


def test_user_edit_inside_an_agent_hunk_stays_in_the_hunk(repo):
    snap = start(repo, "q1")
    (repo / "app.py").write_text("one\nTWO\nthree\nfour\nfive\n")
    end(snap, "q1")
    (repo / "app.py").write_text("one\nTWO!!\nthree\nfour\nfive\n")
    r = review(repo)
    assert r["mixed"] is True and r["hunk_list"][0]["added"] == ["TWO!!\n"]


def test_stacked_turns_keep_the_earliest_baseline(repo):
    snap = start(repo, "q1")
    (repo / "app.py").write_text("one\nTWO\nthree\nfour\nfive\n")
    end(snap, "q1")
    snap = start(repo, "q2")
    (repo / "app.py").write_text("one\nTWO\nthree\nFOUR\nfive\n")
    service.note_agent_edit("q2", "app.py")
    end(snap, "q2")
    r = review(repo)
    assert r["baseline"] == ORIGINAL and r["hunks"] == 2 and r["query_ids"] == ["q1", "q2"]


def test_kept_file_mid_turn_reopens_only_with_new_changes(repo):
    snap = start(repo, "q1")
    (repo / "app.py").write_text("one\nTWO\nthree\nfour\nfive\n")
    service.note_agent_edit("q1", "app.py")
    service.keep(repo, ["app.py"])
    assert review(repo) is None
    (repo / "app.py").write_text("one\nTWO\nthree\nfour\nFIVE\n")
    service.note_agent_edit("q1", "app.py")
    r = review(repo)
    assert r["hunks"] == 1 and r["hunk_list"][0]["added"] == ["FIVE\n"]
    end(snap, "q1")
    assert review(repo)["hunks"] == 1


def test_undo_created_file_deletes_it_and_deleted_file_is_restored(repo):
    snap = start(repo, "q1")
    (repo / "new.py").write_text("print('hi')\n")
    (repo / "app.py").unlink()
    end(snap, "q1")
    found = files(repo)
    assert found["new.py"]["change"] == "added" and found["app.py"]["change"] == "deleted"
    service.undo(repo)  # every file under the root
    assert not (repo / "new.py").exists() and (repo / "app.py").read_text() == ORIGINAL
    assert files(repo) == {}


def test_stale_etag_and_stale_hunk_ids_are_conflicts(repo):
    snap = start(repo, "q1")
    (repo / "app.py").write_text("one\nTWO\nthree\nfour\nfive\n")
    end(snap, "q1")
    r = review(repo)
    with pytest.raises(service.Conflict):
        service.undo(repo, ["app.py"], [r["hunk_list"][0]["id"]], etag="stale")
    with pytest.raises(service.Conflict):
        service.keep(repo, ["app.py"], ["nope-0"])
    assert (repo / "app.py").read_text() == "one\nTWO\nthree\nfour\nfive\n"


def test_paths_cannot_escape_and_unknown_files_are_not_found(repo):
    with pytest.raises(service.ReviewError):
        service.file_review(repo, "../x")
    with pytest.raises(service.ReviewError):
        service.undo(repo, [".git/config"])
    with pytest.raises(service.NotFound):
        service.undo(repo, ["app.py"])


def test_state_survives_restart(repo):
    snap = start(repo, "q1")
    (repo / "app.py").write_text("one\nTWO\nthree\nfour\nfive\n")
    end(snap, "q1")
    rev = service.revision()
    # A fresh store instance on the same file is what a Flask restart sees.
    assert ReviewStore().revision() == rev
    assert files(repo)["app.py"]["hunks"] == 1


def test_step_snapshots_feed_live_paths(repo):
    snap = start(repo, "q1")
    (repo / "app.py").write_text("one\nTWO\nthree\nfour\nfive\n")
    step = record_step("q1", "tool-1")
    assert step and step["paths"] == ["app.py"]
    service.note_agent_paths("q1", step["paths"])
    assert review(repo)["live"] is True
    end(snap, "q1")


def test_workspace_subfolder_scopes_paths(repo):
    (repo / "pkg").mkdir()
    snap = start(repo, "q1")
    (repo / "pkg" / "mod.py").write_text("x = 1\n")
    (repo / "app.py").write_text("changed\n")
    end(snap, "q1")
    sub = service.list_reviews(repo / "pkg")
    assert [f["path"] for f in sub["files"]] == ["mod.py"] and sub["files"][0]["rel_path"] == "pkg/mod.py"


def test_hooks_are_no_ops_while_flag_is_off(repo):
    flags.set_enabled("agent_review", False)
    snap = start(repo, "q1")
    (repo / "app.py").write_text("changed\n")
    end(snap, "q1")
    flags.set_enabled("agent_review", True)
    assert files(repo) == {}


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------
@pytest.fixture()
def http(repo, tmp_path, monkeypatch):
    from api.ide import service as ide_service
    from tests.auth.test_http_authz import _auth_client

    project = {"id": 7, "name": "Demo", "path": str(repo), "available": True}
    monkeypatch.setattr(ide_service, "_registered", lambda: ([project], project))
    ctx = _auth_client(tmp_path, monkeypatch)
    ctx["client"].set_cookie("session_token", ctx["token"])
    return ctx


def test_flag_registered_default_off(monkeypatch):
    monkeypatch.delenv("CUTTLE_EXPERIMENTAL", raising=False)
    spec = next(f for f in flags.flags_payload()["flags"] if f["id"] == "agent_review")
    assert spec["default"] is False


def test_routes_disabled_while_flag_off(http, repo):
    flags.set_enabled("agent_review", False)
    client = http["client"]
    res = client.get("/api/agent-review/files", query_string={"root": str(repo)})
    assert res.status_code == 403 and res.get_json()["disabled"] is True
    assert client.post("/api/agent-review/undo", json={"root": str(repo)}).status_code == 403
    assert client.get("/api/agent-review/stream").status_code == 403


def test_routes_are_owner_only(http, repo):
    client = http["client"]
    client.set_cookie("session_token", http["other_token"])
    assert client.get("/api/agent-review/files", query_string={"root": str(repo)}).status_code == 403
    client.delete_cookie("session_token")
    assert client.post("/api/agent-review/keep", json={"root": str(repo)}).status_code == 401


def test_routes_list_keep_undo_and_conflict(http, repo, tmp_path):
    snap = start(repo, "q1")
    (repo / "app.py").write_text("one\nTWO\nthree\nfour\nfive\nsix\n")
    end(snap, "q1")
    client = http["client"]
    listed = client.get("/api/agent-review/files", query_string={"root": str(repo)}).get_json()
    assert listed["success"] and listed["totals"] == {"hunks": 2, "additions": 2, "deletions": 1}
    detail = client.get("/api/agent-review/file", query_string={"root": str(repo), "path": "app.py"}).get_json()["review"]
    first, second = [h["id"] for h in detail["hunk_list"]]
    stale = client.post("/api/agent-review/undo", json={"root": str(repo), "path": "app.py", "hunks": [first], "etag": "x"})
    assert stale.status_code == 409 and stale.get_json()["conflict"] is True
    ok = client.post("/api/agent-review/undo", json={"root": str(repo), "path": "app.py", "hunks": [first], "etag": detail["etag"]})
    assert ok.status_code == 200 and (repo / "app.py").read_text() == "one\ntwo\nthree\nfour\nfive\nsix\n"
    kept = client.post("/api/agent-review/keep", json={"root": str(repo), "path": "app.py", "hunks": [second]}).get_json()
    assert kept["files"] == [{"path": "app.py", "closed": True}]
    assert client.get("/api/agent-review/files", query_string={"root": str(repo)}).get_json()["files"] == []
    unregistered = client.get("/api/agent-review/files", query_string={"root": str(tmp_path)})
    assert unregistered.status_code == 400


def test_cli_list_show_keep(repo, monkeypatch, capsys):
    import json

    from api.agent_review.__main__ import main
    from api.ide import service as ide_service

    project = {"id": 7, "name": "Demo", "path": str(repo), "available": True}
    monkeypatch.setattr(ide_service, "_registered", lambda: ([project], project))
    snap = start(repo, "q1")
    (repo / "app.py").write_text("one\nTWO\nthree\nfour\nfive\n")
    end(snap, "q1")
    assert main(["list", "--root", str(repo), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["files"][0]["path"] == "app.py"
    assert main(["show", "--root", str(repo), "--path", "app.py"]) == 0
    assert "+TWO" in capsys.readouterr().out
    assert main(["keep", "--root", str(repo), "--path", "app.py"]) == 0
    assert main(["undo", "--root", str(repo), "--path", "app.py"]) == 1  # nothing left to undo
    assert (repo / "app.py").read_text() == "one\nTWO\nthree\nfour\nfive\n"


def test_commit_policy_setting_and_revision_routes(http, repo):
    client = http["client"]
    assert service.commit_policy() == "warn"
    got = client.get("/api/settings/agent-review").get_json()
    assert got == {"success": True, "commit_policy": "warn"}
    bad = client.post("/api/settings/agent-review", json={"commit_policy": "yolo"})
    assert bad.status_code == 400 and service.commit_policy() == "warn"
    ok = client.post("/api/settings/agent-review", json={"commit_policy": "keep"}).get_json()
    assert ok["commit_policy"] == "keep" and service.commit_policy() == "keep"
    # Other signed-in users can read the policy but not change it.
    client.set_cookie("session_token", http["other_token"])
    assert client.post("/api/settings/agent-review", json={"commit_policy": "warn"}).status_code == 403
    client.set_cookie("session_token", http["token"])
    before = client.get("/api/agent-review/revision").get_json()["revision"]
    snap = start(repo, "q1")
    (repo / "app.py").write_text("changed\n")
    end(snap, "q1")
    assert client.get("/api/agent-review/revision").get_json()["revision"] > before

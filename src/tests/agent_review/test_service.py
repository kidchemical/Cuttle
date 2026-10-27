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
    _git(root, "config", "core.autocrlf", "false")
    _git(root, "config", "user.name", "T")
    _git(root, "config", "user.email", "t@example.test")
    (root / "app.py").write_text(ORIGINAL, encoding="utf-8", newline="")
    (root / ".gitignore").write_text("build/\n", encoding="utf-8", newline="")
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
    (repo / "app.py").write_text("one\nTWO\nthree\nfour\nfive\nsix\n", encoding="utf-8", newline="")
    service.note_agent_edit("q1", str(repo / "app.py"))
    r = review(repo)
    assert r["live"] is True and r["baseline"] == ORIGINAL and r["hunks"] == 2
    assert r["agent_id"] == "claude" and r["chat_session_id"] == "42" and r["query_ids"] == ["q1"]
    end(snap, "q1")
    assert review(repo)["live"] is False


def test_keep_and_undo_per_hunk_then_review_closes(repo):
    snap = start(repo, "q1")
    (repo / "app.py").write_text("one\nTWO\nthree\nfour\nfive\nsix\n", encoding="utf-8", newline="")
    end(snap, "q1")
    rename, append = [h["id"] for h in review(repo)["hunk_list"]]
    service.keep(repo, ["app.py"], [rename])
    assert (repo / "app.py").read_text() == "one\nTWO\nthree\nfour\nfive\nsix\n"  # keep never touches disk
    assert [h["id"] for h in review(repo)["hunk_list"]] == [append]
    out = service.undo(repo, ["app.py"], [append])
    assert out["files"] == [{"path": "app.py", "closed": True}]
    assert (repo / "app.py").read_text() == "one\nTWO\nthree\nfour\nfive\n"
    assert review(repo) is None and files(repo) == {}


@pytest.mark.parametrize("use_attributes", [False, True], ids=["autocrlf", "gitattributes"])
def test_crlf_snapshot_baseline_preserves_separate_hunks_and_undo(repo, use_attributes):
    if use_attributes:
        (repo / ".gitattributes").write_bytes(b"app.py text eol=crlf\n")
    else:
        _git(repo, "config", "core.autocrlf", "true")
    original = ORIGINAL.replace("\n", "\r\n").encode("utf-8")
    (repo / "app.py").write_bytes(original)
    snap = start(repo, "crlf")
    (repo / "app.py").write_bytes(b"one\r\nTWO\r\nthree\r\nfour\r\nfive\r\nsix\r\n")
    service.note_agent_edit("crlf", "app.py")
    r = review(repo)
    assert r["baseline"].encode("utf-8") == original
    assert r["hunks"] == 2
    rename, append = [h["id"] for h in r["hunk_list"]]
    service.undo(repo, ["app.py"], [append])
    assert (repo / "app.py").read_bytes() == b"one\r\nTWO\r\nthree\r\nfour\r\nfive\r\n"
    service.undo(repo, ["app.py"], [rename])
    assert (repo / "app.py").read_bytes() == original
    end(snap, "crlf")
    assert review(repo) is None


def test_unreported_writes_are_caught_at_turn_end_ignored_and_preexisting_are_not(repo):
    (repo / "app.py").write_text(ORIGINAL + "mine\n", encoding="utf-8", newline="")  # user's uncommitted edit before the turn
    snap = start(repo, "q1")
    (repo / "build").mkdir()
    (repo / "build" / "out.txt").write_text("artifact\n", encoding="utf-8", newline="")
    (repo / "notes.md").write_text("by a shell command\n", encoding="utf-8", newline="")
    end(snap, "q1")
    found = files(repo)
    assert set(found) == {"notes.md"} and found["notes.md"]["change"] == "added"


def test_failed_checkout_filter_does_not_mark_existing_baseline_absent(repo):
    (repo / ".gitattributes").write_bytes(b"app.py filter=broken\n")
    _git(repo, "config", "filter.broken.smudge", "exit 1")
    _git(repo, "config", "filter.broken.required", "true")
    assert service._at_tree(str(repo), "HEAD", "app.py") == ("skip", None)
    assert (repo / "app.py").read_bytes() == ORIGINAL.encode("utf-8")


def test_user_edits_between_turns_never_become_agent_hunks(repo):
    snap = start(repo, "q1")
    (repo / "app.py").write_text("one\nTWO\nthree\nfour\nfive\n", encoding="utf-8", newline="")
    end(snap, "q1")
    # The user edits a distant line in their own editor.
    (repo / "app.py").write_text("one\nTWO\nthree\nfour\nFIVE (mine)\n", encoding="utf-8", newline="")
    r = review(repo)
    assert r["hunks"] == 1 and r["hunk_list"][0]["added"] == ["TWO\n"]
    assert r["baseline"] == "one\ntwo\nthree\nfour\nFIVE (mine)\n" and r["mixed"] is False
    # Undoing the agent hunk keeps the user's edit.
    service.undo(repo, ["app.py"])
    assert (repo / "app.py").read_text() == "one\ntwo\nthree\nfour\nFIVE (mine)\n"


def test_user_edit_inside_an_agent_hunk_stays_in_the_hunk(repo):
    snap = start(repo, "q1")
    (repo / "app.py").write_text("one\nTWO\nthree\nfour\nfive\n", encoding="utf-8", newline="")
    end(snap, "q1")
    (repo / "app.py").write_text("one\nTWO!!\nthree\nfour\nfive\n", encoding="utf-8", newline="")
    r = review(repo)
    assert r["mixed"] is True and r["hunk_list"][0]["added"] == ["TWO!!\n"]


def test_stacked_turns_keep_the_earliest_baseline(repo):
    snap = start(repo, "q1")
    (repo / "app.py").write_text("one\nTWO\nthree\nfour\nfive\n", encoding="utf-8", newline="")
    end(snap, "q1")
    snap = start(repo, "q2")
    (repo / "app.py").write_text("one\nTWO\nthree\nFOUR\nfive\n", encoding="utf-8", newline="")
    service.note_agent_edit("q2", "app.py")
    end(snap, "q2")
    r = review(repo)
    assert r["baseline"] == ORIGINAL and r["hunks"] == 2 and r["query_ids"] == ["q1", "q2"]


def test_kept_file_mid_turn_reopens_only_with_new_changes(repo):
    snap = start(repo, "q1")
    (repo / "app.py").write_text("one\nTWO\nthree\nfour\nfive\n", encoding="utf-8", newline="")
    service.note_agent_edit("q1", "app.py")
    service.keep(repo, ["app.py"])
    assert review(repo) is None
    (repo / "app.py").write_text("one\nTWO\nthree\nfour\nFIVE\n", encoding="utf-8", newline="")
    service.note_agent_edit("q1", "app.py")
    r = review(repo)
    assert r["hunks"] == 1 and r["hunk_list"][0]["added"] == ["FIVE\n"]
    end(snap, "q1")
    assert review(repo)["hunks"] == 1


def test_undo_created_file_deletes_it_and_deleted_file_is_restored(repo):
    snap = start(repo, "q1")
    (repo / "new.py").write_text("print('hi')\n", encoding="utf-8", newline="")
    (repo / "app.py").unlink()
    end(snap, "q1")
    found = files(repo)
    assert found["new.py"]["change"] == "added" and found["app.py"]["change"] == "deleted"
    service.undo(repo)  # every file under the root
    assert not (repo / "new.py").exists() and (repo / "app.py").read_text() == ORIGINAL
    assert files(repo) == {}


def test_stale_etag_and_stale_hunk_ids_are_conflicts(repo):
    snap = start(repo, "q1")
    (repo / "app.py").write_text("one\nTWO\nthree\nfour\nfive\n", encoding="utf-8", newline="")
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
    (repo / "app.py").write_text("one\nTWO\nthree\nfour\nfive\n", encoding="utf-8", newline="")
    end(snap, "q1")
    rev = service.revision()
    # A fresh store instance on the same file is what a Flask restart sees.
    assert ReviewStore().revision() == rev
    assert files(repo)["app.py"]["hunks"] == 1


def test_step_snapshots_feed_live_paths(repo):
    snap = start(repo, "q1")
    (repo / "app.py").write_text("one\nTWO\nthree\nfour\nfive\n", encoding="utf-8", newline="")
    step = record_step("q1", "tool-1")
    assert step and step["paths"] == ["app.py"]
    service.note_agent_paths("q1", step["paths"])
    assert review(repo)["live"] is True
    end(snap, "q1")


def test_workspace_subfolder_scopes_paths(repo):
    (repo / "pkg").mkdir()
    snap = start(repo, "q1")
    (repo / "pkg" / "mod.py").write_text("x = 1\n", encoding="utf-8", newline="")
    (repo / "app.py").write_text("changed\n", encoding="utf-8", newline="")
    end(snap, "q1")
    sub = service.list_reviews(repo / "pkg")
    assert [f["path"] for f in sub["files"]] == ["mod.py"] and sub["files"][0]["rel_path"] == "pkg/mod.py"


def test_hooks_are_no_ops_while_flag_is_off(repo):
    flags.set_enabled("agent_review", False)
    snap = start(repo, "q1")
    (repo / "app.py").write_text("changed\n", encoding="utf-8", newline="")
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
    (repo / "app.py").write_text("one\nTWO\nthree\nfour\nfive\nsix\n", encoding="utf-8", newline="")
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
    (repo / "app.py").write_text("one\nTWO\nthree\nfour\nfive\n", encoding="utf-8", newline="")
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
    (repo / "app.py").write_text("changed\n", encoding="utf-8", newline="")
    end(snap, "q1")
    assert client.get("/api/agent-review/revision").get_json()["revision"] > before


@pytest.mark.parametrize('kind', ['modified', 'added', 'deleted', 'crlf'])
def test_committing_agent_edits_closes_stale_reviews(repo, kind):
    if kind == 'crlf':
        (repo / '.gitattributes').write_text('app.py text eol=crlf\n', encoding='utf-8', newline='')
        _git(repo, 'add', '.gitattributes')
        _git(repo, 'commit', '-qm', 'attributes')
    path = 'new.py' if kind == 'added' else 'app.py'
    snap = start(repo, 'committed')
    if kind == 'deleted':
        (repo / path).unlink()
    else:
        (repo / path).write_bytes(b'accepted\r\n' if kind == 'crlf' else b'accepted\n')
    end(snap, 'committed')
    assert path in files(repo)
    _git(repo, 'add', '-A')
    _git(repo, 'commit', '-qm', 'accept agent edits')
    assert files(repo) == {}
    assert service.file_review(repo, path)['review'] is None


def test_staging_and_unrelated_commits_do_not_accept_agent_edits(repo):
    snap = start(repo, 'pending')
    (repo / 'app.py').write_text('unreviewed\n', encoding='utf-8', newline='')
    (repo / 'new.py').write_text('new unreviewed\n', encoding='utf-8', newline='')
    end(snap, 'pending')
    assert set(files(repo)) == {'app.py', 'new.py'}
    _git(repo, 'add', 'app.py')
    assert set(files(repo)) == {'app.py', 'new.py'}
    (repo / 'other.txt').write_text('unrelated\n', encoding='utf-8', newline='')
    _git(repo, 'add', 'other.txt')
    _git(repo, 'commit', '-qm', 'unrelated', '--', 'other.txt')
    assert set(files(repo)) == {'app.py', 'new.py'}


def test_newer_agent_edits_remain_reviewable_after_commit(repo):
    snap = start(repo, 'first')
    (repo / 'app.py').write_text('accepted\n', encoding='utf-8', newline='')
    end(snap, 'first')
    _git(repo, 'add', '-A')
    _git(repo, 'commit', '-qm', 'accept first turn')
    assert files(repo) == {}
    snap = start(repo, 'second')
    (repo / 'app.py').write_text('new pending edit\n', encoding='utf-8', newline='')
    end(snap, 'second')
    assert review(repo)['current'] == 'new pending edit\n'
    assert review(repo)['baseline'] == 'accepted\n'


def test_bulk_new_files_remain_reviewable(repo):
    snap = start(repo, 'import')
    (repo / 'app.py').write_text('edited\n', encoding='utf-8', newline='')
    for i in range(205):
        (repo / f'asset{i}.bin.txt').write_text('x\n', encoding='utf-8', newline='')
    end(snap, 'import')
    assert set(files(repo)) == {'app.py'} | {f'asset{i}.bin.txt' for i in range(205)}


@pytest.mark.parametrize('entrypoint', ['list', 'file', 'turn'])
def test_newly_ignored_reviews_close_without_changing_disk(repo, entrypoint):
    (repo / 'assets').mkdir()
    snap = start(repo, 'generated')
    asset = repo / 'assets' / 'generated.prefab'
    asset.write_bytes(b'generated\n')
    (repo / 'app.py').write_bytes(b'pending code\n')
    end(snap, 'generated')
    assert set(files(repo)) == {'app.py', 'assets/generated.prefab'}
    # app.py remains tracked even if a new ignore pattern matches it.
    (repo / '.gitignore').write_bytes(b'build/\nassets/\napp.py\n')
    before = service.revision()
    if entrypoint == 'file':
        assert service.file_review(repo, 'assets/generated.prefab')['review'] is None
    elif entrypoint == 'turn':
        end(start(repo, 'next'), 'next')
    assert set(files(repo)) == {'app.py'}
    assert service.revision() > before
    assert asset.read_bytes() == b'generated\n'
    assert (repo / 'app.py').read_bytes() == b'pending code\n'


def test_nested_files_keep_baseline_and_close_after_commit(repo):
    path = repo / 'pkg' / 'nested' / 'mod.py'
    path.parent.mkdir(parents=True)
    path.write_bytes(b'original\n')
    _git(repo, 'add', '-A')
    _git(repo, 'commit', '-qm', 'nested file')
    snap = start(repo, 'nested')
    path.write_bytes(b'changed\n')
    end(snap, 'nested')
    assert review(repo, 'pkg/nested/mod.py')['baseline'] == 'original\n'
    _git(repo, 'add', '-A')
    _git(repo, 'commit', '-qm', 'accept nested change')
    assert files(repo) == {}


def test_listing_untracked_new_files_uses_constant_git_calls(repo, monkeypatch):
    snap = start(repo, 'many')
    for i in range(40):
        (repo / f'new{i}.txt').write_text(f'{i}\n', encoding='utf-8', newline='')
    end(snap, 'many')
    calls = []
    real = service._git
    monkeypatch.setattr(service, '_git', lambda *a, **k: calls.append(a[1:]) or real(*a, **k))
    assert len(files(repo)) == 40
    assert len(calls) <= 4, calls


def test_listing_probes_disk_and_git_outside_the_write_lock(repo, monkeypatch):
    snap = start(repo, 'q1')
    (repo / 'app.py').write_text('edited\n', encoding='utf-8', newline='')
    end(snap, 'q1')
    real = service._probe

    def probe(*args, **kwargs):
        import sqlite3
        conn = sqlite3.connect(ReviewStore().path, timeout=0, isolation_level=None)
        try:
            conn.execute('BEGIN IMMEDIATE')  # raises "database is locked" if a writer holds it
            conn.execute('ROLLBACK')
        finally:
            conn.close()
        return real(*args, **kwargs)

    monkeypatch.setattr(service, '_probe', probe)
    assert set(files(repo)) == {'app.py'}
    start(repo, 'q2')


def test_partial_commit_accepts_only_committed_hunks(repo):
    snap = start(repo, 'partial')
    (repo / 'app.py').write_text('ONE\ntwo\nthree\nfour\nFIVE\n', encoding='utf-8', newline='')
    end(snap, 'partial')
    assert review(repo)['hunks'] == 2
    (repo / 'app.py').write_text('ONE\ntwo\nthree\nfour\nfive\n', encoding='utf-8', newline='')
    _git(repo, 'add', 'app.py')
    _git(repo, 'commit', '-qm', 'accept first hunk')
    (repo / 'app.py').write_text('ONE\ntwo\nthree\nfour\nFIVE\n', encoding='utf-8', newline='')
    r = review(repo)
    assert r['hunks'] == 1
    assert r['baseline'] == 'ONE\ntwo\nthree\nfour\nfive\n'
    assert r['hunk_list'][0]['added'] == ['FIVE\n']

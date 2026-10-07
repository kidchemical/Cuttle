"""Tests for observed edit attribution journal (no session guessing)."""

from __future__ import annotations

import subprocess
import hashlib
from pathlib import Path

import pytest


@pytest.fixture
def journal_db(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path))
    return tmp_path


def _git_init(repo: Path) -> None:
    subprocess.run(
        ["git", "init"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )


def test_append_and_build_attribution_unattributed(journal_db, tmp_path):
    from api.edit_attribution.journal import (
        append_events,
        build_commit_attribution,
        format_attribution_trailers,
        merge_message_with_trailers,
        settle_events,
    )

    repo = str((tmp_path / "proj").resolve())
    Path(repo).mkdir()
    _git_init(Path(repo))
    (Path(repo) / "src").mkdir()
    (Path(repo) / "src/a.py").write_text("final a")
    (Path(repo) / "src/b.py").write_text("final b")
    digest = lambda text: hashlib.sha256(text.encode()).hexdigest()
    append_events(
        [
            {
                "repo_root": repo,
                "rel_path": "src/a.py",
                "agent_id": "cursor",
                "model": "cursor-grok-4.6-high",
                "query_id": "q1",
                "digest_after": digest("intermediate a"),
            },
            {
                "repo_root": repo,
                "rel_path": "src/a.py",
                "agent_id": "codex",
                "model": "o3",
                "query_id": "q2",
                "digest_before": digest("intermediate a"),
                "digest_after": digest("final a"),
            },
            {
                "repo_root": repo,
                "rel_path": "src/b.py",
                "agent_id": "muse",
                "model": "muse-spark-1.3",
                "query_id": "q3",
                "digest_after": digest("final b"),
            },
        ]
    )

    attr = build_commit_attribution(repo, ["src/a.py", "src/b.py", "notes.txt"])
    assert attr["attributed_count"] == 2
    assert attr["unattributed_count"] == 1
    assert "notes.txt" in attr["unattributed"]
    assert len(attr["attributed"]["src/a.py"]) == 2
    agents = {p["agent_id"] for p in attr["attributed"]["src/a.py"]}
    assert agents == {"cursor", "codex"}
    assert attr["attributed"]["src/b.py"][0]["model"] == "muse-spark-1.3"
    assert attr["query_ids"] == ["q1", "q2", "q3"]

    trailers = format_attribution_trailers(attr)
    assert "Cuttle-Attributed:" in trailers
    assert "src/a.py=" in trailers
    assert "cursor/cursor-grok-4.6-high" in trailers
    assert "Cuttle-Unattributed: notes.txt" in trailers
    assert "Cuttle-Query: q1,q2,q3" in trailers

    msg = merge_message_with_trailers("Fix the door", attr)
    assert msg.startswith("Fix the door\n\n")
    assert "Cuttle-Attributed:" in msg

    n = settle_events(repo, ["src/a.py", "src/b.py"], "abc123full", event_ids=attr["event_ids"])
    assert n == 3
    # Settled events no longer open → all unattributed on next commit lookup.
    attr2 = build_commit_attribution(repo, ["src/a.py", "src/b.py"])
    assert attr2["attributed_count"] == 0
    assert set(attr2["unattributed"]) == {"src/a.py", "src/b.py"}


def test_record_run_deltas_only_observed_changes(journal_db, tmp_path, monkeypatch):
    from api.edit_attribution.journal import build_commit_attribution
    from api.edit_attribution.recorder import record_run_deltas, snapshot_for_attribution

    repo = tmp_path / "gitproj"
    repo.mkdir()
    _git_init(repo)
    (repo / "keep.txt").write_text("keep\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "-A"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )

    # Pre-existing dirty file (should NOT be attributed unless further changed).
    (repo / "dirty.txt").write_text("pre\n", encoding="utf-8")
    baseline = snapshot_for_attribution(str(repo))
    assert baseline and baseline.get("ok")

    # During "run": new file + further change dirty.txt
    (repo / "new.py").write_text("print(1)\n", encoding="utf-8")
    (repo / "dirty.txt").write_text("pre\nchanged\n", encoding="utf-8")

    n = record_run_deltas(
        baseline,
        cwd=str(repo),
        agent_id="cursor",
        agent_model="auto",
        result_model="cursor-grok-4.6-medium-fast",
        result_meta={
            "cursor_run": {
                "requested_model": "cursor-grok-4.6-medium-fast",
                "reported_model": "Cursor Grok 4.6 Medium Fast",
            }
        },
        query_id="run1",
        chat_session_id="382",
    )
    assert n >= 2

    attr = build_commit_attribution(
        str(repo.resolve()), ["new.py", "dirty.txt", "never_touched.txt"]
    )
    assert "new.py" in attr["attributed"]
    assert "dirty.txt" in attr["attributed"]
    assert "never_touched.txt" in attr["unattributed"]
    models = {p["model"] for p in attr["attributed"]["new.py"]}
    # Prefer reported_model from cursor_run when present.
    assert "Cursor Grok 4.6 Medium Fast" in models


def test_commit_pending_changes_adds_trailers(journal_db, tmp_path):
    from api.edit_attribution.journal import append_events
    from scripts.utilities.git_pending_changes import commit_pending_changes

    repo = tmp_path / "commitproj"
    repo.mkdir()
    _git_init(repo)
    (repo / "tracked.py").write_text("v1\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "-A"], cwd=str(repo), check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )

    (repo / "tracked.py").write_text("v2\n", encoding="utf-8")
    (repo / "orphan.txt").write_text("human\n", encoding="utf-8")

    root = str(repo.resolve())
    append_events(
        [
            {
                "repo_root": root,
                "rel_path": "tracked.py",
                "agent_id": "cursor",
                "model": "composer-2.5",
                "query_id": "abc",
                "digest_after": __import__("hashlib").sha256(b"v2\n").hexdigest(),
            }
        ]
    )

    result = commit_pending_changes(root, "Update tracked")
    assert result.get("commit")
    attr = result.get("attribution") or {}
    assert attr.get("attributed_count") == 1
    assert "tracked.py" in (attr.get("attributed") or {})
    assert "orphan.txt" in (attr.get("unattributed") or [])

    show = subprocess.run(
        ["git", "log", "-1", "--format=%B"],
        cwd=str(repo),
        capture_output=True,
        text=True, encoding="utf-8",
        check=True,
    )
    body = show.stdout or ""
    assert "Update tracked" in body
    assert "Cuttle-Attributed: tracked.py=cursor/composer-2.5" in body
    assert "Cuttle-Unattributed: orphan.txt" in body
    assert "Cuttle-Query: abc" in body


def test_old_content_cannot_credit_a_later_unobserved_change(journal_db,tmp_path):
    from api.edit_attribution.journal import append_events,build_commit_attribution
    repo=tmp_path/'proof';repo.mkdir();_git_init(repo)
    path=repo/'file.txt';path.write_text('human final')
    append_events([{'repo_root':str(repo),'rel_path':'file.txt','agent_id':'codex',
                    'digest_after':hashlib.sha256(b'agent earlier').hexdigest()}])
    assert build_commit_attribution(str(repo),['file.txt'])['attributed_count']==0


def test_ambiguous_edits_never_get_trailers(journal_db,tmp_path):
    from api.edit_attribution.journal import append_events,build_commit_attribution
    repo=tmp_path/'overlap';repo.mkdir();_git_init(repo)
    (repo/'file.txt').write_text('overlap')
    append_events([{'repo_root':str(repo),'rel_path':'file.txt','agent_id':'codex',
                    'digest_after':hashlib.sha256(b'overlap').hexdigest(),'ambiguous':True}])
    assert build_commit_attribution(str(repo),['file.txt'])['attributed_count']==0


def test_terminal_commit_settles_by_content(journal_db,tmp_path):
    from api.edit_attribution.journal import append_events,reconcile_commits,open_events_for_paths,_db_path
    repo=tmp_path/'terminal';repo.mkdir();_git_init(repo)
    (repo/'file.txt').write_text('agent change')
    append_events([{'repo_root':str(repo),'rel_path':'file.txt','agent_id':'codex',
                    'digest_after':hashlib.sha256(b'agent change').hexdigest()}])
    subprocess.run(['git','add','.'],cwd=repo,check=True,capture_output=True)
    subprocess.run(['git','commit','-m','terminal commit'],cwd=repo,check=True,capture_output=True)
    assert reconcile_commits(_db_path())==1
    assert open_events_for_paths(str(repo),['file.txt'])==[]

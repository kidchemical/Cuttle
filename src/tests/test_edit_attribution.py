"""Tests for observed edit attribution journal (no session guessing)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def journal_db(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "api.edit_attribution.journal._cuttle_root",
        lambda: tmp_path,
    )
    db_dir = tmp_path / "src" / "data" / "workspace" / "edit_attribution"
    db_dir.mkdir(parents=True, exist_ok=True)
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
    append_events(
        [
            {
                "repo_root": repo,
                "rel_path": "src/a.py",
                "agent_id": "cursor",
                "model": "cursor-grok-4.6-high",
                "query_id": "q1",
            },
            {
                "repo_root": repo,
                "rel_path": "src/a.py",
                "agent_id": "codex",
                "model": "o3",
                "query_id": "q2",
            },
            {
                "repo_root": repo,
                "rel_path": "src/b.py",
                "agent_id": "muse",
                "model": "muse-spark-1.3",
                "query_id": "q3",
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
        text=True,
        check=True,
    )
    body = show.stdout or ""
    assert "Update tracked" in body
    assert "Cuttle-Attributed: tracked.py=cursor/composer-2.5" in body
    assert "Cuttle-Unattributed: orphan.txt" in body
    assert "Cuttle-Query: abc" in body

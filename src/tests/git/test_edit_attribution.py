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
    assert reconcile_commits(_db_path())['settled']==1
    assert open_events_for_paths(str(repo),['file.txt'])==[]


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest() if text is not None else None


def _commit(repo, message):
    subprocess.run(['git','add','-A'],cwd=repo,check=True,capture_output=True)
    subprocess.run(['git','commit','-qm',message],cwd=repo,check=True,capture_output=True)


def _settlements(repo):
    import sqlite3
    from api.edit_attribution.journal import _db_path
    conn=sqlite3.connect(_db_path())
    try:
        return {qid:(state,sha) for qid,state,sha in conn.execute(
            'SELECT query_id,settlement,commit_sha FROM edit_events WHERE repo_root=? ORDER BY ts',(str(repo.resolve()),))}
    finally:
        conn.close()


def _edit(repo,qid,before,after,ts):
    from api.edit_attribution.journal import append_events
    append_events([{'repo_root':str(repo),'rel_path':'file.txt','agent_id':'codex','query_id':qid,
                    'digest_before':_sha(before),'digest_after':_sha(after),'ts':ts}])


def test_deletion_settles_to_the_deleting_commit(journal_db,tmp_path):
    import time
    from api.edit_attribution.journal import reconcile_commits,_db_path
    repo=tmp_path/'del';repo.mkdir();_git_init(repo)
    (repo/'file.txt').write_text('keep me');_commit(repo,'add')
    _edit(repo,'q-del','keep me',None,time.time()-5)
    (repo/'file.txt').unlink();_commit(repo,'delete')
    assert reconcile_commits(_db_path())['settled']==1
    assert _settlements(repo)['q-del'][0]=='settled'


def test_chained_edit_settles_with_the_later_commit(journal_db,tmp_path):
    import time
    from api.edit_attribution.journal import reconcile_commits,_db_path
    repo=tmp_path/'chain';repo.mkdir();_git_init(repo)
    (repo/'file.txt').write_text('A');_commit(repo,'base')
    _edit(repo,'q1','A','B',time.time()-20)
    _edit(repo,'q2','B','C',time.time()-10)
    (repo/'file.txt').write_text('C');_commit(repo,'terminal commit of C')
    stats=reconcile_commits(_db_path())
    assert stats['settled']==1 and stats['chained']==1
    rows=_settlements(repo)
    assert rows['q1'][0]==rows['q2'][0]=='settled' and rows['q1'][1]==rows['q2'][1]


def test_broken_chain_supersedes_the_earlier_edit(journal_db,tmp_path):
    import time
    from api.edit_attribution.journal import reconcile_commits,_db_path,build_commit_attribution
    repo=tmp_path/'broken';repo.mkdir();_git_init(repo)
    (repo/'file.txt').write_text('A');_commit(repo,'base')
    _edit(repo,'q1','A','B',time.time()-20)
    # Someone else turned B into X between the two observed edits.
    _edit(repo,'q2','X','Y',time.time()-10)
    (repo/'file.txt').write_text('Y')
    stats=reconcile_commits(_db_path())
    assert stats['superseded']==1
    rows=_settlements(repo)
    assert rows['q1'][0]=='superseded' and rows['q2'][0]=='open'
    subprocess.run(['git','add','file.txt'],cwd=repo,check=True,capture_output=True)
    attribution=build_commit_attribution(str(repo),['file.txt'])
    assert attribution['query_ids']==['q2']


def test_revert_closes_after_grace_only(journal_db,tmp_path):
    import time
    from api.edit_attribution.journal import reconcile_commits,_db_path
    repo=tmp_path/'revert';repo.mkdir();_git_init(repo)
    (repo/'file.txt').write_text('A');_commit(repo,'base')
    _edit(repo,'fresh','A','B',time.time()-60)
    (repo/'file.txt').write_text('A')
    assert reconcile_commits(_db_path())['reverted']==0  # within grace: maybe a branch switch
    assert reconcile_commits(_db_path(),now=time.time()+2*86400)['reverted']==1
    assert _settlements(repo)['fresh'][0]=='reverted'


def test_net_noop_chain_is_reverted(journal_db,tmp_path):
    import time
    from api.edit_attribution.journal import reconcile_commits,_db_path
    repo=tmp_path/'noop';repo.mkdir();_git_init(repo)
    (repo/'file.txt').write_text('A');_commit(repo,'base')
    # Edits happen after the base commit (an identical earlier blob never settles).
    _edit(repo,'there','A','B',time.time()+5)
    _edit(repo,'back','B','A',time.time()+10)
    stats=reconcile_commits(_db_path(),now=time.time()+2*86400)
    assert stats['reverted']==2
    assert {state for state,_sha in _settlements(repo).values()}=={'reverted'}


def test_content_changed_before_commit_is_superseded(journal_db,tmp_path):
    import time
    from api.edit_attribution.journal import reconcile_commits,_db_path
    repo=tmp_path/'later';repo.mkdir();_git_init(repo)
    (repo/'file.txt').write_text('A');_commit(repo,'base')
    _edit(repo,'agent','A','B',time.time()-30)
    (repo/'file.txt').write_text('B plus a human tweak');_commit(repo,'human commit')
    stats=reconcile_commits(_db_path(),now=time.time()+2*86400)
    assert stats['superseded']==1 and stats['settled']==0
    assert _settlements(repo)['agent'][0]=='superseded'


def test_uncommitted_current_edit_stays_open(journal_db,tmp_path):
    import time
    from api.edit_attribution.journal import reconcile_commits,_db_path
    repo=tmp_path/'pending';repo.mkdir();_git_init(repo)
    (repo/'file.txt').write_text('A');_commit(repo,'base')
    _edit(repo,'agent','A','B',time.time()-30)
    (repo/'file.txt').write_text('B')
    stats=reconcile_commits(_db_path(),now=time.time()+2*86400)
    assert stats=={'settled':0,'chained':0,'superseded':0,'reverted':0}
    assert _settlements(repo)['agent'][0]=='open'


def test_overlapping_duplicate_observations_do_not_supersede(journal_db,tmp_path):
    """Two concurrent runs recording the same change are ambiguous: they settle by
    content for bookkeeping, never close each other, and are never credited."""
    import time
    from api.edit_attribution.journal import append_events,reconcile_commits,_db_path
    repo=tmp_path/'dupe';repo.mkdir();_git_init(repo)
    (repo/'file.txt').write_text('A');_commit(repo,'base')
    for qid,ts in (('run-a',time.time()+5),('run-b',time.time()+6)):
        append_events([{'repo_root':str(repo),'rel_path':'file.txt','agent_id':'codex','query_id':qid,
                        'digest_before':_sha('A'),'digest_after':_sha('B'),'ts':ts,'ambiguous':True}])
    stats=reconcile_commits(_db_path(),now=time.time()+2*86400)
    assert stats['superseded']==0 and stats['reverted']==0
    assert {state for state,_ in _settlements(repo).values()}=={'open'}
    (repo/'file.txt').write_text('B')
    import os
    os.utime(repo/'file.txt')
    subprocess.run(['git','add','-A'],cwd=repo,check=True,capture_output=True)
    subprocess.run(['git','commit','-qm','B','--date',str(int(time.time())+10)],cwd=repo,check=True,capture_output=True,
                   env={**os.environ,'GIT_COMMITTER_DATE':str(int(time.time())+10)})
    assert reconcile_commits(_db_path())['settled']==2

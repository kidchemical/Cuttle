"""Tests for git pending-changes helpers."""

from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

from scripts.utilities.git_pending_changes import (
    _parse_numstat,
    _status_label,
    collect_file_pending_diff,
    collect_pending_changes,
    parse_unified_diff,
    resolve_allowed_project_cwd,
)


def test_parse_numstat_basic():
    text = "10\t2\tsrc/a.py\n3\t0\tsrc/b.js\n-\t-\tbin/x.png\n"
    got = _parse_numstat(text)
    assert got["src/a.py"] == (10, 2)
    assert got["src/b.js"] == (3, 0)
    assert got["bin/x.png"] == (0, 0)


def test_parse_numstat_rename():
    text = "1\t1\toold => new\n"
    got = _parse_numstat(text)
    assert got["new"] == (1, 1)


def test_status_label():
    assert _status_label("??") == "untracked"
    assert _status_label(" M") == "modified"
    assert _status_label("M ") == "modified"
    assert _status_label("A ") == "added"
    assert _status_label(" D") == "deleted"
    assert _status_label("UU") == "conflict"


def test_resolve_allowed_project_cwd(tmp_path: Path):
    proj_dir = tmp_path / "DemoGame"
    proj_dir.mkdir()
    projects = [{"id": 7, "name": "Demo Game", "path": str(proj_dir)}]
    cwd, proj, err = resolve_allowed_project_cwd(str(proj_dir), None, projects, None)
    assert err is None
    assert proj["id"] == 7
    assert Path(cwd) == proj_dir.resolve()

    cwd2, proj2, err2 = resolve_allowed_project_cwd(None, 7, projects, None)
    assert err2 is None and proj2["id"] == 7 and cwd2

    bad = resolve_allowed_project_cwd(str(tmp_path / "nope"), None, projects, None)
    assert bad[0] is None and bad[2]


def test_resolve_allowed_rewrites_windows_cuttle_src_path():
    import os

    if os.name == "nt":
        pytest.skip("Windows checkout already uses native paths")
    from core.runtime_paths import rewrite_windows_cuttle_path

    live = rewrite_windows_cuttle_path(r"C:\Projects\Cuttle\src")
    assert Path(live).is_dir()
    projects = [{"id": 4, "name": "Cuttle", "path": r"C:\Projects\Cuttle\src"}]
    cwd, proj, err = resolve_allowed_project_cwd(r"C:\Projects\Cuttle\src", None, projects, None)
    assert err is None
    assert proj["id"] == 4
    assert Path(cwd) == Path(live).resolve()

    cwd2, proj2, err2 = resolve_allowed_project_cwd(None, 4, projects, None)
    assert err2 is None and Path(cwd2) == Path(live).resolve()


def test_git_run_timeout_returns_124(monkeypatch):
    from scripts.utilities import git_pending_changes as gpc

    class FakeProc:
        pid = 4242
        _n = 0

        def communicate(self, timeout=None):
            type(self)._n += 1
            if type(self)._n == 1:
                raise subprocess.TimeoutExpired(cmd=["git", "status"], timeout=timeout or 8)
            return ("", "")

        def kill(self):
            return None

    FakeProc._n = 0
    monkeypatch.setattr(gpc.subprocess, "Popen", lambda *a, **k: FakeProc())
    monkeypatch.setattr(gpc, "_kill_process_tree", lambda pid: None)
    r = gpc.git_run(["status"], cwd=".", timeout=8)
    assert r.returncode == 124
    assert "timed out" in (r.stderr or "")


def test_collect_pending_changes_skips_when_busy(tmp_path: Path, monkeypatch):
    from scripts.utilities import git_pending_changes as gpc

    repo = str(tmp_path / "repo")
    Path(repo).mkdir()
    monkeypatch.setattr(gpc, "resolve_git_workdir", lambda cwd: repo)
    lock = gpc.threading.Lock()
    lock.acquire()
    gpc._collect_locks[repo] = lock
    with pytest.raises(RuntimeError, match="already running"):
        gpc.collect_pending_changes(repo)


def test_collect_pending_changes_waits_for_lock(tmp_path: Path, monkeypatch):
    from scripts.utilities import git_pending_changes as gpc

    repo = str(tmp_path / "repo")
    Path(repo).mkdir()
    monkeypatch.setattr(gpc, "resolve_git_workdir", lambda cwd: repo)
    monkeypatch.setattr(
        gpc,
        "_collect_pending_changes_locked",
        lambda root, **kw: {"repo_root": root, "clean": True, "files": [], "totals": {"files": 0}},
    )
    lock = gpc.threading.Lock()
    lock.acquire()
    gpc._collect_locks[repo] = lock

    def release_soon():
        time.sleep(0.15)
        lock.release()

    t = threading.Thread(target=release_soon, daemon=True)
    t.start()
    data = gpc.collect_pending_changes(repo, wait_timeout=2.0)
    assert data["repo_root"] == repo
    t.join(timeout=2.0)


def test_heavy_collect_uses_path_limited_numstat(tmp_path: Path, monkeypatch):
    from scripts.utilities import git_pending_changes as gpc

    repo = str(tmp_path / "repo")
    Path(repo).mkdir()
    monkeypatch.setattr(gpc, "resolve_git_workdir", lambda cwd: repo)

    status_lines = "\n".join(f" D path/{i:04d}.txt" for i in range(300))

    calls: list = []

    def fake_git(args, cwd, timeout=20.0):
        calls.append(list(args))
        if args[:2] == ["branch", "--show-current"]:
            return subprocess.CompletedProcess(args, 0, "main\n", "")
        if args[:2] == ["status", "--porcelain"]:
            return subprocess.CompletedProcess(args, 0, status_lines + "\n", "")
        if args[:3] == ["diff", "--numstat", "HEAD"]:
            # Path-limited calls include "--"
            assert "--" in args, "heavy tree must not run full-repo numstat"
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(gpc, "git_run", fake_git)
    data = gpc.collect_pending_changes(repo, max_files=50)
    assert data["truncated"] is True
    assert data["pending_file_count"] == 300
    assert len(data["files"]) == 50
    assert any(c[:3] == ["diff", "--numstat", "HEAD"] and "--" in c for c in calls)


def test_collect_pending_changes_temp_repo(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    def git(*args):
        r = subprocess.run(
            ["git", *args],
            cwd=str(repo),
            capture_output=True,
            text=True,
            check=False,
        )
        assert r.returncode == 0, r.stderr
        return r

    git("init")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    (repo / "a.txt").write_text("one\n", encoding="utf-8")
    git("add", "a.txt")
    git("commit", "-m", "init")
    (repo / "a.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    (repo / "b.txt").write_text("new\nfile\n", encoding="utf-8")

    data = collect_pending_changes(str(repo))
    assert data["clean"] is False
    assert data["totals"]["files"] >= 2
    paths = {f["path"] for f in data["files"]}
    assert "a.txt" in paths
    assert "b.txt" in paths
    assert data["totals"]["additions"] >= 2

    from scripts.utilities.git_pending_changes import collect_commit_suggest_context

    ctx = collect_commit_suggest_context(str(repo))
    assert "a.txt" in ctx["file_summary"]
    assert ctx["heuristic_message"]
    assert "diff" in (ctx["diff_excerpt"] or "").lower() or "+++ b/b.txt" in (ctx["diff_excerpt"] or "")


def test_suggest_context_keeps_selected_file_past_truncation(tmp_path: Path):
    """A selected file the list cap cut off must still resolve (no 'No selected files')."""
    from scripts.utilities.git_pending_changes import collect_commit_suggest_context

    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        r = subprocess.run(
            ["git", *args],
            cwd=str(repo),
            capture_output=True,
            text=True,
            check=False,
        )
        assert r.returncode == 0, r.stderr
        return r

    git("init")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    git("add", "seed.txt")
    git("commit", "-m", "init")
    for i in range(10):
        (repo / f"a_{i:02d}.txt").write_text(" filler\n", encoding="utf-8")
    (repo / "zzz-target.txt").write_text("target\n", encoding="utf-8")

    ctx = collect_commit_suggest_context(str(repo), max_files=5, paths=["zzz-target.txt"])
    assert [f["path"] for f in ctx["files"]] == ["zzz-target.txt"]
    assert "zzz-target.txt" in ctx["file_summary"]
    assert ctx["heuristic_message"]


def test_resolve_git_workdir_nested_source(tmp_path: Path):
    from scripts.utilities.git_pending_changes import resolve_git_workdir

    outer = tmp_path / "DemoGame"
    source = outer / "source"
    source.mkdir(parents=True)

    def git(*args):
        r = subprocess.run(
            ["git", *args],
            cwd=str(source),
            capture_output=True,
            text=True,
            check=False,
        )
        assert r.returncode == 0, r.stderr
        return r

    git("init")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    (source / "a.txt").write_text("x\n", encoding="utf-8")
    git("add", "a.txt")
    git("commit", "-m", "init")

    assert Path(resolve_git_workdir(str(outer))).resolve() == source.resolve()
    data = collect_pending_changes(str(outer))
    assert Path(data["repo_root"]).resolve() == source.resolve()
    assert data["clean"] is True


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    def git(*args):
        r = subprocess.run(
            ["git", *args],
            cwd=str(path),
            capture_output=True,
            text=True,
            check=False,
        )
        assert r.returncode == 0, r.stderr
        return r
    git("init")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    (path / "a.txt").write_text("x\n", encoding="utf-8")
    git("add", "a.txt")
    git("commit", "-m", "init")


def test_discover_sibling_git_repos(tmp_path: Path):
    from scripts.utilities.git_pending_changes import (
        collect_project_pending_changes,
        discover_git_workdirs,
        repo_label,
        resolve_allowed_repo_root,
        resolve_git_workdir,
    )

    parent = tmp_path / "Blender"
    a = parent / "Studio Intro Video"
    b = parent / "Studio Intro Video GOLDEN"
    _init_repo(a)
    _init_repo(b)
    (b / "b.txt").write_text("dirty\n", encoding="utf-8")

    roots = discover_git_workdirs(str(parent))
    resolved = {Path(r).resolve() for r in roots}
    assert a.resolve() in resolved
    assert b.resolve() in resolved
    assert len(roots) == 2

    primary = Path(resolve_git_workdir(str(parent))).resolve()
    assert primary in resolved

    payload = collect_project_pending_changes(str(parent))
    assert len(payload["repos"]) == 2
    by_root = {Path(r["repo_root"]).resolve(): r for r in payload["repos"]}
    assert by_root[a.resolve()]["clean"] is True
    assert by_root[b.resolve()]["clean"] is False
    assert repo_label(str(a), str(parent)) == "Studio Intro Video"

    got = resolve_allowed_repo_root(str(parent), str(b))
    assert Path(got).resolve() == b.resolve()
    with pytest.raises(ValueError, match="not a git worktree"):
        resolve_allowed_repo_root(str(parent), str(tmp_path / "nope"))


def test_discover_parent_repo_ignores_nested_child(tmp_path: Path):
    from scripts.utilities.git_pending_changes import discover_git_workdirs

    parent = tmp_path / "CuttleLike"
    _init_repo(parent)
    child = parent / "vendor" / "nested"
    # vendor is skipped; use a non-skipped name
    child = parent / "tools" / "nested"
    _init_repo(child)

    roots = discover_git_workdirs(str(parent))
    assert len(roots) == 1
    assert Path(roots[0]).resolve() == parent.resolve()


def test_commit_pending_changes(tmp_path: Path):
    from scripts.utilities.git_pending_changes import commit_pending_changes

    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        r = subprocess.run(
            ["git", *args],
            cwd=str(repo),
            capture_output=True,
            text=True,
            check=False,
        )
        assert r.returncode == 0, r.stderr
        return r

    git("init")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    (repo / "a.txt").write_text("one\n", encoding="utf-8")
    git("add", "a.txt")
    git("commit", "-m", "init")
    (repo / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
    (repo / "b.txt").write_text("new\n", encoding="utf-8")

    with pytest.raises(ValueError, match="required"):
        commit_pending_changes(str(repo), "   ")

    result = commit_pending_changes(str(repo), "add second line and new file")
    assert result["commit"]
    assert result["files_count"] >= 2
    assert collect_pending_changes(str(repo))["clean"] is True


def test_commit_pending_changes_without_git_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from scripts.utilities.git_pending_changes import commit_pending_changes

    monkeypatch.setenv("GITEA_COMMIT_AUTHOR_NAME", "Cuttle")
    monkeypatch.setenv("GITEA_COMMIT_AUTHOR_EMAIL", "cuttle@example.com")
    monkeypatch.delenv("GIT_AUTHOR_NAME", raising=False)
    monkeypatch.delenv("GIT_AUTHOR_EMAIL", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)


    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        r = subprocess.run(
            ["git", *args],
            cwd=str(repo),
            capture_output=True,
            text=True,
            check=False,
        )
        assert r.returncode == 0, r.stderr
        return r

    git("init")
    (repo / "a.txt").write_text("one\n", encoding="utf-8")
    git("add", "a.txt")
    git("-c", "user.email=init@example.com", "-c", "user.name=Init", "commit", "-m", "init")
    (repo / "a.txt").write_text("two\n", encoding="utf-8")

    result = commit_pending_changes(str(repo), "linux identity fallback")
    assert result["commit"]
    who = subprocess.run(
        ["git", "log", "-1", "--format=%an <%ae>"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Cuttle" in who.stdout
    assert "cuttle@example.com" in who.stdout

    with pytest.raises(ValueError, match="Nothing to commit"):
        commit_pending_changes(str(repo), "empty")


def test_commit_clears_stale_index_lock(tmp_path: Path):
    import os
    import time

    from scripts.utilities.git_pending_changes import (
        clear_stale_index_lock,
        commit_pending_changes,
    )

    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        r = subprocess.run(
            ["git", *args],
            cwd=str(repo),
            capture_output=True,
            text=True,
            check=False,
        )
        assert r.returncode == 0, r.stderr
        return r

    git("init")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    (repo / "a.txt").write_text("one\n", encoding="utf-8")
    git("add", "a.txt")
    git("commit", "-m", "init")
    (repo / "a.txt").write_text("one\ntwo\n", encoding="utf-8")

    git_dir = (repo / ".git").resolve()
    lock = git_dir / "index.lock"
    lock.write_bytes(b"")
    stale = time.time() - 3600
    os.utime(lock, (stale, stale))

    assert lock.is_file()
    assert clear_stale_index_lock(str(repo)) is True
    assert not lock.is_file()

    lock.write_bytes(b"")
    os.utime(lock, (stale, stale))
    result = commit_pending_changes(str(repo), "recover from stale lock")
    assert result["commit"]
    assert not lock.is_file()
    assert collect_pending_changes(str(repo))["clean"] is True


def test_fresh_index_lock_is_left_alone(tmp_path: Path):
    from scripts.utilities.git_pending_changes import clear_stale_index_lock

    repo = tmp_path / "repo"
    repo.mkdir()
    r = subprocess.run(
        ["git", "init"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stderr

    lock = (repo / ".git" / "index.lock")
    lock.write_bytes(b"")
    assert clear_stale_index_lock(str(repo), min_age_sec=60.0) is False
    assert lock.is_file()


def test_commit_pending_blocks_secrets(tmp_path: Path):
    from scripts.utilities.git_pending_changes import commit_pending_changes

    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        r = subprocess.run(
            ["git", *args],
            cwd=str(repo),
            capture_output=True,
            text=True,
            check=False,
        )
        assert r.returncode == 0, r.stderr
        return r

    git("init")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    (repo / "ok.txt").write_text("x\n", encoding="utf-8")
    git("add", "ok.txt")
    git("commit", "-m", "init")
    (repo / ".env").write_text("SECRET=1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="secret"):
        commit_pending_changes(str(repo), "leak")

    result = commit_pending_changes(str(repo), "allow env", allow_secrets=True)
    assert result["commit"]
    assert any(p.endswith(".env") or p == ".env" for p in result["files_committed"])


def test_commit_pending_allows_env_example_template(tmp_path: Path):
    from scripts.utilities.git_pending_changes import (
        _looks_like_secret_path,
        commit_pending_changes,
    )

    assert _looks_like_secret_path(".env.example") is False
    assert _looks_like_secret_path(".env.sample") is False
    assert _looks_like_secret_path(".env.local") is True
    assert _looks_like_secret_path(".env") is True

    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        r = subprocess.run(
            ["git", *args],
            cwd=str(repo),
            capture_output=True,
            text=True,
            check=False,
        )
        assert r.returncode == 0, r.stderr
        return r

    git("init")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    (repo / "ok.txt").write_text("x\n", encoding="utf-8")
    git("add", "ok.txt")
    git("commit", "-m", "init")
    (repo / ".env.example").write_text("OPENAI_API_KEY=\n", encoding="utf-8")

    result = commit_pending_changes(str(repo), "template ok")
    assert result["commit"]
    assert ".env.example" in result["files_committed"]


def test_commit_pending_selected_paths_only(tmp_path: Path):
    from scripts.utilities.git_pending_changes import commit_pending_changes

    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        r = subprocess.run(
            ["git", *args],
            cwd=str(repo),
            capture_output=True,
            text=True,
            check=False,
        )
        assert r.returncode == 0, r.stderr
        return r

    git("init")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    (repo / "a.txt").write_text("one\n", encoding="utf-8")
    git("add", "a.txt")
    git("commit", "-m", "init")
    (repo / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
    (repo / "b.txt").write_text("new\n", encoding="utf-8")
    (repo / "c.txt").write_text("skip\n", encoding="utf-8")

    result = commit_pending_changes(str(repo), "only a and b", paths=["a.txt", "b.txt"])
    assert result["commit"]
    committed = {p.replace("\\", "/") for p in result["files_committed"]}
    assert "a.txt" in committed
    assert "b.txt" in committed
    assert "c.txt" not in committed

    pending = collect_pending_changes(str(repo))
    assert pending["clean"] is False
    assert {f["path"] for f in pending["files"]} == {"c.txt"}


def test_commit_ignored_tracked_deletions(tmp_path: Path):
    """Commit still works when selected paths include a gitignored /temp/ tree."""
    from scripts.utilities.git_pending_changes import commit_pending_changes

    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        r = subprocess.run(
            ["git", *args],
            cwd=str(repo),
            capture_output=True,
            text=True,
            check=False,
        )
        assert r.returncode == 0, r.stderr
        return r

    git("init")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    (repo / "keep.txt").write_text("k\n", encoding="utf-8")
    (repo / "temp").mkdir()
    (repo / "temp" / "old.py").write_text("x\n", encoding="utf-8")
    git("add", "keep.txt", "temp/old.py")
    git("commit", "-m", "init")
    (repo / ".gitignore").write_text("/temp/\n", encoding="utf-8")
    (repo / "temp" / "old.py").unlink()
    (repo / "keep.txt").write_text("k2\n", encoding="utf-8")

    result = commit_pending_changes(
        str(repo),
        "drop ignored temp, keep.txt",
        paths=["keep.txt", "temp/old.py", "temp"],
    )
    committed = {p.replace("\\", "/") for p in result["files_committed"]}
    assert "keep.txt" in committed
    assert any(p == "temp/old.py" or p.startswith("temp/") for p in committed)


def test_commit_include_unlisted_rest(tmp_path: Path):
    from scripts.utilities.git_pending_changes import commit_pending_changes

    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        r = subprocess.run(
            ["git", *args],
            cwd=str(repo),
            capture_output=True,
            text=True,
            check=False,
        )
        assert r.returncode == 0, r.stderr
        return r

    git("init")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    (repo / "keep.txt").write_text("k\n", encoding="utf-8")
    git("add", "keep.txt")
    git("commit", "-m", "init")
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    (repo / "b.txt").write_text("b\n", encoding="utf-8")
    (repo / "c.txt").write_text("c\n", encoding="utf-8")
    (repo / "d.txt").write_text("d\n", encoding="utf-8")

    # UI showed a,b,c — user unchecked b, checked "+ more" (d not listed).
    result = commit_pending_changes(
        str(repo),
        "unlisted rest",
        paths=["a.txt", "c.txt"],
        include_unlisted=True,
        shown_files=["a.txt", "b.txt", "c.txt"],
    )
    committed = {p.replace("\\", "/") for p in result["files_committed"]}
    assert "a.txt" in committed
    assert "c.txt" in committed
    assert "d.txt" in committed
    assert "b.txt" not in committed

    pending = collect_pending_changes(str(repo))
    assert {f["path"] for f in pending["files"]} == {"b.txt"}


def test_ignore_pending_path(tmp_path: Path):
    from scripts.utilities.git_pending_changes import ignore_pending_path

    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        r = subprocess.run(
            ["git", *args],
            cwd=str(repo),
            capture_output=True,
            text=True,
            check=False,
        )
        assert r.returncode == 0, r.stderr
        return r

    git("init")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    (repo / "keep.txt").write_text("k\n", encoding="utf-8")
    git("add", "keep.txt")
    git("commit", "-m", "init")
    (repo / "noise.log").write_text("x\n", encoding="utf-8")

    result = ignore_pending_path(str(repo), "noise.log")
    assert result["added_rule"] is True
    gi = (repo / ".gitignore").read_text(encoding="utf-8")
    assert "noise.log" in gi

    pending = collect_pending_changes(str(repo))
    paths = {f["path"] for f in pending["files"]}
    assert "noise.log" not in paths
    # .gitignore itself may show as untracked/modified
    assert "noise.log" not in paths


def test_parse_unified_diff_basic():
    diff = """diff --git a/foo.py b/foo.py
index abc..def 100644
--- a/foo.py
+++ b/foo.py
@@ -1,3 +1,4 @@
 line1
-old
+new
+added
 line3
"""
    hunks = parse_unified_diff(diff)
    assert len(hunks) == 1
    assert hunks[0]["old_start"] == 1
    assert hunks[0]["new_start"] == 1
    types = [ln["type"] for ln in hunks[0]["lines"]]
    assert types == ["context", "del", "add", "add", "context"]
    assert hunks[0]["lines"][1]["old_no"] == 2
    assert hunks[0]["lines"][2]["new_no"] == 2


def test_collect_file_pending_diff_modified_and_untracked(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        r = subprocess.run(
            ["git", *args],
            cwd=str(repo),
            capture_output=True,
            text=True,
            check=False,
        )
        assert r.returncode == 0, r.stderr
        return r

    git("init")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    (repo / "a.txt").write_text("one\n", encoding="utf-8")
    git("add", "a.txt")
    git("commit", "-m", "init")
    (repo / "a.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    (repo / "b.txt").write_text("brand\nnew\n", encoding="utf-8")

    modified = collect_file_pending_diff(str(repo), "a.txt", context_lines=3)
    assert modified["status"] == "modified"
    assert modified["hunks"]
    line_types = [ln["type"] for h in modified["hunks"] for ln in h["lines"]]
    assert "add" in line_types
    assert "context" in line_types
    assert modified["sections"]["added"]["total"] == 2
    assert modified["sections"]["removed"]["total"] == 0
    assert all(
        ln["type"] == "add"
        for h in modified["sections"]["added"]["hunks"]
        for ln in h["lines"]
    )

    untracked = collect_file_pending_diff(str(repo), "b.txt")
    assert untracked["status"] == "untracked"
    assert untracked["hunks"]
    assert all(ln["type"] == "add" for ln in untracked["hunks"][0]["lines"])
    assert untracked["sections"]["added"]["total"] == 2
    assert untracked["sections"]["removed"]["total"] == 0


def test_collect_file_pending_diff_sides_truncate_independently(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        r = subprocess.run(
            ["git", *args],
            cwd=str(repo),
            capture_output=True,
            text=True,
            check=False,
        )
        assert r.returncode == 0, r.stderr
        return r

    git("init")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    old = "\n".join(f"old{i}" for i in range(40)) + "\n"
    (repo / "a.txt").write_text(old, encoding="utf-8")
    git("add", "a.txt")
    git("commit", "-m", "init")
    new = "\n".join(f"new{i}" for i in range(80)) + "\n"
    (repo / "a.txt").write_text(new, encoding="utf-8")

    preview = collect_file_pending_diff(str(repo), "a.txt", max_lines=10)
    assert preview["truncated_added"] is True
    assert preview["truncated_removed"] is True
    assert preview["sections"]["added"]["shown"] == 10
    assert preview["sections"]["removed"]["shown"] == 10
    assert preview["sections"]["added"]["total"] == 80
    assert preview["sections"]["removed"]["total"] == 40

    full = collect_file_pending_diff(str(repo), "a.txt", full=True)
    assert full["truncated_added"] is False
    assert full["truncated_removed"] is False
    assert full["sections"]["added"]["shown"] == 80
    assert full["sections"]["removed"]["shown"] == 40


def test_sanitize_git_output_strips_userinfo(monkeypatch: pytest.MonkeyPatch):
    from scripts.utilities.git_pending_changes import sanitize_git_output

    monkeypatch.setenv("GITEA_TOKEN", "secret-token-value")
    text = "fatal: could not read Username for 'http://user:secret-token-value@127.0.0.1:3000'"
    out = sanitize_git_output(text)
    assert "secret-token-value" not in out
    assert "user:" not in out


def test_git_push_target_and_auth_helper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from scripts.utilities.git_pending_changes import (
        git_credential_helper_arg,
        git_push_command,
        git_push_target,
    )

    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        r = subprocess.run(
            ["git", *args],
            cwd=str(repo),
            capture_output=True,
            text=True,
            check=False,
        )
        assert r.returncode == 0, r.stderr
        return r

    git("init")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    git("remote", "add", "origin", "http://127.0.0.1:3000/example/Cuttle.git")
    (repo / "a.txt").write_text("x\n", encoding="utf-8")
    git("add", "a.txt")
    git("commit", "-m", "init")
    git("branch", "-M", "linux")
    target = git_push_target(str(repo))
    assert target["remote"] == "origin"
    assert target["branch"] == "linux"
    assert target["repo"] == "example/Cuttle"
    assert "http://" in (target["url"] or "")

    monkeypatch.setenv("GITEA_TOKEN", "unit-test-token")
    monkeypatch.delenv("GITEA_USERNAME", raising=False)
    cmd, env = git_push_command("origin", "linux")
    assert cmd[:1] == ["git"]
    assert "push" in cmd
    assert env.get("GIT_ASKPASS_PASSWORD") == "unit-test-token"
    helper = git_credential_helper_arg(env)
    assert helper and "git_credential_helper.py" in helper

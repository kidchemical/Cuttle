"""Tests for git graph helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path

from scripts.utilities.git_graph import (
    _parse_decorate,
    collect_commit_detail,
    collect_file_commit_diff,
    collect_git_graph,
)


def test_parse_decorate():
    assert "HEAD" in _parse_decorate(" (HEAD -> master, origin/master)")
    assert "master" in _parse_decorate(" (HEAD -> master, origin/master)")
    assert "v1" in _parse_decorate(" (tag: v1)")
    assert _parse_decorate("") == []


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    r = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True, encoding="utf-8",
        check=False,
    )
    assert r.returncode == 0, r.stderr or r.stdout
    return r


def test_collect_git_graph_linear_and_merge(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Test")

    (repo / "a.txt").write_text("a1\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-m", "init")

    main = _git(repo, "branch", "--show-current").stdout.strip() or "master"

    _git(repo, "checkout", "-b", "feature")
    (repo / "b.txt").write_text("b\n", encoding="utf-8")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-m", "feature work")

    _git(repo, "checkout", main)
    (repo / "a.txt").write_text("a2\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-m", "main update")

    _git(repo, "merge", "feature", "-m", "merge feature")

    graph = collect_git_graph(str(repo), limit=20, all_refs=True)
    assert graph["count"] >= 4
    hashes = {c["hash"] for c in graph["commits"]}
    assert len(hashes) == graph["count"]
    merge = next(c for c in graph["commits"] if c["subject"].startswith("merge"))
    assert len(merge["parents"]) == 2

    page1 = collect_git_graph(str(repo), limit=2, skip=0, all_refs=True)
    assert page1["count"] == 2
    assert page1["has_more"] is True
    page2 = collect_git_graph(str(repo), limit=2, skip=2, all_refs=True)
    assert page2["count"] >= 1
    assert page1["commits"][0]["hash"] != page2["commits"][0]["hash"]
    assert {c["hash"] for c in page1["commits"]}.isdisjoint(
        {c["hash"] for c in page2["commits"]}
    )

    detail = collect_commit_detail(str(repo), merge["hash"])
    assert detail["subject"].startswith("merge")
    assert len(detail["parents"]) == 2
    assert isinstance(detail["files"], list)


def test_collect_file_commit_diff(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Test")

    (repo / "a.txt").write_text("line1\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-m", "init")

    (repo / "a.txt").write_text("line1\nline2\n", encoding="utf-8")
    (repo / "b.txt").write_text("new\n", encoding="utf-8")
    _git(repo, "add", "a.txt", "b.txt")
    _git(repo, "commit", "-m", "update")

    tip = _git(repo, "rev-parse", "HEAD").stdout.strip()
    detail = collect_commit_detail(str(repo), tip)
    paths = {f["path"] for f in detail["files"]}
    assert "a.txt" in paths and "b.txt" in paths

    diff_a = collect_file_commit_diff(str(repo), tip, "a.txt")
    assert diff_a["path"] == "a.txt"
    assert diff_a["status"] == "modified"
    assert diff_a["additions"] >= 1
    assert any(
        ln.get("type") == "add" and "line2" in str(ln.get("text") or "")
        for h in diff_a["hunks"]
        for ln in (h.get("lines") or [])
    )

    diff_b = collect_file_commit_diff(str(repo), tip, "b.txt")
    assert diff_b["status"] == "added"
    assert diff_b["additions"] >= 1

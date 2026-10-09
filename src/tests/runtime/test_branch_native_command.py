"""``/branch`` must be a native Cuttle control command, never an agent turn.

Covers palette registration, parsing, dispatch order, name reservation, and
the read/switch behavior against a throwaway git repository. No model/API calls
and no touches to live Flask/daemon state.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SLASH_JS = REPO_ROOT / "src" / "web" / "js" / "chat" / "chat_slash.js"
CHAT_JS = REPO_ROOT / "src" / "web" / "js" / "chat" / "chat_page.js"
WEB_API = REPO_ROOT / "src" / "api" / "web_chat_api.py"

requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True
    )


def _current(cwd: Path) -> str:
    return _git(cwd, "branch", "--show-current").stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    d = tmp_path / "proj"
    d.mkdir()
    _git(d, "init", "-q")
    _git(d, "config", "user.email", "t@example.com")
    _git(d, "config", "user.name", "Tester")
    (d / "a.txt").write_text("a", encoding="utf-8")
    _git(d, "add", ".")
    _git(d, "commit", "-q", "-m", "init")
    _git(d, "branch", "feature-x")
    return d


# ── palette registration ─────────────────────────────────────────────────────

def test_branch_is_registered_in_the_shared_slash_command_list():
    slash_src = SLASH_JS.read_text(encoding="utf-8")
    block = slash_src.split("const SLASH_COMMANDS = [", 1)[1].split("\n];", 1)[0]
    assert "prefix: '/branch '" in block
    assert "controlCommand: true" in block
    assert "Switch branch" in block


def test_branch_name_is_reserved_for_project_commands():
    from api.project_commands import RESERVED_SLASH_NAMES

    assert "branch" in RESERVED_SLASH_NAMES


# ── parsing ──────────────────────────────────────────────────────────────────

def test_parse_branch_slash():
    from api.branch_slash import parse_branch_slash

    assert parse_branch_slash("/branch")[0] == "status"
    assert parse_branch_slash("/branch status")[0] == "status"
    assert parse_branch_slash("/branch list")[0] == "list"
    assert parse_branch_slash("/branch ls")[0] == "list"
    mode, opts = parse_branch_slash("/branch feature-x")
    assert mode == "switch"
    assert opts["branch"] == "feature-x"


def test_sticky_agent_prefix_does_not_hide_the_branch_command():
    from api.branch_slash import parse_branch_slash

    for msg in ("/cursor /branch status", "/codex /branch feature-x"):
        assert parse_branch_slash(msg) is not None


def test_branch_lookalikes_are_not_intercepted():
    from api.branch_slash import parse_branch_slash

    assert parse_branch_slash("/branching the build") is None
    assert parse_branch_slash("how do I /branch") is None
    assert parse_branch_slash("/brand new") is None
    assert parse_branch_slash("hello") is None


# ── behavior ─────────────────────────────────────────────────────────────────

@requires_git
def test_status_reports_current_branch(repo: Path):
    from api.branch_slash import handle_branch_slash

    out = handle_branch_slash("/branch", project_path=str(repo))
    assert out["success"] is True
    assert out["native_command"] == "/branch"
    assert out["branch"] == _current(repo)


@requires_git
def test_list_marks_current_branch(repo: Path):
    from api.branch_slash import handle_branch_slash

    out = handle_branch_slash("/branch list", project_path=str(repo))
    assert out["success"] is True
    assert "feature-x" in out["response"]
    assert _current(repo) in out["response"]


@requires_git
def test_switch_moves_the_working_branch(repo: Path):
    from api.branch_slash import handle_branch_slash

    out = handle_branch_slash("/branch feature-x", project_path=str(repo))
    assert out["success"] is True
    assert out["branch"] == "feature-x"
    assert _current(repo) == "feature-x"


@requires_git
def test_switch_refuses_a_dirty_worktree(repo: Path):
    from api.branch_slash import handle_branch_slash

    (repo / "a.txt").write_text("dirty", encoding="utf-8")
    out = handle_branch_slash("/branch feature-x", project_path=str(repo))
    assert out["success"] is False
    assert "pending changes" in out["response"].lower()
    assert _current(repo) != "feature-x"


def test_status_outside_a_repo_is_reported_not_raised(tmp_path: Path):
    from api.branch_slash import handle_branch_slash

    out = handle_branch_slash("/branch", project_path=str(tmp_path))
    assert out["success"] is False
    assert "git repository" in out["response"].lower()


# ── dispatch order ───────────────────────────────────────────────────────────

def test_branch_is_dispatched_before_router_and_agent_branches():
    src = WEB_API.read_text(encoding="utf-8")
    branch_at = src.index("from api.branch_slash import")
    router_at = src.index("from api.agent_router.commands import (")
    assert branch_at < router_at, "/branch must be handled before the router"


# ── segmented project chip ───────────────────────────────────────────────────

def test_project_chip_is_segmented_with_branch():
    src = CHAT_JS.read_text(encoding="utf-8")
    chip = src.split("function projectChipHtml", 1)[1].split("\n    }", 1)[0]
    assert "slash-chip-seg--project" in chip
    assert "slash-chip-seg--branch" in chip
    assert "currentProjectBranch()" in chip
    assert "currentProjectBranch" in src
    assert "/api/git/repos?" in src

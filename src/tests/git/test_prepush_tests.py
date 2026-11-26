"""Pre-push test gate (core.prepush_tests): selection, release stamps, tag gate.

Runs against scratch git repos shaped like a Cuttle checkout (pytest.ini +
src/tests/quality) so nothing here runs the real suite.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from core import prepush_tests as pt
from core.git_push_diagnostics import push_failure

REPO_ROOT = Path(__file__).resolve().parents[3]
HOOK = REPO_ROOT / ".cuttle" / "scripts" / "git-hooks" / "pre-push"
ZERO = "0" * 40


def _git(cwd, *args):
    proc = subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@t",
                           "-c", "init.defaultBranch=main", "-c", "commit.gpgsign=false", *args],
                          cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "cuttle"
    root.mkdir()
    _git(root, "init")
    _write(root, "pytest.ini", "[pytest]\n")
    _write(root, "src/tests/quality/test_ceiling.py", "def test_ok():\n    pass\n")
    _write(root, "src/api/widget_board.py", "X = 1\n")
    _write(root, "src/tests/widgets/test_widget_board.py", "from api import widget_board\n")
    _write(root, "src/tests/other/test_unrelated.py", "def test_x():\n    pass\n")
    _write(root, "src/tests/e2e/test_widget_board_ui.py", "# widget_board\n")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "seed")
    return root


def test_fast_selection_takes_quality_changed_and_related_tests_not_e2e(repo):
    base = _git(repo, "rev-parse", "HEAD")
    _write(repo, "src/api/widget_board.py", "X = 2\n")
    _write(repo, "src/tests/other/test_unrelated.py", "def test_x():\n    assert True\n")
    _git(repo, "commit", "-am", "change")
    head = _git(repo, "rev-parse", "HEAD")

    files = pt.changed_files(base, head, repo)
    paths, note = pt.select_fast_tests(files, head, repo)

    assert paths[0] == pt.QUALITY_DIR
    assert set(paths[1:]) == {"src/tests/widgets/test_widget_board.py", "src/tests/other/test_unrelated.py"}
    assert note == ""


def test_fast_selection_over_budget_keeps_only_changed_tests(repo, monkeypatch):
    base = _git(repo, "rev-parse", "HEAD")
    _write(repo, "src/api/widget_board.py", "X = 3\n")
    _write(repo, "src/tests/other/test_unrelated.py", "def test_x():\n    assert 1\n")
    _git(repo, "commit", "-am", "change")
    head = _git(repo, "rev-parse", "HEAD")
    monkeypatch.setattr(pt, "FAST_MAX_FILES", 1)

    paths, note = pt.select_fast_tests(pt.changed_files(base, head, repo), head, repo)

    assert paths == [pt.QUALITY_DIR, "src/tests/other/test_unrelated.py"]
    assert "release-check" in note


def test_release_stamp_round_trip_lives_in_the_git_dir(repo):
    sha = pt.commit_of("HEAD", repo)
    assert pt.read_stamp(sha, repo) is None
    path = pt.write_stamp(sha, "10 passed", repo)
    assert ".git" in path.parts
    assert pt.read_stamp(sha, repo)["summary"] == "10 passed"
    assert _git(repo, "status", "--porcelain") == ""


def test_release_tag_needs_a_stamp_for_its_commit(repo):
    sha = pt.commit_of("HEAD", repo)
    refs = [("refs/tags/v1.2.3", sha, ZERO, sha, [])]

    hits = pt.gate(refs, repo)
    assert [h["hook"] for h in hits] == ["tests"]
    assert "release-check" in hits[0]["hint"]

    pt.write_stamp(sha, "ok", repo)
    assert pt.gate(refs, repo) == []


def test_annotated_release_tag_is_peeled_to_its_commit(repo):
    _git(repo, "tag", "-a", "v1.2.3", "-m", "Release")
    tag_obj = _git(repo, "rev-parse", "v1.2.3")
    pt.write_stamp(pt.commit_of("HEAD", repo), "ok", repo)
    assert pt.gate([("refs/tags/v1.2.3", tag_obj, ZERO, None, [])], repo) == []


def test_branch_push_blocks_on_failed_tests_and_warns_on_timeout(repo, monkeypatch, capsys):
    sha = pt.commit_of("HEAD", repo)
    refs = [("refs/heads/dev", sha, ZERO, None, [sha])]
    monkeypatch.setattr(pt, "fast_check", lambda base, head, repo: {
        "status": "failed", "failed": ["src/tests/x/test_a.py::test_one"], "summary": "1 failed, 9 passed"})
    hits = pt.gate(refs, repo)
    assert [(h["hook"], h["file"]) for h in hits] == [("tests", "src/tests/x/test_a.py::test_one")]

    monkeypatch.setattr(pt, "fast_check", lambda base, head, repo: {"status": "timeout", "summary": "slow"})
    assert pt.gate(refs, repo) == []
    assert "tests timeout" in capsys.readouterr().err


def test_gate_skips_when_disabled_or_not_a_cuttle_checkout(repo, tmp_path, monkeypatch):
    sha = pt.commit_of("HEAD", repo)
    refs = [("refs/tags/v1.2.3", sha, ZERO, None, [])]
    monkeypatch.setenv("CUTTLE_PREPUSH_TESTS", "0")
    assert pt.gate(refs, repo) == []
    monkeypatch.delenv("CUTTLE_PREPUSH_TESTS")
    (repo / "pytest.ini").unlink()
    assert pt.gate(refs, repo) == []


def test_push_report_names_the_test_gate():
    from core.git_push_diagnostics import REPORT_PREFIX
    report = '{"version": 1, "findings": [{"hook": "tests", "rule": "1 failed"}]}'
    out = push_failure(REPORT_PREFIX + report, "")
    assert out["error"].startswith("Pre-push tests blocked the push: 1 failed")


def test_run_pytest_turns_default_encoding_into_a_failure(tmp_path):
    _write(tmp_path, "pytest.ini", "[pytest]\n")
    _write(tmp_path, "test_enc.py",
           "import subprocess, sys\n"
           "def test_default_encoding():\n"
           "    subprocess.run([sys.executable, '-c', 'print(1)'], capture_output=True, text=True)\n")
    log = tmp_path / "logs" / "run.log"
    status, failed, _ = pt.run_pytest(["test_enc.py"], tmp_path, sys.executable, 120, log=log)
    assert status == "failed"
    assert failed == ["test_enc.py::test_default_encoding"]
    assert "EncodingWarning" in log.read_text(encoding="utf-8")


def test_hook_runs_the_gate_in_the_pushed_repo(repo):
    """A vX.Y.Z tag without a release-check pass is refused by the real hook."""
    sha = pt.commit_of("HEAD", repo)
    env = dict(os.environ, CUTTLE_PREPUSH_NO_GITLEAKS="1")
    proc = subprocess.run([sys.executable, str(HOOK)], input=f"refs/tags/v9.9.9 {sha} refs/tags/v9.9.9 {ZERO}\n",
                          cwd=repo, capture_output=True, text=True, encoding="utf-8", timeout=120, env=env)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "tests" in proc.stdout.splitlines()[0]

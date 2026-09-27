"""Workspace branch prepare / park behavior for @cuttle solve."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from api.cuttle_jobs import workspace as ws


def test_prepare_new_branch_uses_origin_base_not_local():
    workdir = Path("/tmp/workspaces/demo-game/source")
    runs: list[tuple] = []

    def fake_run(wd, *args, check=True, timeout=600):
        runs.append(args)
        proc = MagicMock()
        proc.returncode = 0
        proc.stdout = ""
        proc.stderr = ""
        if args[:2] == ("rev-parse", "--verify") and args[2] == "origin/dev/core":
            proc.returncode = 0
        if args[:3] == ("rev-parse", "--abbrev-ref", "HEAD"):
            proc.stdout = "cuttle/issue-43\n"
        return proc

    with (
        patch.object(ws, "ensure_clean"),
        patch.object(ws, "fetch_origin"),
        patch.object(ws, "remote_branch_exists", return_value=False),
        patch.object(ws, "local_branch_exists", return_value=False),
        patch.object(ws, "run_git", side_effect=fake_run),
    ):
        branch, created = ws.prepare_issue_branch(
            workdir, issue_number=43, base_branch="dev/core"
        )

    assert branch == "cuttle/issue-43"
    assert created is True
    assert ("checkout", "-B", "cuttle/issue-43", "origin/dev/core") in runs


def test_prepare_followup_syncs_from_origin_issue_branch():
    workdir = Path("/tmp/workspaces/demo-game/source")
    runs: list[tuple] = []

    def fake_run(wd, *args, check=True, timeout=600):
        runs.append(args)
        proc = MagicMock()
        proc.returncode = 0
        proc.stdout = ""
        if args[:3] == ("rev-parse", "--abbrev-ref", "HEAD"):
            proc.stdout = "cuttle/issue-42\n"
        return proc

    with (
        patch.object(ws, "ensure_clean"),
        patch.object(ws, "fetch_origin"),
        patch.object(ws, "remote_branch_exists", return_value=True),
        patch.object(ws, "local_branch_exists", return_value=True),
        patch.object(ws, "run_git", side_effect=fake_run),
    ):
        branch, created = ws.prepare_issue_branch(
            workdir, issue_number=42, base_branch="dev/core"
        )

    assert branch == "cuttle/issue-42"
    assert created is False
    assert ("checkout", "-B", "cuttle/issue-42", "origin/cuttle/issue-42") in runs
    # Must not recreate from base on follow-up
    assert ("checkout", "-B", "cuttle/issue-42", "origin/dev/core") not in runs


def test_park_does_not_delete_remote_branches():
    workdir = Path("/tmp/workspaces/demo-game/source")
    runs: list[tuple] = []

    def fake_run(wd, *args, check=True, timeout=600):
        runs.append(args)
        proc = MagicMock()
        proc.returncode = 0
        proc.stdout = ""
        return proc

    with (
        patch.object(ws, "reset_hard_clean") as reset,
        patch.object(ws, "fetch_origin"),
        patch.object(ws, "run_git", side_effect=fake_run),
    ):
        ws.park_workspace(workdir, base_branch="dev/core")

    reset.assert_called_once_with(workdir)
    assert ("checkout", "--detach", "origin/dev/core") in runs
    assert not any(a[:2] == ("push", "--delete") for a in runs)
    assert not any("branch" in a and "-D" in a for a in runs)


def test_commit_all_uses_cuttle_identity_env():
    workdir = Path("/tmp/workspaces/demo-game/source")
    commit_kwargs = {}

    def fake_run(wd, *args, check=True, timeout=600, env=None):
        proc = MagicMock()
        proc.returncode = 0
        if args[:1] == ("diff",) and "--cached" in args:
            proc.stdout = "Assets/Foo.cs\n"
        elif args[:1] == ("rev-parse",):
            proc.stdout = "abc123\n"
        else:
            proc.stdout = ""
        if args[:1] == ("commit",):
            commit_kwargs["env"] = env
            commit_kwargs["args"] = args
        return proc

    with (
        patch.object(ws, "run_git", side_effect=fake_run),
        patch.object(
            ws,
            "cuttle_commit_env",
            return_value={
                "GIT_AUTHOR_NAME": "Cuttle",
                "GIT_AUTHOR_EMAIL": "cuttle@localhost",
                "GIT_COMMITTER_NAME": "Cuttle",
                "GIT_COMMITTER_EMAIL": "cuttle@localhost",
            },
        ),
    ):
        sha = ws.commit_all(workdir, "Lower crystal SFX volume\n\nFixes #103")

    assert sha == "abc123"
    assert commit_kwargs["env"]["GIT_AUTHOR_NAME"] == "Cuttle"
    assert commit_kwargs["env"]["GIT_AUTHOR_EMAIL"] == "cuttle@localhost"
    assert commit_kwargs["args"][:2] == ("commit", "-m")


def test_suggest_job_commit_message_uses_suggester_and_issue_trailer():
    workdir = Path("/tmp/workspaces/demo-game/source")

    with (
        patch(
            "scripts.utilities.git_pending_changes.collect_commit_suggest_context",
            return_value={"files": [], "totals": {"files": 1}, "file_summary": "x"},
        ),
        patch(
            "api.commit_message_suggester.suggest_commit_message",
            return_value={"message": "Lower crystal SFX to 70%", "source": "openai"},
        ),
    ):
        msg = ws.suggest_job_commit_message(
            workdir,
            issue_number=103,
            issue_title="Turn down the crystal sfx (70%)",
            triggering_user="alice",
        )

    assert msg.startswith("Lower crystal SFX to 70%")
    assert "Fixes #103" in msg
    assert "Triggered by @alice" in msg


def test_suggest_job_commit_message_falls_back_when_suggest_fails():
    workdir = Path("/tmp/workspaces/demo-game/source")

    with patch(
        "scripts.utilities.git_pending_changes.collect_commit_suggest_context",
        side_effect=RuntimeError("boom"),
    ):
        msg = ws.suggest_job_commit_message(
            workdir,
            issue_number=103,
            issue_title="Turn down the crystal sfx (70%)",
            triggering_user="alice",
        )

    assert "fix(#103): Turn down the crystal sfx (70%)" in msg
    assert "Fixes #103" in msg

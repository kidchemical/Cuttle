"""Unit tests for Cuttle Jobs command/branch helpers."""

from __future__ import annotations

from api.cuttle_jobs.commands import (
    cuttle_issue_branch,
    parse_cuttle_command,
    with_bot_marker,
)
from api.cuttle_jobs.formatting import (
    format_ack_comment,
    format_converse_comment,
    format_investigate_comment,
    format_solve_comment,
    sanitize_gitea_markdown,
)


def test_parse_investigate():
    p = parse_cuttle_command("@cuttle investigate Survival Mode")
    assert p is not None
    assert p.command == "investigate"
    assert p.instructions == "Survival Mode"


def test_parse_solve_multiline():
    p = parse_cuttle_command("thanks\n@cuttle solve still breaks when crouching\n")
    assert p is not None
    assert p.command == "solve"
    assert "crouching" in p.instructions


def test_parse_free_text_is_converse():
    p = parse_cuttle_command("@cuttle stage")
    assert p is not None
    assert p.command == "converse"
    assert p.instructions == "stage"
    assert parse_cuttle_command("no mention") is None


def test_branch_naming():
    assert cuttle_issue_branch(42) == "cuttle/issue-42"
    assert cuttle_issue_branch(1) == "cuttle/issue-1"


def test_bot_marker():
    body = with_bot_marker("hello")
    assert "<!-- cuttle-bot -->" in body
    assert with_bot_marker(body).count("<!-- cuttle-bot -->") == 1


def test_sanitize_strips_think_and_forms():
    raw = (
        "<think>\nplanning…\n</think>\n\n"
        "## ✅ Fix ready\n\nDone.\n\n"
        "<cuttle_action_form>\n{\"title\": \"Post Discord\"}\n</cuttle_action_form>\n"
    )
    out = sanitize_gitea_markdown(raw)
    assert "<think>" not in out
    assert "cuttle_action_form" not in out
    assert "Fix ready" in out
    assert "Done." in out


def test_ack_comment_stylized():
    ack = format_ack_comment("solve", "alice", 6)
    assert "🛠️" in ack
    assert "**solve**" in ack
    assert "@alice" in ack
    assert "job `6`" in ack
    assert "Received" not in ack
    assert "Working on it" not in ack


def test_investigate_comment_compact_wrapper():
    body = format_investigate_comment("Root cause: loud SFX.", issue_number=103)
    assert body.startswith("## 🔍 Investigation · #103")
    assert "Root cause: loud SFX." in body
    assert "@cuttle yes" in body
    assert "<!-- cuttle-offer:implement -->" in body


def test_converse_offer_gets_marker():
    offered = format_converse_comment(
        "Looks clear.\n\nWould you like me to implement the change/fix?"
    )
    assert "<!-- cuttle-offer:implement -->" in offered
    plain = format_converse_comment("Need a screenshot of the HUD first.")
    assert "<!-- cuttle-offer:implement -->" not in plain


def test_solve_comment_strips_form_and_footer():
    agent = (
        "<think>work</think>\n"
        "## ✅ Fix ready · #103\n\nVolumes cut ×0.7.\n\n"
        "<cuttle_action_form>\n{}\n</cuttle_action_form>"
    )
    body = format_solve_comment(
        agent,
        issue_number=103,
        changes=["a.cs", "b.prefab"],
        pr_url="http://gitea/pr/1",
        commit_url="http://gitea/commit/abc",
    )
    assert "cuttle_action_form" not in body
    assert "<think>" not in body
    assert "Volumes cut" in body
    assert "`a.cs`" in body
    assert "[PR](http://gitea/pr/1)" in body
    assert "Needs playtest" in body


def test_status_store_history_ring(tmp_path, monkeypatch):
    from api.cuttle_jobs import status_store as ss

    hist = tmp_path / "cuttle_jobs_history.jsonl"
    running = tmp_path / "cuttle_jobs_running.json"
    monkeypatch.setattr(ss, "_HISTORY_PATH", hist)
    monkeypatch.setattr(ss, "_STATUS_PATH", running)

    job = {
        "id": 42,
        "command": "solve",
        "repository": "acme/demo-game",
        "issue_number": 103,
        "issue_title": "Loud SFX",
        "triggering_user": "alice",
    }
    sid = ss.mark_running(job)
    assert sid == "cuttle-job-42"
    assert len(ss.list_running()) == 1

    ss.append_history(
        {
            "job_id": 42,
            "command": "solve",
            "repository": "acme/demo-game",
            "issue_number": 103,
            "status": "completed",
            "duration_sec": 12,
            "result": {"ok": True, "pr_url": "http://gitea/pr/1"},
            "gitea_url": "http://gitea/issue/103",
        }
    )
    ss.mark_done(42)
    assert ss.list_running() == []
    hist_rows = ss.list_history(limit=10)
    assert len(hist_rows) == 1
    assert hist_rows[0]["job_id"] == 42
    assert hist_rows[0]["status"] == "completed"
    assert hist_rows[0]["result"]["pr_url"] == "http://gitea/pr/1"


def test_enrich_cuttle_job_row_duration():
    from api.web_chat_api import _enrich_cuttle_job_row

    row = _enrich_cuttle_job_row(
        {
            "id": 7,
            "command": "investigate",
            "repository": "acme/demo-game",
            "issue_number": 9,
            "status": "completed",
            "claimed_at": "2026-09-13T12:00:00Z",
            "completed_at": "2026-09-13T12:01:30Z",
            "result": {"ok": True, "pr_url": "http://x/pr/2"},
        }
    )
    assert row["job_id"] == 7
    assert row["duration_sec"] == 90
    assert row["pr_url"] == "http://x/pr/2"
    assert "demo-game" in row["gitea_url"]

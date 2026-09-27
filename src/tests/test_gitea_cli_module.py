"""Tests for ``python -m api.gitea``."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from unittest.mock import patch

import pytest

from api.gitea.cli import main


def _run(argv: list[str]) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = main(argv)
    return code, buf.getvalue()


def test_list_json():
    issues = [{"number": 1, "title": "Bug", "labels": [{"name": "bug"}]}]
    with patch("api.gitea_client.list_issues", return_value=issues):
        with patch("api.gitea_client.issue_web_url", return_value="http://x/1"):
            code, out = _run(["list", "o/r", "--json"])
    assert code == 0
    payload = json.loads(out)
    assert payload["count"] == 1
    assert payload["issues"][0]["title"] == "Bug"


def test_show_json():
    issue = {"number": 12, "title": "Fix", "state": "open", "body": "details", "labels": []}
    with patch("api.gitea_client.get_issue", return_value=issue):
        with patch("api.gitea_client.issue_web_url", return_value="http://x/12"):
            code, out = _run(["show", "o/r", "12", "--json"])
    assert code == 0
    assert json.loads(out)["issue"]["number"] == 12


def test_comments_json():
    comments = [{"user": {"login": "cuttle"}, "body": "hi", "created_at": "t"}]
    with patch("api.gitea_client.list_issue_comments", return_value=comments):
        code, out = _run(["comments", "o/r", "12", "--json"])
    assert code == 0
    payload = json.loads(out)
    assert payload["count"] == 1
    assert payload["comments"][0]["body"] == "hi"


def test_update_comment_json():
    with patch("api.gitea_client.add_issue_comment") as comment:
        with patch("api.gitea_client.issue_web_url", return_value="http://x/12"):
            code, out = _run(
                ["update", "o/r", "12", "--comment", "working", "--json"]
            )
    assert code == 0
    comment.assert_called_once()
    payload = json.loads(out)
    assert payload["ok"] is True
    assert "comment posted" in payload["steps"]


def test_bad_repo():
    code, _out = _run(["list", "not-a-repo", "--json"])
    assert code == 1


def test_help():
    with pytest.raises(SystemExit) as ei:
        main(["--help"])
    assert ei.value.code == 0

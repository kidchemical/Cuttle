"""Tests for ``python -m api.panes_cli``."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from unittest.mock import patch

import pytest

from api.panes_cli.cli import main


def _run(argv: list[str]) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = main(argv)
    return code, buf.getvalue()


def test_list_json():
    body = {
        "success": True,
        "orientation": "horizontal",
        "panes": [
            {"pane": 1, "kind": "chat", "session_id": "430", "title": "A"},
            {"pane": 2, "kind": "chat", "session_id": "465", "title": "B"},
        ],
    }
    with patch("api.panes_cli.cli._get_json", return_value=(200, body, "")):
        code, out = _run(["list", "--json"])
    assert code == 0
    payload = json.loads(out)
    assert payload["count"] == 2
    assert payload["panes"][0]["session_id"] == "430"


def test_messages_json():
    body = {
        "success": True,
        "pane": {"pane": 1, "session_id": "430", "title": "A"},
        "messages": [{"role": "user", "content": "hi"}],
        "count": 1,
    }
    with patch("api.panes_cli.cli._get_json", return_value=(200, body, "")):
        code, out = _run(["messages", "1", "--limit", "10", "--json"])
    assert code == 0
    payload = json.loads(out)
    assert payload["messages"][0]["content"] == "hi"


def test_messages_not_found():
    body = {"success": False, "error": "Pane 9 is not open (2 pane(s) currently)", "panes": []}
    with patch("api.panes_cli.cli._get_json", return_value=(404, body, "Pane 9")):
        code, out = _run(["messages", "9", "--json"])
    assert code == 2


def test_help():
    with pytest.raises(SystemExit) as ei:
        main(["--help"])
    assert ei.value.code == 0


def test_list_unreachable():
    with patch("api.panes_cli.cli._get_json", return_value=(0, None, "connection refused")):
        code, out = _run(["list", "--json"])
    assert code == 3
    assert json.loads(out)["ok"] is False

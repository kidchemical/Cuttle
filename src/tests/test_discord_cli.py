"""Tests for ``python -m api.discord_cli``."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pytest

from api.discord_cli.cli import main


def _project_with_discord(tmp_path: Path) -> Path:
    root = tmp_path / "Game"
    actions = root / ".cuttle" / "actions"
    actions.mkdir(parents=True)
    (actions / "discord-post.yaml").write_text(
        "\n".join(
            [
                "name: discord.post",
                "type: discord.post",
                'guild_id: "111"',
                "channels:",
                '  feature-updates: "222"',
                '  lab: "333"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    return root


def _run(argv: list[str]) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = main(argv)
    return code, buf.getvalue()


def test_aliases(tmp_path: Path):
    root = _project_with_discord(tmp_path)
    code, out = _run(["aliases", "--project", str(root), "--json"])
    assert code == 0
    payload = json.loads(out)
    assert payload["guild_id"] == "111"
    assert payload["channels"]["feature-updates"] == "222"


def test_channels_mocked(tmp_path: Path):
    root = _project_with_discord(tmp_path)
    fake = [
        {"id": "222", "name": "feature-updates", "type": 0, "position": 1},
        {"id": "999", "name": "other", "type": 0, "position": 2},
    ]
    with patch("api.discord_cli.cli._discord_request", return_value=(200, fake, "")):
        code, out = _run(["channels", "--project", str(root), "--json"])
    assert code == 0
    payload = json.loads(out)
    assert payload["count"] == 2
    assert payload["channels"][0]["name"] == "feature-updates"


def test_messages_resolves_alias(tmp_path: Path):
    root = _project_with_discord(tmp_path)
    fake = [
        {
            "id": "m1",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "content": "hello from discord",
            "author": {"id": "u1", "username": "bob"},
        }
    ]
    with patch("api.discord_cli.cli._discord_request", return_value=(200, fake, "")) as req:
        code, out = _run(
            [
                "messages",
                "feature-updates",
                "--project",
                str(root),
                "--limit",
                "5",
                "--json",
            ]
        )
    assert code == 0
    payload = json.loads(out)
    assert payload["channel_id"] == "222"
    assert payload["alias"] == "feature-updates"
    assert payload["messages"][0]["content"] == "hello from discord"
    path = req.call_args[0][1]
    assert path == "/channels/222/messages"


def test_messages_unknown_alias(tmp_path: Path):
    root = _project_with_discord(tmp_path)
    code, out = _run(
        ["messages", "nope", "--project", str(root), "--json"]
    )
    assert code == 2
    payload = json.loads(out)
    assert payload["ok"] is False


def test_messages_snowflake_without_project():
    fake = [
        {
            "id": "m1",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "content": "snowflake path",
            "author": {"id": "u1", "username": "bob"},
        }
    ]
    with patch("api.discord_cli.cli._discord_request", return_value=(200, fake, "")) as req:
        code, out = _run(["messages", "999888777666", "--limit", "3", "--json"])
    assert code == 0
    payload = json.loads(out)
    assert payload["channel_id"] == "999888777666"
    assert payload["alias"] is None
    assert req.call_args[0][1] == "/channels/999888777666/messages"


def test_help():
    with pytest.raises(SystemExit) as ei:
        main(["--help"])
    assert ei.value.code == 0

"""Service-op protocol + registry: discovery, aliases, gating, dispatch."""

from __future__ import annotations

from pathlib import Path

import pytest
from unittest.mock import patch

from api.service_ops import catalog

DROP_IN = '''
class SlackOp:
    id = "slack"
    label = "Slack"
    action_types = ("slack.post",)
    description = "drop-in test op"
    def available(self, action, project_path=None):
        return True
    def execute(self, action, params, *, session_id=None):
        return {"success": True, "response": "slacked", "echo": params}

def build_op():
    return SlackOp()
'''


def _reset():
    catalog.reset_cache()


def test_bundled_discord_registered_with_aliases():
    _reset()
    try:
        op = catalog.find_op("discord.post")
        assert op is not None and op.id == "discord"
        for alias in ("discord.post", "discord_post", "discord", "DISCORD.POST"):
            assert catalog.find_op(alias) is op
    finally:
        _reset()


def test_builtin_shell_is_not_a_service_op():
    _reset()
    try:
        assert catalog.find_op("shell") is None
        assert catalog.find_op("run") is None
        assert catalog.find_op("") is None
    finally:
        _reset()


def test_drop_in_op_is_discovered_and_dispatchable(tmp_path, monkeypatch):
    ext = tmp_path / "service_ops"
    ext.mkdir()
    (ext / "slack.py").write_text(DROP_IN)
    monkeypatch.setenv("CUTTLE_SERVICE_OPS_DIR", str(ext))
    _reset()
    try:
        op = catalog.find_op("slack.post")
        assert op is not None and op.id == "slack"
        out = op.execute({}, {"text": "hi"})
        assert out["success"] and out["echo"] == {"text": "hi"}
        ids = {row["id"] for row in catalog.ops_status()}
        assert {"discord", "slack"} <= ids
    finally:
        _reset()
        monkeypatch.delenv("CUTTLE_SERVICE_OPS_DIR", raising=False)


def test_invalid_drop_in_is_skipped(tmp_path, monkeypatch):
    ext = tmp_path / "service_ops"
    ext.mkdir()
    (ext / "broken.py").write_text("def build_op():\n    return object()\n")
    monkeypatch.setenv("CUTTLE_SERVICE_OPS_DIR", str(ext))
    _reset()
    try:
        assert catalog.find_op("anything") is None
        assert catalog.find_op("discord.post").id == "discord"  # bundled still works
    finally:
        _reset()
        monkeypatch.delenv("CUTTLE_SERVICE_OPS_DIR", raising=False)


def test_discord_op_availability_gate():
    op = catalog.find_op("discord.post")
    action = {"type": "discord.post", "project_path": "/p"}

    with patch("api.experimental.context_bundles.executable_available", return_value=False):
        assert op.available(action) is False
        assert op.execute(action, {"content": "x"})["success"] is False

    with patch("api.experimental.context_bundles.executable_available", return_value=True):
        with patch("api.discord_ops.post.execute_discord_post",
                   return_value={"success": True, "response": "ok"}) as post:
            assert op.execute(action, {"content": "x"})["success"] is True
            post.assert_called_once()


def test_project_action_dispatch_uses_registry(tmp_path, monkeypatch):
    from api.project_actions import clear_pending_for_tests, execute_inline_action
    from api.project_actions import encode_inline_action_payload

    action_dir = tmp_path / ".cuttle" / "actions"
    action_dir.mkdir(parents=True)
    (action_dir / "discord-post.yaml").write_text(
        "name: discord.post\ntype: discord.post\nguild_id: '1'\n"
        "channels:\n  general: '123'\n")
    clear_pending_for_tests()
    token = encode_inline_action_payload(
        action_name="discord.post", project_path=str(tmp_path),
        params={"content": "hi", "channel": "general"})

    op = catalog.find_op("discord.post")
    with patch.object(op, "execute", return_value={"success": True, "response": "posted"}) as ex:
        res = execute_inline_action(token, session_id="s1")
    assert res["success"] is True
    ex.assert_called_once()

"""Recovery changes only requested settings and rejects sandboxed execution."""
import importlib.util
from pathlib import Path
import sys

import pytest

from api.agent_harness.cli_paths import CLI_PATH_ENV
from managers.settings_manager import SettingsManager

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux host recovery")


@pytest.fixture
def recovery():
    path = Path(__file__).resolve().parents[3] / ".cuttle/scripts/restore-native-agents.py"
    spec = importlib.util.spec_from_file_location("native_agent_recovery", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sandboxed_recovery_cannot_change_settings(recovery, monkeypatch):
    monkeypatch.setenv("CUTTLE_BWRAP_ACTIVE", "1")
    monkeypatch.setattr(recovery, "save_cli_paths", lambda _: pytest.fail("settings changed"))
    with pytest.raises(RuntimeError, match="outside the bwrap agent"):
        recovery.apply()


def test_environment_override_requires_resolution_before_settings_change(recovery, monkeypatch):
    monkeypatch.delenv("CUTTLE_BWRAP_ACTIVE", raising=False)
    monkeypatch.setenv("CODEX_CLI_PATH", "/old/wrapper")
    monkeypatch.setattr(recovery, "save_cli_paths", lambda _: pytest.fail("settings changed"))
    with pytest.raises(RuntimeError, match="CODEX_CLI_PATH"):
        recovery.apply()


def test_recovery_clears_all_saved_paths_preserves_other_settings_and_verifies(recovery, monkeypatch, tmp_path):
    from api.agent_harness import cli_paths
    from scripts.utilities import claude_cli_tool, codex_cli_tool, cursor_cli_tool, muse_cli_tool
    from api.agent_harness.agents.opencode import adapter

    monkeypatch.delenv("CUTTLE_BWRAP_ACTIVE", raising=False)
    for env in CLI_PATH_ENV.values():
        monkeypatch.delenv(env, raising=False)
    manager = SettingsManager(str(tmp_path / "settings.json"))
    assert manager.set_setting("agent_cli_paths", {agent: "/old/wrapper" for agent in CLI_PATH_ENV})
    assert manager.set_setting("starred_slash_commands", ["/codex"])
    monkeypatch.setattr(cli_paths, "get_settings_manager", lambda: manager)
    monkeypatch.setattr(recovery, "get_settings_manager", lambda: manager)
    seen = []
    def verify():
        assert manager.get_setting("agent_cli_paths") == {}
        assert manager.get_setting("agent_resource_limits")["enabled"] is True
        seen.append("verified")
        return {"descendant_inherits": True}
    monkeypatch.setattr(recovery, "verify_scope", verify)
    monkeypatch.setattr(claude_cli_tool, "claude_executable", lambda: "/usr/bin/claude")
    monkeypatch.setattr(codex_cli_tool, "codex_executable", lambda: "/usr/bin/codex")
    monkeypatch.setattr(cursor_cli_tool, "_resolve_cursor_agent_argv", lambda: ["/usr/bin/agent"])
    monkeypatch.setattr(muse_cli_tool, "_which_muse_native", lambda: "/usr/bin/muse")
    monkeypatch.setattr(adapter, "opencode_executable", lambda: "/usr/bin/opencode")
    result = recovery.apply()
    assert result["success"] and seen == ["verified"]
    assert manager.get_setting("starred_slash_commands") == ["/codex"]
    assert all(not item["path"] for item in result["cli_paths"].values())

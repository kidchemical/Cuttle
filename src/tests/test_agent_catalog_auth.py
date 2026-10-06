"""Agent catalog credential/auth declarations used by Settings."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from api.agent_harness import catalog as cat  # noqa: E402

BUNDLED = Path(SRC) / "api" / "agent_harness" / "agents"


def _bundled_ids():
    return sorted(
        p.name for p in BUNDLED.iterdir()
        if p.is_dir() and (p / "manifest.yaml").exists()
    )


# ── manifest declarations ─────────────────────────────────────────────────


@pytest.mark.parametrize("agent_id", _bundled_ids())
def test_every_bundled_manifest_parses(agent_id):
    """Adding a manifest must not be able to break discovery."""
    manifest = cat.get_agent(agent_id)[0]
    assert manifest.id == agent_id
    assert manifest.slash


def test_every_bundled_agent_declares_native_authentication():
    for agent_id in _bundled_ids():
        manifest = cat.get_agent(agent_id)[0]
        assert not manifest.credential_env
        assert manifest.auth_command or manifest.install_hint or not manifest.requires_cloud


def test_credential_env_names_are_uppercase_env_vars():
    for agent_id in _bundled_ids():
        manifest = cat.get_agent(agent_id)[0]
        for name in manifest.credential_env:
            assert name.isupper(), f"{manifest.id}: {name}"
            assert name.endswith(("_API_KEY", "_KEY", "_TOKEN")), f"{manifest.id}: {name}"






def test_cli_authed_agents_declare_no_cuttle_credential():
    for agent_id in ("claude", "cursor", "codex"):
        manifest = cat.get_agent(agent_id)[0]
        assert manifest.credential_env == [], agent_id
        assert manifest.auth_command, agent_id


def test_local_agent_needs_no_credential_and_no_login():
    manifest = cat.get_agent("hermes")[0]
    assert manifest.requires_cloud is False
    assert manifest.credential_env == []


# ── public rows ───────────────────────────────────────────────────────────


def test_public_rows_expose_the_new_fields():
    for row in cat.public_catalog():
        assert "credential_env" in row
        assert "credential_present" in row
        assert "ready" in row
        assert isinstance(row["ready"], bool)




def test_cli_authed_agent_is_ready_without_any_key(monkeypatch):
    for name in ("META_API_KEY", "GEMINI_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    row = next(r for r in cat.public_catalog() if r["id"] == "cursor")
    assert row["available"] is True
    assert row["credential_env"] == []
    assert row["ready"] is True


def test_missing_cli_is_never_ready():
    row = next(r for r in cat.public_catalog() if r["id"] == "antigravity")
    assert row["available"] is False
    assert row["ready"] is False
    assert row["install_hint"], "a missing CLI must carry an install hint"


def test_muse_readiness_is_independent_of_cuttle_key(monkeypatch):
    from scripts.utilities import muse_cli_tool
    monkeypatch.setattr(muse_cli_tool, "muse_available", lambda: True)
    for value in (None, "host-key"):
        if value is None:
            monkeypatch.delenv("META_API_KEY", raising=False)
        else:
            monkeypatch.setenv("META_API_KEY", value)
        row = next(r for r in cat.public_catalog() if r['id'] == 'muse')
        assert row['available'] and row['ready']
        assert not row['credential_env']

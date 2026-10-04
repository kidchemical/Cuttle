"""Agent catalog credential/auth declarations + wizard setup status.

Settings → Agents and the OOBE wizard both render from the catalog, so both
now depend on two new public row fields:

* ``credential_env`` / ``credential_present`` / ``ready`` — "which CLI can
  actually run a turn, and what is missing if not",
* ``auth_command`` — the CLI's own login, for agents that need no Cuttle key.

``ready`` is computed in ``public_catalog`` rather than in each consumer so the
Settings list and the wizard cannot disagree about what "installed" means.
"""

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


# ── wizard status ─────────────────────────────────────────────────────────


def _owner_client(tmp_path, monkeypatch):
    from api import auth_db as auth_db_mod
    from api import web_chat_api as wca

    db_path = tmp_path / "wizard-status.db"
    monkeypatch.setattr(auth_db_mod, "DB_PATH", db_path)
    auth_db_mod._db_instance = None
    db = auth_db_mod.AuthDatabase(db_path)
    auth_db_mod._db_instance = db
    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)
    monkeypatch.setattr("api.web_chat_api.get_auth_db", lambda: db)
    monkeypatch.setenv("OWNER_USER_EMAIL", "owner@local")
    owner = db.create_user("owner@local", "Owner", "local", password="x")
    client = wca.app.test_client()
    client.set_cookie("session_token", db.create_auth_session(owner))
    return client


def test_wizard_status_requires_owner(tmp_path, monkeypatch):
    from api import web_chat_api as wca

    anon = wca.app.test_client()
    assert anon.get("/api/wizard/status").status_code == 401


def test_wizard_status_lists_agents_and_provider_state(tmp_path, monkeypatch):
    for name in ("OPENAI_API_KEY", "API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    client = _owner_client(tmp_path, monkeypatch)
    payload = client.get("/api/wizard/status").get_json()
    assert payload["success"] is True
    assert payload["agent_count"] >= 1
    assert len(payload["agents"]) == payload["agent_count"]
    for agent in payload["agents"]:
        for key in ("id", "label", "slash", "ready", "available", "credential_env"):
            assert key in agent, key
    assert payload["completion_providers"]
    assert "pinned_provider" in payload


def test_wizard_never_points_at_a_retired_step(tmp_path, monkeypatch):
    """'default_pipeline' was a hardcoded True from the retired graphs, and
    'api_keys' pushed CLI-only users to a page they never needed."""
    client = _owner_client(tmp_path, monkeypatch)
    steps = client.get("/api/wizard/status").get_json()["steps"]
    assert "api_keys" not in steps
    assert steps["agent_cli"] in (True, False)
    assert "default_pipeline" not in steps


def test_wizard_next_step_prefers_getting_an_agent_working(tmp_path, monkeypatch):
    """No usable agent outranks a missing cheap-completion key: without an
    agent there is nothing to title."""
    from api.agent_harness import catalog as cat_mod

    client = _owner_client(tmp_path, monkeypatch)
    real = cat_mod.public_catalog
    monkeypatch.setattr(
        cat_mod, "public_catalog",
        lambda *a, **k: [
            {**row, "available": False, "ready": False} for row in real(*a, **k)
        ],
    )
    payload = client.get("/api/wizard/status").get_json()
    assert payload["steps"]["agent_cli"] is False
    assert payload["next_step"] == "agent_cli"
    assert payload["next_action"]


def test_wizard_reports_done_when_agents_and_a_provider_exist(tmp_path, monkeypatch):
    from api.agent_harness import catalog as cat_mod

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    client = _owner_client(tmp_path, monkeypatch)
    real = cat_mod.public_catalog
    monkeypatch.setattr(
        cat_mod, "public_catalog",
        lambda *a, **k: [{**row, "available": True, "ready": True} for row in real(*a, **k)],
    )
    payload = client.get("/api/wizard/status").get_json()
    assert payload["steps"]["agent_cli"] is True
    assert payload["steps"]["completion_provider"] is True
    # `channels` is a separate opt-in, so setup is not 'done' until it is set.
    assert payload["next_step"] in ("channels", "done")

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

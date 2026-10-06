"""Session-messages ``agent_pins`` carry the starred effort for unpinned agents.

Regression: in an existing chat, re-pinning another agent (Muse → Claude,
Claude → Muse, …) showed a bare chip with no starred effort. The history
payload sent ``agent_pins.<agent>.effort == ""`` for every agent the chat had
never pinned; ``seed<Agent>SupplementFromSessionData`` treats a present-but-blank
pin as resolved ("explicitly unpinned") and stamps the effort key with the
session id, so ``load<Agent>EffortForPalette`` never fetched the starred
default. New chats were unaffected (no session id → the fetch runs).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from api.agent_harness import agent_defaults

AGENTS = ("muse", "hermes", "opencode", "codex", "claude")


class _FakeSettings:
    def __init__(self):
        self.store = {}

    def get_setting(self, key, default=None):
        return self.store.get(key, default)

    def set_setting(self, key, value):
        self.store[key] = value
        return True


def _stub_session_efforts(monkeypatch, pinned: dict):
    from api.agent_harness.agents.opencode import session_store as opencode_store
    from scripts.utilities import claude_cli_session_store as claude_store
    from scripts.utilities import codex_cli_session_store as codex_store
    from scripts.utilities import hermes_cli_session_store as hermes_store
    from scripts.utilities import muse_cli_session_store as muse_store

    modules = {
        "muse": muse_store,
        "hermes": hermes_store,
        "opencode": opencode_store,
        "codex": codex_store,
        "claude": claude_store,
    }
    for aid, mod in modules.items():
        monkeypatch.setattr(
            mod, f"load_{aid}_effort", lambda _sid, _aid=aid: pinned.get(_aid)
        )


@pytest.fixture
def client_and_sid(tmp_path: Path, monkeypatch):
    from api.auth_db import AuthDatabase

    db = AuthDatabase(tmp_path / "cuttle_auth.db")
    owner = db.create_user("owner@local", "Owner", "local", password="x")
    sid = db.create_chat_session(owner, "existing chat")
    db.add_message(sid, "user", "/muse hi")
    db.add_message(sid, "assistant", "hello")
    token = db.create_auth_session(owner)

    monkeypatch.setattr("api.auth_api.get_auth_db", lambda: db)
    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)
    settings = _FakeSettings()
    monkeypatch.setattr(agent_defaults, "_settings", lambda: settings)

    from api import web_chat_api as wca

    client = wca.app.test_client()
    client.set_cookie("session_token", token)
    return client, sid


def _pins(client, sid):
    res = client.get(f"/api/auth/sessions/{sid}/messages")
    assert res.status_code == 200
    return res.get_json()["agent_pins"]


@pytest.mark.parametrize("aid", AGENTS)
def test_unpinned_agent_effort_falls_back_to_starred(client_and_sid, monkeypatch, aid):
    client, sid = client_and_sid
    # The chat pinned Muse effort earlier; the re-pinned agent never was.
    _stub_session_efforts(monkeypatch, {"muse": "high"} if aid != "muse" else {})
    agent_defaults.set_starred_effort(aid, "low")

    pins = _pins(client, sid)

    assert pins[aid]["effort"] == "low"


@pytest.mark.parametrize("aid", AGENTS)
def test_session_effort_pin_beats_starred(client_and_sid, monkeypatch, aid):
    client, sid = client_and_sid
    _stub_session_efforts(monkeypatch, {aid: "medium"})
    agent_defaults.set_starred_effort(aid, "low")

    assert _pins(client, sid)[aid]["effort"] == "medium"


def test_no_star_no_pin_stays_blank(client_and_sid, monkeypatch):
    client, sid = client_and_sid
    _stub_session_efforts(monkeypatch, {})

    pins = _pins(client, sid)

    assert all(pins[aid]["effort"] == "" for aid in AGENTS)

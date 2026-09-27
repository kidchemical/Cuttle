"""Send-time model/effort identity — shared across effort harnesses.

Composer pins ride on ``/api/chat`` as ``agent_pins``. The server freezes one
(agent, model, effort) tuple for the user-badge chip AND the CLI argv so a
starred default cannot leak into the chip while the process runs ``max``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from api import web_chat_api as w
from api.agent_harness import agent_defaults as defaults

CHAT_JS = Path(__file__).resolve().parents[1] / "web" / "js" / "chat_page.js"

# Harnesses that pin reasoning effort (Cursor bakes effort into the model id).
EFFORT_AGENTS = ("muse", "hermes", "opencode", "codex")

SAMPLE_EFFORT = "xhigh"
SAMPLE_MODEL = {
    "muse": "muse-spark-1.2",
    "hermes": "claude-sonnet-4",
    "opencode": "opencode/big-pickle",
    "codex": "gpt-5.6-luna",
}


class _FakeSettings:
    def __init__(self):
        self.store = {}

    def get_setting(self, key, default=None):
        return self.store.get(key, default)

    def set_setting(self, key, value):
        self.store[key] = value
        return True


def _store_module(agent: str):
    if agent == "muse":
        from scripts.utilities import muse_cli_session_store as store

        return store, "load_muse_effort", "load_muse_model"
    if agent == "hermes":
        from scripts.utilities import hermes_cli_session_store as store

        return store, "load_hermes_effort", "load_hermes_model"
    if agent == "opencode":
        from api.agent_harness.agents.opencode import session_store as store

        return store, "load_opencode_effort", "load_opencode_model"
    if agent == "codex":
        from scripts.utilities import codex_cli_session_store as store

        return store, "load_codex_effort", "load_codex_model"
    raise AssertionError(agent)


@pytest.fixture(params=list(EFFORT_AGENTS))
def effort_agent(request):
    return request.param


def test_chat_request_attaches_active_identity_even_when_not_dirty():
    """Active harness composer pins always go on the POST — not only *Dirty."""
    src = CHAT_JS.read_text(encoding="utf-8")
    assert "function attachAgentIdentityToRequest(requestBody)" in src
    start = src.index("function attachAgentIdentityToRequest(requestBody)")
    end = src.index("\n    function ", start + 10)
    body = src[start:end]
    assert "requestBody.agent_pins" in body
    assert "else if (aid === 'codex') put('codex', S.codexModel, S.codexEffort)" in body
    for agent in EFFORT_AGENTS:
        assert f"S.{agent}Effort" in body, agent
        assert f"{agent}EffortDirty" in body, agent
        assert f"{agent}ModelDirty" in body, agent

    proc = src[
        src.index("await ensureAuthChatSession();")
        : src.index("await ensureAuthChatSession();") + 2500
    ]
    assert "attachAgentIdentityToRequest(requestBody)" in proc


@pytest.mark.parametrize(
    "fn_name",
    [
        "persistMuseEffortSelection",
        "persistHermesEffortSelection",
        "persistOpenCodeEffortSelection",
        "persistCodexEffortSelection",
    ],
)
def test_persist_effort_does_not_clear_dirty_in_finally(fn_name):
    """A failed/slow pin POST must not drop *Dirty before /api/chat attaches pins."""
    src = CHAT_JS.read_text(encoding="utf-8")
    start = src.index(f"function {fn_name}(")
    end = src.index("\n    function ", start + 10)
    chunk = src[start:end]
    assert ".finally" not in chunk, chunk[-400:]


def test_apply_request_agent_pins_beats_starred_on_user_badge(
    effort_agent, tmp_path, monkeypatch
):
    """agent_pins must land in the session store before the user-badge snapshot."""
    settings = _FakeSettings()
    monkeypatch.setattr(defaults, "_settings", lambda: settings)
    defaults.set_starred_effort(effort_agent, "low")

    store, load_effort, load_model = _store_module(effort_agent)
    monkeypatch.setattr(store, "_map_file", lambda: tmp_path / f"{effort_agent}_map.json")

    sid = 42
    model = SAMPLE_MODEL[effort_agent]
    w._apply_request_agent_pins(
        sid,
        {"agent_pins": {effort_agent: {"effort": SAMPLE_EFFORT, "model": model}}},
    )

    assert getattr(store, load_effort)(sid) == SAMPLE_EFFORT
    assert getattr(store, load_model)(sid) == model

    badge = w._user_badge_metadata(f"/{effort_agent} hello", sid)
    assert badge is not None
    chip = badge["slash_command"]["chips"][0]
    assert chip["category"] == effort_agent
    assert SAMPLE_EFFORT in chip["label"], chip
    assert f"effort {SAMPLE_EFFORT}" in chip["meta"], chip
    assert "low" not in chip["label"].split("·")[-1], (
        f"starred low leaked into badge: {chip['label']!r}"
    )


def test_freeze_identity_chip_matches_cli_kwargs(
    effort_agent, tmp_path, monkeypatch
):
    """Composer pin on this POST is the chip AND the CLI, even if the store is stale."""
    settings = _FakeSettings()
    monkeypatch.setattr(defaults, "_settings", lambda: settings)
    defaults.set_starred_effort(effort_agent, "low")
    defaults.set_starred_model(effort_agent, SAMPLE_MODEL[effort_agent])

    store, load_effort, load_model = _store_module(effort_agent)
    monkeypatch.setattr(store, "_map_file", lambda: tmp_path / f"{effort_agent}_map.json")

    sid = 99
    model = SAMPLE_MODEL[effort_agent]
    # Leftover pin from an earlier turn (the CH-000528 split).
    w._apply_request_agent_pins(
        sid,
        {"agent_pins": {effort_agent: {"effort": "low", "model": model}}},
    )
    data = {
        "agent_pins": {effort_agent: {"effort": SAMPLE_EFFORT, "model": model}},
    }
    ident = w._freeze_send_identity(sid, f"/{effort_agent} hello", data)
    assert ident is not None
    assert ident["agent"] == effort_agent
    assert ident["effort"] == SAMPLE_EFFORT, ident

    badge = w._user_badge_metadata(
        f"/{effort_agent} hello", sid, identity=ident
    )
    chip = badge["slash_command"]["chips"][0]
    assert SAMPLE_EFFORT in chip["label"], chip
    assert f"effort {SAMPLE_EFFORT}" in chip["meta"], chip
    assert "low" not in chip["label"].split("·")[-1], chip["label"]

    kw = w._harness_identity_run_kwargs(ident)
    assert kw["execute_kwargs"]["reasoning_effort"] == SAMPLE_EFFORT
    assert kw["model_override"] == model
    assert kw["execute_kwargs"]["reasoning_effort"] in chip["meta"]


def test_server_applies_pins_then_freezes_identity_for_badge_and_cli():
    """Pin apply → freeze → persist chip + harness execute_kwargs, in that order."""
    text = (
        Path(__file__).resolve().parents[1] / "api" / "web_chat_api.py"
    ).read_text(encoding="utf-8")
    main = text.index(
        "# Authenticated chats: assign a DB session id before remaining slash-command"
    )
    apply = text.index("_apply_request_agent_pins(chat_session_id, data)", main)
    freeze = text.index("_freeze_send_identity(", main)
    persist_turn = text.index("def _persist_user_turn(sid):", main)
    ident_badge = text.index(
        "_user_badge_metadata(\n                    message_content, sid, identity=_send_identity",
        main,
    )
    ident_run = text.index("**_ident_run_kw", main)
    assert apply < freeze < persist_turn
    assert persist_turn < ident_badge
    assert ident_run > persist_turn

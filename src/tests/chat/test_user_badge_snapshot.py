"""User-bubble badges must snapshot the send-time config, not live pins.

Regression: user bubbles re-derived their badge from the current
slashPaletteSupplement pins at render, so changing the effort level and
refreshing rewrote every older bubble's badge. The backend now persists a
per-turn ``slash_command`` chip snapshot; the frontend renders stored chips
as-is.
"""

from __future__ import annotations


def test_user_badge_snapshot_muse_model_and_effort(monkeypatch):
    from api import web_chat_api as w

    monkeypatch.setattr(
        "scripts.utilities.muse_cli_session_store.load_muse_model",
        lambda sid: "muse-spark-1.2",
    )
    monkeypatch.setattr(
        "scripts.utilities.muse_cli_session_store.load_muse_effort",
        lambda sid: "high",
    )
    meta = w._user_badge_metadata("/muse do the thing", "sess-1")
    chip = meta["slash_command"]["chips"][0]
    assert "high" in chip["label"]
    assert "effort high" in chip["meta"]
    assert "model muse-spark-1.2" in chip["meta"]
    assert chip["category"] == "muse"


def test_user_badge_snapshot_effort_change_gives_different_chips(monkeypatch):
    """Two turns sent under different effort pins snapshot different badges."""
    from api import web_chat_api as w

    monkeypatch.setattr(
        "scripts.utilities.muse_cli_session_store.load_muse_model",
        lambda sid: "muse-spark-1.2",
    )
    state = {"effort": "low"}
    monkeypatch.setattr(
        "scripts.utilities.muse_cli_session_store.load_muse_effort",
        lambda sid: state["effort"],
    )
    first = w._user_badge_metadata("/muse turn one", "sess-1")["slash_command"]["chips"][0]
    state["effort"] = "ultra"
    second = w._user_badge_metadata("/muse turn two", "sess-1")["slash_command"]["chips"][0]
    assert "low" in first["label"]
    assert "ultra" in second["label"]
    assert first["label"] != second["label"]


def test_user_badge_snapshot_non_agent_returns_none():
    from api import web_chat_api as w

    assert w._user_badge_metadata("just chatting", "sess-1") is None
    assert w._user_badge_metadata("/restart graceful", "sess-1") is None


def test_user_badge_snapshot_cursor_identity_overrides_session_pin(monkeypatch):
    from api import web_chat_api as w

    monkeypatch.setattr(
        "scripts.utilities.cursor_cli_session_store.load_cursor_agent_options",
        lambda root, sid: {"model": "auto"},
    )
    meta = w._user_badge_metadata(
        "/cursor riddle",
        "sess-1",
        identity={"agent": "cursor", "model": "cursor-grok-4.6-low", "effort": "low"},
    )
    chip = meta["slash_command"]["chips"][0]
    assert chip["category"] == "cursor"
    assert "auto" not in chip["label"].lower()
    assert "grok" in chip["label"].lower()


def test_user_badge_snapshot_cursor_format(monkeypatch):
    from api import web_chat_api as w

    monkeypatch.setattr(
        "scripts.utilities.cursor_cli_session_store.load_cursor_agent_options",
        lambda root, sid: {"model": "auto"},
    )
    meta = w._user_badge_metadata("/cursor do it", "sess-1")
    chip = meta["slash_command"]["chips"][0]
    assert chip["label"] == "Cursor - Auto"
    assert chip["category"] == "cursor"


def test_frontend_user_history_uses_stored_chips_without_live_enrichment():
    """addMessageToUI user branch must not call live-pin enrichers on stored chips."""
    from pathlib import Path

    text = (Path(__file__).resolve().parents[2] / "web" / "js" / "chat/chat_page.js").read_text(
        encoding="utf-8"
    )
    assert "hasStoredChips" in text
    assert "never rewrite a snapshot with the current pin" in text

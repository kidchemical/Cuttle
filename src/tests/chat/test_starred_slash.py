"""Starred sticky slash default (Cuttle chat)."""

from __future__ import annotations

from pathlib import Path

from api.auth_db import AuthDatabase
from api import starred_slash as ss


def test_normalize_and_starred_roundtrip(tmp_path: Path, monkeypatch):
    from managers.settings_manager import SettingsManager

    sm = SettingsManager(str(tmp_path / "settings.json"))
    monkeypatch.setattr("managers.settings_manager.get_settings_manager", lambda: sm)

    assert ss.set_starred_prefixes(["/cursor"]) == ["/cursor "]
    assert ss.get_starred_prefixes() == ["/cursor "]
    assert ss.set_starred_prefixes(["/claude ", "/not-an-agent "]) == ["/claude "]
    assert ss.set_starred_prefixes([]) == []


def test_apply_starred_prefix_on_plain_dm(monkeypatch):
    monkeypatch.setattr(ss, "get_starred_prefixes", lambda: ["/cursor "])
    monkeypatch.setattr(ss, "infer_session_sticky_prefix", lambda _sid: None)
    assert ss.apply_default_sticky_prefix("hi") == "/cursor hi"
    assert ss.apply_default_sticky_prefix("/cursor already") == "/cursor already"
    assert ss.apply_default_sticky_prefix("/help") == "/help"
    assert ss.apply_default_sticky_prefix("/invite Joey") == "/invite Joey"


def test_session_history_wins_over_star(tmp_path: Path, monkeypatch):
    db = AuthDatabase(tmp_path / "cuttle_auth.db")
    owner = db.create_user("owner@local", "Owner", "local", password="x")
    sid = db.create_chat_session(owner, "web cursor chat")
    db.add_message(sid, "user", "/cursor fix the pig")
    db.add_message(sid, "assistant", "ok")

    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)
    monkeypatch.setattr(ss, "get_starred_prefixes", lambda: ["/claude "])

    assert ss.apply_default_sticky_prefix("the scarecrow is broken", sid) == (
        "/cursor the scarecrow is broken"
    )


def test_no_star_leaves_pipeline_message(monkeypatch):
    monkeypatch.setattr(ss, "get_starred_prefixes", lambda: [])
    monkeypatch.setattr(ss, "infer_session_sticky_prefix", lambda _sid: None)
    assert ss.apply_default_sticky_prefix("hi") == "hi"


def test_control_payloads_never_get_an_agent_prefix(monkeypatch):
    monkeypatch.setattr(ss, "get_starred_prefixes", lambda: ["/cursor "])
    monkeypatch.setattr(ss, "infer_session_sticky_prefix", lambda _sid: None)
    for payload in (
        "[button:project-action-1]",
        "[action-form:abc123]",
        "[form:signup]",
    ):
        assert ss.apply_default_sticky_prefix(payload) == payload


def test_star_seeds_new_chat_but_not_a_running_one(tmp_path: Path, monkeypatch):
    """Chat-page rule, server-side: star applies to a fresh chat only."""
    db = AuthDatabase(tmp_path / "cuttle_auth.db")
    owner = db.create_user("owner@local", "Owner", "local", password="x")
    fresh = db.create_chat_session(owner, "brand new")
    running = db.create_chat_session(owner, "plain conversation")
    db.add_message(running, "user", "what is a cuttlefish")
    db.add_message(running, "assistant", "a cephalopod")

    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)
    monkeypatch.setattr(ss, "get_starred_prefixes", lambda: ["/cursor "])

    assert ss.apply_default_sticky_prefix(
        "fix the build", fresh, star_on_new_session_only=True
    ) == "/cursor fix the build"
    assert ss.apply_default_sticky_prefix(
        "and what do they eat", running, star_on_new_session_only=True
    ) == "and what do they eat"
    # Discord DMs keep following the star regardless of history.
    assert ss.apply_default_sticky_prefix("and what do they eat", running) == (
        "/cursor and what do they eat"
    )

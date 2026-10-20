"""Stale CLI resume must start a fresh session, not fail the turn.

Repo moves and CLI session pruning can strand mapped resume UUIDs
with no session data behind them on the host. The
muse/cursor adapters drop such dead bindings pre-turn and note the fresh
start in the reply. Also covers the cursor cost-estimate ordering fix
(``reported`` must be resolved before ``attach_estimated_cost`` sees it).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

DEAD_MUSE = "aaaaaaaa-1111-2222-3333-444444444444"
LIVE_MUSE = "bbbbbbbb-1111-2222-3333-444444444444"
NEW_MUSE = "cccccccc-1111-2222-3333-444444444444"
DEAD_CURSOR = "dddddddd-1111-2222-3333-444444444444"
LIVE_CURSOR = "eeeeeeee-1111-2222-3333-444444444444"


def _isolate_muse(tmp_path: Path, monkeypatch) -> Path:
    from scripts.utilities import muse_cli_session_store as store

    home = tmp_path / "muse-home"
    (home / ".local" / "share" / "muse" / "sessions").mkdir(parents=True)
    monkeypatch.setattr(store, "_map_file", lambda: tmp_path / "muse_map.json")
    monkeypatch.setattr(store, "muse_data_home", lambda: home / ".local" / "share" / "muse")
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("MUSE_MODEL", "muse-spark-1.3")
    monkeypatch.setenv("CUTTLE_AGENT_STEER", "0")
    return home


def _muse_log(home: Path, uuid: str) -> Path:
    d = home / ".local" / "share" / "muse" / "sessions" / "2026" / "10" / "18" / uuid
    d.mkdir(parents=True)
    (d / "session.jsonl").write_text('{"kind":"run"}\n', encoding="utf-8")
    return d


class _FakeMuseTool:
    seen_resume: list

    def __init__(self, model=None, **kwargs):
        self.model = model

    async def execute_prompt(self, *args, **kwargs):
        type(self).seen_resume.append(kwargs.get("resume"))
        return {
            "success": True,
            "output": "fresh reply",
            "usage": {},
            "muse_session_id": NEW_MUSE,
        }


def _run_muse(monkeypatch, **kwargs):
    from api.agent_harness.agents.muse.adapter import Adapter
    import scripts.utilities.muse_cli_tool as tool

    _FakeMuseTool.seen_resume = []
    monkeypatch.setattr(tool, "MuseCliTool", _FakeMuseTool)
    return asyncio.run(Adapter().execute("hello", **kwargs))


def test_muse_dead_resume_starts_fresh_with_notice(tmp_path, monkeypatch):
    from scripts.utilities import muse_cli_session_store as store

    home = _isolate_muse(tmp_path, monkeypatch)
    cwd = str(tmp_path / "proj")
    Path(cwd).mkdir()
    store.save_muse_resume_id(cwd, "1319", DEAD_MUSE)

    result = _run_muse(monkeypatch, cwd=cwd, resume=DEAD_MUSE, model="muse-spark-1.3",
                       chat_session_id="1319")

    assert result.success
    assert _FakeMuseTool.seen_resume == [None]
    assert store.load_muse_resume_id(cwd, "1319") is None
    assert "was not found on this host" in (result.output or "")
    assert "fresh session" in (result.output or "")


def test_muse_live_resume_passes_through(tmp_path, monkeypatch):
    from scripts.utilities import muse_cli_session_store as store

    home = _isolate_muse(tmp_path, monkeypatch)
    _muse_log(home, LIVE_MUSE)
    cwd = str(tmp_path / "proj")
    Path(cwd).mkdir()
    store.save_muse_resume_id(cwd, "1319", LIVE_MUSE)

    result = _run_muse(monkeypatch, cwd=cwd, resume=LIVE_MUSE, model="muse-spark-1.3",
                       chat_session_id="1319")

    assert result.success
    assert _FakeMuseTool.seen_resume == [LIVE_MUSE]
    assert store.load_muse_resume_id(cwd, "1319") == LIVE_MUSE
    assert "was not found on this host" not in (result.output or "")


def test_muse_uncertain_layout_attempts_resume(tmp_path, monkeypatch):
    from scripts.utilities import muse_cli_session_store as store

    _isolate_muse(tmp_path, monkeypatch)
    # No sessions root at all: layout uncertain, resume attempted as before.
    import shutil

    shutil.rmtree(tmp_path / "muse-home" / ".local" / "share" / "muse")
    cwd = str(tmp_path / "proj")
    Path(cwd).mkdir()
    store.save_muse_resume_id(cwd, "1319", DEAD_MUSE)

    result = _run_muse(monkeypatch, cwd=cwd, resume=DEAD_MUSE, model="muse-spark-1.3",
                       chat_session_id="1319")

    assert result.success
    assert _FakeMuseTool.seen_resume == [DEAD_MUSE]
    assert store.load_muse_resume_id(cwd, "1319") == DEAD_MUSE


def _isolate_cursor(tmp_path: Path, monkeypatch) -> Path:
    from scripts.utilities import cursor_cli_session_store as store

    home = tmp_path / "cursor-home"
    (home / ".cursor" / "projects").mkdir(parents=True)
    monkeypatch.setattr(store, "_map_file", lambda: tmp_path / "cursor_map.json")
    monkeypatch.setattr(store, "_cursor_transcript_root", lambda: home / ".cursor" / "projects")
    monkeypatch.setattr(
        "api.agent_harness.agent_defaults.get_starred_model", lambda _aid: None
    )
    return home


def _cursor_transcript(home: Path, uuid: str) -> Path:
    d = home / ".cursor" / "projects" / "ws" / "agent-transcripts" / uuid
    d.mkdir(parents=True)
    (d / f"{uuid}.jsonl").write_text("{}\n", encoding="utf-8")
    return d


def _run_cursor(monkeypatch, **kwargs):
    from api.agent_harness.agents.cursor.adapter import Adapter
    import scripts.utilities.cursor_cli_tool as tool

    def _fake(prompt, **kw):
        _fake.seen.append(kw)
        run_meta = kw.get("run_meta_out")
        if isinstance(run_meta, dict):
            run_meta["reported_model"] = "grok-4.6"
            run_meta["usage"] = {"inputTokens": 10, "outputTokens": 5, "totalTokens": 15}
        return "cursor reply"

    _fake.seen = []
    monkeypatch.setattr(tool, "_cursor_agent_oneline_prompt", _fake)
    result = asyncio.run(Adapter().execute("hello", **kwargs))
    return result, _fake.seen


def test_cursor_dead_resume_starts_fresh_with_notice(tmp_path, monkeypatch):
    from scripts.utilities import cursor_cli_session_store as store

    _isolate_cursor(tmp_path, monkeypatch)
    cwd = str(tmp_path / "proj")
    Path(cwd).mkdir()
    store.save_cursor_resume_id(cwd, "453", DEAD_CURSOR)
    assert store.load_cursor_resume_id(cwd, "453") == DEAD_CURSOR

    result, _seen = _run_cursor(
        monkeypatch, cwd=cwd, resume=DEAD_CURSOR, model=None, chat_session_id="453"
    )

    assert result.success
    assert store.load_cursor_resume_id(cwd, "453") is None
    assert "was not found on this host" in (result.output or "")
    assert "fresh session" in (result.output or "")


def test_cursor_live_resume_kept_without_notice(tmp_path, monkeypatch):
    from scripts.utilities import cursor_cli_session_store as store

    home = _isolate_cursor(tmp_path, monkeypatch)
    _cursor_transcript(home, LIVE_CURSOR)
    cwd = str(tmp_path / "proj")
    Path(cwd).mkdir()
    store.save_cursor_resume_id(cwd, "453", LIVE_CURSOR)

    result, _seen = _run_cursor(
        monkeypatch, cwd=cwd, resume=LIVE_CURSOR, model=None, chat_session_id="453"
    )

    assert result.success
    assert store.load_cursor_resume_id(cwd, "453") == LIVE_CURSOR
    assert "was not found on this host" not in (result.output or "")


def test_cursor_uncertain_layout_attempts_resume(tmp_path, monkeypatch):
    from scripts.utilities import cursor_cli_session_store as store

    home = _isolate_cursor(tmp_path, monkeypatch)
    import shutil

    shutil.rmtree(home / ".cursor")
    cwd = str(tmp_path / "proj")
    Path(cwd).mkdir()
    store.save_cursor_resume_id(cwd, "453", DEAD_CURSOR)

    result, _seen = _run_cursor(
        monkeypatch, cwd=cwd, resume=DEAD_CURSOR, model=None, chat_session_id="453"
    )

    assert result.success
    assert store.load_cursor_resume_id(cwd, "453") == DEAD_CURSOR
    assert "was not found on this host" not in (result.output or "")


def test_cursor_cost_estimate_uses_reported_model(tmp_path, monkeypatch):
    import api.model_pricing as pricing

    _isolate_cursor(tmp_path, monkeypatch)
    cwd = str(tmp_path / "proj")
    Path(cwd).mkdir()
    seen = {}

    def _fake_attach(usage, model, **kwargs):
        seen["model"] = model

    monkeypatch.setattr(pricing, "attach_estimated_cost", _fake_attach)

    result, _seen = _run_cursor(
        monkeypatch, cwd=cwd, resume=None, model=None, chat_session_id="453"
    )

    assert result.success
    # Before the ordering fix this silently received "" (the NameError was
    # swallowed), so cost was never estimated for the reported model.
    assert seen.get("model") == "grok-4.6"

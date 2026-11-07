"""Silent auto-compaction detection and managed ("fresh") compaction.

Adapters for Cursor / OpenCode / Hermes / Muse never report
``context_compacted``; the kernel infers it from a large window-fill drop on
the same native session. A fresh compact discards the session and makes the
next turn carry the full briefing plus the recent transcript.
"""

from __future__ import annotations

import pytest

from tests.brain.context_support import ack_snapshot
from tests.brain.test_brain_handoff_context import _Adapter, _chat, _run

from api.agent_harness.types import AgentManifest, AgentResult


class _FillAdapter(_Adapter):
    """Reports a scripted window fill per turn (and optionally a new session)."""

    def __init__(self, cwd, fill, *, session_id="native-1"):
        super().__init__(cwd)
        self.fill = fill
        self.session_id = session_id

    async def execute(self, prompt, **kwargs):
        self.prompts.append(prompt)
        return AgentResult(success=True, output="ok", session_id=self.session_id,
                           usage={"context_tokens": self.fill})


# --- pure drop rule + metrics read -------------------------------------------


@pytest.mark.parametrize("previous,current,dropped", [
    (100_000, 30_000, True),
    (100_000, 60_000, True),
    (100_000, 61_000, False),
    (100_000, 140_000, False),
    (15_000, 1_000, False),   # small windows are noise, not compaction
    (None, 1_000, False),
    (100_000, 0, False),      # unknown fill never counts
])
def test_fill_dropped(previous, current, dropped):
    from api.cuttle_brain.metrics import fill_dropped

    assert fill_dropped(previous, current) is dropped


def test_last_context_fill_reads_newest_successful_turn_of_that_agent():
    from api.cuttle_brain import metrics

    metrics.record_turn(chat_session_id="9", agent_id="cursor", context_tokens=50_000, success=True)
    metrics.record_turn(chat_session_id="9", agent_id="cursor", context_tokens=80_000, success=True)
    metrics.record_turn(chat_session_id="9", agent_id="cursor", context_tokens=5, success=False)
    metrics.record_turn(chat_session_id="9", agent_id="codex", context_tokens=1_000, success=True)
    assert metrics.last_context_fill("9", "cursor") == 80_000
    assert metrics.last_context_fill("9", "claude") is None
    assert metrics.last_context_fill(None, "cursor") is None


# --- kernel fallback ----------------------------------------------------------


def _two_turns(monkeypatch, tmp_path, first, second, **second_kw):
    from api.cuttle_brain import context_delta as cd

    db, sid = _chat()
    db.add_message(sid, "user", "/cursor start")
    _run(monkeypatch, "cursor", _FillAdapter(tmp_path, first), "start", sid)
    assert cd.load_injected_snapshot(str(sid), "cursor", str(tmp_path)) is not None
    db.add_message(sid, "user", "/cursor more")
    _run(monkeypatch, "cursor", _FillAdapter(tmp_path, second, **second_kw), "more", sid)
    return cd.load_injected_snapshot(str(sid), "cursor", str(tmp_path))


def test_large_fill_drop_resends_the_briefing(tmp_path, monkeypatch):
    assert _two_turns(monkeypatch, tmp_path, 120_000, 30_000) is None


def test_growing_fill_keeps_the_receipt(tmp_path, monkeypatch):
    assert _two_turns(monkeypatch, tmp_path, 120_000, 130_000) is not None


def test_a_new_native_session_is_not_a_compaction(tmp_path, monkeypatch):
    # A new session already gets the full briefing via has_resume; the drop
    # alone must not be read as the old session compacting.
    assert _two_turns(monkeypatch, tmp_path, 120_000, 30_000, session_id="native-2") is not None


# --- handoff "fresh" mode -----------------------------------------------------


def test_fresh_session_gets_recent_transcript_under_fresh_notice():
    from api.cuttle_brain import handoff as ho

    db, sid = _chat()
    db.add_message(sid, "user", "keep answers short")
    db.add_message(sid, "assistant", "ok")
    ho.record_last_agent(str(sid), "claude")
    ho.record_last_agent(str(sid), "codex")

    ho.mark_fresh_session(str(sid), "claude")
    out = ho.build_handoff(str(sid), to_agent="claude", has_native_session=False)
    assert out is not None and out.mode == "fresh"
    assert "managed compact" in out.text and "keep answers short" in out.text
    # Other agents' cursors are untouched.
    assert ho.get_seen_cursor(str(sid), "codex") is not None

    ho.record_last_agent(str(sid), "claude")
    assert "claude" not in ho._entry(str(sid))["fresh"]
    assert ho.build_handoff(str(sid), to_agent="claude", has_native_session=True) is None


# --- managed compact (agent_context) -----------------------------------------


class _ResumeAdapter:
    def __init__(self):
        self.cleared = []

    def clear_resume(self, cwd, chat_session_id):
        self.cleared.append((cwd, chat_session_id))


def _fresh(monkeypatch, tmp_path, *, resume=True, live=False, rid="rid-1"):
    from api import agent_context as ac
    from api.agent_harness import catalog

    adapter = _ResumeAdapter()
    manifest = AgentManifest(id="cursor", label="cursor", slash="/cursor", resume=resume)
    monkeypatch.setattr(catalog, "get_agent", lambda aid, project_path=None: (manifest, adapter))
    monkeypatch.setattr(ac, "_load_resume_id", lambda *a, **k: rid)
    monkeypatch.setattr("api.chat_run_registry.has_live_process", lambda sid: live)
    out = ac.fresh_compact_agent_context(chat_session_id="db_session_41", agent_id="cursor",
                                         cwd=str(tmp_path))
    return out, adapter


def test_fresh_compact_discards_session_and_briefing_receipt(tmp_path, monkeypatch):
    from api.cuttle_brain import context_delta as cd
    from api.cuttle_brain import handoff as ho

    ack_snapshot("41", "cursor", str(tmp_path))
    ho.record_last_agent("41", "cursor", through_message_id=3)
    out, adapter = _fresh(monkeypatch, tmp_path)
    assert out["success"] and out["method"] == "fresh"
    assert adapter.cleared == [(str(tmp_path), "41")]
    assert cd.load_injected_snapshot("41", "cursor", str(tmp_path)) is None
    assert ho.get_seen_cursor("41", "cursor") is None
    assert "cursor" in ho._entry("41")["fresh"]


@pytest.mark.parametrize("kw,needle", [
    ({"live": True}, "still running"),
    ({"resume": False}, "keeps no session"),
    ({"rid": None}, "No cursor session"),
])
def test_fresh_compact_refusals_change_nothing(tmp_path, monkeypatch, kw, needle):
    from api.cuttle_brain import context_delta as cd

    ack_snapshot("41", "cursor", str(tmp_path))
    out, adapter = _fresh(monkeypatch, tmp_path, **kw)
    assert not out["success"] and needle in out["error"]
    assert adapter.cleared == []
    assert cd.load_injected_snapshot("41", "cursor", str(tmp_path)) is not None

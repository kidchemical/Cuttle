"""Muse MSP context reader + agent_context integration."""

from __future__ import annotations

from api import agent_context as ac
from scripts.utilities import muse_cli_session_store as store


def test_read_muse_msp_context_from_live_session():
    sid = "01a0c7d5-b94b-7ad2-9a91-994010a9e6d5"
    view = store.muse_msp_view_dir(sid)
    if not view:
        return  # environment without this Muse session
    ctx = store.read_muse_msp_context(sid)
    assert ctx is not None
    assert int(ctx.get("context_tokens") or 0) > 0
    assert ctx.get("source") == "msp_view"


def test_get_agent_context_status_uses_muse_msp(monkeypatch):
    monkeypatch.setattr(ac, "lookup_catalog_context_limit", lambda _m: None)
    monkeypatch.setattr(
        ac,
        "_load_resume_id",
        lambda *_a, **_k: "01a0c7d5-b94b-7ad2-9a91-994010a9e6d5",
    )
    monkeypatch.setattr(ac, "_resolve_model_for_agent", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "scripts.utilities.muse_cli_session_store.read_muse_msp_context",
        lambda _sid: {
            "context_tokens": 27333,
            "prompt_tokens": 27158,
            "completion_tokens": 175,
            "total_tokens": 27333,
            "model": "muse-spark-1.3-contributor",
            "source": "msp_view",
        },
    )
    status = ac.get_agent_context_status(
        chat_session_id=493,
        agent_id="muse",
        cwd="C:/Projects/Cuttle",
        messages=[],
    )
    assert status["success"] is True
    assert status["used_tokens"] == 27333
    assert status["token_source"] == "msp_view"
    assert status["model"] == "muse-spark-1.3-contributor"
    assert status["percent"] > 0
    assert status["limit_tokens"] == 1_000_000

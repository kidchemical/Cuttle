"""Offline matrix: context-gauge behavior for every Cuttle agent CLI.

Does **not** spawn Cursor/Muse/OpenCode/Codex/… or spend tokens. Fixtures are
synthetic usage blobs + monkeypatched resume/MSP/compact helpers.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Set

import pytest

from api import agent_context as ac
from scripts.utilities.cursor_cli_tool import (
    cursor_usage_context_tokens,
    cursor_usage_looks_aggregated,
)

# ---------------------------------------------------------------------------
# Discover harness CLIs shipped in-repo (manifests under agent_harness/agents)
# ---------------------------------------------------------------------------

_REPO = Path(__file__).resolve().parents[2]
_AGENTS_ROOT = _REPO / "src" / "api" / "agent_harness" / "agents"
_CHAT_PAGE_JS = _REPO / "src" / "web" / "js" / "chat_page.js"


def _harness_agent_ids() -> List[str]:
    ids: List[str] = []
    for man in sorted(_AGENTS_ROOT.glob("*/manifest.yaml")):
        text = man.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"(?m)^id:\s*([A-Za-z0-9_-]+)\s*$", text)
        if m:
            ids.append(m.group(1).strip().lower())
    return ids


def _parse_ui_context_gauge_agents() -> Set[str]:
    text = _CHAT_PAGE_JS.read_text(encoding="utf-8", errors="replace")
    m = re.search(
        r"CONTEXT_GAUGE_AGENTS\s*=\s*new\s+Set\(\[([^\]]*)\]\)",
        text,
    )
    if not m:
        return set()
    return {s.strip().strip("'\"") for s in m.group(1).split(",") if s.strip()}


HARNESS_AGENTS = _harness_agent_ids()
GAUGE_BACKEND = set(ac.SUPPORTED_AGENTS)
# Agents with usage footers but no mid-session context API left — empty for now.
USAGE_BUT_NO_GAUGE = frozenset()


def _assistant_msg(
    *,
    agent: str,
    usage: Dict,
    msg_id: int = 2,
    extra_meta: Dict | None = None,
) -> Dict:
    meta: Dict = {
        "slash_command": {"chips": [{"prefix": f"/{agent} "}]},
        "usage": usage,
    }
    if extra_meta:
        meta.update(extra_meta)
    return {
        "id": msg_id,
        "role": "assistant",
        "content": "ok",
        "metadata": meta,
    }


# ---------------------------------------------------------------------------
# Inventory / parity
# ---------------------------------------------------------------------------


def test_harness_agents_discovered():
    assert "cursor" in HARNESS_AGENTS
    assert "muse" in HARNESS_AGENTS
    assert "opencode" in HARNESS_AGENTS
    assert "antigravity" in HARNESS_AGENTS
    assert "gemini" not in HARNESS_AGENTS
    assert len(HARNESS_AGENTS) >= 7


def test_gauge_backend_subset_of_harness():
    missing = GAUGE_BACKEND - set(HARNESS_AGENTS)
    assert not missing, f"SUPPORTED_AGENTS not in harness: {sorted(missing)}"


def test_ui_gauge_agents_match_backend():
    """Composer CONTEXT_GAUGE_AGENTS must match API SUPPORTED_AGENTS."""
    ui = _parse_ui_context_gauge_agents()
    assert ui == GAUGE_BACKEND, (
        f"UI/backend gauge mismatch: ui={sorted(ui)} backend={sorted(GAUGE_BACKEND)}"
    )


def test_codex_is_gauge_supported():
    assert ac.normalize_agent_id("codex") == "codex"
    assert "codex" in ac.SUPPORTED_AGENTS


# ---------------------------------------------------------------------------
# Per-CLI status matrix (no live prompts)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("agent_id", HARNESS_AGENTS, ids=lambda a: a)
def test_get_agent_context_status_support_matrix(agent_id, monkeypatch):
    monkeypatch.setattr(ac, "lookup_catalog_context_limit", lambda _m: None)
    monkeypatch.setattr(ac, "_load_resume_id", lambda *_a, **_k: None)
    monkeypatch.setattr(ac, "_resolve_model_for_agent", lambda *_a, **_k: None)
    if agent_id == "muse":
        monkeypatch.setattr(
            "scripts.utilities.muse_cli_session_store.read_muse_msp_context",
            lambda *_a, **_k: None,
            raising=False,
        )
    if agent_id == "cursor":
        monkeypatch.setattr(ac, "_cursor_recent_usage", lambda *_a, **_k: (0, None, "none"))

    st = ac.get_agent_context_status(
        chat_session_id=9001,
        agent_id=agent_id,
        cwd="C:/Projects/Cuttle",
        messages=[],
    )
    if agent_id in GAUGE_BACKEND:
        assert st.get("success") is True
        assert st.get("supported") is True
        assert st.get("agent_id") == agent_id
        assert int(st.get("limit_tokens") or 0) > 0
        assert st.get("used_tokens") == 0
    else:
        assert st.get("success") is False
        assert st.get("supported") is False


# ---------------------------------------------------------------------------
# Supported agents — synthetic fill paths
# ---------------------------------------------------------------------------


def test_cursor_honest_peak_reports_percent(monkeypatch):
    monkeypatch.setattr(ac, "lookup_catalog_context_limit", lambda _m: 1_000_000)
    monkeypatch.setattr(ac, "_load_resume_id", lambda *_a, **_k: "resume-cursor")
    monkeypatch.setattr(ac, "_resolve_model_for_agent", lambda *_a, **_k: "Auto")
    monkeypatch.setattr(ac, "_cursor_recent_usage", lambda *_a, **_k: (0, None, "none"))
    msgs = [
        _assistant_msg(
            agent="cursor",
            usage={
                "prompt_tokens": 12_000,
                "completion_tokens": 40,
                "model": "Auto",
                "context_tokens": 18_000,
                "inputTokens": 12_000,
                "cacheReadTokens": 0,
            },
        )
    ]
    st = ac.get_agent_context_status(
        chat_session_id=1, agent_id="cursor", cwd="C:/Projects/Cuttle", messages=msgs
    )
    assert st["used_tokens"] == 18_000
    assert st["token_source"] == "peak"
    assert 1.0 < st["percent"] < 5.0
    assert st["compact_available"] is True


def test_cursor_ch496_style_aggregate_not_full_window(monkeypatch):
    """Billing inn+cacheRead stamped as peak must not paint ~100% of 1M."""
    monkeypatch.setattr(ac, "lookup_catalog_context_limit", lambda _m: None)
    monkeypatch.setattr(ac, "_load_resume_id", lambda *_a, **_k: "resume-cursor")
    monkeypatch.setattr(ac, "_resolve_model_for_agent", lambda *_a, **_k: "Auto")
    monkeypatch.setattr(ac, "_cursor_recent_usage", lambda *_a, **_k: (0, None, "none"))
    usage = {
        "inputTokens": 109_965,
        "outputTokens": 15_109,
        "cacheReadTokens": 921_344,
        "cacheWriteTokens": 0,
        "context_tokens": 1_031_309,
        "peak_context_tokens": 1_031_309,
        "prompt_tokens": 109_965,
        "model": "Auto",
    }
    assert cursor_usage_looks_aggregated(usage) is True
    assert cursor_usage_context_tokens(usage) is None
    msgs = [
        _assistant_msg(
            agent="cursor",
            usage=usage,
            extra_meta={"cursor_run": {"reported_model": "Auto", "usage": usage}},
        )
    ]
    st = ac.get_agent_context_status(
        chat_session_id=496, agent_id="cursor", cwd="C:/Projects/Cuttle", messages=msgs
    )
    assert st["used_tokens"] == 0
    assert st["percent"] == 0.0
    assert st["token_source"] == "aggregated"
    assert "multi-step" in (st.get("hint") or "").lower()


def test_muse_msp_view_fill(monkeypatch):
    monkeypatch.setattr(ac, "lookup_catalog_context_limit", lambda _m: None)
    monkeypatch.setattr(ac, "_load_resume_id", lambda *_a, **_k: "muse-resume-uuid")
    monkeypatch.setattr(ac, "_resolve_model_for_agent", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "scripts.utilities.muse_cli_session_store.read_muse_msp_context",
        lambda _sid: {
            "context_tokens": 27_333,
            "prompt_tokens": 27_158,
            "completion_tokens": 175,
            "model": "muse-spark-1.3-contributor",
            "source": "msp_view",
        },
    )
    st = ac.get_agent_context_status(
        chat_session_id=493, agent_id="muse", cwd="C:/Projects/Cuttle", messages=[]
    )
    assert st["used_tokens"] == 27_333
    assert st["token_source"] == "msp_view"
    assert st["limit_tokens"] == 1_000_000
    assert 0 < st["percent"] < 5


def test_muse_message_prompt_tokens_when_no_msp(monkeypatch):
    monkeypatch.setattr(ac, "lookup_catalog_context_limit", lambda _m: 1_000_000)
    monkeypatch.setattr(ac, "_load_resume_id", lambda *_a, **_k: "muse-resume")
    monkeypatch.setattr(ac, "_resolve_model_for_agent", lambda *_a, **_k: "muse-spark-1.3")
    monkeypatch.setattr(
        "scripts.utilities.muse_cli_session_store.read_muse_msp_context",
        lambda *_a, **_k: None,
    )
    msgs = [
        _assistant_msg(
            agent="muse",
            usage={"prompt_tokens": 50_000, "completion_tokens": 10, "model": "muse-spark-1.3"},
        )
    ]
    st = ac.get_agent_context_status(
        chat_session_id=1, agent_id="muse", cwd="C:/Projects/Cuttle", messages=msgs
    )
    assert st["used_tokens"] == 50_000
    assert st["token_source"] == "prompt"
    assert st["percent"] == 5.0


def test_opencode_prompt_tokens_fill(monkeypatch):
    monkeypatch.setattr(ac, "lookup_catalog_context_limit", lambda _m: 200_000)
    monkeypatch.setattr(ac, "_load_resume_id", lambda *_a, **_k: "oc-session")
    monkeypatch.setattr(ac, "_resolve_model_for_agent", lambda *_a, **_k: "opencode/default")
    msgs = [
        _assistant_msg(
            agent="opencode",
            usage={
                "prompt_tokens": 40_000,
                "completion_tokens": 800,
                "total_tokens": 40_800,
                "model": "opencode/default",
            },
        )
    ]
    st = ac.get_agent_context_status(
        chat_session_id=1, agent_id="opencode", cwd="C:/Projects/Cuttle", messages=msgs
    )
    assert st["used_tokens"] == 40_000
    assert st["token_source"] == "prompt"
    assert st["limit_tokens"] == 200_000
    assert st["percent"] == 20.0


def test_opencode_peak_preferred_over_prompt(monkeypatch):
    monkeypatch.setattr(ac, "lookup_catalog_context_limit", lambda _m: 200_000)
    monkeypatch.setattr(ac, "_load_resume_id", lambda *_a, **_k: "oc-session")
    monkeypatch.setattr(ac, "_resolve_model_for_agent", lambda *_a, **_k: None)
    msgs = [
        _assistant_msg(
            agent="opencode",
            usage={
                "prompt_tokens": 40_000,
                "context_tokens": 55_000,
                "completion_tokens": 100,
            },
        )
    ]
    st = ac.get_agent_context_status(
        chat_session_id=1, agent_id="opencode", cwd="C:/Projects/Cuttle", messages=msgs
    )
    assert st["used_tokens"] == 55_000
    assert st["token_source"] == "peak"


def test_codex_prompt_tokens_fill(monkeypatch):
    monkeypatch.setattr(ac, "lookup_catalog_context_limit", lambda _m: None)
    monkeypatch.setattr(ac, "_load_resume_id", lambda *_a, **_k: "01aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    monkeypatch.setattr(ac, "_resolve_model_for_agent", lambda *_a, **_k: "gpt-5.4")
    monkeypatch.setattr(
        ac,
        "_codex_apply_live_or_snapshot",
        lambda **kw: (
            kw["tokens"],
            kw["token_source"],
            kw["limit"],
            kw["limit_source"],
        ),
    )
    msgs = [
        _assistant_msg(
            agent="codex",
            usage={
                "prompt_tokens": 24_763,
                "completion_tokens": 122,
                "total_tokens": 24_885,
                "model": "gpt-5.4",
            },
        )
    ]
    st = ac.get_agent_context_status(
        chat_session_id=1, agent_id="codex", cwd="C:/Projects/Cuttle", messages=msgs
    )
    assert st["success"] is True
    assert st["supported"] is True
    assert st["used_tokens"] == 24_763
    assert st["token_source"] == "prompt"
    assert st["limit_tokens"] == 272_000
    assert 0 < st["percent"] < 20
    assert st["compact_available"] is True


def test_codex_ch504_billing_totals_not_full_window(monkeypatch):
    """CH-000504-2: turn.completed totals must not paint 2.9M/2.9M."""
    monkeypatch.setattr(ac, "lookup_catalog_context_limit", lambda _m: 1_050_000)
    monkeypatch.setattr(ac, "_load_resume_id", lambda *_a, **_k: "01a0cc84-160e-7af1-bacd-0d2a6a0c6f4e")
    monkeypatch.setattr(ac, "_resolve_model_for_agent", lambda *_a, **_k: "gpt-5.6-sol")
    monkeypatch.setattr(
        ac,
        "_codex_apply_live_or_snapshot",
        lambda **kw: (125_554, "app_server", 258_400, "app_server"),
    )
    msgs = [
        _assistant_msg(
            agent="codex",
            usage={
                "prompt_tokens": 2_860_849,
                "completion_tokens": 23_455,
                "cache_read_tokens": 2_732_928,
                "model": "gpt-5.6-sol",
            },
        )
    ]
    st = ac.get_agent_context_status(
        chat_session_id=504, agent_id="codex", cwd="C:/Projects/Cuttle", messages=msgs
    )
    assert st["token_source"] == "app_server"
    assert st["used_tokens"] == 125_554
    assert st["limit_tokens"] == 258_400
    assert st["percent"] < 55
    assert st["percent"] > 40


def test_codex_aggregate_without_snapshot_distrusts_prompt(monkeypatch):
    monkeypatch.setattr(ac, "lookup_catalog_context_limit", lambda _m: 1_050_000)
    monkeypatch.setattr(ac, "_load_resume_id", lambda *_a, **_k: None)
    monkeypatch.setattr(ac, "_resolve_model_for_agent", lambda *_a, **_k: "gpt-5.6-sol")
    monkeypatch.setattr(
        ac,
        "_codex_apply_live_or_snapshot",
        lambda **kw: (
            kw["tokens"],
            kw["token_source"],
            kw["limit"],
            kw["limit_source"],
        ),
    )
    msgs = [
        _assistant_msg(
            agent="codex",
            usage={
                "prompt_tokens": 2_860_849,
                "completion_tokens": 23_455,
                "cache_read_tokens": 2_732_928,
                "model": "gpt-5.6-sol",
            },
        )
    ]
    st = ac.get_agent_context_status(
        chat_session_id=504, agent_id="codex", cwd="C:/Projects/Cuttle", messages=msgs
    )
    assert st["token_source"] == "aggregated"
    assert st["used_tokens"] == 0
    assert st["limit_tokens"] == 1_050_000
    assert st["percent"] == 0.0


def test_compact_codex_uses_app_server(monkeypatch):
    monkeypatch.setattr(ac, "_load_resume_id", lambda *_a, **_k: "01aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    monkeypatch.setattr(
        ac,
        "get_agent_context_status",
        lambda **_k: {
            "success": True,
            "agent_id": "codex",
            "used_tokens": 50_000,
            "limit_tokens": 272_000,
            "percent": 18.4,
        },
    )
    called = {}

    def _fake_compact(tid, *, cwd=None, timeout=180.0):
        called["tid"] = tid
        called["cwd"] = cwd
        return {
            "success": True,
            "agent_id": "codex",
            "method": "app-server-compact",
            "session_id": tid,
            "token_usage": {
                "context_tokens": 9_000,
                "prompt_tokens": 9_000,
                "model_context_window": 272_000,
            },
        }

    monkeypatch.setattr(
        "scripts.utilities.codex_app_server.compact_codex_thread",
        _fake_compact,
    )
    result = ac.compact_agent_context(
        chat_session_id=42, agent_id="codex", cwd="C:/Projects/Cuttle"
    )
    assert result["success"] is True
    assert result["method"] == "app-server-compact"
    assert called["tid"] == "01aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert result["status"]["used_tokens"] == 9_000
    assert result["status"]["token_source"] == "app_server"


# ---------------------------------------------------------------------------
# Unsupported harness CLIs — usage exists elsewhere, gauge API must refuse
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "agent_id",
    sorted(USAGE_BUT_NO_GAUGE) or ["__none__"],
    ids=lambda a: a,
)
def test_unsupported_cli_rejected_even_with_usage_blob(agent_id, monkeypatch):
    if agent_id == "__none__":
        pytest.skip("all harness agents now have gauge support")
    assert agent_id in HARNESS_AGENTS
    monkeypatch.setattr(ac, "lookup_catalog_context_limit", lambda _m: 200_000)
    msgs = [
        _assistant_msg(
            agent=agent_id,
            usage={"prompt_tokens": 12_345, "completion_tokens": 10, "model": "x"},
        )
    ]
    st = ac.get_agent_context_status(
        chat_session_id=1, agent_id=agent_id, cwd="C:/Projects/Cuttle", messages=msgs
    )
    assert st.get("supported") is False
    assert st.get("success") is False
    # Fill helpers must not invent a ring for these agents via normalize miss.
    assert ac.normalize_agent_id(agent_id) is None


# ---------------------------------------------------------------------------
# Compact wiring (mocked — never runs a real CLI)
# ---------------------------------------------------------------------------


COMPACTABLE = set(ac._COMPACTABLE_AGENTS)


@pytest.mark.parametrize("agent_id", sorted(COMPACTABLE))
def test_compact_supported_agents_mocked(agent_id, monkeypatch):
    monkeypatch.setattr(ac, "_load_resume_id", lambda *_a, **_k: f"{agent_id}-resume")
    monkeypatch.setattr(
        ac,
        "get_agent_context_status",
        lambda **_k: {"success": True, "agent_id": agent_id, "used_tokens": 0},
    )
    calls = {"n": 0}

    def _ok(*_a, **_k):
        calls["n"] += 1
        return {"success": True, "agent_id": agent_id, "method": "mock"}

    monkeypatch.setattr(ac, "_compact_cursor", _ok)
    monkeypatch.setattr(ac, "_compact_muse", _ok)
    monkeypatch.setattr(ac, "_compact_opencode", _ok)
    monkeypatch.setattr(ac, "_compact_codex", _ok)
    monkeypatch.setattr(ac, "_compact_hermes", _ok)
    monkeypatch.setattr(ac, "_compact_claude", _ok)

    result = ac.compact_agent_context(
        chat_session_id=42, agent_id=agent_id, cwd="C:/Projects/Cuttle"
    )
    assert result.get("success") is True
    assert calls["n"] == 1
    assert result.get("resume_id") == f"{agent_id}-resume"


@pytest.mark.parametrize("agent_id", ["deepseek", "antigravity"])
def test_compact_unavailable_agents_rejected(agent_id):
    result = ac.compact_agent_context(
        chat_session_id=42, agent_id=agent_id, cwd="C:/Projects/Cuttle"
    )
    assert result.get("success") is False
    err = (result.get("error") or "").lower()
    assert "unsupported" in err or "no headless compact" in err or "compact" in err


def test_antigravity_gauge_without_compact(monkeypatch):
    monkeypatch.setattr(ac, "lookup_catalog_context_limit", lambda _m: None)
    monkeypatch.setattr(ac, "_load_resume_id", lambda *_a, **_k: "agy-conv-1")
    monkeypatch.setattr(ac, "_resolve_model_for_agent", lambda *_a, **_k: "gemini-flash")
    msgs = [
        _assistant_msg(
            agent="antigravity",
            usage={
                "prompt_tokens": 40_000,
                "completion_tokens": 200,
                "context_tokens": 40_000,
                "model": "gemini-flash",
            },
        )
    ]
    st = ac.get_agent_context_status(
        chat_session_id=1, agent_id="antigravity", cwd="C:/Projects/Cuttle", messages=msgs
    )
    assert st["success"] is True
    assert st["used_tokens"] == 40_000
    assert st["compact_available"] is False
    result = ac.compact_agent_context(
        chat_session_id=1, agent_id="antigravity", cwd="C:/Projects/Cuttle"
    )
    assert result["success"] is False
    assert "compact" in (result.get("error") or "").lower()


def test_hermes_compact_available(monkeypatch):
    monkeypatch.setattr(ac, "lookup_catalog_context_limit", lambda _m: None)
    monkeypatch.setattr(ac, "_load_resume_id", lambda *_a, **_k: "hermes-sess")
    monkeypatch.setattr(ac, "_resolve_model_for_agent", lambda *_a, **_k: "qwen3-coder")
    st = ac.get_agent_context_status(
        chat_session_id=1, agent_id="hermes", cwd="C:/Projects/Cuttle", messages=[]
    )
    assert st["success"] is True
    assert st["compact_available"] is True
    assert st["used_tokens"] == 0


def test_compact_no_resume_does_not_call_cli(monkeypatch):
    monkeypatch.setattr(ac, "_load_resume_id", lambda *_a, **_k: None)
    called = {"cursor": 0, "muse": 0, "opencode": 0, "codex": 0, "hermes": 0}
    monkeypatch.setattr(
        ac, "_compact_cursor", lambda *_a, **_k: called.__setitem__("cursor", 1)
    )
    monkeypatch.setattr(
        ac, "_compact_muse", lambda *_a, **_k: called.__setitem__("muse", 1)
    )
    monkeypatch.setattr(
        ac, "_compact_opencode", lambda *_a, **_k: called.__setitem__("opencode", 1)
    )
    monkeypatch.setattr(
        ac, "_compact_codex", lambda *_a, **_k: called.__setitem__("codex", 1)
    )
    monkeypatch.setattr(
        ac, "_compact_hermes", lambda *_a, **_k: called.__setitem__("hermes", 1)
    )
    for aid in ("cursor", "muse", "opencode", "codex", "hermes"):
        r = ac.compact_agent_context(
            chat_session_id=7, agent_id=aid, cwd="C:/Projects/Cuttle"
        )
        assert r.get("success") is False
        assert "No" in (r.get("error") or "") and "compact" in (r.get("error") or "")
    assert called == {"cursor": 0, "muse": 0, "opencode": 0, "codex": 0, "hermes": 0}


# ---------------------------------------------------------------------------
# Cursor billing helpers (shared by stream peak + status)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "usage,expected_agg",
    [
        (
            {
                "inputTokens": 209_952,
                "cacheReadTokens": 4_127_872,
                "cacheWriteTokens": 0,
            },
            True,
        ),
        (
            {
                "inputTokens": 109_965,
                "cacheReadTokens": 921_344,
                "context_tokens": 1_031_309,
            },
            True,
        ),
        (
            {
                "inputTokens": 12_000,
                "cacheReadTokens": 3_000,
                "context_tokens": 15_000,
            },
            False,
        ),
        ({"prompt_tokens": 8_000, "completion_tokens": 20}, False),
    ],
)
def test_cursor_usage_looks_aggregated_cases(usage, expected_agg):
    assert cursor_usage_looks_aggregated(usage) is expected_agg

"""Chat metadata/badge helpers + default cwd (P4-3 baseline).

Pins the current ``api.web_chat_api`` helper contract BEFORE the
self-contained pieces move to genuine owners (``api.chat_metadata``,
``managers.project_manager``): user-badge chips with send-time
identity, usage normalization incl. Cursor cache merge, default-cwd
fallback and its kernel equivalence. Post-move the same assertions
must hold through the new owners and the compatibility aliases.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import api.web_chat_api as wca


def test_user_badge_cursor_with_identity():
    meta = wca._user_badge_metadata(
        "/cursor do things",
        "s1",
        identity={"agent": "cursor", "model": "sonnet", "effort": "high"},
    )
    assert meta is not None
    chips = meta["slash_command"]["chips"]
    assert len(chips) == 1 and chips[0]["category"] == "cursor"
    assert chips[0]["label"].startswith("Cursor - ")
    assert "/cursor" in chips[0]["meta"]


def test_user_badge_non_slash_is_none():
    assert wca._user_badge_metadata("just chatting", "s1") is None
    assert wca._user_badge_metadata("", "s1") is None


def test_user_badge_mismatched_identity_falls_back():
    meta = wca._user_badge_metadata(
        "/codex run it",
        "s1",
        identity={"agent": "cursor", "model": "x", "effort": "y"},
    )
    assert meta is not None
    label = meta["slash_command"]["chips"][0]["label"]
    assert "Codex" in label
    assert " · y" not in label


def test_usage_meta_merges_cursor_cache_fields():
    res = {
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        "cursor_run": {"usage": {"cacheReadTokens": 99, "prompt_tokens": 10}},
    }
    meta = wca._usage_meta_from_assistant_result(res)
    assert meta is not None
    assert meta.get("cache_read_tokens") == 99
    assert wca._usage_meta_from_assistant_result("nope") is None
    assert wca._usage_meta_from_assistant_result({}) is None


def test_default_cwd_fallback_and_kernel_equivalence():
    from api.agent_harness.kernel import _fallback_chat_cwd

    direct = wca._default_chat_cwd()
    assert isinstance(direct, str) and direct.strip()
    assert _fallback_chat_cwd() == direct


def test_metadata_owner_has_no_monolith_import():
    from pathlib import Path

    import api.chat_metadata as svc

    src = Path(svc.__file__).read_text(encoding="utf-8")
    assert "web_chat_api" not in src
    assert "chat_delivery" not in src


def test_wrapper_aliases_are_the_owned_functions():
    import api.chat_metadata as svc

    assert wca._user_badge_metadata is svc.user_badge_metadata
    assert wca._usage_meta_from_assistant_result is svc.usage_meta_from_assistant_result
    assert wca._muse_model_label is svc.muse_model_label


def test_default_cwd_owner_explicit_inputs():
    from managers.project_manager import REPO_ROOT
    from managers.project_manager import default_chat_cwd
    from managers.project_manager import project_manager as pm

    assert default_chat_cwd(pm, REPO_ROOT) == wca._default_chat_cwd()
    assert Path(default_chat_cwd(None, "/tmp")) == Path("/tmp")

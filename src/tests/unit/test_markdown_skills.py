"""Smoke tests for global SKILL.md discovery (.cuttle_global/skills)."""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def test_list_markdown_includes_cursor_workspace_skills():
    from api.markdown_skills import list_markdown_skills

    refs = {s["ref"] for s in list_markdown_skills()}
    assert any(r.startswith("global/") for r in refs)
    assert not any(r.startswith("core/") for r in refs)


def test_get_markdown_skill_govee():
    from api.markdown_skills import get_markdown_skill

    g = get_markdown_skill("global/govee")
    assert g is not None
    assert g["source"] == "global"
    assert "Govee" in (g.get("body_markdown") or "") or "govee" in (g.get("frontmatter") or {}).get("name", "").lower()

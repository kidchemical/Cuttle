"""Smoke tests for markdown skill discovery (src/skills, .cursor/skills)."""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def test_list_markdown_includes_core_cuttle_overview():
    from api.markdown_skills import list_markdown_skills

    refs = {s["ref"] for s in list_markdown_skills()}
    assert "core/cuttle-overview" in refs


def test_get_markdown_skill_outline():
    from api.markdown_skills import get_markdown_skill

    g = get_markdown_skill("core/cuttle-overview")
    assert g is not None
    titles = [h["text"] for h in g["structure"]["headings"]]
    assert "What Cuttle is" in titles


def test_registry_structure_summary():
    from api.skills_registry import get_skill
    from api.markdown_skills import skill_structure_for_registry_entry

    sk = get_skill("oobe_welcome")
    assert sk is not None
    st = skill_structure_for_registry_entry(sk)
    assert st["type"] == "pipeline_template"
    assert st["pipeline"]["node_count"] >= 3

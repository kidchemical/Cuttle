"""Tests for ``.cuttle/personal/`` overlay resolution."""

from __future__ import annotations

from pathlib import Path


def test_resolve_prefers_personal(tmp_path: Path):
    from api.cuttle_brain.personal_overlay import resolve_cuttle_file

    cuttle = tmp_path / ".cuttle"
    (cuttle / "docs").mkdir(parents=True)
    (cuttle / "personal" / "docs").mkdir(parents=True)
    (cuttle / "docs" / "runbook.md").write_text("tracked", encoding="utf-8")
    (cuttle / "personal" / "docs" / "runbook.md").write_text("personal", encoding="utf-8")
    resolved = resolve_cuttle_file(cuttle, "docs", "runbook.md")
    assert resolved is not None
    assert resolved.read_text(encoding="utf-8") == "personal"


def test_rules_merge_personal_overrides(tmp_path: Path):
    from api.cuttle_brain.context_compiler import compile_context

    rules = tmp_path / ".cuttle" / "rules"
    rules.mkdir(parents=True)
    (rules / "01-base.md").write_text("TRACKED_RULE_TOKEN", encoding="utf-8")
    personal = tmp_path / ".cuttle" / "personal" / "rules"
    personal.mkdir(parents=True)
    (personal / "01-base.md").write_text("PERSONAL_RULE_TOKEN", encoding="utf-8")
    (personal / "02-extra.md").write_text("PERSONAL_ONLY_TOKEN", encoding="utf-8")

    compiled = compile_context(
        "hi",
        project_path=str(tmp_path),
        inject_capabilities=False,
    )
    assert "PERSONAL_RULE_TOKEN" in compiled.prompt
    assert "PERSONAL_ONLY_TOKEN" in compiled.prompt
    assert "TRACKED_RULE_TOKEN" not in compiled.prompt


def test_inventory_includes_personal_docs(tmp_path: Path):
    from api.cuttle_brain.context_compiler import project_inventory

    cuttle = tmp_path / ".cuttle"
    (cuttle / "docs").mkdir(parents=True)
    (cuttle / "personal" / "docs").mkdir(parents=True)
    (cuttle / "docs" / "a.md").write_text("a", encoding="utf-8")
    (cuttle / "personal" / "docs" / "b.md").write_text("b", encoding="utf-8")
    inv = project_inventory(str(tmp_path))
    assert "a.md" in inv["docs"]
    assert "b.md" in inv["docs"]


def test_actions_personal_wins(tmp_path: Path):
    from api.project_actions import list_project_actions

    actions = tmp_path / ".cuttle" / "actions"
    personal = tmp_path / ".cuttle" / "personal" / "actions"
    actions.mkdir(parents=True)
    personal.mkdir(parents=True)
    (actions / "demo.yaml").write_text(
        "name: demo\ntitle: Tracked\ntype: shell\nrun: echo tracked\n",
        encoding="utf-8",
    )
    (personal / "demo.yaml").write_text(
        "name: demo\ntitle: Personal\ntype: shell\nrun: echo personal\n",
        encoding="utf-8",
    )
    listed = list_project_actions(str(tmp_path))
    demo = next(a for a in listed if a["name"] == "demo")
    assert demo["title"] == "Personal"

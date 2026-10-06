"""F06: governing rule bodies must not silently truncate beyond 24 files."""

from __future__ import annotations

import os


def _disable_jev(monkeypatch):
    monkeypatch.setenv("CUTTLE_JEV_DISABLED", "1")
    for key in ("TYPESAFE_API_KEY", "OPENROUTER_API_KEY", "CUTTLE_JEV_BASE_URL"):
        monkeypatch.delenv(key, raising=False)


def test_project_rules_beyond_24_all_compile(tmp_path, monkeypatch):
    _disable_jev(monkeypatch)
    from api.cuttle_brain.context_compiler import compile_context, load_project_rules

    rules = tmp_path / ".cuttle" / "rules"
    rules.mkdir(parents=True)
    for i in range(30):
        (rules / f"{i:02d}-rule.md").write_text(f"RULE_BODY_{i:02d}", encoding="utf-8")

    loaded = load_project_rules(str(tmp_path))
    assert len(loaded) == 30

    compiled = compile_context("hi", project_path=str(tmp_path), inject_capabilities=False)
    for i in range(30):
        assert f"RULE_BODY_{i:02d}" in compiled.prompt


def test_personal_twin_and_personal_only_beyond_24(tmp_path, monkeypatch):
    _disable_jev(monkeypatch)
    from api.cuttle_brain.context_compiler import compile_context, load_project_rules

    rules = tmp_path / ".cuttle" / "rules"
    rules.mkdir(parents=True)
    for i in range(28):
        (rules / f"{i:02d}-base.md").write_text(f"TRACKED_{i:02d}", encoding="utf-8")
    personal = tmp_path / ".cuttle" / "personal" / "rules"
    personal.mkdir(parents=True)
    # Twin sorts beyond the old 24-file cutoff.
    (personal / "27-base.md").write_text("PERSONAL_TWIN_27", encoding="utf-8")
    # Personal-only file beyond the old cutoff.
    (personal / "29-extra.md").write_text("PERSONAL_ONLY_29", encoding="utf-8")

    loaded = dict(load_project_rules(str(tmp_path)))
    assert "27-base.md" in loaded
    assert "29-extra.md" in loaded
    assert "TRACKED_27" in loaded["27-base.md"]
    assert "PERSONAL_TWIN_27" in loaded["27-base.md"]
    assert loaded["27-base.md"].index("TRACKED_27") < loaded["27-base.md"].index("PERSONAL_TWIN_27")

    compiled = compile_context("hi", project_path=str(tmp_path), inject_capabilities=False)
    assert "PERSONAL_TWIN_27" in compiled.prompt
    assert "PERSONAL_ONLY_29" in compiled.prompt


def test_snapshot_and_delta_cover_beyond_24(tmp_path, monkeypatch):
    _disable_jev(monkeypatch)
    from api.cuttle_brain.context_delta import build_delta_text, compute_snapshot

    rules = tmp_path / ".cuttle" / "rules"
    rules.mkdir(parents=True)
    for i in range(30):
        (rules / f"{i:02d}-rule.md").write_text(f"V1_{i:02d}", encoding="utf-8")
    before = compute_snapshot(str(tmp_path))
    assert f"{26:02d}-rule.md" in before.project_rules

    (rules / "26-rule.md").write_text("V2_CHANGED_MARKER_26", encoding="utf-8")
    after = compute_snapshot(str(tmp_path))
    delta = build_delta_text(before, after, str(tmp_path))
    assert delta is not None
    assert "26-rule.md" in delta
    assert "V2_CHANGED_MARKER_26" in delta


def test_global_ini_safety_off_shadow_with_many_rules(tmp_path, monkeypatch):
    _disable_jev(monkeypatch)
    import api.cuttle_brain.context_compiler as compiler

    iso = tmp_path / "install"
    (iso / ".cuttle_global" / "rules").mkdir(parents=True)
    (iso / ".cuttle_global" / "rules" / "00-safety.md").write_text("SAFETY_MARKER", encoding="utf-8")
    for i in range(30):
        (iso / ".cuttle_global" / "rules" / f"{i + 1:02d}-policy.md").write_text(
            f"GLOBAL_{i + 1:02d}", encoding="utf-8"
        )
    monkeypatch.setattr(compiler, "_cuttle_install_root", lambda: iso)

    assert len(compiler.load_global_rules()) == 31

    proj = tmp_path / "proj"
    (proj / ".cuttle" / "rules").mkdir(parents=True)
    (proj / ".cuttle" / "rules" / "00-safety.md").write_text("PROJECT_SAFETY_TWIN", encoding="utf-8")

    # off: safety survives, policy drops
    (proj / ".cuttle" / "GLOBAL.ini").write_text("[global]\nrules = off\n", encoding="utf-8")
    env_off = compiler.compile_context(
        "hi", project_path=str(proj), inject_capabilities=False
    ).envelope
    assert "SAFETY_MARKER" in env_off
    assert "GLOBAL_30" not in env_off

    # shadow: project twin shadows same-basename global file, other globals survive
    (iso / ".cuttle_global" / "rules" / "10-policy.md").write_text("SHADOWED_GLOBAL", encoding="utf-8")
    (proj / ".cuttle" / "rules" / "10-policy.md").write_text("PROJECT_SHADOW", encoding="utf-8")
    (proj / ".cuttle" / "GLOBAL.ini").write_text("[global]\nrules = shadow\n", encoding="utf-8")
    env_shadow = compiler.compile_context(
        "hi", project_path=str(proj), inject_capabilities=False
    ).envelope
    assert "SAFETY_MARKER" in env_shadow
    assert "PROJECT_SHADOW" in env_shadow
    assert "GLOBAL_30" in env_shadow


def test_bounded_defaults_preserved_for_inventory(tmp_path):
    from api.cuttle_brain.personal_overlay import (
        list_merged_names,
        merge_named_files,
        read_merged_md,
    )

    docs = tmp_path / ".cuttle" / "docs"
    docs.mkdir(parents=True)
    for i in range(45):
        (docs / f"{i:02d}.md").write_text(f"DOC_{i:02d}", encoding="utf-8")

    # Inventory path stays bounded by default.
    assert len(list_merged_names(docs, ("*.md",))) == 40
    assert len(merge_named_files(docs, patterns=("*.md",))) == 40
    # Explicit None is unbounded.
    assert len(merge_named_files(docs, patterns=("*.md",), limit=None)) == 45
    assert len(read_merged_md(docs, limit=None)) == 45
    # Default markdown bound unchanged for non-rule callers.
    assert len(read_merged_md(docs)) == 24

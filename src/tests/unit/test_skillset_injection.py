"""Unit tests for markdown skillset expansion and selection."""

from api.skillset_injection import (
    build_skills_system_addon,
    expand_skillset_node_config,
    _rank_keyword,
)


def test_expand_cuttle_default():
    cfg = expand_skillset_node_config({"preset": "cuttle_default"})
    assert cfg["skillScope"] == "all"
    assert cfg["selectionMode"] == "dynamic"
    assert cfg["dynamicStrategy"] == "keyword"


def test_expand_custom_defaults():
    cfg = expand_skillset_node_config({"preset": "custom"})
    assert cfg["preset"] == "custom"
    assert cfg["skillScope"] == "all"


def test_rank_keyword_empty_prompt():
    summaries = [
        {"ref": "cursor/b", "name": "B", "description": "", "top_headings": []},
        {"ref": "cursor/a", "name": "A", "description": "", "top_headings": []},
    ]
    out = _rank_keyword("", summaries, 1)
    assert len(out) == 1
    assert out[0]["ref"] == "cursor/a"


def test_rank_keyword_prefers_overlap():
    summaries = [
        {"ref": "cursor/x", "name": "X", "description": "grocery shopping", "top_headings": []},
        {"ref": "cursor/y", "name": "Y", "description": "unrelated topic", "top_headings": []},
    ]
    out = _rank_keyword("order groceries from Instacart", summaries, 1)
    assert out[0]["ref"] == "cursor/x"


def test_build_addon_empty_configs():
    addon, meta = build_skills_system_addon("hello", [])
    assert addon == ""
    assert meta.get("blocks") == 0


def test_build_addon_with_preset():
    _addon, meta = build_skills_system_addon(
        "test prompt about nothing",
        [{"preset": "cuttle_default", "maxSkills": 2, "maxInjectChars": 50000}],
    )
    assert isinstance(meta, dict)
    assert "refs" in meta

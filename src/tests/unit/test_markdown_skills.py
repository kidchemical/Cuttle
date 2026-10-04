"""Offline markdown skill discovery, independent of shipped workflow inventory."""
from api import markdown_skills


def test_global_skill_list_get(tmp_path, monkeypatch):
    root = tmp_path / "global" / "skills"
    skill = root / "example" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: Example\ndescription: Test skill\n---\n# Example\nBody", encoding="utf-8")
    monkeypatch.setattr(markdown_skills, "GLOBAL_SKILLS_DIR", root)
    listed = markdown_skills.list_markdown_skills()
    assert [s["ref"] for s in listed] == ["global/example"]
    got = markdown_skills.get_markdown_skill(listed[0]["ref"])
    assert got["source"] == "global"
    assert got["body_markdown"] == "# Example\nBody"
    assert got["structure"]["headings"][0]["text"] == "Example"

"""Offline scope/overlay regressions using isolated configuration trees."""
from pathlib import Path

import pytest

from api import markdown_skills as skills, project_commands as commands
from api.project_actions import list_project_actions, find_project_action
from api.cuttle_brain.personal_overlay import read_cuttle_file_merged
from api.cuttle_brain.global_layers import load_global_layers
from api.jev import rank
from managers.cuttle_scaffold import ensure_cuttle_scaffold


def put(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def trees(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    global_root = tmp_path / "install" / ".cuttle_global"
    global_root.mkdir(parents=True)
    monkeypatch.setattr(skills, "GLOBAL_SKILLS_DIR", global_root / "skills")
    monkeypatch.setattr(commands, "GLOBAL_COMMANDS_DIR", global_root / "commands")
    return project, global_root


def test_personal_only_doc_and_delta(trees, monkeypatch):
    project, hub = trees
    config = project / ".cuttle"
    put(config, "personal/docs/local.md", "# Local\nprivate body")
    assert read_cuttle_file_merged(config, "docs", "local.md") == "# Local\nprivate body"
    monkeypatch.setattr("api.cuttle_brain.context_compiler._cuttle_global_config", lambda: hub)
    assert rank._read_doc_body("local.md", str(project)).endswith("private body")
    put(config, "docs/local.md", "tracked")
    body = rank._read_doc_body("local.md", str(project))
    assert body.index("tracked") < body.index("private body")


def test_commands_precedence_identity_and_disable(trees):
    project, hub = trees
    locations = [(project, ".cuttle/personal/commands", "project-personal"),
                 (project, ".cuttle/commands", "project"),
                 (project, "source/.cuttle/personal/commands", "project-nested-personal"),
                 (project, "source/.cuttle/commands", "project-nested"),
                 (hub, "personal/commands", "global-personal"),
                 (hub, "commands", "global")]
    paths = [put(root, f"{rel}/ship.md", f"---\nname: ship\naliases: [deploy]\n---\n{source}")
             for root, rel, source in locations]
    for path, (_, _, source) in zip(paths, locations):
        command = commands.find_project_command(str(project), "ship")
        assert command["source"] == source
        assert command["body"] == source
        assert commands.find_project_command(str(project), "deploy") == command
        path.unlink()
    put(hub, "commands/ship.md", "global")
    put(project, ".cuttle/personal/commands/renamed.md", "---\nname: ship\ndisabled: true\n---\n")
    assert commands.find_project_command(str(project), "ship") is None
    put(hub, "personal/commands/only.md", "personal only")
    assert commands.find_project_command(str(project), "only")["source"] == "global-personal"
    put(project, ".cuttle/personal/GLOBAL.ini", "[global]\ncommands=off\n")
    assert commands.list_project_commands(str(project)) == []


def test_skills_list_get_precedence_disable_and_isolation(trees):
    project, hub = trees
    locations = [(project, ".cuttle/personal/skills", "project-personal"),
                 (project, ".cuttle/skills", "project"),
                 (project, "source/.cuttle/personal/skills", "project-nested-personal"),
                 (project, "source/.cuttle/skills", "project-nested"),
                 (hub, "personal/skills", "global-personal"), (hub, "skills", "global")]
    paths = [put(root, f"{rel}/same/SKILL.md", f"---\nname: Test\ndescription: {scope}\n---\n# {scope}")
             for root, rel, scope in locations]
    for path, (_, _, scope) in zip(paths, locations):
        listed = skills.list_markdown_skills(str(project))
        assert len(listed) == 1
        assert listed[0]["source"] == scope
        got = skills.get_markdown_skill(listed[0]["ref"], str(project))
        assert got["body_markdown"] == f"# {scope}"
        assert rank._read_skill_body(listed[0]["ref"], str(project)) == f"# {scope}"
        assert rank._skill_summaries(str(project))[0]["name"] == listed[0]["ref"]
        path.unlink()
    put(hub, "skills/same/SKILL.md", "global")
    put(project, ".cuttle/personal/skills/same/SKILL.md", "---\ndisabled: true\n---\n")
    assert skills.list_markdown_skills(str(project)) == []
    assert skills.get_markdown_skill("global/same", str(project)) is None
    other = project.parent / "other"
    other.mkdir()
    assert skills.get_markdown_skill("global/same", str(other))["body_markdown"] == "global"
    put(project, ".cuttle/personal/skills/local/SKILL.md", "personal only")
    assert skills.get_markdown_skill("project-personal/local", str(other)) is None
    put(project, ".cuttle/personal/GLOBAL.ini", "[global]\nskills=off\n")
    assert not load_global_layers(str(project)).skills
    assert [s["id"] for s in skills.list_markdown_skills(str(project))] == ["local"]
    for ref in ("global/../same", "global/same/SKILL.md", "project-personal/../../local"):
        assert skills.get_markdown_skill(ref, str(project)) is None


def test_nested_personal_actions_and_disable(trees, monkeypatch):
    from api import project_actions
    project, _ = trees
    # The action compatibility resolver also walks parent checkouts. Keep
    # this nested-layout fixture independent of the real checkout's actions.
    real_dirs = project_actions._actions_dirs_for_project
    monkeypatch.setattr(project_actions, "_actions_dirs_for_project",
                        lambda path, **kw: [(directory, owner) for directory, owner
                                           in real_dirs(path, **kw) if owner == project])
    put(project, "source/.cuttle/actions/ship.yaml", "name: ship\nrun: tracked")
    put(project, "source/.cuttle/personal/actions/ship.yaml", "name: ship\nrun: personal")
    put(project, "source/.cuttle/personal/actions/only.yaml", "name: only")
    action = find_project_action(str(project), "ship", include_global=False)
    assert action["run"] == "personal"
    assert action["source"] == "project-nested-personal"
    assert find_project_action(str(project), "only", include_global=False)
    put(project, ".cuttle/personal/actions/disabled.yaml", "name: ship\ndisabled: true")
    assert {a["name"] for a in list_project_actions(str(project), include_global=False)} == {"only"}


def test_scaffold_skills(trees):
    project, _ = trees
    ensure_cuttle_scaffold(project)
    assert (project / ".cuttle/skills").is_dir()
    assert (project / ".cuttle/personal/skills").is_dir()
    assert load_global_layers(str(project)).skills


def test_disabled_action_blocks_registered_project_fallback(trees, monkeypatch):
    from api.project_actions import find_project_action_resolved
    project, _ = trees
    put(project, ".cuttle/personal/actions/no.yaml", "name: ship\ndisabled: true")
    # Resolution must return before consulting real project state.
    monkeypatch.setattr("api.project_actions.find_project_action", lambda *a, **k: pytest.fail("fallback reached"))
    assert find_project_action_resolved(str(project), "ship") == (None, str(project))


def test_skill_invalid_encoding_and_escape_are_ignored(trees):
    project, hub = trees
    outside = put(project, "outside/SKILL.md", "outside")
    directory = hub / "skills"
    directory.mkdir()
    (directory / "escape").symlink_to(outside.parent, target_is_directory=True)
    bad = put(hub, "skills/bad/SKILL.md", "")
    bad.write_bytes(b"\xff")
    put(hub, "skills/valid/SKILL.md", "---\ndisabled: 'true'\n---\nvalid")
    assert [s["id"] for s in skills.list_markdown_skills()] == ["valid"]


def test_action_discovery_survives_pre_restart_overlay_module(trees, monkeypatch):
    """A new action loader can run while Flask still caches the old overlay."""
    from api.cuttle_brain import personal_overlay
    project, _ = trees
    put(project, '.cuttle/actions/restart.yaml', 'name: flask.restart\nrun: restart')
    monkeypatch.delattr(personal_overlay, 'unit_disabled')
    assert find_project_action(str(project), 'flask.restart', include_global=False)['run'] == 'restart'

"""Optional guidance needs an explicit project opt-in, not runtime defaults."""
from api.cuttle_brain import context_compiler as cc
from api.cuttle_brain.global_layers import load_global_layers
from api.cuttle_ui_capabilities import cuttle_ui_capabilities_block
from api.jev import rank
from api import markdown_skills


def put(root, relative, text):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_global_runbook_list_read_and_capabilities_share_gate(tmp_path, monkeypatch):
    project = tmp_path / 'project'
    project.mkdir()
    hub = tmp_path / 'hub'
    put(hub, 'docs/gitea.md', '# Gitea\nForge operations')
    put(hub, 'docs/core.md', '# Core\nShared operations')
    monkeypatch.setattr(cc, '_cuttle_global_config', lambda: hub)
    monkeypatch.setenv('GITEA_BASE_URL', 'http://127.0.0.1:3000')
    for enabled in (False, True):
        put(project, '.cuttle/GLOBAL.ini', '[integrations]\ngitea=' + ('on' if enabled else 'off') + '\n')
        assert ('doc:gitea.md' in {item['id'] for item in rank._doc_summaries(str(project), {})}) is enabled
        assert bool(rank._read_doc_body('gitea.md', str(project))) is enabled
        assert ('gitea.md' in cuttle_ui_capabilities_block(project_path=str(project))) is enabled
        runtime = cc._runtime_block(project_path=str(project), inventory={}, handoff=None, include_chat_store_hint=False)
        assert ('gitea.md' in runtime) is enabled
    put(project, '.cuttle/GLOBAL.ini', '[global]\ndocs=off\n[integrations]\ngitea=on\n')
    assert 'gitea.md' not in cuttle_ui_capabilities_block(project_path=str(project))
    assert rank._read_doc_body('gitea.md', str(project)) == ''
    # A project's own installed guidance remains owned by that project.
    put(project, '.cuttle/docs/gitea.md', '# My forge\nproject-owned')
    assert rank._read_doc_body('gitea.md', str(project)).endswith('project-owned')


def test_personal_opt_in_and_skill_scope_do_not_leak(tmp_path, monkeypatch):
    project = tmp_path / 'project'
    project.mkdir()
    guest = tmp_path / 'guest'
    guest.mkdir()
    hub = tmp_path / 'hub'
    put(hub, 'skills/forge/SKILL.md', '---\nintegration: gitea\n---\nForge body')
    monkeypatch.setattr(markdown_skills, 'GLOBAL_SKILLS_DIR', hub / 'skills')
    assert markdown_skills.list_markdown_skills(str(project)) == []
    put(project, '.cuttle/personal/GLOBAL.ini', '[integrations]\ngitea=on\n')
    assert load_global_layers(str(project)).integrations == frozenset({'gitea'})
    assert markdown_skills.get_markdown_skill('global/forge', str(project))['body_markdown'] == 'Forge body'
    assert markdown_skills.list_markdown_skills(str(guest)) == []
    assert markdown_skills.get_markdown_skill('global/forge', str(guest)) is None

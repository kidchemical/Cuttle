"""Skills are discoverable in fresh/resumed context without optional ranking."""
from api import markdown_skills
from api.cuttle_brain import context_compiler as cc, context_delta as cd


def put(root, name, desc, extra=''):
    path = root / 'skills' / name / 'SKILL.md'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'---\ndescription: {desc}\n{extra}---\nPrivate skill body', encoding="utf-8")
    return path


def test_inventory_uses_owner_precedence_and_policy(tmp_path, monkeypatch):
    global_root = tmp_path / 'global'
    monkeypatch.setattr(markdown_skills, 'GLOBAL_SKILLS_DIR', global_root / 'skills')
    monkeypatch.setenv('CUTTLE_JEV_RANK_CONTEXT', '0')
    put(global_root, 'same', 'Global description')
    project = tmp_path / 'project'
    cuttle = project / '.cuttle'
    put(cuttle, 'same', 'Project description')
    personal = put(cuttle / 'personal', 'same', 'Personal description')
    put(global_root, 'other', 'Shared description')
    put(cuttle, 'disabled', 'Hidden', 'disabled: true\n')
    compiled = cc.compile_context('hello', project_path=str(project), inject_capabilities=False)
    assert str(personal) in compiled.envelope
    assert 'Personal description' in compiled.envelope
    assert 'Shared description' in compiled.envelope
    assert 'Global description' not in compiled.envelope
    assert 'Private skill body' not in compiled.envelope
    assert 'Hidden' not in compiled.envelope
    cd.record_injected_snapshot('chat', 'agent', str(project))
    put(cuttle, 'new', 'New capability')
    delta = cd.build_resume_delta('chat', 'agent', str(project))
    assert 'New capability' in delta
    (cuttle / 'GLOBAL.ini').write_text('[global]\nskills=off\n', encoding="utf-8")
    inventory = cc.skill_inventory(str(project))
    assert any('Personal description' in item for item in inventory)
    assert not any('Shared description' in item for item in inventory)

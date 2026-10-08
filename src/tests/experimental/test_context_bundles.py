"""Feature-owned catalogs, live invocation gates and resume withdrawal."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import pytest

from api.experimental import context_bundles as bundles
from api.experimental import flags
from api.cuttle_brain import context_compiler as compiler, context_delta as delta
from api.markdown_skills import list_markdown_skills, get_markdown_skill
from api.project_commands import list_project_commands, build_agent_prompt, run_project_command_shell
from api.project_actions import list_project_actions, encode_inline_action_payload, execute_inline_action
from api.jev.rank import _doc_summaries, _read_doc_body


def put(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    root = tmp_path / 'install' / '.cuttle_global'
    project = tmp_path / 'guest'
    (project / '.cuttle').mkdir(parents=True)
    put(root / 'rules' / '00-safety.md', 'CORE SAFETY')
    put(root / 'rules' / 'tasks.md', 'CORE TASKS')
    monkeypatch.setattr(compiler, '_cuttle_global_config', lambda: root)
    monkeypatch.setattr('api.markdown_skills.GLOBAL_SKILLS_DIR', root / 'skills')
    monkeypatch.setattr('api.project_commands.GLOBAL_COMMANDS_DIR', root / 'commands')
    monkeypatch.setattr('core.runtime_paths.personal_dir', lambda: root.parent / 'personal')
    monkeypatch.setattr('api.project_actions.personal_dir', lambda: root.parent / 'personal')
    monkeypatch.setattr('api.jev.config.load_jev_config', lambda: SimpleNamespace(rank_context=False))
    monkeypatch.delenv('CUTTLE_EXPERIMENTAL', raising=False)
    enabled = {}
    monkeypatch.setattr(flags, '_stored_flags', lambda: dict(enabled))
    monkeypatch.setattr(flags, 'FLAG_SPECS', {
        'one': flags.FlagSpec('one', 'One', '', context_bundle=True),
        'two': flags.FlagSpec('two', 'Two', '', default=True, context_bundle=True),
        'unowned': flags.FlagSpec('unowned', 'No bundle', '', default=True),
    })
    for ident in ('one', 'two', 'unknown', 'unowned'):
        feature = root / 'features' / ident
        put(feature / 'rules' / 'ops.md', f'{ident.upper()} RULE')
        put(feature / 'docs' / 'guide.md', f'# {ident.upper()} DOC\nrun it')
        put(feature / 'skills' / 'worker' / 'SKILL.md', f'---\nname: {ident}\ndescription: {ident.upper()} SKILL\n---\nbody')
        put(feature / 'commands' / 'run.md', f'---\nname: run-{ident}\nexecute: shell\nrun: echo fake\n---\n{ident.upper()} COMMAND')
        put(feature / 'actions' / 'run.yaml', f'name: {ident}.run\ntype: shell\nrun: echo fake\n')
    return root, str(project), enabled


@pytest.mark.parametrize('prompt', ['hello', 'fix a coding bug', 'create usage meters'])
def test_off_on_off_and_independent_features(catalog, prompt):
    root, project, enabled = catalog
    compiled = compiler.compile_context(prompt, project_path=project).envelope
    assert 'CORE TASKS' in compiled and 'CORE SAFETY' in compiled
    assert 'ONE RULE' not in compiled and 'ONE SKILL' not in compiled
    assert 'TWO RULE' in compiled and 'TWO SKILL' in compiled
    assert not bundles.read_doc('feature/one/docs/guide.md', project)
    assert not get_markdown_skill('feature/one/worker', project)
    enabled['one'] = True
    compiled = compiler.compile_context(prompt, project_path=project).envelope
    assert 'ONE RULE' in compiled and 'ONE SKILL' in compiled
    assert 'feature/one/docs/guide.md' in compiled
    assert 'feature/one/run-one' in compiled and 'feature/one/one.run' in compiled
    assert 'UNKNOWN RULE' not in compiled and 'UNOWNED RULE' not in compiled
    assert get_markdown_skill('feature/one/worker', project)
    assert 'ONE DOC' in _read_doc_body('feature/one/docs/guide.md', project)
    enabled['one'] = False
    assert not bundles.read_doc('feature/one/docs/guide.md', project)
    assert not any(s['source'] == 'feature/one' for s in list_markdown_skills(project))
    assert not any(d['name'].startswith('feature/one/') for d in _doc_summaries(project, compiler.project_inventory(project)))


def test_kill_switch_and_global_policy(catalog, monkeypatch):
    root, project, enabled = catalog
    enabled['one'] = True
    monkeypatch.setenv('CUTTLE_EXPERIMENTAL', '0')
    assert not bundles.roots()
    assert 'TWO RULE' not in compiler.compile_context('hello', project_path=project).envelope
    monkeypatch.delenv('CUTTLE_EXPERIMENTAL')
    put(Path(project) / '.cuttle' / 'GLOBAL.ini', '[global]\nrules=off\ndocs=off\nskills=off\ncommands=off\nactions=off\n')
    compiled = compiler.compile_context('hello', project_path=project).envelope
    assert 'CORE SAFETY' in compiled and 'ONE RULE' not in compiled and 'TWO RULE' not in compiled
    assert not bundles.markdown('docs', project)
    assert not list_markdown_skills(project)
    assert not list_project_commands(project)
    assert not list_project_actions(project, include_global=False)
    assert not _read_doc_body('feature/one/docs/guide.md', project)


def test_personal_append_and_structured_replacement(catalog):
    root, project, enabled = catalog
    enabled['one'] = True
    from api.cuttle_brain.personal_overlay import personal_root
    local = personal_root(root) / 'features' / 'one'
    put(local / 'rules' / 'ops.md', 'PERSONAL RULE')
    put(local / 'docs' / 'guide.md', 'PERSONAL DOC')
    put(local / 'skills' / 'worker' / 'SKILL.md', '---\ndescription: PERSONAL SKILL\n---\nLOCAL BODY')
    put(local / 'commands' / 'run.md', '---\nname: run-one\n---\nLOCAL COMMAND')
    assert 'ONE RULE' in dict(bundles.markdown('rules', project))['feature/one/rules/ops.md']
    assert 'PERSONAL RULE' in compiler.compile_context('hello', project_path=project).envelope
    assert 'ONE DOC' in bundles.read_doc('feature/one/docs/guide.md', project)
    assert 'PERSONAL DOC' in bundles.read_doc('feature/one/docs/guide.md', project)
    assert get_markdown_skill('feature/one/worker', project)['body_markdown'] == 'LOCAL BODY'
    assert next(c for c in list_project_commands(project) if c['name'] == 'run-one')['body'] == 'LOCAL COMMAND'
    enabled['one'] = False
    assert not bundles.read_doc('feature/one/docs/guide.md', project)
    assert not get_markdown_skill('feature/one/worker', project)


def test_old_objects_and_persisted_confirm_recheck_live_gate(catalog):
    root, project, enabled = catalog
    enabled['one'] = True
    command = next(c for c in list_project_commands(project) if c['name'] == 'run-one')
    signed = encode_inline_action_payload(action_name='one.run', project_path=project, params={}, session_id='1')
    enabled['one'] = False
    with patch('api.project_actions._execute_shell') as run:
        assert not execute_inline_action(signed, session_id='1')['success']
        run.assert_not_called()
    with pytest.raises(ValueError, match='unavailable'):
        build_agent_prompt(command)
    with patch('api.project_commands.subprocess.Popen') as run:
        assert not run_project_command_shell(command)['success']
        run.assert_not_called()


def test_resume_toggle_content_and_delivery_race(catalog, monkeypatch):
    root, project, enabled = catalog
    previous = delta.compute_snapshot(project)
    enabled['one'] = True
    current = delta.compute_snapshot(project)
    text = delta.build_delta_text(previous, current, project)
    assert 'ONE RULE' in text and 'Enabled: `feature/one`' in text
    put(root / 'features' / 'one' / 'docs' / 'guide.md', '# EDITED')
    updated = delta.compute_snapshot(project)
    assert updated != current
    assert 'Updated feature resource' in delta.build_delta_text(current, updated, project)
    enabled['one'] = False
    text = delta.build_delta_text(updated, delta.compute_snapshot(project), project)
    assert 'Withdraw `feature/one`' in text and 'ONE RULE' not in text
    monkeypatch.setattr(delta, 'load_injected_snapshot', lambda *a: previous)
    enabled['one'] = True
    formatter = delta.build_delta_text
    def race(*args):
        enabled['one'] = False
        return formatter(*args)
    monkeypatch.setattr(delta, 'build_delta_text', race)
    with pytest.raises(delta.UnstablePreparationError):
        delta.prepare_resume_delta('1', 'codex', project)


def test_collision_and_symlink_escape(catalog):
    root, project, enabled = catalog
    enabled['one'] = True
    put(root / 'features' / 'two' / 'actions' / 'run.yaml', 'name: one.run\ntype: shell\nrun: echo fake\n')
    put(root / 'features' / 'two' / 'commands' / 'run.md', '---\nname: run-one\n---\nconflict')
    assert not any(a['name'] == 'one.run' for a in list_project_actions(project))
    assert not any(c['name'] == 'run-one' for c in list_project_commands(project))
    external = root.parent / 'external.md'
    put(external, 'ESCAPED')
    (root / 'features' / 'one' / 'docs' / 'escape.md').symlink_to(external)
    assert not bundles.read_doc('feature/one/docs/escape.md', project)
    assert not _read_doc_body('../external.md', project)


def test_one_captured_view_for_full_compile(catalog, monkeypatch):
    root, project, enabled = catalog
    enabled['one'] = True
    runtime = compiler._runtime_block
    def race(**kwargs):
        enabled['one'] = False
        return runtime(**kwargs)
    monkeypatch.setattr(compiler, '_runtime_block', race)
    compiled = compiler.compile_context('hello', project_path=project).envelope
    assert 'ONE RULE' in compiled and 'ONE SKILL' in compiled
    assert not bundles.read_doc('feature/one/docs/guide.md', project)


def test_shadow_and_recipe_alias_conflict(catalog):
    root, project, enabled = catalog
    enabled['one'] = True
    put(Path(project) / '.cuttle' / 'rules' / 'ops.md', 'PROJECT OPS')
    put(Path(project) / '.cuttle' / 'GLOBAL.ini', '[global]\nrules=shadow\n')
    compiled = compiler.compile_context('hello', project_path=project).envelope
    assert 'PROJECT OPS' in compiled and 'CORE SAFETY' in compiled
    assert 'ONE RULE' not in compiled and 'TWO RULE' not in compiled
    put(root / 'features' / 'two' / 'commands' / 'run.md', '---\nname: different\naliases: [run-one]\n---\nconflict')
    assert not any(c.get('feature_id') for c in list_project_commands(project))


def test_shipped_default_off_guidance_and_tasks(monkeypatch):
    monkeypatch.setenv('CUTTLE_EXPERIMENTAL', '0')
    compiled = compiler.compile_context('hello', project_path=None).envelope
    assert 'Tasks gizmos' in compiled
    assert 'feature/gizmos/' not in compiled and 'feature/progress_grid/' not in compiled
    assert 'feature/render_result_attachments/' not in compiled
    assert 'python -m api.gizmos create usage_meter' not in bundles.read_doc('feature/gizmos/docs/usage-meters.md')
    assert 'Before reporting work tracked in a Tasks gizmo complete' in compiled


def test_optional_judge_cannot_select_a_disabled_bundle(catalog):
    from api.jev.client import FakeJevClient
    from api.jev.rank import rank_context
    root, project, enabled = catalog
    captured = {}
    def choose(state, questions):
        captured['questions'] = questions
        return {'needs_extra':{'type':'noul','noul':0.9}, 'pick':{'type':'choice',
            'choice':'doc:feature/one/docs/guide.md','confidence':0.9,
            'probabilities':{'doc:feature/one/docs/guide.md':1.0}}}
    fake = FakeJevClient(handler=choose)
    result = rank_context('run one', project_path=project, client=fake)
    assert result['items'] == []
    assert 'feature/one/' not in str(captured['questions'])
    enabled['one'] = True
    result = rank_context('run one', project_path=project, client=fake)
    assert len(result['items']) == 1 and 'ONE DOC' in result['items'][0]['body']

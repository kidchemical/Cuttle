"""Batch contract validation and configured-helper integration."""
import json

import pytest

from api.commit_batch import collect_batch_context, suggest_batch, validate_plan


@pytest.mark.parametrize('groups', [
    [], [{'message': 'Fix A', 'files': ['a']}],
    [{'message': 'Fix A', 'files': ['a', 'b', 'other']}],
    [{'message': 'Fix A', 'files': ['a', 'b', 'a']}],
    [{'message': '', 'files': ['a', 'b']}],
    [{'message': 'Fix A', 'files': '../outside'}],
])
def test_rejects_incomplete_or_unsafe_partitions(groups):
    with pytest.raises(ValueError):
        validate_plan(json.dumps({'commits': groups}), ['a', 'b'])


def test_uses_commit_helper_and_preserves_file_groups(monkeypatch):
    calls = []
    groups = [{'message': 'Fix login', 'files': ['login.py', 'test_login.py']},
              {'message': 'Update guide', 'files': ['guide.md']}]
    def complete(**kwargs):
        calls.append(kwargs)
        return '```json\n' + json.dumps({'commits': groups}) + '\n```'
    monkeypatch.setattr('api.llm_complete.complete', complete)
    context = {'files': [{'path': p} for g in groups for p in g['files']]}
    assert suggest_batch(context)['commits'] == groups
    assert calls[0]['task'] == 'commits'
    assert 'test_login.py' in calls[0]['user']


def test_unavailable_helper_fails_without_inventing_groups(monkeypatch):
    monkeypatch.setattr('api.llm_complete.complete', lambda **kw: None)
    with pytest.raises(ValueError, match='no commits created'):
        suggest_batch({'files': [{'path': 'a'}]})


@pytest.mark.parametrize('body,paths,expected', [
    ({}, ['a'], ['a']),
    ({'all_pending': True}, [], ['a', 'b', 'unlisted']),
    ({'include_unlisted': True, 'shown_files': ['a', 'b']}, ['a'], ['a', 'unlisted']),
])
def test_selection_matches_commit_scope(monkeypatch, body, paths, expected):
    import scripts.utilities.git_pending_changes as git
    monkeypatch.setattr(git, 'collect_pending_changes', lambda *a, **kw: {
        'files': [{'path': p} for p in ['a', 'b', 'unlisted']]})
    monkeypatch.setattr(git, 'collect_commit_suggest_context', lambda cwd, **kw: {'files': [{'path': p} for p in kw['paths']]})
    assert [f['path'] for f in collect_batch_context('/repo', body, paths)['files']] == expected


def test_large_selection_does_not_silently_drop_files(monkeypatch):
    import scripts.utilities.git_pending_changes as git
    files = [{'path': f'{i}.py'} for i in range(100)]
    monkeypatch.setattr(git, 'collect_pending_changes', lambda *a, **kw: {'files': files})
    def context(cwd, *, paths, max_files):
        assert max_files == len(paths) == 100
        return {'files': files}
    monkeypatch.setattr(git, 'collect_commit_suggest_context', context)
    assert collect_batch_context('/repo', {'all_pending': True}, None)['files'] == files


@pytest.mark.parametrize('batch', [False, True])
def test_suggestion_route_dispatches_without_git_writes(monkeypatch, batch):
    from flask import Flask
    from types import SimpleNamespace
    from api import git_routes, commit_batch, commit_message_suggester
    import scripts.utilities.git_pending_changes as git

    monkeypatch.setattr(git_routes, 'project_manager', SimpleNamespace(
        get_projects=lambda: [], get_current_project=lambda: None))
    monkeypatch.setattr(git, 'resolve_allowed_project_cwd', lambda *a: ('/repo', None, None))
    monkeypatch.setattr(git, 'resolve_allowed_repo_root', lambda *a: '/repo')
    context = {'files': [{'path': 'a'}]}
    monkeypatch.setattr(git, 'collect_commit_suggest_context', lambda *a, **kw: context)
    monkeypatch.setattr(commit_batch, 'collect_batch_context', lambda *a: context)
    monkeypatch.setattr(commit_message_suggester, 'load_session_user_prompts', lambda *a: [])
    monkeypatch.setattr(commit_message_suggester, 'suggest_commit_message', lambda *a, **kw: {'message': 'Fix A'})
    groups = [{'message': 'Fix A', 'files': ['a']}]
    monkeypatch.setattr(commit_batch, 'suggest_batch', lambda *a, **kw: {'schema_version': 1, 'commits': groups})
    def no_write(*a, **kw):
        pytest.fail('Suggestion must not commit')
    monkeypatch.setattr(git, 'commit_pending_changes', no_write)
    app = Flask(__name__)
    with app.test_request_context('/api/git/suggest-commit-message', method='POST', json={'path': '/repo', 'batch': batch}):
        response = git_routes.git_suggest_commit_message.__wrapped__()
    data = response.get_json()
    assert data['success'] is True
    if batch:
        assert data['commits'] == groups
    else:
        assert data['message'] == 'Fix A'

"""Shared completion dispatch delegates wire behavior to provider adapters."""
from api.llm_complete import complete


def test_complete_dispatches_resolved_request(monkeypatch):
    seen = []
    monkeypatch.setattr('api.llm_complete._complete_provider',
                        lambda provider, request, **_kw: seen.append((provider, request)) or ' hello ')
    assert complete(user='hi', providers=('openai',), openai_model='custom', json_object=True) == 'hello'
    assert seen[0][0] == 'openai'
    assert seen[0][1].model == 'custom'
    assert seen[0][1].json_object is True


def test_complete_strips_think_blocks(monkeypatch):
    monkeypatch.setattr('api.llm_complete._complete_provider', lambda *a, **k: '<think>secret</think>\nTitle here')
    assert complete(user='x', providers=('local',)) == 'Title here'


def test_complete_explicit_local_provider_skips_cloud(monkeypatch):
    seen = []
    monkeypatch.setattr('api.llm_complete._complete_provider', lambda provider, request, **_kw: seen.append(provider) or 'on device')
    assert complete(user='x', providers=('local',)) == 'on device'
    assert seen == ['local']


def test_provider_failure_is_visible_without_sensitive_body(monkeypatch, caplog):
    def fail(*args, **_kw):
        raise RuntimeError('secret response body')
    monkeypatch.setattr('api.llm_complete._complete_provider', fail)
    assert complete(user='private prompt', providers=('openai',)) is None
    assert 'RuntimeError' in caplog.text
    assert 'secret response body' not in caplog.text
    assert 'private prompt' not in caplog.text

"""Provider discovery/execution: fake SDKs, no inference or live credentials."""
from dataclasses import replace
from types import SimpleNamespace as NS
import json
import time
import pytest
import yaml
from api.model_providers import cache, capabilities, catalog, models_dev, service
from api.model_providers.types import TextRequest, TextResult, TextResult


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv('CUTTLE_HOME', str(tmp_path))
    for key in ('OPENAI_API_KEY', 'API_KEY', 'ANTHROPIC_API_KEY'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(models_dev, 'fetch_catalog', lambda: {
        'openai': {'models': {'gpt-future': {
            'temperature': False, 'reasoning': True,
            'modalities': {'input': ['text', 'image'], 'output': ['text']},
            'reasoning_options': [{'type': 'effort', 'values': ['low', 'high']}],
            'limit': {'context': 10000, 'output': 5000},
        }}}
    })


class FakeAdapter:
    def __init__(self, rows): self.rows, self.calls = rows, 0
    def discovery_identity(self, manifest): return manifest.api_key()
    def list_models(self, manifest):
        self.calls += 1
        if isinstance(self.rows, Exception): raise self.rows
        return self.rows
    def complete_text(self, manifest, request, caps): return 'hello'
    def generate_text(self, manifest, request, caps): return TextResult('hello')


def fake_provider(monkeypatch, rows):
    monkeypatch.setenv('OPENAI_API_KEY', 'account-one-secret')
    adapter = FakeAdapter(rows)
    monkeypatch.setattr(service, 'get_adapter', lambda _: adapter)
    return adapter


def test_live_access_intersection_and_metadata_provenance(monkeypatch):
    fake_provider(monkeypatch, [{'id': 'gpt-future'}, {'id': 'text-embedding-3-small'}, {'id': 'unfamiliar'}])
    result = service.discover_models('openai')
    rows = {r['id']: r for r in result['models']}
    assert result['source'] == 'provider_api' and len(rows) == 3
    assert rows['text-embedding-3-small']['compatibility'] == 'unsupported'
    assert rows['unfamiliar']['compatibility'] == 'unknown'
    future = rows['gpt-future']
    assert future['availability'] == 'available'
    assert future['capabilities']['temperature'] is False
    assert future['capabilities']['sources']['temperature'] == 'models_dev'
    assert future['capabilities']['reasoning_efforts'] == ['low', 'high']
    assert future['capabilities']['max_output_tokens'] == 5000


def test_account_cache_ttl_refresh_empty_and_no_credentials(monkeypatch):
    adapter = fake_provider(monkeypatch, [{'id': 'gpt-a'}])
    service.discover_models('openai'); service.discover_models('openai')
    assert adapter.calls == 1
    monkeypatch.setenv('OPENAI_API_KEY', 'account-two-secret'); adapter.rows = []
    result = service.discover_models('openai')
    assert result['models'] == [] and result['source'] == 'provider_api'
    assert adapter.calls == 2
    assert service.discover_models('openai')['models'] == []
    monkeypatch.delenv('OPENAI_API_KEY')
    result = service.discover_models('openai', refresh=True)
    assert adapter.calls == 2 and result['source'] == 'manifest_fallback'
    assert all(r['availability'] == 'unknown' for r in result['models'])


def test_account_change_during_discovery_cannot_mix_account_snapshots(monkeypatch):
    adapter = fake_provider(monkeypatch, [])
    def list_models(manifest):
        monkeypatch.setenv('OPENAI_API_KEY', 'different-account')
        return [{'id': 'private-account-model'}]
    monkeypatch.setattr(adapter, 'list_models', list_models)
    result = service.discover_models('openai')
    assert result['source'] == 'manifest_fallback'
    assert 'private-account-model' not in json.dumps(result)
    assert 'changed' in result['message']
    assert 'models' not in cache.read(service._snapshot_name(catalog.get_provider('openai'), adapter))


def test_failed_refresh_keeps_last_good_list_and_sanitizes_error(monkeypatch):
    adapter = fake_provider(monkeypatch, [{'id': 'gpt-a'}])
    service.discover_models('openai')
    name = service._snapshot_name(catalog.get_provider('openai'), adapter)
    saved = cache.read(name); saved['fetched_at'] = time.time() - 4000; cache.write(name, saved)
    adapter.rows = RuntimeError('account-one-secret private response')
    result = service.discover_models('openai', refresh=True)
    assert result['source'] == 'stale_provider_api'
    assert result['models'][0]['id'] == 'gpt-a'
    assert result['models'][0]['availability'] == 'unknown'
    assert 'RuntimeError' in result['message']
    assert 'secret' not in json.dumps(result) and 'private response' not in json.dumps(result)


def test_metadata_failure_preserves_snapshot(monkeypatch):
    first = models_dev.snapshot(refresh=True)
    monkeypatch.setattr(models_dev, 'fetch_catalog', lambda: (_ for _ in ()).throw(ValueError('private')))
    failed = models_dev.snapshot(refresh=True)
    assert failed['catalog'] == first['catalog'] and 'private' not in failed['error']


def test_corrupt_cache_falls_back():
    from core.runtime_paths import runtime_cache_path
    cache.write('bad.json', {'fetched_at': 'invalid', 'models': []})
    assert cache.read('bad.json') == {}
    runtime_cache_path('bad.json').write_text('{broken')
    assert cache.read('bad.json') == {}


def test_manifest_validation_and_bundled_registry(tmp_path):
    assert [m.id for m in catalog.manifests()] == ['openai', 'anthropic', 'local', 'elevenlabs', 'google', 'typesafe']
    folder = tmp_path / 'custom'; folder.mkdir()
    manifest = {'schema_version': 1, 'id': 'custom', 'operations': ['text_generation']}
    path = folder / 'manifest.yaml'; path.write_text(yaml.safe_dump(manifest))
    assert catalog.load_manifest(folder).id == 'custom'
    for patch in ({'id': '../bad'}, {'schema_version': 2}, {'defaults': {'temperature': 'false'}},
                  {'models': [{'id': 'a'}, {'id': 'a'}]}, {'needs_credential': 'false'},
                  {'model_rules': [{'match': ['*'], 'capabilities': {'operations': ['image_generation']}}]}):
        path.write_text(yaml.safe_dump({**manifest, **patch}))
        with pytest.raises(ValueError): catalog.load_manifest(folder)
    with pytest.raises(ValueError): catalog.get_adapter('../bad')


@pytest.mark.parametrize('model', ['gpt-image-1', 'tts-1', 'whisper-1', 'o3-deep-research', 'gpt-4o-audio-preview'])
def test_non_background_operations_are_excluded(model):
    assert 'text_generation' not in capabilities.resolve(catalog.get_provider('openai'), model)['operations']


def test_exact_overrides_and_retirement_are_authoritative():
    manifest = replace(catalog.get_provider('openai'), models=(
        {'id': 'gpt-future', 'capabilities': {'temperature': False, 'transport': 'responses'}},))
    caps = capabilities.resolve(manifest, 'gpt-future', metadata={'temperature': True})
    assert caps['temperature'] is False and caps['sources']['temperature'] == 'model_manifest'
    assert capabilities.resolve(manifest, 'gpt-future', live={'lifecycle': 'retired'})['operations'] == []
    assert capabilities.resolve(manifest, 'gpt-future', metadata={'modalities': {'output': ['image']}})['operations'] == []


def test_anthropic_protocol_overrides_outdated_temperature_metadata():
    manifest = catalog.get_provider('anthropic')
    assert capabilities.resolve(manifest, 'claude-sonnet-4-6', metadata={'temperature': True})['temperature'] is False
    assert capabilities.resolve(manifest, 'claude-haiku-4-5-20251001')['temperature'] is True


def test_completion_uses_cached_caps_and_never_discovers(monkeypatch):
    adapter = fake_provider(monkeypatch, RuntimeError('must never list models'))
    monkeypatch.setattr(models_dev, 'fetch_catalog', lambda: pytest.fail('completion fetched metadata'))
    assert service.complete_text('openai', TextRequest('hi', model='gpt-4o-mini')) == 'hello'
    assert service.complete_text('openai', TextRequest('hi', model='text-embedding-3-small')) is None
    assert adapter.calls == 0


class FakeSDK:
    def __init__(self):
        self.kwargs = None
        self.chat = NS(completions=NS(create=self.create))
        self.responses = NS(create=self.create); self.messages = NS(create=self.create)
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def create(self, **kwargs):
        self.kwargs = kwargs
        return NS(choices=[NS(message=NS(content='hello'))], output_text='hello', content=[NS(type='text', text='hello')])


def test_openai_request_translation_reasoning_and_responses(monkeypatch):
    from api.model_providers.openai import adapter as module
    monkeypatch.setenv('OPENAI_API_KEY', 'fake-key')
    sdk = FakeSDK(); monkeypatch.setattr(module, 'client', lambda *a: sdk)
    manifest = catalog.get_provider('openai')
    request = TextRequest('hi', system='system', model='gpt-5', json_object=True)
    caps = capabilities.resolve(manifest, request.model, metadata={
        'reasoning_options': [{'type': 'effort', 'values': ['low', 'high']}]})
    assert module.Adapter().complete_text(manifest, request, caps) == 'hello'
    assert 'temperature' not in sdk.kwargs and sdk.kwargs['max_completion_tokens'] == 4096
    assert sdk.kwargs['reasoning_effort'] == 'low'
    assert sdk.kwargs['response_format'] == {'type': 'json_object'}
    request = replace(request, model='gpt-5-codex')
    assert module.Adapter().complete_text(manifest, request, capabilities.resolve(manifest, request.model)) == 'hello'
    assert 'messages' not in sdk.kwargs and 'input' in sdk.kwargs
    assert sdk.kwargs['text']['format']['type'] == 'json_object'


def test_anthropic_discovery_capabilities_and_native_messages(monkeypatch):
    from api.model_providers.anthropic import adapter as module
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'fake-key')
    sdk = FakeSDK()
    data = {'display_name': 'Future Claude', 'lifecycle': 'active', 'max_tokens': 5000,
            'capabilities': {'thinking': {'supported': True, 'types': {
                'adaptive': {'supported': True}, 'disabled': {'supported': False}}},
                'effort': {'supported': True, 'low': {'supported': True}, 'max': {'supported': False}},
                'structured_outputs': {'supported': True}}}
    sdk.models = NS(list=lambda **kwargs: iter([NS(id='claude-future', model_dump=lambda: data)]))
    monkeypatch.setattr(module, 'client', lambda *a: sdk)
    manifest = catalog.get_provider('anthropic'); rows = module.Adapter().list_models(manifest)
    caps = capabilities.resolve(manifest, rows[0]['id'], live=rows[0])
    assert caps['reasoning_efforts'] == ['low'] and caps['structured_output'] is True
    request = TextRequest('hi', system='system', model='claude-future')
    assert module.Adapter().complete_text(manifest, request, caps) == 'hello'
    assert sdk.kwargs['system'] == 'system' and sdk.kwargs['thinking'] == {'type': 'adaptive'}
    assert sdk.kwargs['output_config'] == {'effort': 'low'} and 'temperature' not in sdk.kwargs
    assert service.complete_text('anthropic', replace(request, json_object=True)) is None


def test_local_discovery_uses_installed_capabilities_and_auth(monkeypatch):
    import requests
    from api.model_providers.local import adapter as module
    monkeypatch.setattr(module, 'get_local_backend', lambda: 'ollama')
    monkeypatch.setattr(module, 'get_local_native_base_url', lambda: 'http://local')
    monkeypatch.setattr(module, 'get_local_api_key', lambda: 'local-key')
    calls = []
    def get(url, **kwargs):
        calls.append(kwargs)
        return NS(ok=True, json=lambda: {'models': [{'name': 'chat'}, {'name': 'vector'}]})
    def post(url, **kwargs):
        caps = ['completion'] if kwargs['json']['model'] == 'chat' else ['embedding']
        return NS(ok=True, json=lambda: {'capabilities': caps})
    monkeypatch.setattr(requests, 'get', get); monkeypatch.setattr(requests, 'post', post)
    manifest = catalog.get_provider('local'); rows = module.Adapter().list_models(manifest)
    assert calls[0]['headers']['Authorization'] == 'Bearer local-key'
    assert capabilities.resolve(manifest, 'vector', live=rows[1])['operations'] == []


def test_anthropic_sdk_follows_every_model_page(monkeypatch):
    import httpx2 as httpx
    from anthropic import Anthropic
    from api.model_providers.anthropic import adapter as module
    cursors = []
    def transport(request):
        cursor = request.url.params.get('after_id')
        cursors.append(cursor)
        mid = 'claude-one' if cursor is None else 'claude-two'
        return httpx.Response(200, json={
            'data': [{'type': 'model', 'id': mid, 'display_name': mid, 'created_at': '2025-01-01T00:00:00Z'}],
            'has_more': cursor is None, 'first_id': mid, 'last_id': mid,
        })
    sdk = Anthropic(api_key='fake', http_client=httpx.Client(transport=httpx.MockTransport(transport)))
    monkeypatch.setattr(module, 'client', lambda *a: sdk)
    assert [r['id'] for r in module.Adapter().list_models(catalog.get_provider('anthropic'))] == ['claude-one', 'claude-two']
    assert cursors == [None, 'claude-one']

"""Every direct model operation uses an adapter. No live inference/network."""
import ast
import base64
import io
import json
import time
import urllib.error
import wave
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
import requests

from api.model_providers import cache, capabilities, catalog, models_dev, service
from api.model_providers.types import (TextRequest, TextResult, ImageInput, SpeechRequest,
                                      SpeechResult, TranscriptionRequest, DecisionRequest)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv('CUTTLE_HOME', str(tmp_path))
    for p in catalog.manifests():
        for key in p.credential_env:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(models_dev, 'fetch_catalog', lambda: {})
    monkeypatch.setattr(requests, 'get', lambda *a, **k: pytest.fail('Unexpected network read'))
    monkeypatch.setattr(requests, 'post', lambda *a, **k: pytest.fail('Unexpected inference call'))


class SDK:
    def __init__(self, response):
        self.calls = []
        def create(**kwargs):
            self.calls.append(kwargs)
            return response
        self.models = NS(list=create)
        self.messages = NS(create=create)
        self.responses = NS(create=create)
        self.chat = NS(completions=NS(create=create))
        self.audio = NS(speech=NS(create=create), transcriptions=NS(create=create))
    def __enter__(self): return self
    def __exit__(self, *_): pass


def test_all_vendor_clients_and_inference_methods_are_owned_by_adapters():
    src = Path(__file__).resolve().parents[2]
    violations = []
    for root in ('api', 'core', 'managers'):
        for file in (src / root).rglob('*.py'):
            if 'model_providers' in file.parts:
                continue
            tree = ast.parse(file.read_text(encoding='utf-8-sig'))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module in ('openai', 'anthropic', 'google.genai'):
                    violations.append(f'{file.name}:{node.lineno}:SDK import')
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    name = ast.unparse(node.func)
                    if name.endswith(('.chat.completions.create', '.messages.create', '.responses.create',
                                      '.audio.speech.create', '.audio.transcriptions.create', '.generate_content')):
                        violations.append(f'{file.name}:{node.lineno}:{name}')
    assert not violations, violations


@pytest.mark.parametrize('operation,model,supported', [
    ('text_generation','gpt-4o-mini',True), ('speech_generation','gpt-4o-mini',False),
    ('speech_generation','gpt-4o-mini-tts',True), ('text_generation','tts-1',False),
    ('audio_transcription','whisper-1',True), ('text_generation','whisper-1',False)])
def test_operation_filters(operation, model, supported):
    result = service.discover_models('openai', operation=operation)
    row = next(r for r in result['models'] if r['id'] == model) if model != 'whisper-1' else service._record(
        catalog.get_provider('openai'), {'id': model}, {}, 'unknown', operation)
    assert (row['compatibility'] == 'supported') == supported


def test_speech_modalities_do_not_get_rejected_as_nontext():
    p = catalog.get_provider('openai')
    caps = capabilities.resolve(p, 'gpt-4o-mini-tts', metadata={'modalities': {'input':['text'], 'output':['audio']}})
    assert caps['operations'] == ['speech_generation']
    assert capabilities.resolve(p, 'tts-1', live={'lifecycle':'retired','capabilities':{'operations':['speech_generation']}})['operations'] == []


def test_native_negative_effort_overrides_models_dev_and_fallback():
    p = catalog.get_provider('anthropic')
    caps = capabilities.resolve(p, 'claude-haiku-5-5', metadata={'reasoning_options':[{'type':'effort','values':['max']}]},
                                live={'capabilities':{'reasoning_efforts':[]}})
    assert caps['reasoning_efforts'] == []
    assert caps['sources']['reasoning_efforts'] == 'provider_api'
    assert capabilities.resolve(p, 'claude-haiku-4-5-20251001').get('reasoning_efforts') == []


def test_anthropic_explicit_effort_and_images_translate_native_wire(monkeypatch):
    from api.model_providers.anthropic import adapter
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'fake')
    response = NS(content=[NS(type='text',text='summary')], usage=NS(input_tokens=8,output_tokens=3))
    sdk = SDK(response); monkeypatch.setattr(adapter, 'client', lambda *a: sdk)
    result = service.generate_text('anthropic', TextRequest('describe', model='claude-haiku-5-5',
                    effort='max', images=(ImageInput(b'image','image/png'),)))
    call = sdk.calls[0]
    assert call['output_config'] == {'effort':'max'} and call['thinking'] == {'type':'adaptive'}
    assert 'temperature' not in call and call['max_tokens'] >= 4096
    assert call['messages'][0]['content'][1]['source']['data'] == base64.b64encode(b'image').decode()
    assert result.text == 'summary' and result.usage['total_tokens'] == 11
    with pytest.raises(ValueError, match='advertise'):
        service.generate_text('anthropic', TextRequest('hello',model='claude-haiku-4-5-20251001',effort='high'))


def test_anthropic_negative_native_effort_is_recorded(monkeypatch):
    from api.model_providers.anthropic import adapter
    model = NS(id='claude-haiku-5-5',model_dump=lambda: {'capabilities':{'effort':{'supported':False}}})
    sdk = SDK([model]); monkeypatch.setattr(adapter,'client',lambda *a:sdk)
    assert adapter.build_adapter().list_models(catalog.get_provider('anthropic'))[0]['capabilities']['reasoning_efforts'] == []


@pytest.mark.parametrize('transport', ['chat_completions', 'responses'])
def test_openai_effort_usage_and_image_wire(monkeypatch, transport):
    from api.model_providers.openai import adapter
    monkeypatch.setenv('OPENAI_API_KEY','fake')
    response = NS(output_text='summary', choices=[NS(message=NS(content='summary'))],usage=NS(input_tokens=7,output_tokens=2))
    sdk = SDK(response); monkeypatch.setattr(adapter,'client',lambda *a:sdk)
    caps = {'reasoning':True,'reasoning_efforts':['low','high'], 'temperature':False,'transport':transport}
    request = TextRequest('describe',model='gpt-future',effort='high',images=(ImageInput(url='https://example.test/image'),))
    result = adapter.build_adapter().generate_text(catalog.get_provider('openai'),request,caps)
    call = sdk.calls[0]; assert 'temperature' not in call
    assert (call.get('reasoning') or {}).get('effort', call.get('reasoning_effort')) == 'high'
    assert result.usage['total_tokens'] == 9
    assert 'https://example.test/image' in json.dumps(call)


def test_openai_speech_transcription_and_builtin_voices(monkeypatch):
    from api.model_providers.openai import adapter
    monkeypatch.setenv('OPENAI_API_KEY','fake')
    sdk = SDK(NS(content=b'mp3',text='spoken words')); monkeypatch.setattr(adapter,'client',lambda *a:sdk)
    result = service.synthesize_speech('openai',SpeechRequest('hello','tts-1','alloy',1.25))
    assert result.audio == b'mp3' and sdk.calls[-1]['voice'] == 'alloy'
    assert 'coral' not in {v['id'] for v in service.discover_voices('openai',model='tts-1')['voices']}
    assert 'coral' in {v['id'] for v in service.discover_voices('openai',model='gpt-4o-mini-tts')['voices']}
    with pytest.raises(ValueError, match='Voice'):
        service.synthesize_speech('openai',SpeechRequest('hello','tts-1','coral'))
    assert service.transcribe('openai',TranscriptionRequest(b'audio','whisper-1',prompt='hint',language='en')) == 'spoken words'
    assert sdk.calls[-1]['file'][1] == b'audio'


@pytest.mark.parametrize('provider', ['openai','anthropic'])
def test_key_testing_lists_models_with_supplied_key_without_inference_or_env_mutation(monkeypatch, provider):
    module = __import__('api.model_providers.' + provider + '.adapter',fromlist=['client'])
    sdk = SDK([]); seen = []
    def client(manifest, timeout, key=None):
        seen.append(key); return sdk
    monkeypatch.setattr(module,'client',client)
    assert service.check_key(provider,'new-secret-key-value-123')[0]
    assert seen == ['new-secret-key-value-123'] and len(sdk.calls) == 1
    assert 'model' not in sdk.calls[0] and not catalog.get_provider(provider).api_key()


def test_requests_auth_errors_include_status_without_body_or_secret():
    response = NS(status_code=401)
    message = cache.public_error(requests.HTTPError('secret payload', response=response),'Discovery')
    assert 'HTTP 401' in message and 'secret' not in message


def test_voice_cache_preserves_empty_last_good_and_isolates_account(monkeypatch):
    monkeypatch.setenv('ELEVENLABS_API_KEY','one')
    class Voices:
        calls=0; rows=[]
        def discovery_identity(self, p): return p.api_key()
        def list_voices(self, p, model):
            self.calls += 1
            if isinstance(self.rows,Exception): raise self.rows
            return self.rows
    adapter=Voices(); monkeypatch.setattr(service,'get_adapter',lambda p:adapter)
    result=service.discover_voices('elevenlabs')
    assert result['voices'] == [] and result['source']=='provider_api'
    adapter.rows=RuntimeError('secret'); result=service.discover_voices('elevenlabs',refresh=True)
    assert result['voices']==[] and 'secret' not in result['message']
    monkeypatch.setenv('ELEVENLABS_API_KEY','two'); adapter.rows=[{'id':'new','label':'Mine'}]
    result=service.discover_voices('elevenlabs'); assert result['voices'][0]['id']=='new'
    assert adapter.calls==3


def test_voice_cache_discards_account_change_in_flight(monkeypatch):
    monkeypatch.setenv('ELEVENLABS_API_KEY','one')
    class Voices:
        def discovery_identity(self,p): return p.api_key()
        def list_voices(self,p,model):
            monkeypatch.setenv('ELEVENLABS_API_KEY','two'); return [{'id':'private'}]
    monkeypatch.setattr(service,'get_adapter',lambda p:Voices())
    result=service.discover_voices('elevenlabs')
    assert result['voices']==[] and 'private' not in json.dumps(result)


@pytest.mark.parametrize('new_api', [False,True])
def test_google_speech_formats_and_endpoint(monkeypatch,new_api):
    monkeypatch.setenv('GEMINI_API_KEY','fake')
    pcm=b'\x00\x00\x01\x00'
    data=({'steps':[{'type':'model_output','content':[{'type':'audio','data':base64.b64encode(b'RIFFwav').decode(),'mime_type':'audio/wav'}]}],
           'usage':{'total_input_tokens':5,'total_output_tokens':11}} if new_api else
          {'candidates':[{'content':{'parts':[{'inlineData':{'data':base64.b64encode(pcm).decode(),'mimeType':'audio/L16;codec=pcm;rate=24000'}}]}}]})
    calls=[]
    def post(url,**kwargs):
        calls.append((url,kwargs)); return NS(raise_for_status=lambda:None,json=lambda:data)
    monkeypatch.setattr(requests,'post',post)
    model='gemini-3.8-flash-tts' if new_api else 'gemini-2.5-flash-preview-tts'
    result=service.synthesize_speech('google',SpeechRequest('hello',model,'Kore'))
    assert result.mime=='audio/wav' and result.audio.startswith(b'RIFF')
    assert calls[0][1]['headers']=={'x-goog-api-key':'fake'} and 'key=' not in calls[0][0]
    if new_api:
        assert calls[0][0].endswith('/interactions') and result.usage['output_tokens']==11
    else:
        with wave.open(io.BytesIO(result.audio)) as wav:
            assert wav.getframerate()==24000 and wav.readframes(2)==pcm


def test_google_models_and_voice_pagination(monkeypatch):
    from api.model_providers.google import adapter
    monkeypatch.setenv('GEMINI_API_KEY','fake'); calls=[]
    def get(url, **kw):
        calls.append(kw['params'])
        if url.endswith('/voices'):
            data=({'voices':[{'id':'Custom','display_name':'Custom'}]} if kw['params'].get('page_token') else
                  {'voices':[{'id':'Kore'}],'next_page_token':'next'})
        else:
            data=({'models':[{'name':'models/gemini-3.8-flash-tts'}]} if kw['params'].get('pageToken') else
                  {'models':[{'name':'models/text','supportedGenerationMethods':['generateContent']}],'nextPageToken':'next'})
        return NS(raise_for_status=lambda:None,json=lambda:data)
    monkeypatch.setattr(requests,'get',get)
    sdk=adapter.build_adapter(); p=catalog.get_provider('google')
    assert len(sdk.list_models(p))==2
    assert len(sdk.list_voices(p,'gemini-3.8-flash-tts'))==2
    assert sdk.list_voices(p,'gemini-2.5-flash-preview-tts')==[{'id':'Kore','label':'Kore'}]


def test_jev_transport_keeps_domain_parsing_and_error_retry_policy(monkeypatch):
    from api.jev.client import JevClient, JevError
    import urllib.request
    class Response:
        status=200
        def __enter__(self): return self
        def __exit__(self,*_): pass
        def read(self): return json.dumps({'answers':{'urgent':{'type':'noul','noul':.9}},'usage':{'input_tokens':4}}).encode()
    calls=[]
    def urlopen(req,timeout):
        calls.append(json.loads(req.data)); return Response()
    monkeypatch.setattr(urllib.request,'urlopen',urlopen)
    client=JevClient(api_key='fake',base_url='https://example.test',model='jev-latest')
    assert client.system_one('help',{'urgent':{'type':'noul'}}).get('urgent').noul==.9
    assert calls[0]['state']=='help'
    def rejected(*a,**kw): raise urllib.error.HTTPError('https://example.test',429,'secret',{},None)
    monkeypatch.setattr(urllib.request,'urlopen',rejected)
    with pytest.raises(JevError) as error:
        client.system_one('help',{'urgent':{'type':'noul'}})
    assert error.value.status==429 and error.value.retryable and 'secret' not in str(error.value)


def test_background_and_narration_resolve_separate_model_efforts(monkeypatch):
    from api import completion_providers as cp, llm_complete, chat_tts
    class Settings:
        values={'completion_provider':{'provider':'anthropic'},'completion_models':{'anthropic':'claude-haiku-5-5'},
                'completion_efforts':{'anthropic':{'claude-haiku-5-5':'high'}}}
        def get_setting(self,key): return self.values.get(key)
        def set_setting(self,key,value): self.values[key]=value; return True
    monkeypatch.setattr(cp,'_settings',lambda:Settings())
    calls=[]
    monkeypatch.setattr(llm_complete,'_complete_provider',lambda p,r:calls.append(r) or 'text')
    assert llm_complete.complete(user='hello',providers=['anthropic'])=='text' and calls[-1].effort=='high'
    monkeypatch.setattr(service,'generate_text',lambda p,r:calls.append(r) or TextResult('Summary'))
    settings=chat_tts.normalize_chat_tts_settings({'summarize_provider':'anthropic','summarize_models':{'anthropic':'claude-haiku-5-5'},
               'summarize_efforts':{'anthropic':{'claude-haiku-5-5':'max'}}})
    chat_tts.prepare_spoken_text_with_usage('word '*100,settings)
    assert calls[-1].effort=='max'
    assert cp.set_efforts({'anthropic':{'claude-haiku-5-5':'low'}}) is True
    with pytest.raises(ValueError): cp.set_efforts({'anthropic':{'claude-haiku-4-5-20251001':'high'}})


def test_settings_operation_discovery_authorization_and_effort_roundtrip(monkeypatch):
    from flask import Flask, jsonify
    from api import http_authz, settings_routes, completion_providers as cp, chat_tts
    data = {}
    class Settings:
        def get_setting(self, key, default=None): return data.get(key, default)
        def set_setting(self, key, value): data[key] = value; return True
    monkeypatch.setattr(cp, '_settings', lambda: Settings())
    monkeypatch.setattr('managers.settings_manager.get_settings_manager', lambda: Settings())
    monkeypatch.setattr(http_authz, 'require_owner', lambda: ({'id':1},None))
    monkeypatch.setattr(http_authz, 'require_authenticated', lambda: ({'id':1},None))
    app = Flask(__name__); app.register_blueprint(settings_routes.settings_bp)
    chat_tts.register_chat_tts_routes(app)
    client = app.test_client()
    result = client.get('/api/settings/model-providers/openai/models?operation=speech_generation')
    assert result.status_code == 200 and result.json['operation'] == 'speech_generation'
    assert next(r for r in result.json['models'] if r['id']=='tts-1')['compatibility'] == 'supported'
    assert client.get('/api/settings/model-providers/anthropic/models?operation=speech_generation').status_code == 400
    assert client.get('/api/settings/model-providers/missing/models').status_code == 404
    effort = {'anthropic':{'claude-haiku-5-5':'high'}}
    result = client.post('/api/settings/completion-providers',json={'efforts':effort})
    assert result.status_code == 200 and result.json['efforts'] == effort
    result = client.post('/api/settings/completion-providers',json={'efforts':{'anthropic':{'claude-haiku-4-5-20251001':'high'}}})
    assert result.status_code == 400 and data['completion_efforts'] == effort
    result = client.post('/api/settings/chat-tts',json={'summarize_efforts':effort})
    assert result.status_code == 200 and result.json['chat_tts']['summarize_efforts'] == effort
    monkeypatch.setattr(http_authz,'require_owner',lambda: (None,(jsonify({'success':False}),403)))
    assert client.get('/api/settings/model-providers/openai/models').status_code == 403
    assert client.post('/api/settings/chat-tts',json={}).status_code == 403


def test_speech_defaults_and_settings_save_failure_are_honest(monkeypatch):
    from api import tts_providers, chat_tts
    assert tts_providers.normalize_voice('openai',None,'tts-1') == 'alloy'
    class Settings:
        def get_setting(self,key): return {}
        def set_setting(self,key,value): return False
    monkeypatch.setattr('managers.settings_manager.get_settings_manager',lambda:Settings())
    with pytest.raises(RuntimeError,match='Failed to save'):
        chat_tts.save_chat_tts_settings({'enabled':False})

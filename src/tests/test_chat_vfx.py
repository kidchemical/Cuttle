import json
import subprocess
from pathlib import Path
import pytest
from api import chat_vfx

@pytest.fixture(autouse=True)
def private_store(tmp_path, monkeypatch):
    monkeypatch.setenv('CUTTLE_CHAT_VFX_DB', str(tmp_path / 'vfx.db'))

def test_session_delivery_cursors_and_expiry(monkeypatch):
    monkeypatch.setattr(chat_vfx.time, 'time', lambda: 100)
    eid = chat_vfx.spawn_confetti('CH-000927', 60)
    assert chat_vfx.pending(928) == []
    events = chat_vfx.pending('db_session_927')
    assert events[0]['id'] == eid
    assert chat_vfx.pending(927) == events  # another browser can read independently
    assert chat_vfx.pending(927, events[0]['seq']) == []
    monkeypatch.setattr(chat_vfx.time, 'time', lambda: 131)
    assert chat_vfx.pending(927) == []

@pytest.mark.parametrize('count', [True, -1, 0, 401, '60'])
def test_invalid_particle_counts(count):
    with pytest.raises(ValueError):
        chat_vfx.spawn_confetti(927, count)

def test_cli_reports_validation_errors(capsys):
    from api.chat_vfx.__main__ import main
    assert main(['confetti', '--session', 'nope']) == 2
    assert json.loads(capsys.readouterr().out)['success'] is False
    assert main(['confetti', '--session', '927']) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'queued'

def test_http_scope_and_auth(monkeypatch):
    from flask import Flask
    from api.chat_vfx import routes
    app = Flask(__name__)
    app.register_blueprint(routes.chat_vfx_bp)
    client = app.test_client()
    assert client.get('/api/chat-vfx/927').status_code == 401
    assert client.post('/api/chat-vfx/927', json={'kind': 'confetti'}).status_code == 401
    monkeypatch.setattr(routes, 'require_owner', lambda: ({}, None))
    monkeypatch.setattr(routes, 'require_chat_session_access', lambda sid: ({}, int(sid), None) if sid == '927' else (None, None, (json.dumps({'error': 'denied'}), 404)))
    assert client.post('/api/chat-vfx/928', json={'kind': 'confetti'}).status_code == 404
    assert client.post('/api/chat-vfx/927', json={'kind': 'confetti', 'count': 0}).status_code == 400
    assert client.post('/api/chat-vfx/927', json={'kind': 'confetti'}).json['status'] == 'queued'
    assert len(client.get('/api/chat-vfx/927').json['events']) == 1

def test_client_navigation_dedup_and_disposal():
    js = Path(__file__).resolve().parents[1] / 'web/js/chat_vfx.js'
    script = r'''
const assert = require('assert');
const V = require(process.argv[1]);
(async () => {
 let session = '927', resolve, tick, drawn = 0;
 const event = {seq:1,kind:'confetti',count:60,expires:999};
 const host = {getSessionId:()=>session, now:()=>1000,
 fetch:()=>new Promise(r=>resolve=r), confetti:()=>drawn++, toast:()=>{},
 setTimeout:f=>{tick=f;return 1},clearTimeout:()=>{}};
 const c = V.create(host);
 session='928';resolve({ok:true,json:async()=>({events:[event]})});
 await new Promise(r=>setImmediate(r));assert.equal(drawn,0);
 session='927';tick();resolve({ok:true,json:async()=>({events:[event]})});
 await new Promise(r=>setImmediate(r));assert.equal(drawn,1);
 tick();resolve({ok:true,json:async()=>({events:[event]})});
 await new Promise(r=>setImmediate(r));assert.equal(drawn,1);
 tick();c.dispose();resolve({ok:true,json:async()=>({events:[{...event,seq:2}]})});
 await new Promise(r=>setImmediate(r));assert.equal(drawn,1);
})().catch(e=>{console.error(e);process.exit(1)});
'''
    subprocess.run(['node', '-e', script, str(js)], check=True)

def test_real_session_authorization(tmp_path, monkeypatch):
    from tests.test_http_authz import _auth_client
    ctx = _auth_client(tmp_path, monkeypatch)
    client = ctx['client']
    client.set_cookie('session_token', ctx['token'])
    url = '/api/chat-vfx/' + str(ctx['sid'])
    assert client.post(url, json={'kind': 'confetti', 'count': 60}).status_code == 200
    assert client.get(url).json['events'][0]['count'] == 60
    other = '/api/chat-vfx/' + str(ctx['other_sid'])
    assert client.get(other).status_code == 404
    assert client.post(other, json={'kind': 'confetti'}).status_code == 404
    monkeypatch.setenv('OWNER_USER_EMAIL', 'someoneelse@local')
    assert client.post(url, json={'kind': 'confetti'}).status_code == 403


def test_flag_cli_external_write_visible_and_failures(tmp_path, monkeypatch, capsys):
    import managers.settings_manager as managers
    from api.experimental import flags
    from api.experimental.__main__ import main
    path = tmp_path / 'settings.json'
    path.write_text('{"experimental_flags": {}, "unrelated": 42}')
    manager = managers.SettingsManager(path)
    monkeypatch.setattr(managers, 'get_settings_manager', lambda: manager)
    monkeypatch.delenv('CUTTLE_EXPERIMENTAL', raising=False)
    assert not flags.is_enabled('achievements')
    # Simulate another process writing its toggle after Flask cached settings.
    path.write_text('{"experimental_flags": {"achievements": true}, "unrelated": 99}')
    assert flags.is_enabled('achievements')
    assert main(['set', 'achievements', 'off']) == 0
    assert json.loads(path.read_text())['unrelated'] == 99
    capsys.readouterr()
    assert main(['get', 'missing']) == 2
    assert json.loads(capsys.readouterr().out)['success'] is False
    with pytest.raises(ValueError):
        flags.set_enabled('achievements', 'false')
    monkeypatch.setattr(manager, 'set_setting', lambda *args: False)
    assert main(['set', 'achievements', 'on']) == 2
    assert json.loads(capsys.readouterr().out)['success'] is False

def test_renderer_honors_motion_and_cleans_up():
    js = Path(__file__).resolve().parents[1] / 'web/js/chat_vfx.js'
    script = r'''
const assert=require('assert');let reduced=true, cleanup, removed=false;
global.window={matchMedia:()=>({matches:reduced}),innerHeight:800};
global.localStorage={getItem:()=>null};
const body={appendChild:e=>{e.parentNode=body},removeChild:e=>{removed=true;e.parentNode=null}};
global.document={body,createElement:()=>({style:{},setAttribute:()=>{},appendChild:()=>{}})};
global.requestAnimationFrame=f=>f();global.setTimeout=f=>{cleanup=f};
const V=require(process.argv[1]);
assert.equal(V.confetti(60),null);
reduced=false;const layer=V.confetti(60);assert(layer);cleanup();assert(removed);
'''
    subprocess.run(['node', '-e', script, str(js)], check=True)

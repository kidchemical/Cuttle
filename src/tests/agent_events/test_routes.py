from flask import Flask
import pytest
from api.agent_events.store import EventStore,state_dir


@pytest.fixture
def client(owner_session):
    from api.agent_events import routes
    app=Flask(__name__);app.register_blueprint(routes.bp)
    return owner_session.sign_in(app.test_client())


def test_full_payload_lazy_lookup_and_paged_log(client):
    store=EventStore(state_dir())
    store.write_batch([('run','q',{'harness':{'agent_id':'codex','model':'cheap'}})]+[
        ('event','q',{'kind':'tool','block_id':str(i),'args':{'path':'x'*10000}}) for i in range(130)])
    response=client.get('/api/agent-events/runs/q/log').get_json()
    assert len(response['events'])==100 and response['has_more']
    assert 'args' not in response['events'][0]
    detail=client.get('/api/agent-events/events/'+str(response['events'][0]['id'])).get_json()
    assert detail['detail']['args']['path']=='x'*10000
    next_page=client.get('/api/agent-events/runs/q/log?after='+str(response['next_cursor'])).get_json()
    assert len(next_page['events'])==30
    assert not next_page['has_more']
    deep=client.get('/api/agent-events/runs/q/log?seq=110').get_json()
    assert deep['events'][0]['seq']==110


def test_descending_history_and_changes_include_run_identity(client):
    store=EventStore(state_dir())
    store.write_batch([('run','q',{'harness':{'agent_id':'codex','chat_session_id':'1066'}}),
                       ('event','q',{'kind':'thinking','block_id':'one','text':'hello'}),
                       ('event','q',{'kind':'thinking','block_id':'two','text':'world'})])
    payload=client.get('/api/agent-events?direction=desc').get_json()
    assert payload['events'][0]['seq']==2
    assert store.changes()['events'][0]['agent_id']=='codex'


def test_anonymous_cannot_read_fleet_payloads():
    from api.agent_events.routes import bp
    app=Flask(__name__);app.register_blueprint(bp)
    assert app.test_client().get('/api/agent-events').status_code==401


def test_history_filters_and_live_cursor(client):
    store=EventStore(state_dir())
    store.write_batch([('run','q',{'harness':{'agent_id':'codex','model':'cheap','cwd':'/project','chat_session_id':'1066'}}),
                       ('event','q',{'kind':'tool','block_id':'one','args':{'needle':'searchable argument'}})])
    payload=client.get('/api/agent-events?model=cheap&project=project&chat=CH-001066&search=searchable').get_json()
    assert len(payload['events'])==1 and payload['stream_cursor']>0
    assert client.get('/api/agent-events?model=other').get_json()['events']==[]


def test_feed_page_requires_experiment_but_inspector_remains_available(client,monkeypatch):
    import api.experimental
    monkeypatch.setattr(api.experimental,'is_enabled',lambda flag:False)
    assert client.get('/agent_feed.html').status_code==403
    assert client.get('/api/agent-events/stream').status_code==403
    assert client.get('/api/agent-events').status_code==200
    monkeypatch.setattr(api.experimental,'is_enabled',lambda flag:True)
    assert client.get('/agent_feed.html').status_code==200


def test_active_runs_lists_only_running_agents(client):
    store=EventStore(state_dir())
    store.write_batch([('run','live',{'harness':{'agent_id':'codex','model':'cheap','cwd':'/p','chat_session_id':'1066'}}),
                       ('run','done',{'harness':{'agent_id':'cursor','model':'auto'},'finished_at':1,'success':True})])
    payload=client.get('/api/agent-events/active').get_json()
    assert [r['query_id'] for r in payload['runs']]==['live']
    assert payload['runs'][0]['agent_id']=='codex'
    assert payload['runs'][0]['chat_session_id']=='1066'


def test_active_feed_recovers_prior_boot_without_hiding_current_run(client):
    store=EventStore(state_dir())
    store.write_batch([
        ('run','orphan',{'harness':{'agent_id':'opencode'},'user_context':{'web_ui':True}}),
        ('run','current',{'harness':{'agent_id':'codex'},'user_context':{'web_ui':True}}),
    ])
    with store.connection() as conn:
        conn.execute("UPDATE runs SET started_at=1 WHERE query_id='orphan'")
    payload=client.get('/api/agent-events/active').get_json()
    assert [r['query_id'] for r in payload['runs']]==['current']
    log=client.get('/api/agent-events/runs/orphan/log').get_json()
    assert log['executing'] is False
    assert log['capture_interrupted'] is True


def test_log_sends_run_metadata_only_when_its_revision_is_stale(client):
    store=EventStore(state_dir())
    store.write_batch([('run','q',{'harness':{'agent_id':'codex'},'sent':{'text':'p'*20000,'chars':20000}}),
                       ('event','q',{'kind':'tool','block_id':'one'})])
    first=client.get('/api/agent-events/runs/q/log').get_json()
    assert first['sent']['chars']==20000 and first['meta_rev'] and first['stream_cursor']>0
    again=client.get('/api/agent-events/runs/q/log?meta_rev='+first['meta_rev']).get_json()
    assert again['meta_omitted'] is True and 'sent' not in again and 'harness' not in again
    assert again['executing'] is True and len(again['events'])==1
    store.write_batch([('run','q',{'harness':{'agent_id':'codex'},'finished_at':2,'success':True})])
    changed=client.get('/api/agent-events/runs/q/log?meta_rev='+first['meta_rev']).get_json()
    assert 'meta_omitted' not in changed and changed['meta_rev']!=first['meta_rev']
    assert changed['executing'] is False


def _sse_messages(response):
    buf=''
    for chunk in response.response:
        buf+=chunk.decode() if isinstance(chunk,bytes) else chunk
        while '\n\n' in buf:
            block,buf=buf.split('\n\n',1)
            fields=dict(line.split(': ',1) for line in block.splitlines() if ': ' in line and not line.startswith(':'))
            yield fields


def test_run_log_stream_pushes_only_this_runs_deltas_then_ends(client,monkeypatch):
    from api.agent_events import routes
    monkeypatch.setattr(routes,'LOG_STREAM_POLL_SEC',0)
    store=EventStore(state_dir())
    store.write_batch([('run','q',{'harness':{'agent_id':'codex'},'sent':{'text':'big'}}),
                       ('event','q',{'kind':'thinking','block_id':'a','text':'one'})])
    first=client.get('/api/agent-events/runs/q/log').get_json()
    store.write_batch([('event','q',{'kind':'thinking','block_id':'a','text':'one two'}),
                       ('event','other',{'kind':'tool','block_id':'x'}),
                       ('event','q',{'kind':'tool','block_id':'b'})])
    resp=client.get(f"/api/agent-events/runs/q/log/stream?after={first['stream_cursor']}&meta_rev={first['meta_rev']}",
                    buffered=False)
    assert resp.mimetype=='text/event-stream'
    messages=_sse_messages(resp)
    import json
    msg=next(messages)
    data=json.loads(msg['data'])
    assert [e['seq'] for e in data['events']]==[1,2]
    assert data['events'][0]['rev']==2 and 'text' not in data['events'][0]
    assert 'meta' not in data and data['executing'] is True
    assert int(msg['id'])==data['cursor']
    store.write_batch([('run','q',{'harness':{'agent_id':'codex'},'finished_at':3,'success':True})])
    msg=next(messages)
    data=json.loads(msg['data'])
    assert data['executing'] is False and data['meta']['success'] is True
    assert next(messages).get('event')=='end'
    resp.close()


def test_run_log_stream_unknown_run_is_404(client):
    assert client.get('/api/agent-events/runs/missing/log/stream').status_code==404

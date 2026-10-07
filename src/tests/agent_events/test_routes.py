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

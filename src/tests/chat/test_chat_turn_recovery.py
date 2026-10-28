"""Stopped/crashed turns survive reload without completing the current turn."""
import json
import subprocess
from pathlib import Path

from api.agent_events.store import EventStore
from api.chat_turn_recovery import preserve_interrupted_turns


def plant(store, sid, qid='lost', *, finished=True, status='cancelled'):
    body = {'timestamp': '2026-10-28T01:04:27',
            'user_context': {'web_ui': True},
            'harness': {'agent_id': 'cursor', 'model': 'auto', 'chat_session_id': str(sid)}}
    if finished:
        body.update(finished_at=200, success=False)
        if status == 'cancelled':
            body['error_message'] = '[CANCELLED] stopped'
        elif status == 'interrupted':
            body['capture_interrupted'] = True
    store.write_batch([('run', qid, body)])
    store.write_batch([
        ('event', qid, {'kind': 'thinking', 'block_id': 'private', 't': 101, 'text': 'PRIVATE THINKING'}),
        ('event', qid, {'kind': 'tool', 'block_id': 'tool', 't': 102, 'text': 'PRIVATE TOOL OUTPUT'}),
        ('event', qid, {'kind': 'writing', 'block_id': 'public', 't': 103, 'text': 'Investigated branches.'}),
    ])


def setup(owner_session, tmp_path):
    db = owner_session.db
    sid = db.create_chat_session(owner_session.user_id, 'recovery')
    db.add_message(sid, 'user', 'Investigate branches')
    return db, sid, EventStore(tmp_path / 'events')


def test_stopped_run_without_writing_still_persists_card(owner_session, tmp_path):
    """Stop must never drop work: a turn stopped before any writing event
    (only status/sent/edit progress) still leaves a durable interrupted card
    with a handle and a query-log link."""
    db, sid, store = setup(owner_session, tmp_path)
    store.write_batch([('run', 'early-stop', {
        'timestamp': '2026-10-28T01:04:27',
        'user_context': {'web_ui': True},
        'harness': {'agent_id': 'muse', 'model': 'x', 'chat_session_id': str(sid)},
        'finished_at': 200, 'success': False,
        'error_message': '[CANCELLED] stopped',
    })])
    store.write_batch([
        ('event', 'early-stop', {'kind': 'sent', 'block_id': 's', 't': 101, 'text': 'hi'}),
        ('event', 'early-stop', {'kind': 'status', 'block_id': 'st', 't': 102}),
        ('event', 'early-stop', {'kind': 'edit', 'block_id': 'e', 't': 103}),
    ])
    assert len(preserve_interrupted_turns(db, sid, store=store)) == 1
    assert preserve_interrupted_turns(db, sid, store=store) == []
    row = db.get_messages(sid)[-1]
    assert row['role'] == 'assistant'
    assert 'stopped before completion' in row['content']
    assert row['metadata']['query_id'] == 'early-stop'
    assert row['metadata']['report_url'] == '/query_log.html?id=early-stop'


def test_cancelled_writing_and_log_survive_reload_exactly_once(owner_session, tmp_path):
    db, sid, store = setup(owner_session, tmp_path)
    plant(store, sid)
    assert len(preserve_interrupted_turns(db, sid, store=store)) == 1
    assert preserve_interrupted_turns(db, sid, store=store) == []
    row = db.get_messages(sid)[-1]
    assert row['role'] == 'assistant'
    assert 'Investigated branches.' in row['content']
    assert 'stopped before completion' in row['content']
    assert 'PRIVATE' not in row['content']
    assert row['metadata']['query_id'] == 'lost'
    assert row['metadata']['report_url'] == '/query_log.html?id=lost'


def test_running_turn_only_snapshotted_at_explicit_stop(owner_session, tmp_path):
    db, sid, store = setup(owner_session, tmp_path)
    plant(store, sid, finished=False)
    assert preserve_interrupted_turns(db, sid, store=store) == []
    # Cutoff is a real Stop time later than header creation, but excludes late text.
    cutoff = store.run('lost')['started_at'] + 1
    store.write_batch([('event', 'lost', {'kind': 'writing', 'block_id': 'late', 't': cutoff + 10, 'text': 'LATE REPLY'})])
    assert preserve_interrupted_turns(db, sid, store=store, stopped_at=cutoff)
    assert 'LATE REPLY' not in db.get_messages(sid)[-1]['content']


def test_restart_repairs_missing_turn_and_does_not_duplicate_saved_reply(owner_session, tmp_path):
    db, sid, store = setup(owner_session, tmp_path)
    plant(store, sid, status='interrupted')
    assert preserve_interrupted_turns(db, sid, store=EventStore(store.root))
    assert 'interrupted before completion' in db.get_messages(sid)[-1]['content']
    plant(store, sid, qid='saved')
    db.add_message(sid, 'assistant', 'Already delivered', {'query_id': 'saved'})
    assert preserve_interrupted_turns(db, sid, store=store) == []


def test_recovery_is_session_scoped_and_does_not_revive_deleted_chat(owner_session, tmp_path):
    db, sid, store = setup(owner_session, tmp_path)
    other = db.create_chat_session(owner_session.user_id, 'other')
    plant(store, sid)
    assert preserve_interrupted_turns(db, other, store=store) == []
    db.delete_chat_session(sid, owner_session.user_id)
    assert preserve_interrupted_turns(db, sid, store=store) == []


def test_draft_action_cards_are_not_reactivated(owner_session, tmp_path):
    db, sid, store = setup(owner_session, tmp_path)
    plant(store, sid)
    store.write_batch([('event', 'lost', {'kind': 'writing', 'block_id': 'card', 't': 104,
        'text': 'Choose cleanup:\n<cuttle_action_form>{"fields":[]}</cuttle_action_form>\nAfter card.\n<cuttle_confirm>unfinished'})])
    preserve_interrupted_turns(db, sid, store=store)
    text = db.get_messages(sid)[-1]['content']
    assert 'After card.' in text
    assert '<cuttle_' not in text


def test_paged_events_and_missing_detail_keep_run_visible(owner_session, tmp_path):
    db, sid, store = setup(owner_session, tmp_path)
    plant(store, sid)
    store.write_batch([('event', 'lost', {'kind': 'status', 'block_id': f's{i}', 't': 104}) for i in range(510)])
    store.write_batch([('event', 'lost', {'kind': 'writing', 'block_id': 'last', 't': 105, 'text': 'Last page.'})])
    preserve_interrupted_turns(db, sid, store=store)
    assert 'Last page.' in db.get_messages(sid)[-1]['content']
    plant(store, sid, qid='damaged')
    long_text = 'Large captured text ' * 1000
    store.write_batch([('event', 'damaged', {'kind': 'writing', 'block_id': 'public', 't': 103, 'text': long_text})])
    for blob in store.root.glob('blobs/*/*.gz'):
        blob.unlink()
    preserve_interrupted_turns(db, sid, store=store)
    assert db.get_messages(sid)[-1]['metadata']['query_id'] == 'damaged'


def test_sync_replays_snapshot_after_stop_without_completing_new_stream():
    module = Path(__file__).resolve().parents[2] / 'web/js/chat/chat_pending_result.js'
    result = subprocess.run(['node', '-e', '''
const R = require(process.argv[1]);
const msg = {id: 5, role:'assistant', content:'Captured text',
 metadata:{kind:'interrupted-agent-turn', query_id:'old'}};
const fns = {hasDomId:()=>false, hasStoppedQuery:()=>true};
const claimed = R.classifyServerMessage(msg, {stopped:true}, fns);
fns.hasStoppedQuery = ()=>false;
const appended = R.classifyServerMessage(msg, {stopped:true}, fns);
console.log(JSON.stringify({claimed, appended, reply:R.isAgentReply(msg), transcript:R.agentTranscript([msg])}));
''', str(module)], capture_output=True, text=True, check=True)
    data = json.loads(result.stdout)
    assert data['claimed']['decision'] == 'claim-interrupted'
    assert data['appended']['decision'] == 'append'
    assert data['reply'] is False
    assert data['transcript'] == []


def test_authenticated_history_repairs_terminal_run_before_windowing(owner_session, tmp_path, monkeypatch):
    from api.agent_events import store as event_store
    from api.web_chat_api import app
    db, sid, store = setup(owner_session, tmp_path)
    monkeypatch.setattr(event_store, 'state_dir', lambda: store.root)
    plant(store, sid)
    client = owner_session.sign_in(app.test_client())
    response = client.get(f'/api/auth/sessions/{sid}/messages?limit=1')
    assert response.status_code == 200
    rows = response.get_json()['messages']
    assert len(rows) == 1
    assert rows[0]['metadata']['kind'] == 'interrupted-agent-turn'
    assert rows[0]['query_id'] == 'lost'
    assert client.get(f'/api/auth/sessions/{sid}/messages?after_id={rows[0]["id"]}').get_json()['messages'] == []


def test_stop_endpoint_preserves_running_activity(owner_session, tmp_path, monkeypatch):
    import time
    from api.agent_events import store as event_store
    from api.agent_events.writer import get_writer
    from api.web_chat_api import app
    db, sid, store = setup(owner_session, tmp_path)
    plant(store, sid, finished=False)
    monkeypatch.setattr(event_store, 'state_dir', lambda: store.root)
    monkeypatch.setattr(get_writer(), 'flush', lambda **kw: None)
    # Real cancel gate, fake process killing (there is no vendor execution here).
    from api import chat_delivery
    assert chat_delivery.try_begin(sid)
    client = owner_session.sign_in(app.test_client())
    response = client.post('/api/chat-cancel', json={'session_id': sid})
    assert response.status_code == 200
    assert chat_delivery.is_turn_cancelled(sid)
    rows = db.get_messages(sid)
    assert rows[-1]['metadata']['interrupted_turn']['captured_until'] <= time.time()
    assert 'Investigated branches.' in rows[-1]['content']
    assert chat_delivery.peek_result(sid) is None

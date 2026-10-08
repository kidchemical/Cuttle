"""Real SQLite shared-queue transactions; no agent execution."""
from concurrent.futures import ThreadPoolExecutor
import threading
import pytest
from api.auth_db import AuthDatabase
from api.chat_followups import Conflict


def test_simultaneous_appends_preserve_every_item(tmp_path):
    db = AuthDatabase(tmp_path / 'auth.db')
    uid = db.create_user('queue@local', 'Queue', 'local', password='x')
    sid = db.create_chat_session(uid, 'Queue')
    barrier = threading.Barrier(8)
    def append(i):
        barrier.wait()
        db.append_followup(sid, uid, {'id': f'item-{i}', 'content': str(i)})
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(append, range(8)))
    assert {x['id'] for x in db.get_followup_queue(sid, uid)} == {f'item-{i}' for i in range(8)}


def test_simultaneous_take_claims_each_item_once(tmp_path):
    db = AuthDatabase(tmp_path / 'auth.db')
    uid = db.create_user('take@local', 'Take', 'local', password='x')
    sid = db.create_chat_session(uid, 'Take')
    db.set_followup_queue(sid, uid, [{'id': 'ready', 'content': 'work'}, {'id': 'paused', 'content': 'hold', 'paused': True}])
    barrier = threading.Barrier(8)
    def take(_):
        barrier.wait()
        return db.take_followup_queue(sid, uid)['taken']
    with ThreadPoolExecutor(max_workers=8) as pool:
        result = list(pool.map(take, range(8)))
    assert [x['id'] for batch in result for x in batch] == ['ready']
    assert [x['id'] for x in db.get_followup_queue(sid, uid)] == ['paused']


def test_stale_replace_cannot_erase_remote_append_or_restore_taken_item(tmp_path):
    db = AuthDatabase(tmp_path / 'auth.db')
    uid = db.create_user('rev@local', 'Revision', 'local', password='x')
    sid = db.create_chat_session(uid, 'Revision')
    initial = db.get_followup_state(sid, uid)
    db.append_followup(sid, uid, {'id': 'remote', 'content': 'remote'})
    with pytest.raises(Conflict):
        db.set_followup_queue(sid, uid, [], expected_revision=initial['revision'])
    snapshot = db.get_followup_state(sid, uid)
    db.take_followup_queue(sid, uid)
    with pytest.raises(Conflict):
        db.set_followup_queue(sid, uid, snapshot['followups'], expected_revision=snapshot['revision'])
    assert db.get_followup_queue(sid, uid) == []


def test_revision_conflict_is_explicit_api_response(owner_session):
    from api.web_chat_api import app
    client = owner_session.sign_in(app.test_client())
    sid = owner_session.db.create_chat_session(owner_session.user_id, 'Conflict')
    initial = client.get(f'/api/auth/sessions/{sid}/followups').get_json()
    client.post(f'/api/auth/sessions/{sid}/followups', json={'followup': {'id': 'remote', 'content': 'remote'}})
    response = client.put(f'/api/auth/sessions/{sid}/followups', json={'followups': [], 'revision': initial['revision']})
    assert response.status_code == 409
    assert response.get_json()['followups'][0]['id'] == 'remote'


def test_lost_take_response_reuses_receipt_with_fresh_remaining_queue(tmp_path):
    db = AuthDatabase(tmp_path / 'auth.db')
    uid = db.create_user('receipt@local', 'Receipt', 'local', password='x')
    sid = db.create_chat_session(uid, 'Receipt')
    db.append_followup(sid, uid, {'id': 'original', 'content': 'work'})
    before = db.get_followup_state(sid, uid)
    claimed = db.mutate_followups(sid, uid, 'take', expected_revision=before['revision'], claim_id='lost-response')
    db.append_followup(sid, uid, {'id': 'later', 'content': 'later work'})
    # Retry the same lost response, even with the original (now stale) revision.
    retry = db.mutate_followups(sid, uid, 'take', expected_revision=before['revision'], claim_id='lost-response')
    assert retry['taken'] == claimed['taken']
    assert [x['id'] for x in retry['followups']] == ['later']
    assert retry['revision'] == db.get_followup_state(sid, uid)['revision']
    assert [x['id'] for x in db.get_followup_queue(sid, uid)] == ['later']
    assert db.mutate_followups(sid, uid + 100, 'take', claim_id='lost-response') is None

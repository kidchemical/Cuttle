"""Durable attention recovery, ownership, migration and stale-read fences."""
import json

from api.auth_db import AuthDatabase


def database(tmp_path):
    db = AuthDatabase(tmp_path / 'auth.db')
    uid = db.create_user('attention@local', 'Attention', 'local', password='x')
    sid = db.create_chat_session(uid, 'Attention')
    return db, uid, sid


def test_reply_unread_survives_no_observer_and_reopen(tmp_path):
    db, uid, sid = database(tmp_path)
    db.add_message(sid, 'user', 'work')
    reply = db.add_message(sid, 'assistant', 'done')
    reopened = AuthDatabase(db.db_path)
    state = reopened.get_attention(sid, uid)
    assert state['hasUnread'] and state['reply_id'] == reply
    assert reopened.get_user_chat_sessions(uid)[0]['attention'] == state
    read = reopened.mark_attention(sid, uid, {'through_id': reply})
    assert not read['hasUnread']


def test_stale_read_does_not_clear_new_reply_or_move_backward(tmp_path):
    db, uid, sid = database(tmp_path)
    first = db.add_message(sid, 'assistant', 'first')
    second = db.add_message(sid, 'assistant', 'second')
    state = db.mark_attention(sid, uid, {'through_id': first})
    assert state['hasUnread'] and state['read_id'] == first
    db.mark_attention(sid, uid, {'through_id': second})
    state = db.mark_attention(sid, uid, {'through_id': first})
    assert not state['hasUnread'] and state['read_id'] == second
    assert db.mark_attention(sid, uid + 100, {'through_id': second}) is None


def test_question_answer_and_dismiss_recover_on_another_device(tmp_path):
    db, uid, sid = database(tmp_path)
    spec = {'id': 'q', 'options': [{'id': 'yes', 'label': 'Yes'}], 'resume': True}
    card = '<cuttle_action_form_pending id="q">' + json.dumps(spec) + '</cuttle_action_form_pending>'
    reply = db.add_message(sid, 'assistant', card)
    assert db.get_attention(sid, uid)['awaitingInput']
    # A form lock is persisted through the real message mutation interface.
    db.update_message_content(reply, card.replace('id="q"', 'id="q" locked="1"'))
    assert not AuthDatabase(db.db_path).get_attention(sid, uid)['awaitingInput']
    db.update_message_content(reply, card)
    assert db.get_attention(sid, uid)['awaitingInput']
    db.add_message(sid, 'user', 'answer without clicking')
    assert not db.get_attention(sid, uid)['awaitingInput']


def test_error_manual_unread_and_one_time_legacy_import(tmp_path):
    db, uid, sid = database(tmp_path)
    reply = db.add_message(sid, 'assistant', 'failed', {'slash_command_failed': True})
    assert db.get_attention(sid, uid)['unreadIsError']
    db.mark_attention(sid, uid, {'through_id': reply})
    state = db.mark_attention(sid, uid, {'unread': True})
    assert state['hasUnread']
    db.mark_attention(sid, uid, {'through_id': reply})
    assert not db.mark_attention(sid, uid, {'legacy': True, 'unread': True})['hasUnread']


def test_upgrade_baselines_old_replies_without_hiding_new_work(tmp_path):
    db, uid, sid = database(tmp_path)
    first = db.add_message(sid, 'assistant', 'historic reply')
    conn = db._get_connection()
    conn.execute('DROP TABLE chat_attention')
    conn.commit()
    conn.close()
    upgraded = AuthDatabase(db.db_path)
    assert upgraded.get_attention(sid, uid)['read_id'] == first
    assert not upgraded.get_attention(sid, uid)['hasUnread']
    upgraded.add_message(sid, 'assistant', 'new reply')
    assert upgraded.get_attention(sid, uid)['hasUnread']


def test_completion_receipt_updates_attention_once(tmp_path):
    db, uid, sid = database(tmp_path)
    first = db.add_message_once(sid, 'done', delivery_key='test-completion')
    assert db.get_attention(sid, uid)['reply_id'] == first
    before = db.get_attention(sid, uid)
    assert db.add_message_once(sid, 'duplicate', delivery_key='test-completion') == first
    assert db.get_attention(sid, uid) == before


def test_attention_api_requires_auth_and_ownership(owner_session):
    from api.web_chat_api import app
    db = owner_session.db
    sid = db.create_chat_session(owner_session.user_id, 'API attention')
    reply = db.add_message(sid, 'assistant', 'reply')
    client = owner_session.sign_in(app.test_client())
    assert app.test_client().put(f'/api/auth/sessions/{sid}/attention', json={'through_id': reply}).status_code == 401
    assert client.put(f'/api/auth/sessions/{sid}/attention', json={'through_id': 'bad'}).status_code == 400
    assert client.put(f'/api/auth/sessions/{sid}/attention', json={'through_id': reply}).get_json()['attention']['hasUnread'] is False
    assert client.get(f'/api/auth/sessions/{sid}/messages').get_json()['attention']['read_id'] == reply


def test_metadata_error_changes_and_noop_reads_publish_only_real_changes(tmp_path):
    from api.chat_live_status import change_version
    db, uid, sid = database(tmp_path)
    reply = db.add_message(sid, 'assistant', 'reply', {'query_id': 'attention-error'})
    before = change_version()
    db.merge_message_metadata_by_query(sid, 'attention-error', {'failed': True})
    assert db.get_attention(sid, uid)['unreadIsError']
    assert change_version() > before
    db.mark_attention(sid, uid, {'through_id': reply})
    before = change_version()
    db.mark_attention(sid, uid, {'through_id': reply})
    assert change_version() == before
    assert not db.mark_attention(sid, uid, {'legacy': True, 'unread': True})['hasUnread']

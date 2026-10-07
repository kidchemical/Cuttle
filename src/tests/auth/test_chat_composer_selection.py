"""Real session API/DB shared selections, isolated from live data and providers."""
import pytest

CODEX = {'stickyChips': [{'prefix': '/codex ', 'label': 'Codex - GPT-6.1-Sol · medium', 'category': 'codex'}]}
CLAUDE = {'stickyChips': [{'prefix': '/claude ', 'label': 'Claude - Opus 5.5', 'category': 'claude'}]}


def clients(owner_session):
    from api.web_chat_api import app
    return [owner_session.sign_in(app.test_client()) for _ in range(2)]


def test_two_devices_share_selection_and_removal(owner_session):
    phone, desktop = clients(owner_session)
    sid = owner_session.db.create_chat_session(owner_session.user_id, 'Badge sync')
    first = phone.put(f'/api/auth/sessions/{sid}/composer', json=CODEX)
    assert first.status_code == 200
    shared = desktop.get(f'/api/auth/sessions/{sid}/messages').get_json()['composer_selection']
    assert shared['stickyChips'] == CODEX['stickyChips']
    second = desktop.put(f'/api/auth/sessions/{sid}/composer', json={'stickyChips': [], 'stickyCleared': True})
    assert second.get_json()['composer_selection']['revision'] > shared['revision']
    assert phone.get(f'/api/auth/sessions/{sid}/messages').get_json()['composer_selection']['stickyCleared'] is True


def test_user_send_updates_selection_but_steer_and_reply_do_not(owner_session):
    db, uid = owner_session.db, owner_session.user_id
    sid = db.create_chat_session(uid, 'Phone sends Codex')
    db.set_composer_selection(sid, uid, CLAUDE)
    codex = {'slash_command': {'chips': [{'label': 'Codex - GPT-6.1-Sol · medium', 'meta': '/codex · model gpt-6.1-sol · effort medium', 'category': 'codex'}]}}
    db.add_message(sid, 'user', '/codex work', metadata=codex)
    shared = db.get_composer_selection(sid, uid)
    assert shared['stickyChips'][0]['prefix'] == '/codex '
    db.add_message(sid, 'user', 'steer', metadata={**codex, 'steered': True})
    db.add_message(sid, 'assistant', 'fallback', metadata={'slash_command': {'chips': [{'meta': '/claude', 'label': 'Claude'}]}})
    assert db.get_composer_selection(sid, uid) == shared


def test_legacy_history_fallback_without_writing(owner_session):
    db, uid = owner_session.db, owner_session.user_id
    sid = db.create_chat_session(uid, 'Legacy')
    db.add_message(sid, 'user', '/codex work', metadata={'slash_command': {'chips': [{'meta': '/codex', 'label': 'Codex'}]}})
    conn = db._get_connection()
    conn.execute('UPDATE chat_sessions SET composer_selection = NULL, composer_revision = 0 WHERE id = ?', (sid,))
    conn.commit()
    conn.close()
    assert db.get_composer_selection(sid, uid)['stickyChips'][0]['prefix'] == '/codex '
    assert db.get_chat_session(sid, uid)['composer_selection'] is None


def test_selection_ownership_and_validation(owner_session):
    phone, _ = clients(owner_session)
    db = owner_session.db
    other = db.create_user('other@local', 'Other', 'local', password='x')
    sid = db.create_chat_session(other, 'Other chat')
    assert phone.put(f'/api/auth/sessions/{sid}/composer', json=CODEX).status_code == 404
    own = db.create_chat_session(owner_session.user_id, 'Own')
    for invalid in [None, {'stickyChips': 'bad'}, {'stickyChips': [{'prefix': '/restart '}]}, {'stickyChips': [{}, {}]}]:
        assert phone.put(f'/api/auth/sessions/{own}/composer', json=invalid).status_code == 400
    from api.web_chat_api import app
    assert app.test_client().put(f'/api/auth/sessions/{own}/composer', json=CODEX).status_code == 401


def test_live_execution_identity_reaches_single_and_shell_batch(owner_session):
    from api import chat_live_status
    phone, desktop = clients(owner_session)
    sid = owner_session.db.create_chat_session(owner_session.user_id, 'Live badge')
    codex = {'chips': [{'label': 'Codex - GPT-6.1-Sol · medium', 'meta': '/codex', 'category': 'codex'}]}
    chat_live_status.set_live_status(sid, turn=1, slash_command=codex)
    try:
        assert phone.get(f'/api/chat-live-status?session_id={sid}').get_json()['slash_command'] == codex
        batch = desktop.get(f'/api/chat-live-status-batch?session_ids={sid}').get_json()
        assert batch['statuses'][str(sid)]['slash_command'] == codex
    finally:
        chat_live_status.clear_live_status(sid, turn=1)

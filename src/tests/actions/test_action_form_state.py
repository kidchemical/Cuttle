"""Host-owned action-form lifecycle state + restart reconciliation.

These pin the design fix: one durable per-form state row that attention, history
dots and restart reconciliation derive from, so a card locked on one client (or a
restart that already finished) cannot keep another client blue or spinning.
"""
from __future__ import annotations

import json

from api import action_form_state
from api.action_forms import reconcile_restart_forms
from api.auth_db import get_auth_db


def _db():
    db = get_auth_db()
    uid = db.create_user('forms@local', 'Forms', 'local', password='x')
    sid = db.create_chat_session(uid, 'Forms')
    return db, uid, sid


def _restart_card(form_id='flask-restart-g1', **spec):
    body = {'id': form_id, 'restartFormGroup': form_id, 'locked': False,
            'options': [{'id': 'graceful', 'action': 'flask.restart'}]}
    body.update(spec)
    return f'<cuttle_action_form_pending id="{form_id}">' + json.dumps(body) + '</cuttle_action_form_pending>'


def test_parse_forms_classifies_and_honors_lock_attr():
    restart = action_form_state.parse_forms(_restart_card())
    assert restart[0]['kind'] == action_form_state.KIND_RESTART
    assert restart[0]['interactive'] is True
    assert restart[0]['restart_generation'] == 1

    locked = action_form_state.parse_forms(
        '<cuttle_action_form_pending id="q" locked="1">'
        '{"options":[{"id":"yes"}]}</cuttle_action_form_pending>'
    )
    assert locked[0]['status'] == action_form_state.STATUS_LOCKED

    watch = action_form_state.parse_forms(
        '<cuttle_action_form id="job">{"watch":{"url":"/output/x.json"},"options":[]}'
        '</cuttle_action_form>'
    )
    assert watch[0]['kind'] == action_form_state.KIND_WATCH

    # Un-rewritten cards without an id still index (positional) so a question
    # is not silently dropped from attention.
    anon = action_form_state.parse_forms(
        '<cuttle_action_form>{"options":[{"id":"yes"}]}</cuttle_action_form>'
    )
    assert anon and anon[0]['form_id'] == '__anon_0'
    assert anon[0]['kind'] == action_form_state.KIND_QUESTION


def test_message_write_syncs_host_index_and_is_exposed():
    db, uid, sid = _db()
    reply = db.add_message(sid, 'assistant', _restart_card())
    forms = db.get_action_form_state(sid)
    assert forms[str(reply)]['flask-restart-g1']['status'] == action_form_state.STATUS_OPEN
    assert forms[str(reply)]['flask-restart-g1']['message_id'] == reply

    # A lock patch through the real mutation interface updates the host index.
    db.update_message_content(reply, _restart_card(locked=True, selected=['graceful'], toast='Done'))
    forms = db.get_action_form_state(sid)
    assert forms[str(reply)]['flask-restart-g1']['status'] == action_form_state.STATUS_LOCKED
    assert forms[str(reply)]['flask-restart-g1']['selected'] == ['graceful']


def test_awaiting_input_is_scoped_to_the_last_reply(tmp_path):
    db, uid, sid = _db()
    db.add_message(sid, 'assistant', '<cuttle_action_form>{"options":[{"id":"yes"}]}</cuttle_action_form>')
    assert db.get_attention(sid, uid)['awaitingInput']
    # A later plain reply means the old question is no longer waiting on anyone.
    db.add_message(sid, 'assistant', 'all done')
    assert not db.get_attention(sid, uid)['awaitingInput']


def test_reconcile_superseded_restart_locks_history_and_clears_attention(monkeypatch):
    from api import flask_restart
    db, uid, sid = _db()
    reply = db.add_message(sid, 'assistant', _restart_card())
    monkeypatch.setattr(flask_restart, 'live_flask_generation', lambda: 1)
    assert db.get_attention(sid, uid)['awaitingInput']

    monkeypatch.setattr(flask_restart, 'live_flask_generation', lambda: 2)
    changed = reconcile_restart_forms(live_generation=2, status={'state': 'healthy', 'generation': 2})
    assert changed == 1
    # Idempotent: the terminal row no longer qualifies.
    assert reconcile_restart_forms(live_generation=2, status={'state': 'healthy', 'generation': 2}) == 0

    content = db.get_message_by_id(reply)['content']
    assert 'locked="1"' in content
    assert '"locked": true' in content
    assert 'Flask already restarted' in content
    assert not db.get_attention(sid, uid)['awaitingInput']
    assert db.get_action_form_state(sid)[str(reply)]['flask-restart-g1']['status'] != action_form_state.STATUS_OPEN


def test_reconcile_finished_restart_drops_live_restart_id_same_generation():
    """The stuck-spinner case: a completed restart in the same generation."""
    db, uid, sid = _db()
    reply = db.add_message(sid, 'assistant', _restart_card(restartId='abc123'))
    assert db.get_action_form_state(sid)[str(reply)]['flask-restart-g1']['status'] == action_form_state.STATUS_RUNNING

    changed = reconcile_restart_forms(
        live_generation=1,
        status={'state': 'healthy', 'generation': 1, 'restart_id': 'abc123'},
    )
    assert changed == 1
    content = db.get_message_by_id(reply)['content']
    assert 'restartId' not in content
    assert 'locked="1"' in content


def test_reconcile_leaves_a_live_same_generation_restart_alone():
    db, uid, sid = _db()
    db.add_message(sid, 'assistant', _restart_card())
    assert reconcile_restart_forms(
        live_generation=1, status={'state': 'waiting_for_idle', 'generation': 1}
    ) == 0
    forms = db.get_action_form_state(sid)
    assert next(iter(forms.values()))['flask-restart-g1']['status'] == action_form_state.STATUS_OPEN


def test_reconcile_keeps_fresh_card_open_after_earlier_healthy_restart():
    """A healthy restart that produced this generation must not lock a new card.

    The stale status records ``generation == live_generation`` because that is
    the generation the restart created; a card stamped with the same generation
    was authored afterwards and is still awaiting a click.
    """
    db, uid, sid = _db()
    reply = db.add_message(sid, 'assistant', _restart_card())
    changed = reconcile_restart_forms(
        live_generation=1,
        status={'state': 'healthy', 'generation': 1, 'restart_id': 'earlier-restart'},
    )
    assert changed == 0
    assert 'locked="1"' not in db.get_message_by_id(reply)['content']
    assert db.get_action_form_state(sid)[str(reply)]['flask-restart-g1']['status'] == action_form_state.STATUS_OPEN


def test_shared_restart_id_in_two_bubbles_is_indexed_and_reconciled_per_message():
    """flask-restart-gN is reused in every bubble of a generation; each is its own row."""
    db, uid, sid = _db()
    first = db.add_message(sid, 'assistant', _restart_card())
    second = db.add_message(sid, 'assistant', _restart_card(restartId='run-2'))
    forms = db.get_action_form_state(sid)
    assert set(forms) == {str(first), str(second)}
    assert forms[str(first)]['flask-restart-g1']['status'] == action_form_state.STATUS_OPEN
    assert forms[str(second)]['flask-restart-g1']['status'] == action_form_state.STATUS_RUNNING

    changed = reconcile_restart_forms(live_generation=2, status={'state': 'healthy', 'generation': 2})
    assert changed == 2
    assert 'locked="1"' in db.get_message_by_id(first)['content']
    assert 'locked="1"' in db.get_message_by_id(second)['content']
    assert 'restartId' not in db.get_message_by_id(second)['content']


def test_messages_api_exposes_host_form_state(owner_session):
    from api.web_chat_api import app

    db = owner_session.db
    sid = db.create_chat_session(owner_session.user_id, 'API forms')
    reply = db.add_message(sid, 'assistant', _restart_card())
    client = owner_session.sign_in(app.test_client())
    payload = client.get(f'/api/auth/sessions/{sid}/messages').get_json()
    assert payload['action_forms'][str(reply)]['flask-restart-g1']['status'] == action_form_state.STATUS_OPEN

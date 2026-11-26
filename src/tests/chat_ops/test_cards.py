"""Fleet cards for chats a turn messaged through chat-ops (``api.chat_ops.cards``)."""
import pytest

from api.chat_ops import cards, service, store
from tests.chat_ops.test_chat_ops import FakeHost, _ch, chats, isolated_home  # noqa: F401

TURN = 'turn0001'


@pytest.fixture(autouse=True)
def _fleet_on(monkeypatch):
    from api.subagents import fleet
    monkeypatch.setattr(fleet, 'fleet_enabled', lambda: True)
    monkeypatch.setattr(service, 'chat_agent', lambda db, row: 'claude')


def _history(db, sup, turn=TURN):
    """The supervisor's history page with one saved reply of ``turn``."""
    db.add_message(sup, 'assistant', 'Steered.', metadata={'turn_id': turn, 'query_id': turn})
    messages = db.get_messages(sup)
    cards.hydrate(db, sup, messages)
    return messages


def _cards(messages):
    return [c for m in messages for c in (m.get('metadata') or {}).get('subagents') or []]


def _send(chats, state, result=None, turn=TURN, text='go on'):
    _, _, sup, target, _ = chats
    op = store.create('send', sup, target, {'text': text, **({'turn_id': turn} if turn else {})}, state=state)
    if result is not None:
        store.finish(op['id'], state, result)
    return store.find(op['id'])


def test_send_records_the_calling_turn_only_when_given(chats):
    db, _, sup, target, _ = chats
    service.send(_ch(target), 'from a turn', actor=_ch(sup), db=db, request=FakeHost(), turn_id=TURN)
    service.send(_ch(target), 'host nudge', actor=_ch(sup), db=db, request=FakeHost())
    payloads = sorted((o['payload'] for o in store.listing(sup)), key=lambda p: p['text'])
    assert payloads == [{'text': 'from a turn', 'turn_id': TURN}, {'text': 'host nudge'}]


def test_a_queued_send_shows_as_queued_on_the_sending_reply(chats):
    db, _, sup, target, _ = chats
    _send(chats, 'pending')
    [card] = _cards(_history(db, sup))
    assert (card['handle'], card['outcome'], card['fleet']) == (_ch(target), 'queued', True)
    assert (card['label'], card['agent'], card['chat_op']) == ('worker', 'claude', 'send')
    assert card['summary'] == 'Queued for delivery'


def test_a_dispatched_send_follows_the_turn_it_started(chats):
    db, _, sup, target, _ = chats
    op = _send(chats, 'delivered', {'query_id': 'q1'})
    db.add_message(target, 'user', 'go on', metadata={'chat_op_id': op['id']})
    reply = db.add_message(target, 'assistant', '', metadata={'query_id': 'q1', 'in_progress': True})
    [card] = _cards(_history(db, sup))
    assert (card['outcome'], card['summary'], card['generating']) == ('running', 'Working…', True)

    db.update_message_content(reply, '**Fixed** the pause hitch.', metadata={
        'query_id': 'q1', 'completed_at': 1795428138.0,
        'slash_command': {'chips': [{'category': 'cursor'}]}, 'usage': {'model': 'Auto'}})
    messages = db.get_messages(sup)
    cards.hydrate(db, sup, messages)
    [card] = _cards(messages)
    assert (card['outcome'], card['summary']) == ('done', 'Fixed the pause hitch.')
    assert (card['agent'], card['model'], card['finished_at']) == ('cursor', 'Auto', '2026-11-23 10:02:18')


def test_a_steer_shows_the_agents_receipt(chats):
    db, _, sup, target, _ = chats
    op = _send(chats, 'delivered', {'steered': True, 'confirmed': True})
    db.add_message(target, 'assistant', 'Long reply about everything.', metadata={'query_id': 'q7'})
    db.add_message(target, 'user', 'stop waiting', metadata={
        'chat_op_id': op['id'], 'turn_id': 'q7',
        'steer': {'state': 'delivered', 'reply': {'text': 'The wait is cancelled.', 'at': '2026-11-23T03:12:23+00:00'}}})
    [card] = _cards(_history(db, sup))
    assert (card['outcome'], card['summary'], card['chat_op']) == ('done', 'The wait is cancelled.', 'steer')
    assert card['finished_at'] == '2026-11-23T03:12:23+00:00'


def test_an_interrupted_target_turn_is_not_reported_done(chats):
    db, _, sup, target, _ = chats
    _send(chats, 'delivered', {'query_id': 'q2'})
    db.add_message(target, 'assistant', '**Agent stopped before completion.**', metadata={
        'query_id': 'q2', 'kind': 'interrupted-agent-turn', 'interrupted_turn': {'status': 'cancelled'}})
    [card] = _cards(_history(db, sup))
    assert (card['outcome'], card['summary']) == ('cancelled', 'Stopped before completion')


@pytest.mark.parametrize('state, result, outcome, summary', [
    ('cancelled', None, 'cancelled', 'Cancelled before delivery'),
    ('failed', {'http_status': 500}, 'failed', 'Delivery failed'),
    ('unknown', {'error': 'Not delivered after 3 attempts'}, 'failed', 'Not delivered after 3 attempts'),
    ('delivered', {'recovered': True}, 'done', 'Delivered'),
    ('pending', {'waiting_on': 'target_turn'}, 'queued', "Waiting for this chat's current turn to end"),
])
def test_delivery_states_without_a_reply(chats, state, result, outcome, summary):
    _send(chats, state, result)
    [card] = _cards(_history(chats[0], chats[2]))
    assert (card['outcome'], card['summary']) == (outcome, summary)


def test_one_card_per_chat_per_turn_and_none_for_other_turns(chats):
    db, _, sup, _, _ = chats
    _send(chats, 'cancelled')
    _send(chats, 'pending')
    _send(chats, 'pending', turn='another')
    _send(chats, 'pending', turn=None)
    [card] = _cards(_history(db, sup))
    assert card['outcome'] == 'queued'


def test_a_sub_agent_card_for_the_same_chat_wins(chats):
    db, _, sup, target, _ = chats
    _send(chats, 'pending')
    db.add_message(sup, 'assistant', 'Spawned.', metadata={
        'turn_id': TURN, 'subagents': [{'id': 'child1', 'session_id': target, 'frozen': True}]})
    messages = db.get_messages(sup)
    cards.hydrate(db, sup, messages)
    assert [c['id'] for c in _cards(messages)] == ['child1']


def test_nothing_is_written_and_the_flag_gates_it(chats, monkeypatch):
    db, _, sup, _, _ = chats
    _send(chats, 'pending')
    assert len(_cards(_history(db, sup))) == 1
    assert _cards(db.get_messages(sup)) == []
    from api.subagents import fleet
    monkeypatch.setattr(fleet, 'fleet_enabled', lambda: False)
    messages = db.get_messages(sup)
    cards.hydrate(db, sup, messages)
    assert _cards(messages) == []


def test_cli_names_the_turn_only_for_the_turns_own_chat(monkeypatch):
    from api.chat_cli.cli import _calling_turn
    monkeypatch.setenv('CUTTLE_CHAT_SESSION_ID', '41')
    monkeypatch.setenv('CUTTLE_TURN_ID', TURN)
    assert _calling_turn(None) == TURN
    assert _calling_turn('CH-000041') == TURN
    assert _calling_turn('CH-000042') == ''

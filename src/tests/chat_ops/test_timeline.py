"""Supervision timeline projection."""
from api.chat_ops import store, timeline
from tests.chat_ops.test_chat_ops import chats, isolated_home, _schedule  # noqa: F401


def test_only_confirmed_sends_are_nudges(chats):
    _, _, sup, target, foreign = chats
    sched = _schedule(chats)
    for state in ('pending', 'verifying', 'failed', 'delivered', 'done'):
        store.create('send', sup, target, {'text': state}, state=state)
    store.create('send', sup, foreign, {'text': 'other'}, state='delivered')
    store.record_check(sched['id'], 'nudged', {'text': 'attempt'})
    items = timeline.project(sup)['items']
    assert {i['detail'] for i in items if i['kind'] == 'nudged'} == {'delivered', 'done'}

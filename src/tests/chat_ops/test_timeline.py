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


def test_check_and_lifecycle_event_kinds(chats):
    _, _, sup, _, _ = chats
    sched = _schedule(chats)
    store.record_check(sched['id'], 'checkpoint', {'checkpoint': 'milestone', 'lines': ['Task finished']})
    store.record_check(sched['id'], 'checkpoint', {'checkpoint': 'tests', 'lines': ['Tests passed']})
    store.record_check(sched['id'], 'stalled', {})
    store.record_check(sched['id'], 'error', {'error': 'failed'})
    store.set_paused(sched['id'], sup, True)
    store.set_paused(sched['id'], sup, False)
    events = timeline.project(sup)['items']
    assert {e['kind'] for e in events} == {'milestone', 'stalled', 'error', 'paused', 'resumed'}
    assert next(e for e in events if e['kind'] == 'milestone')['detail'] == 'Task finished'


def test_paging_ties_and_all_schedules(chats, monkeypatch):
    _, _, sup, target, _ = chats
    one = _schedule(chats)
    two = _schedule(chats)
    monkeypatch.setattr('api.chat_ops.store.time.time', lambda: 100.0)
    store.record_check(one['id'], 'stalled')
    store.record_check(two['id'], 'error')
    first = timeline.project(sup, limit=1)
    second = timeline.project(sup, limit=1, before=first['next_before'])
    assert {first['items'][0]['kind'], second['items'][0]['kind']} == {'stalled', 'error'}
    assert second['next_before'] is None
    assert len(timeline.project(sup, limit=1000)['items']) == 2
    assert all(x['chat'] == f'CH-{target:06d}' for x in first['items'] + second['items'])


def test_legacy_checks_only_before_retained_history(chats):
    from api.orchestration_telemetry import store as telemetry
    _, _, sup, target, _ = chats
    sched = _schedule(chats)
    store.record_check(sched['id'], 'stalled')
    at = store.timeline_checks(sched['id'])[0]['at']
    for timestamp in (at - 10, at + 10):
        telemetry.append('sup_check', at=timestamp, session_id=sup, parent_session_id=target,
                         batch_id=sched['id'], status='error')
    items = timeline.project(sup)['items']
    assert len([i for i in items if i['source'] == 'telemetry' and i['kind'] == 'error']) == 1
    assert len({i['id'] for i in items}) == len(items)

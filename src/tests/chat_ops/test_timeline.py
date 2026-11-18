"""Supervision timeline projection."""
from api.chat_ops import store, timeline
from tests.chat_ops.test_chat_ops import chats, isolated_home, _schedule  # noqa: F401

TEMPLATE = ('Supervision: "Release v0.8" still has open items. Continue with the next one: '
            '**Quarantine `test_render_timeout`**. If it is blocked, say what blocks it; if it is already done, check it off.')


def _items(chats, sup, **kw):
    return timeline.project(sup, db=chats[0], **kw)['items']


def test_only_confirmed_sends_are_nudges(chats):
    _, _, sup, target, foreign = chats
    sched = _schedule(chats)
    for state in ('pending', 'verifying', 'failed', 'delivered', 'done'):
        store.create('send', sup, target, {'text': state}, state=state)
    store.create('send', sup, foreign, {'text': 'other'}, state='delivered')
    store.record_check(sched['id'], 'nudged', {'text': 'attempt'})
    items = _items(chats, sup)
    assert {i['detail'] for i in items if i['kind'] == 'nudged'} == {'"delivered"', '"done"'}
    assert {i['headline'] for i in items if i['kind'] == 'nudged'} == {'Nudged worker'}


def test_check_and_lifecycle_event_kinds(chats):
    _, _, sup, _, _ = chats
    sched = _schedule(chats)
    store.record_check(sched['id'], 'checkpoint', {'checkpoint': 'milestone', 'lines': ['Task finished']})
    store.record_check(sched['id'], 'checkpoint', {'checkpoint': 'tests', 'lines': ['Tests passed']})
    store.record_check(sched['id'], 'stalled', {})
    store.record_check(sched['id'], 'error', {'error': 'failed'})
    store.set_paused(sched['id'], sup, True)
    store.set_paused(sched['id'], sup, False)
    events = _items(chats, sup)
    assert {e['kind'] for e in events} == {'milestone', 'stalled', 'error', 'paused', 'resumed'}
    milestone = next(e for e in events if e['kind'] == 'milestone')
    assert milestone['headline'] == 'Task milestone reached'
    assert milestone['detail'] == 'Task finished'


def test_stalled_names_the_worker_and_its_duration(chats):
    sched = _schedule(chats)
    store.record_check(sched['id'], 'stalled', {'stalled_min': 45, 'state': 'idle_with_work'})
    store.record_check(sched['id'], 'stalled', {'state': 'busy'})
    heads = sorted((i['headline'], i['detail']) for i in _items(chats, chats[2]) if i['kind'] == 'stalled')
    assert heads == [('worker has been stuck for 45 min', 'Idle with open Tasks items'),
                     ('worker looks stuck', 'Busy on one turn with no Tasks progress')]


def test_worker_is_named_by_its_agent_when_known(chats, monkeypatch):
    from api.chat_ops import service
    monkeypatch.setattr(service, 'chat_agent', lambda db, row: 'cursor')
    sched = _schedule(chats)
    store.record_check(sched['id'], 'stalled', {'stalled_min': 45})
    assert [i['headline'] for i in _items(chats, chats[2]) if i['kind'] == 'stalled'] == [
        'Cursor Agent has been stuck for 45 min']


def test_nudge_with_the_next_task_quotes_the_item(chats):
    _, _, sup, target, _ = chats
    _schedule(chats)
    store.create('send', sup, target, {'text': TEMPLATE}, state='delivered')
    nudge = next(i for i in _items(chats, sup) if i['kind'] == 'nudged')
    assert nudge['headline'] == 'Nudged worker with the next task'
    assert nudge['detail'] == '"Quarantine `test_render_timeout`"'


def test_care_reports_the_process_without_claiming_it_ended_it(chats):
    sched = _schedule(chats)
    gone = {'name': 'blender', 'pid': 48213, 'create_time': 1.5, 'rss': int(3.1 * 1024 ** 3)}
    store.record_check(sched['id'], 'woke', {'process_snapshot': {'measured': True, 'leftover': [gone]}})
    store.record_check(sched['id'], 'woke', {'process_snapshot': {'measured': True, 'leftover': []}})
    care = [i for i in _items(chats, chats[2]) if i['kind'] == 'care']
    assert [(c['headline'], c['detail']) for c in care] == [
        ('System care: a leftover process ended', '`blender` · PID 48213 · 3.1 GB')]


def test_guard_names_the_branch_and_the_real_verb(chats):
    sched = _schedule(chats)
    state = {'branch': 'main'}
    store.record_check(sched['id'], 'violation', {'state': state, 'branch': 'main', 'violations': ['x'],
                                                  'command': 'git push origin main', 'blocked': True})
    store.record_check(sched['id'], 'violation', {'state': state, 'branch': 'main', 'violations': ['x'],
                                                  'command': 'git commit -m wip'})
    store.record_check(sched['id'], 'violation', {'state': state, 'branch': 'main', 'violations': ['x']})
    guards = {(i['headline'], i['detail']) for i in _items(chats, chats[2]) if i['kind'] == 'guard'}
    assert guards == {('Guard: blocked a push to `main`', '`git push origin main`'),
                      ('Guard: flagged a git command on `main`', '`git commit -m wip`'),
                      ('Guard: flagged a git command on `main`', 'x')}


def test_milestone_names_the_tasks_item(chats):
    sched = _schedule(chats)
    store.record_check(sched['id'], 'checkpoint', {'checkpoint': 'milestone',
                                                   'lines': ['Checked off in **Release v0.8**: Quarantine flaky test']})
    milestone = next(i for i in _items(chats, chats[2]) if i['kind'] == 'milestone')
    assert (milestone['headline'], milestone['detail']) == ('Quarantine flaky test done', 'Checked off in `Release v0.8`')


def test_paging_ties_and_all_schedules(chats, monkeypatch):
    _, _, sup, target, _ = chats
    one = _schedule(chats)
    two = _schedule(chats)
    monkeypatch.setattr('api.chat_ops.store.time.time', lambda: 100.0)
    store.record_check(one['id'], 'stalled')
    store.record_check(two['id'], 'error')
    first = timeline.project(sup, limit=1, db=chats[0])
    second = timeline.project(sup, limit=1, before=first['next_before'], db=chats[0])
    assert {first['items'][0]['kind'], second['items'][0]['kind']} == {'stalled', 'error'}
    assert second['next_before'] is None
    assert len(_items(chats, sup, limit=1000)) == 2
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
    items = _items(chats, sup)
    assert len([i for i in items if i['source'] == 'telemetry' and i['kind'] == 'error']) == 1
    assert len({i['id'] for i in items}) == len(items)


def test_cli_timeline_prints_the_projection_headline(chats, monkeypatch, capsys):
    """Not a stub: the CLI runs the real projection and prints its headline."""
    from api import auth_db
    from api.chat_cli.cli import main
    db, _, sup, _, _ = chats
    sched = _schedule(chats)
    store.record_check(sched['id'], 'stalled', {'stalled_min': 45, 'state': 'idle_with_work'})
    monkeypatch.setattr('api.chat_cli.cli._open_db', lambda _path=None: db)
    monkeypatch.setattr(auth_db, 'get_auth_db', lambda: db)
    assert main(['timeline', f'CH-{sup:06d}']) == 0
    assert 'stalled  worker has been stuck for 45 min' in capsys.readouterr().out

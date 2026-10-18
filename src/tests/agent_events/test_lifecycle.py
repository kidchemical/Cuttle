import gzip
import json

from api.agent_events import lifecycle
from api.agent_events.store import EventStore
from api.agent_events.writer import EventWriter


def test_recovery_closes_dead_owner_preserves_live_and_unknown(tmp_path, monkeypatch):
    store = EventStore(tmp_path)
    owner = lifecycle.process_owner()
    monkeypatch.setattr(lifecycle, 'owner_alive', lambda item: item['pid'] == owner['pid'])
    store.write_batch([
        ('run', 'dead', {'capture_owner': {'pid': -1, 'started_at': 1}}),
        ('run', 'live', {'capture_owner': owner}),
        ('run', 'unknown', {}),
        ('run', 'legacy', {'user_context': {'web_ui': True}}),
        ('run', 'current', {'user_context': {'web_ui': True}}),
    ])
    with store.connection() as conn:
        conn.execute("UPDATE runs SET started_at=? WHERE query_id IN ('live','legacy','unknown')", (owner['started_at'] - 1000000,))
    assert set(lifecycle.reconcile(store, owner)) == {'dead', 'legacy'}
    assert {r['query_id'] for r in store.active_runs()} == {'live', 'unknown', 'current'}
    assert store.run('dead')['status'] == 'interrupted'
    assert store.run('dead')['metadata']['success'] is False
    assert lifecycle.reconcile(store, owner) == []


def test_pid_reuse_is_not_a_live_owner():
    owner = lifecycle.process_owner()
    assert lifecycle.owner_alive(owner)
    assert not lifecycle.owner_alive({**owner, 'started_at': owner['started_at'] - 1})


def test_writer_replays_finish_before_legacy_recovery(tmp_path):
    store = EventStore(tmp_path)
    metadata = {'user_context': {'web_ui': True}}
    store.write_batch([('run', 'finished', metadata), ('run', 'orphan', metadata)])
    with store.connection() as conn:
        conn.execute('UPDATE runs SET started_at=1')
    pending = tmp_path / 'pending'
    pending.mkdir()
    (pending / 'finish.json.gz').write_bytes(gzip.compress(json.dumps([
        ['run', 'finished', {**metadata, 'finished_at': 2, 'success': True}]
    ]).encode()))
    writer = EventWriter(tmp_path)
    try:
        writer.submit('run', 'new', {'harness': {'agent_id': 'opencode'}})
        writer.flush()
        assert store.run('finished')['status'] == 'ok'
        assert store.run('orphan')['status'] == 'interrupted'
        assert store.run('new')['metadata']['capture_owner'] == writer.owner
        assert [r['query_id'] for r in store.active_runs()] == ['new']
    finally:
        writer.close()


def test_late_metadata_cannot_reopen_run_but_finish_can_restore_outcome(tmp_path):
    store = EventStore(tmp_path)
    store.write_batch([
        ('run', 'q', {'finished_at': 2, 'success': False, 'capture_interrupted': True}),
        ('run', 'q', {'harness': {'agent_id': 'opencode'}}),
    ])
    assert store.run('q')['status'] == 'interrupted'
    store.write_batch([('run', 'q', {'finished_at': 3, 'success': True})])
    assert store.run('q')['status'] == 'ok'
    store.write_batch([('run', 'q', {'finished_at': 4, 'capture_interrupted': True})])
    assert store.run('q')['status'] == 'ok'


def test_writer_batch_keeps_finish_before_late_snapshot(tmp_path):
    writer = EventWriter(tmp_path)
    try:
        # Hold the writer in a callback while the lifecycle batch is queued.
        import threading
        entered = threading.Event()
        release = threading.Event()
        done = threading.Event()
        result = []
        def block(store):
            entered.set()
            assert release.wait(5)
        writer.queue.put(('call', block, done, result))
        assert entered.wait(5)
        try:
            writer.submit('run', 'q', {'harness': {'agent_id': 'opencode'}})
            writer.submit('run', 'q', {'finished_at': 3, 'success': True})
            writer.submit('run', 'q', {'harness': {'agent_id': 'opencode'}})
        finally:
            release.set()
        writer.flush()
        store = EventStore(tmp_path)
        assert store.run('q')['status'] == 'ok'
        assert store.active_runs() == []
    finally:
        writer.close()

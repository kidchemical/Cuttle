"""Steer receipts: the ``metadata.steer`` contract, correlation and retention.

Owner: ``api.agent_harness.steer_receipts`` (guide: ``docs/guides/AGENT_STEERING.md``).
"""
import concurrent.futures
import json
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from api.agent_harness import steer, steer_receipts
from api.agent_harness.steer_receipts import Receipts


@pytest.fixture(autouse=True)
def _clean_receipt_state():
    yield
    steer_receipts.flush(2.0)
    with steer_receipts._write_lock:
        steer_receipts._queue.clear()
    with steer_receipts._pending_lock:
        steer_receipts._pending.clear()
        steer_receipts._live.clear()


def _future(value):
    out = concurrent.futures.Future()
    out.set_result(value)
    return out


class FakeDb:
    """Stands in for ``AuthDatabase``: one batched metadata patch per write."""

    def __init__(self, path, gate=None):
        self.path = path
        self.gate = gate
        self.writes = []
        conn = sqlite3.connect(path)
        conn.execute('CREATE TABLE chat_messages (id INTEGER PRIMARY KEY, role TEXT, metadata TEXT)')
        conn.commit()
        conn.close()

    def add_message(self, _sid, role, _text, metadata=None):
        conn = sqlite3.connect(self.path)
        cur = conn.execute('INSERT INTO chat_messages (role, metadata) VALUES (?, ?)',
                           (role, json.dumps(metadata)))
        conn.commit()
        conn.close()
        return cur.lastrowid

    def patch_message_metadata(self, updates):
        if self.gate is not None:
            self.gate.wait(10)
        self.writes.append([mid for mid, _ in updates])
        conn = sqlite3.connect(self.path)
        for message_id, patch in updates:
            row = conn.execute('SELECT metadata FROM chat_messages WHERE id=?', (message_id,)).fetchone()
            meta = (json.loads(row[0]) if row and row[0] else None) or {}
            meta.update(patch)
            conn.execute('UPDATE chat_messages SET metadata=? WHERE id=?', (json.dumps(meta), message_id))
        conn.commit()
        conn.close()
        return len(updates)

    def steer_meta(self, message_id):
        conn = sqlite3.connect(self.path)
        row = conn.execute('SELECT metadata FROM chat_messages WHERE id=?', (message_id,)).fetchone()
        conn.close()
        return json.loads(row[0])['steer']


# ── the contract ────────────────────────────────────────────────────────────

def test_full_contract_shape_is_written_at_insert():
    from api.chat_coordinator import steered_user_meta

    meta = steered_user_meta({'steered': True, 'agent': 'codex', 'steer_id': 'abc'})
    assert meta['steer'] == {'v': 1, 'steer_id': 'abc', 'state': 'pending',
                             'delivered_at': None, 'mode': 'live',
                             'reply': None, 'covers': []}
    idle = steered_user_meta({'steered': True, 'agent': 'claude', 'steer_id': 'xyz',
                              'delivered': True, 'background_idle': True})
    assert idle['steer']['state'] == 'delivered' and idle['steer']['mode'] == 'detached'
    assert idle['steer']['delivered_at']


def test_snapshot_keys_never_change_through_the_life_of_a_steer():
    tracker = Receipts()
    first = tracker.begin('one')
    keys = set(steer_receipts.insert_shape(first))
    for step in (lambda: tracker.accepted(first, True),
                 lambda: tracker.echo(first),
                 lambda: tracker.bind(first, 7),
                 lambda: tracker.output_text('a1', 'Seen.'),
                 tracker.finish):
        step()
        snap = tracker.snapshot(first)
        assert keys <= set(snap)
        assert snap['state'] in ('pending', 'delivered', 'undelivered')
        assert 'delivered' not in snap  # one status field only


def test_grouped_reply_belongs_to_the_last_covered_row_only():
    tracker = Receipts()
    first, second = tracker.begin('one'), tracker.begin('two')
    tracker.accepted(first, True)
    tracker.accepted(second, True)
    tracker.echo(first)
    tracker.echo(second)
    tracker.bind(first, 10)
    tracker.bind(second, 11)
    tracker.output_text('a1', 'Both done.\n\nMore detail.')
    assert tracker.snapshot(first)['covers'] == [10, 11]
    assert tracker.snapshot(second)['covers'] == [10, 11]
    assert tracker.snapshot(first)['reply'] is None
    assert tracker.snapshot(second)['reply']['text'] == 'Both done.'


def test_a_row_bound_after_the_reply_takes_the_reply_over():
    tracker = Receipts()
    first, second = tracker.begin('one'), tracker.begin('two')
    for sid in (first, second):
        tracker.accepted(sid, True)
        tracker.echo(sid)
    tracker.bind(first, 10)
    tracker.output_text('a1', 'Both done.')
    assert tracker.snapshot(first)['reply']['text'] == 'Both done.'
    tracker.bind(second, 11)
    assert tracker.snapshot(first)['reply'] is None
    assert tracker.snapshot(second)['reply']['text'] == 'Both done.'


# ── no SQLite on the reader / teardown path ─────────────────────────────────

def test_a_blocked_writer_never_delays_the_reader_or_a_steer(tmp_path):
    gate = threading.Event()
    db = FakeDb(tmp_path / 'slow.db', gate=gate)
    sent = []

    def send(text, steer_id=None):
        sent.append((text, steer_id))
        return _future((True, None))

    token = steer.register('940', 'codex', send)
    try:
        info = steer.steer('940', 'one')
        message_id = db.add_message(940, 'user', 'one')
        steer_receipts.bind(info['steer_id'], db, message_id)
        steer_receipts.flush(0.2)  # wakes the writer, which is now blocked
        started = time.monotonic()
        # Everything a vendor reader thread, a second steer and teardown do,
        # while the writer sits inside the database:
        steer.note_echo('940', token, info['steer_id'])
        steer.note_output_start('940', token, 'a1')
        steer.note_output_text('940', token, 'a1', 'Seen.')
        second = steer.steer('940', 'two')
        steer.unregister('940', token)
        elapsed = time.monotonic() - started
        assert second['steered'] is True
        assert elapsed < 1.0, f"reader path waited on the database for {elapsed:.2f}s"
    finally:
        gate.set()
    assert steer_receipts.flush(5.0)
    assert db.writes, "the receipt is still persisted, just not on the reader thread"


def test_grouped_rows_are_written_in_one_batch(tmp_path):
    db = FakeDb(tmp_path / 'batch.db')
    tracker = Receipts(steer_receipts.changed, session='941')
    ids = [tracker.begin(text) for text in ('one', 'two')]
    for index, steer_id in enumerate(ids):
        steer_receipts.track('941', tracker, steer_id)
        tracker.accepted(steer_id, True)
        tracker.echo(steer_id)
        steer_receipts.bind(steer_id, db, db.add_message(941, 'user', 'x'))
    assert steer_receipts.flush(5.0)
    db.writes.clear()
    tracker.output_text('a1', 'Both done.')
    assert steer_receipts.flush(5.0)
    assert db.writes == [[1, 2]], "one transaction covers the grouped rows"
    assert db.steer_meta(2)['reply']['text'] == 'Both done.'
    assert db.steer_meta(1)['reply'] is None
    assert db.steer_meta(1)['covers'] == [1, 2]
    tracker.finish()
    assert steer_receipts.flush(5.0)


def test_a_failing_write_is_retried_then_given_up(tmp_path, monkeypatch):
    monkeypatch.setattr(steer_receipts, '_RETRY_SLEEP_SEC', 0.001)

    class BrokenDb:
        attempts = 0

        def patch_message_metadata(self, updates):
            BrokenDb.attempts += 1
            raise sqlite3.OperationalError('database is locked')

    tracker = Receipts(steer_receipts.changed, session='942')
    steer_id = tracker.begin('one')
    steer_receipts.track('942', tracker, steer_id)
    tracker.accepted(steer_id, True)
    steer_receipts.bind(steer_id, BrokenDb(), 5)
    assert steer_receipts.flush(5.0)
    assert BrokenDb.attempts == steer_receipts._MAX_WRITE_ATTEMPTS


# ── echo correlation ────────────────────────────────────────────────────────

def test_initial_input_echo_cannot_confirm_an_identical_steer():
    from api.agent_harness.steer_delivery import SteerDelivery

    delivery = SteerDelivery()
    receipt = delivery.begin('go', 'S1')
    delivery.acknowledge(receipt, succeeded=True)
    # The runner excludes the turn's own prompt before the matcher sees it.
    assert delivery.undelivered == ['go']
    assert [r.steer_id for r in delivery.receive('go', 'u2')] == ['S1']


def test_identical_concurrent_steers_stay_unconfirmed_while_ambiguous():
    from api.agent_harness.steer_delivery import SteerDelivery

    delivery = SteerDelivery()
    for steer_id in ('A', 'B'):
        delivery.acknowledge(delivery.begin('again', steer_id), succeeded=True)
    assert delivery.receive('again', 'u1') == []
    assert delivery.undelivered == ['again', 'again']
    assert [r.steer_id for r in delivery.receive('again', 'u2')] == ['A', 'B']
    assert delivery.undelivered == []


def test_an_echo_confirms_only_the_send_it_was_reported_for():
    tracker = Receipts()
    first, second = tracker.begin('same'), tracker.begin('same')
    tracker.accepted(first, True)
    tracker.accepted(second, True)
    tracker.echo(second)
    assert tracker.snapshot(first)['state'] == 'pending'
    assert tracker.snapshot(second)['state'] == 'delivered'


def test_echo_before_a_delivery_timeout_still_reports_steered():
    pending = concurrent.futures.Future()
    holder = {}

    def send(text, steer_id=None):
        holder['steer_id'] = steer_id
        # The CLI replays the line before its RPC reply (or never replies).
        steer.note_echo('943', holder['token'], steer_id)
        return pending

    holder['token'] = steer.register('943', 'codex', send)
    try:
        info = steer.steer('943', 'one', timeout=0.05)
        assert info['steered'] is True and info['delivered'] is True
        assert info['steer_id'] == holder['steer_id']
    finally:
        pending.set_result((True, None))
        steer.unregister('943', holder['token'])


def test_a_rejected_send_without_an_echo_is_not_a_receipt():
    token = steer.register('944', 'codex', lambda text, sid=None: _future((False, 'no turn')))
    try:
        info = steer.steer('944', 'one')
        assert info == {'steered': False, 'reason': 'rejected', 'error': 'no turn', 'agent': 'codex'}
        assert steer_receipts.retained()['pending'] == 0
    finally:
        steer.unregister('944', token)


# ── reply attribution ───────────────────────────────────────────────────────

def test_output_that_started_before_the_echo_is_not_the_reply():
    tracker = Receipts()
    tracker.output_start('a1')  # already writing when the steer lands
    steer_id = tracker.begin('one')
    tracker.accepted(steer_id, True)
    tracker.echo(steer_id)
    tracker.output_text('a1', 'Unrelated answer.')
    assert tracker.snapshot(steer_id)['reply'] is None
    tracker.output_start('a2')
    tracker.output_text('a2', 'Seen, one.')
    assert tracker.snapshot(steer_id)['reply']['text'] == 'Seen, one.'


def test_detached_acceptance_alone_never_claims_a_reply():
    tracker = Receipts()
    steer_id = tracker.begin('follow up')
    tracker.accepted(steer_id, True)
    tracker.accepted_detached(steer_id)
    # A background task's own completion follow-up, with no replay linking it.
    tracker.output_start('a1')
    tracker.output_text('a1', 'The build finished.')
    assert tracker.snapshot(steer_id)['state'] == 'delivered'
    assert tracker.snapshot(steer_id)['mode'] == 'detached'
    assert tracker.snapshot(steer_id)['reply'] is None
    # Once the CLI replays the follow-up, later output is its reply.
    tracker.echo(steer_id)
    tracker.output_start('a2')
    tracker.output_text('a2', 'Answering your message.')
    assert tracker.snapshot(steer_id)['reply']['text'] == 'Answering your message.'


def test_reply_is_the_first_paragraph_clipped():
    tracker = Receipts()
    steer_id = tracker.begin('one')
    tracker.echo(steer_id)
    tracker.output_text('a1', 'x' * 300 + '\n\nignored')
    assert tracker.snapshot(steer_id)['reply']['text'] == 'x' * 280


# ── Stop / resend fence ─────────────────────────────────────────────────────

def test_a_superseded_turn_publishes_and_persists_nothing(tmp_path):
    from api import chat_delivery

    db = FakeDb(tmp_path / 'fence.db')
    chat_delivery.try_begin(945)
    token = steer.register('945', 'codex', lambda text, sid=None: _future((True, None)))
    info = steer.steer('945', 'one')
    message_id = db.add_message(945, 'user', 'one')
    chat_delivery.end(945)
    assert chat_delivery.try_begin(945), 'Stop → re-send: a newer turn owns the chat'
    try:
        steer_receipts.bind(info['steer_id'], db, message_id)
        steer.note_echo('945', token, info['steer_id'])
        steer.unregister('945', token)
        assert steer_receipts.flush(2.0)
        assert db.writes == []
        assert steer_receipts.live('945') == []
        assert steer_receipts.snapshot(info['steer_id']) is None
        assert steer_receipts.retained() == {'pending': 0, 'sessions': 0, 'live': 0}
    finally:
        chat_delivery.end(945)
        steer_receipts.discard(info['steer_id'])


def test_live_status_only_carries_the_current_turn(tmp_path, monkeypatch):
    turn = {'query_id': 'q-one'}
    monkeypatch.setattr('api.chat_turn_row.current', lambda sid: dict(turn))
    db = FakeDb(tmp_path / 'live.db')
    token = steer.register('946', 'codex', lambda text, sid=None: _future((True, None)))
    try:
        info = steer.steer('946', 'one')
        assert info['turn_id'] == 'q-one'
        steer_receipts.bind(info['steer_id'], db, db.add_message(946, 'user', 'one'))
        rows = steer_receipts.live('946')
        assert [row['steer_id'] for row in rows] == [info['steer_id']]
        assert rows[0]['message_id'] == 1 and rows[0]['turn_id'] == 'q-one'
        turn['query_id'] = 'q-two'
        assert steer_receipts.live('946') == []
    finally:
        steer.unregister('946', token)


# ── retention ───────────────────────────────────────────────────────────────

def test_an_ended_persisted_receipt_is_released(tmp_path):
    db = FakeDb(tmp_path / 'release.db')
    token = steer.register('947', 'codex', lambda text, sid=None: _future((True, None)))
    info = steer.steer('947', 'one')
    steer_receipts.bind(info['steer_id'], db, db.add_message(947, 'user', 'one'))
    steer.note_echo('947', token, info['steer_id'])
    steer.unregister('947', token)
    assert steer_receipts.flush(5.0)
    assert db.steer_meta(1)['state'] == 'delivered'
    assert steer_receipts.retained() == {'pending': 0, 'sessions': 0, 'live': 0}
    assert steer_receipts.snapshot(info['steer_id']) is None


def test_an_unbound_receipt_is_dropped_after_the_turn_ends(monkeypatch):
    monkeypatch.setattr(steer_receipts, '_PENDING_GRACE_SEC', 0.0)
    token = steer.register('948', 'codex', lambda text, sid=None: _future((True, None)))
    info = steer.steer('948', 'one')
    assert steer_receipts.retained()['pending'] == 1
    steer.unregister('948', token)  # turn ended; no row ever carried it
    assert steer_receipts.retained()['pending'] == 0


def test_an_unauthenticated_steer_keeps_no_receipt():
    from api.chat_coordinator import steered_user_meta
    from api.chat_turn_persist import discard_steered_user_receipt

    token = steer.register('949', 'claude', lambda text, sid=None: _future((True, None)))
    try:
        info = steer.steer('949', 'one')
        assert steer_receipts.retained()['pending'] == 1
        # The unauthenticated lane writes no user row (api.chat_turn_workflow).
        discard_steered_user_receipt(steered_user_meta(info))
        assert steer_receipts.retained() == {'pending': 0, 'sessions': 0, 'live': 0}
    finally:
        steer.unregister('949', token)


def test_a_row_that_was_never_written_discards_its_receipt():
    from api.chat_coordinator import steered_user_meta
    from api.chat_turn_persist import persist_auth_user_message

    class BrokenDb:
        def add_message(self, *_a, **_k):
            raise RuntimeError('insert failed')

    token = steer.register('950', 'codex', lambda text, sid=None: _future((True, None)))
    try:
        info = steer.steer('950', 'one')
        persist_auth_user_message(BrokenDb(), 950, 'one', metadata=steered_user_meta(info),
                                  project_merge_fn=lambda m, *_: m)
        assert steer_receipts.snapshot(info['steer_id']) is None
    finally:
        steer.unregister('950', token)


def test_bind_failure_does_not_discard_an_inserted_receipt(monkeypatch):
    from api import chat_turn_persist as persist

    discarded = []

    class Db:
        def add_message(self, *_a, **_k):
            return 42

    def fail(*_a):
        raise RuntimeError('bind failed')

    monkeypatch.setattr(persist, '_bind_steer', fail)
    monkeypatch.setattr(persist, '_discard_unwritten_steer', discarded.append)
    meta = {'steer': steer_receipts.insert_shape('receipt')}
    merge = (lambda m, *_: m)
    persist.persist_auth_user_message(Db(), 1, 'hello', metadata=meta, project_merge_fn=merge)
    persist.persist_user_turn(Db(), 1, message_text='hello', history_text='hello',
                              base_meta=meta, badge_fn=lambda *a, **kw: None,
                              project_merge_fn=merge)
    assert discarded == []


# ── writer lifecycle ───────────────────────────────────────────────────────

def test_a_bound_receipt_whose_write_keeps_failing_is_released(monkeypatch):
    """N1: three failed final writes must not retain the turn for the daemon's life."""
    monkeypatch.setattr(steer_receipts, '_RETRY_SLEEP_SEC', 0.001)
    monkeypatch.setattr(steer_receipts, '_PENDING_GRACE_SEC', 0.0)

    class LockedDb:
        attempts = 0

        def patch_message_metadata(self, _updates):
            LockedDb.attempts += 1
            raise sqlite3.OperationalError('database is locked')

    tracker = Receipts(steer_receipts.changed, session='960')
    steer_id = tracker.begin('one')
    steer_receipts.track('960', tracker, steer_id)
    tracker.accepted(steer_id, True)
    steer_receipts.bind(steer_id, LockedDb(), 42)
    tracker.echo(steer_id)
    tracker.finish()
    assert steer_receipts.flush(5.0)
    assert LockedDb.attempts >= steer_receipts._MAX_WRITE_ATTEMPTS
    assert steer_receipts.retained() == {'pending': 0, 'sessions': 0, 'live': 0}


def test_the_writer_purges_a_retained_receipt_without_any_further_traffic(monkeypatch):
    """Codex 3 / N5: grace purging never depends on another write arriving."""
    monkeypatch.setattr(steer_receipts, '_PENDING_GRACE_SEC', 0.0)
    monkeypatch.setattr(steer_receipts, '_WRITER_IDLE_SEC', 0.01)
    tracker = Receipts(steer_receipts.changed, session='961')
    steer_id = tracker.begin('one')
    steer_receipts.track('961', tracker, steer_id)  # starts the writer by itself
    tracker.accepted(steer_id, True)
    tracker.finish()  # ended, never bound: no write is ever queued for it
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        with steer_receipts._pending_lock:
            if not steer_receipts._pending:
                break
        time.sleep(0.01)
    with steer_receipts._pending_lock:
        assert not steer_receipts._pending, "the idle writer never purged it"


def test_flush_cannot_report_success_while_a_failed_write_is_requeued(tmp_path, monkeypatch):
    """Codex 3: the retry requeue is atomic with the drain accounting."""
    monkeypatch.setattr(steer_receipts, '_RETRY_SLEEP_SEC', 0.0)

    class FlakyDb(FakeDb):
        def __init__(self, path):
            super().__init__(path)
            self.fail_next = False

        def patch_message_metadata(self, updates):
            if self.fail_next:
                self.fail_next = False
                raise sqlite3.OperationalError('database is locked')
            return super().patch_message_metadata(updates)

    db = FlakyDb(tmp_path / 'flaky.db')
    for round_no in range(20):
        tracker = Receipts(steer_receipts.changed, session='962')
        steer_id = tracker.begin('one')
        steer_receipts.track('962', tracker, steer_id)
        tracker.accepted(steer_id, True)
        tracker.echo(steer_id)
        message_id = db.add_message(962, 'user', 'one')
        db.fail_next = True
        steer_receipts.bind(steer_id, db, message_id)
        tracker.finish()
        deadline = time.monotonic() + 5.0
        while not steer_receipts.flush(0.0):
            assert time.monotonic() < deadline, f"round {round_no} never drained"
        # flush() said the queue is empty with nothing in flight: the write it
        # was waiting for must really have landed, not be mid-requeue.
        assert db.steer_meta(message_id)['state'] == 'delivered', f"round {round_no}"


def test_an_idle_writer_exits_and_is_restarted_on_demand(tmp_path, monkeypatch):
    """N5: no thread wakes every few seconds for the life of the daemon."""
    monkeypatch.setattr(steer_receipts, '_WRITER_IDLE_SEC', 0.01)
    db = FakeDb(tmp_path / 'idle.db')
    tracker = Receipts(steer_receipts.changed, session='963')
    steer_id = tracker.begin('one')
    steer_receipts.track('963', tracker, steer_id)
    tracker.accepted(steer_id, True)
    steer_receipts.bind(steer_id, db, db.add_message(963, 'user', 'one'))
    tracker.finish()
    assert steer_receipts.flush(5.0)
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and steer_receipts._writer is not None:
        time.sleep(0.01)
    assert steer_receipts._writer is None, "the idle writer never exited"

    # On demand it comes back and the next receipt still persists.
    tracker2 = Receipts(steer_receipts.changed, session='963')
    second = tracker2.begin('two')
    steer_receipts.track('963', tracker2, second)
    tracker2.accepted(second, True)
    tracker2.echo(second)
    second_id = db.add_message(963, 'user', 'two')
    steer_receipts.bind(second, db, second_id)
    tracker2.finish()
    assert steer_receipts.flush(5.0)
    assert db.steer_meta(second_id)['state'] == 'delivered'


def test_orderly_shutdown_drains_queued_receipt_metadata(tmp_path):
    """Codex 3: an atexit drain, so a restart does not lose final metadata."""
    db = FakeDb(tmp_path / 'exit.db')
    tracker = Receipts(steer_receipts.changed, session='964')
    steer_id = tracker.begin('one')
    steer_receipts.track('964', tracker, steer_id)
    tracker.accepted(steer_id, True)
    tracker.echo(steer_id)
    message_id = db.add_message(964, 'user', 'one')
    steer_receipts.bind(steer_id, db, message_id)
    tracker.finish()
    steer_receipts._drain_at_exit()
    assert db.steer_meta(message_id)['state'] == 'delivered'


# ── turn lifetime ───────────────────────────────────────────────────────────

def test_a_turn_that_ends_during_delivery_reports_undelivered():
    holder = {}

    def send(text, steer_id=None):
        steer.unregister('951', holder['token'])
        return _future((True, None))

    holder['token'] = steer.register('951', 'codex', send)
    info = steer.steer('951', 'too late')
    assert steer_receipts.snapshot(info['steer_id'])['state'] == 'undelivered'
    steer_receipts.discard(info['steer_id'])


def test_a_receipt_callback_that_raises_never_stops_the_turn(caplog):
    def fail(_entry):
        raise RuntimeError('storage unavailable')

    tracker = Receipts(fail)
    steer_id = tracker.begin('one')
    tracker.accepted(steer_id, True)
    tracker.echo(steer_id)
    tracker.output_text('a1', 'Seen.')
    tracker.finish()
    assert tracker.snapshot(steer_id)['reply']['text'] == 'Seen.'
    assert 'Steer receipt update failed' in caplog.text


def test_real_auth_db_patches_a_batch_without_losing_other_metadata():
    """The writer's one transaction, against the production patcher."""
    from api.auth_db import get_auth_db

    db = get_auth_db()
    user = db.create_user('steer-receipts@example.com', 'Steer', 'local')
    user_id = user['id'] if isinstance(user, dict) else user
    session_id = db.create_chat_session(user_id, 'receipts')
    first = db.add_message(session_id, 'user', 'one', metadata={'steered': True})
    second = db.add_message(session_id, 'user', 'two', metadata={'steered': True})
    assert db.patch_message_metadata([
        (first, {'steer': steer_receipts.insert_shape('a')}),
        (second, {'steer': steer_receipts.insert_shape('b')}),
    ]) == 2
    rows = {row['id']: row['metadata'] for row in db.get_messages(session_id) if row['role'] == 'user'}
    assert rows[first]['steer']['steer_id'] == 'a'
    assert rows[first]['steered'] is True, 'the patch merges, never replaces the row metadata'
    assert rows[second]['steer']['state'] == 'pending'
    assert db.patch_message_metadata([(first, {'steer': None})]) == 1
    assert 'steer' not in {row['id']: row['metadata'] for row in db.get_messages(session_id)}[first]
    assert db.patch_message_metadata([]) == 0
    assert db.patch_message_metadata([(999999, {'steer': {}})]) == 0


# ── recorded vendor streams (the runners' own parsing) ──────────────────────

def _fixture(name):
    return json.loads((Path(__file__).parent / 'fixtures' / name).read_text())


def test_recorded_codex_rapid_steers():
    events = _fixture('codex_rapid_steers.json')
    tracker = Receipts()
    ids = [tracker.begin(event['text']) for event in events if event['kind'] == 'echo']
    index = 0
    for event in events:
        if event['kind'] == 'echo':
            tracker.echo(ids[index])
            tracker.bind(ids[index], 10 + index)
            index += 1
        else:
            tracker.output_start('a1')
            tracker.output_text('a1', event['text'])
    assert tracker.snapshot(ids[0])['covers'] == [10, 11]
    assert tracker.snapshot(ids[0])['reply'] is None
    assert tracker.snapshot(ids[1])['reply']['text'].startswith('Acknowledged: amber and cobalt')


def test_recorded_claude_replay_and_assistant_block():
    from scripts.utilities.claude_cli_tool import _replay_text
    from api.agent_harness.steer_delivery import SteerDelivery

    fixture = _fixture('claude-tool.json')
    delivery = SteerDelivery()
    tracker = Receipts()
    steer_id = tracker.begin(fixture['sent'])
    tracker.accepted(steer_id, True)
    delivery.acknowledge(delivery.begin(fixture['sent'], steer_id), succeeded=True)
    for event in fixture['events']:
        replay = _replay_text(event)
        if replay is not None:
            for receipt in delivery.receive(replay, event.get('uuid')):
                tracker.echo(receipt.steer_id)
        elif event.get('type') == 'assistant' and not event.get('parent_tool_use_id'):
            message = event.get('message') or {}
            for block in message.get('content') or []:
                if block.get('type') == 'text':
                    tracker.output_text(str(message.get('id') or ''), block['text'])
                    break
    tracker.bind(steer_id, 12)
    receipt = tracker.snapshot(steer_id)
    assert receipt['state'] == 'delivered'
    assert receipt['reply']['text'].startswith('Noted, marigold.')


def test_recorded_muse_steered_item_and_agent_message():
    from api.agent_harness.steer_delivery import SteerDelivery

    fixture = _fixture('muse-tool.json')
    delivery = SteerDelivery()
    tracker = Receipts()
    steer_id = tracker.begin(fixture['sent'])
    tracker.accepted(steer_id, True)
    delivery.acknowledge(delivery.begin(fixture['sent'], steer_id), succeeded=True)
    for event in fixture['events']:
        item = (event.get('params') or {}).get('item') or {}
        if item.get('kind') == 'userMessage' and item.get('steered'):
            for receipt in delivery.receive(item.get('text') or '', item.get('itemId')):
                tracker.echo(receipt.steer_id)
        elif item.get('kind') == 'agentMessage':
            tracker.output_text(item.get('itemId'), item.get('text'))
    tracker.bind(steer_id, 13)
    receipt = tracker.snapshot(steer_id)
    assert receipt['state'] == 'delivered'
    assert receipt['reply']['text'] == 'Command finished with output `READY`.'
    assert delivery.undelivered == []

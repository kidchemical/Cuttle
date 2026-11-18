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


def _idle_detached_wait(session):
    """A running detached wait, with its card/status side effects stubbed out."""
    from api.agent_harness import background_wait as bw

    wait = bw.BackgroundWait(session, 'claude')
    wait._state = 'running'
    wait.log = lambda *a, **k: None
    wait.working = lambda *a, **k: None
    with bw._lock:
        bw._active[wait.job_id] = wait
    return wait


def _drop_detached_wait(wait):
    from api.agent_harness import background_wait as bw

    wait._state = 'done'
    with bw._lock:
        bw._active.pop(wait.job_id, None)


class FakeDb:
    """Stands in for ``AuthDatabase``: one batched metadata patch per write."""

    def __init__(self, path, gate=None):
        self.path = path
        self.gate = gate
        self.writes = []
        self.broken = False   # the database refuses writes while set
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
        if self.broken:
            raise sqlite3.OperationalError('database is locked')
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


class _MemoryDb:
    """An in-memory ``AuthDatabase`` stand-in whose every write can be scripted.

    ``before_write(attempt_no)`` runs on the writer thread before each attempt
    and may block or raise; a patch is applied all-or-nothing, like the real
    transaction.
    """

    def __init__(self, before_write=None):
        self.rows = {}
        self.before_write = before_write
        self.attempts = []     # row ids of every attempt, failed or not
        self.writes = []       # row ids of every committed transaction
        self.reply_saved = False

    def add_message(self, _sid, _role, _text, metadata=None):
        message_id = len(self.rows) + 1
        self.rows[message_id] = json.loads(json.dumps(metadata or {}))
        return message_id

    def patch_message_metadata(self, updates):
        self.attempts.append([mid for mid, _ in updates])
        if self.before_write is not None:
            self.before_write(len(self.attempts))
        for message_id, patch in updates:
            self.rows[message_id].update(json.loads(json.dumps(patch)))
        self.writes.append([mid for mid, _ in updates])
        self.reply_saved = self.reply_saved or any(
            patch['steer']['reply'] for _, patch in updates)
        return len(updates)

    def steer_meta(self, message_id):
        return self.rows[message_id]['steer']


def _echoed_group(session, labels):
    """A tracker whose steers were all echoed, so one agent message covers them."""
    tracker = Receipts(steer_receipts.changed, many=steer_receipts.changed_many,
                       session=session)
    ids = {}
    for label in labels:
        ids[label] = tracker.begin(label)
        steer_receipts.track(session, tracker, ids[label])
        tracker.accepted(ids[label], True)
        tracker.echo(ids[label])
    return tracker, ids


def _insert(db, session, label, steer_id):
    return db.add_message(session, 'user', label,
                          {'steer': steer_receipts.insert_shape(steer_id)})


def _held(db, message_id):
    row = db.steer_meta(message_id)
    return ((row['reply'] or {}).get('text'), row['covers'])


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
    tracker.output_start('a1')
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
    tracker.output_start('a1')
    tracker.output_text('a1', 'Both done.')
    assert tracker.snapshot(first)['reply']['text'] == 'Both done.'
    tracker.bind(second, 11)
    assert tracker.snapshot(first)['reply'] is None
    assert tracker.snapshot(second)['reply']['text'] == 'Both done.'


# ── no SQLite on the reader / teardown path ─────────────────────────────────

def test_a_group_is_not_released_until_every_member_is_bound(tmp_path):
    """N4 / Codex 2: a late bind must not leave two copies of one excerpt."""
    db = FakeDb(tmp_path / 'group.db')
    tracker = Receipts(steer_receipts.changed, many=steer_receipts.changed_many, session='965')
    first, second = tracker.begin('one'), tracker.begin('two')
    for steer_id in (first, second):
        steer_receipts.track('965', tracker, steer_id)
        tracker.accepted(steer_id, True)
        tracker.echo(steer_id)
    first_id = db.add_message(965, 'user', 'one')
    steer_receipts.bind(first, db, first_id)
    tracker.output_start('a1')
    tracker.output_text('a1', 'Both noted.')   # one reply covers both sends
    tracker.finish()                           # the turn ends mid-delivery
    assert steer_receipts.flush(5.0)
    assert db.steer_meta(first_id)['reply']['text'] == 'Both noted.'

    second_id = db.add_message(965, 'user', 'two')
    steer_receipts.bind(second, db, second_id)  # the second row binds late
    assert steer_receipts.flush(5.0)
    assert db.steer_meta(second_id)['reply']['text'] == 'Both noted.'
    assert db.steer_meta(first_id)['reply'] is None, 'exactly one excerpt holder'
    assert db.steer_meta(first_id)['covers'] == [first_id, second_id]
    assert db.steer_meta(second_id)['covers'] == [first_id, second_id]
    assert db.writes[-1] == [first_id, second_id], 'one transaction covers the group'
    assert steer_receipts.retained() == {'pending': 0, 'sessions': 0, 'live': 0}


def test_a_group_member_that_never_binds_still_lets_the_others_go(monkeypatch, tmp_path):
    """The grace purge is the backstop when a late bind never comes."""
    monkeypatch.setattr(steer_receipts, '_PENDING_GRACE_SEC', 0.0)
    db = FakeDb(tmp_path / 'group-gone.db')
    tracker = Receipts(steer_receipts.changed, many=steer_receipts.changed_many, session='966')
    first, second = tracker.begin('one'), tracker.begin('two')
    for steer_id in (first, second):
        steer_receipts.track('966', tracker, steer_id)
        tracker.accepted(steer_id, True)
        tracker.echo(steer_id)
    steer_receipts.bind(first, db, db.add_message(966, 'user', 'one'))
    tracker.output_start('a1')
    tracker.output_text('a1', 'Both noted.')
    tracker.finish()
    assert steer_receipts.flush(5.0)
    assert steer_receipts.retained() == {'pending': 0, 'sessions': 0, 'live': 0}


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
    tracker.output_start('a1')
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


def test_a_held_echo_can_never_confirm_a_later_send():
    """Codex 4: evidence is scoped to the sends outstanding when it arrived."""
    from api.agent_harness.steer_delivery import SteerDelivery

    delivery = SteerDelivery()
    first = delivery.begin('same', 'A')
    second = delivery.begin('same', 'B')
    delivery.acknowledge(first, succeeded=True)
    delivery.acknowledge(second, succeeded=True)
    assert delivery.receive('same', 'u1') == [], 'either send could own this echo'
    # B's RPC errors; its held echo is proof B was read, not evidence for A.
    assert delivery.acknowledge(second, succeeded=False) is True
    assert second.received is True
    assert delivery.undelivered == ['same'], 'A is still unconfirmed'

    # A third identical send echoes: that echo is its own, and A stays unproven.
    third = delivery.begin('same', 'C')
    delivery.acknowledge(third, succeeded=True)
    assert delivery.receive('same', 'u2') == [], 'A and C cannot share one echo'
    assert delivery.undelivered == ['same', 'same']
    assert first.received is False and third.received is False


def test_a_rejection_rechecks_the_held_echo():
    """N3: a held echo with one candidate left confirms that candidate."""
    from api.agent_harness.steer_delivery import SteerDelivery

    confirmed = []
    delivery = SteerDelivery(confirmed=lambda rs: confirmed.extend(r.steer_id for r in rs))
    first = delivery.begin('same', 'A')
    second = delivery.begin('same', 'B')
    delivery.acknowledge(first, succeeded=True)
    assert delivery.receive('same', 'u1') == []
    # B is rejected: the one held echo can only have been B's own line.
    assert delivery.acknowledge(second, succeeded=False) is True
    assert confirmed == ['B']
    assert delivery.received_count == 1
    assert delivery.undelivered == ['same'], "A was never echoed"


def test_the_closing_notice_counts_identical_sends_and_never_asks_for_a_resend():
    """Codex 4: unconfirmed is not "send it again" — that would run it twice."""
    from api.agent_harness.steer_delivery import SteerDelivery

    delivery = SteerDelivery()
    for steer_id in ('A', 'B', 'C'):
        delivery.acknowledge(delivery.begin('same', steer_id), succeeded=True)
    assert delivery.receive('same', 'u1') == []
    notice = delivery.closing_notice('Codex')
    assert 'it read 3 of 3 identical follow-ups' in notice
    assert 'Delivery is not confirmed' in notice
    assert 'again' not in notice.lower()

    one = SteerDelivery()
    one.acknowledge(one.begin('fix the typo', 'A'), succeeded=True)
    single = one.closing_notice('Claude Code')
    assert single.startswith('Claude Code ended this turn without confirming it read your follow-up:')
    assert 'again' not in single.lower()
    assert SteerDelivery().closing_notice('Codex') is None


def test_identical_sends_are_confirmed_once_each_has_its_own_echo():
    """Three identical sends, three echoes: every one is accounted for."""
    from api.agent_harness.steer_delivery import SteerDelivery

    delivery = SteerDelivery()
    first = delivery.begin('same', 'A')
    second = delivery.begin('same', 'B')
    delivery.acknowledge(first, succeeded=True)
    delivery.acknowledge(second, succeeded=True)
    assert delivery.receive('same', 'u1') == []       # scope {A, B}
    third = delivery.begin('same', 'C')
    delivery.acknowledge(third, succeeded=True)
    assert delivery.receive('same', 'u2') == []       # scope {A, B, C}
    ids = sorted(r.steer_id for r in delivery.receive('same', 'u3'))
    assert ids == ['A', 'B', 'C']
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
    # Accepted input is not proven delivery: pending until the CLI replays it.
    assert tracker.snapshot(steer_id)['state'] == 'pending'
    assert tracker.snapshot(steer_id)['mode'] == 'detached'
    assert tracker.snapshot(steer_id)['reply'] is None
    # Once the CLI replays the follow-up, later output is its reply.
    tracker.echo(steer_id)
    tracker.output_start('a2')
    tracker.output_text('a2', 'Answering your message.')
    assert tracker.snapshot(steer_id)['reply']['text'] == 'Answering your message.'


def test_output_without_an_observed_start_claims_nothing():
    """Codex 5: no completion-time fallback — an unordered item is not a reply."""
    tracker = Receipts()
    steer_id = tracker.begin('one')
    tracker.accepted(steer_id, True)
    tracker.echo(steer_id)
    tracker.output_text('a1', 'Could be from before the echo.')  # no output_start
    assert tracker.snapshot(steer_id)['reply'] is None
    # The same item, once its start is known to be after the echo, is the reply.
    tracker.output_start('a2')
    tracker.output_text('a2', 'Answering your note.')
    assert tracker.snapshot(steer_id)['reply']['text'] == 'Answering your note.'


def test_detached_acceptance_is_pending_until_replay_and_undelivered_on_exit():
    """Codex 5: acceptance into a background run is input, not confirmed delivery."""
    from api.agent_harness.background_wait import steer_detached
    from api.agent_harness.steer_delivery import SteerDelivery

    wait = _idle_detached_wait(9470)
    delivery = SteerDelivery()
    sends = []

    def send(text, steer_id=None):
        sends.append(delivery.begin(text, steer_id))
        delivery.acknowledge(sends[-1], succeeded=True)  # stdin write accepted
        return _future((True, None))

    token = steer.register('9470', 'claude', send)
    wait.claim_steer(token)
    try:
        info = steer_detached(9470, 'claude', 'status?')
        assert info and info['steered'] is True and info['background_idle'] is True
        assert info.get('delivered') is not True, '"delivered" means a confirmed echo'
        snap = steer_receipts.snapshot(info['steer_id'])
        assert snap['mode'] == 'detached' and snap['state'] == 'pending'
        # Claude's own closing notice calls the same input unread: one matcher.
        assert delivery.undelivered == ['status?']

        # The CLI replays it: now it is delivered.
        steer.note_echo('9470', token, info['steer_id'])
        assert steer_receipts.snapshot(info['steer_id'])['state'] == 'delivered'
        assert delivery.accepted_count == 1
    finally:
        steer.unregister('9470', token)
        _drop_detached_wait(wait)

    # A second run that exits before any replay: undelivered, like the notice.
    wait2 = _idle_detached_wait(9471)
    quiet = SteerDelivery()
    token2 = steer.register('9471', 'claude', lambda text, sid=None: (
        quiet.acknowledge(quiet.begin(text, sid), succeeded=True), _future((True, None)))[1])
    wait2.claim_steer(token2)
    try:
        info2 = steer_detached(9471, 'claude', 'anything new?')
        assert steer_receipts.snapshot(info2['steer_id'])['state'] == 'pending'
        steer.unregister('9471', token2)  # the CLI exits with it unread
        assert quiet.closing_notice('Claude Code')
        assert steer_receipts.snapshot(info2['steer_id']) is None or \
            steer_receipts.snapshot(info2['steer_id'])['state'] == 'undelivered'
    finally:
        steer_receipts.discard(info2['steer_id'])
        _drop_detached_wait(wait2)


def test_reply_is_the_first_paragraph_clipped():
    tracker = Receipts()
    steer_id = tracker.begin('one')
    tracker.echo(steer_id)
    tracker.output_start('a1')
    tracker.output_text('a1', 'x' * 300 + '\n\nignored')
    assert tracker.snapshot(steer_id)['reply']['text'] == 'x' * 280


# ── Stop / resend fence ─────────────────────────────────────────────────────

def test_a_superseded_turn_stops_publishing_and_writes_one_terminal_state(tmp_path):
    """N2: the user's own row is never left on the non-terminal ``pending``."""
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
        # An echo observed only after Stop is not a fact of this turn.
        steer.note_echo('945', token, info['steer_id'])
        steer.unregister('945', token)
        assert steer_receipts.flush(2.0)
        assert db.steer_meta(message_id)['state'] == 'undelivered'
        assert db.steer_meta(message_id)['delivered_at'] is None
        assert steer_receipts.live('945') == [], 'a superseded receipt is not published'
        assert steer_receipts.retained() == {'pending': 0, 'sessions': 0, 'live': 0}
    finally:
        chat_delivery.end(945)
        steer_receipts.discard(info['steer_id'])


def test_a_confirmed_echo_before_stop_is_kept_when_the_turn_is_superseded(tmp_path):
    """N2: Stop then re-send must not turn a delivered row back into ``pending``."""
    from api import chat_delivery

    db = FakeDb(tmp_path / 'keep.db')
    chat_delivery.try_begin(9450)
    token = steer.register('9450', 'codex', lambda text, sid=None: _future((True, None)))
    info = steer.steer('9450', 'one')
    message_id = db.add_message(9450, 'user', 'one')
    steer_receipts.bind(info['steer_id'], db, message_id)
    steer.note_echo('9450', token, info['steer_id'])  # the agent read it
    try:
        chat_delivery.end(9450)
        assert chat_delivery.try_begin(9450), 'Stop → re-send'
        steer.unregister('9450', token)  # the old runner finishes afterwards
        assert steer_receipts.flush(2.0)
        saved = db.steer_meta(message_id)
        assert saved['state'] == 'delivered' and saved['delivered_at']
    finally:
        chat_delivery.end(9450)
        steer_receipts.discard(info['steer_id'])


def test_an_excerpt_captured_before_the_turn_ended_survives_a_late_bind(tmp_path, monkeypatch):
    """Codex 1 (inverse): a normal turn end plus a new turn must not drop the excerpt."""
    turn = {'query_id': 'q-one'}
    monkeypatch.setattr('api.chat_turn_row.current', lambda sid: dict(turn))
    db = FakeDb(tmp_path / 'late.db')
    token = steer.register('9451', 'codex', lambda text, sid=None: _future((True, None)))
    info = steer.steer('9451', 'one')
    steer.note_echo('9451', token, info['steer_id'])
    steer.note_output_start('9451', token, 'a1')
    steer.note_output_text('9451', token, 'a1', 'Included amber.')
    steer.unregister('9451', token)      # the turn ends normally
    turn['query_id'] = 'q-two'           # and another turn starts
    message_id = db.add_message(9451, 'user', 'one')
    steer_receipts.bind(info['steer_id'], db, message_id)  # the HTTP request binds late
    assert steer_receipts.flush(2.0)
    saved = db.steer_meta(message_id)
    assert saved['state'] == 'delivered'
    assert saved['reply']['text'] == 'Included amber.'
    assert saved['covers'] == [message_id]


def test_an_observation_made_after_stop_is_never_recorded(tmp_path):
    """Codex 1: the old CLI's later output is not this receipt's reply."""
    from api import chat_delivery

    db = FakeDb(tmp_path / 'stale.db')
    chat_delivery.try_begin(9452)
    token = steer.register('9452', 'codex', lambda text, sid=None: _future((True, None)))
    info = steer.steer('9452', 'one')
    message_id = db.add_message(9452, 'user', 'one')
    steer_receipts.bind(info['steer_id'], db, message_id)
    try:
        chat_delivery.end(9452)
        assert chat_delivery.try_begin(9452), 'Stop → re-send'
        steer.note_output_start('9452', token, 'a1')
        steer.note_output_text('9452', token, 'a1', 'Still running from the old turn.')
        steer.note_echo('9452', token, info['steer_id'])
        steer.unregister('9452', token)
        assert steer_receipts.flush(2.0)
        saved = db.steer_meta(message_id)
        assert saved['reply'] is None and saved['state'] == 'undelivered'
    finally:
        chat_delivery.end(9452)
        steer_receipts.discard(info['steer_id'])


def test_a_detached_send_only_reaches_its_own_registration():
    """Codex 1: a replacement run must never receive the wait's steer."""
    from api.agent_harness.background_wait import steer_detached

    first, second = [], []
    wait = _idle_detached_wait(9453)
    token = steer.register('9453', 'claude',
                           lambda text, sid=None: (first.append(text), _future((True, None)))[1])
    wait.claim_steer(token)
    try:
        # Sanity: the wait's own registration does deliver.
        assert steer_detached(9453, 'claude', 'first') is not None
        assert first == ['first'] and second == []
        replacement = steer.register('9453', 'claude',
                                     lambda text, sid=None: (second.append(text), _future((True, None)))[1])
        wait.steer_token = token  # the wait still believes it owns the channel
        assert steer_detached(9453, 'claude', 'hello') is None
        assert first == ['first'] and second == [], 'the swapped-in CLI is not the wait\'s run'
        steer.unregister('9453', replacement)
    finally:
        steer.unregister('9453', token)
        _drop_detached_wait(wait)


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
    tracker.output_start('a1')
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
    # This trial was recorded before output boundaries were captured, so it
    # holds no ``message_start`` and no message id: without an observed start
    # the block cannot be ordered against the echo, and nothing claims it.
    # (The live reader gets those boundaries from
    # ``--include-partial-messages``; ordering is covered against the real
    # reader in test_agent_steer.py.)
    assert receipt['reply'] is None


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
            # The recording kept only ``item/completed``; the live reader also
            # notes the matching ``item/started`` boundary for this itemId
            # (muse_serve_turn.py), which is what orders it after the echo.
            tracker.output_start(item.get('itemId'))
            tracker.output_text(item.get('itemId'), item.get('text'))
    tracker.bind(steer_id, 13)
    receipt = tracker.snapshot(steer_id)
    assert receipt['state'] == 'delivered'
    assert receipt['reply']['text'] == 'Command finished with output `READY`.'
    assert delivery.undelivered == []


# ── explicit rejection vs ambiguous failure ─────────────────────────────────

def test_an_explicit_vendor_rejection_credits_the_echo_to_the_other_send():
    """Codex final 1: a refusal is proof the send was NOT read.

    Identical A and B outstanding, one echo. B's RPC answers with the vendor's
    own rejection, so the echo can only have been A's line: A is delivered, B
    is not, and B's ack reports failure so the client keeps it on the queue.
    """
    from api.agent_harness.steer_delivery import SteerDelivery

    confirmed = []
    delivery = SteerDelivery(confirmed=lambda rs: confirmed.extend(r.steer_id for r in rs))
    first = delivery.begin('same', 'A')
    second = delivery.begin('same', 'B')
    assert delivery.acknowledge(first, succeeded=True) is True
    assert delivery.receive('same', 'u1') == [], 'either send could own this echo'

    assert delivery.acknowledge(second, succeeded=False, refused=True) is False
    assert confirmed == ['A'], 'the echo belongs to the send that was accepted'
    assert first.received is True and second.received is False
    assert second.rejected is True
    assert delivery.undelivered == [], 'A is confirmed, B was never accepted'
    assert delivery.accepted_count == 1


def test_an_ambiguous_failure_still_claims_its_own_held_echo():
    """A timeout/transport error leaves acceptance unknown: no double send."""
    from api.agent_harness.steer_delivery import SteerDelivery

    delivery = SteerDelivery()
    first = delivery.begin('same', 'A')
    second = delivery.begin('same', 'B')
    delivery.acknowledge(first, succeeded=True)
    assert delivery.receive('same', 'u1') == []
    # No vendor answer at all: the held echo may well have been B's own line.
    assert delivery.acknowledge(second, succeeded=False) is True
    assert second.received is True and first.received is False
    assert delivery.undelivered == ['same']


def test_only_the_vendors_own_error_answer_counts_as_a_refusal():
    """The runners' discriminator: a synthesized local failure is ambiguous."""
    from api.agent_harness.steer_delivery import vendor_refused

    assert vendor_refused({'code': -32602, 'message': 'no such turn'}) is True
    assert vendor_refused({'message': 'event loop closed: ...'}) is False
    assert vendor_refused({'message': 'server connection closed'}) is False
    assert vendor_refused(None) is False
    assert vendor_refused('boom') is False


def test_a_late_bind_after_a_given_up_write_never_duplicates_the_excerpt(tmp_path, monkeypatch):
    """Codex final 2: one row ever holds the excerpt, give-up or not.

    The excerpt is saved on the first row while the second is unbound; every
    write at turn finish then fails and is given up. The group is still
    retained whole, so the second row binding afterwards rewrites **both** rows
    in one transaction: the excerpt moves, it is never copied.
    """
    monkeypatch.setattr(steer_receipts, '_RETRY_SLEEP_SEC', 0.001)
    db = FakeDb(tmp_path / 'given-up.db')
    tracker = Receipts(steer_receipts.changed, many=steer_receipts.changed_many,
                       session='967')
    first, second = tracker.begin('one'), tracker.begin('two')
    for steer_id in (first, second):
        steer_receipts.track('967', tracker, steer_id)
        tracker.accepted(steer_id, True)
        tracker.echo(steer_id)
    first_id = db.add_message(967, 'user', 'one')
    steer_receipts.bind(first, db, first_id)
    tracker.output_start('a1')
    tracker.output_text('a1', 'Both noted.')     # one excerpt covers both sends
    assert steer_receipts.flush(5.0)
    assert db.steer_meta(first_id)['reply']['text'] == 'Both noted.'

    db.broken = True                             # the turn ends, writes fail 3x
    tracker.finish()
    assert steer_receipts.flush(5.0)
    assert steer_receipts.snapshot(first) is not None, 'the group is retained whole'
    assert db.steer_meta(first_id)['reply']['text'] == 'Both noted.'

    db.broken = False                            # writes come back
    second_id = db.add_message(967, 'user', 'two')
    steer_receipts.bind(second, db, second_id)   # the second row binds late
    assert steer_receipts.flush(5.0)

    late, early = db.steer_meta(second_id), db.steer_meta(first_id)
    assert late['reply']['text'] == 'Both noted.', 'the last covered row carries it'
    assert early['reply'] is None, 'and the earlier row gave it up in the same write'
    assert early['covers'] == late['covers'] == [first_id, second_id]
    assert early['state'] == late['state'] == 'delivered'
    assert db.writes[-1] == [first_id, second_id], 'one transaction covers the group'
    assert steer_receipts.retained() == {'pending': 0, 'sessions': 0, 'live': 0}


# ── detached sends: identity and mode ───────────────────────────────────────

def test_a_wait_whose_registration_is_gone_never_reaches_a_replacement():
    """Codex final 3a: teardown clears the wait's token before the send.

    Claude's ``_close_input`` unregisters and clears ``wait.steer_token``. A
    send through the still-running wait afterwards used to pass
    ``expect_token=None`` — no expectation at all — so it reached whatever CLI
    had registered since and came back stamped with the old turn id.
    """
    from api.agent_harness.background_wait import steer_detached

    wait = _idle_detached_wait(9454)
    old, replacement_writes = [], []
    token = steer.register('9454', 'claude',
                           lambda text, sid=None: (old.append(text), _future((True, None)))[1])
    wait.claim_steer(token)
    # Claude teardown order: unregister, drop the wait's token, close stdin.
    steer.unregister('9454', token)
    wait.claim_steer(None)
    replacement = steer.register(
        '9454', 'claude',
        lambda text, sid=None: (replacement_writes.append(text), _future((True, None)))[1])
    try:
        assert steer_detached(9454, 'claude', 'hello') is None, 'the send is refused'
        assert replacement_writes == [], "the replacement CLI's stdin is untouched"
        assert old == []
        assert steer_receipts.retained()['pending'] == 0
    finally:
        steer.unregister('9454', replacement)
        _drop_detached_wait(wait)


def test_a_superseded_idle_detached_send_keeps_its_detached_mode(tmp_path, monkeypatch):
    """Codex final 3b: the mode is a fact of the send, not a later observation."""
    from api.agent_harness.background_wait import steer_detached

    turn = {'query_id': 'q-one'}
    monkeypatch.setattr('api.chat_turn_row.current', lambda sid: dict(turn))
    db = FakeDb(tmp_path / 'detached-mode.db')
    wait = _idle_detached_wait(9455)
    token = steer.register('9455', 'claude', lambda text, sid=None: _future((True, None)))
    wait.claim_steer(token)
    try:
        turn['query_id'] = 'q-two'   # a newer turn takes the chat mid-delivery
        info = steer_detached(9455, 'claude', 'status?')
        assert info and info['steered'] is True and info['background_idle'] is True
        message_id = db.add_message(
            9455, 'user', 'status?',
            {'steer': steer_receipts.insert_shape(info['steer_id'], mode='detached')})
        steer_receipts.bind(info['steer_id'], db, message_id)
        assert steer_receipts.flush(5.0)
        saved = db.steer_meta(message_id)
        assert saved['mode'] == 'detached', 'an idle background send is never "live"'
        assert saved['state'] == 'undelivered', 'one terminal state, by its own facts'
    finally:
        steer.unregister('9455', token)
        _drop_detached_wait(wait)


def test_unrelated_traffic_cannot_move_a_groups_excerpt(tmp_path, monkeypatch):
    """Codex gate F1: the group's state is the group's, not a global LRU's.

    The excerpt is saved on the first row while the second is unbound; the
    group's terminal writes then fail and are given up. Hundreds of unrelated
    receipts come and go before the second row binds, inside its grace period.
    The outcome must be the same as with no other traffic at all: the excerpt
    moves to the last covered row, and one row holds it.
    """
    monkeypatch.setattr(steer_receipts, '_RETRY_SLEEP_SEC', 0.001)
    db = FakeDb(tmp_path / 'lru.db')
    tracker = Receipts(steer_receipts.changed, many=steer_receipts.changed_many,
                       session='968')
    first, second = tracker.begin('one'), tracker.begin('two')
    for steer_id in (first, second):
        steer_receipts.track('968', tracker, steer_id)
        tracker.accepted(steer_id, True)
        tracker.echo(steer_id)
    first_id = db.add_message(968, 'user', 'one',
                              {'steer': steer_receipts.insert_shape(first)})
    steer_receipts.bind(first, db, first_id)
    tracker.output_start('a1')
    tracker.output_text('a1', 'Both noted.')
    assert steer_receipts.flush(5.0)
    assert db.steer_meta(first_id)['reply']['text'] == 'Both noted.'

    db.broken = True                     # the turn ends, the writes fail 3x
    tracker.finish()
    assert steer_receipts.flush(5.0)

    # Unrelated chats come and go: far more releases than any bounded set holds.
    noise_db = _MemoryDb()
    noise = Receipts(steer_receipts.changed, many=steer_receipts.changed_many,
                     session='9680')
    for index in range(600):
        steer_id = noise.begin(f'noise {index}')
        steer_receipts.track('9680', noise, steer_id)
        noise.accepted(steer_id, True)
        steer_receipts.bind(steer_id, noise_db, noise_db.add_message(9680, 'user', 'n'))
    noise.finish()
    assert steer_receipts.flush(5.0)
    assert steer_receipts.retained()['pending'] == 2, 'only the group is still held'

    db.broken = False                    # writes recover, then the row binds
    second_id = db.add_message(968, 'user', 'two',
                               {'steer': steer_receipts.insert_shape(second)})
    steer_receipts.bind(second, db, second_id)
    assert steer_receipts.flush(5.0)
    late = db.steer_meta(second_id)
    assert late['reply']['text'] == 'Both noted.'
    assert late['covers'] == [first_id, second_id]
    assert late['state'] == 'delivered'
    holders = [mid for mid in (first_id, second_id) if db.steer_meta(mid)['reply']]
    assert holders == [second_id], 'exactly one excerpt holder, ever'
    assert steer_receipts.retained() == {'pending': 0, 'sessions': 0, 'live': 0}


def test_an_excerpt_no_row_ever_saved_lands_on_the_late_bound_row(tmp_path, monkeypatch):
    """Codex gate F2: a reply nothing persisted is not lost, it moves.

    Every write for the first row fails — the excerpt reaches no row and the
    receipt is given up — and the database then recovers before the second row
    binds. The group's reply has no durable holder, so the late row takes it
    rather than saving the insert shape and dropping the agent's answer.
    """
    monkeypatch.setattr(steer_receipts, '_RETRY_SLEEP_SEC', 0.001)
    db = FakeDb(tmp_path / 'never-saved.db')
    tracker = Receipts(steer_receipts.changed, many=steer_receipts.changed_many,
                       session='969')
    first, second = tracker.begin('one'), tracker.begin('two')
    for steer_id in (first, second):
        steer_receipts.track('969', tracker, steer_id)
        tracker.accepted(steer_id, True)
        tracker.echo(steer_id)
    first_id = db.add_message(969, 'user', 'one',
                              {'steer': steer_receipts.insert_shape(first)})
    steer_receipts.bind(first, db, first_id)

    db.broken = True                     # the excerpt's own write fails 3x
    tracker.output_start('a1')
    tracker.output_text('a1', 'Both noted.')
    assert steer_receipts.flush(5.0)
    tracker.finish()                     # and so does the terminal write
    assert steer_receipts.flush(5.0)
    assert db.steer_meta(first_id)['reply'] is None, 'no row carries the excerpt'

    db.broken = False                    # writes recover, then the row binds
    second_id = db.add_message(969, 'user', 'two',
                               {'steer': steer_receipts.insert_shape(second)})
    steer_receipts.bind(second, db, second_id)
    assert steer_receipts.flush(5.0)
    late = db.steer_meta(second_id)
    assert late['reply'] and late['reply']['text'] == 'Both noted.', \
        "the agent's reply must not vanish"
    assert late['covers'] == [first_id, second_id]
    assert late['state'] == 'delivered'
    holders = [mid for mid in (first_id, second_id) if db.steer_meta(mid)['reply']]
    assert holders == [second_id], 'exactly one excerpt holder, ever'
    assert steer_receipts.retained() == {'pending': 0, 'sessions': 0, 'live': 0}


# ── the group is the unit of writing ────────────────────────────────────────

def test_a_reply_no_write_ever_saved_is_not_lost_to_a_reverse_order_bind(monkeypatch):
    """Codex gate 4, zero holders: the given-up row is the last covered one.

    B's row is inserted first but binds last. A (the later row) binds, and
    every write of the excerpt and of the turn's end fails. The database
    recovers, then B binds. The excerpt belongs on A, the last covered row;
    A used to be released at give-up, so B wrote ``reply: null`` with the full
    ``covers`` and no row carried the reply at all.
    """
    monkeypatch.setattr(steer_receipts, '_RETRY_SLEEP_SEC', 0.001)
    locked = [False]

    def before_write(_attempt):
        if locked[0]:
            raise sqlite3.OperationalError('database is locked')

    db = _MemoryDb(before_write)
    tracker, ids = _echoed_group('970', 'AB')
    b = _insert(db, '970', 'B', ids['B'])            # row 1, bind delayed
    a = _insert(db, '970', 'A', ids['A'])            # row 2
    steer_receipts.bind(ids['A'], db, a)
    assert steer_receipts.flush(5.0)

    locked[0] = True
    tracker.output_start('reply')
    tracker.output_text('reply', 'Both noted.')
    assert steer_receipts.flush(5.0)
    tracker.finish()
    assert steer_receipts.flush(5.0)
    assert _held(db, a) == (None, []), 'nothing was ever saved'

    locked[0] = False
    steer_receipts.bind(ids['B'], db, b)
    assert steer_receipts.flush(5.0)
    assert _held(db, a) == ('Both noted.', [b, a])
    assert _held(db, b) == (None, [b, a])
    assert db.writes[-1] == [b, a], 'one transaction covers the group'
    assert steer_receipts.retained() == {'pending': 0, 'sessions': 0, 'live': 0}


def test_binds_during_a_failing_and_a_recovering_write_leave_one_holder(monkeypatch):
    """Codex gate 4, two holders: binds landing while writes are in flight.

    Rows B, A, C. A binds and every excerpt write fails. B binds while A's
    last terminal attempt is inside the database; that attempt then fails. C
    binds while the recovered write of A and B is inside the database, before
    it commits. A used to be released by the failed attempt, so the recovered
    write froze the excerpt on A while C's write put a second copy on C.
    """
    monkeypatch.setattr(steer_receipts, '_RETRY_SLEEP_SEC', 0.001)
    locked = [True]
    base = [None]
    last_attempt, fail_it = threading.Event(), threading.Event()
    recovering, commit_it = threading.Event(), threading.Event()

    def before_write(attempt):
        if base[0] is not None:
            turn_end_attempt = attempt - base[0]
            if turn_end_attempt == 3:
                last_attempt.set()
                assert fail_it.wait(5)
                raise sqlite3.OperationalError('last terminal attempt failed')
            if turn_end_attempt == 4:
                locked[0] = False
                recovering.set()
                assert commit_it.wait(5)
        if locked[0]:
            raise sqlite3.OperationalError('database is locked')

    db = _MemoryDb()
    tracker, ids = _echoed_group('971', 'ABC')
    b = _insert(db, '971', 'B', ids['B'])
    a = _insert(db, '971', 'A', ids['A'])
    c = _insert(db, '971', 'C', ids['C'])
    steer_receipts.bind(ids['A'], db, a)
    assert steer_receipts.flush(5.0)

    db.before_write = before_write
    tracker.output_start('reply')
    tracker.output_text('reply', 'Both noted.')
    assert steer_receipts.flush(5.0)
    assert all(_held(db, mid) == (None, []) for mid in (a, b, c))

    base[0] = len(db.attempts)
    tracker.finish()
    try:
        assert last_attempt.wait(5)
        steer_receipts.bind(ids['B'], db, b)     # during A's 3rd terminal attempt
        fail_it.set()
        assert recovering.wait(5)
        steer_receipts.bind(ids['C'], db, c)     # before the A+B write commits
    finally:
        fail_it.set()
        commit_it.set()
    assert steer_receipts.flush(5.0)

    assert db.writes[-2:] == [[b, a], [b, a, c]], 'whole-group transactions only'
    assert _held(db, c) == ('Both noted.', [b, a, c])
    assert _held(db, a) == (None, [b, a, c])
    assert _held(db, b) == (None, [b, a, c])
    assert steer_receipts.retained() == {'pending': 0, 'sessions': 0, 'live': 0}


# ── a plan must not go stale while its write is in flight ───────────────────

def _pause_first_group_read(monkeypatch, pause_sec=0.4):
    """Pause the writer inside its first read of a group, as gate 5 did.

    Returns ``(sampled, resume)``. Before round 6 the pause sat between the
    membership read and the binding read, so events landed in between. Now the
    read is inside the planning critical section: the events wait for it, and
    the pause simply runs out.
    """
    sampled, resume = threading.Event(), threading.Event()
    group = Receipts.group

    def paused(tracker, steer_id):
        members = group(tracker, steer_id)
        if not sampled.is_set():
            sampled.set()
            resume.wait(pause_sec)
        return members

    monkeypatch.setattr(Receipts, 'group', paused)
    return sampled, resume


def test_stop_while_the_writer_plans_keeps_the_confirmed_echo_and_excerpt(monkeypatch):
    """Codex gate 5, race 1: a pre-Stop membership must not meet a post-Stop flag.

    The writer samples A as a pending group of one and is paused. B binds, both
    echoes are confirmed, the agent's reply is captured, then Stop and a
    re-send supersede the turn. The old singleton snapshot used to be combined
    with the newer ``stale`` flag: A was written ``undelivered`` with no reply
    and released, and B then saved ``reply: null`` with the full ``covers``.
    """
    from api import chat_delivery

    monkeypatch.setattr(steer_receipts, '_RETRY_SLEEP_SEC', 0.001)
    turn = {'query_id': 'old-turn'}
    monkeypatch.setattr('api.chat_turn_row.current', lambda sid: dict(turn))
    db = _MemoryDb()
    session = '9801'
    assert chat_delivery.try_begin(session)
    token = steer.register(session, 'codex', lambda text, sid=None: _future((True, None)))
    a = steer.steer(session, 'A')['steer_id']
    b = steer.steer(session, 'B')['steer_id']
    row_b = _insert(db, session, 'B', b)
    row_a = _insert(db, session, 'A', a)
    sampled, resume = _pause_first_group_read(monkeypatch)
    try:
        steer_receipts.bind(a, db, row_a)
        assert sampled.wait(5)
        steer_receipts.bind(b, db, row_b)
        steer.note_echo(session, token, a)
        steer.note_echo(session, token, b)
        steer.note_output_start(session, token, 'reply')
        steer.note_output_text(session, token, 'reply', 'Both noted.')
        chat_delivery.cancel_current_turn(session)      # Stop …
        turn['query_id'] = 'replacement-turn'
        assert chat_delivery.try_begin(session)         # … and re-send
        steer.unregister(session, token)
        resume.set()
        assert steer_receipts.flush(5.0)
    finally:
        resume.set()
        steer.unregister(session, token)
        chat_delivery.end(session)
    saved_a, saved_b = db.steer_meta(row_a), db.steer_meta(row_b)
    assert saved_a['state'] == 'delivered' and saved_b['state'] == 'delivered'
    assert _held(db, row_a) == ('Both noted.', [row_b, row_a]), 'the last bound row'
    assert _held(db, row_b) == (None, [row_b, row_a])
    assert steer_receipts.retained() == {'pending': 0, 'sessions': 0, 'live': 0}


def test_stop_during_an_in_flight_write_ends_on_the_frozen_facts(monkeypatch):
    """The same supersession, landing while the old plan is inside the database."""
    from api import chat_delivery

    monkeypatch.setattr(steer_receipts, '_RETRY_SLEEP_SEC', 0.001)
    turn = {'query_id': 'old-turn'}
    monkeypatch.setattr('api.chat_turn_row.current', lambda sid: dict(turn))
    writing, commit = threading.Event(), threading.Event()

    def before_write(attempt):
        if attempt == 1:
            writing.set()
            assert commit.wait(5)

    db = _MemoryDb(before_write)
    session = '9802'
    assert chat_delivery.try_begin(session)
    token = steer.register(session, 'codex', lambda text, sid=None: _future((True, None)))
    a = steer.steer(session, 'A')['steer_id']
    b = steer.steer(session, 'B')['steer_id']
    row_b = _insert(db, session, 'B', b)
    row_a = _insert(db, session, 'A', a)
    try:
        steer_receipts.bind(a, db, row_a)
        assert writing.wait(5)                          # A: pending, alone
        steer_receipts.bind(b, db, row_b)
        steer.note_echo(session, token, a)
        steer.note_echo(session, token, b)
        steer.note_output_start(session, token, 'reply')
        steer.note_output_text(session, token, 'reply', 'Both noted.')
        chat_delivery.cancel_current_turn(session)
        turn['query_id'] = 'replacement-turn'
        assert chat_delivery.try_begin(session)
        steer.unregister(session, token)
        assert not steer_receipts.flush(0.05), 'a write is owed while one is in flight'
        commit.set()
        assert steer_receipts.flush(5.0)
    finally:
        commit.set()
        steer.unregister(session, token)
        chat_delivery.end(session)
    assert db.attempts[0] == [row_a]
    assert db.writes[-1] == [row_b, row_a], 'the newer revision was planned again'
    assert db.steer_meta(row_a)['state'] == db.steer_meta(row_b)['state'] == 'delivered'
    assert _held(db, row_a) == ('Both noted.', [row_b, row_a])
    assert _held(db, row_b) == (None, [row_b, row_a])
    assert steer_receipts.retained() == {'pending': 0, 'sessions': 0, 'live': 0}


def test_grace_expiring_during_a_write_never_drops_the_newer_revision(monkeypatch):
    """Codex gate 5, race 2: the purge must not take a group that still owes a write.

    The writer samples A (bound, no reply yet) and is paused. The grouped reply
    arrives and the turn ends with B unbound. The old write is held inside the
    database while the clock passes the grace period. That write used to settle
    A's *newer* revision, so the purge dropped the group together with the
    queued excerpt write and ``flush()`` reported done with A at ``null/[]``.
    """
    from types import SimpleNamespace

    monkeypatch.setattr(steer_receipts, '_RETRY_SLEEP_SEC', 0.001)
    offset = [0.0]
    monkeypatch.setattr(steer_receipts, 'time', SimpleNamespace(
        monotonic=lambda: time.monotonic() + offset[0], sleep=time.sleep))
    writing, commit = threading.Event(), threading.Event()

    def before_write(attempt):
        if attempt == 1:
            writing.set()
            assert commit.wait(5)

    db = _MemoryDb(before_write)
    tracker, ids = _echoed_group('9803', 'AB')
    a = _insert(db, '9803', 'A', ids['A'])
    sampled, resume = _pause_first_group_read(monkeypatch)
    try:
        steer_receipts.bind(ids['A'], db, a)
        assert sampled.wait(5)
        tracker.output_start('reply')
        tracker.output_text('reply', 'Both noted.')
        tracker.finish()                                # B never binds
        resume.set()
        assert writing.wait(5)
        offset[0] = steer_receipts._PENDING_GRACE_SEC + 1
        assert steer_receipts.retained()['pending'] == 2, 'purge leaves an owed group'
        assert not steer_receipts.flush(0.05), 'a write is owed'
    finally:
        resume.set()
        commit.set()
    assert steer_receipts.flush(5.0)
    assert db.steer_meta(a)['state'] == 'delivered'
    assert _held(db, a) == ('Both noted.', [a]), 'saved before anything is purged'
    assert steer_receipts.retained() == {'pending': 0, 'sessions': 0, 'live': 0}


def test_any_interleaving_leaves_the_excerpt_on_exactly_one_row(monkeypatch):
    """Property: 300 seeded schedules, with events landing during in-flight writes.

    Three steers are accepted. Their echoes, their row binds (in a random row
    order), the agent's one output, the turn's end, a Stop/re-send supersession
    and the clock passing the grace period are shuffled into one schedule.
    Every write attempt randomly fails, and the database may recover before the
    last event. Each event either lands freely, after a flush, or **while a
    write attempt is held inside the database** (the writer's plan is then old
    by the time it commits).

    Whatever happened, the saved rows must be one committed whole-group state:
    the excerpt on exactly one row iff a write carrying it ever succeeded, that
    row being the last of the rows the write covered, all of which list the
    same ``covers``. And ``flush()`` reporting done means no write is owed:
    unless a write was given up after its retries, every bound row equals the
    tracker's final state.
    """
    import random
    from types import SimpleNamespace

    monkeypatch.setattr(steer_receipts, '_RETRY_SLEEP_SEC', 0.0)
    offset = [0.0]
    monkeypatch.setattr(steer_receipts, 'time', SimpleNamespace(
        monotonic=lambda: time.monotonic() + offset[0], sleep=time.sleep))
    stopped = set()
    monkeypatch.setattr(steer_receipts, '_superseded',
                        lambda snapshot: snapshot.get('session') in stopped)
    gave_up = []
    give_up = steer_receipts._give_up
    monkeypatch.setattr(steer_receipts, '_give_up',
                        lambda plan: (gave_up.append(plan), give_up(plan))[1])

    def owed():
        with steer_receipts._write_lock:
            if steer_receipts._queue or steer_receipts._inflight:
                return True
        return steer_receipts._write_owed()

    rng = random.Random(20261118)
    in_flight = {'bind': 0, 'echo': 0, 'output': 0, 'finish': 0, 'stop': 0, 'grace': 0}
    strong = 0
    for case in range(300):
        fail_rate = rng.choice([0.0, 0.0, 0.3, 0.6, 0.9, 1.0])
        healed = [False]
        write_rng = random.Random(rng.random())
        gate = threading.Condition()
        closed, held = [False], [False]
        del gave_up[:]

        def before_write(_attempt, fail_rate=fail_rate, healed=healed,
                         write_rng=write_rng, gate=gate, closed=closed, held=held):
            with gate:
                held[0] = True
                gate.notify_all()
                assert gate.wait_for(lambda: not closed[0], 10)
                held[0] = False
            if not healed[0] and write_rng.random() < fail_rate:
                raise sqlite3.OperationalError('database is locked')

        def hold_a_write():
            """Close the database; True when an attempt is now held inside it."""
            with gate:
                closed[0] = True
                if owed():
                    gate.wait_for(lambda: held[0], 0.5)
                return held[0]

        def let_writes_through():
            with gate:
                closed[0] = False
                gate.notify_all()

        db = _MemoryDb(before_write)
        session = str(98000 + case)
        tracker = Receipts(steer_receipts.changed, many=steer_receipts.changed_many,
                           session=session)
        ids, rows = {}, {}
        for label in 'ABC':
            ids[label] = tracker.begin(label)
            steer_receipts.track(session, tracker, ids[label])
            tracker.accepted(ids[label], True)
        for label in rng.sample('ABC', 3):               # row id order
            rows[label] = _insert(db, session, label, ids[label])
        steps = [('bind', label) for label in 'ABC'] + [('echo', label) for label in 'ABC']
        steps += [('output', ''), ('finish', '')]
        if rng.random() < 0.5:
            steps.append(('stop', ''))
        if rng.random() < 0.5:
            steps.append(('grace', ''))
        rng.shuffle(steps)
        heal_before_last = rng.random() < 0.6
        finished = False
        try:
            for index, (step, label) in enumerate(steps):
                if index == len(steps) - 1 and heal_before_last:
                    healed[0] = True
                mode = rng.choice(['free', 'flushed', 'in flight', 'in flight'])
                if mode == 'flushed':
                    let_writes_through()
                    assert steer_receipts.flush(5.0)
                elif mode == 'in flight':
                    if hold_a_write():
                        in_flight[step] += 1
                else:
                    let_writes_through()
                if step == 'bind':
                    steer_receipts.bind(ids[label], db, rows[label])
                elif step == 'echo':
                    if not finished:     # a runner that ended reports no echo
                        tracker.echo(ids[label])
                elif step == 'output':
                    tracker.output_start('reply')
                    tracker.output_text('reply', 'All noted.')
                elif step == 'finish':
                    tracker.finish()
                    finished = True
                elif step == 'stop':
                    stopped.add(session)
                else:
                    offset[0] += steer_receipts._PENDING_GRACE_SEC + 1
                    if rng.random() < 0.5:
                        steer_receipts.retained()        # purge right now
                    else:
                        steer_receipts._wake.set()       # or let the writer
        finally:
            let_writes_through()
        assert steer_receipts.flush(5.0)

        where = f'case {case}: rows {rows}, steps {steps}, fail {fail_rate}, ' \
                f'healed {heal_before_last}, attempts {db.attempts}, writes {db.writes}'
        assert not owed(), where
        saved = {mid: db.steer_meta(mid) for mid in rows.values()}
        holders = [mid for mid, row in saved.items() if row['reply']]
        covered = sorted(mid for mid, row in saved.items() if row['covers'])
        assert len(holders) == (1 if db.reply_saved else 0), where
        if holders:
            assert saved[holders[0]]['reply']['text'] == 'All noted.', where
            assert holders[0] == covered[-1], 'the last covered row holds it: ' + where
        for mid, row in saved.items():
            assert row['covers'] == (covered if mid in covered else []), where
        if not gave_up:
            # flush() true ⇒ the saved rows are the final in-memory state.
            strong += 1
            for label, mid in rows.items():
                final = tracker.snapshot(ids[label])
                if final['message_id'] is None:
                    # Bound only after its group's grace ran out: never written.
                    assert saved[mid] == {**saved[mid], 'state': 'pending',
                                          'reply': None, 'covers': []}, where
                    continue
                expected = {key: final[key] for key in steer_receipts._CONTRACT_KEYS}
                assert saved[mid] == expected, f'{label} is not the final state: ' + where
        stopped.discard(session)
        with steer_receipts._pending_lock:
            steer_receipts._pending.clear()
            steer_receipts._live.clear()
    assert all(count >= 20 for count in in_flight.values()), in_flight
    assert strong >= 100, strong

"""Steer receipts: what became of a follow-up pushed into a live agent turn.

The transport registers a request before sending it, so an echo that arrives
before the delivery response (or before the user row exists) is not lost. A row
can be bound later; the latest snapshot is retained until then.

**The tracker only changes memory.** Vendor reader threads (``_on_line``),
``steer()`` and turn teardown never open SQLite and never wait on it: one
background writer thread persists the latest snapshot per ``steer_id``, batching
a turn's rows into a single transaction
(``AuthDatabase.patch_message_metadata``). A busy database therefore delays no
agent event loop.

Contract on the saved user row, written in full at insert
(``api.chat_coordinator.steered_user_meta``) and only ever replaced wholesale:

    metadata["steer"] = {
        "v": 1,
        "steer_id": str,
        "state": "pending" | "delivered" | "undelivered",
        "delivered_at": iso8601 | None,
        "mode": "live" | "detached",
        "reply": {"text": str, "at": iso8601} | None,
        "covers": [message_id, ...],
    }

``state`` is the only status field (no separate ``delivered`` bool; readers
derive it from ``state == "delivered"``). ``covers`` lists the message ids of
the rows that share one grouped reply, and that reply is carried by the **last**
covered row only, so a client renders it once.

Supersession: each registration pins its run identity (the turn's query id and
the delivery guard token). Once a newer turn owns the chat — Stop then re-send,
or another turn taking the chat from a detached run — the tracker **freezes**:
no vendor event after that point records a new fact, nothing is published to
live status any more, and the row the user already sees gets exactly one
terminal write carrying the facts established while the turn was still its own
(a confirmed echo, a captured excerpt). A row bound late, after its turn ended
and another began, therefore still keeps its excerpt instead of losing it to
the fence.
"""
from __future__ import annotations

import atexit
from datetime import datetime, timezone
import logging
import re
import threading
import time
import uuid

CONTRACT_VERSION = 1

_CONTRACT_KEYS = ('v', 'steer_id', 'state', 'delivered_at', 'mode', 'reply', 'covers')

PENDING = 'pending'
DELIVERED = 'delivered'
UNDELIVERED = 'undelivered'

# A bound row keeps its binding until the receipt ends and is persisted. One
# that never binds (the turn ended during delivery, so the row insert may still
# be in flight) is dropped this long after the turn ended.
_PENDING_GRACE_SEC = 60.0
# Write attempts per snapshot before the receipt is given up (and, when it has
# ended, released: nothing is retained for a row that cannot be written).
_MAX_WRITE_ATTEMPTS = 3
_RETRY_SLEEP_SEC = 0.05
# How long an idle writer waits before purging again; with nothing queued and
# nothing retained it exits, and the next ``track``/``changed`` restarts it.
_WRITER_IDLE_SEC = 5.0
# Bounded drain at interpreter exit, so an orderly restart does not lose the
# metadata still queued. Never kills or restarts anything itself.
_EXIT_DRAIN_SEC = 2.0
# Output items whose start we remember, for reply attribution.
_MAX_TRACKED_ITEMS = 200


def _now():
    return datetime.now(timezone.utc).isoformat()


def _clip(text):
    return re.split(r'\n[ \t]*\n', str(text or '').strip(), maxsplit=1)[0].strip()[:280]


def _state(entry):
    if entry['delivered_at']:
        return DELIVERED
    if entry['ended'] and entry['accepted']:
        return UNDELIVERED
    return PENDING


def insert_shape(steer_id, *, delivered=False, mode='live'):
    """The contract shape for a row being inserted, before any vendor event."""
    return {'v': CONTRACT_VERSION, 'steer_id': str(steer_id),
            'state': DELIVERED if delivered else PENDING,
            'delivered_at': _now() if delivered else None,
            'mode': mode, 'reply': None, 'covers': []}


class Receipts:
    """One live registration's receipts. Memory only; never touches the DB."""

    def __init__(self, changed=None, *, many=None, stale=None,
                 session=None, turn_id='', guard_turn=None):
        self._lock = threading.RLock()
        self._entries = []
        self._starts = {}
        self._seq = 0
        self._finished = False
        self._frozen = False
        self._changed = changed or (lambda entry: None)
        self._many = many
        self._stale = stale if stale is not None else superseded
        self.session = '' if session is None else str(session)
        self.turn_id = str(turn_id or '')
        self.guard_turn = guard_turn

    @property
    def frozen(self):
        """True once a newer turn owns the chat: facts are closed, not changed."""
        with self._lock:
            return self._frozen

    def _fresh(self):
        """False when this run no longer owns the chat, so no new fact may land.

        Checked **before** recording, so an observation made after Stop (output
        the old CLI was still producing) never becomes a fact, while everything
        established before it persists.
        """
        if self._frozen:
            return False
        try:
            if self._stale(self.session, self.turn_id, self.guard_turn):
                self._frozen = True
        except Exception:
            pass
        return not self._frozen

    # ── registration ────────────────────────────────────────────────────────
    def begin(self, text, turn_id=None):
        with self._lock:
            entry = {'steer_id': uuid.uuid4().hex, 'text': text,
                     'turn_id': str(turn_id or self.turn_id or ''),
                     'message_id': None, 'accepted': False, 'ended': self._finished,
                     'delivered_at': None, 'echo_seq': None, 'mode': 'live',
                     'group': None}
            self._entries.append(entry)
            return entry['steer_id']

    def accepted(self, steer_id, accepted):
        """Record the transport's answer. Returns True when delivery is proven.

        A rejection after the echo already arrived keeps the receipt: the agent
        read the text, so reporting failure would make the client send it twice.
        """
        with self._lock:
            entry = self._find(steer_id)
            if entry is None:
                return False
            if not accepted:
                if entry['delivered_at']:
                    return True
                self._entries.remove(entry)
                return False
            entry['accepted'] = True
            self._emit(entry)
            return bool(entry['delivered_at'])

    def accepted_detached(self, steer_id):
        """A follow-up accepted into an idle detached wait: input, not delivery.

        ``delivered`` is reserved for a confirmed echo, so the state stays
        ``pending`` under ``mode: "detached"`` until the CLI replays the
        follow-up. A run that exits before that replay ends ``undelivered``,
        which is what its closing "unread" notice says too.
        """
        with self._lock:
            entry = self._find(steer_id)
            if entry is None or not self._fresh():
                return
            entry['mode'] = 'detached'
            self._emit(entry)

    def bind(self, steer_id, message_id):
        with self._lock:
            entry = self._find(steer_id)
            if entry is None:
                return
            entry['message_id'] = message_id
            # A grouped reply belongs to the last covered row: binding a later
            # row moves it, so every member is republished — in one batch, so
            # one transaction covers the whole group.
            self._emit_many(self._group_members(entry))

    # ── vendor correlation (by steer id; never by matching text again) ──────
    def echo(self, steer_id):
        """The runner's own matcher confirmed the agent read this exact send."""
        with self._lock:
            entry = self._find(steer_id)
            if entry is None or entry['echo_seq'] is not None or not self._fresh():
                return
            entry['echo_seq'] = self._next()
            entry['delivered_at'] = entry['delivered_at'] or _now()
            self._emit(entry)

    def output_start(self, item_id):
        """An agent output item started: only later items can be its reply."""
        with self._lock:
            key = str(item_id or '')
            if not key or key in self._starts:
                return
            self._starts[key] = self._next()
            while len(self._starts) > _MAX_TRACKED_ITEMS:
                self._starts.pop(next(iter(self._starts)))

    def output_text(self, item_id, text):
        """Text of a finished agent output item: the reply of the steers it follows.

        Only an item that **started** after a steer's echo can be its reply, so
        an item already running when the steer landed never claims it. An item
        whose start was never observed cannot be ordered against an echo at
        all, so it claims nothing: there is no completion-time fallback. A
        steer that was accepted but never echoed (a detached wait's accepted
        input with no replay yet) has no echo to order against either, and
        keeps ``reply: null``.
        """
        clipped = _clip(text)
        if not clipped:
            return
        with self._lock:
            if not self._fresh():
                return
            started = self._starts.get(str(item_id or ''))
            if started is None:
                return  # no observed start: nothing can be attributed to it
            waiting = [e for e in self._entries
                       if e['echo_seq'] is not None and e['echo_seq'] < started
                       and e['group'] is None and not e['ended']]
            if not waiting:
                return
            group = {'members': waiting, 'reply': {'text': clipped, 'at': _now()}}
            for entry in waiting:
                entry['group'] = group
            self._emit_many(waiting)

    def finish(self):
        with self._lock:
            self._finished = True
            for entry in self._entries:
                entry['ended'] = True
            self._emit_many(list(self._entries))

    # ── reads ───────────────────────────────────────────────────────────────
    def snapshot(self, steer_id):
        with self._lock:
            entry = self._find(steer_id)
            return self._snapshot(entry) if entry is not None else None

    def delivered(self, steer_id):
        with self._lock:
            entry = self._find(steer_id)
            return bool(entry and entry['delivered_at'])

    # ── internals ───────────────────────────────────────────────────────────
    def _next(self):
        self._seq += 1
        return self._seq

    def _find(self, steer_id):
        return next((e for e in self._entries if e['steer_id'] == steer_id), None)

    def _group_members(self, entry):
        group = entry['group']
        return list(group['members']) if group is not None else [entry]

    def _public(self, entry):
        group = entry['group']
        covers, reply = [], None
        if group is not None:
            bound = sorted((m for m in group['members'] if m['message_id'] is not None),
                           key=lambda m: m['message_id'])
            covers = [m['message_id'] for m in bound]
            # The reply renders once, under the last covered row (the last
            # member still unbound while nothing is bound yet).
            holder = bound[-1] if bound else group['members'][-1]
            if entry is holder:
                reply = dict(group['reply'])
        return {'v': CONTRACT_VERSION, 'steer_id': entry['steer_id'],
                'state': _state(entry), 'delivered_at': entry['delivered_at'],
                'mode': entry['mode'], 'reply': reply, 'covers': covers}

    def _snapshot(self, entry):
        group = entry['group']
        binding = bool(group is not None
                       and any(m['message_id'] is None for m in group['members']))
        return {**self._public(entry), 'message_id': entry['message_id'],
                'turn_id': entry['turn_id'], 'ended': entry['ended'],
                'group_binding': binding,
                'session': self.session, 'guard_turn': self.guard_turn}

    def _emit(self, entry):
        self._emit_many([entry])

    def _emit_many(self, entries):
        """Publish these entries' snapshots as one update (one transaction)."""
        snapshots = [self._snapshot(entry) for entry in entries]
        try:
            if self._many is not None:
                stale = self._many(snapshots)
            else:
                stale = any([self._changed(snap) for snap in snapshots])
            if stale:
                self._frozen = True
        except Exception:
            logging.getLogger(__name__).exception("Steer receipt update failed")


# ── pending bindings, live status and the background writer ─────────────────
# ``_pending`` keeps a steer's binding until its row is written and its final
# snapshot saved; ``_live`` is what ``/api/chat-live-status`` shows for the
# current turn. Both maps are taken while the emitting tracker's lock is held
# (the lock order is always tracker → these, so it cannot deadlock), and
# nothing under them touches the database: the writer thread does that.
# The writer's exit decision is the one place that nests ``_write_lock`` →
# ``_pending_lock``; nothing anywhere nests them the other way round, and
# neither critical section calls out of this module.
_pending_lock = threading.Lock()
_pending = {}
_live = {}

_write_lock = threading.Lock()
_queue = {}
_inflight = 0
_wake = threading.Event()
_writer = None


def track(session, tracker, steer_id):
    with _pending_lock:
        _purge_locked()
        _pending[steer_id] = {'tracker': tracker, 'session': str(session),
                              'db': None, 'ended_at': None}
    # Anything retained needs the writer running: grace purging must not
    # depend on later traffic (a process whose only receipt ends unbound).
    _ensure_writer()
    _wake.set()


def discard(steer_id):
    """Forget a steer whose user row will never be written."""
    with _pending_lock:
        binding = _pending.pop(steer_id, None)
        if binding is not None:
            _unpublish_locked(binding['session'], steer_id)
    with _write_lock:
        _queue.pop(steer_id, None)


def discard_meta(metadata):
    """Discard the receipt named by a row's metadata (the row was not written)."""
    steer = (metadata or {}).get('steer') or {}
    if steer.get('steer_id'):
        discard(steer['steer_id'])


def _purge_locked():
    """Drop bindings of ended receipts that no row will ever carry."""
    now = time.monotonic()
    for steer_id, binding in list(_pending.items()):
        ended_at = binding.get('ended_at')
        if ended_at is None:
            continue
        if (binding.get('db') is not None and binding.get('message_id') is not None
                and not binding.get('held_for_group')):
            continue  # bound: the writer releases it once it is saved
        if now - ended_at >= _PENDING_GRACE_SEC:
            _pending.pop(steer_id, None)
            _unpublish_locked(binding['session'], steer_id)


def _superseded(snapshot):
    """True when a newer turn took this chat, so the receipt must not land."""
    session = snapshot.get('session')
    if not session:
        return False
    try:
        from api import chat_delivery

        if chat_delivery.is_stale_turn(session, snapshot.get('guard_turn')):
            return True
    except Exception:
        pass
    turn = str(snapshot.get('turn_id') or '')
    if not turn:
        return False
    try:
        from api.chat_turn_id import ambient

        current = ambient(session)
    except Exception:
        return False
    if current:
        return current != turn
    try:
        from api.agent_harness.background_wait import detached_wait

        wait = detached_wait(session)
    except Exception:
        return False
    base = str(getattr(wait, 'base_turn', '') or '') if wait is not None else ''
    return bool(base) and base != turn


def superseded(session, turn_id, guard_turn):
    """True when a newer turn owns this chat (the receipt's generation is over)."""
    return _superseded({'session': session, 'turn_id': turn_id, 'guard_turn': guard_turn})


def _terminal(contract):
    """One terminal state for the user's own row, keeping the established facts.

    Called when the turn was superseded: the facts were finalized while the run
    still owned the chat (the tracker freezes at that point, so nothing newer
    is in here), and the row the user is looking at must not be left on the
    non-terminal ``pending``.
    """
    delivered_at = contract.get('delivered_at')
    return {**contract,
            'state': DELIVERED if delivered_at else UNDELIVERED}


def changed(snapshot):
    """Publish one snapshot; True when the run no longer owns the chat."""
    return changed_many([snapshot])


def changed_many(snapshots):
    """Publish snapshots to live status and queue their writes. Never blocks.

    Everything a group emits arrives here together, so one transaction covers
    it. Returns True when any snapshot's turn was superseded, which freezes the
    emitting tracker.
    """
    writes, stale = [], False
    for snapshot in snapshots:
        item, gone = _apply(snapshot)
        stale = stale or gone
        if item is not None:
            writes.append(item)
    if writes:
        with _write_lock:
            for item in writes:
                _queue[item['steer_id']] = item
        _ensure_writer()
        _wake.set()
    return stale


def _apply(snapshot):
    """Live-status side of one snapshot → (queue item or None, superseded)."""
    steer_id = snapshot['steer_id']
    contract = {key: snapshot[key] for key in _CONTRACT_KEYS}
    stale = _superseded(snapshot)
    if stale:
        # A newer turn owns the chat: stop publishing, but never throw away a
        # fact the user's own row has already earned. Exactly one terminal
        # state lands there (this is the user's row, not the stale run's reply,
        # so the turn guard is untouched).
        contract = _terminal(contract)
    ended = bool(snapshot.get('ended')) or stale
    with _pending_lock:
        binding = _pending.get(steer_id)
        if binding is None:
            return None, stale
        if ended:
            binding['ended_at'] = binding.get('ended_at') or time.monotonic()
        binding['held_for_group'] = False  # a fresh snapshot is on its way
        session, db = binding['session'], binding['db']
        message_id = snapshot.get('message_id')
        binding['message_id'] = message_id
        if stale:
            _unpublish_locked(session, steer_id)
        elif message_id is not None:
            bucket = _live.setdefault(session, {})
            bucket[steer_id] = {**contract, 'message_id': message_id,
                                'turn_id': snapshot.get('turn_id') or ''}
            while len(bucket) > 100:
                bucket.pop(next(iter(bucket)))
        if ended and message_id is None:
            _purge_locked()
    if db is None or message_id is None:
        return None, stale
    return ({'steer_id': steer_id, 'db': db, 'message_id': message_id,
             'patch': contract, 'session': session, 'ended': ended,
             'turn_id': snapshot.get('turn_id') or '',
             'guard_turn': snapshot.get('guard_turn'),
             'group_binding': bool(snapshot.get('group_binding')),
             'attempts': 0}, stale)


def _unpublish_locked(session, steer_id):
    bucket = _live.get(session)
    if bucket is not None:
        bucket.pop(steer_id, None)
        if not bucket:
            _live.pop(session, None)


def bind(steer_id, db, message_id):
    with _pending_lock:
        binding = _pending.get(steer_id)
        if binding is None:
            return
        binding['db'] = db
        tracker = binding['tracker']
    tracker.bind(steer_id, message_id)


def accepted_detached(steer_id):
    with _pending_lock:
        binding = _pending.get(steer_id)
        tracker = binding['tracker'] if binding else None
    if tracker is not None:
        tracker.accepted_detached(steer_id)


def snapshot(steer_id):
    """The receipt this steer id carries right now, or None once released."""
    with _pending_lock:
        binding = _pending.get(steer_id)
        tracker = binding['tracker'] if binding else None
    return tracker.snapshot(steer_id) if tracker is not None else None


def retained():
    """What this owner still holds (diagnostics; receipts must not accumulate)."""
    with _pending_lock:
        _purge_locked()
        return {'pending': len(_pending), 'sessions': len(_live),
                'live': sum(len(bucket) for bucket in _live.values())}


def live(session):
    """This chat's receipts for the turn that owns it now (transient)."""
    from api.agent_harness.steer import _key

    key = str(_key(session))
    current = _current_turn(session)
    with _pending_lock:
        _purge_locked()
        rows = [dict(value) for value in _live.get(key, {}).values()]
    return [row for row in rows if (row.get('turn_id') or '') == current]


def _current_turn(session):
    try:
        from api.chat_turn_id import ambient

        turn = ambient(session)
    except Exception:
        turn = ''
    if turn:
        return turn
    try:
        from api.agent_harness.background_wait import detached_wait

        wait = detached_wait(session)
    except Exception:
        return ''
    return str(getattr(wait, 'base_turn', '') or '') if wait is not None else ''


# ── writer thread ──────────────────────────────────────────────────────────
def _ensure_writer():
    global _writer
    with _write_lock:
        if _writer is None or not _writer.is_alive():
            _writer = threading.Thread(target=_writer_loop, name='steer-receipts',
                                       daemon=True)
            _writer.start()


def _retaining():
    """Whether anything is still held (read inside ``_write_lock``; no I/O)."""
    with _pending_lock:
        return bool(_pending)


def _writer_loop():
    global _inflight, _writer
    log = logging.getLogger(__name__)
    while True:
        _wake.wait(timeout=_WRITER_IDLE_SEC)
        _wake.clear()
        with _pending_lock:
            _purge_locked()  # time-bound retention even on an idle daemon
        with _write_lock:
            if not _queue:
                # Nothing queued and nothing retained: stop waking up. Both
                # the exit decision and ``_ensure_writer`` happen under
                # _write_lock, and retention is re-read inside it, so a
                # ``track`` racing this exit either keeps the writer alive or
                # sees it gone and starts a new one — the handoff cannot be
                # missed in either order.
                if not _inflight and not _retaining():
                    if _writer is threading.current_thread():
                        _writer = None
                    return
                continue
            batch = dict(_queue)
            _queue.clear()
            _inflight += 1
        retry = {}
        try:
            _write_batch(batch, retry, log)
        finally:
            # Requeue under the same lock hold that drops the in-flight count,
            # so flush() can never see an empty queue with no writer in flight
            # while a failed batch is still waiting to be put back.
            with _write_lock:
                for steer_id, item in retry.items():
                    _queue.setdefault(steer_id, item)
                _inflight -= 1
        if retry:
            time.sleep(_RETRY_SLEEP_SEC)
            _wake.set()


def _write_batch(batch, retry, log):
    """Persist one coalesced batch, filling ``retry`` with another attempt's items."""
    for items in _group_by_db(batch).values():
        try:
            items[0]['db'].patch_message_metadata(
                [(item['message_id'], {'steer': item['patch']}) for item in items])
        except Exception:
            log.warning("Steer receipt write failed for %d row(s)", len(items), exc_info=True)
            for item in items:
                item['attempts'] += 1
                if item['attempts'] < _MAX_WRITE_ATTEMPTS:
                    retry[item['steer_id']] = item
                elif item['ended']:
                    # Given up: the row keeps whatever it last had, and nothing
                    # is retained in memory for a receipt that cannot land.
                    _release(item['steer_id'])
            continue
        for item in items:
            if item['ended']:
                _settle(item['steer_id'], group_binding=item['group_binding'])


def _group_by_db(batch):
    groups = {}
    for item in batch.values():
        groups.setdefault(id(item['db']), []).append(item)
    return groups


def _settle(steer_id, *, group_binding):
    """An ended receipt was saved: release it unless its group is still binding.

    A grouped reply moves to the last covered row as members bind, and every
    member is rewritten when it does — so releasing a member early would
    freeze its row with a second copy of the excerpt and a short ``covers``.
    The grace purge is the backstop for a member that never binds.
    """
    if not group_binding:
        _release(steer_id)
        return
    with _pending_lock:
        binding = _pending.get(steer_id)
        if binding is not None:
            binding['held_for_group'] = True


def _release(steer_id):
    """The receipt is final and saved: nothing needs to be kept for it."""
    with _pending_lock:
        binding = _pending.pop(steer_id, None)
        if binding is None:
            return
        _unpublish_locked(binding['session'], steer_id)


def flush(timeout=5.0):
    """Wait for the writer to drain its queue. For tests and shutdown."""
    deadline = time.monotonic() + timeout
    while True:
        with _write_lock:
            if not _queue and not _inflight:
                return True
        if time.monotonic() >= deadline:
            return False
        _ensure_writer()
        _wake.set()
        time.sleep(0.005)


def _drain_at_exit():
    """Bounded drain on orderly shutdown: queued metadata is not worth losing.

    This only waits for the writer; it never stops, kills or restarts anything
    (Flask and the daemon own their own lifetime).
    """
    try:
        with _write_lock:
            if not _queue and not _inflight:
                return
        flush(_EXIT_DRAIN_SEC)
    except Exception:
        pass


atexit.register(_drain_at_exit)

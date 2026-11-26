"""Steer receipts: what became of a follow-up pushed into a live agent turn.

The transport registers a request before sending it, so an echo that arrives
before the delivery response (or before the user row exists) is not lost. A row
can be bound later; the latest snapshot is retained until then.

**The tracker only changes memory.** Vendor reader threads (``_on_line``),
``steer()`` and turn teardown never open SQLite and never wait on it: one
background writer thread persists receipts
(``AuthDatabase.patch_message_metadata``). A busy database therefore delays no
agent event loop.

**The group is the unit of writing.** Steers answered by one agent message form
a group (an ungrouped steer is a group of one). An event never queues a row
patch: it only marks its group dirty. The writer then reads the group's state
as it is *at write time* and saves every bound member in one transaction — the
excerpt and full ``covers`` on the last bound row, ``reply: null`` on the
others. Writes are serialized on the one writer thread, so whichever
transaction commits last is self-consistent whatever failed, was retried or
bound in between: there is no per-row write that could leave the excerpt on two
rows or on none. A group is retained as a whole — until every member has ended
and bound and that state is committed (or given up on), or its grace expires.

**A write settles only the revision it was planned from.** The writer plans a
group in one critical section (the tracker's lock, then ``_pending_lock``): the
tracker's facts, the membership, the bindings, their superseded flags and the
revision of every bound member are read together, so a plan never combines a
membership from before Stop with a flag from after it. The database is written
outside every lock, so events keep landing while a write is in flight; when it
returns, the group is settled (and possibly released) only if no member's
revision moved meanwhile. Otherwise it stays dirty and retained and is planned
again. A group being written is dirty until that settlement, so "dirty" is the
only thing to check: the grace purge never drops a dirty group, and ``flush()``
does not report done while one exists.

**An unexpected error in this bookkeeping fails closed.** Only a refused
database write is retried. Any other exception while a group is planned or
settled is a bug: it is logged once and the group, as it stood at the fault, is dropped
from memory whole (bindings, queue entries, live status), with no retry and
nothing written after it. Its tracker forgets those steers too, so they never
join a later reply's group: a steer that shares a reply with them afterwards is
regrouped without them and saved normally. The failed rows keep the last
committed state, and a ``pending`` row whose turn is not live reads as "not
confirmed". No other group is affected.

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

# A group is retained until every member has ended and bound and that state is
# saved. One with a member that never binds (the turn ended during delivery, so
# the row insert may still be in flight) is dropped this long after it ended.
_PENDING_GRACE_SEC = 60.0
# Attempts per group write before it is given up. The whole group is given up
# at once and its rows keep the last committed (self-consistent) state; a group
# that can still change stays retained, and its next change is a fresh write.
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
            if entry is None:
                return
            # The mode is a fact of **this send** — the caller just accepted it
            # into an idle background run — not an observation of later vendor
            # output. A supersession landing between acceptance and this call
            # must not relabel the saved row ``live``; the terminal write still
            # carries only facts earned while the run owned the chat.
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

    def confirmed_without_echo(self, steer_id):
        """Retroactive confirmation when the transport never echoes live input.

        Marks the steer ``delivered`` without setting ``echo_seq``, so no later
        output item (which may answer a superseding steer) can be attributed as
        this send's reply excerpt.
        """
        with self._lock:
            entry = self._find(steer_id)
            if entry is None or entry['delivered_at'] is not None or not self._fresh():
                return
            entry['delivered_at'] = _now()
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

    def forget(self, steer_ids):
        """Stop tracking these steers: their group failed closed in the writer.

        They leave ``_entries``, so no later reply groups them, and they leave
        any group they are in, so they are in nobody's ``covers`` and never
        hold the excerpt. A member left behind (it joined after the fault) is
        republished: its group is now only the healthy steers.

        Idempotent, also for a run that failed part way. The groups are found
        by steer id and pruned first, and a pruned group stays marked until
        its members were republished, so doing it again finishes the job.
        """
        with self._lock:
            ids = set(steer_ids)
            for entry in self._entries:
                group = entry['group']
                if group is not None and any(m['steer_id'] in ids for m in group['members']):
                    group['members'] = [m for m in group['members']
                                        if m['steer_id'] not in ids]
                    group['regroup'] = True
            for entry in [e for e in self._entries if e['steer_id'] in ids]:
                entry['group'] = None
                self._entries.remove(entry)
            owed = []
            for entry in self._entries:
                group = entry['group']
                if (group is not None and group.get('regroup')
                        and not any(group is other for other in owed)):
                    owed.append(group)
            left = [member for group in owed for member in group['members']]
            if left:
                self._emit_many(left)
            for group in owed:
                group.pop('regroup', None)

    # ── reads ───────────────────────────────────────────────────────────────
    def snapshot(self, steer_id):
        with self._lock:
            entry = self._find(steer_id)
            return self._snapshot(entry) if entry is not None else None

    def delivered(self, steer_id):
        with self._lock:
            entry = self._find(steer_id)
            return bool(entry and entry['delivered_at'])

    @property
    def lock(self):
        """Held by the writer while it plans, so no event lands mid-plan.

        Every event that changes a receipt (and so its binding's revision and
        superseded flag) runs under this lock; lock order is tracker →
        ``_pending_lock``, as for the events themselves.
        """
        return self._lock

    def group(self, steer_id):
        """Every member of this steer's group as it stands now (one consistent read).

        The writer builds a group's transaction from this, at write time and
        under :attr:`lock`, so a write never carries a row computed before the
        group last changed.
        """
        with self._lock:
            entry = self._find(steer_id)
            if entry is None:
                return None
            return [self._snapshot(member) for member in self._group_members(entry)]

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
        return {**self._public(entry), 'message_id': entry['message_id'],
                'turn_id': entry['turn_id'], 'ended': entry['ended'],
                # Who is retained (and purged) together with this steer.
                'group': tuple(m['steer_id'] for m in self._group_members(entry)),
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
# ``_pending`` keeps each steer's binding (its tracker, database handle and
# retention facts) for as long as its group is retained; ``_live`` is what
# ``/api/chat-live-status`` shows for the current turn. Both maps are taken
# while the emitting tracker's lock is held (the lock order is always tracker →
# these, so it cannot deadlock), and nothing under them touches the database:
# the writer thread does that.
# ``_queue`` holds no row data at all: only "this steer's group needs writing"
# and the attempts already spent on it. The writer's exit decision is the one
# place that nests ``_write_lock`` → ``_pending_lock``; nothing anywhere nests
# them the other way round, and neither critical section calls out of this
# module.
_pending_lock = threading.Lock()
_pending = {}
_live = {}

_write_lock = threading.Lock()
_queue = {}
_inflight = 0
# The steers of the batch that is inside its database call right now. Set and
# cleared by the writer under ``_pending_lock`` around that one call, so the
# grace purge and ``flush()`` see a write in progress whatever the revisions
# say.
_db_call = frozenset()
_wake = threading.Event()
_writer = None


def track(session, tracker, steer_id):
    with _pending_lock:
        _purge_locked()
        _pending[steer_id] = {'tracker': tracker, 'session': str(session),
                              'db': None, 'message_id': None, 'ended_at': None,
                              'stale': False, 'group': (steer_id,),
                              # ``rev`` counts the changes that asked for a
                              # write; ``settled`` is the one the writer last
                              # finished with (saved or given up). Dirty means
                              # they differ.
                              'rev': 0, 'settled': 0}
    # Anything retained needs the writer running: grace purging must not
    # depend on later traffic (a process whose only receipt ends unbound).
    _ensure_writer()
    _wake.set()


def discard(steer_id):
    """Forget a steer whose user row will never be written."""
    with _pending_lock:
        _release_locked([steer_id])


def discard_meta(metadata):
    """Discard the receipt named by a row's metadata (the row was not written)."""
    steer = (metadata or {}).get('steer') or {}
    if steer.get('steer_id'):
        discard(steer['steer_id'])


def _purge_locked():
    """Drop whole groups that ended a grace period ago and owe no write.

    A group goes as one: dropping a single member would leave the others
    waiting for a bind that can no longer be seen. A dirty group (a member's
    latest revision is not settled) or one inside the writer's database call
    is left to the writer, which settles or releases it.
    """
    now = time.monotonic()
    seen = set()
    for steer_id, binding in list(_pending.items()):
        if steer_id in seen:
            continue
        ids = {steer_id, *binding['group']}
        members = {sid: _pending[sid] for sid in ids if sid in _pending}
        seen.update(members)
        if not _db_call.isdisjoint(members):
            continue
        if any(m['ended_at'] is None or m['rev'] != m['settled'] for m in members.values()):
            continue
        if now - max(m['ended_at'] for m in members.values()) >= _PENDING_GRACE_SEC:
            _release_locked(members)


def _release_locked(steer_ids):
    """Forget these steers' live entries and bindings, each by steer id.

    The live entry is found by id, not through the binding, so a release that
    failed part way can simply be done again: nothing is left that only the
    lost binding could name.
    """
    steer_ids = list(steer_ids)
    for session in list(_live):
        for steer_id in steer_ids:
            _unpublish_locked(session, steer_id)
    for steer_id in steer_ids:
        _pending.pop(steer_id, None)


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
    """Publish snapshots to live status and mark their groups dirty. Never blocks.

    Nothing computed here is ever written: the writer reads each dirty group
    afresh. Returns True when any snapshot's turn was superseded, which freezes
    the emitting tracker.
    """
    dirty, stale = [], False
    for snapshot in snapshots:
        writable, gone = _apply(snapshot)
        stale = stale or gone
        if writable:
            dirty.append(snapshot['steer_id'])
    if dirty:
        with _write_lock:
            for steer_id in dirty:
                _queue[steer_id] = 0   # a newer change is a fresh write
        _ensure_writer()
        _wake.set()
    return stale


def _apply(snapshot):
    """Live-status and retention side of one snapshot → (row writable, superseded)."""
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
            return False, stale
        binding['stale'] = binding['stale'] or stale
        binding['group'] = tuple(snapshot.get('group') or (steer_id,))
        if ended:
            binding['ended_at'] = binding.get('ended_at') or time.monotonic()
        session = binding['session']
        message_id = snapshot.get('message_id')
        binding['message_id'] = message_id
        writable = binding['db'] is not None and message_id is not None
        if writable:
            binding['rev'] += 1
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
    return writable, stale


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
# Steer ids whose group hit an unexpected error in this module's own
# bookkeeping. Only the writer thread reads or changes it.
_doomed = set()


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


def _queue_owed():
    """Queue every dirty steer nobody queued (the writer's work is its state)."""
    with _pending_lock:
        dirty = [steer_id for steer_id, binding in _pending.items()
                 if binding['rev'] != binding['settled']]
    if dirty:
        with _write_lock:
            for steer_id in dirty:
                _queue.setdefault(steer_id, 0)


def _fail_closed(steer_ids, log):
    """An unexpected error in planning or settling: a bug, never retried.

    ``steer_ids`` is the failed group as it stood **at the fault**: its
    membership under ``_pending_lock``, captured by the faulting code under the
    lock it held or by its handler (``_fault_group``). Nothing is read again here, so
    a reply that lands after the fault cannot widen the failure: a steer it
    groups with these is regrouped by the drop and saved normally. The first
    statement records the whole set — from it on nothing is planned or written
    for these steers, and the writer's next pass drops them from memory; their
    rows keep the last committed state. Logging comes last.
    """
    _doomed.update(steer_ids)
    _wake.set()
    log.exception("Steer receipt bookkeeping failed; the group is dropped and "
                  "its rows keep the last committed state")


def _mark_fault_locked(exc, steer_ids):
    """Attach the failed group to ``exc``. Caller holds ``_pending_lock``.

    Called where the fault happened, before any lock is released: these
    steers plus whoever shares their group at this instant.
    """
    try:
        group = set(steer_ids)
        for steer_id in steer_ids:
            group.update(_pending.get(steer_id, {}).get('group', ()))
        exc.steer_group = tuple(group)
    except Exception:
        pass   # the caller's own membership is used instead


def _fault_group(exc, known):
    """The group that failed with ``exc``, as it stands under ``_pending_lock``.

    The one rule for every handler: the doomed set is ``known`` plus whoever
    shares a group with it, read under ``_pending_lock`` at the fault. Code
    that faulted holding that lock marked ``exc`` there and that is used as it
    is. For a fault outside the lock (before a plan is read, or after the
    database call, when a reply may have regrouped the plan's members) the
    handler reads the membership here, once, before ``_fail_closed`` records
    anything — never ``known`` alone, and never again afterwards.
    """
    if not getattr(exc, 'steer_group', None):
        try:
            known = tuple(known)
            with _pending_lock:
                _mark_fault_locked(exc, known)
        except Exception:
            pass   # the caller's own membership is used instead
    return tuple(getattr(exc, 'steer_group', None) or known)


def _drop_doomed():
    """Forget every doomed steer: tracker entry, binding, live and queue entry.

    Exactly the steers marked at the fault go. Their tracker forgets them
    first (lock order tracker → ``_pending_lock``), which regroups and
    republishes a healthy steer that joined them since; then the bindings and
    live entries are removed by id. Every step is idempotent and ``_doomed``
    is cleared last, so a drop that fails part way is simply done again on the
    next pass. No other group is touched.
    """
    if not _doomed:
        return
    gone = set(_doomed)
    with _pending_lock:
        trackers = {id(_pending[steer_id]['tracker']): _pending[steer_id]['tracker']
                    for steer_id in gone if steer_id in _pending}
    for tracker in trackers.values():
        tracker.forget(gone)
    with _pending_lock:
        _release_locked(gone)
    with _write_lock:
        for steer_id in gone:
            _queue.pop(steer_id, None)
    _doomed.difference_update(gone)


def _writer_loop():
    global _inflight, _writer
    log = logging.getLogger(__name__)
    while True:
        _wake.wait(timeout=_WRITER_IDLE_SEC)
        _wake.clear()
        try:
            _drop_doomed()
        except Exception:
            log.exception("Steer receipt drop failed; it is done again on the next pass")
        # Neither may end the writer: a queued write would then wait for the
        # next event. Each is simply done again on the next pass.
        try:
            with _pending_lock:
                _purge_locked()  # time-bound retention even on an idle daemon
        except Exception:
            log.exception("Steer receipt purge failed; it is done again on the next pass")
        try:
            _queue_owed()
        except Exception:
            log.exception("Steer receipt queueing failed; it is done again on the next pass")
        with _write_lock:
            if not _queue:
                # Nothing queued and nothing retained: stop waking up. Both
                # the exit decision and ``_ensure_writer`` happen under
                # _write_lock, and retention is re-read inside it, so a
                # ``track`` racing this exit either keeps the writer alive or
                # sees it gone and starts a new one — the handoff cannot be
                # missed in either order.
                if not _inflight and not _retaining() and not _doomed:
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
        except Exception:
            log.exception("Steer receipt writer failed")
        finally:
            # Requeue under the same lock hold that drops the in-flight count,
            # so flush() can never see an empty queue with no writer in flight
            # while a failed batch is still waiting to be put back. A change
            # queued meanwhile wins: it is a newer write of the same group.
            with _write_lock:
                for steer_id, attempts in retry.items():
                    _queue.setdefault(steer_id, attempts)
                _inflight -= 1
        if retry:
            time.sleep(_RETRY_SLEEP_SEC)
            _wake.set()


def _write_batch(batch, retry, log):
    """Write every dirty group in ``batch`` whole; fill ``retry`` with what is owed.

    ``batch`` is ``{steer_id: attempts}``. Each steer resolves to its group as
    it is now, so several dirty members — or a retry and a newer change — are
    one write of one group. The database is written outside every lock, so a
    plan can be overtaken while it is in flight: ``_settle`` then leaves the
    group dirty and it is planned again.

    A plan is plain data and holds nothing: a group that is planned is dirty
    until ``_settle``, and dirty is what keeps it from the grace purge and
    ``flush()`` from reporting done. A refused database write is retried within
    the group's budget; any other exception — building the arguments, or after
    the commit — fails the batch's groups closed.
    """
    global _db_call
    by_db = {}
    for plan in _plan_groups(batch, log):
        by_db.setdefault(id(plan['db']), []).append(plan)
    for plans in by_db.values():
        try:
            # Built before the database is asked: only an exception raised by
            # the database call itself is a refusal.
            write = plans[0]['db'].patch_message_metadata
            payload = [(message_id, {'steer': contract})
                       for plan in plans for message_id, contract in plan['rows']]
            try:
                with _pending_lock:
                    _db_call = frozenset(s for plan in plans for s in plan['members'])
                try:
                    write(payload)
                except Exception:
                    log.warning("Steer receipt write failed for %d group(s)", len(plans),
                                exc_info=True)
                    saved = False
                else:
                    saved = True
            finally:
                # A plain rebind, no lock to take: it cannot fail, on any way
                # out of the database call.
                _db_call = frozenset()
            for plan in plans:
                try:
                    _requeue(retry, _settle(plan) if saved else _fail(plan, retry))
                except Exception as exc:
                    _fail_closed(_fault_group(exc, plan['members']), log)
        except Exception as exc:
            # Not the database: nothing here is retried, so what a commit just
            # saved is never queued again.
            _db_call = frozenset()
            _fail_closed(_fault_group(exc, [s for plan in plans for s in plan['members']]),
                         log)


def _fail(plan, retry):
    """This plan was not saved: retry it within its budget, else give it up."""
    attempts = plan['attempts'] + 1
    if attempts < _MAX_WRITE_ATTEMPTS:
        for steer_id in plan['revs']:
            retry[steer_id] = attempts
        return []
    # Given up, as a group: its rows keep the last state a transaction
    # committed, which is self-consistent.
    return _give_up(plan)


def _requeue(retry, steer_ids):
    for steer_id in steer_ids:
        retry[steer_id] = 0   # a newer revision is a fresh write


def _plan_groups(batch, log):
    """Dirty steers → the plans to write, one per group, each from one read.

    An exception while planning a steer fails its group closed. Only plans
    whose every member is still retained in an undoomed group are returned, so
    nothing is written for a group after it failed (not even an older plan of
    this batch, read before the group grew).
    """
    plans = {}   # member steer id → its group's plan
    for steer_id, attempts in batch.items():
        known = (steer_id,)   # widened to its group under the lock at a fault
        try:
            plan = plans.get(steer_id)
            if plan is not None:
                # Its group is already planned in this batch, from a read that
                # is at least as new; the freshest request sets the budget.
                plan['attempts'] = min(plan['attempts'], attempts)
                continue
            with _pending_lock:
                try:
                    binding = _pending.get(steer_id)
                    if binding is None or not _doomed.isdisjoint(binding['group']):
                        continue   # released, discarded or failed closed
                    tracker = binding['tracker']
                    known = (steer_id, *binding['group'])
                except Exception as exc:
                    # The lock is still held: this is the group at the fault.
                    _mark_fault_locked(exc, known)
                    raise
            plan = _plan(tracker, steer_id, attempts)
            if plan is None:
                continue
            known = (steer_id, *plan['members'])
            for member in plan['members']:
                older = plans.get(member)
                if older is not None and older is not plan:
                    # The group grew since an earlier plan of this batch read
                    # it: the newer read replaces that plan.
                    plan['attempts'] = min(plan['attempts'], older['attempts'])
                    plans = {key: value for key, value in plans.items()
                             if value is not older}
                plans[member] = plan
        except Exception as exc:
            _fail_closed(_fault_group(exc, known), log)
    with _pending_lock:
        return [plan for plan in {id(plan): plan for plan in plans.values()}.values()
                if all(steer_id in _pending
                       and _doomed.isdisjoint(_pending[steer_id]['group'])
                       for steer_id in plan['members'])]


def _plan(tracker, steer_id, attempts):
    """One group's transaction, read in ONE critical section.

    The tracker's lock is held across the whole read, then ``_pending_lock``
    inside it (the same order the events use). Every event that changes a
    receipt — and with it the binding's revision and superseded flag — runs
    under the tracker's lock, so the facts, the membership, the bindings, the
    flags and the revisions in a plan all describe the same instant: exactly
    one row (the last bound) carries the excerpt, all carry the same
    ``covers``, and a superseded member's terminal state is built from the
    facts frozen before Stop. ``revs`` is the revision this plan was made
    from; ``_settle`` compares it with the group's revision after the write.

    A group with no unsettled revision owes no write and gets no plan: a
    second queue entry for a group an earlier plan already saved up to date
    must not write it again.

    Planning changes nothing (but for a steer with no row left to write, which
    is settled on the spot), so an exception in here leaves nothing behind.
    """
    rows, revs, members, db, final = [], {}, [], None, True
    with tracker.lock:
        with _pending_lock:
            try:
                for member in tracker.group(steer_id) or []:
                    member_id = member['steer_id']
                    binding = _pending.get(member_id)
                    if binding is None:
                        continue   # discarded: no row will ever carry this steer
                    members.append(member_id)
                    if not (member['ended'] or binding['stale']):
                        final = False
                    message_id = member['message_id']
                    if binding['db'] is None or message_id is None:
                        final = False   # still to bind: the group will be rewritten
                        continue
                    contract = {key: member[key] for key in _CONTRACT_KEYS}
                    if binding['stale']:
                        contract = _terminal(contract)
                    rows.append((message_id, contract))
                    revs[member_id] = binding['rev']
                    # One handle writes the whole group, so it is one transaction
                    # (every handle is the same database).
                    db = db if db is not None else binding['db']
                if not rows:
                    # No row to write (the tracker no longer knows the steer):
                    # nothing is owed for it.
                    binding = _pending.get(steer_id)
                    if binding is not None:
                        binding['settled'] = binding['rev']
                    return None
                if all(_pending[member]['rev'] == _pending[member]['settled']
                       for member in revs):
                    return None   # clean: already saved (or given up) as it is
                rows.sort(key=lambda row: row[0])
                return {'db': db, 'rows': rows, 'revs': revs, 'members': members,
                        'final': final, 'attempts': attempts}
            except Exception as exc:
                # Both locks are still held: this is the group at the fault.
                _mark_fault_locked(exc, (steer_id, *members))
                raise


def _settle(plan):
    """The writer is done with this plan (saved, or given up after retries).

    Only the revision the plan was made from is settled. If any member's
    revision moved while the write was in flight, what was just saved is
    already old: the group stays dirty and retained, nothing is released, and
    the moved members are returned to be planned again.

    Otherwise a group that can no longer change — every member ended and
    bound — is released whole. Any other stays retained with all its bindings,
    so the next change (a late bind, the turn's end) rewrites every row again;
    the grace purge is the backstop for a member that never binds.
    """
    moved = []
    with _pending_lock:
        try:
            for steer_id, rev in plan['revs'].items():
                binding = _pending.get(steer_id)
                if binding is None:
                    continue
                if binding['rev'] == rev:
                    binding['settled'] = rev
                else:
                    moved.append(steer_id)
            if plan['final'] and not moved:
                _release_locked(plan['members'])
        except Exception as exc:
            # The lock is still held: this is the group at the fault.
            _mark_fault_locked(exc, plan['members'])
            raise
    return moved


def _give_up(plan):
    """Stop retrying this plan's revision; the rows keep the last committed state."""
    return _settle(plan)


def _write_owed():
    with _pending_lock:
        return bool(_db_call) or any(binding['rev'] != binding['settled'] for binding in _pending.values())


def flush(timeout=5.0):
    """Wait until no write is owed: nothing queued, in flight, dirty, inside a
    database call or to drop.

    For tests and shutdown.
    """
    deadline = time.monotonic() + timeout
    while True:
        with _write_lock:
            idle = not _queue and not _inflight
        if idle and not _write_owed() and not _doomed:
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

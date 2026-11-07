"""The assistant row of an agent turn, from its first frame (owner).

A turn's assistant row used to be inserted only when the reply was saved, so the
live bubble had no row id, no ``CH-…-N`` handle and — until the harness kernel
minted one — no query id: its inspector link opened the log index, and a stop
or crash before the save left nothing to point at.

Now the turn workflow (``api.chat_turn_workflow``) opens the row when the turn
starts, right after the user row:

- ``open_row`` inserts an empty assistant row marked ``in_progress`` carrying
  the turn's query id, and opens that query's inspector record (agent-events
  run + live snapshot, marked executing). The harness kernel then tracks the
  turn under that same id (``claim_query_id``), so the link is right at once.
- ``finalize`` is how the saver persists the reply: the same row, updated in
  place. ``close`` runs when the turn ends without a saved reply (Stop, a
  status line, an empty failure, a superseded turn): the row becomes the
  "stopped / failed before completion" card (``chat_turn_recovery``), or is
  removed when it is still the chat's newest row and nothing was shown for it.
- ``heal_orphans`` (history reads) settles rows whose turn is not live in this
  process any more — a Flask restart or crash mid-turn — the same way.

Readers: ``AuthDatabase.get_messages`` hides in-progress rows unless asked
(``include_in_progress``), so context compile, titling, supervision and the
CLI never see an empty reply; chat attention ignores them until finalized, so
"new reply" fires when the reply lands, not when the turn starts.

Stores without ``update_message_content`` / ``delete_message`` (test fakes)
keep the old insert-at-save behaviour: ``open_row`` returns None.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

IN_PROGRESS = 'in_progress'

_lock = threading.Lock()
# chat key -> {'message_id', 'query_id', 'turn', 'claimed', 'opened_at', 'key'}
_open: Dict[str, Dict[str, Any]] = {}


def _bare(session_id: Any) -> Optional[str]:
    s = str(session_id if session_id is not None else '').strip().removeprefix('db_session_')
    return s if s.isdigit() else None


def _key(session_id: Any, slot: str = '') -> Optional[str]:
    sid = _bare(session_id)
    if sid is None:
        return None
    return f'{sid}:{slot}' if slot else sid


def _store_id(db: Any) -> str:
    """Which store a row lives in: an entry never acts on another store's row ids."""
    return str(getattr(db, 'db_path', '') or id(db))


def _take(key: Optional[str], db: Any, *, pop: bool, turn: Any = None) -> Optional[Dict[str, Any]]:
    """The open entry for ``key`` in ``db`` (removed when ``pop``); drops one from another store."""
    if key is None:
        return None
    with _lock:
        entry = _open.get(key)
        if not entry:
            return None
        if entry.get('store') != _store_id(db):
            _open.pop(key, None)  # left by another store (tests reuse chat ids)
            return None
        if turn is not None and entry.get('turn') not in (None, turn):
            return None
        if pop:
            _open.pop(key, None)
        return dict(entry)


def _supports(db: Any) -> bool:
    return all(callable(getattr(db, name, None)) for name in
               ('add_message', 'update_message_content', 'delete_message', 'get_message_by_id'))


def new_query_id() -> str:
    return uuid.uuid4().hex[:8]


def report_url(query_id: str) -> str:
    return f'/query_log.html?id={query_id}'


def is_in_progress(msg: Any) -> bool:
    meta = (msg or {}).get('metadata') if isinstance(msg, dict) else None
    if isinstance(meta, str):
        return f'"{IN_PROGRESS}": true' in meta
    return isinstance(meta, dict) and bool(meta.get(IN_PROGRESS))


# ── the inspector record (query log) from turn start ─────────────────────────


def _open_query_record(query_id: str, session_id: str, user_input: str, label: str) -> None:
    """Make ``/api/query-log/<id>`` answer (executing) before any harness starts."""
    data = {
        'query_id': query_id,
        'timestamp': datetime.now().isoformat(),
        'user_input': str(user_input or '')[:4000],
        'user_context': {'web_ui': True, 'chat_session_id': session_id, 'turn_row': True},
        'execution_stages': [], 'llm_calls': [], 'tool_calls': [],
        'events': [{'kind': 'status', 't': time.time(), 'text': 'Turn started — connecting…',
                    'summary': 'Turn started', 'block_id': uuid.uuid4().hex}],
        'harness': {}, 'brain': {}, 'sent': {}, 'success': True, 'error_message': None,
    }
    try:
        from api.query_tracker import _publish_shared_live_snapshot

        _publish_shared_live_snapshot(query_id, data)
    except Exception:
        log.debug('live snapshot for %s failed', query_id, exc_info=True)
    try:
        from api.agent_events.writer import record

        record('run', query_id, {k: v for k, v in data.items()
                                 if k not in ('events', 'execution_stages', 'graph_structure')})
        record('event', query_id, dict(data['events'][0]))
    except Exception:
        log.debug('run record for %s failed', query_id, exc_info=True)
    try:
        from api.active_executions import register_execution

        register_execution(query_id, label or 'Chat turn')
    except Exception:
        pass


def _close_query_record(entry: Dict[str, Any], *, success: bool, note: str = '') -> None:
    """Finish the turn's own record when no harness tracker took it over."""
    qid = entry.get('query_id')
    if not qid or entry.get('foreign_run'):
        return  # another turn's record: not ours to end
    try:
        from api.active_executions import unregister_execution

        unregister_execution(qid)
    except Exception:
        pass
    if entry.get('claimed'):
        return  # the kernel's tracker owns and finishes it
    try:
        from api.agent_events.writer import record

        text = note or ('Turn finished' if success else 'Turn ended')
        record('event', qid, {'kind': 'status', 't': time.time(), 'block_id': uuid.uuid4().hex,
                              'text': text, 'summary': text})
        record('run', qid, {'query_id': qid, 'finished_at': time.time(), 'success': bool(success),
                            'error_message': None if success else (note or 'ended'),
                            'user_context': {'web_ui': True, 'chat_session_id': entry.get('session_id'),
                                             'turn_row': True}})
    except Exception:
        log.debug('run close for %s failed', qid, exc_info=True)


# ── open / claim / finalize / close ──────────────────────────────────────────


def open_row(db: Any, session_id: Any, *, turn: Any = None, user_input: str = '', label: str = '',
             metadata: Optional[dict] = None, slot: str = '',
             query_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Insert the turn's in-progress assistant row; ``{message_id, query_id, report_url}``.

    ``slot`` separates a second row a chat can own at once (a detached run's
    follow-up answer, keyed by its job). ``query_id`` attaches the row to a
    run that already has its inspector record (that follow-up's own turn):
    nothing new is opened or finished for it. None when the store cannot
    update rows in place, or the chat id is not a stored chat."""
    key = _key(session_id, slot)
    if key is None or not _supports(db):
        return None
    existing_run = bool(query_id)
    qid = str(query_id) if existing_run else new_query_id()
    meta = dict(metadata or {})
    meta.update({IN_PROGRESS: True, 'query_id': qid, 'report_url': report_url(qid),
                 'turn_id': qid, 'turn_started_at': time.time()})
    try:
        mid = int(db.add_message(int(_bare(session_id)), 'assistant', '', metadata=meta))
    except Exception:
        log.exception('could not open the turn row for chat %s', session_id)
        return None
    user_mid, user_handle = _triggering_user_message(db, _bare(session_id), mid)
    entry = {'message_id': mid, 'query_id': qid, 'turn': turn, 'claimed': existing_run,
             'foreign_run': existing_run, 'user_message_id': user_mid, 'user_message': user_handle,
             'opened_at': time.time(), 'key': key, 'session_id': _bare(session_id),
             'store': _store_id(db)}
    with _lock:
        stale = _open.pop(key, None)
        _open[key] = entry
    if stale and stale.get('store') == entry['store']:
        _settle(db, stale, status='interrupted')  # a previous turn never closed
    if not existing_run:
        _open_query_record(qid, entry['session_id'], user_input, label)
    return {'message_id': mid, 'query_id': qid, 'report_url': report_url(qid),
            'user_message_id': user_mid, 'user_message': user_handle}


def _triggering_user_message(db: Any, sid: Optional[str], row_id: int):
    """The user bubble that started this turn: ``(row id, 'CH-…-N')``, or ``(None, '')``.

    The chat's newest user row before the turn's own row; a turn with no user
    bubble of its own (a background continuation) gets none."""
    try:
        conn = db._get_connection()
    except Exception:
        return None, ''
    try:
        last_assistant = conn.execute(
            "SELECT MAX(id) FROM chat_messages WHERE chat_session_id=? AND role='assistant' AND id<?",
            (int(sid), int(row_id))).fetchone()[0] or 0
        user = conn.execute(
            "SELECT MAX(id) FROM chat_messages WHERE chat_session_id=? AND role='user' AND id<? AND id>?",
            (int(sid), int(row_id), int(last_assistant))).fetchone()[0]
        if not user:
            return None, ''
        index = conn.execute(
            "SELECT COUNT(*) FROM chat_messages WHERE chat_session_id=? AND role IN ('user','assistant') AND id<=?",
            (int(sid), int(user))).fetchone()[0]
        return int(user), f"CH-{int(sid):06d}-{int(index)}"
    except Exception:
        return None, ''
    finally:
        conn.close()


def current(session_id: Any, slot: str = '') -> Optional[Dict[str, Any]]:
    key = _key(session_id, slot)
    with _lock:
        entry = _open.get(key) if key else None
        return dict(entry) if entry else None


def is_live_row(message_id: Any) -> bool:
    """Whether ``message_id`` is the open row of a turn running in this process."""
    try:
        mid = int(message_id)
    except (TypeError, ValueError):
        return False
    with _lock:
        return any(int(e['message_id']) == mid for e in _open.values())


def claim_query_id(session_id: Any) -> Optional[str]:
    """The open turn's query id for the harness kernel to track under (once).

    A second kernel run in the same turn (a router retry) gets None and mints
    its own id; the saved row then carries whichever id the result reports."""
    key = _key(session_id)
    with _lock:
        entry = _open.get(key) if key else None
        if not entry or entry['claimed']:
            return None
        entry['claimed'] = True
        return entry['query_id']


def finalize(db: Any, session_id: Any, content: str, metadata: Optional[dict], *,
             slot: str = '') -> Optional[int]:
    """Write the saved reply into the open row; its id, or None when none is open."""
    entry = _take(_key(session_id, slot), db, pop=True)
    if not entry:
        return None
    meta = dict(metadata or {})
    meta.pop(IN_PROGRESS, None)
    meta.setdefault('query_id', entry['query_id'])
    if not meta.get('report_url') and meta.get('query_id'):
        meta['report_url'] = report_url(meta['query_id'])
    if meta.get('query_id') != entry['query_id']:
        meta.setdefault('turn_query_id', entry['query_id'])
    meta['turn_id'] = entry['query_id']  # the turn's anchor (api.chat_turn_id), whatever ran in it
    try:
        ok = db.update_message_content(entry['message_id'], content or '', metadata=meta)
    except Exception:
        log.exception('could not finalize turn row %s', entry['message_id'])
        ok = False
    if not ok:  # row gone (chat cleared mid-turn): the caller inserts
        _close_query_record(entry, success=True)
        return None
    _close_query_record(entry, success=True,
                        note='' if meta.get('query_id') == entry['query_id']
                        else f"Reply tracked under query {meta.get('query_id')}")
    return int(entry['message_id'])


def close(db: Any, session_id: Any, *, status: str = 'cancelled', slot: str = '', turn: Any = None) -> None:
    """End an open row whose turn saved no reply. Idempotent.

    ``status`` is cancelled / failed / interrupted / skipped. A skipped turn (a
    status line, a ``ui == system`` answer, a supervised row standing in) leaves
    no card. ``turn`` (when given) must match the row's turn, so a stale worker
    never closes a newer turn's row."""
    entry = _take(_key(session_id, slot), db, pop=True, turn=turn)
    if entry:
        _settle(db, entry, status=status)


def _settle(db: Any, entry: Dict[str, Any], *, status: str) -> None:
    """Finish an open row whose turn saved no reply: drop it, or make it the card.

    A dropped row's record ends as a quiet finished run; only a row that becomes
    the stopped/failed card records the failure — Stop/history recovery must
    never manufacture a second card for a turn that rightly left none."""
    try:
        row = db.get_message_by_id(entry['message_id'])
    except Exception:
        row = None
    if not row or not is_in_progress(row):
        _close_query_record(entry, success=True, note=f'Turn {status}')
        return  # already finalized (e.g. by Stop's recovery) or deleted
    drop = status == 'drop' or (status == 'skipped' and _is_newest(db, row))
    if drop:  # a supervised canonical row stands in, or nothing was ever shown for it
        try:
            db.delete_message(entry['message_id'])
            _close_query_record(entry, success=True, note='Turn ended without a reply')
            return
        except Exception:
            log.exception('could not drop turn row %s', entry['message_id'])
    if status in ('skipped', 'drop'):
        status = 'cancelled'
    _close_query_record(entry, success=False, note=f'Turn {status}')
    from api.chat_turn_recovery import interrupted_turn_card

    content, meta = interrupted_turn_card(entry['query_id'], status=status, run=_run(entry['query_id']),
                                          cutoff=time.time())
    meta = with_subagent_cards(db, entry['session_id'], entry['message_id'], meta)
    try:
        db.update_message_content(entry['message_id'], content, metadata=meta)
    except Exception:
        log.exception('could not settle turn row %s', entry['message_id'])


def with_subagent_cards(db: Any, session_id: Any, message_id: int, meta: Dict[str, Any]) -> Dict[str, Any]:
    """A settled row keeps the sub-agent cards bound to it (spawned during its turn)."""
    try:
        from api.subagents.service import attach_batches_to_assistant_meta

        return attach_batches_to_assistant_meta(int(_bare(session_id)), meta, message_id=int(message_id),
                                                db=db) or meta
    except Exception:
        return meta


def _is_newest(db: Any, row: Dict[str, Any]) -> bool:
    newest = getattr(db, 'newest_message_id', None)
    if not callable(newest):
        return False
    try:
        return int(newest(int(row['chat_session_id']))) == int(row['id'])
    except Exception:
        return False


def _run(query_id: str) -> Optional[Dict[str, Any]]:
    try:
        from api.agent_events.store import EventStore, state_dir

        return EventStore(state_dir()).run(query_id)
    except Exception:
        return None


def heal_orphans(db: Any, session_id: Any, *, store: Any = None) -> list:
    """Settle in-progress rows whose turn is not open in this process.

    A Flask restart or crash mid-turn leaves the row marked in progress with no
    turn behind it; history reads turn it into the interrupted card (keeping
    any text the run wrote), so no empty bubble stays behind."""
    lister = getattr(db, 'in_progress_messages', None)
    sid = _bare(session_id)
    if not callable(lister) or sid is None:
        return []
    with _lock:
        live = {int(e['message_id']) for e in _open.values() if e.get('store') == _store_id(db)}
    healed = []
    for row in lister(int(sid)) or []:
        if int(row['id']) in live:
            continue
        meta = row.get('metadata') if isinstance(row.get('metadata'), dict) else {}
        qid = str(meta.get('query_id') or '')
        run = None
        if qid:
            try:
                run = store.run(qid) if store is not None else _run(qid)
            except Exception:
                run = None
        status = (run or {}).get('status') or 'interrupted'
        if status in ('running', 'ok'):
            status = 'interrupted'
        from api.chat_turn_recovery import interrupted_turn_card

        content, card_meta = interrupted_turn_card(qid, status=status, run=run, cutoff=time.time(),
                                                   store=store)
        card_meta = with_subagent_cards(db, sid, int(row['id']), card_meta)
        try:
            db.update_message_content(int(row['id']), content, metadata=card_meta)
            healed.append(int(row['id']))
        except Exception:
            log.exception('could not heal turn row %s', row.get('id'))
    return healed


def reset_for_tests() -> None:
    """Forget every open row, and release its executing mark (a pinned
    execution would block ``/restart graceful``)."""
    with _lock:
        entries = list(_open.values())
        _open.clear()
    try:
        from api.active_executions import unregister_execution

        for entry in entries:
            if not entry.get('foreign_run'):
                unregister_execution(entry.get('query_id'))
    except Exception:
        pass

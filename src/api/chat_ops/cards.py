"""Fleet cards for the chats a turn messaged through chat-ops (read model).

A reply shows one card per chat its turn sent to or steered. The send's
operation row names the sending turn (``payload.turn_id``, from ``chat_cli send``
inside an agent turn) and its delivery receipt names the turn it reached in the
target, so the card is derived when history loads: nothing is written to message
metadata and no timing is guessed. Host sends (template nudges) and sends made
before turns were recorded carry no turn and get no card.

Same payload and renderer as sub-agent fleet cards (``api.subagents.fleet``,
``chat_subagent_fleet.js``), behind the same ``subagent_fleet_cards`` flag. A
chat that already has a sub-agent card on the bubble keeps that one.
Teardown: delete this module, its call in ``auth_api`` history hydration,
``store.sends_in_turns`` and ``turn_id`` in ``service.send`` / ``chat_cli send``.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from api.chat_ops import store

_QUEUED = ('pending', 'dispatching', 'verifying', 'steering')


def _meta(row):
    meta = (row or {}).get('metadata')
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except ValueError:
            meta = None
    return meta if isinstance(meta, dict) else {}


def _utc(epoch):
    try:
        return datetime.fromtimestamp(float(epoch), timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    except (TypeError, ValueError, OverflowError, OSError):
        return ''


def describe(op, sent=None, reply=None, *, live=''):
    """``(outcome, text, started_at, finished_at)`` for one send (pure).

    ``sent``: the user row the send arrived as in the target; ``reply``: the
    assistant row of the turn it reached there; ``live``: that chat's status line."""
    from api.subagents.fleet import plain_text

    state, result = op.get('state'), op.get('result') or {}
    if state in _QUEUED:
        waiting = result.get('waiting_on') == 'target_turn'
        return 'queued', "Waiting for this chat's current turn to end" if waiting else 'Queued for delivery', '', ''
    if state == 'cancelled':
        return 'cancelled', 'Cancelled before delivery', '', ''
    if state == 'unknown':
        return 'failed', plain_text(result.get('error')) or 'Delivery could not be confirmed', '', ''
    started = str((sent or reply or {}).get('timestamp') or '')
    if reply is None:
        return ('failed', 'Delivery failed', started, '') if state == 'failed' else ('done', 'Delivered', started, '')
    # A steer's receipt is the agent's direct answer to this message.
    receipt = (_meta(sent).get('steer') or {}).get('reply') or {}
    answer = plain_text(receipt.get('text'))
    meta = _meta(reply)
    if meta.get('in_progress'):
        return 'running', answer or plain_text(live) or 'Working…', started, ''
    finished = str(receipt.get('at') or '') if answer else _utc(meta.get('completed_at'))
    if meta.get('kind') == 'interrupted-agent-turn':
        stopped = (meta.get('interrupted_turn') or {}).get('status') == 'cancelled'
        return ('cancelled' if stopped else 'failed'), answer or 'Stopped before completion', started, finished
    return 'done', answer or plain_text(reply.get('content')) or 'Finished with an empty reply', started, finished


def _rows(db, op):
    """The target's user row for this send and the assistant row of the turn it reached."""
    from api.chat_turn_id import turn_id_of

    target = int(op['target'])
    sent = db.user_message_by_chat_op(target, op['id'])
    # A dispatched send started the turn its receipt names; a steer joined a running one.
    turn = str((op.get('result') or {}).get('query_id') or '') or turn_id_of(_meta(sent))
    return sent, (db.assistant_message_by_query(target, turn) if turn else None)


def _live(target):
    try:
        from api.chat_live_status import get_live_status
        status = get_live_status(target).get('status')
        return status if isinstance(status, str) else ''
    except Exception:
        return ''


def _agent(db, row, reply):
    meta = _meta(reply)
    chips = (meta.get('slash_command') or {}).get('chips') or []
    agent = str((chips[0] or {}).get('category') or '') if chips and isinstance(chips[0], dict) else ''
    if not agent and row:
        from api.chat_ops import service
        try:
            agent = service.chat_agent(db, row)
        except Exception:
            agent = ''
    return agent, str((meta.get('usage') or {}).get('model') or '')


def entry(db, op):
    """Fleet-card payload for one send: the target chat, how the send went, what it answered."""
    from api.subagents.fleet import card_fields
    from api.subagents.spec import public_launcher

    target = int(op['target'])
    row = db.get_chat_session_by_id(target) or {}
    sent, reply = _rows(db, op)
    state, text, started, finished = describe(op, sent, reply, live=_live(target))
    handle = f'CH-{target:06d}'
    name = str(row.get('session_name') or '').strip() or handle
    agent, model = _agent(db, row, reply)
    card = public_launcher({'id': f"chatop:{op['id']}", 'session_id': target, 'handle': handle, 'label': name,
                            'model': model, 'status': state, 'generating': state == 'running',
                            'display_name': name})
    card['agent'] = agent  # unknown stays blank: the launcher default names a harness
    return {**card, **card_fields(state, text, started_at=started, finished_at=finished),
            'chat_op': 'steer' if (op.get('result') or {}).get('steered') else 'send'}


def hydrate(db, session_id, messages):
    """Add a card per messaged chat to the saved reply of the turn that sent it."""
    from api.chat_turn_id import turn_id_of
    from api.subagents.fleet import fleet_enabled

    if not messages or not fleet_enabled():
        return
    replies = {}  # turn id -> that turn's newest saved reply on this page
    for msg in messages:
        meta = _meta(msg)
        if msg.get('role') == 'assistant' and not meta.get('in_progress') and turn_id_of(meta):
            replies[turn_id_of(meta)] = msg
    if not replies:
        return
    newest = {}  # one card per chat per turn: its latest send, in first-send order
    for op in store.sends_in_turns(int(session_id), list(replies)):
        newest[(op['payload']['turn_id'], int(op['target']))] = op
    for (turn, target), op in newest.items():
        msg = replies[turn]
        meta = dict(_meta(msg))
        cards = [c for c in (meta.get('subagents') or []) if isinstance(c, dict)]
        if any(str(c.get('session_id') or '') == str(target) for c in cards):
            continue  # a sub-agent child: its own card round already rides this bubble
        meta['subagents'] = cards + [entry(db, op)]
        msg['metadata'] = meta

"""Materialize interrupted activity in history without delivering a late reply.

The event log owns captured activity; auth owns transcript rows. Stop invalidates
delivery tokens, but must not erase text already captured before cancellation.
Authenticated history reads also repair old missing rows and process crashes.
"""
from __future__ import annotations

from datetime import datetime, timezone
import logging
import re
import time

log = logging.getLogger(__name__)
_CONTROLS = re.compile(
    r'<(cuttle_(?:action_form|confirm|widget)[\w-]*)\b[^>]*>.*?(?:</\1\s*>|\Z)',
    re.I | re.S,
)


def preserve_interrupted_turns(db, session_id, *, store=None, stopped_at=None):
    """Idempotent recovery after ownership validation by the caller.

    ``stopped_at`` snapshots still-running turns during Stop. Ordinary reads
    recover only terminal failed/cancelled/interrupted runs, never live output.
    Missing thinking/tool payloads remain accessible through the query log;
    only public writing is included in the transcript. Recovered cards are inert.
    """
    try:
        if store is None:
            if stopped_at is not None:
                # Drain already-emitted items before freezing the stopped turn.
                from api.agent_events.writer import get_writer
                get_writer().flush(timeout=2)
            from api.agent_events.store import EventStore, state_dir
            store = EventStore(state_dir())
        runs = store.session_runs(session_id)
        candidates = [run for run in runs if (
            run['status'] in ('cancelled', 'interrupted', 'failed')
            or (stopped_at is not None and run['status'] == 'running'
                and run['started_at'] <= stopped_at)
        ) and (run.get('metadata', {}).get('user_context') or {}).get('web_ui')]
        if not candidates:
            return []
        represented = db.assistant_query_ids(session_id)
        recovered = []
        for run in candidates:
            qid = run['query_id']
            if qid in represented:
                continue
            # Do not revive deleted chats or non-chat/background harness runs.
            session = db.get_chat_session_by_id(session_id)
            if not session or not session.get('is_active'):
                return recovered
            cutoff = stopped_at if stopped_at is not None else run.get('finished_at') or time.time()
            writing, after, activity = [], 0, False
            while True:
                events = store.events(qid, after=after, limit=500)
                if not events:
                    break
                for event in events:
                    if event['ts'] > cutoff:
                        continue
                    if event['kind'] in ('tool', 'thinking', 'writing'):
                        activity = True
                    if event['kind'] == 'writing' and not event.get('compacted'):
                        try:
                            payload = (store.event(event['id']) or {}).get('detail') or {}
                        except (OSError, ValueError):
                            # Expired/damaged detail must not erase the run's
                            # durable identity and remaining activity link.
                            log.warning('Writing detail unavailable for %s event %s', qid, event['id'])
                            continue
                        text = str(payload.get('text') or '').strip()
                        if text and not text.startswith('[CANCELLED]'):
                            writing.append(_CONTROLS.sub('', text).strip())
                after = events[-1]['id']
            if not activity:
                continue
            status = 'cancelled' if run['status'] == 'running' else run['status']
            label = 'stopped' if status == 'cancelled' else 'interrupted' if status == 'interrupted' else 'failed'
            when = datetime.fromtimestamp(run['started_at'], timezone.utc).isoformat(timespec='seconds')
            content = f'**Agent {label} before completion.** Recorded activity from {when} is preserved in the query log.'
            if any(writing):
                content += '\n\nText captured before the turn ended:\n\n' + '\n\n'.join(filter(None, writing))
            metadata = {
                'kind': 'interrupted-agent-turn', 'query_id': qid,
                'report_url': f'/query_log.html?id={qid}',
                'interrupted_turn': {'status': status, 'started_at': run['started_at'], 'captured_until': cutoff},
                'project_path': run.get('project_root'),
                'slash_command_failed': True,
            }
            agent = run.get('agent_id') or 'agent'
            metadata['slash_command'] = {'chips': [{
                'label': agent.capitalize(), 'category': agent,
                'meta': f'/{agent} · {run.get("model") or ""}',
            }]}
            mid = db.add_message_once(
                session_id, content, delivery_key=f'interrupted-agent-turn:{qid}',
                metadata=metadata,
            )
            if mid:
                recovered.append(mid)
        return recovered
    except Exception:
        # A degraded event store must not prevent Stop or opening a transcript.
        log.exception('Could not preserve interrupted turns for chat %s', session_id)
        return []

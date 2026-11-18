"""Read-only projection of supervision activity for one supervisor chat.

Headlines and details are built here, server-side, so the Studio panel and
``python -m api.chat_cli timeline`` show the same words. Only facts the check
rows record are stated: when a row lacks one, the wording falls back rather than
guessing (no "ended by care" unless care killed it, no reason text a check never stored).
"""
from __future__ import annotations

import re

from api.chat_ops import store

# chat_ops.brief.template_nudge: the host's own nudge naming the open Tasks item.
_TEMPLATE_NUDGE = re.compile(r'^Supervision: ".*" still has open items\. Continue with the next one: \*\*(.+?)\*\*', re.S)
# chat_ops.checkpoints.milestone_lines: "Checked off in **<Tasks title>**: <item text>".
_CHECKED_OFF = re.compile(r'^Checked off in \*\*(.+?)\*\*: (.+)$', re.S)
# Why a stalled check fired, from the snapshot state the check recorded.
_STALL_REASON = {
    'busy': 'Busy on one turn with no Tasks progress',
    'waiting_background': 'Waiting on background work with no Tasks progress',
    'idle_with_work': 'Idle with open Tasks items',
}
_DETAIL_MAX = 300
_HEADLINE = {'nudged': 'Nudge delivered', 'paused': 'Supervision paused', 'resumed': 'Supervision resumed',
             'error': 'Supervision check failed'}


def _agent_label(agent_id):
    from api.agent_harness.catalog import get_agent
    found = get_agent(agent_id)
    return (found[0].label or agent_id) if found else agent_id.replace('-', ' ').title()


class _Names:
    """What a worker is called in a headline: its agent when known, else its chat
    title, else its CH- handle. One lookup per chat per projection."""

    def __init__(self, db=None):
        self._db = db
        self._cache = {}

    def __call__(self, target):
        key = int(target)
        if key not in self._cache:
            self._cache[key] = self._lookup(key)
        return self._cache[key]

    def _lookup(self, target):
        handle = f'CH-{target:06d}'
        from api.auth_db import get_auth_db
        db = self._db or get_auth_db()
        row = db.get_chat_session_by_id(target)
        if not row:
            return handle
        from api.chat_ops import service
        try:
            agent = service.chat_agent(db, row)
        except Exception:
            agent = ''
        if agent:
            return _agent_label(agent)
        return str(row.get('session_name') or '').strip() or handle


def _guard(d):
    branch = d.get('branch') or (d['state'].get('branch') if isinstance(d.get('state'), dict) else None)
    command = str(d.get('command') or '')
    pushes = bool(re.search(r'\bpush\b', command))
    if d.get('blocked'):
        verb = 'push' if pushes else 'commit'
        head = f'Guard: blocked a {verb} to `{branch}`' if branch else f'Guard: blocked a {verb}'
    elif branch and pushes:
        head = f'Guard: flagged a push to `{branch}`'
    elif branch:
        head = f'Guard: flagged a git command on `{branch}`'
    else:
        head = 'Guard: flagged a git command' if command else 'Guard: flagged a violation'
    if command:
        detail = f'`{command[:200]}`'
    else:
        detail = '; '.join(str(v) for v in d.get('violations') or [])
    return head, detail[:_DETAIL_MAX]


def _describe(kind, d, who):
    """(headline, detail) for a check or telemetry event of ``kind``; ``who`` names the worker."""
    if kind == 'guard':
        return _guard(d)
    if kind == 'stalled':
        minutes = d.get('stalled_min')
        head = f'{who} has been stuck for {minutes} min' if minutes else f'{who} looks stuck'
        return head, _STALL_REASON.get(d.get('state'), '')
    text = str(d.get('text') or d.get('error') or ', '.join(d.get('lines') or []))
    if kind == 'milestone':
        hits = [m for m in (_CHECKED_OFF.match(str(line)) for line in d.get('lines') or []) if m]
        if hits:
            head = f'{hits[0].group(2)} done' + (f' (+{len(hits) - 1} more)' if len(hits) > 1 else '')
            return head, f'Checked off in `{hits[0].group(1)}`'
        return 'Task milestone reached', text
    return _HEADLINE.get(kind, 'Supervision activity'), text


def project(session, *, before=None, limit=50, include_quiet=False, db=None):
    sid = int(session)
    limit = max(1, min(200, int(limit)))
    names = _Names(db)
    operations = store.timeline_operations(sid)
    ops = [o for o in operations if o['kind'] == 'supervise']
    schedules = {o['id']: o for o in ops}
    items = []
    oldest = {}
    for oid, op in schedules.items():
        rows = store.timeline_checks(oid)
        oldest[oid] = min((r['at'] for r in rows), default=float('inf'))
        snapshots = [row for row in reversed(rows) if 'process_snapshot' in row['detail']]
        from api.chat_ops.system_care import _size
        for previous, current in zip(snapshots, snapshots[1:]):
            old, new = previous['detail']['process_snapshot'], current['detail']['process_snapshot']
            if not old.get('measured') or not new.get('measured'):
                continue
            live = {(r['pid'], r['create_time']) for r in new['leftover']}
            for process in old['leftover']:
                identity = (process['pid'], process['create_time'])
                if identity in live or not all(identity):
                    continue
                pid = process['pid']
                items.append({'schema_version': 1, 'id': f'care:{oid}:{current["at"]}:{pid}:{identity[1]}',
                              'at': current['at'], 'kind': 'care', 'chat': f"CH-{int(op['target']):06d}",
                              'headline': 'System care: a leftover process ended',
                              'detail': f"`{process.get('name') or 'process'}` · PID {pid} · {_size(process['rss'])}",
                              'refs': {'pid': pid}, 'source': 'check'})
        for row in rows:
            outcome, d = row['outcome'], row['detail']
            if outcome == 'checkpoint' and d.get('checkpoint') != 'milestone':
                continue
            if outcome in ('skipped', 'unchanged', 'note', 'acted', 'early_wake') and not include_quiet:
                continue
            kind = {'violation':'guard','checkpoint':'milestone','paused_alert':'paused',
                    'resumed':'resumed','stalled':'stalled','error':'error',
                    'paused':'paused'}.get(outcome)
            if outcome in ('skipped', 'unchanged') and include_quiet: kind = outcome
            if not kind: continue
            headline, detail = _describe(kind, d, names(op['target']))
            branch = d.get('branch') or (d['state'].get('branch') if isinstance(d.get('state'), dict) else None)
            command = d.get('command')
            items.append({'schema_version':1,'id':f'check:{row["id"]}','at':row['at'],'kind':kind,
                          'chat':f"CH-{int(op['target']):06d}",'headline':headline,'detail':detail,
                          'refs':{k: v for k, v in {'branch': branch, 'command': command}.items() if v},'source':'check'})
    from api.orchestration_telemetry import store as telemetry
    for event in telemetry.events(kinds=('sup_schedule', 'sup_check')):
        if event['session_id'] != sid or event['batch_id'] not in schedules:
            continue
        if event['kind'] == 'sup_check':
            if event['at'] >= oldest[event['batch_id']]:
                continue
            kind = {'violation': 'guard', 'stalled': 'stalled', 'error': 'error',
                    'paused_alert': 'paused', 'resumed': 'resumed'}.get(event['status'])
            if include_quiet and event['status'] in ('skipped', 'unchanged'):
                kind = event['status']
        else:
            kind = {'pause': 'paused', 'resume': 'resumed'}.get(event['status'])
        if kind:
            op = schedules[event['batch_id']]
            headline, detail = _describe(kind, event['detail'] or {}, names(op['target']))
            items.append({'schema_version': 1, 'id': f"telemetry:{event['id']}", 'at': event['at'],
                          'kind': kind, 'chat': f"CH-{int(op['target']):06d}",
                          'headline': headline, 'detail': detail,
                          'refs': {}, 'source': 'telemetry'})
    for op in operations:
        if (op['kind'] != 'send' or op['state'] not in ('delivered', 'done')
                or op['target'] not in {s['target'] for s in schedules.values()}):
            continue
        text = str(op['payload'].get('text') or '')
        template = _TEMPLATE_NUDGE.match(text)
        if template:
            headline, detail = f'Nudged {names(op["target"])} with the next task', f'"{template.group(1)}"'
        else:
            headline, detail = f'Nudged {names(op["target"])}', f'"{text[:_DETAIL_MAX]}"' if text else ''
        items.append({'schema_version':1,'id':'send:'+op['id'],'at':op['updated'],'kind':'nudged',
                      'chat':f"CH-{int(op['target']):06d}",'headline':headline,
                      'detail':detail,'refs':{},'source':'operation'})
    items.sort(key=lambda x:(x['at'],x['id']), reverse=True)
    seen=set(); unique=[]
    for x in items:
        if x['id'] in seen: continue
        seen.add(x['id']); unique.append(x)
    if before is not None:
        try:
            timestamp, separator, ident = str(before).partition('|')
            key = (float(timestamp), ident if separator else '')
        except ValueError:
            raise ValueError('Invalid timeline cursor') from None
        unique = [x for x in unique if (x['at'], x['id']) < key]
    cursor = f"{unique[limit-1]['at']}|{unique[limit-1]['id']}" if len(unique) > limit else None
    return {'schema_version': 1, 'items': unique[:limit], 'next_before': cursor}

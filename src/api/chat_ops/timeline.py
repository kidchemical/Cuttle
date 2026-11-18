"""Read-only projection of supervision activity for one supervisor chat."""
from __future__ import annotations

from api.chat_ops import store


def project(session, *, before=None, limit=50, include_quiet=False):
    sid = int(session)
    limit = max(1, min(200, int(limit)))
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
                              'headline': f"Leftover process ended: {process['name']} (PID {pid}, {_size(process['rss'])})",
                              'detail': '', 'refs': {'pid': pid}, 'source': 'check'})
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
            text = str(d.get('text') or d.get('error') or ', '.join(d.get('lines') or []))
            branch = d.get('branch') or (d['state'].get('branch') if isinstance(d.get('state'), dict) else None)
            command = d.get('command')
            guard_headline = (f'Guard blocked a commit to {branch}' if d.get('blocked') and branch
                              else f'Flagged a forbidden command: {command}' if command
                              else 'Guard flagged a violation')
            headline = {'guard': guard_headline,
                        'nudged':'Nudge delivered', 'stalled':'Chat stalled', 'milestone':'Task milestone reached',
                        'paused':'Supervision paused', 'resumed':'Supervision resumed', 'error':'Supervision check failed'}.get(kind, 'Supervision activity')
            items.append({'schema_version':1,'id':f'check:{row["id"]}','at':row['at'],'kind':kind,
                          'chat':f"CH-{int(op['target']):06d}",'headline':headline,'detail':text,
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
            items.append({'schema_version': 1, 'id': f"telemetry:{event['id']}", 'at': event['at'],
                          'kind': kind, 'chat': f"CH-{int(op['target']):06d}",
                          'headline': {'guard': 'Guard flagged a violation', 'stalled': 'Chat stalled',
                                       'error': 'Supervision check failed'}.get(kind, f'Supervision {kind}'),
                          'detail': '; '.join(event['detail'].get('violations') or []),
                          'refs': {}, 'source': 'telemetry'})
    for op in operations:
        if (op['kind'] != 'send' or op['state'] not in ('delivered', 'done')
                or op['target'] not in {s['target'] for s in schedules.values()}):
            continue
        items.append({'schema_version':1,'id':'send:'+op['id'],'at':op['updated'],'kind':'nudged',
                      'chat':f"CH-{int(op['target']):06d}",'headline':'Nudge delivered',
                      'detail':str(op['payload'].get('text') or ''),'refs':{},'source':'operation'})
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

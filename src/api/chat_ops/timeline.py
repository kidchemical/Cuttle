"""Read-only projection of supervision activity for one supervisor chat."""
from __future__ import annotations

from api.chat_ops import store


def project(session, *, before=None, limit=50, include_quiet=False):
    sid = int(session)
    limit = max(1, min(200, int(limit)))
    ops = [o for o in store.listing(sid) if o['kind'] == 'supervise']
    schedules = {o['id']: o for o in ops}
    items = []
    for oid, op in schedules.items():
        for row in store.checks(oid, limit=10000):
            outcome, d = row['outcome'], row['detail']
            if outcome in ('skipped', 'unchanged', 'note', 'acted', 'early_wake') and not include_quiet:
                continue
            kind = {'violation':'guard','checkpoint':'milestone','paused_alert':'paused',
                    'resumed':'resumed','nudged':'nudged','stalled':'stalled','error':'error',
                    'paused':'paused'}.get(outcome)
            if not kind and include_quiet: kind = 'error'
            if not kind: continue
            text = str(d.get('text') or d.get('error') or ', '.join(d.get('lines') or []))
            branch = d.get('branch') or (d.get('state') or {}).get('branch')
            command = d.get('command')
            guard_headline = (f'Guard blocked a commit to {branch}' if d.get('blocked') and branch
                              else f'Flagged a forbidden command: {command}' if command
                              else 'Guard flagged a violation')
            headline = {'guard': guard_headline,
                        'nudged':'Nudge delivered', 'stalled':'Chat stalled', 'milestone':'Task milestone reached',
                        'paused':'Supervision paused', 'resumed':'Supervision resumed', 'error':'Supervision check failed'}.get(kind, 'Supervision activity')
            items.append({'schema_version':1,'id':f'check:{oid}:{row["at"]}:{outcome}','at':row['at'],'kind':kind,
                          'chat':f"CH-{int(op['target']):06d}",'headline':headline,'detail':text,
                          'refs':{k: v for k, v in {'branch': branch, 'command': command}.items() if v},'source':'check'})
    for op in store.listing(sid):
        if op['kind'] != 'send' or op['state'] not in ('delivered','done'):
            continue
        items.append({'schema_version':1,'id':'send:'+op['id'],'at':op['updated'],'kind':'nudged',
                      'chat':f"CH-{int(op['target']):06d}",'headline':'Nudge delivered',
                      'detail':str(op['payload'].get('text') or ''),'refs':{},'source':'operation'})
    items.sort(key=lambda x:(x['at'],x['id']), reverse=True)
    seen=set(); unique=[]
    for x in items:
        if x['id'] in seen: continue
        seen.add(x['id']); unique.append(x)
    if before is not None: unique=[x for x in unique if x['at'] < float(before)]
    return {'schema_version':1,'items':unique[:limit], 'next_before':unique[limit-1]['at'] if len(unique)>limit else None}

"""Coordinate owner-specific maintenance without changing execution lifetime."""
from pathlib import Path
from .store import EventStore,state_dir
from .snapshots import git
from api.storage.service import policy


def starred_sessions(store):
    from api.auth_db import get_auth_db
    with store.connection() as conn:
        ids=[r[0] for r in conn.execute('SELECT DISTINCT chat_session_id FROM runs') if str(r[0] or '').isdigit()]
    db=get_auth_db()
    return [s for s in ids if (db.get_chat_session_by_id(int(s)) or {}).get('starred')]


def prune(store=None):
    store=store or EventStore(state_dir())
    from api.edit_attribution.journal import reconcile_commits
    settlement=reconcile_commits(store.path)
    result=store.maintain(policy(),starred=starred_sessions(store))
    result['settlement']=settlement
    release_refs(store)
    result.update(store.vacuum())
    result['over_quota']=result['bytes']>policy()['quota_mb']*1_000_000
    return result


def release_refs(store,all_completed=False):
    with store.connection() as conn:
        rows=conn.execute("SELECT e.* FROM events e JOIN runs r USING(query_id) WHERE e.kind='edit' AND r.status!='running'").fetchall()
    for row in rows:
        if not (all_completed or row['compacted'] in ('quota','retention')):
            continue
        body=store.decode(row)
        if body.get('source')!='snapshot' or not body.get('repo_root'):
            continue
        if not Path(body['repo_root']).is_dir():continue
        prefix=f"refs/cuttle/turns/{row['query_id']}/"
        for ref in git(body['repo_root'],'for-each-ref','--format=%(refname)',prefix).splitlines():
            git(body['repo_root'],'update-ref','-d',ref)

    # Also release refs for failed/crashed captures without a final edit event.
    selected_policy=policy()
    import time
    stars=set(str(s) for s in starred_sessions(store))
    with store.connection() as conn:
        snapshots=conn.execute("SELECT s.*,r.finished_at,r.chat_session_id FROM snapshots s JOIN runs r USING(query_id) WHERE r.status!='running'").fetchall()
    for snapshot in snapshots:
        days=selected_policy['starred_retention_days'] if snapshot['chat_session_id'] in stars else selected_policy['retention_days']
        if days==-1:days=selected_policy['retention_days']
        if not all_completed and (not days or not snapshot['finished_at'] or time.time()-snapshot['finished_at']<=days*86400):
            continue
        if not Path(snapshot['repo_root']).is_dir():continue
        prefix=f"refs/cuttle/turns/{snapshot['query_id']}/"
        for ref in git(snapshot['repo_root'],'for-each-ref','--format=%(refname)',prefix).splitlines():
            git(snapshot['repo_root'],'update-ref','-d',ref)
        with store.connection() as conn:
            conn.execute('DELETE FROM snapshots WHERE query_id=?',(snapshot['query_id'],))

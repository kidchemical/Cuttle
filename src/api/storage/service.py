"""Validated per-store settings, independent of Flask and frontend state.

Each store is owned by its own slice (``owner``). The registry declares what
the generic surface may do with a store; destructive work runs through the
owner's public path/policy helpers, never through copied internals.
Stores without an owner maintenance contract expose sizes only.
"""
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from api.agent_events.store import EventStore, state_dir

DEFAULTS={'retention_days':90,'quota_mb':5000,'thinking_full_days':14,
          'thinking_mode':'full_then_summary','starred_retention_days':-1}

# Reset policies: NEVER (no reset control), TYPED_CONFIRM (caller must pass
# confirm=<store id>; agents should emit a confirm form first), ALLOWED.
NEVER='never'
TYPED_CONFIRM='typed_confirm'
ALLOWED='allowed'

SIMPLE_FIELDS=('retention_days',)


@dataclass(frozen=True)
class StoreSpec:
    id: str
    label: str
    owner: str
    configurable: bool=False
    reset: str=NEVER
    retention_default: Optional[int]=None
    supports_prune: bool=False
    supports_vacuum: bool=False
    no_prune_reason: str=''


REGISTRY=(StoreSpec('agent_events','Agent events','api.agent_events',True,TYPED_CONFIRM,90,True,True),
          StoreSpec('chats','Chats and accounts','api.auth_db'),
          StoreSpec('query_logs','Legacy query logs','api.query_tracker',True,TYPED_CONFIRM,90,True,
                    no_prune_reason=''),
          StoreSpec('edit_journal','Legacy edit journal','api.edit_attribution',False,TYPED_CONFIRM,None,False,
                    no_prune_reason='Read-only rollback artifact; prune is not supported, reset only.'),
          StoreSpec('router_outcomes','Router outcomes','api.agent_router.outcomes',True,TYPED_CONFIRM,90,True,
                    no_prune_reason=''),
          StoreSpec('device_workers','Device workers','api.device_workers.store'),
          StoreSpec('brain_metrics','Brain metrics','api.cuttle_brain.metrics',False,ALLOWED,None,True,
                    no_prune_reason=''),
          StoreSpec('outputs','Shared output and uploads','api.shared_media',True,ALLOWED,7,True,
                    no_prune_reason=''))


def _spec(ident):
    for spec in REGISTRY:
        if spec.id==ident:
            return spec
    raise ValueError('Unknown store: '+str(ident))


def validate(raw,current=None):
    if not isinstance(raw,dict):
        raise ValueError('Policy must be an object')
    if set(raw)-set(DEFAULTS):
        raise ValueError('Unknown storage policy field')
    out={**DEFAULTS,**(current or {}),**raw}
    for name in ('retention_days','thinking_full_days','starred_retention_days','quota_mb'):
        value=out[name]
        if isinstance(value,bool) or not isinstance(value,int):
            raise ValueError(name+' must be an integer')
        minimum=1 if name in ('retention_days','quota_mb') else -1 if name=='starred_retention_days' else 0
        if value<minimum or value>1_000_000:
            raise ValueError(name+' is outside the supported range')
    if out['thinking_full_days']>out['retention_days']:
        raise ValueError('Full thinking days cannot exceed retention days')
    if out['thinking_mode'] not in ('full_then_summary','summary_only'):
        raise ValueError('Unknown thinking mode')
    # Zero starred days means exemption from age, still subject to quota.
    return out


def _simple_defaults(spec):
    return {'retention_days':spec.retention_default}


def _validate_simple(spec,raw,current=None):
    if not isinstance(raw,dict):
        raise ValueError('Policy must be an object')
    if set(raw)-set(SIMPLE_FIELDS):
        raise ValueError('Only retention_days is configurable for '+spec.id)
    out={**_simple_defaults(spec),**(current or {}),**raw}
    value=out['retention_days']
    if isinstance(value,bool) or not isinstance(value,int):
        raise ValueError('retention_days must be an integer')
    if value<1 or value>1_000_000:
        raise ValueError('retention_days is outside the supported range')
    return out


def _stored(manager):
    stored=manager.get_setting('storage',{})
    return stored if isinstance(stored,dict) else {}


def policy(ident='agent_events',manager=None):
    spec=_spec(ident)
    if manager is None:
        from managers.settings_manager import get_settings_manager
        manager=get_settings_manager()
    raw=_stored(manager).get(ident,{})
    try:
        if ident=='agent_events':
            return validate(raw if isinstance(raw,dict) else {})
        if not spec.configurable:
            return {}
        return _validate_simple(spec,raw if isinstance(raw,dict) else {})
    except (ValueError,AttributeError):
        return dict(DEFAULTS) if ident=='agent_events' else _simple_defaults(spec)


def set_policy(ident,raw,manager=None):
    spec=_spec(ident)
    if not spec.configurable:
        raise ValueError('This store does not support editable retention or quotas yet')
    if manager is None:
        from managers.settings_manager import get_settings_manager
        manager=get_settings_manager()
    if ident=='agent_events':
        selected=validate(raw,policy(ident,manager))
    else:
        selected=_validate_simple(spec,raw,policy(ident,manager))
    def update(current):
        current=dict(current or {})
        current[ident]=selected
        return current
    if not manager.update_setting('storage',update):
        raise RuntimeError('Storage settings could not be saved')
    return selected


def _store_files(ident):
    from core.runtime_paths import cuttle_home, output_dir, query_logs_dir
    base=cuttle_home()
    return {'chats':[base/'db'/'cuttle_auth.db'],
            'query_logs':[query_logs_dir()],
            'router_outcomes':[base/'db'/'router_outcomes.db'],
            'device_workers':[base/'db'/'device_workers.db'],
            'brain_metrics':[base/'brain'/'context_metrics.db'],
            'outputs':[output_dir()],
            'edit_journal':[base/'edit_attribution'/'edit_journal.sqlite3']}[ident]


def _dir_size(paths):
    files=[]
    for path in paths:
        files.extend(path.rglob('*') if path.is_dir() else [path,Path(str(path)+'-wal'),Path(str(path)+'-shm')])
    return sum(p.stat().st_size for p in files if p.is_file())


def _prune_query_logs(retention_days):
    from api.query_events import logs_dir
    root=logs_dir()
    cutoff=time.time()-retention_days*86400
    deleted=[]
    if root.is_dir():
        for path in root.iterdir():
            if not path.is_file() or path.name.startswith('.'):
                continue
            try:
                if path.stat().st_mtime>cutoff:
                    continue
                path.unlink()
                deleted.append(path.name)
            except OSError:
                continue
    return {'deleted':len(deleted)}


def _reset_query_logs():
    from api.query_events import logs_dir
    root=logs_dir()
    deleted=0
    if root.is_dir():
        for path in root.iterdir():
            if not path.is_file() or path.name.startswith('.'):
                continue
            try:
                path.unlink()
                deleted+=1
            except OSError:
                continue
    return {'deleted':deleted}


def _reset_edit_journal():
    from core.runtime_paths import cuttle_home
    path=cuttle_home()/'edit_attribution'/'edit_journal.sqlite3'
    removed=False
    for sibling in (path,Path(str(path)+'-wal'),Path(str(path)+'-shm')):
        try:
            if sibling.is_file():
                sibling.unlink()
                removed=True
        except OSError:
            continue
    return {'removed':removed}


def _prune_router_outcomes(retention_days):
    from api.agent_router.outcomes import database_path
    cutoff=time.time()-retention_days*86400
    path=database_path()
    if not path.is_file():
        return {'deleted':0}
    conn=sqlite3.connect(str(path),timeout=30.0)
    try:
        conn.execute('DELETE FROM router_outcomes WHERE recorded_at<?',(cutoff,))
        deleted=conn.total_changes
        conn.commit()
        conn.execute('VACUUM')
        conn.commit()
    finally:
        conn.close()
    return {'deleted':deleted}


def _reset_router_outcomes():
    from api.agent_router.outcomes import database_path
    path=database_path()
    if not path.is_file():
        return {'deleted':0}
    conn=sqlite3.connect(str(path),timeout=30.0)
    try:
        conn.execute('DELETE FROM router_outcomes')
        deleted=conn.total_changes
        conn.commit()
        conn.execute('VACUUM')
        conn.commit()
    finally:
        conn.close()
    return {'deleted':deleted,'warning':'Router history and achievement inputs were cleared'}


def _prune_brain_metrics():
    from api.cuttle_brain.state import prune
    return prune()


def _reset_brain_metrics():
    from api.cuttle_brain.metrics import database_path
    path=database_path()
    removed=False
    for sibling in (path,Path(str(path)+'-wal'),Path(str(path)+'-shm')):
        try:
            if sibling.is_file():
                sibling.unlink()
                removed=True
        except OSError:
            continue
    return {'removed':removed}


def _prune_outputs(retention_days):
    from api.shared_media import purge_expired
    return purge_expired(ttl=retention_days)


def _reset_outputs():
    from api.shared_media import shared_media_root
    root=shared_media_root()
    deleted=0
    if root.is_dir():
        for path in root.iterdir():
            if not path.is_file() or path.name.startswith('.'):
                continue
            try:
                path.unlink()
                deleted+=1
            except OSError:
                continue
    return {'deleted':deleted}


def _prune_store(ident,retention_days):
    if ident=='query_logs':
        return _prune_query_logs(retention_days)
    if ident=='router_outcomes':
        return _prune_router_outcomes(retention_days)
    if ident=='brain_metrics':
        return _prune_brain_metrics()
    if ident=='outputs':
        return _prune_outputs(retention_days)
    raise ValueError('This store does not support pruning')


def _prune_with_policy(ident,manager=None):
    spec=_spec(ident)
    if spec.retention_default is None:
        return _prune_store(ident,None)
    return _prune_store(ident,policy(ident,manager)['retention_days'])


def _reset_store(ident):
    if ident=='query_logs':
        return _reset_query_logs()
    if ident=='edit_journal':
        return _reset_edit_journal()
    if ident=='router_outcomes':
        return _reset_router_outcomes()
    if ident=='brain_metrics':
        return _reset_brain_metrics()
    if ident=='outputs':
        return _reset_outputs()
    raise ValueError('This store does not support reset')


def operate(ident,operation,confirm=None,manager=None):
    """Run prune/vacuum/reset for one store through its owning service.

    ``all`` fans prune out to every store with an owner prune contract.
    Reset on TYPED_CONFIRM stores requires ``confirm == <store id>``.
    """
    if operation=='prune' and ident=='all':
        results={}
        for spec in REGISTRY:
            if not spec.supports_prune:
                continue
            try:
                results[spec.id]=operate(spec.id,'prune',manager=manager)
            except ValueError as exc:
                results[spec.id]={'error':str(exc)}
        return results
    spec=_spec(ident)
    if operation=='prune':
        if not spec.supports_prune:
            raise ValueError(spec.no_prune_reason or 'This store does not support pruning')
        if ident=='agent_events':
            from api.agent_events.service import operate as agent_operate
            return agent_operate(ident,operation,confirm)
        return _prune_with_policy(ident,manager)
    if operation=='vacuum':
        if ident=='agent_events':
            from api.agent_events.service import operate as agent_operate
            return agent_operate(ident,operation,confirm)
        raise ValueError('This store has no owner vacuum contract')
    if operation=='reset':
        if spec.reset==NEVER:
            raise ValueError('This store cannot be reset')
        if spec.reset==TYPED_CONFIRM and confirm!=ident:
            raise ValueError('Type '+ident+' to confirm the reset')
        if ident=='agent_events':
            from api.agent_events.service import operate as agent_operate
            return agent_operate(ident,operation,confirm)
        return _reset_store(ident)
    raise ValueError('Unknown maintenance operation')


def list_stores(manager=None):
    rows=[]
    for spec in REGISTRY:
        if spec.id=='agent_events':
            stats=EventStore(state_dir()).stats()
            stats['over_quota']=stats['bytes']>policy(manager=manager)['quota_mb']*1_000_000
        else:
            stats={'bytes':_dir_size(_store_files(spec.id))}
        row={**spec.__dict__,**stats}
        row['reset_policy']=row.pop('reset')
        row['reset']=row['reset_policy']!=NEVER
        row['policy']=policy(spec.id,manager) if spec.configurable else None
        rows.append(row)
    return rows

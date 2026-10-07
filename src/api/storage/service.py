"""Validated per-store settings, independent of Flask and frontend state."""
from dataclasses import dataclass
from pathlib import Path
from api.agent_events.store import EventStore, state_dir

DEFAULTS={'retention_days':90,'quota_mb':5000,'thinking_full_days':14,
          'thinking_mode':'full_then_summary','starred_retention_days':-1}


@dataclass(frozen=True)
class StoreSpec:
    id: str
    label: str
    owner: str
    configurable: bool=False
    reset: bool=False


REGISTRY=(StoreSpec('agent_events','Agent events','api.agent_events',True,True),
          StoreSpec('chats','Chats and accounts','api.auth_db'),
          StoreSpec('query_logs','Legacy query logs','api.query_tracker'),
          StoreSpec('edit_journal','Legacy edit journal','api.edit_attribution'),
          StoreSpec('router_outcomes','Router outcomes','api.agent_router.outcomes'),
          StoreSpec('device_workers','Device workers','api.device_workers.store'),
          StoreSpec('brain_metrics','Brain metrics','api.cuttle_brain.metrics'),
          StoreSpec('outputs','Shared output and uploads','api.shared_media'))


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


def policy(manager=None):
    if manager is None:
        from managers.settings_manager import get_settings_manager
        manager=get_settings_manager()
    stored=manager.get_setting('storage',{})
    try:
        return validate(stored.get('agent_events',{}))
    except (ValueError,AttributeError):
        return dict(DEFAULTS)


def set_policy(ident,raw,manager=None):
    if ident!='agent_events':
        raise ValueError('This store does not support editable retention or quotas yet')
    if manager is None:
        from managers.settings_manager import get_settings_manager
        manager=get_settings_manager()
    selected=validate(raw,policy(manager))
    def update(current):
        current=dict(current or {})
        current[ident]=selected
        return current
    if not manager.update_setting('storage',update):
        raise RuntimeError('Storage settings could not be saved')
    return selected


def list_stores():
    from core.runtime_paths import cuttle_home, output_dir, query_logs_dir
    base=cuttle_home()
    paths={'chats':[base/'db'/'cuttle_auth.db'],
           'query_logs':[query_logs_dir()],
           'router_outcomes':[base/'db'/'router_outcomes.db'],
           'device_workers':[base/'db'/'device_workers.db'],
           'brain_metrics':[base/'brain'/'context_metrics.db'],
           'outputs':[output_dir()],
           'edit_journal':[base/'edit_attribution'/'edit_journal.sqlite3']}
    rows=[]
    for spec in REGISTRY:
        if spec.id=='agent_events':
            stats=EventStore(state_dir()).stats()
            stats['over_quota']=stats['bytes']>policy()['quota_mb']*1_000_000

        else:
            files=[]
            for path in paths[spec.id]:
                files.extend(path.rglob('*') if path.is_dir() else [path,Path(str(path)+'-wal'),Path(str(path)+'-shm')])
            stats={'bytes':sum(p.stat().st_size for p in files if p.is_file())}
        rows.append({**spec.__dict__,**stats,'policy':policy() if spec.configurable else None})
    return rows

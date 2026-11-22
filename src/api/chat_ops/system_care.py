"""Process and memory care for supervision check-ins (schedule setting ``system``).

The host measures; the supervisor decides. Each check reads what the setting
turned on and hands the worker one picture:

* ``processes``: OS processes the supervised chat or its sub-agents still own
  although their turns ended (``api.jobs.work_tree`` owners in state
  ``processes``). A root the supervisor has not been told about wakes it once.
* ``memory``: machine memory (psutil) and the kernel's OOM-kill counter
  (``/proc/vmstat``, Linux). Low memory wakes the supervisor once per episode,
  a new OOM kill every time, a working target included.
* ``display``: owned by ``api.chat_ops.display_care`` (keeps the display awake,
  reports it off); its picture rides along under ``display``.

Nothing here ends a process: the check-in names
``python -m api.jobs end-process`` (identity-checked, Cuttle hosts refused).
The pure part (``leftover``, ``memory_state``, ``section``) takes plain dicts;
``read`` is the only function that touches the machine and never raises.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Iterable, List, Mapping, Optional

from api.chat_ops import brief

# Available memory below this share of the total counts as low.
MEMORY_LOW_PERCENT = 10
# A target in these states may still be using what it started.
_WORKING = ('busy', 'waiting_background')
_TOP_CONSUMERS = 5
_TOP_MIN_BYTES = 200 * 1024 * 1024
_LEFTOVER_MAX = 12


# -- pure ----------------------------------------------------------------------


def _tree_rss(node: Mapping[str, Any]) -> int:
    return int(node.get('rss') or 0) + sum(_tree_rss(c) for c in node.get('children') or ())


def _tree_count(node: Mapping[str, Any]) -> int:
    return 1 + sum(_tree_count(c) for c in node.get('children') or ())


def _owners(owners: Iterable[Mapping[str, Any]]):
    for owner in owners or ():
        yield owner
        yield from _owners(owner.get('children') or ())


def _row(owner: Mapping[str, Any], node: Mapping[str, Any]) -> Dict[str, Any]:
    return {'handle': owner.get('handle') or '', 'pid': int(node.get('pid') or 0),
            'create_time': float(node.get('create_time') or 0), 'name': node.get('name') or '',
            'cmdline': str(node.get('cmdline') or '')[:120], 'rss': _tree_rss(node),
            'count': _tree_count(node), 'detached': bool(node.get('detached')),
            'state': owner.get('state') or ''}


def leftover(tree: Mapping[str, Any], chats: Iterable[Any]) -> List[Dict[str, Any]]:
    """Process roots of ``chats`` whose owner has no live turn, wait or watch."""
    wanted = {str(c) for c in chats}
    rows = [_row(owner, node) for owner in _owners(tree.get('owners'))
            if str(owner.get('chat_session_id')) in wanted and owner.get('state') == 'processes'
            for node in owner.get('processes') or ()]
    return sorted(rows, key=lambda r: -r['rss'])


def consumers(tree: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """The largest agent-owned process trees on the machine, any chat."""
    rows = [_row(owner, node) for owner in _owners(tree.get('owners')) for node in owner.get('processes') or ()]
    return sorted((r for r in rows if r['rss'] >= _TOP_MIN_BYTES), key=lambda r: -r['rss'])[:_TOP_CONSUMERS]


def memory_state(reading: Optional[Mapping[str, Any]], previous: Mapping[str, Any]) -> Dict[str, Any]:
    """``{low, oom_new, alert, tracking}`` for one reading against the last check."""
    if not reading or not reading.get('total'):
        return {'low': False, 'oom_new': 0, 'alert': False, 'tracking': {}}
    low = reading['available'] * 100 / reading['total'] < MEMORY_LOW_PERCENT
    kills, before = reading.get('oom_kills'), previous.get('oom_kills')
    # The counter restarts at boot: a lower value is a new baseline, not news.
    oom_new = kills - before if kills is not None and before is not None and kills > before else 0
    return {'low': low, 'oom_new': oom_new,
            'alert': oom_new > 0 or (low and not previous.get('memory_alerted')),
            'tracking': {'oom_kills': kills, 'memory_alerted': low}}


def _size(n: Any) -> str:
    n = float(n or 0)
    return f'{n / 1024 ** 3:.1f} GB' if n >= 1024 ** 3 else f'{n / 1024 ** 2:.0f} MB'


def _age(create_time: float, now: float) -> str:
    sec = max(0, int(now - create_time)) if create_time else 0
    return f'{sec // 3600}h{sec % 3600 // 60:02d}m' if sec >= 3600 else f'{sec // 60}m'


def _process_line(row: Mapping[str, Any], now: float) -> str:
    more = f" +{row['count'] - 1} children" if row['count'] > 1 else ''
    return (f"- {row['handle']} PID {row['pid']} (create time {row['create_time']}): `{row['cmdline'] or row['name']}`"
            f"{more} · {_size(row['rss'])} · up {_age(row['create_time'], now)}"
            + (' · detached' if row['detached'] else ''))


_END = '`python -m api.jobs end-process <PID> --create-time <T>`'


def section(picture: Optional[Mapping[str, Any]], *, now: Optional[float] = None) -> List[str]:
    """Check-in lines for a picture from ``read`` ([] when there is nothing to say)."""
    picture = picture or {}
    now = now or time.time()
    lines: List[str] = []
    rows = picture.get('leftover') or []
    if rows:
        lines += ['', f"**Leftover processes** ({_size(sum(r['rss'] for r in rows))}): started by these chats and "
                  'still running although their turns ended.']
        lines += [_process_line(r, now) for r in rows[:_LEFTOVER_MAX]]
        if len(rows) > _LEFTOVER_MAX:
            lines.append(f'- … {len(rows) - _LEFTOVER_MAX} more (`python -m api.jobs work-tree --json`)')
        lines.append(f'End the ones nothing still needs with {_END} (it ends the whole tree and refuses Cuttle '
                     'hosts). Leave a server, build, editor or app that the remaining work or the user still '
                     'uses; when you cannot tell, ask the chat before ending it.')
    mem, state = picture.get('memory') or {}, picture.get('memory_state') or {}
    if mem and (state.get('low') or state.get('oom_new')):
        swap = (f", swap {round(mem['swap_used'] * 100 / mem['swap_total'])}% used"
                if mem.get('swap_total') else ', no swap')
        kills = (f" The kernel killed {state['oom_new']} process(es) for memory since the last check."
                 if state.get('oom_new') else '')
        lines += ['', f"**Host memory {'low' if state.get('low') else 'pressure'}:** {_size(mem['available'])} of "
                  f"{_size(mem['total'])} available ({round(mem['available'] * 100 / mem['total'])}%){swap}.{kills}"]
        top = picture.get('consumers') or []
        if top:
            lines.append('Largest agent-owned processes:')
            lines += [_process_line(r, now) + f" · chat is {_OWNER_STATE.get(r['state'], r['state'])}" for r in top]
        lines.append('Free memory by ending leftover processes of chats that are not mid-turn '
                     f'({_END}; `python -m api.chat_cli status CH-…` says whether a chat is working). Never end '
                     "a working chat's processes to make room, and do not change machine memory settings "
                     '(swap, sysctl, OOM policy): tell the user what is using the memory instead.')
    from api.chat_ops import display_care
    lines += display_care.section(picture.get('display'))
    return lines


_OWNER_STATE = {'running': 'mid-turn', 'waiting': 'waiting on background work', 'watch': 'watching a job',
                'processes': 'not working (leftover)'}


# -- effects -------------------------------------------------------------------


def _work_tree() -> Dict[str, Any]:
    from api.jobs.work_tree import work_tree
    return work_tree()


def _oom_kills() -> Optional[int]:
    try:
        with open('/proc/vmstat', encoding='ascii') as fh:
            for line in fh:
                if line.startswith('oom_kill '):
                    return int(line.split()[1])
    except (OSError, ValueError):
        pass
    return None


def _memory() -> Optional[Dict[str, Any]]:
    import psutil
    vm, swap = psutil.virtual_memory(), psutil.swap_memory()
    return {'total': int(vm.total), 'available': int(vm.available), 'swap_total': int(swap.total),
            'swap_used': int(swap.used), 'oom_kills': _oom_kills()}


def read(cfg: Mapping[str, Any], sid: Any, state: str, previous: Mapping[str, Any], *,
         children: Iterable[Any] = ()) -> Dict[str, Any]:
    """One check's picture: ``{leftover, memory, memory_state, consumers, display,
    alert, tracking, seen}``. ``children``: the target's sub-agent chat ids. ``tracking``
    goes into the schedule result; ``alert`` wakes the supervisor."""
    on = brief.system(cfg.get('system'))
    out: Dict[str, Any] = {'leftover': [], 'measured': False, 'alert': False, 'tracking': {}, 'seen': {}}
    tree = None
    if on['processes'] and state not in _WORKING:
        try:
            tree = _work_tree()
            out['leftover'] = leftover(tree, [sid, *children])
            out['measured'] = True
        except Exception:
            pass
        ids = sorted(f"{r['pid']}:{r['create_time']}" for r in out['leftover'])
        told = previous.get('leftover_reported') or []
        out['alert'] = any(i not in told for i in ids)
        out['tracking']['leftover_reported'] = ids
        if ids:
            out['seen']['leftover_processes'] = len(ids)
    elif on['processes']:
        out['tracking']['leftover_reported'] = previous.get('leftover_reported') or []
    if on['memory']:
        try:
            out['memory'] = _memory()
        except Exception:
            out['memory'] = None
        out['memory_state'] = memory_state(out['memory'], previous)
        out['tracking'].update(out['memory_state']['tracking'])
        if out['memory_state']['low'] or out['memory_state']['oom_new']:
            out['seen']['memory_low'] = True
            try:
                out['consumers'] = consumers(tree or _work_tree())
            except Exception:
                out['consumers'] = []
        out['alert'] = out['alert'] or out['memory_state']['alert']
    from api.chat_ops import display_care
    out['display'] = display_care.read(cfg, state, previous)
    out['tracking'].update(out['display']['tracking'])
    out['seen'].update(out['display']['seen'])
    out['alert'] = out['alert'] or out['display']['alert']
    return out

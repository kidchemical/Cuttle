"""Agent Tasks commands, usable during planning and throughout a live turn."""
import json
import os
from api.gizmos import tasks
from api.gizmos.service import GizmoError
from core.agent_cli_env import operation_actor


def add_parser(sub):
    parser = sub.add_parser('tasks', help='chat/project Tasks gizmos (always available)')
    verbs = parser.add_subparsers(dest='task_cmd', required=True)
    for verb in ('list', 'get', 'create', 'patch', 'history'):
        p = verbs.add_parser(verb)
        p.add_argument('--session', default=os.getenv('CUTTLE_CHAT_SESSION_ID'), help='current CH-… (normally inherited)')
        p.add_argument('--actor-agent', default=None, help='agent identity for standalone calls')
        if verb not in ('list', 'create'):
            p.add_argument('gizmo_id')
        if verb == 'list':
            p.add_argument('--status', choices=['active', 'archived'], default='active')
        if verb == 'create':
            p.add_argument('--id', dest='gizmo_id')
            p.add_argument('--title', default='Tasks')
            p.add_argument('--items', required=True, help='nonempty JSON array of tasks')
            p.add_argument('--scope', choices=['session', 'project'], default='session')
            p.add_argument('--edit-mode', choices=['agent', 'shared'], default='agent')
            p.add_argument('--description', default='')
            p.add_argument('--link', action='append', metavar='CH-…',
                           help='also show this list in another chat (repeatable, comma-separated)')
        if verb == 'patch':
            p.add_argument('--ops', help='JSON patch object: add, remove, set_text, items, description, …')
            p.add_argument('--set-done', action='append', help='item id(s), comma-separated; repeatable')
            p.add_argument('--set-undone', action='append', help='item id(s), comma-separated; repeatable')
            p.add_argument('--owner', action='append', metavar='ITEM=OWNER',
                           help='who owns an item: ITEM=CH-… (a chat of yours), ITEM=user, '
                                'ITEM=agent:<id>, or ITEM=none to clear; repeatable')
            p.add_argument('--label', action='append', metavar='ITEM=LABEL',
                           help='short display label (1-3 words) for an item; ITEM= clears; repeatable')
            p.add_argument('--working', action='append', metavar='ITEM',
                           help='mark item(s) as being worked on, comma-separated; repeatable')
            p.add_argument('--not-working', action='append', metavar='ITEM',
                           help='clear the in-progress mark, comma-separated; repeatable')
            p.add_argument('--hide', action='append', metavar='ITEM',
                           help='hide item(s) from the chat UI (still in get/context), comma-separated; repeatable')
            p.add_argument('--unhide', action='append', metavar='ITEM',
                           help='show previously hidden item(s) again, comma-separated; repeatable')
            p.add_argument('--description')
            p.add_argument('--title')
            p.add_argument('--status', choices=['active', 'archived'])
            p.add_argument('--link', action='append', metavar='CH-…',
                           help='link more chats (repeatable, comma-separated)')
            p.add_argument('--unlink', action='append', metavar='CH-…',
                           help='remove linked chats (repeatable, comma-separated)')
        if verb == 'history':
            p.add_argument('--limit', type=int, default=100)


def _split(values):
    return [v.strip() for v in ','.join(values or []).split(',') if v.strip()]


def _owner_value(value):
    """CLI owner spelling → owner object (``None`` clears). Chat handles are
    resolved and ownership-checked by ``tasks`` on write."""
    low = value.lower()
    if low in ('', 'none', 'clear'):
        return None
    if low == 'user':
        return {'kind': 'user', 'label': 'You'}
    if low.startswith('agent:'):
        agent = low.split(':', 1)[1].strip()
        if not agent:
            raise GizmoError('--owner agent:<id> needs an agent id')
        return {'kind': 'agent', 'agent': agent, 'label': agent}
    if tasks.parse_handle(value) is not None:
        return {'kind': 'chat', 'id': value}
    raise GizmoError('--owner must be CH-…, user, agent:<id> or none')


def run(args):
    uid = sid = None
    project = ''
    if args.session:
        uid, sid, project = tasks.session_context(args.session)
    actor = operation_actor(session_id=sid, agent_id=args.actor_agent, user_id=uid)
    cmd = args.task_cmd
    if cmd == 'list':
        if uid is None:
            raise GizmoError('list requires --session CH-…')
        rows = tasks.list_tasks(user_id=uid, session_id=sid, project_path=project, status=args.status)
        # Explicit agent inspection is audited; background UI polling is not.
        for row in rows:
            tasks.record_interaction(row['id'], 'list', user_id=uid, actor=actor)
        return {'success': True, 'gizmos': rows}
    if cmd == 'create':
        try:
            items = json.loads(args.items)
        except ValueError as exc:
            raise GizmoError('--items must be a JSON array') from exc
        row = tasks.create(gizmo_id=args.gizmo_id, title=args.title, items=items,
                           description=args.description, scope=args.scope, edit_mode=args.edit_mode,
                           session_id=sid, user_id=uid, actor=actor,
                           linked_sessions=_split(args.link) or None)
        return {'success': True, 'gizmo': row}
    if cmd == 'patch':
        try:
            ops = json.loads(args.ops) if args.ops else {}
        except ValueError as exc:
            raise GizmoError('--ops must be a JSON object') from exc
        if not isinstance(ops, dict):
            raise GizmoError('--ops must be a JSON object')
        for key in ('set_done', 'set_undone'):
            raw = ','.join(getattr(args, key) or [])
            if raw:
                ops[key] = list(ops.get(key) or []) + [i.strip() for i in raw.split(',') if i.strip()]
        for key in ('link', 'unlink'):
            values = _split(getattr(args, key))
            if values:
                ops[key] = list(ops.get(key) or []) + values
        owners = {}
        for spec in args.owner or []:
            item, sep, value = str(spec).partition('=')
            if not sep or not item.strip():
                raise GizmoError('--owner must be ITEM=CH-…, ITEM=user, ITEM=agent:<id> or ITEM=none')
            owners[item.strip()] = _owner_value(value.strip())
        if owners:
            ops['set_owner'] = {**(ops.get('set_owner') or {}), **owners}
        labels = {}
        for spec in args.label or []:
            item, sep, value = str(spec).partition('=')
            if not sep or not item.strip():
                raise GizmoError('--label must be ITEM=LABEL')
            labels[item.strip()] = value.strip()
        if labels:
            ops['set_label'] = {**(ops.get('set_label') or {}), **labels}
        working = _split(args.working)
        if working:
            ops['set_in_progress'] = list(ops.get('set_in_progress') or []) + working
        not_working = _split(args.not_working)
        if not_working:
            ops['clear_in_progress'] = list(ops.get('clear_in_progress') or []) + not_working
        hide = _split(args.hide)
        if hide:
            ops['set_hidden'] = list(ops.get('set_hidden') or []) + hide
        unhide = _split(args.unhide)
        if unhide:
            ops['clear_hidden'] = list(ops.get('clear_hidden') or []) + unhide
        for key in ('description', 'title', 'status'):
            value = getattr(args, key)
            if value is not None:
                ops[key] = value
        if not ops:
            raise GizmoError('pass --ops, --set-done, --owner, --working, --hide or another patch option')
        return {'success': True, 'gizmo': tasks.patch(args.gizmo_id, ops, user_id=uid,
                                                     session_id=sid, actor=actor)}
    row = tasks.get(args.gizmo_id, user_id=uid)
    tasks.record_interaction(args.gizmo_id, cmd, user_id=uid, actor=actor)
    if cmd == 'history':
        return {'success': True, 'events': tasks.history(args.gizmo_id, user_id=uid, limit=args.limit)}
    return {'success': True, 'gizmo': row}

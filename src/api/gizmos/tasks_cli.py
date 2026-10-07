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
        if verb == 'patch':
            p.add_argument('--ops', help='JSON patch object: add, remove, set_text, items, description, …')
            p.add_argument('--set-done')
            p.add_argument('--set-undone')
            p.add_argument('--description')
            p.add_argument('--title')
            p.add_argument('--status', choices=['active', 'archived'])
        if verb == 'history':
            p.add_argument('--limit', type=int, default=100)


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
                           session_id=sid, user_id=uid, actor=actor)
        return {'success': True, 'gizmo': row}
    if cmd == 'patch':
        try:
            ops = json.loads(args.ops) if args.ops else {}
        except ValueError as exc:
            raise GizmoError('--ops must be a JSON object') from exc
        if not isinstance(ops, dict):
            raise GizmoError('--ops must be a JSON object')
        for key in ('set_done', 'set_undone'):
            raw = getattr(args, key)
            if raw:
                ops[key] = list(ops.get(key) or []) + [i.strip() for i in raw.split(',') if i.strip()]
        for key in ('description', 'title', 'status'):
            value = getattr(args, key)
            if value is not None:
                ops[key] = value
        if not ops:
            raise GizmoError('pass --ops, --set-done or another patch option')
        return {'success': True, 'gizmo': tasks.patch(args.gizmo_id, ops, user_id=uid,
                                                     session_id=sid, actor=actor)}
    row = tasks.get(args.gizmo_id, user_id=uid)
    tasks.record_interaction(args.gizmo_id, cmd, user_id=uid, actor=actor)
    if cmd == 'history':
        return {'success': True, 'events': tasks.history(args.gizmo_id, user_id=uid, limit=args.limit)}
    return {'success': True, 'gizmo': row}

"""Scoped Tasks gizmos, using the existing auth-owned rows without copying state.

The legacy table name is a storage compatibility detail. This owner supplies
all task semantics to Gizmos transports; auth_db owns transactions and audit.
Tasks are established behavior and remain available without the usage flag.
"""
from __future__ import annotations
from api.gizmos import tasks_model as model
from api.gizmos.service import GizmoError
from core.agent_cli_env import operation_actor


def database(db=None):
    if db is None:
        from api.auth_db import get_auth_db
        db = get_auth_db()
    return db


def session_context(session_id, *, user_id=None, db=None):
    from api.cuttle_ui_capabilities import parse_chat_handle
    parsed = parse_chat_handle(str(session_id or ''))
    if not parsed:
        raise GizmoError('pass a chat handle with --session CH-…')
    sid = int(parsed['session_id'])
    row = database(db).get_chat_session_by_id(sid)
    if not row or (user_id is not None and int(row['user_id']) != int(user_id)):
        raise GizmoError('chat not found', 'not_found')
    return int(row['user_id']), sid, model._norm_project_path(row.get('project_path'))


def public(row):
    return {**row, 'placement': {'dock': 'composer'},
            'config': {k: row.get(k) for k in ('scope', 'session_id', 'project_path',
                                              'description', 'edit_mode', 'status')}
                      | {'items': (row.get('payload') or {}).get('items', [])}}


def list_tasks(*, user_id, session_id=None, project_path='', status='active', db=None):
    db = database(db)
    if session_id is not None:
        _, session_id, project_path = session_context(session_id, user_id=user_id, db=db)
    rows = db.list_chat_widgets(user_id=user_id, session_id=session_id,
                               project_path=project_path or None, status=status)
    return [public(r) for r in rows if r.get('type') == 'tasks']


def get(gizmo_id, *, user_id=None, db=None):
    row = database(db).get_chat_widget(gizmo_id, user_id=user_id)
    if not row or row.get('type') != 'tasks':
        raise GizmoError('Tasks gizmo not found', 'not_found')
    return public(row)


def create(*, gizmo_id=None, title='Tasks', items=None, description='', scope='session',
           edit_mode='agent', session_id=None, user_id=None, actor=None, db=None):
    db = database(db)
    uid, sid, project = session_context(session_id, user_id=user_id, db=db)
    gid = str(gizmo_id or model._new_id('tasks')).strip()
    if not gid or len(gid) > 128:
        raise GizmoError('Tasks id must contain 1-128 characters')
    if db.get_chat_widget(gid):
        raise GizmoError('Tasks id already exists; patch it instead', 'conflict')
    if scope not in ('session', 'project') or edit_mode not in ('agent', 'shared'):
        raise GizmoError('invalid scope or edit mode')
    if scope == 'project' and not project:
        raise GizmoError('project-scoped Tasks require a chat with a project')
    if not isinstance(items, list) or not items:
        raise GizmoError('create Tasks with a nonempty items array')
    payload = model.normalize_tasks_payload({'items': items})
    if not payload['items']:
        raise GizmoError('create Tasks with real items')
    _validate(payload)
    try:
        row = db.upsert_chat_widget(widget_id=gid, user_id=uid, wtype='tasks',
            title=str(title or 'Tasks')[:200], scope=scope, session_id=sid,
            project_path=project, payload=payload,
            status=model.resolve_tasks_widget_status(payload), edit_mode=edit_mode,
            description=model._norm_description(description), expected_revision=0,
            actor=actor or operation_actor(session_id=sid, user_id=uid), operation='create')
    except ValueError as exc:
        raise GizmoError(str(exc), 'conflict') from exc
    return public(row)


def _validate(payload):
    seen = set()
    def walk(nodes, depth=0):
        if depth > 16:
            raise GizmoError('Tasks nesting exceeds 16 levels')
        for node in nodes:
            iid = node['id']
            if not iid or iid in seen or len(iid) > 128:
                raise GizmoError('task item ids must be unique, nonempty and at most 128 characters')
            seen.add(iid)
            if len(seen) > 1000 or len(node['text']) > 4000:
                raise GizmoError('Tasks size limit exceeded')
            walk(node.get('children', []), depth + 1)
    walk(payload['items'])


def patch(gizmo_id, ops, *, user_id=None, session_id=None, actor=None, db=None, item_edit=False):
    if not isinstance(ops, dict):
        raise GizmoError('patch must be an object')
    db = database(db)
    for key in ('set_done', 'set_undone', 'remove', 'add', 'items'):
        if key in ops and not isinstance(ops[key], list):
            raise GizmoError(f'{key} must be an array')
    if 'set_text' in ops and not isinstance(ops['set_text'], dict):
        raise GizmoError('set_text must be an object')
    if 'status' in ops and ops['status'] not in ('active', 'archived'):
        raise GizmoError('status must be active or archived')
    if session_id is not None:
        uid, sid, project = session_context(session_id, user_id=user_id, db=db)
        user_id = uid
    else:
        sid = None
        project = ''
    for attempt in range(5):
        row = get(gizmo_id, user_id=user_id, db=db)
        uid = int(row['user_id'])
        if item_edit and row.get('edit_mode') != 'shared':
            raise GizmoError('This Tasks list is agent-managed; switch to Shared to edit.', 'forbidden')
        scope = ops.get('scope', row['scope'])
        edit = ops.get('edit_mode', row.get('edit_mode', 'agent'))
        if scope not in ('session', 'project') or edit not in ('agent', 'shared'):
            raise GizmoError('invalid scope or edit mode')
        if scope != row['scope'] and sid is None:
            raise GizmoError('scope changes require the current chat')
        target_project = project if scope != row['scope'] else row.get('project_path', '')
        if scope == 'project' and not target_project:
            raise GizmoError('project-scoped Tasks require a project')
        payload = model.apply_tasks_patch(row['payload'], ops)
        _validate(payload)
        requested = ops.get('status')
        item_ops = any(key in ops for key in ('items', 'add', 'remove', 'set_done', 'set_undone'))
        if requested is None and not item_ops:
            requested = row['status']
        try:
            updated = db.upsert_chat_widget(widget_id=gizmo_id, user_id=uid, wtype='tasks',
                title=str(ops.get('title', row['title']) or 'Tasks')[:200], scope=scope,
                session_id=(sid if scope != row['scope'] else row.get('session_id')),
                project_path=target_project if scope == 'project' else '', payload=payload,
                status=model.resolve_tasks_widget_status(payload, requested=requested),
                edit_mode=edit, description=model._description_from_sources(ops, existing=row),
                expected_revision=row['revision'], operation='patch',
                actor=actor or operation_actor(session_id=sid, user_id=uid))
            return public(updated)
        except ValueError as exc:
            if 'revision conflict' not in str(exc) or attempt == 4:
                raise GizmoError(str(exc), 'conflict') from exc
    raise GizmoError('Tasks revision conflict', 'conflict')


def history(gizmo_id, *, user_id=None, limit=100, db=None):
    db = database(db)
    row = get(gizmo_id, user_id=user_id, db=db)
    return db.task_gizmo_history(gizmo_id, user_id=row['user_id'], limit=limit)


def record_interaction(gizmo_id, operation, *, user_id=None, actor=None, db=None):
    db = database(db)
    row = get(gizmo_id, user_id=user_id, db=db)
    db.record_task_gizmo_interaction(gizmo_id, user_id=row['user_id'], operation=operation,
                                    actor=actor or operation_actor(user_id=row['user_id']))

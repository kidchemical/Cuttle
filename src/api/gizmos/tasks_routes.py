"""Authenticated chat-scoped Tasks transport; usage meters remain owner-only."""
from flask import Blueprint, jsonify, request
from api.gizmos import tasks
from api.gizmos.service import GizmoError
from api.http_authz import authenticated_required, current_user
from api.gizmos.attribution import request_actor as _actor

tasks_bp = Blueprint('task_gizmos', __name__, url_prefix='/tasks')


def _body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise GizmoError('body must be an object')
    return data


@tasks_bp.errorhandler(GizmoError)
def _error(exc):
    return jsonify(success=False, error=str(exc), code=exc.code), {
        'not_found': 404, 'conflict': 409, 'forbidden': 403}.get(exc.code, 400)


@tasks_bp.after_request
def _no_store(response):
    response.headers['Cache-Control'] = 'no-store'
    return response


@tasks_bp.route('', methods=['GET'])
@authenticated_required
def list_tasks():
    uid = current_user()['id']
    sid = request.args.get('session_id')
    if not sid:
        raise GizmoError('session_id is required')
    rows = tasks.list_tasks(user_id=uid, session_id=sid)
    return jsonify(success=True, gizmos=rows, revision=tasks.database().widgets_revision_for(user_id=uid))


@tasks_bp.route('', methods=['POST'])
@authenticated_required
def create_tasks():
    data = _body()
    row = tasks.create(gizmo_id=data.get('id'), title=data.get('title'), items=data.get('items'),
                       description=data.get('description', ''), scope=data.get('scope', 'session'),
                       edit_mode=data.get('edit_mode', 'agent'), session_id=data.get('session_id'),
                       user_id=current_user()['id'], actor=_actor(data))
    return jsonify(success=True, gizmo=row), 201


@tasks_bp.route('/<gizmo_id>', methods=['GET', 'PATCH'])
@authenticated_required
def task_gizmo(gizmo_id):
    uid = current_user()['id']
    if request.method == 'GET':
        row = tasks.get(gizmo_id, user_id=uid)
        tasks.record_interaction(gizmo_id, 'get', user_id=uid,
                                 actor=_actor({'session_id': request.args.get('session_id', '')}))
    else:
        data = _body()
        ops = {**data, **(data.get('patch') if isinstance(data.get('patch'), dict) else {})}
        actor = _actor(data)
        item_ops = any(key in ops for key in ('set_done', 'set_undone', 'set_text', 'items', 'add', 'remove'))
        row = tasks.patch(gizmo_id, ops, user_id=uid, session_id=data.get('session_id'), actor=actor,
                          item_edit=item_ops and actor['source'] == 'ui')
    return jsonify(success=True, gizmo=row)


@tasks_bp.route('/<gizmo_id>/items/<item_id>', methods=['PATCH'])
@authenticated_required
def task_item(gizmo_id, item_id):
    data = _body()
    if not isinstance(data.get('done'), bool):
        raise GizmoError('done must be a boolean')
    row = tasks.patch(gizmo_id, {'set_done' if data['done'] else 'set_undone': [item_id]},
                      user_id=current_user()['id'], session_id=data.get('session_id'),
                      actor=_actor(data), item_edit=True)
    return jsonify(success=True, gizmo=row)


@tasks_bp.route('/<gizmo_id>/history', methods=['GET'])
@authenticated_required
def task_history(gizmo_id):
    try:
        limit = int(request.args.get('limit', 100))
    except ValueError:
        raise GizmoError('limit must be an integer')
    uid = current_user()['id']
    actor = _actor()
    tasks.record_interaction(gizmo_id, 'history', user_id=uid, actor=actor)
    return jsonify(success=True, events=tasks.history(gizmo_id, user_id=uid, limit=limit))

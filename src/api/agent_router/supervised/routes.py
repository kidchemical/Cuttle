"""HTTP transport for supervised task controls and artifacts."""

import uuid

from flask import Blueprint, Response, jsonify, request

from api.auth_db import get_auth_db
from api.auth_session import get_request_session_token
from api.agent_router.supervised.control import session_owns_task
from api.agent_router.supervised.delivery import public_indicator
from api.agent_router.supervised.orchestrator import cancel_task, user_followup
from api.agent_router.supervised.store import load_task, store_dir


supervised_bp = Blueprint("supervised", __name__)


@supervised_bp.post("/api/supervised/tasks/<task_id>/control")
def supervised_task_control(task_id):
    """Typed, model-free controls for the Activity disclosure."""
    try:
        data = request.get_json(silent=True) or {}
        action = str(data.get('action') or '').strip().lower()
        task = load_task(task_id)
        if not task:
            return jsonify({'success': False, 'error': 'Task not found.'}), 404
        token = get_request_session_token()
        db = get_auth_db()
        user = db.verify_auth_session(token) if token else None
        if not user:
            return jsonify({'success': False, 'error': 'Unauthorized.'}), 401
        parent = str(task.parent_session_id or '')
        bare = parent[len('db_session_'):] if parent.startswith('db_session_') else parent
        try:
            session_id = int(bare)
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'Task has no authenticated session.'}), 403
        if not db.get_chat_session(session_id, user['id']):
            return jsonify({'success': False, 'error': 'Task belongs to another session.'}), 403
        if not session_owns_task(session_id, task):
            return jsonify({'success': False, 'error': 'Task belongs to another session.'}), 403
        control_id = str(data.get('control_request_id') or f"ui_{task_id}_{action}_{uuid.uuid4().hex[:10]}")
        if action == 'status':
            result = {'success': True, 'acknowledgement': 'Activity refreshed.'}
        elif action == 'followup':
            instruction = str(data.get('instruction') or '').strip()
            if not instruction:
                return jsonify({'success': False, 'error': 'Instruction is required.'}), 400
            result = user_followup(session_id, instruction, control_request_id=control_id)
            result['acknowledgement'] = 'Instruction added.'
        elif action == 'cancel':
            result = cancel_task(session_id, control_request_id=control_id)
            result['acknowledgement'] = 'Task cancelled.'
        else:
            return jsonify({'success': False, 'error': 'Unknown control action.'}), 400
        latest = load_task(task_id) or task
        result['supervised_task'] = public_indicator(latest)
        result['control_request_id'] = control_id
        return jsonify(result)
    except Exception as e:
        print(f'[CHAT] supervised typed control failed: {e}', flush=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@supervised_bp.get("/api/supervised/tasks/<task_id>/runs/<run_id>/raw")
def supervised_task_raw_artifact(task_id, run_id):
    """Session/task-scoped read-only viewer for durable worker raw output.

    Validates ownership and confines reads to the supervised task runs directory.
    """
    tid = str(task_id or '').strip()
    rid = str(run_id or '').strip()
    if not tid or not rid:
        return jsonify({'success': False, 'error': 'task_id and run_id required'}), 400
    if '/' in tid or '\\' in tid or '..' in tid or '/' in rid or '\\' in rid or '..' in rid:
        return jsonify({'success': False, 'error': 'invalid id'}), 400

    session_token = get_request_session_token()
    if not session_token:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    try:
        db = get_auth_db()
        user = db.verify_auth_session(session_token)
        if not user:
            return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    except Exception as e:
        return jsonify({'success': False, 'error': f'Unauthorized: {e}'}), 401

    try:
        task = load_task(tid)
        if not task:
            return jsonify({'success': False, 'error': 'Task not found'}), 404
        parent = str(task.parent_session_id or '')
        bare = parent[len('db_session_'):] if parent.startswith('db_session_') else parent
        try:
            db_sid = int(bare)
        except (TypeError, ValueError):
            db_sid = None
        if db_sid is None or not db.get_chat_session(db_sid, user['id']):
            return jsonify({'success': False, 'error': 'Forbidden'}), 403
        if not session_owns_task(db_sid, task) and not session_owns_task(parent, task):
            return jsonify({'success': False, 'error': 'Forbidden'}), 403

        root = (store_dir() / tid / 'runs').resolve()
        path = (root / f'{rid}.raw.txt').resolve()
        try:
            path.relative_to(root)
        except ValueError:
            return jsonify({'success': False, 'error': 'path rejected'}), 400
        if not path.is_file():
            return jsonify({'success': False, 'error': 'artifact not found'}), 404
        size = path.stat().st_size
        max_bytes = 2_000_000
        data = path.read_bytes()[:max_bytes]
        text = data.decode('utf-8', errors='replace')
        as_download = (request.args.get('download') or '').strip() in ('1', 'true', 'yes')
        if as_download or request.args.get('format') == 'text':
            return Response(
                text,
                mimetype='text/plain; charset=utf-8',
                headers={
                    'Content-Disposition': (
                        f'attachment; filename="{tid}_{rid}.raw.txt"'
                        if as_download
                        else 'inline'
                    ),
                    'X-Content-Type-Options': 'nosniff',
                },
            )
        return jsonify({
            'success': True,
            'task_id': tid,
            'run_id': rid,
            'bytes': size,
            'truncated': size > max_bytes,
            'content': text,
            'content_type': 'text/plain',
        })
    except Exception as e:
        print(f'[CHAT] supervised raw artifact failed: {e}', flush=True)
        return jsonify({'success': False, 'error': 'failed to read artifact'}), 500

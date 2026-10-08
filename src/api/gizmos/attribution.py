"""HTTP provenance: authenticated user/chat, separately declared agent metadata."""
from flask import request
from api.http_authz import current_user
from core.agent_cli_env import operation_actor


def request_actor(data=None):
    from api.gizmos.tasks import session_context
    data = data or {}
    uid = current_user()['id']
    sid = data.get('session_id', request.args.get('session_id', ''))
    project = ''
    if sid:
        _, sid, project = session_context(sid, user_id=uid)
    actor = operation_actor(source='ui', user_id=uid, session_id=sid, agent_id='')
    actor['project_path'] = project
    declared = data.get('actor')
    if not isinstance(declared, dict):
        declared = {'agent_id': request.headers.get('X-Cuttle-Agent', ''),
                    'model': request.headers.get('X-Cuttle-Model', ''),
                    'run_id': request.headers.get('X-Cuttle-Run', '')}
    # Reported provenance is not authentication. The user and chat are always
    # resolved on the server and cannot be overridden by declared metadata.
    if declared.get('agent_id'):
        actor['source'] = 'api_agent'
        for key in ('agent_id', 'model', 'run_id'):
            actor[key] = str(declared.get(key) or '')[:160]
    return actor

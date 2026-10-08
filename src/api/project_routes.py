"""Projects HTTP surface — the single owner for project concerns.

Ownership contract (do not split across the monolith again):
- Transport only: request parsing, auth gating, response shapes. Business
  logic lives in ``managers.project_manager``. Never add project rules here.
- Persistence: ``managers.project_manager``. No other stores.
- Authorization: reads are ``authenticated_required``; every write is
  ``owner_required``, enforced SOLELY by the route decorator. The
  ``requires_project_manager`` guard answers 503 when the backend failed
  to import — never inline another availability check in a handler.
- This module must never import ``api.web_chat_api`` (Phase 2 rule:
  new subsystems do not reach back into the monolith for globals).

Route contract is frozen: same paths, methods, status codes and payload
shapes as when these handlers lived on the monolith app object.
"""

from __future__ import annotations

from functools import wraps

from flask import Blueprint, jsonify, request, send_from_directory
from pathlib import Path
from api.http_authz import current_user, is_owner_user

from api.http_authz import authenticated_required, owner_required

projects_bp = Blueprint("projects", __name__, url_prefix="/api")

# Mirror the monolith: routes answer 503 when the backend is missing.
try:
    from managers.project_manager import project_manager
except ImportError as e:
    print(f"Warning: Project manager not available: {e}")
    project_manager = None


def requires_project_manager(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if project_manager is None:
            print("[API] /api/projects*: project_manager is None, returning 503")
            return jsonify({
                'success': False,
                'error': 'Project manager not available'
            }), 503
        return f(*args, **kwargs)
    return decorated_function


@projects_bp.route('/projects', methods=['GET'])
@requires_project_manager
@authenticated_required
def get_projects():
    """Get all projects"""
    try:
        projects = (project_manager.get_projects(include_archived=True)
                    if request.args.get('include_archived') == '1' else project_manager.get_projects())
        print(f"[API] GET /api/projects: success, count={len(projects)}")
        return jsonify({
            'success': True,
            'data': projects
        })
    except Exception as e:
        print(f"[API] GET /api/projects: error: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@projects_bp.route('/projects/<int:project_id>', methods=['GET'])
@requires_project_manager
@authenticated_required
def get_project(project_id):
    """Get a specific project"""
    try:
        project = project_manager.get_project(project_id)
        if project:
            return jsonify({
                'success': True,
                'data': project
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Project not found'
            }), 404
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@projects_bp.route('/projects/<int:project_id>/branch', methods=['POST'])
@requires_project_manager
@owner_required
def use_project_branch(project_id):
    from api.git_service import GitError, use_working_branch
    from managers.project_locations import require_project_path
    project = project_manager.get_project(project_id)
    if project is None:
        return jsonify(success=False, error='Project not found.'), 404
    try:
        result = use_working_branch(require_project_path(project), project['default_branch'])
        return jsonify(success=True, data=result)
    except ValueError as exc:
        return jsonify(success=False, error=str(exc)), 400
    except GitError as exc:
        return jsonify(success=False, error=str(exc)), 409


@projects_bp.route('/projects/current', methods=['GET'])
@requires_project_manager
@authenticated_required
def get_current_project():
    """Get the currently active project"""
    try:
        project = project_manager.get_current_project()
        print(f"[API] GET /api/projects/current: success, project={project.get('name') if project else None}")
        return jsonify({
            'success': True,
            'data': project
        })
    except Exception as e:
        print(f"[API] GET /api/projects/current: error: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@projects_bp.route('/projects', methods=['POST'])
@requires_project_manager
@owner_required
def create_project():
    """Create a new project"""
    try:
        data = request.get_json()
        if not data or 'name' not in data or 'type' not in data:
            return jsonify({
                'success': False,
                'error': 'Name and type are required'
            }), 400

        project_type = data['type']
        name = data['name']
        description = data.get('description', '')
        tags = data.get('tags', [])

        success = False

        if project_type == 'local':
            path = data.get('path')
            if not path:
                return jsonify({
                    'success': False,
                    'error': 'Path is required for local projects'
                }), 400

            # Check if path exists before trying to add
            import os
            if not os.path.exists(path):
                return jsonify({
                    'success': False,
                    'error': f'Path does not exist: {path}'
                }), 400

            success = project_manager.add_local_project(name, path, description, tags)

        elif project_type == 'github':
            repo_url = data.get('repo_url')
            if not repo_url:
                return jsonify({
                    'success': False,
                    'error': 'Repository URL is required for GitHub projects'
                }), 400
            local_path = data.get('local_path')
            success = project_manager.add_github_project(name, repo_url, local_path, description, tags)

        elif project_type == 'gitlab':
            repo_url = data.get('repo_url')
            if not repo_url:
                return jsonify({
                    'success': False,
                    'error': 'Repository URL is required for GitLab projects'
                }), 400
            local_path = data.get('local_path')
            success = project_manager.add_gitlab_project(name, repo_url, local_path, description, tags)

        else:
            return jsonify({
                'success': False,
                'error': f'Invalid project type: {project_type}'
            }), 400

        if success:
            return jsonify({
                'success': True,
                'message': f'Project "{name}" created successfully'
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Failed to create project'
            }), 500

    except Exception as e:
        print(f"Error creating project: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@projects_bp.route('/projects/<int:project_id>', methods=['PUT'])
@requires_project_manager
@owner_required
def update_project(project_id):
    """Update a project"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({
                'success': False,
                'error': 'No data provided'
            }), 400

        success = project_manager.update_project(project_id, **data)

        if success:
            return jsonify({
                'success': True,
                'message': 'Project updated successfully'
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Failed to update project'
            }), 500

    except (ValueError, TypeError) as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@projects_bp.route('/projects/<int:project_id>', methods=['DELETE'])
@requires_project_manager
@owner_required
def delete_project(project_id):
    """Delete a project"""
    try:
        success = project_manager.delete_project(project_id)

        if success:
            return jsonify({
                'success': True,
                'message': 'Project deleted successfully'
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Failed to delete project'
            }), 500

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@projects_bp.route('/projects/<int:project_id>/switch', methods=['POST'])
@requires_project_manager
@owner_required
def switch_project(project_id):
    """Switch to a specific project"""
    try:
        success = project_manager.switch_to_project(project_id)

        if success:
            project = project_manager.get_current_project()
            return jsonify({
                'success': True,
                'message': f'Switched to project: {project["name"]}',
                'data': project
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Failed to switch project'
            }), 500

    except Exception as e:
        print(f"Error switching project: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@projects_bp.route('/projects/<int:project_id>/sync', methods=['POST'])
@requires_project_manager
@owner_required
def sync_project(project_id):
    """Sync a remote project"""
    try:
        success = project_manager.sync_remote_project(project_id)

        if success:
            return jsonify({
                'success': True,
                'message': 'Project synced successfully'
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Failed to sync project'
            }), 500

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@projects_bp.route('/projects/history', methods=['GET'])
@requires_project_manager
@authenticated_required
def get_project_history():
    """Get project history"""
    try:
        project_id = request.args.get('project_id', type=int)
        limit = request.args.get('limit', 50, type=int)

        history = project_manager.get_project_history(project_id, limit)

        return jsonify({
            'success': True,
            'data': history
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@projects_bp.route('/projects/stats', methods=['GET'])
@requires_project_manager
@authenticated_required
def get_project_stats():
    """Get project statistics"""
    try:
        stats = project_manager.get_project_stats()
        return jsonify({
            'success': True,
            'data': stats
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@projects_bp.route('/projects/app/access')
@authenticated_required
def projects_app_access():
    return jsonify({'success': True, 'can_edit': is_owner_user(current_user())})


@projects_bp.route('/projects/paths/check', methods=['POST'])
@owner_required
def check_project_paths():
    from managers.project_locations import check_paths, validate_paths
    try:
        body = request.get_json(silent=True) or {}
        return jsonify({'success': True, 'data': check_paths(validate_paths(body.get('paths')))})
    except (ValueError, TypeError, AttributeError) as exc:
        return jsonify({'success': False, 'error': str(exc)}), 400


@projects_bp.route('/projects/<int:project_id>/overview')
@requires_project_manager
@authenticated_required
def project_overview(project_id):
    from managers.project_details import config_inventory, repository_summary
    project = project_manager.get_project(project_id)
    if not project:
        return jsonify({'success': False, 'error': 'Project not found'}), 404
    return jsonify({'success': True, 'data': {
        'project': project, 'repository': repository_summary(project),
        'configuration': config_inventory(project),
        'changes': project_manager.get_project_history(project_id, 15),
    }})


@projects_bp.route('/projects/<int:project_id>/activity')
@requires_project_manager
@authenticated_required
def project_activity(project_id):
    from api.auth_db import get_auth_db
    project = project_manager.get_project(project_id)
    if not project:
        return jsonify({'success': False, 'error': 'Project not found'}), 404
    try:
        before = request.args.get('before_id')
        activity = get_auth_db().get_project_activity(
            current_user()['id'], project_id, project['name'], project.get('paths', [project['path']]),
            before_id=int(before) if before else None)
    except ValueError:
        return jsonify({'success': False, 'error': 'Invalid feed cursor'}), 400
    return jsonify({'success': True, 'data': activity})


@projects_bp.route('/projects/<int:project_id>/chat', methods=['POST'])
@requires_project_manager
@authenticated_required
def new_project_chat(project_id):
    from api.auth_db import get_auth_db
    from managers.project_locations import require_project_path, ProjectUnavailable
    project = project_manager.get_project(project_id)
    if not project:
        return jsonify({'success': False, 'error': 'Project not found'}), 404
    try:
        path = require_project_path(project)
    except ProjectUnavailable as exc:
        return jsonify({'success': False, 'error': str(exc)}), 409
    db = get_auth_db()
    user = current_user()
    sid = db.create_chat_session(user['id'])
    db.set_session_project(sid, user['id'], project_id=project_id, project_name=project['name'], project_path=path)
    return jsonify({'success': True, 'session_id': sid})


@projects_bp.route('/projects/<int:project_id>/remove', methods=['POST'])
@requires_project_manager
@owner_required
def remove_project_confirmed(project_id):
    project = project_manager.get_project(project_id)
    if not project:
        return jsonify({'success': False, 'error': 'Project not found'}), 404
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict) or body.get('confirm_name') != project['name']:
        return jsonify({'success': False, 'error': 'Type the exact project name to remove it.'}), 400
    success = project_manager.delete_project(project_id)
    return jsonify({'success': success})


projects_pages_bp = Blueprint('projects_pages', __name__)


@projects_pages_bp.route('/projects_page.html')
@authenticated_required
def projects_app_page():
    return send_from_directory(Path(__file__).resolve().parents[1] / 'web', 'projects_page.html')


@projects_bp.route('/projects/register', methods=['POST'])
@requires_project_manager
@owner_required
def register_existing_project():
    try:
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            raise ValueError('Expected a project object.')
        project_id = project_manager.register_project(
            body.get('name'), body.get('path'), body.get('description', ''),
            body.get('tags', []), body.get('repo_url', ''))
        return jsonify({'success': True, 'project_id': project_id})
    except (ValueError, TypeError) as exc:
        return jsonify({'success': False, 'error': str(exc)}), 400
    except Exception:
        return jsonify({'success': False, 'error': 'Could not register project. Check folder permissions and duplicate names.'}), 409

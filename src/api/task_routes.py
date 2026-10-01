"""Tasks HTTP surface — the single owner for task transport.

Ownership contract (do not split across the monolith again):
- Transport only: request parsing/validation, auth gating, response
  shaping, status codes. Task behavior lives in
  ``managers.task_manager`` (SQLite persistence); the one composite
  workflow (close-via-commit) delegates its git step to
  ``api.git_service.stage_and_commit`` with frozen legacy semantics.
  Never add subprocess calls or domain rules here.
- Persistence: ``managers.task_manager``. No other stores.
- Authorization: every route except ``GET /api/tasks/<id>`` is
  ``owner_required`` (the public single-task GET is pinned as-is, not
  fixed here), enforced SOLELY by the route decorator.
- Imports from ``managers.task_manager`` stay function-lazy (as in the
  monolith) so tests patching that module keep working.
- This module must never import ``api.web_chat_api`` (Phase 2 rule:
  new subsystems do not reach back into the monolith for globals).

Route contract is frozen: same paths, methods, status codes and payload
shapes as when these handlers lived on the monolith app object.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from api.http_authz import owner_required

tasks_bp = Blueprint("tasks", __name__, url_prefix="/api")


@tasks_bp.route('/tasks', methods=['GET'])
@owner_required
def get_tasks():
    """Get all tasks"""
    try:
        from managers.task_manager import task_manager
        tasks = task_manager.get_all_tasks()
        return jsonify(tasks)
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@tasks_bp.route('/tasks', methods=['POST'])
@owner_required
def create_task():
    """Create a new task"""
    try:
        from managers.task_manager import task_manager
        data = request.get_json()
        
        # Validate required fields
        if not data.get('title'):
            return jsonify({
                'success': False,
                'error': 'Title is required'
            }), 400
        
        task = task_manager.create_task(
            title=data['title'],
            description=data.get('description', ''),
            owner=data.get('owner', ''),
            status=data.get('status', 'None'),
            priority=data.get('priority', 'Medium')
        )
        
        return jsonify(task), 201
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@tasks_bp.route('/tasks/<int:task_id>', methods=['GET'])
def get_task(task_id):
    """Get a specific task"""
    try:
        from managers.task_manager import task_manager
        task = task_manager.get_task(task_id)
        
        if not task:
            return jsonify({
                'success': False,
                'error': 'Task not found'
            }), 404
        
        return jsonify(task)
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@tasks_bp.route('/tasks/<int:task_id>', methods=['PUT'])
@owner_required
def update_task(task_id):
    """Update a task"""
    try:
        from managers.task_manager import task_manager
        data = request.get_json()
        
        # Validate required fields
        if not data.get('title'):
            return jsonify({
                'success': False,
                'error': 'Title is required'
            }), 400
        
        task = task_manager.update_task(
            task_id=task_id,
            title=data['title'],
            description=data.get('description', ''),
            owner=data.get('owner', ''),
            status=data.get('status', 'None'),
            priority=data.get('priority', 'Medium')
        )
        
        if not task:
            return jsonify({
                'success': False,
                'error': 'Task not found'
            }), 404
        
        return jsonify(task)
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@tasks_bp.route('/tasks/<int:task_id>', methods=['DELETE'])
@owner_required
def delete_task(task_id):
    """Delete a task"""
    try:
        from managers.task_manager import task_manager
        success = task_manager.delete_task(task_id)
        
        if not success:
            return jsonify({
                'success': False,
                'error': 'Task not found'
            }), 404
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@tasks_bp.route('/tasks/<int:task_id>/comments', methods=['POST'])
@owner_required
def add_task_comment(task_id):
    """Add a comment to a task"""
    try:
        from managers.task_manager import task_manager
        data = request.get_json()
        
        if not data.get('content'):
            return jsonify({
                'success': False,
                'error': 'Comment content is required'
            }), 400
        
        comment = task_manager.add_comment(
            task_id=task_id,
            content=data['content'],
            author=data.get('author', 'Anonymous')
        )
        
        if not comment:
            return jsonify({
                'success': False,
                'error': 'Task not found'
            }), 404
        
        return jsonify(comment), 201
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@tasks_bp.route('/tasks/<int:task_id>/comments/<int:comment_id>', methods=['DELETE'])
@owner_required
def delete_task_comment(task_id, comment_id):
    """Delete a comment from a task"""
    try:
        from managers.task_manager import task_manager
        
        success = task_manager.delete_comment(
            task_id=task_id,
            comment_id=comment_id
        )
        
        if not success:
            return jsonify({
                'success': False,
                'error': 'Comment or task not found'
            }), 404
        
        return jsonify({
            'success': True,
            'message': 'Comment deleted successfully'
        }), 200
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@tasks_bp.route('/tasks/<int:task_id>/close-via-commit', methods=['POST'])
@owner_required
def close_task_via_commit(task_id):
    """Close a task via commit"""
    try:
        import os

        from api.git_service import GitError, stage_and_commit
        from managers.task_manager import task_manager

        data = request.get_json()
        commit_message = data.get('message', '')
        files = data.get('files', '.')

        if not commit_message:
            return jsonify({
                'success': False,
                'error': 'Commit message is required'
            }), 400

        # Get the task to include in commit message
        task = task_manager.get_task(task_id)
        if not task:
            return jsonify({
                'success': False,
                'error': 'Task not found'
            }), 404

        # Add task reference to commit message
        full_commit_message = f"{commit_message}\n\nCloses {task['taskId']}"

        try:
            # Git step owned by git_service (frozen legacy semantics:
            # process cwd, bare environment/identity).
            stage_and_commit(os.getcwd(), full_commit_message, files)

            # Update task status to closed
            task_manager.update_task(
                task_id=task_id,
                title=task['title'],
                description=task['description'],
                owner=task['owner'],
                status='Closed',
                priority=task['priority']
            )

            # Add a comment about the commit
            task_manager.add_comment(
                task_id=task_id,
                content=f"Task closed via commit: {commit_message}",
                author="System"
            )

            return jsonify({
                'success': True,
                'message': 'Task closed successfully via commit'
            })

        except GitError as e:
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# =================================================================================

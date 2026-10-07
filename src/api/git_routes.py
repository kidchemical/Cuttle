"""Git HTTP surface — the single owner for Git transport.

Ownership contract (do not split across the monolith again):
- Transport only: request parsing/validation, auth gating, project and
  repository allowlist resolution, response shaping, status codes. Git
  behavior lives in ``api.git_service`` and
  ``scripts.utilities.git_pending_changes`` / ``git_graph`` (+
  ``commit_message_suggester``, ``fs_reveal`` for their lanes). Never add
  subprocess calls or output parsing here.
- Persistence: none in this layer (stateless subprocess calls).
- Authorization: reads are ``authenticated_required``; every write is
  ``owner_required``, enforced SOLELY by the route decorator.
- This module must never import ``api.web_chat_api`` (Phase 2 rule:
  new subsystems do not reach back into the monolith for globals).

Route contract is frozen: same paths, methods, status codes and payload
shapes as when these handlers lived on the monolith app object.
"""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, request

from api.http_authz import authenticated_required, owner_required

git_bp = Blueprint("git", __name__, url_prefix="/api")

# Mirror the monolith: handlers tolerate a missing backend.
try:
    from managers.project_manager import project_manager
except ImportError as e:
    print(f"Warning: Project manager not available: {e}")
    project_manager = None


@git_bp.route('/git/status', methods=['GET'])
@authenticated_required
def git_status():
    """Get Git repository status"""
    try:
        from api.git_service import (
            NotARepositoryError,
            GitError,
            repo_status,
            resolve_repo_cwd,
        )

        current_project = project_manager.get_current_project()
        cwd = resolve_repo_cwd(current_project)

        try:
            data = repo_status(cwd)
        except NotARepositoryError:
            return jsonify({
                'success': False,
                'error': 'Not in a Git repository'
            }), 400

        return jsonify({
            'success': True,
            'data': data
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


@git_bp.route('/git/repos', methods=['GET'])
@authenticated_required
def git_repos_list():
    """List git work trees under a registered Cuttle project path.

    Query: path=… and/or project_id=…
    """
    try:
        from scripts.utilities.git_pending_changes import (
            discover_git_workdirs,
            git_push_target,
            git_run,
            repo_label,
            resolve_allowed_project_cwd,
        )
    except ImportError:
        try:
            from utilities.git_pending_changes import (  # type: ignore
                discover_git_workdirs,
                git_push_target,
                git_run,
                repo_label,
                resolve_allowed_project_cwd,
            )
        except ImportError as e:
            return jsonify({'success': False, 'error': f'git helper unavailable: {e}'}), 500

    path = (request.args.get('path') or '').strip() or None
    pid_raw = request.args.get('project_id')
    project_id = None
    if pid_raw not in (None, ''):
        try:
            project_id = int(pid_raw)
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'Invalid project_id'}), 400

    try:
        projects = project_manager.get_projects() if project_manager else []
        current = project_manager.get_current_project() if project_manager else None
    except Exception as e:
        return jsonify({'success': False, 'error': f'Project lookup failed: {e}'}), 500

    cwd, proj, err = resolve_allowed_project_cwd(path, project_id, projects, current)
    if err or not cwd:
        return jsonify({'success': False, 'error': err or 'No project'}), 400

    roots = discover_git_workdirs(cwd)
    repos = []
    for root in roots:
        br = git_run(['branch', '--show-current'], root, timeout=8.0)
        branch = (br.stdout or '').strip() if br.returncode == 0 else ''
        target = git_push_target(root)
        repos.append({
            'repo_root': root,
            'label': repo_label(root, cwd),
            'branch': target.get('branch') or branch or None,
            'remote': target.get('remote') or 'origin',
            'repo': target.get('repo'),
            'url': target.get('url'),
        })
    return jsonify({
        'success': True,
        'project': {
            'id': proj.get('id') if proj else None,
            'name': proj.get('name') if proj else None,
            'path': cwd,
        },
        'repos': repos,
    })


@git_bp.route('/git/pending-changes', methods=['GET'])
@authenticated_required
def git_pending_changes():
    """Uncommitted changes for a chat project (files + line add/remove counts).

    Query:
      path=C:\\Projects\\DemoGame   (must match a registered project)
      project_id=3                         (optional alternative)
      repo_root=…                          (optional; filter to one work tree)

    Response always includes ``repos`` (one entry per discovered work tree).
    When exactly one repo is returned, its fields are also flattened at the top
    level for older clients.
    """
    try:
        from scripts.utilities.git_pending_changes import (
            collect_project_pending_changes,
            resolve_allowed_project_cwd,
            resolve_allowed_repo_root,
        )
    except ImportError:
        try:
            from utilities.git_pending_changes import (  # type: ignore
                collect_project_pending_changes,
                resolve_allowed_project_cwd,
                resolve_allowed_repo_root,
            )
        except ImportError as e:
            return jsonify({'success': False, 'error': f'git helper unavailable: {e}'}), 500

    path = (request.args.get('path') or '').strip() or None
    repo_root_q = (request.args.get('repo_root') or '').strip() or None
    pid_raw = request.args.get('project_id')
    project_id = None
    if pid_raw not in (None, ''):
        try:
            project_id = int(pid_raw)
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'Invalid project_id'}), 400

    try:
        projects = project_manager.get_projects() if project_manager else []
        current = project_manager.get_current_project() if project_manager else None
    except Exception as e:
        return jsonify({'success': False, 'error': f'Project lookup failed: {e}'}), 500

    cwd, proj, err = resolve_allowed_project_cwd(path, project_id, projects, current)
    if err or not cwd:
        return jsonify({'success': False, 'error': err or 'No project'}), 400

    try:
        payload = collect_project_pending_changes(cwd)
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e), 'clean': True, 'repos': []}), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

    repos = list(payload.get('repos') or [])
    if repo_root_q:
        try:
            want = resolve_allowed_repo_root(cwd, repo_root_q)
        except ValueError as e:
            return jsonify({'success': False, 'error': str(e)}), 400
        repos = [
            r for r in repos
            if str(r.get('repo_root') or '') == want
        ]

    out = {
        'success': True,
        'project': {
            'id': proj.get('id') if proj else None,
            'name': proj.get('name') if proj else None,
            'path': cwd,
        },
        'repos': repos,
    }
    # Back-compat: single-repo clients still read flat fields.
    if len(repos) == 1:
        flat = dict(repos[0])
        flat.pop('label', None)
        out.update(flat)
    elif not repos:
        out['clean'] = True
        out['totals'] = {'files': 0, 'additions': 0, 'deletions': 0}
        out['files'] = []
    return jsonify(out)


@git_bp.route('/fs/reveal', methods=['POST'])
@owner_required
def fs_reveal_in_explorer():
    """Reveal a local file/folder in the host OS file manager (selected when possible).

    JSON: { "path": "C:/Projects/..." } or { "url": "file:///C:/Projects/..." }
    Chat file chips call this so clicks open Explorer instead of a blocked file:// nav.
    """
    try:
        from api.fs_reveal import reveal_path_or_error
    except ImportError:
        try:
            from fs_reveal import reveal_path_or_error  # type: ignore
        except ImportError as e:
            return jsonify({'success': False, 'error': f'reveal helper unavailable: {e}'}), 500

    body = request.get_json(silent=True) or {}
    target = (body.get('path') or body.get('url') or body.get('href') or '').strip()
    if not target:
        return jsonify({'success': False, 'error': 'path or url is required'}), 400

    result, err, status = reveal_path_or_error(target)
    if err:
        return jsonify({'success': False, 'error': err}), status
    return jsonify({'success': True, **(result or {})})


@git_bp.route('/fs/open', methods=['POST'])
@owner_required
def fs_open_default_app():
    """Open a local file with its registered desktop application."""
    try:
        from api.fs_reveal import open_in_default_app
    except ImportError:
        try:
            from fs_reveal import open_in_default_app  # type: ignore
        except ImportError as e:
            return jsonify({'success': False, 'error': f'file opener unavailable: {e}'}), 500

    body = request.get_json(silent=True) or {}
    target = (body.get('path') or body.get('url') or body.get('href') or '').strip()
    if not target:
        return jsonify({'success': False, 'error': 'path or url is required'}), 400
    try:
        result = open_in_default_app(target)
    except FileNotFoundError as e:
        return jsonify({'success': False, 'error': str(e)}), 404
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except OSError as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    return jsonify({'success': True, **result})


@git_bp.route('/git/open-file', methods=['POST'])
@owner_required
def git_open_file_default_app():
    """Open a project worktree file with its OS-registered application."""
    try:
        from scripts.utilities.git_pending_changes import resolve_allowed_project_cwd, resolve_allowed_repo_root
        from api.fs_reveal import open_in_default_app
    except ImportError as e:
        return jsonify({'success': False, 'error': f'file opener unavailable: {e}'}), 500
    body = request.get_json(silent=True) or {}
    rel_file = (body.get('file') or body.get('rel_path') or '').strip().replace('\\', '/')
    if not rel_file or rel_file.startswith('/') or '..' in rel_file.split('/'):
        return jsonify({'success': False, 'error': 'Invalid file path'}), 400
    pid_raw = body.get('project_id')
    try:
        project_id = int(pid_raw) if pid_raw not in (None, '') else None
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'Invalid project_id'}), 400
    try:
        projects = project_manager.get_projects() if project_manager else []
        current = project_manager.get_current_project() if project_manager else None
        cwd, _proj, err = resolve_allowed_project_cwd((body.get('path') or '').strip() or None, project_id, projects, current)
        if err or not cwd:
            return jsonify({'success': False, 'error': err or 'No project'}), 400
        root = resolve_allowed_repo_root(cwd, (body.get('repo_root') or '').strip() or None)
        target = (Path(root) / rel_file).resolve(strict=True)
        if not target.is_relative_to(Path(root).resolve()) or not target.is_file():
            return jsonify({'success': False, 'error': 'File is outside the selected worktree'}), 400
        result = open_in_default_app(str(target))
    except FileNotFoundError as e:
        return jsonify({'success': False, 'error': str(e)}), 404
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except OSError as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    return jsonify({'success': True, **result})


@git_bp.route('/git/pending-diff', methods=['GET'])
@authenticated_required
def git_pending_diff():
    """Return structured diff hunks for one pending file (in-app preview).

    Query: path (project), file (repo-relative path), repo_root (optional),
           context (optional, default 3)
    """
    try:
        from scripts.utilities.git_pending_changes import (
            collect_file_pending_diff,
            resolve_allowed_project_cwd,
            resolve_allowed_repo_root,
        )
    except ImportError as e:
        return jsonify({'success': False, 'error': f'git helper unavailable: {e}'}), 500

    path = (request.args.get('path') or '').strip() or None
    repo_root = (request.args.get('repo_root') or '').strip() or None
    rel_file = (request.args.get('file') or request.args.get('rel_path') or '').strip()
    if not rel_file:
        return jsonify({'success': False, 'error': 'file is required'}), 400

    ctx_raw = request.args.get('context', 3, type=int)
    context_lines = max(0, min(int(ctx_raw or 3), 20))
    max_lines_raw = request.args.get('max_lines', 300, type=int)
    max_lines = max(50, min(int(max_lines_raw or 300), 100_000))
    full = str(request.args.get('full') or '').strip().lower() in ('1', 'true', 'yes')

    pid_raw = request.args.get('project_id')
    project_id = None
    if pid_raw not in (None, ''):
        try:
            project_id = int(pid_raw)
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'Invalid project_id'}), 400

    try:
        projects = project_manager.get_projects() if project_manager else []
        current = project_manager.get_current_project() if project_manager else None
    except Exception as e:
        return jsonify({'success': False, 'error': f'Project lookup failed: {e}'}), 500

    cwd, proj, err = resolve_allowed_project_cwd(path, project_id, projects, current)
    if err or not cwd:
        return jsonify({'success': False, 'error': err or 'No project'}), 400

    try:
        git_cwd = resolve_allowed_repo_root(cwd, repo_root)
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400

    try:
        result = collect_file_pending_diff(
            git_cwd,
            rel_file,
            context_lines=context_lines,
            max_lines=max_lines,
            full=full,
        )
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

    return jsonify({
        'success': True,
        'project': {
            'id': proj.get('id') if proj else None,
            'name': proj.get('name') if proj else None,
            'path': cwd,
        },
        **result,
    })


@git_bp.route('/git/commit', methods=['POST'])
@owner_required
def git_commit_pending():
    """Stage pending changes for a registered project and create a commit.

    JSON body:
      path (required) — registered project path
      message (required) — commit message
      allow_secrets (optional bool) — allow committing .env / credential-like files
      files (optional list[str]) — repo-relative paths to include; omit for all
      all_pending (optional bool) — stage entire dirty tree (ignore files list)
      include_unlisted (optional bool) — also commit paths not in shown_files
      shown_files (list[str]) — required with include_unlisted (UI truncated list)
      repo_root (optional) — which nested work tree when the project has several
    """
    try:
        from scripts.utilities.git_pending_changes import (
            commit_pending_changes,
            resolve_allowed_project_cwd,
            resolve_allowed_repo_root,
        )
    except ImportError:
        try:
            from utilities.git_pending_changes import (  # type: ignore
                commit_pending_changes,
                resolve_allowed_project_cwd,
                resolve_allowed_repo_root,
            )
        except ImportError as e:
            return jsonify({'success': False, 'error': f'git helper unavailable: {e}'}), 500

    body = request.get_json(silent=True) or {}
    path = (body.get('path') or '').strip() or None
    repo_root = (body.get('repo_root') or '').strip() or None
    message = body.get('message')
    allow_secrets = bool(body.get('allow_secrets'))
    all_pending = bool(body.get('all_pending'))
    include_unlisted = bool(body.get('include_unlisted'))
    files_raw = body.get('files')
    shown_raw = body.get('shown_files')
    paths = None
    shown_files = None
    if all_pending:
        paths = None
        include_unlisted = False
    elif files_raw is not None:
        if not isinstance(files_raw, list):
            return jsonify({'success': False, 'error': 'files must be a list of paths'}), 400
        paths = [str(p).strip() for p in files_raw if str(p).strip()]
    if include_unlisted:
        if shown_raw is None:
            return jsonify({
                'success': False,
                'error': 'shown_files is required when include_unlisted is true',
            }), 400
        if not isinstance(shown_raw, list):
            return jsonify({'success': False, 'error': 'shown_files must be a list'}), 400
        shown_files = [str(p).strip() for p in shown_raw if str(p).strip()]
        if paths is None:
            paths = []
    pid_raw = body.get('project_id')
    project_id = None
    if pid_raw not in (None, ''):
        try:
            project_id = int(pid_raw)
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'Invalid project_id'}), 400

    try:
        projects = project_manager.get_projects() if project_manager else []
        current = project_manager.get_current_project() if project_manager else None
    except Exception as e:
        return jsonify({'success': False, 'error': f'Project lookup failed: {e}'}), 500

    cwd, proj, err = resolve_allowed_project_cwd(path, project_id, projects, current)
    if err or not cwd:
        return jsonify({'success': False, 'error': err or 'No project'}), 400

    try:
        git_cwd = resolve_allowed_repo_root(cwd, repo_root)
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400

    try:
        result = commit_pending_changes(
            git_cwd,
            str(message or ''),
            allow_secrets=allow_secrets,
            paths=paths,
            include_unlisted=include_unlisted,
            shown_files=shown_files,
        )
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        print(f"Error git_commit_pending: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

    return jsonify({
        'success': True,
        'project': {
            'id': proj.get('id') if proj else None,
            'name': proj.get('name') if proj else None,
            'path': cwd,
        },
        **result,
    })


@git_bp.route('/git/ignore', methods=['POST'])
@owner_required
def git_ignore_pending_path():
    """Add a pending path to .gitignore (and untrack from index if needed).

    JSON body:
      path (required) — registered project path
      file (required) — repo-relative path to ignore
      repo_root (optional) — nested work tree when the project has several
    """
    try:
        from scripts.utilities.git_pending_changes import (
            ignore_pending_path,
            resolve_allowed_project_cwd,
            resolve_allowed_repo_root,
        )
    except ImportError:
        try:
            from utilities.git_pending_changes import (  # type: ignore
                ignore_pending_path,
                resolve_allowed_project_cwd,
                resolve_allowed_repo_root,
            )
        except ImportError as e:
            return jsonify({'success': False, 'error': f'git helper unavailable: {e}'}), 500

    body = request.get_json(silent=True) or {}
    path = (body.get('path') or '').strip() or None
    repo_root = (body.get('repo_root') or '').strip() or None
    rel = (body.get('file') or body.get('rel_path') or '').strip()
    if not rel:
        return jsonify({'success': False, 'error': 'file is required'}), 400
    pid_raw = body.get('project_id')
    project_id = None
    if pid_raw not in (None, ''):
        try:
            project_id = int(pid_raw)
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'Invalid project_id'}), 400

    try:
        projects = project_manager.get_projects() if project_manager else []
        current = project_manager.get_current_project() if project_manager else None
    except Exception as e:
        return jsonify({'success': False, 'error': f'Project lookup failed: {e}'}), 500

    cwd, proj, err = resolve_allowed_project_cwd(path, project_id, projects, current)
    if err or not cwd:
        return jsonify({'success': False, 'error': err or 'No project'}), 400

    try:
        git_cwd = resolve_allowed_repo_root(cwd, repo_root)
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400

    try:
        result = ignore_pending_path(git_cwd, rel)
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

    return jsonify({
        'success': True,
        'project': {
            'id': proj.get('id') if proj else None,
            'name': proj.get('name') if proj else None,
            'path': cwd,
        },
        **result,
    })


@git_bp.route('/git/suggest-commit-message', methods=['POST'])
@owner_required
def git_suggest_commit_message():
    """Suggest a commit message from pending diffs + optional chat prompts.

    JSON body:
      path (required) — registered project path
      repo_root (optional) — nested work tree when the project has several
      session_id (optional) — auth chat session id for recent user prompts
    """
    try:
        from scripts.utilities.git_pending_changes import (
            collect_commit_suggest_context,
            resolve_allowed_project_cwd,
            resolve_allowed_repo_root,
        )
        from api.commit_message_suggester import (
            load_session_user_prompts,
            suggest_commit_message,
        )
    except ImportError as e:
        return jsonify({'success': False, 'error': f'suggest helper unavailable: {e}'}), 500

    body = request.get_json(silent=True) or {}
    path = (body.get('path') or '').strip() or None
    repo_root = (body.get('repo_root') or '').strip() or None
    pid_raw = body.get('project_id')
    project_id = None
    if pid_raw not in (None, ''):
        try:
            project_id = int(pid_raw)
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'Invalid project_id'}), 400

    session_id = body.get('session_id')
    if session_id in (None, ''):
        session_id = None
    else:
        try:
            session_id = int(session_id)
        except (TypeError, ValueError):
            session_id = None

    try:
        projects = project_manager.get_projects() if project_manager else []
        current = project_manager.get_current_project() if project_manager else None
    except Exception as e:
        return jsonify({'success': False, 'error': f'Project lookup failed: {e}'}), 500

    cwd, proj, err = resolve_allowed_project_cwd(path, project_id, projects, current)
    if err or not cwd:
        return jsonify({'success': False, 'error': err or 'No project'}), 400

    try:
        git_cwd = resolve_allowed_repo_root(cwd, repo_root)
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400

    files_raw = body.get('files')
    suggest_paths = None
    if files_raw is not None:
        if not isinstance(files_raw, list):
            return jsonify({'success': False, 'error': 'files must be a list of paths'}), 400
        suggest_paths = [str(p).strip() for p in files_raw if str(p).strip()]

    try:
        ctx = collect_commit_suggest_context(git_cwd, paths=suggest_paths)
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

    prompts = load_session_user_prompts(session_id)
    # Also accept client-supplied prompt snippets (anonymous / in-memory history).
    extra = body.get('prompts')
    if isinstance(extra, list):
        for p in extra:
            if isinstance(p, str) and p.strip():
                prompts.append(p.strip()[:280])
        if len(prompts) > 16:
            prompts = prompts[-16:]

    avoid_raw = body.get('avoid') or body.get('avoid_messages') or body.get('previous')
    avoid_messages = []
    if isinstance(avoid_raw, list):
        for item in avoid_raw:
            if isinstance(item, str) and item.strip():
                avoid_messages.append(item.strip()[:120])
    elif isinstance(avoid_raw, str) and avoid_raw.strip():
        avoid_messages.append(avoid_raw.strip()[:120])

    result = suggest_commit_message(
        ctx,
        user_prompts=prompts,
        avoid_messages=avoid_messages or None,
    )
    return jsonify({
        'success': True,
        'project': {
            'id': proj.get('id') if proj else None,
            'name': proj.get('name') if proj else None,
            'path': cwd,
        },
        'message': result.get('message'),
        'source': result.get('source'),
        'heuristic_message': result.get('heuristic_message'),
        'files_count': (ctx.get('totals') or {}).get('files') or len(ctx.get('files') or []),
    })


@git_bp.route('/prompt/enhance', methods=['POST'])
@authenticated_required
def prompt_enhance():
    """Rewrite a rough composer prompt into a clearer one (composer wand button).

    JSON body:
      prompt (required) — raw composer text
      project (optional) — project name for context
      context (optional) — [{role, content}] recent turns for grounding
    """
    from api.experimental import is_enabled
    if not is_enabled('composer_prompt_enhance'):
        return jsonify({'success': False, 'disabled': True, 'error': 'Prompt enhancer is disabled'})
    try:
        from api.prompt_enhancer import MAX_PROMPT_CHARS, enhance_prompt
    except ImportError as e:
        return jsonify({'success': False, 'error': f'enhancer unavailable: {e}'}), 500

    body = request.get_json(silent=True) or {}
    prompt = body.get('prompt')
    if not isinstance(prompt, str) or not prompt.strip():
        return jsonify({'success': False, 'error': 'prompt is required'}), 400
    if len(prompt) > MAX_PROMPT_CHARS:
        return jsonify({
            'success': False,
            'error': f'Prompt too long (>{MAX_PROMPT_CHARS} chars)',
        }), 400

    project_name = str(body.get('project') or '').strip()[:120]

    context_messages = []
    raw_ctx = body.get('context')
    if isinstance(raw_ctx, list):
        for m in raw_ctx[-8:]:
            if not isinstance(m, dict):
                continue
            content = str(m.get('content') or '').strip()
            if not content:
                continue
            context_messages.append({
                'role': str(m.get('role') or 'user'),
                'content': content[:2000],
            })

    try:
        result = enhance_prompt(
            prompt,
            context_messages=context_messages,
            project_name=project_name,
        )
    except Exception as e:
        print(f"[PROMPT-ENHANCE] failed: {e}", flush=True)
        return jsonify({'success': False, 'error': str(e)}), 500

    if not result.get('ok'):
        return jsonify({
            'success': False,
            'error': result.get('error') or 'Enhancement failed',
        }), 502

    return jsonify({
        'success': True,
        'prompt': result.get('prompt') or prompt,
        'original': prompt,
        'source': result.get('source') or '',
        'unchanged': bool(result.get('unchanged')),
    })


@git_bp.route('/git/graph', methods=['GET'])
@authenticated_required
def git_graph():
    """Commit graph for the visualizer (allowlisted project path).

    Query:
      path=…          registered project path (required unless project_id)
      project_id=…    optional
      repo_root=…     optional nested work tree when the project has several
      limit=80        page size (1–200)
      skip=0          commits to skip (pagination)
      all=0           include all refs (default 0); 1 = all branches
      branch=name     optional local branch filter (overrides all=1)
    """
    try:
        from scripts.utilities.git_pending_changes import (
            resolve_allowed_project_cwd,
            resolve_allowed_repo_root,
        )
        from scripts.utilities.git_graph import collect_git_graph
    except ImportError as e:
        return jsonify({'success': False, 'error': f'git graph helper unavailable: {e}'}), 500

    path = (request.args.get('path') or '').strip() or None
    repo_root = (request.args.get('repo_root') or '').strip() or None
    pid_raw = request.args.get('project_id')
    project_id = None
    if pid_raw not in (None, ''):
        try:
            project_id = int(pid_raw)
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'Invalid project_id'}), 400

    limit = request.args.get('limit', 80, type=int) or 80
    skip = request.args.get('skip', 0, type=int) or 0
    all_raw = (request.args.get('all') or '0').strip().lower()
    all_refs = all_raw in ('1', 'true', 'yes', 'on')
    branch = (request.args.get('branch') or '').strip() or None

    try:
        projects = project_manager.get_projects() if project_manager else []
        current = project_manager.get_current_project() if project_manager else None
    except Exception as e:
        return jsonify({'success': False, 'error': f'Project lookup failed: {e}'}), 500

    cwd, proj, err = resolve_allowed_project_cwd(path, project_id, projects, current)
    if err or not cwd:
        return jsonify({'success': False, 'error': err or 'No project'}), 400

    try:
        git_cwd = resolve_allowed_repo_root(cwd, repo_root)
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400

    try:
        data = collect_git_graph(
            git_cwd, limit=limit, skip=skip, all_refs=all_refs, branch=branch
        )
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

    return jsonify({
        'success': True,
        'project': {
            'id': proj.get('id') if proj else None,
            'name': proj.get('name') if proj else None,
            'path': cwd,
        },
        **data,
    })


@git_bp.route('/git/commit/<commit_hash>/detail', methods=['GET'])
@authenticated_required
def git_commit_detail(commit_hash):
    """Commit metadata + files + capped patch for an allowlisted project path."""
    try:
        from scripts.utilities.git_pending_changes import (
            resolve_allowed_project_cwd,
            resolve_allowed_repo_root,
        )
        from scripts.utilities.git_graph import collect_commit_detail
    except ImportError as e:
        return jsonify({'success': False, 'error': f'git graph helper unavailable: {e}'}), 500

    path = (request.args.get('path') or '').strip() or None
    repo_root = (request.args.get('repo_root') or '').strip() or None
    pid_raw = request.args.get('project_id')
    project_id = None
    if pid_raw not in (None, ''):
        try:
            project_id = int(pid_raw)
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'Invalid project_id'}), 400

    try:
        projects = project_manager.get_projects() if project_manager else []
        current = project_manager.get_current_project() if project_manager else None
    except Exception as e:
        return jsonify({'success': False, 'error': f'Project lookup failed: {e}'}), 500

    cwd, proj, err = resolve_allowed_project_cwd(path, project_id, projects, current)
    if err or not cwd:
        return jsonify({'success': False, 'error': err or 'No project'}), 400

    try:
        git_cwd = resolve_allowed_repo_root(cwd, repo_root)
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400

    try:
        data = collect_commit_detail(git_cwd, commit_hash)
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

    return jsonify({
        'success': True,
        'project': {
            'id': proj.get('id') if proj else None,
            'name': proj.get('name') if proj else None,
            'path': cwd,
        },
        **data,
    })


@git_bp.route('/git/commit/<commit_hash>/file-diff', methods=['GET'])
@authenticated_required
def git_commit_file_diff(commit_hash):
    """Structured diff hunks for one file in a commit (Git page modal).

    Query: path (project), file (repo-relative), repo_root (optional),
           context (optional, default 3)
    """
    try:
        from scripts.utilities.git_pending_changes import (
            resolve_allowed_project_cwd,
            resolve_allowed_repo_root,
        )
        from scripts.utilities.git_graph import collect_file_commit_diff
    except ImportError as e:
        return jsonify({'success': False, 'error': f'git graph helper unavailable: {e}'}), 500

    path = (request.args.get('path') or '').strip() or None
    repo_root = (request.args.get('repo_root') or '').strip() or None
    rel_file = (request.args.get('file') or request.args.get('rel_path') or '').strip()
    if not rel_file:
        return jsonify({'success': False, 'error': 'file is required'}), 400

    ctx_raw = request.args.get('context', 3, type=int)
    context_lines = max(0, min(int(ctx_raw or 3), 20))
    max_lines_raw = request.args.get('max_lines', 300, type=int)
    max_lines = max(50, min(int(max_lines_raw or 300), 100_000))
    full = str(request.args.get('full') or '').strip().lower() in ('1', 'true', 'yes')

    pid_raw = request.args.get('project_id')
    project_id = None
    if pid_raw not in (None, ''):
        try:
            project_id = int(pid_raw)
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'Invalid project_id'}), 400

    try:
        projects = project_manager.get_projects() if project_manager else []
        current = project_manager.get_current_project() if project_manager else None
    except Exception as e:
        return jsonify({'success': False, 'error': f'Project lookup failed: {e}'}), 500

    cwd, proj, err = resolve_allowed_project_cwd(path, project_id, projects, current)
    if err or not cwd:
        return jsonify({'success': False, 'error': err or 'No project'}), 400

    try:
        git_cwd = resolve_allowed_repo_root(cwd, repo_root)
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400

    try:
        result = collect_file_commit_diff(
            git_cwd,
            commit_hash,
            rel_file,
            context_lines=context_lines,
            max_lines=max_lines,
            full=full,
        )
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

    return jsonify({
        'success': True,
        'project': {
            'id': proj.get('id') if proj else None,
            'name': proj.get('name') if proj else None,
            'path': cwd,
        },
        **result,
    })


@git_bp.route('/git/branches', methods=['GET'])
@authenticated_required
def git_branches():
    """Get Git branches"""
    try:
        from api.git_service import GitError, list_branches, resolve_repo_cwd

        current_project = project_manager.get_current_project()
        cwd = resolve_repo_cwd(current_project)

        out = list_branches(cwd)

        return jsonify({
            'success': True,
            'data': out['branches'],
            'current': out['current']
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

@git_bp.route('/git/commits', methods=['GET'])
@authenticated_required
def git_commits():
    """Get recent Git commits with pagination"""
    try:
        from api.git_service import GitError, list_commits, resolve_repo_cwd

        # Get pagination parameters
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 20, type=int)

        # Validate pagination parameters
        page = max(1, page)
        per_page = min(max(1, per_page), 100)  # Limit to 100 commits per page

        current_project = project_manager.get_current_project()
        cwd = resolve_repo_cwd(current_project)

        out = list_commits(cwd, page, per_page)

        return jsonify({
            'success': True,
            'data': out['commits'],
            'pagination': out['pagination']
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

@git_bp.route('/git/files', methods=['GET'])
@authenticated_required
def git_files():
    """Get Git working directory files with pagination"""
    try:
        from api.git_service import GitError, list_files, resolve_repo_cwd

        # Get pagination parameters
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)
        status_filter = request.args.get('status', None)  # Filter by status: modified, added, deleted, untracked, clean

        # Validate pagination parameters
        page = max(1, page)
        per_page = min(max(1, per_page), 200)  # Limit to 200 items per page

        current_project = project_manager.get_current_project()
        cwd = resolve_repo_cwd(current_project)

        out = list_files(cwd, page, per_page, status_filter)

        return jsonify({
            'success': True,
            'data': out['files'],
            'pagination': out['pagination']
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

@git_bp.route('/git/commit/<commit_hash>/diff', methods=['GET'])
@authenticated_required
def git_commit_diff(commit_hash):
    """Get diff for a specific commit with pagination"""
    try:
        from api.git_service import GitError, commit_diff, resolve_repo_cwd

        # Get pagination parameters
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 100, type=int)  # Lines per page for diff
        file_filter = request.args.get('file', None)  # Filter by specific file

        # Validate pagination parameters
        page = max(1, page)
        per_page = min(max(1, per_page), 500)  # Limit to 500 lines per page

        current_project = project_manager.get_current_project()
        cwd = resolve_repo_cwd(current_project)

        out = commit_diff(cwd, commit_hash, page, per_page, file_filter)

        return jsonify({
            'success': True,
            'data': {
                'commit': out['commit'],
                'files': out['files'],
                'diff': out['diff'],
                'pagination': out['pagination']
            }
        })

    except GitError as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500
    except Exception as e:
        print(f"Error getting commit diff: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@git_bp.route('/git/pull', methods=['POST'])
@owner_required
def git_pull():
    """Pull changes from remote"""
    try:
        from api.git_service import GitError, pull_repo, resolve_repo_cwd

        data = request.get_json()
        remote = data.get('remote', 'origin')
        branch = data.get('branch', 'main')

        current_project = project_manager.get_current_project()
        cwd = resolve_repo_cwd(current_project)

        # Pull from remote
        output = pull_repo(cwd, remote, branch)

        return jsonify({
            'success': True,
            'message': 'Pull successful',
            'output': output
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

@git_bp.route('/git/push', methods=['POST'])
@owner_required
def git_push():
    """Push the current branch to its upstream (or optional remote/branch).

    JSON (all optional):
      path / project_id — chat project (same allowlist as pending-changes)
      repo_root          — nested work tree when the project has several
      remote / branch   — when both set, `git push <remote> <branch>` (legacy git UI)
    """
    try:
        from api.git_service import current_branch_name, push_repo

        try:
            from scripts.utilities.git_pending_changes import (
                git_push_target,
                resolve_allowed_project_cwd,
                resolve_allowed_repo_root,
                sanitize_git_output,
            )
        except ImportError:
            from utilities.git_pending_changes import (  # type: ignore
                git_push_target,
                resolve_allowed_project_cwd,
                resolve_allowed_repo_root,
                sanitize_git_output,
            )

        data = request.get_json(silent=True) or {}
        path = (data.get('path') or '').strip() or None
        repo_root = (data.get('repo_root') or '').strip() or None
        pid_raw = data.get('project_id')
        project_id = None
        if pid_raw not in (None, ''):
            try:
                project_id = int(pid_raw)
            except (TypeError, ValueError):
                return jsonify({'success': False, 'error': 'Invalid project_id'}), 400

        try:
            projects = project_manager.get_projects() if project_manager else []
            current = project_manager.get_current_project() if project_manager else None
        except Exception as e:
            return jsonify({'success': False, 'error': f'Project lookup failed: {e}'}), 500

        cwd, proj, err = resolve_allowed_project_cwd(path, project_id, projects, current)
        if err or not cwd:
            return jsonify({'success': False, 'error': err or 'No project'}), 400

        try:
            git_cwd = resolve_allowed_repo_root(cwd, repo_root)
        except ValueError as e:
            return jsonify({'success': False, 'error': str(e)}), 400

        remote = (data.get('remote') or '').strip()
        branch = (data.get('branch') or '').strip()
        target = git_push_target(git_cwd)
        if not remote:
            remote = (target.get('remote') or '').strip()
        if not branch:
            branch = (target.get('branch') or '').strip()
            if not branch:
                branch = current_branch_name(git_cwd)

        result = push_repo(git_cwd, remote, branch)
        if result.returncode != 0:
            from core.git_push_diagnostics import push_failure
            failure = push_failure(sanitize_git_output(result.stdout or ''),
                                   sanitize_git_output(result.stderr or ''))
            detail = failure['error']
            print(f"Error git_push: {detail}", flush=True)
            if 'could not read Username' in detail or 'Authentication failed' in detail:
                detail = (
                    'Git push needs credentials for this remote. Set up a git credential '
                    'helper (for GitHub: gh auth setup-git) or an SSH remote.'
                )
            return jsonify({
                'success': False,
                'error': detail,
                'push_report': failure.get('push_report'),
                'output': failure['output'],
                'branch': branch or target.get('branch'),
                'remote': remote or target.get('remote'),
                'repo': target.get('repo'),
                'url': target.get('url'),
                'repo_root': git_cwd,
                'project': {
                    'id': proj.get('id') if proj else None,
                    'name': proj.get('name') if proj else None,
                    'path': cwd,
                },
            }), 500

        out = sanitize_git_output((result.stdout or '').strip())
        err_out = sanitize_git_output((result.stderr or '').strip())
        return jsonify({
            'success': True,
            'message': 'Push successful',
            'output': out or err_out,
            'branch': branch or target.get('branch'),
            'remote': remote or target.get('remote'),
            'repo': target.get('repo'),
            'url': target.get('url'),
            'repo_root': git_cwd,
            'project': {
                'id': proj.get('id') if proj else None,
                'name': proj.get('name') if proj else None,
                'path': cwd,
            },
        })

    except Exception as e:
        print(f"Error git_push: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@git_bp.route('/git/branch', methods=['POST'])
@owner_required
def git_branch():
    """Create, switch, or delete branches"""
    try:
        from api.git_service import GitError, branch_operation, resolve_repo_cwd

        data = request.get_json()
        if not data or 'action' not in data or 'branch' not in data:
            return jsonify({
                'success': False,
                'error': 'Action and branch name are required'
            }), 400

        current_project = project_manager.get_current_project()
        cwd = resolve_repo_cwd(current_project)
        action = data['action']
        branch = data['branch']

        try:
            out = branch_operation(cwd, action, branch)
        except ValueError:
            return jsonify({
                'success': False,
                'error': f'Invalid action: {action}'
            }), 400

        return jsonify({
            'success': True,
            'message': out['message'],
            'output': out['output']
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


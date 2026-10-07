#!/usr/bin/env python3
"""
Project Management System for Cuttle
Handles local and remote project tracking, switching, and management
"""

import os
import json
import sqlite3
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime
import requests
from urllib.parse import urlparse
from contextlib import contextmanager

from core.runtime_paths import data_db_dir
from managers.cuttle_scaffold import ensure_cuttle_scaffold
from managers.project_locations import check_paths, validate_paths, require_project_path


def _live_project_path(path: Optional[str]) -> str:
    """Rewrite cloned Windows paths onto this machine when they exist."""
    raw = (path or "").strip()
    if not raw:
        return raw
    try:
        from core.runtime_paths import rewrite_windows_lab_path

        mapped = (rewrite_windows_lab_path(raw) or "").strip()
        if mapped:
            return mapped
    except Exception:
        pass
    return raw


def _validated_project_changes(kwargs):
    allowed = {'name', 'description', 'tags', 'paths', 'path', 'repo_url', 'archived'}
    if set(kwargs) - allowed:
        raise ValueError('Unsupported project setting.')
    values = dict(kwargs)
    if 'path' in values:
        if 'paths' in values:
            raise ValueError('Use paths or path, not both.')
        values['paths'] = [values.pop('path')]
    if 'paths' in values:
        values['paths'] = validate_paths(values['paths'])
    for field in ('name', 'description', 'repo_url'):
        if field in values and not isinstance(values[field], str):
            raise ValueError(f'{field} must be text.')
    if len(values.get('description', '')) > 4000 or len(values.get('repo_url', '')) > 2048:
        raise ValueError('Description or repository URL is too long.')
    if 'name' in values:
        values['name'] = values['name'].strip()
        if not values['name'] or len(values['name']) > 120:
            raise ValueError('Project name must contain 1–120 characters.')
    if 'tags' in values and (not isinstance(values['tags'], list) or
            not all(isinstance(t, str) and len(t) <= 80 for t in values['tags']) or len(values['tags']) > 32):
        raise ValueError('Tags must be a list of up to 32 short strings.')
    if 'archived' in values and not isinstance(values['archived'], bool):
        raise ValueError('Archived must be true or false.')
    if 'repo_url' in values:
        url = values['repo_url'].strip()
        if url and not (url.startswith(('https://', 'http://', 'ssh://', 'git@'))):
            raise ValueError('Use an HTTP(S) or SSH repository URL.')
        values['repo_url'] = url
    return values


class ProjectManager:
    """Manages local and remote projects for the Cuttle suite"""
    
    def __init__(self, db_path: str = "projects.db"):
        self.db_path = db_path
        self.current_project = None
        self.init_database()
        self.load_current_project()
        self.ensure_default_project()
    
    @contextmanager
    def get_db_connection(self):
        """Context manager for database connections with proper error handling"""
        conn = None
        try:
            conn = sqlite3.connect(self.db_path, timeout=30.0)
            yield conn
        except sqlite3.OperationalError as e:
            if conn:
                conn.rollback()
            raise e
        finally:
            if conn:
                conn.close()
    
    def init_database(self):
        """Initialize the projects database"""
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        cursor = conn.cursor()
        
        # Enable WAL mode for better concurrency
        cursor.execute('PRAGMA journal_mode=WAL')
        cursor.execute('PRAGMA synchronous=NORMAL')
        cursor.execute('PRAGMA cache_size=1000')
        cursor.execute('PRAGMA temp_store=MEMORY')
        
        # Create projects table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                type TEXT NOT NULL,  -- 'local', 'github', 'gitlab', 'ftp', 'ssh'
                path TEXT,  -- Local path or remote URL
                description TEXT,
                tags TEXT,  -- JSON array of tags
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_accessed TIMESTAMP,
                is_active BOOLEAN DEFAULT 0,
                config TEXT  -- JSON config for project-specific settings
            )
        ''')
        
        # Create project_history table for tracking project switches
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS project_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER,
                action TEXT,  -- 'switched_to', 'created', 'updated', 'deleted'
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                details TEXT,  -- JSON details about the action
                FOREIGN KEY (project_id) REFERENCES projects (id)
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def ensure_default_project(self):
        """Ensure there's a default project pointing at this Cuttle checkout.

        The repository root is resolved from this file's location — never
        ``Path.cwd()``, which is ``src/`` when Flask runs with ``cwd=src``
        and would register the wrong directory on fresh installs.
        """
        try:
            # Check if we already have any projects
            projects = self.get_projects()
            if projects:
                self._normalize_legacy_default_name(projects)
                return  # Already have projects, no need to add default

            # Repository root from this file's location (robust to cwd).
            repo_root = Path(__file__).resolve().parents[2]
            current_dir = repo_root if repo_root.exists() else Path.cwd().resolve()

            # Add the current Cuttle project as the default project
            success = self.add_local_project(
                name="Cuttle",
                path=str(current_dir),
                description="Main Cuttle project - your current workspace",
                tags=["cuttle", "main", "default"]
            )

            if success:
                # Set this as the current project
                projects = self.get_projects()
                if projects:
                    self.switch_to_project(projects[0]['id'])
                    print(f"✅ Added default project: Cuttle ({current_dir})")

        except Exception as e:
            print(f"Warning: Could not create default project: {e}")

    def _normalize_legacy_default_name(self, projects) -> None:
        """Rename the auto-created fresh-install default to ``Cuttle``.

        Early fresh installs registered the checkout as ``Cuttle
        Development``. Only the untouched auto-created row is renamed
        (matching name, default tag, and repo-root path) — user projects
        and user-selected pins are never modified.
        """
        try:
            repo_root = str(Path(__file__).resolve().parents[2])
            names = {str(p.get('name') or '') for p in projects}
            if 'Cuttle' in names:
                return
            for p in projects:
                if str(p.get('name') or '') != 'Cuttle Development':
                    continue
                tags = p.get('tags') or []
                if isinstance(tags, str):
                    try:
                        tags = json.loads(tags)
                    except (ValueError, TypeError):
                        tags = []
                if 'default' not in [str(t) for t in tags]:
                    continue
                if str(p.get('path') or '') != repo_root:
                    continue
                with self.get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        'UPDATE projects SET name = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
                        ('Cuttle', p['id']),
                    )
                    conn.commit()
                print(f"✅ Renamed default project to Cuttle ({repo_root})")
                return
        except Exception as e:
            print(f"Warning: Could not normalize default project name: {e}")
    
    def register_project(self, name, path, description='', tags=None, repo_url=''):
        values = _validated_project_changes({'name': name, 'paths': [path],
                    'description': description, 'tags': tags or [], 'repo_url': repo_url})
        health = check_paths(values['paths'])
        if not health['available']:
            raise ValueError('The folder must be accessible on the Cuttle host.')
        with self.get_db_connection() as conn:
            if conn.execute('SELECT 1 FROM projects WHERE name = ?', (values['name'],)).fetchone():
                raise ValueError('A project with this name already exists.')
        ensure_cuttle_scaffold(health['resolved_path'], project_name=values['name'])
        with self.get_db_connection() as conn:
            cursor = conn.execute("""
                INSERT INTO projects(name, type, path, description, tags, is_active, config)
                VALUES (?, 'local', ?, ?, ?, 0, ?)
            """, (values['name'], values['paths'][0], values['description'],
                   json.dumps(values['tags']), json.dumps({'paths': values['paths'], 'repo_url': values['repo_url']})))
            project_id = cursor.lastrowid
            conn.execute('INSERT INTO project_history(project_id, action, details) VALUES (?, ?, ?)',
                         (project_id, 'created', json.dumps(values)))
            conn.commit()
        return project_id

    def add_local_project(self, name: str, path: str, description: str = "", tags: List[str] = None) -> bool:
        """Add a local project"""
        import time
        max_retries = 3
        
        for attempt in range(max_retries):
            try:
                path = Path(path).resolve()
                if not path.exists():
                    print(f"Error: Path does not exist: {path}")
                    return False
                
                with self.get_db_connection() as conn:
                    cursor = conn.cursor()
                    
                    cursor.execute('''
                        INSERT INTO projects (name, type, path, description, tags, is_active)
                        VALUES (?, ?, ?, ?, ?, ?)
                    ''', (name, 'local', str(path), description, json.dumps(tags or []), 0))
                    
                    project_id = cursor.lastrowid
                    conn.commit()
                    
                    # Log the action in a separate transaction
                    self._log_project_action(project_id, 'created', {'path': str(path)})

                # Idempotent .cuttle/ tree (API register + agent-driven creates)
                try:
                    created = ensure_cuttle_scaffold(path, project_name=name)
                    if created:
                        print(f"Scaffolded .cuttle/ for {name}: {len(created)} path(s)")
                except Exception as scaffold_err:
                    print(f"Warning: .cuttle/ scaffold failed for {path}: {scaffold_err}")

                return True
            except sqlite3.OperationalError as e:
                if "database is locked" in str(e) and attempt < max_retries - 1:
                    print(f"Database locked, retrying in 1 second... (attempt {attempt + 1})")
                    time.sleep(1)
                    continue
                else:
                    print(f"Error adding local project: {e}")
                    return False
            except Exception as e:
                print(f"Error adding local project: {e}")
                return False
        
        return False
    
    def add_github_project(self, name: str, repo_url: str, local_path: str = None, description: str = "", tags: List[str] = None) -> bool:
        """Add a GitHub project"""
        try:
            # Parse GitHub URL
            parsed = urlparse(repo_url)
            if 'github.com' not in parsed.netloc:
                return False
            
            # Extract repo info
            path_parts = parsed.path.strip('/').split('/')
            if len(path_parts) < 2:
                return False
            
            owner, repo = path_parts[0], path_parts[1]
            
            # If no local path provided, create one
            if not local_path:
                local_path = Path.home() / "Projects" / repo
                local_path.mkdir(parents=True, exist_ok=True)
            
            # Clone if directory doesn't exist or is empty
            local_path = Path(local_path)
            if not local_path.exists() or not any(local_path.iterdir()):
                subprocess.run(['git', 'clone', repo_url, str(local_path)], check=True)
            
            conn = sqlite3.connect(self.db_path, timeout=30.0)
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO projects (name, type, path, description, tags, is_active, config)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (name, 'github', str(local_path), description, json.dumps(tags or []), 0, 
                  json.dumps({'repo_url': repo_url, 'owner': owner, 'repo': repo})))
            
            project_id = cursor.lastrowid
            self._log_project_action(project_id, 'created', {'repo_url': repo_url, 'local_path': str(local_path)})
            
            conn.commit()
            conn.close()
            try:
                created = ensure_cuttle_scaffold(local_path, project_name=name)
                if created:
                    print(f"Scaffolded .cuttle/ for {name}: {len(created)} path(s)")
            except Exception as scaffold_err:
                print(f"Warning: .cuttle/ scaffold failed for {local_path}: {scaffold_err}")
            return True
        except Exception as e:
            print(f"Error adding GitHub project: {e}")
            return False
    
    def add_gitlab_project(self, name: str, repo_url: str, local_path: str = None, description: str = "", tags: List[str] = None) -> bool:
        """Add a GitLab project"""
        try:
            # Parse GitLab URL
            parsed = urlparse(repo_url)
            if 'gitlab.com' not in parsed.netloc:
                return False
            
            # If no local path provided, create one
            if not local_path:
                repo_name = parsed.path.strip('/').split('/')[-1]
                local_path = Path.home() / "Projects" / repo_name
                local_path.mkdir(parents=True, exist_ok=True)
            
            # Clone if directory doesn't exist or is empty
            local_path = Path(local_path)
            if not local_path.exists() or not any(local_path.iterdir()):
                subprocess.run(['git', 'clone', repo_url, str(local_path)], check=True)
            
            conn = sqlite3.connect(self.db_path, timeout=30.0)
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO projects (name, type, path, description, tags, is_active, config)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (name, 'gitlab', str(local_path), description, json.dumps(tags or []), 0, 
                  json.dumps({'repo_url': repo_url})))
            
            project_id = cursor.lastrowid
            self._log_project_action(project_id, 'created', {'repo_url': repo_url, 'local_path': str(local_path)})
            
            conn.commit()
            conn.close()
            try:
                created = ensure_cuttle_scaffold(local_path, project_name=name)
                if created:
                    print(f"Scaffolded .cuttle/ for {name}: {len(created)} path(s)")
            except Exception as scaffold_err:
                print(f"Warning: .cuttle/ scaffold failed for {local_path}: {scaffold_err}")
            return True
        except Exception as e:
            print(f"Error adding GitLab project: {e}")
            return False
    
    def _project_record(self, row):
        config = json.loads(row[10]) if row[10] else {}
        stored_path = row[3] or ''
        paths = config.get('paths') or [stored_path]
        # Preserve legacy lab-path compatibility until the user saves an explicit list.
        if 'paths' not in config:
            mapped = _live_project_path(stored_path)
            if mapped and mapped != stored_path:
                paths = [mapped, stored_path]
        health = check_paths(paths)
        return {
            'id': row[0], 'name': row[1], 'type': row[2],
            'path': health['resolved_path'] or stored_path,
            'stored_path': stored_path, 'paths': paths,
            'description': row[4], 'tags': json.loads(row[5]) if row[5] else [],
            'created_at': row[6], 'updated_at': row[7], 'last_accessed': row[8],
            'is_active': bool(row[9]), 'config': config,
            'archived': bool(config.get('archived', False)), **health,
        }

    def get_projects(self, include_archived=False) -> List[Dict[str, Any]]:
        with self.get_db_connection() as conn:
            rows = conn.execute("""
                SELECT id, name, type, path, description, tags, created_at, updated_at,
                       last_accessed, is_active, config FROM projects
                ORDER BY last_accessed DESC, name ASC
            """).fetchall()
        projects = [self._project_record(row) for row in rows]
        return projects if include_archived else [p for p in projects if not p['archived']]

    def get_project(self, project_id: int) -> Optional[Dict[str, Any]]:
        with self.get_db_connection() as conn:
            row = conn.execute("""
                SELECT id, name, type, path, description, tags, created_at, updated_at,
                       last_accessed, is_active, config FROM projects WHERE id = ?
            """, (project_id,)).fetchone()
        return self._project_record(row) if row else None

    def switch_to_project(self, project_id: int) -> bool:
        """Switch to a specific project"""
        import time
        max_retries = 3
        
        for attempt in range(max_retries):
            try:
                project = self.get_project(project_id)
                if not project or project.get('archived'):
                    return False
                require_project_path(project)
                
                with self.get_db_connection() as conn:
                    cursor = conn.cursor()
                    
                    # Set all projects to inactive
                    cursor.execute('UPDATE projects SET is_active = 0')
                    
                    # Set selected project to active
                    cursor.execute('''
                        UPDATE projects 
                        SET is_active = 1, last_accessed = CURRENT_TIMESTAMP
                        WHERE id = ?
                    ''', (project_id,))
                    
                    conn.commit()
                
                # Log the action in a separate transaction
                self._log_project_action(project_id, 'switched_to', {'project_name': project['name']})
                
                self.current_project = project
                return True
            except sqlite3.OperationalError as e:
                if "database is locked" in str(e) and attempt < max_retries - 1:
                    print(f"Database locked while switching project, retrying in 1 second... (attempt {attempt + 1})")
                    time.sleep(1)
                    continue
                else:
                    print(f"Error switching to project: {e}")
                    return False
            except Exception as e:
                print(f"Error switching to project: {e}")
                return False
        
        return False
    
    def update_project(self, project_id: int, **kwargs) -> bool:
        """Atomic registry update. Path changes preserve identity and historical chats."""
        values = _validated_project_changes(kwargs)
        with self.get_db_connection() as conn:
            # Read + merge under the same lock so concurrent changes preserve unknown config.
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT config FROM projects WHERE id = ?', (project_id,)).fetchone()
            if row is None:
                return False
            config = json.loads(row[0]) if row[0] else {}
            fields, args = [], []
            for key, value in values.items():
                if key in ('paths', 'repo_url', 'archived'):
                    config[key] = value
                    if key == 'paths':
                        fields.append('path = ?')
                        args.append(value[0])
                else:
                    fields.append(key + ' = ?')
                    args.append(json.dumps(value) if key == 'tags' else value)
            fields += ['config = ?', 'updated_at = CURRENT_TIMESTAMP']
            args += [json.dumps(config), project_id]
            conn.execute('UPDATE projects SET ' + ', '.join(fields) + ' WHERE id = ?', args)
            conn.execute('INSERT INTO project_history(project_id, action, details) VALUES (?, ?, ?)',
                         (project_id, 'updated', json.dumps(values)))
            conn.commit()
        if self.current_project and self.current_project['id'] == project_id:
            self.current_project = self.get_project(project_id)
        return True

    def delete_project(self, project_id: int) -> bool:
        """Unregister only; preserve worktree, chat history, and the registry audit log."""
        with self.get_db_connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT name FROM projects WHERE id = ?', (project_id,)).fetchone()
            if row is None:
                return False
            conn.execute('DELETE FROM projects WHERE id = ?', (project_id,))
            conn.execute('INSERT INTO project_history(project_id, action, details) VALUES (?, ?, ?)',
                         (project_id, 'deleted', json.dumps({'project_name': row[0]})))
            conn.commit()
        if self.current_project and self.current_project['id'] == project_id:
            self.current_project = None
        return True

    def get_current_project(self) -> Optional[Dict[str, Any]]:
        if self.current_project:
            self.current_project = self.get_project(self.current_project['id'])
        return self.current_project

    def load_current_project(self):
        with self.get_db_connection() as conn:
            row = conn.execute('SELECT id FROM projects WHERE is_active = 1 LIMIT 1').fetchone()
        self.current_project = self.get_project(row[0]) if row else None

    def get_project_history(self, project_id: int = None, limit: int = 50) -> List[Dict[str, Any]]:
        """Get project history"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        if project_id:
            cursor.execute('''
                SELECT ph.id, ph.project_id, p.name, ph.action, ph.timestamp, ph.details
                FROM project_history ph
                LEFT JOIN projects p ON ph.project_id = p.id
                WHERE ph.project_id = ?
                ORDER BY ph.timestamp DESC
                LIMIT ?
            ''', (project_id, limit))
        else:
            cursor.execute('''
                SELECT ph.id, ph.project_id, p.name, ph.action, ph.timestamp, ph.details
                FROM project_history ph
                LEFT JOIN projects p ON ph.project_id = p.id
                ORDER BY ph.timestamp DESC
                LIMIT ?
            ''', (limit,))
        
        history = []
        for row in cursor.fetchall():
            history.append({
                'id': row[0],
                'project_id': row[1],
                'project_name': row[2],
                'action': row[3],
                'timestamp': row[4],
                'details': json.loads(row[5]) if row[5] else {}
            })
        
        conn.close()
        return history
    
    def _log_project_action(self, project_id: int, action: str, details: Dict[str, Any]):
        """Log a project action to history"""
        try:
            with self.get_db_connection() as conn:
                cursor = conn.cursor()
                
                cursor.execute('''
                    INSERT INTO project_history (project_id, action, details)
                    VALUES (?, ?, ?)
                ''', (project_id, action, json.dumps(details)))
                
                conn.commit()
        except Exception as e:
            print(f"Error logging project action: {e}")
    
    def sync_remote_project(self, project_id: int) -> bool:
        """Sync a remote project (pull latest changes)"""
        try:
            project = self.get_project(project_id)
            if not project or project['type'] not in ['github', 'gitlab']:
                return False
            
            local_path = Path(project['path'])
            if not local_path.exists():
                return False
            
            # Pull latest changes
            result = subprocess.run(['git', 'pull'], cwd=local_path, 
                                  capture_output=True, text=True, check=True)
            
            # Update last accessed time
            conn = sqlite3.connect(self.db_path, timeout=30.0)
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE projects 
                SET last_accessed = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (project_id,))
            conn.commit()
            conn.close()
            
            self._log_project_action(project_id, 'synced', {'output': result.stdout})
            return True
        except Exception as e:
            print(f"Error syncing project: {e}")
            return False
    
    def get_project_stats(self) -> Dict[str, Any]:
        """Get project statistics"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Total projects
        cursor.execute('SELECT COUNT(*) FROM projects')
        total_projects = cursor.fetchone()[0]
        
        # Projects by type
        cursor.execute('SELECT type, COUNT(*) FROM projects GROUP BY type')
        by_type = dict(cursor.fetchall())
        
        # Recent activity
        cursor.execute('''
            SELECT COUNT(*) FROM project_history 
            WHERE timestamp > datetime('now', '-7 days')
        ''')
        recent_activity = cursor.fetchone()[0]
        
        conn.close()
        
        return {
            'total_projects': total_projects,
            'by_type': by_type,
            'recent_activity': recent_activity,
            'current_project': self.current_project['name'] if self.current_project else None
        }

# Global project manager instance — the per-user home's projects.db (not cwd-relative).
_PROJECTS_DB_PATH = data_db_dir() / "projects.db"
project_manager = ProjectManager(str(_PROJECTS_DB_PATH))

# Repo root (parent of src/) for cwd fallbacks that cannot rely on the
# entry module's globals.
REPO_ROOT = Path(__file__).resolve().parents[2]


def default_chat_cwd(pm, root=None) -> str:
    """Fallback cwd when the request/session has no project.

    Prefer the registered Cuttle project (often ``…/src``, matching the
    Cursor workspace) over the repo root. Mixing those two on the first
    vs second /cursor turn forks Cursor ``--resume``. Explicit inputs so
    harness code never imports the entry module for this heuristic;
    pass the module ``project_manager`` singleton (or None) and an
    explicit root.
    """
    import re as _re

    if pm is not None:
        try:
            cur = getattr(pm, 'current_project', None)
            if isinstance(cur, dict) and (cur.get('path') or '').strip():
                return str(cur['path']).strip()
            for p in pm.get_projects() or []:
                name = str(p.get('name') or '')
                path = str(p.get('path') or '').strip()
                if path and _re.search(r'cuttle', name, _re.I):
                    return path
        except Exception:
            pass
    base = Path(root) if root is not None else REPO_ROOT
    return str(base)

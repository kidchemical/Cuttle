"""Ordered host-side project locations; no client filesystem checks or writes."""
import os
import re
import stat
from pathlib import Path


class ProjectUnavailable(ValueError):
    pass


def validate_paths(paths):
    if not isinstance(paths, list) or not 1 <= len(paths) <= 32:
        raise ValueError('Provide between 1 and 32 project paths.')
    result = []
    for path in paths:
        if not isinstance(path, str) or not path.strip() or len(path) > 4096 or '\x00' in path:
            raise ValueError('Each project path must be a nonempty string.')
        path = path.strip()
        if not (path.startswith('~/') or Path(path).is_absolute() or re.match(r'^[A-Za-z]:[\\/]', path) or path.startswith('\\\\')):
            raise ValueError('Project paths must be absolute (a leading ~ is also supported).')
        if path in result:
            raise ValueError('Duplicate project paths are not allowed.')
        result.append(path)
    return result


def check_paths(paths):
    """Try paths in order on this host. Foreign OS paths never become relative cwd."""
    checks = []
    selected = None
    for raw in paths:
        path = os.path.expanduser(raw)
        item = {'path': raw, 'status': 'missing', 'selected': False}
        if os.name != 'nt' and (re.match(r'^[A-Za-z]:', path) or path.startswith('\\\\')):
            item['status'] = 'different_os'
        elif not Path(path).is_absolute():
            item['status'] = 'invalid'
        else:
            try:
                info = os.stat(path)
                if not stat.S_ISDIR(info.st_mode):
                    item['status'] = 'not_directory'
                elif not os.access(path, os.R_OK | os.X_OK):
                    item['status'] = 'permission_denied'
                else:
                    item['status'] = 'available'
                    if selected is None:
                        selected = str(Path(path).resolve())
                        item['selected'] = True
            except PermissionError:
                item['status'] = 'permission_denied'
            except FileNotFoundError:
                pass
            except OSError:
                item['status'] = 'unreachable'
        checks.append(item)
    return {'resolved_path': selected, 'available': selected is not None, 'path_checks': checks}


def require_project_path(project):
    """Legacy/fake records lack health fields; real registry records fail closed."""
    if project.get('archived') or project.get('available') is False:
        raise ProjectUnavailable(f'Project "{project.get("name", "Unknown")}" is unavailable. Repair its paths in Apps → Projects.')
    return str(project.get('path') or '').strip()

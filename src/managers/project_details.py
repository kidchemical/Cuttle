"""Project App summaries. Registry state remains owned by ProjectManager."""
import os
import subprocess
from pathlib import Path


CONFIG_KINDS = ('commands', 'rules', 'actions', 'agents', 'docs', 'scripts', 'skills', 'memory')


def config_inventory(project):
    """Show project and shared layers without reading secrets or executing config."""
    root = Path(project['resolved_path']) if project.get('available') else None
    global_root = Path(__file__).resolve().parents[2] / '.cuttle_global'
    layers = [('Shared', global_root), ('Shared personal', global_root / 'personal')]
    if root:
        layers += [('Project', root / '.cuttle'), ('Project personal', root / '.cuttle' / 'personal')]
    result = []
    for label, base in layers:
        files = []
        if base.is_dir():
            for kind in CONFIG_KINDS:
                folder = base / kind
                if folder.is_symlink() or not folder.is_dir():
                    continue
                for path in sorted(folder.rglob('*')):
                    if len(files) >= 250:
                        break
                    rel = path.relative_to(base)
                    if any(part.startswith('.') or part in ('secrets', '__pycache__', 'node_modules') for part in rel.parts):
                        continue
                    # Never follow a file or intermediate symlink beyond its layer.
                    if path.is_file() and path.resolve().is_relative_to(base.resolve()):
                        files.append(str(rel))
            ini = base / 'GLOBAL.ini'
            if ini.is_file() and not ini.is_symlink():
                files.insert(0, 'GLOBAL.ini')
        result.append({'label': label, 'path': str(base), 'present': base.is_dir(), 'files': files})
    return result


def repository_summary(project):
    if not project.get('available'):
        return {'available': False}
    env = dict(os.environ, GIT_OPTIONAL_LOCKS='0', GIT_TERMINAL_PROMPT='0')
    def git(*args):
        result = subprocess.run(['git', '-C', project['resolved_path'], *args],
                                capture_output=True, text=True, timeout=4, env=env)
        return result.stdout.strip() if result.returncode == 0 else None
    try:
        root = git('rev-parse', '--show-toplevel')
        if not root:
            return {'available': False}
        branch = git('branch', '--show-current') or '(detached HEAD)'
        branches = (git('for-each-ref', '--format=%(refname:short)', 'refs/heads') or '').splitlines()
        remote = git('remote', 'get-url', 'origin') or ''
        # Remove URL userinfo so tokens in remotes are never sent to the UI.
        from urllib.parse import urlsplit, urlunsplit
        if '://' in remote:
            parsed = urlsplit(remote)
            remote = urlunsplit((parsed.scheme, parsed.netloc.rsplit('@', 1)[-1], parsed.path, '', ''))
        return {'available': True, 'root': root, 'branch': branch,
                'branches': branches[:100], 'branch_count': len(branches), 'remote': remote}
    except (OSError, subprocess.TimeoutExpired):
        return {'available': False, 'error': 'Git information unavailable or timed out.'}

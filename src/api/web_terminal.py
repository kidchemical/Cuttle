"""
Web terminal — PTY sessions over WebSocket for Cuttle's browser UI.

Localhost-only: full shell access is never exposed on LAN, even when lan_access_enabled.

PTY processes outlive a single WebSocket so Electron/browser refresh can reattach
to the same shell (cwd, scrollback, running commands) via the CH- session id.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from flask import Flask, jsonify, request

try:
    from flask_sock import Sock
except ImportError:
    Sock = None  # type: ignore[misc, assignment]

# Project roots (match web_chat_api.py)
_API_DIR = Path(__file__).resolve().parent
_SRC_ROOT = _API_DIR.parent
_REPO_ROOT = _SRC_ROOT.parent
try:
    from core.runtime_paths import venv_bin_dir, venv_python
except ImportError:  # pragma: no cover
    venv_bin_dir = None  # type: ignore[assignment]
    venv_python = None  # type: ignore[assignment]

_VENV_SCRIPTS = (
    venv_bin_dir(_REPO_ROOT)
    if callable(venv_bin_dir)
    else (_REPO_ROOT / '.venv' / ('Scripts' if sys.platform == 'win32' else 'bin'))
)

# Detached PTYs are kept so refresh/reconnect can resume the same shell.
_PTY_REGISTRY: Dict[str, '_PtySession'] = {}
_PTY_LOCK = threading.Lock()
_PTY_IDLE_TTL_SEC = 30 * 60
_SCROLLBACK_MAX_BYTES = 256 * 1024


def _is_windows() -> bool:
    return sys.platform == 'win32'


def _is_local_request() -> bool:
    """Only allow terminal from the machine running Cuttle."""
    addr = (request.remote_addr or '').strip()
    if addr in ('127.0.0.1', '::1', 'localhost'):
        return True
    # Werkzeug may report IPv4-mapped IPv6
    if addr.startswith('::ffff:127.'):
        return True
    # Same PC opened via LAN IP (e.g. https://192.168.x.x:8080) — still local.
    try:
        from api.lan_access import get_lan_ipv4

        lan_ip = get_lan_ipv4()
        if lan_ip and addr == lan_ip:
            return True
    except Exception:
        pass
    return False


def _terminal_unavailable_payload() -> Dict[str, Any]:
    return {
        'success': False,
        'error': 'Terminal is only available on this PC (localhost).',
        'hint': 'Open Cuttle on the machine running the daemon, not from a phone or another computer.',
        'localhost_url': 'https://127.0.0.1:8080/terminal_page.html',
    }


def _pwsh_or_powershell() -> Optional[List[str]]:
    names = (
        ('pwsh.exe', 'powershell.exe')
        if _is_windows()
        else ('pwsh', 'powershell', 'pwsh.exe', 'powershell.exe')
    )
    for exe in names:
        path = shutil.which(exe)
        if path:
            return [path, '-NoLogo']
    if _is_windows():
        return ['powershell.exe', '-NoLogo']
    return None


def _venv_python_path() -> str:
    if callable(venv_python):
        return str(venv_python(_REPO_ROOT))
    candidate = _VENV_SCRIPTS / ('python.exe' if _is_windows() else 'python3')
    if candidate.is_file():
        return str(candidate)
    return shutil.which('python3') or shutil.which('python') or sys.executable


def _cuttle_venv_env() -> Dict[str, str]:
    env = os.environ.copy()
    venv = _REPO_ROOT / '.venv'
    scripts = _VENV_SCRIPTS
    if scripts.is_dir():
        env['VIRTUAL_ENV'] = str(venv)
        env['PATH'] = str(scripts) + os.pathsep + env.get('PATH', '')
    env['CUTTLE_ROOT'] = str(_REPO_ROOT)
    return env


def _hermes_command_and_env() -> Tuple[Optional[List[str]], Dict[str, str]]:
    try:
        from scripts.utilities.hermes_cli_tool import _hermes_subprocess_env, hermes_executable

        exe = hermes_executable()
        if not exe:
            return None, {}
        return [exe], _hermes_subprocess_env()
    except Exception:
        return None, {}


def _claude_command() -> Optional[List[str]]:
    if shutil.which('claude'):
        return ['claude']
    if _is_windows() and shutil.which('wsl'):
        return ['wsl', 'claude']
    return None


def _wsl_command() -> Optional[List[str]]:
    if _is_windows() and shutil.which('wsl'):
        return ['wsl', '-d', 'Ubuntu', 'bash', '-l']
    if not _is_windows():
        return [os.environ.get('SHELL') or '/bin/bash', '-l']
    return None


def _resolve_shell_preset(shell_id: str) -> Tuple[Optional[List[str]], str, Dict[str, str]]:
    """Return (argv, cwd, env) for a shell preset id."""
    cwd = str(_REPO_ROOT)
    env: Dict[str, str] = os.environ.copy()

    def _hermes_preset() -> Tuple[Optional[List[str]], str, Dict[str, str]]:
        cmd, hm_env = _hermes_command_and_env()
        return cmd, cwd, hm_env

    presets: Dict[str, Callable[[], Tuple[Optional[List[str]], str, Dict[str, str]]]] = {
        'powershell': lambda: (_pwsh_or_powershell(), cwd, {**env, 'TERM': 'xterm-256color'}),
        'cmd': lambda: (['cmd.exe'] if _is_windows() else None, cwd, env),
        'bash': lambda: ([os.environ.get('SHELL') or '/bin/bash', '-l'], cwd, {**env, 'TERM': 'xterm-256color'}),
        'cuttle': lambda: (
            (_pwsh_or_powershell() if _is_windows() else [os.environ.get('SHELL') or '/bin/bash', '-l']),
            cwd,
            {**_cuttle_venv_env(), 'TERM': 'xterm-256color'},
        ),
        'python': lambda: (
            [_venv_python_path(), '-i'],
            cwd,
            _cuttle_venv_env(),
        ),
        'hermes': _hermes_preset,
        'claude': lambda: (_claude_command(), cwd, env),
        'wsl': lambda: (_wsl_command(), cwd, env),
    }

    default_id = 'powershell' if _is_windows() else 'bash'
    factory = presets.get(shell_id, presets[default_id])
    argv, workdir, shell_env = factory()
    return argv, workdir, shell_env


def list_shell_presets() -> List[Dict[str, Any]]:
    """Shell types exposed to the UI."""
    items = [
        {'id': 'bash', 'label': 'Bash', 'icon': '🐧', 'description': 'Login shell'},
        {'id': 'powershell', 'label': 'PowerShell', 'icon': '⚡', 'description': 'Windows PowerShell / pwsh'},
        {'id': 'cmd', 'label': 'Command Prompt', 'icon': '⌨️', 'description': 'cmd.exe'},
        {'id': 'cuttle', 'label': 'Cuttle (venv)', 'icon': '🦑', 'description': 'Project root with .venv activated'},
        {'id': 'python', 'label': 'Python REPL', 'icon': '🐍', 'description': 'Cuttle venv Python interactive shell'},
        {'id': 'hermes', 'label': 'Hermes CLI', 'icon': '🤖', 'description': 'Hermes Agent interactive mode'},
        {'id': 'claude', 'label': 'Claude Code', 'icon': '🧠', 'description': 'Anthropic Claude Code CLI'},
        {'id': 'wsl', 'label': 'WSL Bash', 'icon': '🐧', 'description': 'Linux shell via WSL'},
    ]
    if _is_windows():
        items = [item for item in items if item['id'] != 'bash'] + [
            item for item in items if item['id'] == 'bash'
        ]
    out: List[Dict[str, Any]] = []
    for item in items:
        argv, _, _ = _resolve_shell_preset(item['id'])
        out.append({**item, 'available': bool(argv)})
    return out


class _PtySession:
    """One interactive PTY that can detach/reattach across WebSocket refreshes."""

    def __init__(self, session_id: str, shell_id: str, cols: int, rows: int):
        self.session_id = session_id
        self.shell_id = shell_id
        self.cols = max(20, cols)
        self.rows = max(5, rows)
        self._pty: Any = None
        self._pty_master: Optional[int] = None
        self._reader: Optional[threading.Thread] = None
        self._cb_lock = threading.Lock()
        self._closed = threading.Event()
        self._on_output: Optional[Callable[[bytes], None]] = None
        self._on_exit: Optional[Callable[[int], None]] = None
        self._scrollback = bytearray()
        self._scrollback_lock = threading.Lock()
        self.last_active = time.time()
        self.exit_code: Optional[int] = None

    def touch(self) -> None:
        self.last_active = time.time()

    def is_alive(self) -> bool:
        if self._closed.is_set() or not self._pty:
            return False
        try:
            if _is_windows():
                return bool(self._pty.isalive())
            return self._pty.poll() is None
        except Exception:
            return False

    def set_callbacks(
        self,
        on_output: Optional[Callable[[bytes], None]],
        on_exit: Optional[Callable[[int], None]],
    ) -> None:
        with self._cb_lock:
            self._on_output = on_output
            self._on_exit = on_exit

    def detach(self) -> None:
        """Keep the PTY running but stop pushing to a dead WebSocket."""
        self.set_callbacks(None, None)
        self.touch()

    def _append_scrollback(self, chunk: bytes) -> None:
        if not chunk:
            return
        with self._scrollback_lock:
            self._scrollback.extend(chunk)
            overflow = len(self._scrollback) - _SCROLLBACK_MAX_BYTES
            if overflow > 0:
                del self._scrollback[:overflow]

    def take_scrollback_b64(self) -> str:
        with self._scrollback_lock:
            if not self._scrollback:
                return ''
            return base64.b64encode(bytes(self._scrollback)).decode('ascii')

    def start(self) -> Optional[str]:
        argv, cwd, env = _resolve_shell_preset(self.shell_id)
        if not argv:
            return f'Shell "{self.shell_id}" is not available on this machine.'

        try:
            if _is_windows():
                from winpty import PtyProcess

                cmdline = subprocess_argv_to_str(argv) if len(argv) > 1 else argv[0]
                self._pty = PtyProcess.spawn(
                    cmdline,
                    cwd=cwd,
                    env=env,
                    dimensions=(self.cols, self.rows),
                )
            else:
                import pty
                import subprocess

                master, slave = pty.openpty()
                self._pty = subprocess.Popen(
                    argv,
                    stdin=slave,
                    stdout=slave,
                    stderr=slave,
                    cwd=cwd,
                    env=env,
                    start_new_session=True,
                )
                os.close(slave)
                self._pty_master = master
        except Exception as exc:
            return str(exc)

        self.touch()
        self._reader = threading.Thread(target=self._read_loop, name=f'pty-{self.session_id}', daemon=True)
        self._reader.start()
        return None

    def _emit_output(self, chunk: bytes) -> None:
        self._append_scrollback(chunk)
        self.touch()
        with self._cb_lock:
            cb = self._on_output
        if cb:
            try:
                cb(chunk)
            except Exception:
                pass

    def _emit_exit(self, code: int) -> None:
        self.exit_code = int(code or 0)
        with self._cb_lock:
            cb = self._on_exit
        if cb:
            try:
                cb(self.exit_code)
            except Exception:
                pass

    def _read_loop(self) -> None:
        exit_code = 0
        try:
            while not self._closed.is_set():
                if _is_windows():
                    if not self._pty or not self._pty.isalive():
                        break
                    try:
                        chunk = self._pty.read(4096)
                    except EOFError:
                        break
                    except Exception:
                        break
                else:
                    import select

                    if not self._pty or self._pty.poll() is not None:
                        break
                    r, _, _ = select.select([self._pty_master], [], [], 0.1)
                    if not r:
                        continue
                    chunk = os.read(self._pty_master, 4096)
                if chunk:
                    raw = chunk if isinstance(chunk, bytes) else chunk.encode('utf-8', 'replace')
                    self._emit_output(raw)
                else:
                    time.sleep(0.02)
        finally:
            if self._pty:
                try:
                    if _is_windows():
                        exit_code = self._pty.exitstatus or 0
                    else:
                        exit_code = self._pty.wait(timeout=0.5)
                except Exception:
                    exit_code = 1
            self._emit_exit(int(exit_code or 0))

    def write(self, data: str) -> None:
        if self._closed.is_set() or not self._pty:
            return
        self.touch()
        try:
            if _is_windows():
                self._pty.write(data)
            else:
                os.write(self._pty_master, data.encode('utf-8', 'replace'))
        except Exception:
            self.close()

    def resize(self, cols: int, rows: int) -> None:
        self.cols = max(20, cols)
        self.rows = max(5, rows)
        self.touch()
        if not self._pty:
            return
        try:
            if _is_windows() and hasattr(self._pty, 'set_size'):
                self._pty.set_size(self.cols, self.rows)
            elif not _is_windows() and self._pty_master is not None:
                import struct
                import fcntl
                import termios

                winsize = struct.pack('HHHH', rows, cols, 0, 0)
                fcntl.ioctl(self._pty_master, termios.TIOCSWINSZ, winsize)
        except Exception:
            pass

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        try:
            if self._pty:
                if _is_windows():
                    if self._pty.isalive():
                        self._pty.close()
                else:
                    self._pty.terminate()
                    try:
                        if self._pty_master is not None:
                            os.close(self._pty_master)
                    except Exception:
                        pass
        except Exception:
            pass


def subprocess_argv_to_str(argv: List[str]) -> str:
    """Quote argv for Windows PTY spawn."""
    parts: List[str] = []
    for arg in argv:
        if not arg:
            parts.append('""')
        elif any(c in arg for c in ' \t"&|<>^'):
            parts.append('"' + arg.replace('"', '\\"') + '"')
        else:
            parts.append(arg)
    return ' '.join(parts)


def _ws_send(ws: Any, payload: Dict[str, Any]) -> None:
    try:
        ws.send(json.dumps(payload))
    except Exception:
        pass


def _registry_put(session: _PtySession) -> None:
    with _PTY_LOCK:
        _PTY_REGISTRY[session.session_id] = session


def _registry_get(session_id: str) -> Optional[_PtySession]:
    with _PTY_LOCK:
        return _PTY_REGISTRY.get(session_id)


def _registry_pop(session_id: str) -> Optional[_PtySession]:
    with _PTY_LOCK:
        return _PTY_REGISTRY.pop(session_id, None)


def _gc_idle_ptys() -> None:
    """Drop detached PTYs that have been idle too long (or already exited)."""
    now = time.time()
    to_close: List[_PtySession] = []
    with _PTY_LOCK:
        for sid, sess in list(_PTY_REGISTRY.items()):
            try:
                idle = now - float(sess.last_active or 0)
            except (TypeError, ValueError):
                idle = 0.0
            if not sess.is_alive() or idle >= _PTY_IDLE_TTL_SEC:
                _PTY_REGISTRY.pop(sid, None)
                to_close.append(sess)
    for sess in to_close:
        try:
            sess.close()
        except Exception:
            pass


def _destroy_session(session_id: str) -> None:
    sess = _registry_pop(session_id)
    if sess:
        sess.detach()
        sess.close()


def _attach_or_create_session(
    session_id: str,
    shell_id: str,
    cols: int,
    rows: int,
    on_output: Callable[[bytes], None],
    on_exit: Callable[[int], None],
) -> Tuple[Optional[_PtySession], bool, Optional[str]]:
    """
    Return (session, resumed, error).
    resumed=True when reattaching to a live PTY for the same shell id.
    """
    _gc_idle_ptys()
    existing = _registry_get(session_id)
    if existing:
        if existing.is_alive() and existing.shell_id == shell_id:
            existing.resize(cols, rows)
            existing.set_callbacks(on_output, on_exit)
            existing.touch()
            return existing, True, None
        # Shell switched, or process died — replace.
        _destroy_session(session_id)

    session = _PtySession(session_id, shell_id, cols, rows)
    session.set_callbacks(on_output, on_exit)
    err = session.start()
    if err:
        session.close()
        return None, False, err
    _registry_put(session)
    return session, False, None


def _handle_ws_connection(ws: Any) -> None:
    if not _is_local_request():
        payload = _terminal_unavailable_payload()
        _ws_send(ws, {
            'op': 'error',
            'message': payload['error'],
            'hint': payload.get('hint'),
            'localhost_url': payload.get('localhost_url'),
        })
        return

    session: Optional[_PtySession] = None
    attached_id: Optional[str] = None

    def on_output(chunk: bytes) -> None:
        _ws_send(ws, {'op': 'output', 'data': base64.b64encode(chunk).decode('ascii')})

    def on_exit(code: int) -> None:
        _ws_send(ws, {'op': 'exit', 'code': code})
        # Process ended — drop from registry so a later init starts fresh.
        if attached_id:
            popped = _registry_pop(attached_id)
            if popped is not None and popped is session:
                pass

    try:
        while True:
            raw = ws.receive()
            if raw is None:
                break
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            op = msg.get('op')
            if op == 'init':
                shell_id = str(msg.get('shell') or 'powershell')
                cols = int(msg.get('cols') or 80)
                rows = int(msg.get('rows') or 24)
                session_id = str(msg.get('session_id') or uuid.uuid4().hex[:12])

                # Switching attachment on this socket: detach previous handle first.
                if attached_id and attached_id != session_id and session:
                    session.detach()
                    session = None
                    attached_id = None

                session, resumed, err = _attach_or_create_session(
                    session_id, shell_id, cols, rows, on_output, on_exit,
                )
                if err or not session:
                    _ws_send(ws, {'op': 'error', 'message': err or 'Failed to start shell'})
                    session = None
                    attached_id = None
                else:
                    attached_id = session_id
                    ready: Dict[str, Any] = {
                        'op': 'ready',
                        'session_id': session_id,
                        'shell': shell_id,
                        'resumed': resumed,
                    }
                    if resumed:
                        sb = session.take_scrollback_b64()
                        if sb:
                            ready['scrollback'] = sb
                    _ws_send(ws, ready)

            elif op == 'close':
                sid = str(msg.get('session_id') or attached_id or '')
                if sid:
                    _destroy_session(sid)
                if session and session.session_id == sid:
                    session = None
                    attached_id = None
                _ws_send(ws, {'op': 'closed', 'session_id': sid})

            elif op == 'input' and session:
                data = msg.get('data')
                if isinstance(data, str):
                    if msg.get('encoding') == 'base64':
                        try:
                            data = base64.b64decode(data).decode('utf-8', 'replace')
                        except Exception:
                            continue
                    session.write(data)

            elif op == 'resize' and session:
                session.resize(int(msg.get('cols') or 80), int(msg.get('rows') or 24))

            elif op == 'ping':
                _ws_send(ws, {'op': 'pong'})
    finally:
        # Refresh / navigation: keep the PTY alive for reattach.
        if session:
            session.detach()


def register_terminal_routes(app: Flask) -> None:
    """Attach terminal HTTP + WebSocket routes to the Flask app."""
    from flask import send_from_directory

    @app.route('/terminal_page.html')
    def serve_terminal_page():
        return send_from_directory(_SRC_ROOT / 'web', 'terminal_page.html')

    @app.route('/api/terminal/status', methods=['GET'])
    def api_terminal_status():
        if not _is_local_request():
            return jsonify(_terminal_unavailable_payload()), 403
        return jsonify({
            'success': True,
            'available': True,
            'localhost_url': 'https://127.0.0.1:8080/terminal_page.html',
        })

    @app.route('/api/terminal/shells', methods=['GET'])
    def api_terminal_shells():
        if not _is_local_request():
            return jsonify({**_terminal_unavailable_payload(), 'shells': []}), 403
        return jsonify({'success': True, 'shells': list_shell_presets()})

    if Sock is None:
        print('[TERMINAL] flask-sock not installed — web terminal disabled')
        return

    sock = Sock(app)

    @sock.route('/api/terminal/ws')
    def terminal_ws(ws):
        _handle_ws_connection(ws)

    print('[TERMINAL] Web terminal registered (localhost-only, PTY resume enabled)')

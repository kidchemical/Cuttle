#!/usr/bin/env python3
"""
Cuttle Daemon - Process manager for hot-swap and restarts.

Manages the Flask web server (and on-demand llama.cpp). Discord is **not**
a daemon child: optional REST agent-ops (``python -m api.discord_cli``,
``discord.post``) use a token without an inbound gateway.
LOCAL_LLM_BACKEND=llamacpp is NOT started at boot: it launches on demand when a
Local-mode chat asks for it (Yes/No prompt), via POST /api/local-llm/start, or
from the tray menu — and then stays running until stopped. Supports:
- Start/stop/restart services
- Pipeline hot-reload (reload running pipelines from disk)
- Cron scheduler (fires trigger-schedule nodes in running pipelines)
- Gitea @cuttle remote jobs (claim loop against the Cuttle Jobs API)
- System tray icon (right-click → Open, Node Editor, Restart, Reload, Exit)
"""
import os
import sys
import signal
import ssl
import subprocess
import threading
import time
import json
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Tuple


def _local_ssl_ctx() -> ssl.SSLContext:
    """Return an SSL context that trusts the self-signed localhost cert.

    Used for all internal daemon → Flask urllib calls so they can speak HTTPS
    without triggering certificate verification errors.
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


_SSL_CTX = _local_ssl_ctx()


def _flask_url(path: str) -> str:
    """Build https URL for daemon → Flask internal API calls."""
    if not path.startswith('/'):
        path = '/' + path
    return f"https://127.0.0.1:{FLASK_PORT}{path}"


def _urlopen_flask(req, timeout: float = 5):
    """urllib.request.urlopen against Flask with self-signed cert verification disabled."""
    import urllib.request
    return urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX)

# Project root (parent of src)
SRC_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = SRC_ROOT.parent
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

# Load src/.env so API keys and optional REST tokens (e.g. DISCORD_TOKEN for agent-ops) are available
_env_file = SRC_ROOT / ".env"
if _env_file.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_file)
    except ImportError:
        # Fallback: parse .env manually
        with open(_env_file) as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith('#') and '=' in _line:
                    _k, _, _v = _line.partition('=')
                    os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

# Electron executable (for tray "Open Cuttle" action + auto-open on boot)
try:
    from core.runtime_paths import electron_packaged_exe

    ELECTRON_EXE = electron_packaged_exe(PROJECT_ROOT) or (
        PROJECT_ROOT / "electron" / "dist" / "win-unpacked" / "Cuttle.exe"
    )
except Exception:
    ELECTRON_EXE = PROJECT_ROOT / "electron" / "dist" / "win-unpacked" / "Cuttle.exe"

# ANSI console colors (Windows VT enabled in run_daemon)
_ANSI_RESET = "\033[0m"
_ANSI = {
    "error": "\033[91m",    # bright red
    "warn": "\033[93m",     # yellow
    "ok": "\033[92m",       # green
    "info": "\033[96m",     # cyan
    "note": "\033[95m",     # magenta
    "dim": "\033[90m",      # gray
}

# Notify queue for tray toasts (Flask appends, daemon reads)
NOTIFY_QUEUE_PATH = PROJECT_ROOT / "cuttle_notify_queue.jsonl"

# Graceful Flask restart IPC (Flask writes request; daemon owns stop/start/health)
FLASK_RESTART_REQUEST_PATH = PROJECT_ROOT / "cuttle_flask_restart_request.json"
FLASK_RESTART_STATUS_PATH = PROJECT_ROOT / "cuttle_flask_restart_status.json"

# Daemon/Flask logs (in user home for easy access, gitignore-safe)
LOGS_DIR = Path.home() / "cuttle_logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# Process tracking
processes: Dict[str, subprocess.Popen] = {}
_process_log_handles: List = []  # Keep log file handles open for child processes
daemon_running = True
FLASK_PORT = 8080
tray_icon = None  # Set by setup_tray, used by Exit handler
_flask_restart_lock = threading.Lock()
_flask_generation = 0
_flask_restart_in_progress = False


def get_python_cmd() -> str:
    """Get Python executable (venv preferred, Windows or POSIX)."""
    try:
        from core.runtime_paths import venv_python

        return str(venv_python(PROJECT_ROOT))
    except Exception:
        if sys.platform == "win32":
            venv_py = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
        else:
            venv_py = PROJECT_ROOT / ".venv" / "bin" / "python3"
        if venv_py.exists():
            return str(venv_py)
        return sys.executable


def _enable_windows_ansi() -> None:
    """Turn on VT processing so the daemon console can show colored logs."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        for std_id in (-11, -12):  # STD_OUTPUT_HANDLE, STD_ERROR_HANDLE
            handle = kernel32.GetStdHandle(std_id)
            mode = ctypes.c_uint()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        kernel32.SetConsoleTitleW("Cuttle daemon")
    except Exception:
        pass


def stamp_daemon_line(line: str, now: Optional[datetime] = None) -> str:
    """Prefix a log line with [HH:MM:SS]. Blank lines and already-stamped lines are unchanged."""
    if not line:
        return line
    if line.startswith("[") and len(line) >= 10 and line[1:3].isdigit() and line[3] == ":" and line[9:11] == "] ":
        return line
    ts = (now or datetime.now()).strftime("%H:%M:%S")
    return f"[{ts}] {line}"


def colorize_daemon_line(line: str) -> str:
    """Color a daemon log line by status/severity. Safe for plain (no ANSI) logs."""
    if not line or "\033[" in line:
        return line
    low = line.lower()
    if any(k in low for k in ("error", "failed", "exception", "traceback", "unresponsive")):
        kind = "error"
    elif any(k in low for k in ("warning", "conflict", "not found", "skipping", "not set")):
        kind = "warn"
    elif any(k in low for k in ("shutting", "stopping", "exited")):
        kind = "note"
    elif any(k in low for k in ("restarting", "reloading", "reload ")):
        kind = "warn"
    elif any(
        k in low
        for k in (
            "started",
            "starting",
            "ready",
            "registered",
            "already running",
            "phone portal",
            "opening electron",
            " ✓",
        )
    ):
        kind = "ok"
    elif "[DAEMON]" in line:
        kind = "info"
    else:
        kind = "dim"
    return f"{_ANSI[kind]}{line}{_ANSI_RESET}"


def _env_flag_off(name: str) -> bool:
    flag = (os.environ.get(name) or "").strip().lower()
    return flag in ("1", "true", "yes", "on")


def _should_open_ui() -> bool:
    if "--no-ui" in sys.argv:
        return False
    return not _env_flag_off("CUTTLE_NO_UI")


def _should_show_tray() -> bool:
    """Skip pystray when Electron already owns the tray (Host/Client launcher)."""
    if "--no-tray" in sys.argv:
        return False
    return not _env_flag_off("CUTTLE_NO_TRAY")


def _flask_port_open() -> bool:
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.4)
    try:
        sock.connect(("127.0.0.1", FLASK_PORT))
        return True
    except OSError:
        return False
    finally:
        try:
            sock.close()
        except OSError:
            pass


def wait_for_flask_ready(timeout_s: float = 45.0) -> bool:
    """Wait until Flask is accepting TCP on :8080 (HTTPS listener)."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if _flask_port_open():
            return True
        time.sleep(0.3)
    return False


def _popen_gui(argv: List[str], cwd: str, env: Optional[dict] = None) -> subprocess.Popen:
    """Launch a GUI app without attaching a second console to the daemon."""
    kwargs: dict = {"cwd": cwd, "env": env}
    if sys.platform == "win32":
        # Do not inherit the daemon console. Do not use STARTF_USESHOWWINDOW /
        # CREATE_NO_WINDOW — those can hide Cuttle.exe itself.
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
        kwargs["close_fds"] = True
        kwargs["stdin"] = subprocess.DEVNULL
        kwargs["stdout"] = subprocess.DEVNULL
        kwargs["stderr"] = subprocess.DEVNULL
    else:
        kwargs["start_new_session"] = True
        kwargs["stdin"] = subprocess.DEVNULL
        kwargs["stdout"] = subprocess.DEVNULL
        kwargs["stderr"] = subprocess.DEVNULL
    return subprocess.Popen(argv, **kwargs)


def _electron_package_version() -> str:
    """Semver from electron/package.json (checkout), not the frozen app.asar copy."""
    pkg = PROJECT_ROOT / "electron" / "package.json"
    try:
        data = json.loads(pkg.read_text(encoding="utf-8"))
        ver = str(data.get("version") or "").strip()
        return ver or ""
    except Exception:
        return ""


def open_cuttle_ui(extra_args: Optional[List[str]] = None, browser_path: str = "/app_shell.html") -> bool:
    """Open the Electron app (or browser fallback). Returns True if Electron was spawned."""
    env = os.environ.copy()
    # Electron must not spawn a second cuttle_daemon.py (blank console, cwd=src/.env).
    env["CUTTLE_HOSTED_BY_DAEMON"] = "1"
    # Packaged Cuttle.exe bakes package.json into app.asar at build time. Stamp the
    # live checkout version so titlebar / worker ads match mesh bumps.
    live_ver = _electron_package_version()
    if live_ver:
        env["CUTTLE_PACKAGE_VERSION"] = live_ver
    try:
        from core.runtime_paths import electron_launch_argv

        launch = electron_launch_argv(PROJECT_ROOT)
    except Exception:
        launch = [str(ELECTRON_EXE)] if ELECTRON_EXE.exists() else None
    if launch:
        args = list(launch)
        if extra_args:
            args.extend(extra_args)
        print(f"[DAEMON] Opening Electron app (version={live_ver or 'unknown'})")
        _popen_gui(args, cwd=str(PROJECT_ROOT), env=env)
        return True
    print("[DAEMON] Electron app not found — opening browser")
    webbrowser.open(_flask_url(browser_path))
    return False


def _warn_port_8080_conflicts() -> None:
    """Warn when something other than web_chat_api owns :8080 (common: Unity MCP default).

    On Windows, a process bound to 127.0.0.1:8080 steals localhost traffic from Flask's
    0.0.0.0:8080 HTTPS listener → ERR_SSL_PROTOCOL_ERROR / WRONG_VERSION_NUMBER loops.
    """
    if sys.platform != "win32":
        return
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue | "
                "ForEach-Object { $p = Get-CimInstance Win32_Process -Filter \"ProcessId=$($_.OwningProcess)\"; "
                "[PSCustomObject]@{Addr=$_.LocalAddress; PID=$_.OwningProcess; Cmd=$p.CommandLine} } | "
                "ConvertTo-Json -Compress",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        raw = (result.stdout or "").strip()
        if not raw:
            return
        import json as _json
        rows = _json.loads(raw)
        if isinstance(rows, dict):
            rows = [rows]
        for row in rows:
            cmd = (row.get("Cmd") or "") or ""
            if "web_chat_api" in cmd:
                continue
            addr = row.get("Addr", "?")
            pid = row.get("PID", "?")
            hint = "mcp-for-unity" if "mcp-for-unity" in cmd else "unknown"
            print(
                f"[DAEMON] WARNING: port 8080 conflict — {hint} PID {pid} listening on {addr}:8080. "
                "This steals https://127.0.0.1:8080 from Flask (blank Electron window / SSL errors). "
                "Move Unity MCP to http://127.0.0.1:8090/mcp (and update Cursor mcp.json)."
            )
    except Exception:
        pass


def start_flask() -> bool:
    """Start Flask web server."""
    global _flask_generation
    if "flask" in processes and processes["flask"].poll() is None:
        return True
    _warn_port_8080_conflicts()
    _reload_env_file()
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC_ROOT)
    env["FLASK_ENV"] = "production"
    _flask_generation += 1
    env["CUTTLE_FLASK_GENERATION"] = str(_flask_generation)
    log_path = LOGS_DIR / "flask.log"
    logf = open(log_path, "a", encoding="utf-8")
    _process_log_handles.append(logf)
    logf.write(
        f"\n--- Flask started {datetime.now().isoformat()} "
        f"generation={_flask_generation} ---\n"
    )
    logf.flush()
    proc = subprocess.Popen(
        [get_python_cmd(), str(SRC_ROOT / "api" / "web_chat_api.py")],
        cwd=str(SRC_ROOT),
        env=env,
        stdout=logf,
        stderr=subprocess.STDOUT,
    )
    processes["flask"] = proc
    return True


def _llamacpp_enabled() -> bool:
    try:
        from core.local_llm import is_llamacpp
        return is_llamacpp()
    except Exception:
        val = (os.getenv("LOCAL_LLM_BACKEND") or "ollama").strip().lower()
        return val in ("llamacpp", "llama.cpp", "llama_cpp", "llama-cpp", "llama", "llama-server", "llamaserver")


def _llamacpp_reachable(timeout: float = 1.5) -> bool:
    try:
        from core.local_llm import local_reachable
        return local_reachable(timeout=timeout)
    except Exception:
        return False


def start_llamacpp() -> bool:
    """Start llama-server if LOCAL_LLM_BACKEND=llamacpp and nothing is listening yet."""
    if not _llamacpp_enabled():
        return False
    if "llamacpp" in processes and processes["llamacpp"].poll() is None:
        return True
    if _llamacpp_reachable(timeout=1.5):
        print("[DAEMON] llama-server already running — skipping spawn")
        return True

    _reload_env_file()
    script = (os.getenv("LLAMACPP_START_SCRIPT") or r"F:\llama.cpp\start-qwen-coder.ps1").strip()
    script_path = Path(script)
    if not script_path.is_file():
        print(f"[DAEMON] llama-server start script not found: {script_path}")
        print("[DAEMON] Set LLAMACPP_START_SCRIPT in src/.env or start llama-server manually")
        return False

    log_path = LOGS_DIR / "llamacpp.log"
    logf = open(log_path, "a", encoding="utf-8")
    _process_log_handles.append(logf)
    logf.write(f"\n--- llama-server started {datetime.now().isoformat()} ---\n")
    logf.flush()
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    suffix = script_path.suffix.lower()
    if suffix == ".ps1":
        argv = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
        ]
    else:
        argv = [str(script_path)] if os.access(script_path, os.X_OK) else ["bash", str(script_path)]
    proc = subprocess.Popen(
        argv,
        cwd=str(script_path.parent),
        stdout=logf,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )
    processes["llamacpp"] = proc
    print(f"[DAEMON] Starting llama-server (model load may take 1–2 min) — log: {log_path}")
    return True


def _reload_env_file() -> None:
    """Re-read src/.env so per-service restarts pick up env changes without daemon restart."""
    if not _env_file.exists():
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_file, override=True)
    except ImportError:
        with open(_env_file, encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _, _v = _line.partition("=")
                    os.environ[_k.strip()] = _v.strip().strip('"').strip("'")


def _kill_process_tree(pid: int) -> None:
    """Terminate a process and its children (Flask launcher+child, llama-server)."""
    try:
        from api.process_kill_safety import may_kill_pid
    except Exception:
        may_kill_pid = None  # type: ignore[assignment]
    if pid <= 1 or pid == os.getpid() or pid == os.getppid():
        return
    if may_kill_pid is not None and not may_kill_pid(pid):
        return
    try:
        if sys.platform == "win32":
            # Daemon-owned kill: does not go through Cursor hooks or shell_manager.
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                timeout=20,
            )
        else:
            try:
                import psutil

                parent = psutil.Process(pid)
                children = [
                    c
                    for c in parent.children(recursive=True)
                    if int(c.pid) > 1 and int(c.pid) != os.getpid()
                ]
                parent.terminate()
                for child in children:
                    try:
                        child.terminate()
                    except Exception:
                        pass
                gone, alive = psutil.wait_procs([parent, *children], timeout=8)
                for leftover in alive:
                    try:
                        leftover.kill()
                    except Exception:
                        pass
            except PermissionError:
                return
            except Exception:
                try:
                    os.kill(pid, signal.SIGTERM)
                except PermissionError:
                    return
    except Exception:
        pass


def _kill_processes_by_cmdline(fragment: str) -> None:
    """Kill processes whose command line contains fragment (Flask orphans, etc.)."""
    if not fragment:
        return
    if sys.platform == "win32":
        frag = fragment.replace("'", "''")
        try:
            subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    f"Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                    f"Where-Object {{ $_.CommandLine -like '*{frag}*' }} | "
                    f"ForEach-Object {{ taskkill /F /T /PID $_.ProcessId 2>$null }}",
                ],
                capture_output=True,
                timeout=30,
            )
        except Exception:
            pass
        return
    try:
        import psutil

        me = os.getpid()
        for proc in psutil.process_iter(["pid", "cmdline"]):
            pid = proc.info.get("pid")
            if not pid or pid == me or pid <= 1:
                continue
            try:
                from api.process_kill_safety import may_kill_pid

                if not may_kill_pid(int(pid)):
                    continue
            except Exception:
                continue
            cmd = " ".join(proc.info.get("cmdline") or [])
            if fragment in cmd:
                try:
                    _kill_process_tree(int(pid))
                except Exception:
                    pass
    except Exception:
        pass


def _kill_llama_server_processes() -> None:
    """Stop llama-server so a managed restart can bind port 8081 again."""
    if sys.platform == "win32":
        try:
            subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "Get-Process -Name 'llama-server' -ErrorAction SilentlyContinue | Stop-Process -Force",
                ],
                capture_output=True,
                timeout=15,
            )
        except Exception:
            pass
        return
    try:
        subprocess.run(["pkill", "-f", "llama-server"], capture_output=True, timeout=15)
    except Exception:
        pass


def stop_process(name: str) -> bool:
    """Stop a managed process (and its child tree on Windows)."""
    if name in processes:
        proc = processes[name]
        pid = proc.pid
        try:
            _kill_process_tree(pid)
            proc.wait(timeout=8)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        del processes[name]
        if name == "flask":
            _kill_processes_by_cmdline("web_chat_api")
        elif name == "llamacpp":
            _kill_llama_server_processes()
        time.sleep(0.5)
        return True
    return False


def stop_all_processes():
    """Stop all managed processes (Flask, llama-server)."""
    for name in list(processes.keys()):
        stop_process(name)


def restart_process(name: str) -> bool:
    """Restart a managed process."""
    if name == "flask":
        return perform_flask_restart(restart_id=None, mode="daemon", source="restart_process")
    if name == "llamacpp" and _llamacpp_enabled():
        stop_process(name)
        _kill_llama_server_processes()
        time.sleep(1.0)
        return start_llamacpp()
    stop_process(name)
    if name == "llamacpp":
        return start_llamacpp()
    return False


def _port_8080_listeners() -> List[int]:
    """PIDs listening on TCP 8080. Empty on failure."""
    pids: List[int] = []
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue | "
                    "Select-Object -ExpandProperty OwningProcess",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            for line in (result.stdout or "").splitlines():
                line = line.strip()
                if line.isdigit():
                    pids.append(int(line))
            return pids
        except Exception:
            return []
    try:
        import psutil

        listen = getattr(psutil, "CONN_LISTEN", "LISTEN")
        for conn in psutil.net_connections(kind="inet"):
            laddr = conn.laddr
            if not laddr or getattr(laddr, "port", None) != 8080:
                continue
            status = getattr(conn, "status", None)
            if status not in (listen, "LISTEN"):
                continue
            pid = conn.pid
            if pid:
                pids.append(int(pid))
        return sorted(set(pids))
    except Exception:
        pass
    try:
        result = subprocess.run(
            ["ss", "-lptn", "sport = :8080"],
            capture_output=True,
            text=True,
            timeout=8,
        )
        for line in (result.stdout or "").splitlines():
            if "pid=" not in line:
                continue
            for part in line.replace(",", " ").split():
                if part.startswith("pid="):
                    num = part.split("=", 1)[1].split(",")[0]
                    if num.isdigit():
                        pids.append(int(num))
        return sorted(set(pids))
    except Exception:
        return []


def _wait_port_free(timeout_sec: float = 20.0) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if not _port_8080_listeners():
            return True
        time.sleep(0.4)
    return not _port_8080_listeners()


def _flask_health_poll(timeout_sec: float = 45.0, interval: float = 1.0) -> Tuple[bool, Optional[float]]:
    """Poll /api/health until healthy or timeout. Returns (ok, elapsed_ms)."""
    started = time.time()
    deadline = started + timeout_sec
    while time.time() < deadline:
        if _flask_health_check():
            return True, (time.time() - started) * 1000.0
        time.sleep(interval)
    return False, (time.time() - started) * 1000.0


def _update_restart_status(patch: Dict) -> None:
    """Best-effort durable status update (shared file with Flask)."""
    try:
        from api import flask_restart as fr

        rid = patch.get("restart_id")
        state = patch.get("state")
        if rid and state:
            rest = {k: v for k, v in patch.items() if k not in ("restart_id", "state", "error")}
            fr.transition(
                str(rid),
                str(state),
                patch=rest,
                error=patch.get("error"),
            )
        elif FLASK_RESTART_STATUS_PATH.exists():
            import json as _json

            cur = {}
            try:
                with open(FLASK_RESTART_STATUS_PATH, "r", encoding="utf-8") as f:
                    cur = _json.load(f) or {}
            except Exception:
                cur = {}
            cur.update(patch)
            cur["updated_at"] = datetime.now().isoformat()
            tmp = FLASK_RESTART_STATUS_PATH.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                _json.dump(cur, f, indent=2)
            os.replace(tmp, FLASK_RESTART_STATUS_PATH)
    except Exception as e:
        print(f"[DAEMON] restart status update failed: {e}")


def perform_flask_restart(
    restart_id: Optional[str] = None,
    mode: str = "daemon",
    source: str = "daemon",
) -> bool:
    """
    Daemon-owned Flask replacement: stop → wait port → start one → health poll.

    Never reports success unless /api/status returns 200 (liveness).
    Do not use /api/health here — that route runs CLI/Ollama probes and can
    exceed the watchdog timeout while Flask is actually serving.
    """
    global _flask_restart_in_progress, _flask_generation
    if not _flask_restart_lock.acquire(blocking=False):
        print(f"[DAEMON] Flask restart already in progress — ignoring ({source})")
        return False
    _flask_restart_in_progress = True
    rid = restart_id or f"daemon-{int(time.time())}"
    old_pid = None
    try:
        if "flask" in processes and processes["flask"].poll() is None:
            old_pid = processes["flask"].pid
        print(f"[DAEMON] Flask restart begin id={rid} mode={mode} source={source} old_pid={old_pid}")
        _update_restart_status(
            {
                "restart_id": rid,
                "state": "stopping_old_flask",
                "old_flask_pid": old_pid,
                "mode": mode,
                "source": source,
            }
        )
        t0 = time.time()
        stop_process("flask")
        # Extra sweep in case launcher/child split left orphans
        _kill_processes_by_cmdline("web_chat_api")
        if not _wait_port_free(20.0):
            print("[DAEMON] WARNING: port 8080 still in use after stop")
        shutdown_ms = (time.time() - t0) * 1000.0

        _update_restart_status(
            {
                "restart_id": rid,
                "state": "starting_new_flask",
                "shutdown_ms": round(shutdown_ms, 1),
            }
        )
        if not start_flask():
            _update_restart_status(
                {
                    "restart_id": rid,
                    "state": "failed",
                    "error": "start_flask returned False",
                }
            )
            return False
        new_pid = processes.get("flask").pid if "flask" in processes else None

        _update_restart_status(
            {
                "restart_id": rid,
                "state": "health_checking",
                "new_flask_pid": new_pid,
                "generation": _flask_generation,
            }
        )
        ok, health_ms = _flask_health_poll(45.0, 1.0)
        if ok:
            _update_restart_status(
                {
                    "restart_id": rid,
                    "state": "healthy",
                    "new_flask_pid": new_pid,
                    "generation": _flask_generation,
                    "health_ms": round(health_ms or 0, 1),
                    "delivery": "persisted",
                    "error": None,
                }
            )
            print(
                f"[DAEMON] Flask restart healthy id={rid} "
                f"pid={new_pid} gen={_flask_generation} health_ms={health_ms:.0f}"
            )
            # Persist a post-restart completion into the requesting chat (once).
            try:
                _post_restart_chat_notice(rid)
            except Exception as e:
                print(f"[DAEMON] post-restart chat notice: {e}")
            return True

        _update_restart_status(
            {
                "restart_id": rid,
                "state": "timed_out",
                "new_flask_pid": new_pid,
                "generation": _flask_generation,
                "health_ms": round(health_ms or 0, 1),
                "error": "health check timed out",
            }
        )
        print(f"[DAEMON] Flask restart FAILED (health timeout) id={rid}")
        return False
    except Exception as e:
        _update_restart_status(
            {
                "restart_id": rid,
                "state": "failed",
                "error": str(e)[:400],
            }
        )
        print(f"[DAEMON] Flask restart failed: {e}")
        return False
    finally:
        _flask_restart_in_progress = False
        _flask_restart_lock.release()


def _post_restart_chat_notice(restart_id: str) -> None:
    """Ask the new Flask to append a completion message for the requesting session."""
    import urllib.request

    try:
        from api import flask_restart as fr

        status = fr.read_status()
        if str(status.get("restart_id")) != str(restart_id):
            return
        if status.get("post_restart_message_written"):
            return
        sid = status.get("session_id")
        if not sid:
            return
        body = json.dumps(
            {
                "restart_id": restart_id,
                "session_id": sid,
                "message": fr.build_completion_message(status),
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            _flask_url("/api/flask/restart/notify"),
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with _urlopen_flask(req, timeout=10) as resp:
            _ = resp.read()
    except Exception as e:
        print(f"[DAEMON] notify endpoint: {e}")


def watch_flask_restart_requests():
    """Poll for Flask-authored restart requests; daemon owns the replacement."""
    while daemon_running:
        try:
            if FLASK_RESTART_REQUEST_PATH.exists() and not _flask_restart_in_progress:
                # Let Flask finish flushing the HTTP ack / persist before kill.
                time.sleep(2.0)
                try:
                    from api import flask_restart as fr

                    req = fr.consume_daemon_request()
                except Exception:
                    req = None
                    try:
                        FLASK_RESTART_REQUEST_PATH.unlink(missing_ok=True)
                    except Exception:
                        pass
                if req and req.get("restart_id"):
                    perform_flask_restart(
                        restart_id=str(req.get("restart_id")),
                        mode=str(req.get("mode") or "graceful"),
                        source="flask_request",
                    )
        except Exception as e:
            print(f"[DAEMON] restart request watcher: {e}")
        time.sleep(1.0)


def run_home_automation_loop():
    """Apply Govee schedule when the active period changes (cadence from home_automation constants)."""
    from managers.home_automation import (
        DAEMON_SCHEDULE_CHECK_INTERVAL_SEC,
        DAEMON_SCHEDULE_STARTUP_DELAY_SEC,
        maybe_apply_scheduled_theme,
        record_daemon_schedule_tick,
    )

    time.sleep(DAEMON_SCHEDULE_STARTUP_DELAY_SEC)
    while daemon_running:
        try:
            r = maybe_apply_scheduled_theme()
            if not r.get("skipped") and r.get("auto"):
                if r.get("success"):
                    print(f"[DAEMON] Home auto lighting: period={r.get('period')} theme={r.get('theme')}")
                else:
                    print(f"[DAEMON] Home auto lighting failed: {r.get('errors', r)}")
        except Exception as e:
            print(f"[DAEMON] Home automation loop: {e}")
        record_daemon_schedule_tick()
        for _ in range(DAEMON_SCHEDULE_CHECK_INTERVAL_SEC):
            if not daemon_running:
                return
            time.sleep(1)


def run_cuttle_jobs_loop():
    """Claim Gitea @cuttle jobs from the Cuttle Jobs API."""
    try:
        from api.cuttle_jobs.worker import run_cuttle_jobs_loop as _loop

        _loop(should_continue=lambda: daemon_running)
    except Exception as e:
        print(f"[DAEMON] Cuttle Jobs worker exited: {e}")


def run_device_workers_loop():
    """Local device worker: register + claim host mesh jobs (CUTTLE_WORKERS.md)."""
    try:
        from api.device_workers.config import device_workers_enabled
        from managers.settings_manager import get_settings_manager

        if not device_workers_enabled():
            print("[DAEMON] Device workers disabled")
            return
        block = get_settings_manager().get_setting("device_workers") or {}
        if isinstance(block, dict) and block.get("local_worker") is False:
            print("[DAEMON] Device local_worker=false — not claiming jobs on host")
            return
        from api.device_workers.worker_loop import run_local_worker_loop

        run_local_worker_loop(should_continue=lambda: daemon_running)
    except Exception as e:
        print(f"[DAEMON] Device worker exited: {e}")


def _flask_health_check() -> bool:
    """Liveness: Flask must answer /api/status within 5 seconds over HTTPS :8080.

    /api/status is a cheap JSON ping. /api/health is a diagnostic (claude CLI,
    WSL, local LLM) and can take longer than this timeout even when Flask is up.
    """
    import json as _json
    import urllib.request
    try:
        req = urllib.request.Request(
            _flask_url("/api/status"),
            method="GET",
        )
        with _urlopen_flask(req, timeout=5) as resp:
            if resp.status != 200:
                return False
            body = _json.loads(resp.read().decode("utf-8", errors="replace") or "{}")
            if not isinstance(body, dict):
                return False
            if body.get("flask") is True:
                return True
            return body.get("status") in ("ok", "healthy")
    except Exception as e:
        print(f"[DAEMON] Flask liveness check failed: {type(e).__name__}: {e}")
        return False


def watch_flask_health():
    """Background thread: if Flask stops responding, restart it to recover from freezes."""
    last_restart = 0.0
    RESTART_COOLDOWN = 45  # Don't restart again within 45 seconds
    CHECK_INTERVAL = 15    # Check every 15 seconds
    while daemon_running:
        time.sleep(CHECK_INTERVAL)
        if not daemon_running:
            return
        if _flask_restart_in_progress:
            continue
        if "flask" not in processes:
            continue
        proc = processes["flask"]
        if proc.poll() is not None:
            continue  # Process dead - main loop will restart
        if not _flask_health_check():
            now = time.time()
            if now - last_restart < RESTART_COOLDOWN:
                continue  # Already restarted recently
            print("[DAEMON] Flask unresponsive — restarting...")
            perform_flask_restart(
                restart_id=f"watchdog-{int(now)}",
                mode="watchdog",
                source="health_watchdog",
            )
            last_restart = now
            time.sleep(5)  # Give Flask time to bind before next check


def _load_tray_icon_image():
    """Load a small RGBA tray icon. Prefer PNG on Linux — the .ico is a bad size and shows as a black square."""
    import warnings
    from PIL import Image

    candidates = [
        SRC_ROOT / "img" / "cuttle-mascot_square.png",
        SRC_ROOT / "img" / "cuttle-logo.png",
        SRC_ROOT / "img" / "cuttle_logo.ico",
    ]
    for icon_path in candidates:
        if not icon_path.exists():
            continue
        try:
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore", message="Image was not the expected size", module="PIL.IcoImagePlugin"
                )
                img = Image.open(icon_path)
            if hasattr(img, "n_frames") and img.n_frames > 1:
                img.seek(0)
            img = img.convert("RGBA")
            img.thumbnail((32, 32), Image.Resampling.LANCZOS)
            canvas = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
            x = (32 - img.width) // 2
            y = (32 - img.height) // 2
            canvas.paste(img, (x, y), img)
            return canvas
        except Exception as e:
            print(f"[DAEMON] Tray icon load warning ({icon_path.name}): {e}")
    return Image.new("RGBA", (32, 32), (26, 26, 26, 255))


def _tray_apply_lighting_theme(theme_id: str) -> None:
    """POST to Flask — same path as the Home Automation page."""
    import urllib.request

    try:
        payload = json.dumps({"theme": theme_id}).encode("utf-8")
        req = urllib.request.Request(
            _flask_url("/api/home-automation/apply-theme"),
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with _urlopen_flask(req, timeout=120) as resp:
            body = json.loads(resp.read().decode())
        ok = body.get("success")
        msg = f"Lights: {theme_id}" + (" ✓" if ok else " — error")
        if tray_icon and hasattr(tray_icon, "notify"):
            tray_icon.notify(title="Cuttle", message=msg[:256])
        if not ok:
            print(f"[DAEMON] Tray lighting {theme_id}: {body.get('errors', body)}")
    except Exception as e:
        print(f"[DAEMON] Tray lighting {theme_id} failed: {e}")
        if tray_icon and hasattr(tray_icon, "notify"):
            try:
                tray_icon.notify(title="Cuttle", message=f"Lights failed: {e}"[:256])
            except Exception:
                pass


def _setup_tray():
    """Create and run system tray icon in a separate thread."""
    global tray_icon, daemon_running
    if not _should_show_tray():
        print("[DAEMON] Tray skipped (CUTTLE_NO_TRAY) — Electron owns the tray.")
        return
    try:
        import pystray
    except ImportError:
        print("[DAEMON] No tray icon (pip install pystray for tray). Daemon running.")
        return

    def _theme_handler(tid: str):
        return lambda icon, item: _tray_apply_lighting_theme(tid)

    def on_open(icon, item):
        open_cuttle_ui()

    def on_node_editor(icon, item):
        open_cuttle_ui(["--router"], browser_path="/router_editor.html")

    def on_home_automation(icon, item):
        webbrowser.open(_flask_url("/home_automation.html"))

    def on_restart_flask(icon, item):
        print("[DAEMON] Tray: restarting Flask server...")
        restart_process("flask")

    def on_restart_llamacpp(icon, item):
        print("[DAEMON] Tray: restarting llama-server...")
        restart_process("llamacpp")

    def on_exit(icon, item):
        global daemon_running
        daemon_running = False
        stop_all_processes()
        icon.stop()

    image = _load_tray_icon_image()
    lights_menu = pystray.Menu(
        pystray.MenuItem("All off", _theme_handler("off")),
        pystray.MenuItem("Firelit", _theme_handler("firelit")),
        pystray.MenuItem("Cinematic", _theme_handler("cinematic")),
        pystray.MenuItem("Warm", _theme_handler("warm")),
        pystray.MenuItem("Aurora", _theme_handler("aurora")),
    )
    menu_items = [
        pystray.MenuItem("Open Cuttle", on_open, default=True),
        pystray.MenuItem("Open Router", on_node_editor),
        pystray.MenuItem("Home Automation…", on_home_automation),
        pystray.MenuItem("Lights", lights_menu),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Restart Flask", on_restart_flask),
    ]
    if _llamacpp_enabled():
        menu_items.append(pystray.MenuItem("Start/Restart llama-server", on_restart_llamacpp))
    menu_items.extend([
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Exit", on_exit),
    ])
    menu = pystray.Menu(*menu_items)
    tray_icon = pystray.Icon("Cuttle Daemon", image, "Cuttle Daemon", menu)
    tray_icon.run_detached()  # Runs in separate thread, main thread continues


def _watch_notify_queue():
    """Poll the notify queue and show tray notifications when app window is closed."""
    global tray_icon
    while daemon_running:
        try:
            if tray_icon and NOTIFY_QUEUE_PATH.exists() and NOTIFY_QUEUE_PATH.stat().st_size > 0:
                with open(NOTIFY_QUEUE_PATH, 'r+', encoding='utf-8') as f:
                    lines = f.readlines()
                    f.seek(0)
                    f.truncate()
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        title = entry.get('title', 'Cuttle')
                        msg = entry.get('message', '')[:256]
                        if msg and hasattr(tray_icon, 'notify'):
                            tray_icon.notify(title=title, message=msg)
                    except (json.JSONDecodeError, Exception):
                        pass
        except Exception:
            pass
        time.sleep(1.5)


def _emit_daemon_line(stream, line: str, color: bool) -> None:
    stamped = stamp_daemon_line(line)
    stream.write((colorize_daemon_line(stamped) if color else stamped) + "\n")


class _ColorConsole:
    """Write timestamped (and ANSI-colored, if tty) lines; keep incomplete lines buffered."""

    def __init__(self, stream):
        self._stream = stream
        self._buf = ""
        self._color = bool(getattr(stream, "isatty", lambda: False)())

    def write(self, data):
        if not data:
            return 0
        self._buf += data.replace("\r\n", "\n")
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            try:
                _emit_daemon_line(self._stream, line, self._color)
            except (OSError, ValueError):
                pass
        return len(data)

    def flush(self):
        if self._buf:
            text = self._buf
            self._buf = ""
            try:
                stamped = stamp_daemon_line(text)
                self._stream.write(colorize_daemon_line(stamped) if self._color else stamped)
            except (OSError, ValueError):
                pass
        try:
            self._stream.flush()
        except (OSError, ValueError):
            pass


class _StampLog:
    """Plain timestamped lines for daemon.log (no ANSI)."""

    def __init__(self, stream):
        self._stream = stream
        self._buf = ""

    def write(self, data):
        if not data:
            return 0
        self._buf += data.replace("\r\n", "\n")
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            try:
                self._stream.write(stamp_daemon_line(line) + "\n")
                self._stream.flush()
            except (OSError, ValueError):
                pass
        return len(data)

    def flush(self):
        if self._buf:
            text = self._buf
            self._buf = ""
            try:
                self._stream.write(stamp_daemon_line(text))
            except (OSError, ValueError):
                pass
        try:
            self._stream.flush()
        except (OSError, ValueError):
            pass


class _Tee:
    """Write to console (colored) and daemon.log (plain)."""
    def __init__(self, *files):
        self._files = [f for f in files if f is not None]

    def write(self, data):
        for f in self._files:
            try:
                f.write(data)
                f.flush()
            except (OSError, ValueError):
                pass

    def flush(self):
        for f in self._files:
            try:
                f.flush()
            except (OSError, ValueError):
                pass


def run_daemon():
    """Run daemon loop: start services, watch pipelines for hot-reload, system tray."""
    global daemon_running

    _enable_windows_ansi()

    # Redirect daemon output to log file (also to original stdout/stderr if attached)
    try:
        daemon_log = open(LOGS_DIR / "daemon.log", "a", encoding="utf-8")
        daemon_log.write(f"\n--- Daemon started {datetime.now().isoformat()} ---\n")
        daemon_log.flush()
        sys.stdout = _Tee(_ColorConsole(sys.stdout), _StampLog(daemon_log))
        sys.stderr = _Tee(_ColorConsole(sys.stderr), _StampLog(daemon_log))
    except Exception as e:
        print(f"[DAEMON] Could not open daemon log: {e}")

    def handler(signum, frame):
        global daemon_running
        daemon_running = False

    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)

    # Start system tray icon (runs in separate thread)
    tray_thread = threading.Thread(target=_setup_tray, daemon=False)
    tray_thread.start()
    time.sleep(0.5)  # Let tray initialize

    print("[DAEMON] Cuttle Daemon starting...")
    try:
        from api.shared_media import purge_expired

        purged = purge_expired(project_root=PROJECT_ROOT)
        n = purged.get("deleted") or 0
        if n:
            print(f"[DAEMON] Purged {n} expired shared media file(s) (TTL {purged.get('ttl_days')}d)")
        else:
            print(f"[DAEMON] Shared media TTL check OK (0 expired, TTL {purged.get('ttl_days')}d)")
    except Exception as e:
        print(f"[DAEMON] Shared media purge skipped: {e}")
    if _llamacpp_enabled():
        # On-demand model server: not started at boot. Local-mode chat, Auto when
        # local is needed (/hermes or LLM fallback), tray menu, or POST /api/local-llm/start.
        if _llamacpp_reachable():
            print("[DAEMON] llama-server already running (external)")
        else:
            print("[DAEMON] llama-server not started at boot (on-demand) — chat Yes/No prompt or tray menu launches it")
    start_flask()
    flask_ready = wait_for_flask_ready(timeout_s=45.0)
    if flask_ready:
        print("[DAEMON] Flask is ready on https://127.0.0.1:8080")
    else:
        print("[DAEMON] WARNING: Flask did not accept connections within 45s")
    try:
        from api.lan_access import is_lan_access_enabled, lan_phone_portal_url, get_lan_ipv4, LAN_HTTP_PORT
        if is_lan_access_enabled():
            ip = get_lan_ipv4()
            url = lan_phone_portal_url(LAN_HTTP_PORT, ip)
            if url:
                print(f"[DAEMON] Phone portal: {url}/phone")
                print(f"[DAEMON] Phone test:  {url}/api/lan-ping")
    except Exception:
        pass
    print("[DAEMON] Discord inbound gateway is not started (optional REST agent-ops only).")


    # Start notify queue watcher (shows tray toasts when app window is closed)
    notify_watcher = threading.Thread(target=_watch_notify_queue, daemon=True)
    notify_watcher.start()

    # Start Flask health watchdog — auto-restart if Flask freezes (e.g. viewport goes blank)
    health_watcher = threading.Thread(target=watch_flask_health, daemon=True)
    health_watcher.start()

    # Graceful restart requests from Flask (/restart, flask.restart action)
    restart_req_watcher = threading.Thread(target=watch_flask_restart_requests, daemon=True)
    restart_req_watcher.start()

    # Home automation — time-of-day Govee themes (see /home_automation.html)
    ha_watcher = threading.Thread(target=run_home_automation_loop, daemon=True)
    ha_watcher.start()

    # Gitea @cuttle remote jobs — claim from the jobs-host queue (LAN)
    cuttle_jobs_watcher = threading.Thread(target=run_cuttle_jobs_loop, daemon=True)
    cuttle_jobs_watcher.start()

    # LAN device-worker mesh — host registers as a worker + claims local queue jobs
    device_workers_watcher = threading.Thread(target=run_device_workers_loop, daemon=True)
    device_workers_watcher.start()

    if _should_open_ui() and flask_ready:
        open_cuttle_ui()
    elif _should_open_ui() and not flask_ready:
        print("[DAEMON] Skipping Electron auto-open until Flask is up — use the tray icon")

    print("[DAEMON] Services started. Tray icon manages Cuttle. Exit from tray to stop.")
    while daemon_running:
        # Re-spawn dead processes (skip while a coordinated restart owns the lifecycle)
        for name in list(processes.keys()):
            proc = processes[name]
            if proc.poll() is None:
                continue
            if name == "llamacpp":
                # On-demand model server: don't auto-respawn. The user (or a
                # chat Yes/No launch prompt) starts it again when needed.
                print(f"[DAEMON] llama-server exited (code {proc.returncode}) — staying stopped (on-demand)")
                del processes[name]
                continue
            if name == "flask" and _flask_restart_in_progress:
                continue
            print(f"[DAEMON] {name} exited (code {proc.returncode}), restarting...")
            if name == "flask":
                # External kill (agent taskkill / action worker): coordinated path
                perform_flask_restart(
                    restart_id=f"respawn-{int(time.time())}",
                    mode="respawn",
                    source="exit_watch",
                )
        time.sleep(5)

    print("[DAEMON] Shutting down...")
    stop_all_processes()


if __name__ == "__main__":
    run_daemon()

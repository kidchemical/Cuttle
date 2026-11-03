"""Agent-owned OS processes, attributed by the run markers on every harness CLI.

Every agent CLI is launched with ``CUTTLE_CHAT_SESSION_ID`` /
``CUTTLE_AGENT_RUN_ID`` / ``CUTTLE_AGENT_ID`` in its environment
(``core.agent_cli_env.agent_operation_context``). Children inherit that
environment, including work an agent detaches on purpose (``nohup`` /
``setsid`` builds that the OS re-parents to init or ``systemd --user``). The
process tree loses the link at the re-parent; the environment does not. That
marker is the ownership record here; nothing is registered by the harness.

* ``build_forest(records, host_pids=…)`` (pure) groups attributed processes
  into one tree per chat and flags *detached* roots: processes whose parent is
  neither another attributed process nor a Cuttle host process.
* ``scan()`` reads the live process table (psutil, cached ``SCAN_TTL_SEC``).
* ``end_process(pid, create_time)`` terminates one attributed process and its
  descendants, identity-checked by creation time.

Known gaps: a launcher that clears the environment (``env -i``, a service
manager, a container runtime) produces processes this cannot attribute; work
on other machines is the mesh's (``api.device_workers``).
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Set

CHAT_ENV = "CUTTLE_CHAT_SESSION_ID"
RUN_ENV = "CUTTLE_AGENT_RUN_ID"
AGENT_ENV = "CUTTLE_AGENT_ID"
MODEL_ENV = "CUTTLE_AGENT_MODEL"
SCAN_TTL_SEC = 5.0 if os.name == "nt" else 2.0  # environ reads are slower on Windows
_CMDLINE_MAX = 400
# PID reuse guard tolerance (psutil create_time is float seconds).
_CTIME_EPS = 0.01

_lock = threading.Lock()
_cache: Dict[str, Any] = {"at": 0.0, "records": []}


def _bare_chat(raw: Any) -> str:
    text = str(raw or "").strip()
    if text.startswith("db_session_"):
        text = text[len("db_session_"):]
    if text.upper().startswith("CH-"):
        text = text[3:].split("-", 1)[0]
    return str(int(text)) if text.isdigit() else text


# ── pure ────────────────────────────────────────────────────────────────────


def build_forest(records: Iterable[Dict[str, Any]], *, host_pids: Iterable[int] = ()) -> Dict[str, Dict[str, Any]]:
    """Group attributed process records into per-chat trees.

    ``records``: ``{pid, ppid, create_time, chat_session_id, …}`` for every
    attributed process. ``host_pids``: Cuttle's own processes (Flask, daemon);
    a root started by one of them is attached, any other root is detached.

    Returns ``{chat_id: {roots, process_count, detached_count, spawned_by}}``
    where ``roots`` are node dicts (the record plus ``children``,
    ``detached``, ``parent_chat``) ordered by creation time, and
    ``spawned_by`` maps the chat ids that started this chat's roots (a parent
    chat whose CLI ran ``python -m api.subagents``) to that process id.
    """
    hosts = {int(p) for p in host_pids if p}
    nodes: Dict[int, Dict[str, Any]] = {}
    for rec in records:
        try:
            pid = int(rec["pid"])
        except (KeyError, TypeError, ValueError):
            continue
        chat = _bare_chat(rec.get("chat_session_id"))
        if pid <= 1 or not chat:
            continue
        nodes[pid] = {**rec, "pid": pid, "chat_session_id": chat, "children": [],
                      "detached": False, "parent_chat": None}
    forest: Dict[str, Dict[str, Any]] = {}

    def owner(chat: str) -> Dict[str, Any]:
        return forest.setdefault(chat, {"roots": [], "process_count": 0, "detached_count": 0, "spawned_by": {}})

    for pid, node in nodes.items():
        chat = node["chat_session_id"]
        entry = owner(chat)
        entry["process_count"] += 1
        try:
            ppid = int(node.get("ppid") or 0)
        except (TypeError, ValueError):
            ppid = 0
        parent = nodes.get(ppid)
        if parent is not None and float(parent.get("create_time") or 0) > float(node.get("create_time") or 0) + _CTIME_EPS:
            parent = None  # the recorded parent pid now names a younger process
        if parent is not None and parent["chat_session_id"] == chat:
            parent["children"].append(node)
            continue
        if parent is not None:
            node["parent_chat"] = parent["chat_session_id"]
            entry["spawned_by"].setdefault(parent["chat_session_id"], ppid)
        elif ppid not in hosts:
            node["detached"] = True
            entry["detached_count"] += 1
        entry["roots"].append(node)

    def order(items: List[Dict[str, Any]]) -> None:
        items.sort(key=lambda n: (float(n.get("create_time") or 0), n["pid"]))
        for item in items:
            order(item["children"])

    for entry in forest.values():
        order(entry["roots"])
    return forest


def count_tree(nodes: Iterable[Dict[str, Any]]) -> int:
    return sum(1 + count_tree(n.get("children") or ()) for n in nodes)


# ── effects ─────────────────────────────────────────────────────────────────


def host_pids() -> Set[int]:
    """Processes that start attached agent CLIs: this Flask process and its parent (the daemon).

    Deliberately not every ancestor: ``systemd --user`` / init are ancestors
    too, and they are exactly where detached work gets re-parented.
    """
    return {p for p in (os.getpid(), os.getppid()) if p > 1}


_MANAGED_SCRIPTS = ("web_chat_api.py", "cuttle_daemon.py", "cuttle_shadow_app.py")


def _protected(pid: int, cmdline: str = "") -> bool:
    """Never signal init, Flask, its ancestors, or a daemon/Flask process."""
    from api.process_kill_safety import is_forbidden_kill_target

    if is_forbidden_kill_target(pid) or pid in host_pids():
        return True
    return _managed_cmdline(cmdline)


def _managed_cmdline(cmdline: str) -> bool:
    argv = (cmdline or "").split()
    # Flask / daemon run as `python …/web_chat_api.py`; an editor or grep that
    # merely names the file is not one of them.
    base = lambda arg: arg.replace("\\", "/").rsplit("/", 1)[-1].lower()  # noqa: E731
    return bool(argv) and "python" in base(argv[0]) and any(base(arg) in _MANAGED_SCRIPTS for arg in argv[1:3])


def _cmdline(proc) -> str:
    try:
        return " ".join(proc.cmdline() or [])
    except Exception:
        return ""


def _record(proc, env: Dict[str, str]) -> Dict[str, Any]:
    import psutil

    info = proc.info
    try:
        rss = int(proc.memory_info().rss)
    except psutil.Error:
        rss = None
    try:
        cpu = proc.cpu_times()
        cpu_sec = round(float(cpu.user) + float(cpu.system), 1)
    except psutil.Error:
        cpu_sec = None
    return {
        "pid": int(info["pid"]),
        "ppid": int(info.get("ppid") or 0),
        "name": info.get("name") or str(info["pid"]),
        "status": info.get("status") or "",
        "create_time": float(info.get("create_time") or 0),
        "cmdline": _cmdline(proc)[:_CMDLINE_MAX],
        "rss": rss,
        "cpu_sec": cpu_sec,
        "chat_session_id": _bare_chat(env.get(CHAT_ENV)),
        "run_id": (env.get(RUN_ENV) or "")[:64],
        "agent_id": (env.get(AGENT_ENV) or "")[:40],
        "model": (env.get(MODEL_ENV) or "")[:80],
    }


def _scan_now() -> List[Dict[str, Any]]:
    from core.runtime_paths import is_shadow_instance

    # A shadow dev child is sandboxed: host processes (and their environments)
    # belong to the live install, so it attributes none of them.
    if is_shadow_instance():
        return []
    try:
        import psutil
    except ImportError:
        return []
    me = os.getpid()
    out: List[Dict[str, Any]] = []
    for proc in psutil.process_iter(["pid", "ppid", "name", "create_time", "status"]):
        try:
            if proc.info["pid"] == me or proc.info.get("status") == psutil.STATUS_ZOMBIE:
                continue
            env = proc.environ()
        except (psutil.Error, OSError):
            continue
        if not (env.get(CHAT_ENV) or "").strip():
            continue
        try:
            rec = _record(proc, env)
        except (psutil.Error, OSError):
            continue
        if rec["chat_session_id"]:
            out.append(rec)
    return out


def scan(*, max_age: float = SCAN_TTL_SEC) -> List[Dict[str, Any]]:
    """Attributed live processes (copies), from a scan at most ``max_age`` old.

    ``max_age <= 0`` always rescans: Windows' monotonic clock ticks every
    ~15 ms, so ``now - at <= 0`` can hold right after a previous scan.
    """
    now = time.monotonic()
    with _lock:
        if max_age > 0 and _cache["at"] and now - _cache["at"] <= max_age:
            return [dict(r) for r in _cache["records"]]
    records = _scan_now()
    with _lock:
        _cache["at"] = time.monotonic()
        _cache["records"] = records
    return [dict(r) for r in records]


def invalidate() -> None:
    with _lock:
        _cache["at"] = 0.0


def _is_host_script(pid: int) -> bool:
    try:
        import psutil

        return _managed_cmdline(" ".join(psutil.Process(pid).cmdline() or []))
    except Exception:
        return False


def forest(*, max_age: float = SCAN_TTL_SEC) -> Dict[str, Dict[str, Any]]:
    """Per-chat trees for the live process table (see ``build_forest``)."""
    records = scan(max_age=max_age)
    hosts = host_pids()
    known = {r["pid"] for r in records}
    # Outside Flask (``python -m api.jobs``) the launcher is not this process;
    # a parent running web_chat_api.py / cuttle_daemon.py is still the host.
    for ppid in {r["ppid"] for r in records} - known - hosts:
        if ppid > 1 and _is_host_script(ppid):
            hosts.add(ppid)
    return build_forest(records, host_pids=hosts)


def end_process(pid: Any, create_time: Any, *, grace: float = 5.0) -> Dict[str, Any]:
    """Terminate one agent-owned process and its descendants.

    Raises ``LookupError`` when the process is gone or ``pid`` now names a
    different process than the one listed (``create_time`` mismatch), and
    ``PermissionError`` for a protected process (init, Flask and its
    ancestors, the daemon) or one without a Cuttle run marker.
    Detached processes are usually not Flask descendants, so the
    descendant-only rule in ``chat_run_registry.kill_pid_tree`` cannot apply;
    identity plus the marker is the guard.
    """
    import psutil
    from api.process_kill_safety import coerce_pid
    from core.runtime_paths import is_shadow_instance

    if is_shadow_instance():
        raise PermissionError("a shadow instance does not end host processes")
    pid_i = coerce_pid(pid)
    if pid_i is None or pid_i <= 1:
        raise LookupError(f"not a process id: {pid}")
    try:
        expected = float(create_time)
    except (TypeError, ValueError):
        raise LookupError("create_time is required to identify the process")
    try:
        root = psutil.Process(pid_i)
        actual = root.create_time()
    except psutil.NoSuchProcess:
        raise LookupError(f"process {pid_i} is no longer running")
    if abs(actual - expected) >= _CTIME_EPS:
        raise LookupError(f"process {pid_i} is no longer the listed process")
    try:
        env = root.environ()
        name = root.name()
        cmdline = " ".join(root.cmdline() or [])
    except psutil.AccessDenied:
        raise PermissionError(f"process {pid_i} belongs to another user")
    except psutil.NoSuchProcess:
        raise LookupError(f"process {pid_i} is no longer running")
    if _protected(pid_i, cmdline):
        raise PermissionError(f"process {pid_i} is protected")
    chat = _bare_chat(env.get(CHAT_ENV))
    if not chat:
        raise PermissionError(f"process {pid_i} was not started by a Cuttle agent")
    try:
        procs = [root] + root.children(recursive=True)
    except psutil.NoSuchProcess:
        procs = [root]
    procs = [p for p in procs if p.pid == pid_i or not _protected(p.pid, _cmdline(p))]
    # Descendants first: signalling the root first re-parents its children
    # before they are reached, and they would survive outside the tree.
    for p in reversed(procs):
        try:
            p.terminate()
        except psutil.Error:
            pass
    _gone, alive = psutil.wait_procs(procs, timeout=grace)
    for p in alive:
        try:
            p.kill()
        except psutil.Error:
            pass
    if alive:
        _gone, alive = psutil.wait_procs(alive, timeout=3)
    invalidate()
    return {"pid": pid_i, "name": name, "chat_session_id": chat,
            "ended": len(procs) - len(alive), "still_running": [p.pid for p in alive]}


def end_chat_processes(chat_session_id: Any) -> Dict[str, Any]:
    """End every process attributed to one chat (each root tree, oldest first)."""
    chat = _bare_chat(chat_session_id)
    if not chat:
        raise LookupError("chat session id required")
    entry = forest(max_age=0).get(chat) or {"roots": []}
    ended, errors = [], []
    for node in entry["roots"]:
        try:
            ended.append(end_process(node["pid"], node["create_time"]))
        except LookupError:
            continue  # already gone with an earlier tree
        except PermissionError as exc:
            errors.append({"pid": node["pid"], "error": str(exc)})
    return {"chat_session_id": chat, "ended": ended, "errors": errors}

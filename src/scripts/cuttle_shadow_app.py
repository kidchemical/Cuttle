"""Shadow-app child bootstrap (runs ONLY as the spawned dev child).

Order: env validation -> deny guards (subprocess, socket, files) -> real app
import -> executor fakes -> route allowlist (first) -> bind port 0 ->
manifest -> serve. Importing this module must have NO side effects;
everything runs under ``main()``.

NOTE: importing the production app here trips the architecture boundary
scanner (``test_no_reverse_imports_into_web_chat_api`` scans src/scripts).
The justified composition exception is recorded in
REVERSE_IMPORT_ALLOWLIST; this file is dev-only composition, not an owned
layer, and owned API modules never import it (the CLI spawns it as a
subprocess).

Guards here are application-level dev effect isolation, NOT an OS security
sandbox. Every denial is counted and logged; unexpected attempts fail tests.
"""

from __future__ import annotations

import collections
import hashlib
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

RESERVED_PORTS = frozenset({8080, 8000, 8888})


def _reserved_ports() -> frozenset | None:
    """Parent-resolved denylist (defaults + configured live ports).

    Returns None when CUTTLE_SHADOW_RESERVED_PORTS is malformed so the
    caller refuses boot instead of checking a partial denylist.
    """
    raw = (os.environ.get("CUTTLE_SHADOW_RESERVED_PORTS") or "").strip()
    if not raw:
        return RESERVED_PORTS
    extra = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit():
            return None
        port = int(part, 10)
        if not 1 <= port <= 65535:
            return None
        extra.add(port)
    return RESERVED_PORTS | frozenset(extra)

# Reviewed B1-journey route allowlist. Default-deny:
# anything not listed gets a stable side-effect-free 403. Method-aware: path
# prefixes are GET-only, never broad method-agnostic POST pages. No blanket
# /output/ (shared staging). S2 runs no action execution; Q&A stays open.
# Real auth uses the owned blueprint routes (registered on the root app);
# no DB-minted cookies, no fake auth.
ALLOW_EXACT = frozenset({
    ("POST", "/api/chat"),
    ("POST", "/api/chat-cancel"),
    ("GET", "/api/chat-pending-result"),
    ("GET", "/api/chat-live-status"),
    ("GET", "/api/chat-live-status-batch"),
    ("POST", "/api/auth/register"),
    ("POST", "/api/auth/login"),
    ("GET", "/api/auth/me"),
    ("GET", "/api/auth/sessions"),
    ("POST", "/api/auth/sessions"),
    ("GET", "/api/sessions"),
    ("GET", "/api/sessions/list"),
    ("POST", "/api/sessions/send"),
    ("GET", "/api/settings/video-background"),
    ("GET", "/api/settings/chat-tts"),
    ("GET", "/api/settings/starred-project"),
    ("GET", "/api/settings/starred-slash"),
    ("GET", "/api/projects"),
    ("GET", "/api/gizmos/tasks"),
    ("GET", "/api/status"),
    ("GET", "/"),
    ("GET", "/chat_page.html"),
})
# NOTE: no /favicon.ico route exists in the app (verified); browsers simply
# 404 it, so it stays default-denied.
ALLOW_GET_PREFIXES = (
    "/static/",
    "/js/",
    "/css/",
    "/img/",
    "/sounds/",
    "/api/auth/sessions/",
    "/api/sessions/",
    "/api/shell/panes/",
)

# PATCH-only session rename/project update: exact numeric id, no child
# paths (auth_api.patch_chat_session: owned private DB, no executor or
# title-provider calls).
import re as _re
ALLOW_PATCH_SESSION_RE = _re.compile(r"^/api/auth/sessions/\d+$")
# Attention acknowledgments mutate only the owned private DB; no executor,
# project action or external service can run through this numeric-only route.
ALLOW_PUT_SESSION_ATTENTION_RE = _re.compile(r"^/api/auth/sessions/\d+/attention$")
# POST-only durable system-notice persist (auth_api.post_session_message):
# exact numeric id + /messages suffix, no child paths. The real endpoint
# accepts role=system only (400 otherwise); no assistant writes via this
# route, so B1's notice-not-assistant policy is enforced by production code.
ALLOW_POST_SESSION_MESSAGE_RE = _re.compile(
    r"^/api/auth/sessions/\d+/messages$")

# Effectful chat payloads denied explicitly at the chat gate (deny-list on
# top of default-deny): restart controls, compat command runner, and
# action-form/button markup. S2 needs no Q&A execution.
DENY_CHAT_PREFIXES = ("/restart", "/cmd", "[button", "[action-form")

# Owner execution seams replaced IN CHILD (deterministic, scenario-driven).
# Real coordinator/workflow/saver still process every returned result dict.
# NOTE on aliasing: integration.execute_decision IS dispatch.execute_decision
# (same object via from-import), but call sites resolve through their own
# module globals, so every module attribute must be patched, not just one.
# The pipeline lane (maybe_route_plain_message) reaches the fake through
# integration.execute_decision after decide(); the routing *decision* itself
# is stubbed too (see install_executor_fakes) so no provider is consulted.
SEAMS = (
    ("api.agent_harness.kernel", "run_agent_web_command"),
    ("api.agent_router.dispatch", "execute_decision"),
    ("api.agent_router.integration", "execute_decision"),
    ("api.agent_router.integration", "execute_explicit_target"),
)

SCENARIOS = (
    "blocked", "success", "usefulfailure", "emptyfailure",
    "cancelled", "system", "supervised", "hold",
)


class ShadowBlocked(RuntimeError):
    """A denied subprocess/spawn, socket, file, or default executor call."""


class ShadowError(RuntimeError):
    pass


_blocked_counts: dict[str, int] = {
    "subprocess": 0, "socket": 0, "executor": 0, "file": 0, "route": 0,
}
_block_log: list[str] = []


def _note(kind: str, detail: str) -> None:
    _blocked_counts[kind] += 1
    entry = f"[shadow-guard] blocked {kind}: {detail}"
    _block_log.append(entry)
    print(entry, flush=True)


# ---------------------------------------------------------------------------
# Uninstall registry (tests + teardown restore every patch).
# ---------------------------------------------------------------------------

_UNINSTALL: list = []


def _remember(restore) -> None:
    _UNINSTALL.append(restore)


def uninstall_all() -> None:
    """Restore every installed guard, newest first. Idempotent."""
    while _UNINSTALL:
        try:
            _UNINSTALL.pop()()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Subprocess deny-all (before app import).
# ---------------------------------------------------------------------------

def install_subprocess_guard() -> None:
    import subprocess as _sp

    def _deny(name: str):
        def _raise(*a, **k):
            _note("subprocess", f"{name} args={a[0] if a else '?'}")
            raise ShadowBlocked(f"shadow dev: subprocess {name} denied")
        return _raise

    saved = {}
    for attr in ("Popen", "run", "call", "check_call", "check_output"):
        saved[attr] = getattr(_sp, attr)
        setattr(_sp, attr, _deny(attr))
    _remember(lambda: [setattr(_sp, k, v) for k, v in saved.items()])

    saved_os = {}
    for attr in ("system", "posix_spawn", "spawnl", "spawnle", "spawnlp",
                 "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe",
                 "execl", "execle", "execlp", "execlpe",
                 "execv", "execve", "execvp", "execvpe"):
        if hasattr(os, attr):
            saved_os[attr] = getattr(os, attr)
            setattr(os, attr, _deny(f"os.{attr}"))
    _remember(lambda: [setattr(os, k, v) for k, v in saved_os.items()])


# ---------------------------------------------------------------------------
# Socket guard (before app import). Two phases: deny-all, then exact bound
# address once published. connect_ex returns an errno instead of raising;
# sendto/sendmsg (datagram bypass) denied; AF_UNIX connect denied (live IPC)
# — socketpair never calls connect so it keeps working. Non-INET binds
# (AF_UNIX paths) denied outright since the file audit policy does not
# inspect socket.bind events. INET bind restricted to one loopback
# listener (port 0-or-fixed) plus the literal ::1:0 stdlib probe, so no
# extra listener can appear.
# ---------------------------------------------------------------------------

class _SockState:
    allowed_inet: tuple | None = None
    fixed_port: int = 0
    bound_once: bool = False


def install_socket_guard(fixed_port: int = 0) -> _SockState:
    state = _SockState()
    state.fixed_port = fixed_port
    _real_connect = socket.socket.connect
    _real_connect_ex = socket.socket.connect_ex
    _real_bind = socket.socket.bind
    _real_getaddrinfo = socket.getaddrinfo
    _real_sendto = socket.socket.sendto
    _real_sendmsg = getattr(socket.socket, "sendmsg", None)

    def _inet_ok(sock, host, port) -> bool:
        return (state.allowed_inet is not None
                and (host, port) == state.allowed_inet)

    def _guarded_connect(sock, address, *a, **k):
        if sock.family not in (socket.AF_INET, socket.AF_INET6):
            _note("socket", f"AF_UNIX connect denied")
            raise ShadowBlocked("shadow dev: AF_UNIX connect denied")
        host, port = address[0], address[1]
        if _inet_ok(sock, host, port):
            return _real_connect(sock, address, *a, **k)
        _note("socket", f"connect {host}:{port}")
        raise ShadowBlocked(f"shadow dev: outbound connect {host}:{port} denied")

    def _guarded_connect_ex(sock, address, *a, **k):
        try:
            _guarded_connect(sock, address, *a, **k)
            return 0
        except ShadowBlocked:
            import errno
            return errno.EPERM

    def _guarded_bind(sock, address, *a, **k):
        if sock.family not in (socket.AF_INET, socket.AF_INET6):
            # Non-INET binds (AF_UNIX paths, etc.) are denied outright: the
            # filesystem audit policy does not inspect socket.bind events,
            # and the shadow app needs no Unix or other family listener.
            _note("socket", f"non-INET bind denied")
            raise ShadowBlocked("shadow dev: non-INET bind denied")
        try:
            host, port = address[0], address[1]
        except Exception:
            _note("socket", "malformed INET bind denied")
            raise ShadowBlocked("shadow dev: malformed INET bind denied")
        if host == "::1" and port == 0:
            return _real_bind(sock, address, *a, **k)  # stdlib probe
        if host == "127.0.0.1" and port in (0, state.fixed_port):
            if state.bound_once:
                _note("socket", f"second bind {host}:{port}")
                raise ShadowBlocked(
                    "shadow dev: only one INET listener allowed")
            state.bound_once = True
            return _real_bind(sock, address, *a, **k)
        _note("socket", f"bind {host}:{port}")
        raise ShadowBlocked(
            f"shadow dev: unexpected INET bind {host}:{port} denied")

    def _guarded_getaddrinfo(host, *a, **k):
        if host in ("127.0.0.1", "::1"):
            return _real_getaddrinfo(host, *a, **k)
        _note("socket", f"getaddrinfo {host}")
        raise ShadowBlocked(f"shadow dev: DNS for {host} denied")

    def _guarded_sendto(sock, *a, **k):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            _note("socket", "sendto denied")
            raise ShadowBlocked("shadow dev: sendto denied")
        return _real_sendto(sock, *a, **k)

    def _guarded_sendmsg(sock, *a, **k):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            _note("socket", "sendmsg denied")
            raise ShadowBlocked("shadow dev: sendmsg denied")
        return _real_sendmsg(sock, *a, **k)

    socket.socket.connect = _guarded_connect
    socket.socket.connect_ex = _guarded_connect_ex
    socket.socket.bind = _guarded_bind
    socket.getaddrinfo = _guarded_getaddrinfo
    socket.socket.sendto = _guarded_sendto
    _has_sendmsg = hasattr(socket.socket, "sendmsg")  # absent on Windows
    if _has_sendmsg:
        socket.socket.sendmsg = _guarded_sendmsg

    def _restore():
        socket.socket.connect = _real_connect
        socket.socket.connect_ex = _real_connect_ex
        socket.socket.bind = _real_bind
        socket.getaddrinfo = _real_getaddrinfo
        socket.socket.sendto = _real_sendto
        if _has_sendmsg:
            socket.socket.sendmsg = _real_sendmsg
    _remember(_restore)
    return state


# ---------------------------------------------------------------------------
# Child-only file/process policy via sys.addaudithook (before app import).
# Pure policy function + hook installer. The hook cannot be uninstalled, so
# it is installed ONLY in the spawned child main() — never in tests.
# Trusted application fences, NOT OS security sandboxing.
# ---------------------------------------------------------------------------

def _norm_path(path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _under(path: str, prefixes) -> bool:
    return any(path == p or path.startswith(p + os.sep) for p in prefixes)


def check_file_event(event: str, args, *, read_roots, write_roots,
                     exact_write=()) -> None:
    """Pure policy: raise ShadowBlocked on deny, else return None.

    read_roots: instance root (app+data+logs, all private) + read-only
    interpreter/dependency prefixes + explicit system files as needed.
    write_roots: private data dirs. exact_write: manifest/log paths.
    """
    if event == "open":
        path, mode, _flags = args[0], args[1] if len(args) > 1 else "", \
            args[2] if len(args) > 2 else 0
        if isinstance(path, int):
            return  # fds pass through; path opens are checked
        mode = mode or ""
        _flags = _flags or 0
        norm = _norm_path(os.path.realpath(path))
        writing = ("w" in mode or "a" in mode or "x" in mode or "+" in mode
                   or bool(_flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT
                                     | os.O_APPEND | os.O_TRUNC)))
        if writing:
            if norm in exact_write or _under(norm, write_roots):
                return
            _note("file", f"write {path} mode={mode}")
            raise ShadowBlocked(f"shadow dev: write denied: {path}")
        if _under(norm, read_roots):
            return
        _note("file", f"read {path}")
        raise ShadowBlocked(f"shadow dev: read denied: {path}")
    elif event in ("os.mkdir", "os.remove", "os.rmdir"):
        _reject_dir_fd(event, args)
        norm = _norm_path(os.path.realpath(args[0]))
        if _under(norm, write_roots):
            return
        _note("file", f"{event} {args[0]}")
        raise ShadowBlocked(f"shadow dev: {event} denied: {args[0]}")
    elif event in ("os.rename", "os.replace"):
        _reject_dir_fd(event, args)
        src = _norm_path(os.path.realpath(args[0]))
        dst = _norm_path(os.path.realpath(args[1]))
        if _under(src, write_roots) and (
                _under(dst, write_roots) or dst in exact_write):
            return
        _note("file", f"{event} {args[0]} -> {args[1]}")
        raise ShadowBlocked(f"shadow dev: {event} denied")
    elif event in ("os.symlink", "os.link"):
        _note("file", event)
        raise ShadowBlocked(f"shadow dev: {event} denied")
    elif event in ("os.kill", "os.killpg", "os.spawn", "os.system"):
        _note("file", event)
        raise ShadowBlocked(f"shadow dev: {event} denied")
    elif event == "sqlite3.connect":
        # args: (database,). The follow-up connect/handle carries only the
        # Connection object — the path was already gated here.
        db_path = args[0] if args and isinstance(args[0], str) else ""
        if db_path == ":memory:":
            return
        if db_path.startswith("file:"):
            _note("file", f"sqlite {db_path}")
            raise ShadowBlocked("shadow dev: sqlite file: URI denied")
        norm = _norm_path(os.path.realpath(db_path))
        if norm in exact_write or _under(norm, write_roots):
            return
        _note("file", f"sqlite {db_path}")
        raise ShadowBlocked(f"shadow dev: sqlite denied: {db_path}")
    elif event == "sqlite3.connect/handle":
        return  # path gated at sqlite3.connect above
    # Unknown events pass through (policy covers file/process only).


def _reject_dir_fd(event: str, args) -> None:
    """Reject non-default dir_fd inputs (positional audit tuples).

    Observed shapes: mkdir (path, mode, dir_fd), rename (src, dst,
    src_dir_fd, dst_dir_fd), remove/rmdir (path, dir_fd); defaults are
    None or -1. Only mkdir mode bits are additionally allowed. Strict:
    fd-relative stdlib internals (shutil.rmtree) are denied too — revisit
    with fd resolution if a dynamic trial shows legitimate breakage.
    """
    # Observed tuples: mkdir (path, mode, dir_fd), rename/replace
    # (src, dst, src_dir_fd, dst_dir_fd), remove/rmdir (path, dir_fd).
    if event == "os.mkdir":
        extras = list(args[2:])
    else:
        extras = list(args[1:]) if event in ("os.remove", "os.rmdir") \
            else list(args[2:])
    for extra in extras:
        if extra is None or extra == -1:
            continue
        _note("file", f"{event} dir_fd")
        raise ShadowBlocked(f"shadow dev: {event} dir_fd denied")


def _default_read_roots(instance_root: str):
    roots = [os.path.abspath(instance_root),
             sys.prefix, sys.base_prefix, sys.exec_prefix]
    norm = []
    for p in roots:
        try:
            norm.append(os.path.normcase(os.path.abspath(p)))
        except OSError:
            pass
    return norm


def install_audit_hook(instance_root: str, write_roots, exact_write,
                       extra_read=()) -> None:
    """Install the audit hook. CHILD ONLY — cannot be uninstalled."""
    read_roots = tuple(_default_read_roots(instance_root)) + tuple(extra_read)
    write_roots = tuple(write_roots)

    def _hook(event, args):
        check_file_event(event, args, read_roots=read_roots,
                         write_roots=write_roots, exact_write=exact_write)

    sys.addaudithook(_hook)
    # No sqlite3.connect wrapper: sqlite emits native sqlite3.connect /
    # sqlite3.connect/handle audit events (alias sqlite3.dbapi2 covered),
    # handled in the same policy above.


# ---------------------------------------------------------------------------
# Deterministic executor queue (in child, at owner seams).
# ---------------------------------------------------------------------------

_QUEUE: collections.deque = collections.deque()
_QUEUE_LOCK = threading.Lock()
_ID_COUNTER = {"n": 0}
_RELEASES: dict[str, threading.Event] = {}
_STATUSES: dict[str, str] = {}
_STARTED: dict[str, int] = {}
_COMPLETED: dict[str, int] = {}
_CANONICAL_IDS: dict[str, int] = {}
_DEFAULT_SCENARIO = {"name": "blocked"}
_HOLD_TIMEOUT = 120.0
OUTCOMES = ("success", "usefulfailure", "emptyfailure",
            "cancelled", "system", "supervised")


def _result_shape(outcome: str, prompt: str, response: str = "") -> dict:
    base = {"agent_used": "shadow", "model": "shadow", "provider": "shadow"}
    if outcome == "success":
        return {**base, "success": True,
                "response": response or f"shadow ok: {prompt}"}
    if outcome == "usefulfailure":
        return {**base, "success": False,
                "response": response or "shadow useful failure text (nonempty)"}
    if outcome == "emptyfailure":
        return {**base, "success": False, "response": ""}
    if outcome == "cancelled":
        return {**base, "success": False, "cancelled": True,
                "response": "[CANCELLED] shadow stop"}
    if outcome == "system":
        return {**base, "success": True,
                "response": response or "shadow note", "ui": "system"}
    raise ShadowError(f"unknown outcome: {outcome!r}")


def enqueue_item(item: dict) -> str:
    """Queue insert (control plane). Pre-creates the release event so a
    release-before-take is retained. Duplicate ids fail."""
    with _QUEUE_LOCK:
        call_id = str(item.get("id") or f"q{_ID_COUNTER['n']}")
        _ID_COUNTER["n"] += 1
        if call_id in _STATUSES or any(
                q.get("id") == call_id for q in _QUEUE):
            raise ShadowError(f"duplicate queue id: {call_id}")
        stored = dict(item)
        stored["id"] = call_id
        _QUEUE.append(stored)
        _RELEASES.setdefault(call_id, threading.Event())
        return call_id


def _take_queued() -> dict | None:
    with _QUEUE_LOCK:
        if not _QUEUE:
            return None
        item = dict(_QUEUE.popleft())  # immutable capture at entry
    call_id = str(item["id"])
    _STATUSES[call_id] = "started"
    _STARTED[call_id] = _STARTED.get(call_id, 0) + 1
    return {"call_id": call_id, **item}


def _finish(call_id: str, status: str) -> None:
    _STATUSES[call_id] = status
    _COMPLETED[call_id] = _COMPLETED.get(call_id, 0) + 1


def _push_status(kwargs, texts) -> None:
    """status_queue entries BEFORE any held wait: ('status', text)."""
    queue = kwargs.get("status_queue")
    if queue is None:
        return
    put = getattr(queue, "put", None)
    if put is None:
        return
    for text in texts or []:
        try:
            put(("status", text))
        except Exception:
            pass


def _run_one(item: dict, chat_session_id, kwargs) -> dict:
    """Execute one queued/default item through REAL downstream code.
    `completed` counts fake returns only, never route finalization."""
    call_id = item["call_id"]
    outcome = item.get("outcome", "success")
    _push_status(kwargs, item.get("status"))
    if item.get("hold"):
        _STATUSES[call_id] = "held"
        if not _RELEASES[call_id].wait(timeout=_HOLD_TIMEOUT):
            _finish(call_id, "hold-timeout")
            raise ShadowBlocked("shadow dev: held turn timed out")
    if outcome == "supervised":
        # The supervised orchestrator owns its canonical row: insert it via
        # the REAL db layer (not the saver) and capture the actual row id,
        # then return ONE skip marker so the shared saver takes its real
        # skip branch. Marker choices: 'skip' -> skip_history_persist,
        # 'coord' -> coordinator_response_message_id=<actual row id>.
        from api.auth_db import get_auth_db
        db = get_auth_db()
        sid_text = str(chat_session_id)
        if sid_text.startswith("db_session_"):
            sid_text = sid_text[len("db_session_"):]
        try:
            numeric_sid = int(sid_text)
        except (TypeError, ValueError):
            raise ShadowError(f"bad supervised sid: {chat_session_id!r}")
        text = item.get("response") or "shadow supervised canonical"
        row_id = db.add_message(numeric_sid, "assistant", text,
                                metadata={"supervised_by": "shadow-supervisor"})
        _CANONICAL_IDS[call_id] = int(row_id)
        _finish(call_id, "supervised-canonical")
        marker = item.get("supervised_marker", "skip")
        result = {"success": True, "response": text,
                  "agent_used": "shadow", "model": "shadow",
                  "provider": "shadow"}
        if marker == "coord":
            result["coordinator_response_message_id"] = int(row_id)
        else:
            result["skip_history_persist"] = True
        return result
    result = _result_shape(outcome, item.get("prompt", "shadow probe"),
                           item.get("response", ""))
    _finish(call_id, "completed")
    return result


def _extract_sid(args, kwargs):
    if "chat_session_id" in kwargs:
        return kwargs["chat_session_id"]
    if len(args) > 2:
        return args[2]  # kernel.run_agent_web_command(agent, prompt, sid)
    return kwargs.get("session_id")


def install_executor_fakes(default_scenario: str) -> None:
    """Replace owner call seams IN CHILD. Default 'blocked' raises on any
    execution attempt (a plain boot can never run a provider/CLI). The
    routing *decision* is stubbed too (direct target), so no routing-brain
    provider is consulted on the pipeline path."""
    if default_scenario not in SCENARIOS:
        raise ShadowError(f"unknown scenario: {default_scenario!r}")
    if default_scenario == "hold":
        raise ShadowError("default 'hold' is invalid: queue holds with ids")
    _DEFAULT_SCENARIO["name"] = default_scenario
    import importlib

    def _make_fake(mod_name: str, attr: str):
        def _fake(*a, **k):
            item = _take_queued()
            if item is None:
                if _DEFAULT_SCENARIO["name"] == "blocked":
                    _note("executor", f"{mod_name}.{attr}")
                    raise ShadowBlocked("shadow dev: executor seam blocked")
                with _QUEUE_LOCK:
                    call_id = f"d{_ID_COUNTER['n']}"
                    _ID_COUNTER["n"] += 1
                item = {"call_id": call_id,
                        "outcome": _DEFAULT_SCENARIO["name"]}
                _STATUSES[call_id] = "started"
                _STARTED[call_id] = _STARTED.get(call_id, 0) + 1
            prompt = ""
            if len(a) > 1 and isinstance(a[1], str):
                prompt = a[1]
            item.setdefault("prompt", prompt)
            return _run_one(item, _extract_sid(a, k), k)
        _fake.__name__ = attr
        return _fake

    saved_seams = []
    for mod_name, attr in SEAMS:
        mod = importlib.import_module(mod_name)
        saved_seams.append((mod, attr, getattr(mod, attr)))
        setattr(mod, attr, _make_fake(mod_name, attr))

    def _restore_seams():
        for mod, attr, original in saved_seams:
            setattr(mod, attr, original)
    _remember(_restore_seams)

    try:
        integration = importlib.import_module("api.agent_router.integration")
        _saved_decide = integration.decide
        _remember(lambda: setattr(integration, "decide", _saved_decide))
        from api.agent_router.config import load_router_config
        from api.agent_router.engine import default_decision

        _real_config = load_router_config()

        def _fake_decide(ctx, cfg):
            # Real constructor on the real provider boundary: no routing
            # brain is consulted, but the decision object is genuine.
            return default_decision(_real_config, "shadow deterministic")

        integration.decide = _fake_decide
        # should_invoke_router stays REAL (heuristic gating, no provider).
    except Exception as exc:
        raise ShadowError(f"decision stub failed: {exc}")


# ---------------------------------------------------------------------------
# Route allowlist gate (installed FIRST, ahead of app callbacks).
# ---------------------------------------------------------------------------

def install_route_allowlist(app) -> None:
    """before_request gate inserted at position 0 so it executes before the
    app's own callbacks: allowlisted B1 paths pass, everything else gets a
    stable side-effect-free 403."""

    def _shadow_gate():
        from flask import jsonify, request
        if request.path.startswith("/__shadow/"):
            return None
        if request.path == "/api/chat" and request.method == "POST":
            try:
                body = request.get_json(force=True, silent=True) or {}
                text = str(body.get("message", ""))
            except Exception:
                text = ""
            if text.lstrip().startswith(DENY_CHAT_PREFIXES):
                _note("route", f"denied chat payload {text[:40]!r}")
                return jsonify({"success": False,
                                "shadow_denied": "effectful payload"}), 403
        key = (request.method, request.path)
        if key in ALLOW_EXACT:
            return None
        if request.method == "PATCH" and ALLOW_PATCH_SESSION_RE.match(
                request.path):
            return None
        if request.method == "PUT" and ALLOW_PUT_SESSION_ATTENTION_RE.match(request.path):
            return None
        if request.method == "POST" and ALLOW_POST_SESSION_MESSAGE_RE.match(
                request.path):
            return None
        if request.method == "GET" and any(
                request.path.startswith(p) for p in ALLOW_GET_PREFIXES):
            return None
        _note("route", f"{request.method} {request.path}")
        return jsonify({"success": False,
                        "shadow_denied": f"{request.method} {request.path}"}), 403

    funcs = app.before_request_funcs.setdefault(None, [])
    funcs.insert(0, _shadow_gate)


def _control_state() -> dict:
    """Original `blocked_attempts` key with the FULL retained log (never
    truncated: earlier unexpected native/socket/route attempts must survive
    many expected profile denies) plus all five kind counters. "Retained
    full log" describes contents, not a field rename. No per-route ledger
    duplication."""
    return {"success": True,
            "started": dict(_STARTED),
            "completed": dict(_COMPLETED),
            "statuses": dict(_STATUSES),
            "blocked_attempts": list(_block_log),
            "blocked_counts": dict(_blocked_counts),
            "canonical_ids": dict(_CANONICAL_IDS)}


def _add_control_routes(app, nonce: str, codehash: str) -> None:
    """Bootstrap-only readiness/control (nonce on read AND write). NOT a
    production route."""

    @app.route("/__shadow/ready", methods=["GET"])
    def _shadow_ready():
        from flask import jsonify, request
        if request.headers.get("X-Shadow-Nonce") != nonce:
            return jsonify({"success": False}), 403
        return jsonify({"nonce": nonce, "codehash": codehash,
                        "pid": os.getpid()})

    @app.route("/__shadow/control", methods=["GET", "POST"])
    def _shadow_control():
        # S2 driver contract (CH860 owns the browser/HTTP tests):
        #   POST {queue: [{id, outcome, response, hold, status: [texts],
        #                 supervised_marker: 'skip'|'coord'}], release: [ids]}
        #   GET -> {started: {id: n}, completed: {id: n}, statuses: {...},
        #           blocked_attempts: [...], canonical_ids: {id: rowID}}
        # No cancel_mid_run/supersede_mid_run fake state: those are real HTTP
        # driver endpoints. `completed` counts fake returns, never route
        # finalization. Nonce required on read and write.
        from flask import jsonify, request
        if request.headers.get("X-Shadow-Nonce") != nonce:
            return jsonify({"success": False}), 403
        if request.method == "GET":
            return jsonify(_control_state())
        body = request.get_json(force=True, silent=True) or {}
        try:
            for item in body.get("queue", []):
                outcome = item.get("outcome", "success")
                if outcome not in OUTCOMES:
                    return jsonify({"success": False,
                                    "error": f"bad outcome {outcome!r}"}), 400
                if item.get("supervised_marker", "skip") not in ("skip",
                                                                  "coord"):
                    return jsonify({"success": False,
                                    "error": "bad supervised_marker"}), 400
                enqueue_item(item)
        except ShadowError as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        for call_id in body.get("release", []):
            event = _RELEASES.get(str(call_id))
            if event is not None:
                event.set()
        if "scenario" in body:
            if body["scenario"] not in SCENARIOS:
                return jsonify({"success": False}), 400
            _DEFAULT_SCENARIO["name"] = body["scenario"]
        return jsonify({"success": True, "queued": len(_QUEUE),
                        "started": dict(_STARTED),
                        "completed": dict(_COMPLETED)})


def _write_manifest_atomic(path: str, payload: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    os.replace(tmp, path)


def _verify_anchors(snapshot_src: str, anchors: dict) -> None:
    """Prove the loaded production modules are the candidate working bytes:
    each anchor module's __file__ must resolve inside the snapshot AND its
    bytes must hash to the parent-computed anchor."""
    import api.chat_coordinator as _cc
    import api.chat_turn_persist as _ctp
    import api.chat_turn_workflow as _ctw
    import api.web_chat_api as _wca

    by_rel = {
        "src/api/web_chat_api.py": _wca,
        "src/api/chat_turn_workflow.py": _ctw,
        "src/api/chat_turn_persist.py": _ctp,
        "src/api/chat_coordinator.py": _cc,
    }
    snap = os.path.abspath(snapshot_src)
    proven = {}
    for rel, mod in by_rel.items():
        path = getattr(mod, "__file__", "") or ""
        resolved = os.path.abspath(path)
        if not (resolved == snap or resolved.startswith(snap + os.sep)):
            raise ShadowError(f"anchor outside snapshot: {rel} -> {path}")
        with open(resolved, "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()
        if anchors.get(rel) != digest:
            raise ShadowError(f"anchor hash mismatch: {rel}")
        proven[rel] = {"path": resolved, "sha": digest}
    return proven


def main() -> int:
    get = os.environ.get
    snapshot = get("CUTTLE_SHADOW_SNAPSHOT", "")
    manifest_path = get("CUTTLE_SHADOW_MANIFEST", "")
    expected_hash = get("CUTTLE_SHADOW_CODEHASH", "")
    nonce = get("CUTTLE_SHADOW_NONCE", "")
    scenario = get("CUTTLE_SHADOW_SCENARIO", "blocked")
    fixed_port = int(get("CUTTLE_SHADOW_PORT", "0") or 0)
    try:
        anchors = json.loads(get("CUTTLE_SHADOW_ANCHORS", "{}") or "{}")
    except ValueError:
        anchors = {}
    if not (snapshot and manifest_path and expected_hash and nonce):
        print("shadow: missing CUTTLE_SHADOW_* bootstrap env", flush=True)
        return 2
    snapshot_src = os.path.join(snapshot, "src")
    if os.path.exists(os.path.join(snapshot_src, ".env")):
        print("shadow: live .env inside snapshot; refusing boot", flush=True)
        return 2
    denied_ports = _reserved_ports()
    if denied_ports is None:
        print("shadow: malformed CUTTLE_SHADOW_RESERVED_PORTS; refusing boot", flush=True)
        return 2
    if fixed_port in denied_ports:
        print(f"shadow: reserved port {fixed_port} refused", flush=True)
        return 2

    data = get("CUTTLE_SHADOW_DATA", "")
    log = get("CUTTLE_SHADOW_LOG", "")
    root = os.path.realpath(os.path.dirname(os.path.abspath(manifest_path)))
    home = get("CUTTLE_HOME", "")
    if not home or not os.path.realpath(home).startswith(root + os.sep):
        print("shadow: CUTTLE_HOME missing or outside the instance root; refusing boot", flush=True)
        return 2
    sock_state = install_socket_guard(fixed_port)
    install_subprocess_guard()
    _manifest_norm = os.path.normcase(os.path.abspath(manifest_path))
    _log_norm = os.path.normcase(os.path.abspath(log))
    # Ephemeral mutable snapshot: writes allowed across the ENTIRE private
    # instance root (app+data+logs) — never the source candidate or live
    # paths. No immutability claim on the snapshot.
    _extra_read = []
    _mime = "/etc/mime.types"
    if os.path.isfile(_mime):
        _extra_read.append(_mime)  # single explicit file, nothing broader
    install_audit_hook(
        root,
        [os.path.normcase(root)],
        {_manifest_norm, _log_norm},
        extra_read=_extra_read,
    )

    sys.path.insert(0, snapshot_src)
    from api.web_chat_api import app  # dev-shadow composition only (see note)

    # Parent-computed full fingerprint covers the pre-boot inventory; the
    # child verifies 4 anchor files by path+bytes (minimum B1 proof — no
    # full child-side hash recomputation is claimed). Config/DBs generated
    # after boot are never part of the code inventory.
    proven_anchors = _verify_anchors(snapshot_src, anchors)
    install_executor_fakes(scenario)
    install_route_allowlist(app)
    _add_control_routes(app, nonce, expected_hash)

    from werkzeug.serving import make_server
    server = make_server("127.0.0.1", fixed_port, app, threaded=True)
    host, bound_port = server.socket.getsockname()[:2]
    if bound_port in denied_ports:
        print(f"shadow: bound reserved port {bound_port}; refusing", flush=True)
        return 2
    sock_state.allowed_inet = (host, bound_port)

    _write_manifest_atomic(manifest_path, {
        "state": "ready", "nonce": nonce, "pid": os.getpid(),
        "port": bound_port, "codehash": expected_hash,
        "anchors": proven_anchors,
        "origin": f"http://127.0.0.1:{bound_port}",
        "log": log,
        "started_at": time.time(),
    })
    print(f"shadow ready on 127.0.0.1:{bound_port} pid={os.getpid()}",
          flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())

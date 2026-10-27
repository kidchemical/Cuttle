"""Per-run systemd resource scopes for agent CLIs (Linux user manager).

When explicitly enabled, a spawned agent CLI moves into its own transient user scope
(``cuttle-agent-<cli>-<pid>.scope`` in ``cuttle-agents.slice``) with memory
and task limits. A runaway turn is throttled or OOM-killed inside its scope;
the aggregate agent slice also has soft and hard memory ceilings. These
limits reduce host memory pressure; they do not guarantee OS stability.

Uses systemd's own D-Bus API through ``busctl`` (ships with systemd), so there
is no Python dependency. Best effort: off Linux, or with no reachable user
manager, CLIs run exactly as before. The CLI retains its native execution environment.

Opt in through Settings → Experimental → Agent resource limits (default off).
Machine setting ``agent_resource_limits`` customizes ceilings only; its legacy
``enabled`` field no longer activates limits. Off switches:
``CUTTLE_AGENT_SCOPES=0`` or ``CUTTLE_EXPERIMENTAL=0``. Limits apply to new launches.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
import time

SLICE = "cuttle-agents.slice"
SETTING_KEY = "agent_resource_limits"
DEFAULTS = {
    "enabled": False,
    "memory_high": "6G",       # per CLI: reclaim/throttle above this
    "memory_max": "8G",        # per CLI: hard cap, kernel OOM inside the scope only
    "memory_swap_max": "512M",
    "tasks_max": 4096,
    "slice_memory_high": "60%",  # all agents together, of physical RAM
    "slice_memory_max": "70%",   # leave headroom for the desktop and host
}
_SUFFIX = {"K": 1 << 10, "M": 1 << 20, "G": 1 << 30, "T": 1 << 40}

_lock = threading.Lock()
_confined: set[int] = set()  # in-flight moves only
_slice_limits: tuple | None = None
_reported: set[str] = set()


def parse_size(value, total: int | None = None) -> int | None:
    """``"6G"`` / ``"512M"`` / ``4096`` / ``"60%"`` → bytes; blank/none → None."""
    if value is None or value == "" or value is False:
        return None
    if isinstance(value, bool):
        raise ValueError(f"invalid size: {value!r}")
    if isinstance(value, (int, float)):
        if value < 0:
            raise ValueError(f"invalid size: {value!r}")
        return int(value)
    text = str(value).strip().upper()
    if text in ("NONE", "INFINITY"):
        return None
    if text.endswith("%"):
        percent = float(text[:-1])
        if not 0 < percent <= 100:
            raise ValueError(f"invalid size: {value!r}")
        if not total:
            return None
        return int(total * percent / 100)
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([KMGT])?I?B?", text)
    if not m:
        raise ValueError(f"invalid size: {value!r}")
    return int(float(m.group(1)) * _SUFFIX.get(m.group(2) or "", 1))


def resolve_limits(raw, env=None, total_memory: int | None = None) -> dict | None:
    """Merge the machine setting over defaults; None when disabled."""
    env = os.environ if env is None else env
    if str(env.get("CUTTLE_AGENT_SCOPES", "")).strip().lower() in ("0", "false", "off", "no"):
        return None
    merged = {**DEFAULTS, **(raw if isinstance(raw, dict) else {})}
    if merged.get("enabled") is not True:
        return None
    return {
        "memory_high": parse_size(merged.get("memory_high"), total_memory),
        "memory_max": parse_size(merged.get("memory_max"), total_memory),
        "memory_swap_max": parse_size(merged.get("memory_swap_max"), total_memory),
        "tasks_max": parse_size(merged.get("tasks_max")),
        "slice_memory_high": parse_size(merged.get("slice_memory_high"), total_memory),
        "slice_memory_max": parse_size(merged.get("slice_memory_max"), total_memory),
    }


def unit_name(comm: str, pid: int) -> str:
    label = re.sub(r"[^A-Za-z0-9_-]+", "-", comm or "cli").strip("-")[:32] or "cli"
    return f"cuttle-agent-{label}-{pid}.scope"


def _props(limits: dict) -> list[tuple[str, str, str]]:
    props = []
    for key, prop in (("memory_high", "MemoryHigh"), ("memory_max", "MemoryMax"),
                      ("memory_swap_max", "MemorySwapMax"), ("tasks_max", "TasksMax")):
        if limits.get(key) is not None:
            props.append((prop, "t", str(int(limits[key]))))
    return props


def start_scope_argv(unit: str, pids: list[int], limits: dict, description: str) -> list[str]:
    """busctl argv for Manager.StartTransientUnit moving ``pids`` into ``unit``."""
    props = [("Description", "s", description), ("Slice", "s", SLICE),
             ("CollectMode", "s", "inactive-or-failed"), ("OOMPolicy", "s", "kill"),
             *_props(limits)]
    argv = ["busctl", "--user", "call", "org.freedesktop.systemd1", "/org/freedesktop/systemd1",
            "org.freedesktop.systemd1.Manager", "StartTransientUnit", "ssa(sv)a(sa(sv))",
            unit, "fail", str(len(props) + 1), "PIDs", "au", str(len(pids)), *map(str, pids)]
    for name, sig, value in props:
        argv += [name, sig, value]
    return argv + ["0"]


def slice_argv(limits: dict) -> list[str]:
    # UINT64_MAX is systemd's infinity value; explicitly clear prior ceilings.
    props = [
        (prop, str(int(limits[key])) if limits.get(key) is not None else str((1 << 64) - 1))
        for key, prop in (("slice_memory_high", "MemoryHigh"), ("slice_memory_max", "MemoryMax"))
    ]
    return ["busctl", "--user", "call", "org.freedesktop.systemd1", "/org/freedesktop/systemd1",
            "org.freedesktop.systemd1.Manager", "SetUnitProperties", "sba(sv)",
            SLICE, "true", str(len(props)),
            *[item for prop, value in props for item in (prop, "t", value)]]


def _descendants(pid: int) -> list[int]:
    found, stack, seen = [], [pid], {pid}
    while stack:
        cur = stack.pop()
        try:
            tids = os.listdir(f"/proc/{cur}/task")
        except OSError:
            continue
        for tid in tids:
            try:
                with open(f"/proc/{cur}/task/{tid}/children") as fh:
                    kids = [int(x) for x in fh.read().split()]
            except (OSError, ValueError):
                continue
            for kid in kids:
                if kid not in seen:
                    seen.add(kid)
                    found.append(kid)
                    stack.append(kid)
    return found


def _comm(pid: int) -> str:
    try:
        with open(f"/proc/{pid}/comm") as fh:
            return fh.read().strip()
    except OSError:
        return "cli"


def _total_memory() -> int | None:
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return None


def _env() -> dict:
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return env


def _report(key: str, message: str) -> None:
    if key in _reported:
        return
    _reported.add(key)
    print(f"[AGENT-SCOPE] {message}", file=sys.stderr, flush=True)


def _load_limits() -> dict | None:
    from api.experimental import is_enabled

    if not is_enabled(SETTING_KEY):
        return None
    try:
        from managers.settings_manager import get_settings_manager
        raw = get_settings_manager().get_setting(SETTING_KEY)
    except Exception:
        raw = None
    # The experimental flag owns activation, including for old machine configs.
    raw = {**(raw if isinstance(raw, dict) else {}), "enabled": True}
    try:
        return resolve_limits(raw, total_memory=_total_memory())
    except ValueError as exc:
        _report("config", f"invalid {SETTING_KEY}: {exc}; using defaults")
        return resolve_limits(None, total_memory=_total_memory())


def _run(argv: list[str]) -> tuple[bool, str]:
    try:
        cp = subprocess.run(argv, capture_output=True, text=True, timeout=5, env=_env())
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    return cp.returncode == 0, (cp.stderr or cp.stdout or "").strip()


def _already_scoped(pid: int, unit: str) -> bool:
    """True when ``pid`` already sits in its own scope (or has exited).

    A sub-agent CLI spawned from inside another agent's scope still gets its own.
    """
    try:
        with open(f"/proc/{pid}/cgroup") as fh:
            return any(line.rstrip().endswith(f"/{unit}") for line in fh)
    except OSError:
        return True


def _confine(pid: int) -> bool:
    try:
        return _confine_once(pid)
    finally:
        with _lock:
            _confined.discard(pid)


def _confine_once(pid: int) -> bool:
    global _slice_limits
    limits = _load_limits()
    if limits is None:
        return False
    comm = _comm(pid)
    unit = unit_name(comm, pid)
    if _already_scoped(pid, unit):
        return True
    ok, err = _run(start_scope_argv(unit, [pid, *_descendants(pid)], limits, f"Cuttle agent CLI {comm} (pid {pid})"))
    if not ok:
        if "No such process" not in err:  # CLI already exited; nothing to confine
            _report("start", f"could not create resource scope; running unconfined: {err[:300]}")
        return False
    # StartTransientUnit returns a queued job, not proof that PID migration
    # already completed. Wait briefly before returning control to prompt I/O.
    deadline = time.monotonic() + 1
    while not _already_scoped(pid, unit):
        if time.monotonic() >= deadline:
            _report("migration", "systemd accepted the scope but PID migration was not confirmed")
            return False
        time.sleep(.01)
    _report("ok", f"agent CLIs run in per-run scopes under {SLICE} (first: {unit})")
    requested = (limits.get("slice_memory_high"), limits.get("slice_memory_max"))
    with _lock:
        argv = slice_argv(limits) if requested != _slice_limits else None
        if argv:
            ok, err = _run(argv)
            if not ok:
                _report("slice", f"could not set {SLICE} limits: {err[:300]}")
                return False
            _slice_limits = requested
    return True


def confine(proc) -> bool:
    """Attach before the caller sends input; no background migration threads.

    False means isolation was disabled or unavailable. This does not change the
    executable, environment, credentials, filesystem, network or vendor protocol.
    """
    if (not sys.platform.startswith("linux") or proc is None
            or not os.path.isfile("/sys/fs/cgroup/cgroup.controllers")
            or not shutil.which("busctl")):
        return False
    if _load_limits() is None:
        return False
    try:
        pid = int(getattr(proc, "pid", 0) or 0)
    except (TypeError, ValueError):
        return False
    if pid <= 1 or pid == os.getpid():
        return False
    with _lock:
        if pid in _confined:
            return False
        _confined.add(pid)
    return _confine(pid)

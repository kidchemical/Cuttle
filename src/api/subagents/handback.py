"""Hand a finished ``--wait`` round back to a parent that stopped listening.

``spawn``/``message``/``wait --wait`` print the round's result to whoever ran
them, normally the parent chat's agent CLI. When that CLI is gone by the time
the round ends (a Claude turn hit its background-wait limit, the turn timed
out, Flask restarted under it), nobody reads the output and the parent never
hears that its children finished. The CLI notes its *listener* when it starts
(the nearest ancestor that is not a shell wrapper) and, if that process is gone
at the end, queues a Cuttle-origin ``notice`` turn on the parent chat through
the ``api.chat_ops`` outbox with each child's handle, status and reply. The host
dispatches it once the parent is idle, so the parent agent continues on its own.

A listener that is still alive gets the result the normal way (stdout, or
Claude Code's own background-task notification), so nothing is posted twice.

A CLI launched *detached* (a ``systemd-run`` unit, or re-parented to init) has
no listener by construction: its parent is the service manager, which never
exits, so "is the listener gone" would stay false for good and the parent chat
would never hear of the round (CH-002353, 2026-11-12: integrator children that
finished or hit ``--timeout`` in a unit left the orchestrator idle until its
supervisor's stall check). Such a run always hands back, unless the agent
registered a ``job_watch watch-unit`` on that unit, which resumes the chat itself.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

# Process names between this CLI and the agent that ran it (Claude Code runs
# ``bash -c``; users wrap with timeout/nohup/uv …).
_WRAPPERS = frozenset({
    "sh", "bash", "zsh", "dash", "fish", "ksh", "tcsh", "csh", "busybox", "pwsh", "powershell",
    "cmd", "conhost", "timeout", "env", "nohup", "setsid", "stdbuf", "nice", "ionice", "time",
    "uv", "sudo", "doas", "script", "tee", "xargs",
})
# A CLI whose nearest non-wrapper ancestor is one of these was detached from the
# agent that launched it: nobody reads its stdout.
_SERVICE_MANAGERS = frozenset({"systemd", "init", "launchd"})
REPLY_CHARS = 2500
MAX_CHARS = 19000


def capture_listener() -> Optional[Dict[str, Any]]:
    """The process that will read this CLI's output: ``{pid, create_time, name}``;
    ``{detached: True, name}`` when a service manager started it (nobody reads
    it); None when unknown."""
    try:
        import psutil

        for parent in psutil.Process().parents():
            name = (parent.name() or "").lower()
            name = name[:-4] if name.endswith(".exe") else name
            if name in _WRAPPERS:
                continue
            if int(parent.pid) <= 1 or name in _SERVICE_MANAGERS:
                return {"detached": True, "name": name}  # a unit, or orphaned to init
            return {"pid": int(parent.pid), "create_time": float(parent.create_time()), "name": name}
    except Exception:
        return None
    return None


def listener_gone(listener: Optional[Dict[str, Any]]) -> bool:
    if not listener:
        return False
    if listener.get("detached"):
        return True  # never had one
    try:
        import psutil
    except Exception:
        return False
    try:
        proc = psutil.Process(int(listener["pid"]))
        if abs(proc.create_time() - float(listener["create_time"])) > 1.0:
            return True  # pid reused
        return proc.status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True
    except Exception:
        return False


def _own_unit() -> str:
    """The systemd unit this process runs in (``name`` without ``.service``), or ''."""
    try:
        with open("/proc/self/cgroup", encoding="utf-8") as fh:
            leaf = fh.read().strip().splitlines()[-1].rsplit("/", 1)[-1]
    except (OSError, IndexError):
        return ""
    return leaf[: -len(".service")] if leaf.endswith(".service") else ""


def _unit_watch_resumes(chat_session_id: int) -> bool:
    """Whether a pending ``watch-unit`` on this process (its unit or pid) will
    resume the chat when it exits, so a notice would wake it twice."""
    try:
        from api.job_watch import unit_watch_records

        unit, pid = _own_unit(), os.getpid()
        for record in unit_watch_records(chat_session_id):
            watched = str(record.get("unit") or "")
            watched = watched[: -len(".service")] if watched.endswith(".service") else watched
            if (unit and watched == unit) or int(record.get("pid") or 0) == pid:
                return True
    except Exception:
        return False
    return False


def _excerpt(text: str, limit: int = REPLY_CHARS) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def notice_text(batch: Dict[str, Any], *, verb: str = "spawn", timed_out: bool = False,
                focus: Optional[str] = None, detached: bool = False) -> str:
    bid = batch.get("id") or "?"
    where = "ran detached from your turn" if detached else "after your turn stopped listening"
    if timed_out:
        head = (f"Sub-agent batch {bid} (`subagents {verb} --wait`) hit its --timeout "
                f"{'and ' + where if detached else where}; its unfinished work was cancelled. "
                f"Status: {batch.get('status')}.")
    else:
        head = (f"Sub-agent batch {bid} (`subagents {verb} --wait`) "
                f"{where + ' and has finished' if detached else 'finished ' + where}, "
                f"so its result is delivered here. Status: {batch.get('status')}.")
    parts = [head, ""]
    for child in batch.get("children") or []:
        if focus and child.get("handle") != focus:
            continue
        who = " ".join(x for x in (child.get("agent"), child.get("model")) if x)
        parts.append(f"- {child.get('handle')} {child.get('label') or ''} ({who}): {child.get('status')}".rstrip())
        body = child.get("error") or child.get("result")
        if body:
            parts.append(f"  {_excerpt(body)}")
    parts += ["", f"Full replies: open the handles, or `python -m api.subagents status --batch {bid} --json`.",
              "Continue the task with these results."]
    return "\n".join(parts)[:MAX_CHARS]


def hand_back(listener: Optional[Dict[str, Any]], batch: Optional[Dict[str, Any]], *, verb: str = "spawn",
              timed_out: bool = False, focus: Optional[str] = None,
              notify_session: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Queue the parent notice when the listener is gone (or the CLI ran detached
    and no unit watch resumes the chat for it); the chat-ops op, or None."""
    if not batch or not listener_gone(listener):
        return None
    detached = bool(listener and listener.get("detached"))
    target = notify_session or batch.get("parent_session_id")
    if not target:
        env = os.environ.get("CUTTLE_CHAT_SESSION_ID") or ""
        target = int(env) if env.strip().isdigit() else None
    if not target:
        return None
    from api.chat_ops import store

    sid = int(target)
    if detached and _unit_watch_resumes(sid):
        return None
    return store.create("notice", sid, sid, {
        "text": notice_text(batch, verb=verb, timed_out=timed_out, focus=focus, detached=detached),
        "label": "Sub-agents finished",
    })

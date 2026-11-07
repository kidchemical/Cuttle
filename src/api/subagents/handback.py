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
REPLY_CHARS = 2500
MAX_CHARS = 19000


def capture_listener() -> Optional[Dict[str, Any]]:
    """The process that will read this CLI's output: ``{pid, create_time, name}``, or None."""
    try:
        import psutil

        for parent in psutil.Process().parents():
            name = (parent.name() or "").lower()
            name = name[:-4] if name.endswith(".exe") else name
            if name in _WRAPPERS:
                continue
            if int(parent.pid) <= 1:
                return None  # already orphaned: nobody to compare against
            return {"pid": int(parent.pid), "create_time": float(parent.create_time()), "name": name}
    except Exception:
        return None
    return None


def listener_gone(listener: Optional[Dict[str, Any]]) -> bool:
    if not listener:
        return False
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


def _excerpt(text: str, limit: int = REPLY_CHARS) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def notice_text(batch: Dict[str, Any], *, verb: str = "spawn", timed_out: bool = False,
                focus: Optional[str] = None) -> str:
    bid = batch.get("id") or "?"
    if timed_out:
        head = (f"Sub-agent batch {bid} (`subagents {verb} --wait`) hit its --timeout after your turn "
                f"stopped listening; its unfinished work was cancelled. Status: {batch.get('status')}.")
    else:
        head = (f"Sub-agent batch {bid} (`subagents {verb} --wait`) finished after your turn stopped "
                f"listening, so its result is delivered here. Status: {batch.get('status')}.")
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
    """Queue the parent notice when the listener is gone; the chat-ops op, or None."""
    if not batch or not listener_gone(listener):
        return None
    target = notify_session or batch.get("parent_session_id")
    if not target:
        env = os.environ.get("CUTTLE_CHAT_SESSION_ID") or ""
        target = int(env) if env.strip().isdigit() else None
    if not target:
        return None
    from api.chat_ops import store

    sid = int(target)
    return store.create("notice", sid, sid, {
        "text": notice_text(batch, verb=verb, timed_out=timed_out, focus=focus),
        "label": "Sub-agents finished",
    })

"""
Guard against agent shell commands that terminate Cuttle-managed processes.

Hard enforcement exists for:
  - Cursor Agent CLI via project ``beforeShellExecution`` hook

Pipeline ``tools.shell`` nodes are retired. Codex / Hermes / Claude Code do **not**
expose a Cuttle-owned pre-shell intercept today — those get advisory policy
injection only.

Authorization model
-------------------
Agent-facing guards **never** trust environment variables (including
``CUTTLE_INTERNAL_RESTART``). Spoofing ``$env:CUTTLE_INTERNAL_RESTART=1`` in a
shell command or passing it via ``shell_manager`` ``env=`` must not allow a kill.

The daemon bypasses these guards by construction: it calls ``taskkill`` from
Python in ``cuttle_daemon.py``, not via Cursor hooks or ``shell_manager``.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

DENY_MESSAGE = (
    "Direct termination of Cuttle-managed processes is blocked.\n"
    "Use /restart graceful or /restart when-idle."
)

# Command-line fragments that identify daemon-managed processes.
DEFAULT_MANAGED_FRAGMENTS = (
    "web_chat_api",
    "cuttle_daemon",
    "discord_bot.py",
    "discord_bot",
    "bot_mcp.py",
    "bot_mcp",
)

# Deprecated spoofable marker — kept only so tests prove it is ignored.
LEGACY_SPOOFABLE_ENV = "CUTTLE_INTERNAL_RESTART"

_PID_FLAGS = re.compile(
    r"(?i)(?:/PID|[-/]Id|ProcessId\s*=)\s*:?\s*(\d+)"
)
_PID_LIST = re.compile(r"(?i)/PID\s+(\d+(?:\s+/PID\s+\d+)*)")
_STOP_PROCESS_ID = re.compile(
    r"(?i)Stop-Process\b[^\n;|]*?(?:-Id|-PID)\s+(\d+(?:\s*,\s*\d+)*)"
)
_TASKKILL = re.compile(r"(?i)\btaskkill\b")
_STOP_PROCESS = re.compile(r"(?i)\bStop-Process\b")
_WMIC_DELETE = re.compile(r"(?i)\bwmic\b.*\bprocess\b.*\b(?:delete|call\s+terminate)\b")
_FILTER_MANAGED = re.compile(
    r"(?i)(?:web_chat_api|cuttle_daemon|discord_bot\.py|discord_bot|bot_mcp\.py|bot_mcp)"
)
_WHERE_OBJECT_KILL = re.compile(
    r"(?i)Where-Object[^\n]*CommandLine[^\n]*(?:web_chat_api|cuttle_daemon|discord_bot|bot_mcp)"
    r"[^\n]*(?:taskkill|Stop-Process|kill)"
)
_GET_CIM_KILL = re.compile(
    r"(?i)Get-CimInstance[^\n]*(?:web_chat_api|cuttle_daemon|discord_bot|bot_mcp)[^\n]*"
    r"(?:taskkill|Stop-Process|ForEach-Object)"
)
_TASKKILL_IM_FILTER = re.compile(
    r"(?i)taskkill\b[^\n]*/(?:IM|im)\s+python\.exe[^\n]*/(?:FI|fi)[^\n]*"
    r"(?:web_chat_api|cuttle_daemon|discord_bot|bot_mcp|WINDOWTITLE\s+eq\s+web_chat)"
)


def list_managed_python_pids(
    *,
    fragments: Sequence[str] = DEFAULT_MANAGED_FRAGMENTS,
) -> Dict[int, str]:
    """Best-effort live PID → cmdline for managed processes (Windows)."""
    out: Dict[int, str] = {}
    if sys.platform != "win32":
        return out
    try:
        import subprocess

        frag_filter = " -or ".join(
            f"$_.CommandLine -like '*{f}*'" for f in fragments
        )
        ps = (
            "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
            f"Where-Object {{ {frag_filter} }} | "
            "ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }"
        )
        r = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                ps,
            ],
            capture_output=True,
            text=True,
            timeout=12,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        for line in (r.stdout or "").splitlines():
            line = line.strip()
            if not line or "\t" not in line:
                continue
            pid_s, cmd = line.split("\t", 1)
            if pid_s.isdigit():
                out[int(pid_s)] = cmd
    except Exception:
        pass
    return out


def extract_target_pids(command: str) -> Set[int]:
    pids: Set[int] = set()
    for m in _PID_FLAGS.finditer(command or ""):
        pids.add(int(m.group(1)))
    for m in _PID_LIST.finditer(command or ""):
        for part in re.findall(r"\d+", m.group(1)):
            pids.add(int(part))
    for m in _STOP_PROCESS_ID.finditer(command or ""):
        for part in re.findall(r"\d+", m.group(1)):
            pids.add(int(part))
    return pids


def looks_like_process_termination(command: str) -> bool:
    c = command or ""
    if _TASKKILL.search(c) or _STOP_PROCESS.search(c) or _WMIC_DELETE.search(c):
        return True
    if re.search(r"(?i)\b(?:kill|taskkill)\b", c) and re.search(r"(?i)/PID|-Id|ProcessId", c):
        return True
    return False


def evaluate_shell_command(
    command: str,
    *,
    managed_pids: Optional[Iterable[int]] = None,
    managed_cmdlines: Optional[Dict[int, str]] = None,
    env: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Return ``{allow: bool, reason?, denial?, matched?}``.

    ``env`` is accepted for API compatibility with shell_manager but is **never**
    used as authorization. Spoofed ``CUTTLE_INTERNAL_RESTART`` is ignored.
    """
    # Explicitly ignore spoofable env markers (document for auditors / tests).
    _ = env  # noqa: F841 — intentionally unused for auth

    cmd = command or ""
    if not looks_like_process_termination(cmd) and not _FILTER_MANAGED.search(cmd):
        return {"allow": True, "matched": False}

    # Pattern-based: killing by cmdline filter without needing live PIDs
    if (
        _WHERE_OBJECT_KILL.search(cmd)
        or _GET_CIM_KILL.search(cmd)
        or _TASKKILL_IM_FILTER.search(cmd)
    ):
        return {
            "allow": False,
            "matched": True,
            "denial": DENY_MESSAGE,
            "reason": "managed_cmdline_filter_kill",
        }

    # taskkill/Stop-Process that mentions managed fragments + termination
    if _FILTER_MANAGED.search(cmd) and looks_like_process_termination(cmd):
        return {
            "allow": False,
            "matched": True,
            "denial": DENY_MESSAGE,
            "reason": "managed_name_in_kill_command",
        }

    live = managed_cmdlines
    if live is None and managed_pids is None:
        live = list_managed_python_pids()
    managed: Set[int] = set(int(p) for p in (managed_pids or []))
    if live:
        managed |= set(int(p) for p in live.keys())

    targets = extract_target_pids(cmd)
    hit = sorted(targets & managed)
    if hit and looks_like_process_termination(cmd):
        return {
            "allow": False,
            "matched": True,
            "denial": DENY_MESSAGE,
            "reason": "managed_pid_kill",
            "pids": hit,
        }

    return {"allow": True, "matched": False}


def guard_or_raise(command: str, **kwargs) -> None:
    """Raise PermissionError if the command targets managed processes."""
    result = evaluate_shell_command(command, **kwargs)
    if not result.get("allow", True):
        raise PermissionError(result.get("denial") or DENY_MESSAGE)


def hook_decision_from_stdin(stdin_text: str) -> Dict[str, Any]:
    """Parse Cursor beforeShellExecution stdin JSON → permission decision."""
    try:
        payload = json.loads(stdin_text or "{}")
    except json.JSONDecodeError:
        payload = {}
    command = str(payload.get("command") or "")
    # Never consult os.environ / payload env for authorization.
    result = evaluate_shell_command(command, env=None)
    if result.get("allow", True):
        return {"permission": "allow"}
    return {
        "permission": "deny",
        "user_message": DENY_MESSAGE,
        "agent_message": DENY_MESSAGE,
    }


def main() -> int:
    """Cursor hook entry: read stdin, print JSON decision."""
    raw = sys.stdin.read()
    decision = hook_decision_from_stdin(raw)
    sys.stdout.write(json.dumps(decision))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

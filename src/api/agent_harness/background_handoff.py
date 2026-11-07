"""Hand-off of background work a turn stopped waiting on.

A Claude Code turn that detaches (``api.agent_harness.background_wait``) waits
at most ``CLAUDE_BACKGROUND_WAIT_SEC`` for its background tasks; at that cap the
CLI's stdin closes and the CLI is reaped. Claude Code runs each background
shell in its own session with stdout/stderr on a task output file, so that
work does not need the CLI and keeps running. This module makes sure it is
neither killed nor forgotten:

* ``output_paths`` reads the task output files out of the CLI's stream (the
  ``run_in_background`` tool result names them) and ``task_processes`` finds
  the processes writing them, so the reap spares them (``kill_process_tree``
  ``keep``).
* ``start`` records the hand-off under ``<home>/background_handoff/`` and
  launches a small detached watcher (``python -m
  api.agent_harness.background_handoff watch <record>``) that outlives Flask
  restarts.
* When a task's processes are gone the watcher queues a Cuttle-origin
  ``notice`` turn on the chat through the ``api.chat_ops`` outbox (the host
  dispatches it once the chat is idle): the task's label, output file and the
  tail of its output, like Claude Code's own task notification. The agent picks
  the result up without the user asking.

``api.subagents`` ``--wait`` runs are spared but not watched here: their CLI
hands the batch back to the parent itself (``api.subagents.handback``), with
the children's handles and replies, which also covers a parent CLI that died
for any other reason.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

POLL_SEC = 5.0
MAX_WATCH_SEC = 72 * 3600.0
TAIL_CHARS = 3000
_OUTPUT_RE = re.compile(r"ID:\s*([A-Za-z0-9_-]+)\.\s*Output is being written to:\s*([^\s\"\\]+?\.output)")
_SUBAGENT_WAIT = re.compile(r"api\.subagents\b.*\s--wait\b")


def output_paths(raw: Any) -> Dict[str, str]:
    """``task_id -> output file`` named by a ``run_in_background`` tool result line."""
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    text = str(raw or "")
    if "Output is being written to" not in text:
        return {}
    return {m.group(1): m.group(2) for m in _OUTPUT_RE.finditer(text)}


def guess_output_file(task_id: str, session_id: Optional[str]) -> str:
    """Claude Code's task file when the stream never named it (``<tmp>/claude-*/…/<session>/tasks``)."""
    if not (task_id and session_id):
        return ""
    for base in {tempfile.gettempdir(), "/tmp"}:
        for hit in Path(base).glob(f"claude-*/*/{session_id}/tasks/{task_id}.output"):
            return str(hit)
    return ""


def _writes_to(proc: Any, path: str) -> bool:
    target = os.path.realpath(path)
    if os.path.isdir(f"/proc/{proc.pid}/fd"):
        for fd in (1, 2):
            try:
                if os.path.realpath(os.readlink(f"/proc/{proc.pid}/fd/{fd}")) == target:
                    return True
            except OSError:
                continue
        return False
    try:
        return any(os.path.realpath(f.path) == target for f in proc.open_files())
    except Exception:
        return False


def task_processes(cli_pid: Optional[int], output_file: str) -> List[Dict[str, Any]]:
    """Top-most descendants of the CLI writing ``output_file``: ``[{pid, create_time, cmdline}]``."""
    if not (cli_pid and output_file):
        return []
    try:
        import psutil

        procs = psutil.Process(int(cli_pid)).children(recursive=True)
    except Exception:
        return []
    hits = [p for p in procs if _writes_to(p, output_file)]
    pids = {p.pid for p in hits}
    out = []
    for proc in hits:
        try:
            if proc.ppid() in pids:
                continue
            cmd = " ".join(proc.cmdline())
            out.append({"pid": int(proc.pid), "create_time": float(proc.create_time()), "cmdline": cmd})
        except Exception:
            continue
    return out


def collect(cli_pid: Optional[int], tasks: List[Dict[str, Any]], outputs: Dict[str, str],
            session_id: Optional[str], label_of: Any) -> List[Dict[str, Any]]:
    """Hand-off entries for the CLI's still-listed background tasks (at the wait cap)."""
    found = []
    for task in tasks:
        task_id = str(task.get("task_id") or "")
        path = outputs.get(task_id) or guess_output_file(task_id, session_id)
        procs = task_processes(cli_pid, path)
        found.append({
            "task_id": task_id,
            "label": label_of(task),
            "output_file": path,
            "procs": [{"pid": p["pid"], "create_time": p["create_time"]} for p in procs],
            "handback": "subagents" if any(_SUBAGENT_WAIT.search(p["cmdline"]) for p in procs) else "",
        })
    return found


def keep_pids(entries: List[Dict[str, Any]]) -> List[int]:
    return [p["pid"] for e in entries for p in e.get("procs") or []]


def _alive(proc: Dict[str, Any]) -> bool:
    try:
        import psutil

        p = psutil.Process(int(proc["pid"]))
        if abs(p.create_time() - float(proc["create_time"])) > 1.0:
            return False  # pid reused
        return p.status() != psutil.STATUS_ZOMBIE
    except Exception:
        return False


def running(entry: Dict[str, Any]) -> bool:
    return any(_alive(p) for p in entry.get("procs") or [])


def records_dir() -> Path:
    from core.runtime_paths import runtime_state_path

    return runtime_state_path("background_handoff")


def start(chat_session_id: Any, entries: List[Dict[str, Any]], *, waited_sec: float,
          agent: str = "claude", origin: str = "") -> Optional[Path]:
    """Record still-running entries and launch their watcher; the record path, or None."""
    watched = [e for e in entries if not e.get("handback") and running(e)]
    if not watched or not str(chat_session_id or "").strip():
        return None
    try:
        from api.cuttle_ui_capabilities import numeric_chat_session_id

        sid = numeric_chat_session_id(chat_session_id)
    except Exception:
        sid = None
    if sid is None:
        return None
    record = {
        "schema_version": 1,
        "id": f"{agent}-{sid}-{secrets.token_hex(3)}",
        "chat_session_id": int(sid),
        "agent": agent,
        "origin": origin,
        "waited_sec": float(waited_sec),
        "created_ts": time.time(),
        "tasks": [{**e, "notified": False} for e in watched],
    }
    path = records_dir() / f"{record['id']}.json"
    _save(path, record)
    try:
        _spawn_watcher(path, int(sid))
    except Exception as exc:
        print(f"[background_handoff] watcher launch failed: {exc}", flush=True)
        path.unlink(missing_ok=True)
        return None
    return path


def _spawn_watcher(path: Path, chat_session_id: int) -> None:
    src = str(Path(__file__).resolve().parents[2])
    env = dict(os.environ)
    env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    # Shows under the chat in Running now (api.jobs.processes), where End works.
    env["CUTTLE_CHAT_SESSION_ID"] = str(chat_session_id)
    kwargs: Dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        )
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(
        [sys.executable, "-m", "api.agent_harness.background_handoff", "watch", str(path)],
        cwd=src, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, **kwargs,
    )


def _save(path: Path, record: Dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _tail(path: str, limit: int = TAIL_CHARS) -> str:
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - limit * 4))
            text = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text).strip()
    return ("…" + text[-limit:]) if len(text) > limit else text


def notice_text(record: Dict[str, Any], finished: List[Dict[str, Any]], *, gave_up: bool = False) -> str:
    waited = int(float(record.get("waited_sec") or 0) // 60)
    if gave_up:
        head = (f"Cuttle stopped watching background work from an earlier turn after "
                f"{int(MAX_WATCH_SEC // 3600)} h; it is still running:")
    else:
        head = (f"Background work from an earlier turn finished. That turn stopped waiting on it "
                f"after {waited} min, so this is its completion notice (exit status not available):")
    parts = [head]
    for task in finished:
        line = f"- `{task.get('label') or task.get('task_id')}`"
        if task.get("task_id"):
            line += f" (task {task['task_id']})"
        parts.append(line)
        if task.get("output_file"):
            parts.append(f"  Output file: {task['output_file']}")
            tail = "" if gave_up else _tail(task["output_file"])
            if tail:
                fence = "````" if "```" in tail else "```"
                parts.append(f"  Last output:\n{fence}\n{tail}\n{fence}")
    parts.append("" if gave_up else "Continue the task with this result.")
    return "\n".join(parts).strip()[:19000]


def post_notice(chat_session_id: int, text: str, label: str) -> Dict[str, Any]:
    """Queue a Cuttle-origin turn on the chat (``api.chat_ops`` outbox, dispatched when idle)."""
    from api.chat_ops import store

    sid = int(chat_session_id)
    return store.create("notice", sid, sid, {"text": text, "label": label})


def check(path: Path, *, now: Optional[float] = None) -> bool:
    """One watcher pass: notify finished tasks; True once nothing is left to watch."""
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    pending = [t for t in record.get("tasks") or [] if not t.get("notified")]
    finished = [t for t in pending if not running(t)]
    gave_up = not finished and (now or time.time()) - float(record.get("created_ts") or 0) > MAX_WATCH_SEC
    batch = pending if gave_up else finished
    if batch:
        label = "Background work: stopped watching" if gave_up else "Background work finished"
        post_notice(record["chat_session_id"], notice_text(record, batch, gave_up=gave_up), label)
        for task in batch:
            task["notified"] = True
        _save(path, record)
    if all(t.get("notified") for t in record.get("tasks") or []):
        path.unlink(missing_ok=True)
        return True
    return False


def watch(path: Path, *, poll: float = POLL_SEC) -> None:
    while True:
        try:
            if check(path):
                return
        except Exception as exc:  # keep watching: the outbox may be briefly locked
            print(f"[background_handoff] check failed: {exc}", file=sys.stderr, flush=True)
        time.sleep(poll)


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2 or args[0] != "watch":
        print("usage: python -m api.agent_harness.background_handoff watch <record.json>", file=sys.stderr)
        return 2
    watch(Path(args[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

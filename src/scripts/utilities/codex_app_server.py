"""One-shot Codex app-server JSON-RPC helpers (stdio).

Used for real session compaction via ``thread/compact/start`` and restored token
usage. Steerable chat turns live in ``codex_app_server_turn``; ``codex exec
--json`` remains the fallback turn path.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from typing import Any, Dict, Optional

from scripts.utilities.codex_cli_tool import codex_executable

_DEFAULT_TIMEOUT_SEC = 180.0


def _drain_stderr(proc: subprocess.Popen) -> None:
    try:
        if not proc.stderr:
            return
        for _ in proc.stderr:
            pass
    except Exception:
        pass


def _send(proc: subprocess.Popen, msg: Dict[str, Any]) -> None:
    if not proc.stdin:
        raise RuntimeError("app-server stdin closed")
    line = (json.dumps(msg, separators=(",", ":")) + "\n").encode("utf-8")
    proc.stdin.write(line)
    proc.stdin.flush()


def _reader(proc: subprocess.Popen, out_q: "queue.Queue[Optional[Dict[str, Any]]]") -> None:
    try:
        if not proc.stdout:
            return
        for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                out_q.put(obj)
    finally:
        out_q.put(None)


def _token_usage_fill(token_usage: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Normalize app-server tokenUsage into Cuttle gauge fields."""
    if not isinstance(token_usage, dict):
        return {}
    last = token_usage.get("last") if isinstance(token_usage.get("last"), dict) else {}
    total = token_usage.get("total") if isinstance(token_usage.get("total"), dict) else {}

    def _int(d: Dict[str, Any], *keys: str) -> int:
        for k in keys:
            if d.get(k) is None:
                continue
            try:
                return max(0, int(d.get(k) or 0))
            except (TypeError, ValueError):
                continue
        return 0

    # Prefer last-call occupancy; fall back to cumulative total.
    used = _int(last, "totalTokens", "inputTokens") or _int(
        total, "totalTokens", "inputTokens"
    )
    window = token_usage.get("modelContextWindow")
    try:
        limit = int(window) if window is not None else 0
    except (TypeError, ValueError):
        limit = 0
    out: Dict[str, Any] = {
        "prompt_tokens": used,
        "input_tokens": used,
        "context_tokens": used,
        "raw": token_usage,
    }
    if limit > 0:
        out["model_context_window"] = limit
    return out


def _run_app_server_session(
    thread_id: str,
    *,
    cwd: Optional[str],
    timeout: float,
    codex_bin: Optional[str],
    after_resume: str,
) -> Dict[str, Any]:
    """Shared stdio loop: initialize → resume → (compact | read usage) → unsubscribe.

    ``after_resume`` is ``\"compact\"`` or ``\"usage\"``.
    """
    tid = (thread_id or "").strip()
    method_label = (
        "app-server-compact" if after_resume == "compact" else "app-server-usage"
    )
    if not tid:
        return {
            "success": False,
            "agent_id": "codex",
            "method": method_label,
            "error": "Missing Codex thread id",
        }
    exe = (codex_bin or "").strip() or (codex_executable() or "")
    if not exe:
        return {
            "success": False,
            "agent_id": "codex",
            "method": method_label,
            "error": "Codex CLI not found (set CODEX_CLI_PATH or install Codex)",
        }
    work = (cwd or "").strip() or os.getcwd()
    try:
        proc = subprocess.Popen(
            [exe, "app-server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=work if os.path.isdir(work) else None,
        )
    except OSError as exc:
        return {
            "success": False,
            "agent_id": "codex",
            "method": method_label,
            "error": f"Failed to spawn Codex app-server: {exc}",
        }

    out_q: "queue.Queue[Optional[Dict[str, Any]]]" = queue.Queue()
    threading.Thread(target=_reader, args=(proc, out_q), daemon=True).start()
    threading.Thread(target=_drain_stderr, args=(proc,), daemon=True).start()

    req_id = 0
    pending: Dict[int, str] = {}
    last_token_usage: Optional[Dict[str, Any]] = None
    result: Dict[str, Any] = {
        "success": False,
        "agent_id": "codex",
        "method": method_label,
        "session_id": tid,
        "error": None,
        "token_usage": None,
        "status": "unknown",
    }
    deadline = time.monotonic() + max(15.0, float(timeout or _DEFAULT_TIMEOUT_SEC))
    done = False
    unsub_sent = False
    waiting_usage = False

    def next_id() -> int:
        nonlocal req_id
        req_id += 1
        return req_id

    def send_req(method: str, params: Dict[str, Any]) -> int:
        mid = next_id()
        pending[mid] = method
        _send(proc, {"method": method, "id": mid, "params": params})
        return mid

    def finish_usage() -> None:
        nonlocal done, unsub_sent
        if last_token_usage:
            result["token_usage"] = _token_usage_fill(last_token_usage)
            result["usage"] = result["token_usage"]
            result["success"] = True
            result["status"] = "resumed"
            result["error"] = None
        else:
            result["success"] = False
            result["error"] = result.get("error") or "No tokenUsage after thread/resume"
        if not unsub_sent:
            unsub_sent = True
            try:
                send_req("thread/unsubscribe", {"threadId": tid})
            except Exception:
                done = True

    try:
        send_req(
            "initialize",
            {
                "clientInfo": {
                    "name": "cuttle",
                    "title": "Cuttle",
                    "version": "1.0",
                }
            },
        )
        _send(proc, {"method": "initialized", "params": {}})

        while not done:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if waiting_usage and last_token_usage:
                    finish_usage()
                    continue
                result["error"] = f"Codex {method_label} timed out after {timeout:.0f}s"
                break
            try:
                msg = out_q.get(timeout=min(remaining, 5.0))
            except queue.Empty:
                if waiting_usage and last_token_usage:
                    finish_usage()
                    continue
                if proc.poll() is not None:
                    result["error"] = "Codex app-server exited unexpectedly"
                    break
                continue
            if msg is None:
                if not done:
                    result["error"] = "Codex app-server closed stdout"
                break

            msg_id = msg.get("id")
            method = str(msg.get("method") or "")

            if msg_id is not None and "error" in msg:
                err = msg.get("error") if isinstance(msg.get("error"), dict) else {}
                sent = pending.pop(int(msg_id), "?")
                result["error"] = f"{sent}: {err.get('message') or err}"
                done = True
                continue

            if msg_id is not None and "result" in msg:
                sent = pending.pop(int(msg_id), "")
                if sent == "initialize":
                    send_req("thread/resume", {"threadId": tid})
                elif sent == "thread/resume":
                    if after_resume == "compact":
                        send_req("thread/compact/start", {"threadId": tid})
                    else:
                        waiting_usage = True
                        if last_token_usage:
                            finish_usage()
                elif sent == "thread/compact/start":
                    pass
                elif sent == "thread/unsubscribe":
                    done = True
                continue

            if method == "thread/tokenUsage/updated":
                params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
                tu = params.get("tokenUsage")
                if isinstance(tu, dict):
                    last_token_usage = tu
                    if waiting_usage:
                        finish_usage()
                continue

            if method == "turn/completed":
                params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
                turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
                status = str(turn.get("status") or "unknown")
                result["status"] = status
                result["success"] = status == "completed"
                if not result["success"]:
                    err = turn.get("error") if isinstance(turn.get("error"), dict) else {}
                    result["error"] = str(
                        err.get("message") or f"compact turn status: {status}"
                    )
                if last_token_usage:
                    result["token_usage"] = _token_usage_fill(last_token_usage)
                    result["usage"] = result["token_usage"]
                if not unsub_sent:
                    unsub_sent = True
                    try:
                        send_req("thread/unsubscribe", {"threadId": tid})
                    except Exception:
                        done = True
                continue

            if msg_id is not None and (
                "approval" in method.lower() or method == "requestApproval"
            ):
                try:
                    _send(proc, {"id": msg_id, "result": {"decision": "accept"}})
                except Exception:
                    pass

    except Exception as exc:
        result["error"] = f"Codex {method_label} failed: {exc}"
        result["success"] = False
    finally:
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    return result


def fetch_codex_thread_token_usage(
    thread_id: str,
    *,
    cwd: Optional[str] = None,
    timeout: float = 45.0,
    codex_bin: Optional[str] = None,
) -> Dict[str, Any]:
    """Resume a Codex thread and read live ``tokenUsage`` (no compact).

    ``last.*`` is single-call occupancy; ``total.*`` is turn billing — gauge uses last.
    """
    return _run_app_server_session(
        thread_id,
        cwd=cwd,
        timeout=timeout,
        codex_bin=codex_bin,
        after_resume="usage",
    )


def compact_codex_thread(
    thread_id: str,
    *,
    cwd: Optional[str] = None,
    timeout: float = _DEFAULT_TIMEOUT_SEC,
    codex_bin: Optional[str] = None,
) -> Dict[str, Any]:
    """Resume ``thread_id`` on app-server and run ``thread/compact/start``.

    Returns a result dict with ``success``, ``method``, ``token_usage`` (normalized),
    and ``error`` when failed. Does not spend a normal agent turn via ``exec``.
    """
    return _run_app_server_session(
        thread_id,
        cwd=cwd,
        timeout=timeout,
        codex_bin=codex_bin,
        after_resume="compact",
    )

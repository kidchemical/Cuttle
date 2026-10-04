"""Structured query-log events for harness turns (Brain, sent prompt, tools, thinking).

Source of truth is JSON sidecars under ``web/logs/query_data_<id>.json``.
The inspector reads ``GET /api/query-log/<id>``. Bind ``query_id`` with
:func:`bind_query_id` so CLI worker threads (``asyncio.to_thread``) can record
without extra kwargs.
"""

from __future__ import annotations

import copy
import json
import os
import re
import time
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

_current_query_id: ContextVar[Optional[str]] = ContextVar("cuttle_query_id", default=None)

MAX_EVENTS = 400
MAX_TEXT = 24_000
MAX_SENT = 80_000
MAX_TOOL_ARGS = 4_000
MAX_STATUS = 800

_THINKING_RE = re.compile(r"^thinking:\s*(.*)$", re.I | re.S)
_WRITING_RE = re.compile(r"^writing:\s*(.*)$", re.I | re.S)
_TOOL_RE = re.compile(r"^tool\s+(\d+):\s*(.*)$", re.I | re.S)
_TOOL_FAIL_RE = re.compile(r"^tool failed:\s*(.*)$", re.I | re.S)
_HEARTBEAT_RE = re.compile(r"working[…\.]\s*\d+\s*s", re.I)


def bind_query_id(query_id: Optional[str]) -> Token:
    return _current_query_id.set((query_id or "").strip() or None)


def reset_query_id(token: Token) -> None:
    _current_query_id.reset(token)


def current_query_id() -> Optional[str]:
    return _current_query_id.get()


def query_log_url(query_id: Optional[str]) -> Optional[str]:
    qid = (query_id or "").strip()
    if not qid:
        return None
    return f"/query_log.html?id={qid}"


def _cap(text: Any, limit: int) -> str:
    s = "" if text is None else str(text)
    if len(s) <= limit:
        return s
    return s[: limit] + "\n… *(truncated)*"


def _tracker(query_id: Optional[str] = None):
    try:
        from api.query_tracker import get_query_tracker
    except Exception:
        return None
    qid = (query_id or current_query_id() or "").strip()
    if not qid:
        return get_query_tracker()
    t = get_query_tracker(qid)
    if t is None:
        return None
    if (getattr(t, "query_id", None) or "") != qid and qid:
        # Fallback tracker has no session.
        if not getattr(t, "query_id", None):
            return None
    return t


def add_event(kind: str, *, query_id: Optional[str] = None, **payload: Any) -> None:
    tracker = _tracker(query_id)
    if tracker is None or not getattr(tracker, "query_id", None):
        return
    try:
        tracker.add_event(kind, payload)
    except Exception:
        pass


def record_thinking(text: str, *, query_id: Optional[str] = None) -> None:
    blob = _cap(text, MAX_TEXT)
    if not blob.strip():
        return
    tracker = _tracker(query_id)
    if tracker is None:
        return
    data = getattr(tracker, "execution_data", None) or {}
    events = data.get("events")
    if isinstance(events, list) and events:
        last = events[-1]
        if isinstance(last, dict) and last.get("kind") == "thinking":
            last["text"] = blob
            last["t"] = time.time()
            try:
                tracker._publish_live_snapshot()
            except Exception:
                pass
            return
    add_event("thinking", query_id=query_id, text=blob)


def enrich_or_record_tool(
    summary: str,
    *,
    tool_call: Optional[Dict[str, Any]] = None,
    phase: str = "started",
    failed: bool = False,
    query_id: Optional[str] = None,
) -> None:
    args = _tool_args_preview(tool_call) if tool_call else None
    tracker = _tracker(query_id)
    if tracker is None:
        add_event(
            "tool",
            query_id=query_id,
            summary=summary,
            phase=phase,
            failed=failed,
            args=args,
        )
        return
    data = getattr(tracker, "execution_data", None) or {}
    events = data.get("events")
    if isinstance(events, list):
        for ev in reversed(events[-8:]):
            if not isinstance(ev, dict) or ev.get("kind") != "tool":
                continue
            prev = str(ev.get("summary") or ev.get("text") or "")
            if summary and (summary == prev or summary in prev or prev in summary):
                if args and not ev.get("args"):
                    ev["args"] = args
                ev["phase"] = phase
                if failed:
                    ev["failed"] = True
                ev["t"] = time.time()
                try:
                    tracker._publish_live_snapshot()
                except Exception:
                    pass
                return
    add_event(
        "tool",
        query_id=query_id,
        summary=_cap(summary, MAX_STATUS),
        phase=phase,
        failed=failed,
        args=args,
    )


def _tool_args_preview(tool_call: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(tool_call, dict):
        return None
    out: Dict[str, Any] = {}
    for key, val in tool_call.items():
        if not str(key).endswith("ToolCall") or not isinstance(val, dict):
            continue
        out["name"] = str(key)[: -len("ToolCall")]
        args = val.get("args")
        if isinstance(args, dict):
            slim = {}
            for ak, av in list(args.items())[:24]:
                if isinstance(av, (str, int, float, bool)) or av is None:
                    slim[str(ak)] = av if not isinstance(av, str) else _cap(av, 800)
                else:
                    try:
                        slim[str(ak)] = _cap(json.dumps(av, ensure_ascii=False), 400)
                    except Exception:
                        slim[str(ak)] = _cap(av, 200)
            out["args"] = slim
        break
    if not out:
        try:
            return {"raw": _cap(json.dumps(tool_call, ensure_ascii=False), MAX_TOOL_ARGS)}
        except Exception:
            return None
    return out


def ingest_status_message(message: str, *, query_id: Optional[str] = None) -> None:
    text = (message or "").strip()
    if not text or _HEARTBEAT_RE.search(text):
        return
    m = _THINKING_RE.match(text)
    if m:
        record_thinking(m.group(1), query_id=query_id)
        return
    m = _WRITING_RE.match(text)
    if m:
        blob = _cap(m.group(1), MAX_STATUS)
        tracker = _tracker(query_id)
        data = getattr(tracker, "execution_data", None) if tracker else None
        events = (data or {}).get("events") if isinstance(data, dict) else None
        if isinstance(events, list) and events:
            last = events[-1]
            if isinstance(last, dict) and last.get("kind") == "writing":
                last["text"] = blob
                last["t"] = time.time()
                try:
                    tracker._publish_live_snapshot()
                except Exception:
                    pass
                return
        add_event("writing", query_id=query_id, text=blob)
        return
    m = _TOOL_RE.match(text)
    if m:
        enrich_or_record_tool(
            m.group(2).strip(),
            phase="started",
            query_id=query_id,
        )
        return
    m = _TOOL_FAIL_RE.match(text)
    if m:
        enrich_or_record_tool(
            m.group(1).strip(),
            phase="failed",
            failed=True,
            query_id=query_id,
        )
        return
    add_event("status", query_id=query_id, text=_cap(text, MAX_STATUS))


class QueryStatusTee:
    """Forward status_queue items and parse thinking/tool/writing into the query log."""

    def __init__(self, inner: Any):
        self._inner = inner

    def put(self, item: Any, *args: Any, **kwargs: Any) -> Any:
        self._ingest(item)
        if self._inner is None:
            return None
        return self._inner.put(item, *args, **kwargs)

    def put_nowait(self, item: Any) -> Any:
        self._ingest(item)
        if self._inner is None:
            return None
        put_nowait = getattr(self._inner, "put_nowait", None)
        if callable(put_nowait):
            return put_nowait(item)
        return self._inner.put(item)

    def _ingest(self, item: Any) -> None:
        try:
            if isinstance(item, tuple) and len(item) >= 2 and item[0] == "status":
                ingest_status_message(str(item[1] or ""))
        except Exception:
            pass

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def logs_dir() -> Path:
    """Query sidecar folder; ``CUTTLE_QUERY_LOG_DIR`` overrides (tests)."""
    override = (os.environ.get("CUTTLE_QUERY_LOG_DIR") or "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[1] / "web" / "logs"


def stable_json_path(query_id: str) -> Path:
    return logs_dir() / f"query_data_{query_id}.json"


def load_query_json(query_id: str) -> Optional[Dict[str, Any]]:
    qid = (query_id or "").strip()
    if not qid or len(qid) > 36:
        return None
    path = stable_json_path(qid)
    if path.is_file():
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    folder = logs_dir()
    if not folder.is_dir():
        return None
    matches = sorted(
        folder.glob(f"query_data_{qid}_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for p in matches:
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception:
            continue
    return None


def public_query_payload(data: Dict[str, Any], *, executing: bool) -> Dict[str, Any]:
    d = data or {}
    events = d.get("events") if isinstance(d.get("events"), list) else []
    sent = d.get("sent") if isinstance(d.get("sent"), dict) else {}
    return {
        "query_id": d.get("query_id"),
        "timestamp": d.get("timestamp"),
        "user_input": d.get("user_input") or "",
        "success": d.get("success"),
        "error_message": d.get("error_message"),
        "total_execution_time": d.get("total_execution_time") or 0,
        "total_tokens": d.get("total_tokens") or 0,
        "total_cost": d.get("total_cost") or 0,
        "harness": d.get("harness") or {},
        "brain": d.get("brain") or {},
        "sent": sent,
        "events": copy.deepcopy(events),
        "llm_calls": d.get("llm_calls") or [],
        "tool_calls": d.get("tool_calls") or [],
        "executing": executing,
        "report_url": query_log_url(d.get("query_id")),
    }


def build_query_log_response(query_id: str) -> Tuple[Optional[Dict[str, Any]], Optional[str], int]:
    qid = (query_id or "").strip()
    if not qid or len(qid) > 36:
        return None, "query_id required", 400
    executing = False
    try:
        from api.active_executions import is_query_executing

        executing = bool(is_query_executing(qid))
    except Exception:
        executing = False
    data = None
    try:
        from api.query_tracker import get_query_tracker, get_shared_live_snapshot

        tracker = get_query_tracker(qid)
        if tracker and tracker.query_id == qid and tracker.execution_data:
            data = tracker.execution_data
        if data is None and tracker:
            data = tracker.get_live_snapshot(qid)
        if data is None:
            data = get_shared_live_snapshot(qid)
    except Exception:
        data = None
    if data is None:
        data = load_query_json(qid)
        executing = False
    if not data:
        return None, "query log not found", 404
    if not data.get("query_id"):
        data = dict(data)
        data["query_id"] = qid
    return public_query_payload(data, executing=executing), None, 200

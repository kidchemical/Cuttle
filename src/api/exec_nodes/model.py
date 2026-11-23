"""Execution nodes: pure decisions and the node wire contract.

No Cuttle imports and no I/O, so both sides of the wire (the host and a node's
worker) and the tests share one definition.

Contract v1
-----------
Mesh job ``agent_turn`` carries metadata only (agent id, chat handle, node
cwd). The claiming node fetches the turn payload over its device bearer::

    {"contract": 1, "agent_id": str, "prompt": str, "cwd": str,
     "resume": str | None, "model": str | None, "effort": str | None,
     "timeout": float, "chat": "CH-…"}

and posts ``{"events": [status line, …], "result": {…} | None}`` back; each
reply carries ``{"cancel": bool}``. Events are plain status strings
(``status_line``); ``result`` is ``result_to_wire`` of the adapter's result.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

CONTRACT = 1
HOST = "host"
JOB_TYPE = "agent_turn"

STATE_ONLINE = "online"
STATE_BUSY = "busy"
STATE_UNAVAILABLE = "unavailable"

_MAX_ITEM_CHARS = 8000


def normalize_node_id(value: Any) -> str:
    """``""`` / ``"host"`` / ``None`` all mean the Cuttle host itself."""
    text = str(value or "").strip()
    return HOST if not text or text.lower() == HOST else text


def node_state(worker: Optional[Dict[str, Any]], *, agent_id: str = "") -> Dict[str, str]:
    """``{"state", "reason"}`` for one registered worker as an execution node.

    ``unavailable`` nodes cannot start a turn; ``busy`` ones can (the turn
    waits for a free slot).
    """
    if not worker:
        return {"state": STATE_UNAVAILABLE, "reason": "not paired with this Cuttle host"}
    if not worker.get("online"):
        return {"state": STATE_UNAVAILABLE, "reason": "offline"}
    caps = worker.get("capabilities") if isinstance(worker.get("capabilities"), dict) else {}
    if not caps.get("agent_exec"):
        return {"state": STATE_UNAVAILABLE, "reason": "agent execution is not enabled on that machine"}
    agents = [str(a) for a in (caps.get("agents") or [])]
    aid = (agent_id or "").strip().lower()
    if aid and aid not in agents:
        return {"state": STATE_UNAVAILABLE, "reason": f"the {aid} CLI is not installed on that machine"}
    load = worker.get("load") if isinstance(worker.get("load"), dict) else {}
    try:
        running = int(load.get("agent_turns") or 0)
    except (TypeError, ValueError):
        running = 0
    if running > 0:
        return {"state": STATE_BUSY, "reason": f"{running} agent turn{'s' if running != 1 else ''} running"}
    return {"state": STATE_ONLINE, "reason": ""}


def _norm_prefix(path: str) -> str:
    return str(path or "").strip().rstrip("/\\")


def map_path(host_path: str, paths: Optional[Dict[str, str]]) -> str:
    """Node path for ``host_path`` by the longest mapped host prefix ('' when unmapped)."""
    src = _norm_prefix(host_path)
    if not src or not isinstance(paths, dict):
        return ""
    best_host, best_node = "", ""
    for host_prefix, node_prefix in paths.items():
        hp, np_ = _norm_prefix(host_prefix), _norm_prefix(node_prefix)
        if not hp or not np_:
            continue
        if (src == hp or src.startswith(hp + "/")) and len(hp) > len(best_host):
            best_host, best_node = hp, np_
    if not best_host:
        return ""
    rest = src[len(best_host):]
    if "\\" in best_node and "/" not in best_node:
        rest = rest.replace("/", "\\")
    return best_node + rest


def node_cwd(*, chat_cwd: str, host_cwd: str, prefs: Optional[Dict[str, Any]]) -> str:
    """Where the CLI runs on the node: the chat's own choice, the mapped
    project, else the node's default directory ('' when none is known)."""
    prefs = prefs if isinstance(prefs, dict) else {}
    return (
        str(chat_cwd or "").strip()
        or map_path(host_cwd, prefs.get("paths"))
        or str(prefs.get("default_cwd") or "").strip()
    )


def rewrite_envelope(prompt: str, paths: Optional[Dict[str, str]], request_header: str) -> str:
    """Point mapped host paths in the Cuttle context at the node's copy.

    Only the context above the last ``request_header`` is rewritten: what the
    user typed is never altered.
    """
    text = str(prompt or "")
    if not isinstance(paths, dict) or not paths:
        return text
    cut = text.rfind(request_header) if request_header else -1
    head, tail = (text[:cut], text[cut:]) if cut >= 0 else ("", text)
    if not head:
        return text
    for host_prefix in sorted(paths, key=lambda p: -len(_norm_prefix(p))):
        hp, np_ = _norm_prefix(host_prefix), _norm_prefix(paths[host_prefix])
        if hp and np_:
            head = head.replace(hp, np_)
    return head + tail


def node_note(*, label: str, os_name: str, cwd: str) -> str:
    """The one line a fresh CLI session on a node needs about where it is."""
    where = f"`{label}`" + (f" ({os_name})" if os_name else "")
    return (
        f"[Cuttle execution node: this agent runs on {where} in `{cwd}`, not on the Cuttle host. "
        "Files and tools are this machine's; the host's Cuttle home and `python -m api.*` "
        "verbs are not reachable from here.]"
    )


def status_line(item: Any) -> Optional[str]:
    """The text of a ``("status", text)`` queue entry; ``None`` for anything else.

    Status lines are all a node may send while a turn runs. Other queue
    entries steer the host's turn pump (``query_started``, ``done``, …) and
    are the host's alone, so neither side relays them.
    """
    if isinstance(item, (tuple, list)) and len(item) >= 2 and item[0] == "status" and isinstance(item[1], str):
        return item[1][:_MAX_ITEM_CHARS]
    return None


def _plain(value: Any, depth: int = 0) -> Any:
    """JSON-safe copy of adapter usage/meta (unknown objects are dropped)."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if depth > 6:
        return None
    if isinstance(value, dict):
        return {str(k): _plain(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v, depth + 1) for v in value]
    return None


# Adapter extras that only mean something on the machine that produced them.
_NODE_LOCAL_META = frozenset({"background_job", "billing"})


def result_to_wire(result: Any) -> Dict[str, Any]:
    """An adapter ``AgentResult`` (or its dict form) for the wire."""
    get = result.get if isinstance(result, dict) else (lambda k, d=None: getattr(result, k, d))
    meta = get("meta") or {}
    return {
        "success": bool(get("success")),
        "output": str(get("output") or ""),
        "error": (str(get("error")) if get("error") else None),
        "usage": _plain(get("usage") or {}) or {},
        "session_id": (str(get("session_id")) if get("session_id") else None),
        "model": str(get("model") or ""),
        "meta": {k: v for k, v in (_plain(meta) or {}).items() if k not in _NODE_LOCAL_META},
    }


def result_from_wire(raw: Any) -> Dict[str, Any]:
    """Validated result fields for ``AgentResult(**fields)``."""
    raw = raw if isinstance(raw, dict) else {}
    return {
        "success": bool(raw.get("success")),
        "output": str(raw.get("output") or ""),
        "error": (str(raw.get("error")) if raw.get("error") else None),
        "usage": raw.get("usage") if isinstance(raw.get("usage"), dict) else {},
        "session_id": (str(raw.get("session_id")) if raw.get("session_id") else None),
        "model": str(raw.get("model") or ""),
        "meta": raw.get("meta") if isinstance(raw.get("meta"), dict) else {},
    }


def clean_paths(raw: Any) -> Dict[str, str]:
    """Host-prefix → node-prefix map from user input (blank rows dropped)."""
    out: Dict[str, str] = {}
    if isinstance(raw, dict):
        for host_prefix, node_prefix in raw.items():
            hp, np_ = _norm_prefix(str(host_prefix)), _norm_prefix(str(node_prefix))
            if hp and np_:
                out[hp[:1000]] = np_[:1000]
    return out


def node_label(worker: Optional[Dict[str, Any]], prefs: Optional[Dict[str, Any]], node_id: str) -> str:
    custom = str((prefs or {}).get("label") or "").strip()
    if custom:
        return custom
    return str((worker or {}).get("hostname") or "").strip() or node_id


def os_family(os_name: Any) -> str:
    """``Windows-11-10.0…`` → ``Windows``; ``Linux-7.0…`` → ``Linux``."""
    text = str(os_name or "").strip()
    low = text.lower()
    for key, name in (("windows", "Windows"), ("linux", "Linux"), ("darwin", "macOS"), ("macos", "macOS")):
        if key in low:
            return name
    return text.split("-")[0][:24]


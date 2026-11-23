"""Execution nodes, host side: the node list, a chat's node, the kernel seam.

A node is a paired device worker (``api.device_workers``) that opted in to
agent turns; pairing, tokens and liveness stay with the mesh. This module adds
only what the mesh does not know: which node a chat runs on and where on that
machine. The binding is read once when a turn starts, so changing it never
touches a running turn: it applies from the chat's next turn.
"""

from __future__ import annotations

import re
import socket
from typing import Any, Dict, List, Optional

from api.exec_nodes import model, store

FLAG = "exec_nodes"


class ExecNodeError(ValueError):
    """A request the caller can fix (unknown node, bad session, feature off)."""


def enabled() -> bool:
    from api.experimental import is_enabled

    return is_enabled(FLAG)


def session_number(value: Any) -> Optional[int]:
    """Numeric chat id from ``123`` / ``db_session_123`` / ``CH-000123``."""
    match = re.fullmatch(r"(?:db_session_|CH-0*)?(\d+)", str(value if value is not None else "").strip(), re.I)
    return int(match.group(1)) if match else None


def _workers() -> List[Dict[str, Any]]:
    from api.device_workers.config import heartbeat_stale_seconds, worker_id
    from api.device_workers.store import get_store

    self_id = worker_id()
    return [w for w in get_store().list_workers(stale_after=float(heartbeat_stale_seconds()))
            if str(w.get("worker_id") or "") != self_id]


def _node_row(worker: Dict[str, Any], agent_id: str, running: Dict[str, int]) -> Dict[str, Any]:
    node_id = str(worker.get("worker_id") or "")
    prefs = store.get_prefs(node_id)
    # Turns this host relays are known at once; the node's own count arrives with its heartbeat.
    load = dict(worker.get("load") or {}) if isinstance(worker.get("load"), dict) else {}
    load["agent_turns"] = max(int(load.get("agent_turns") or 0), running.get(node_id, 0))
    state = model.node_state({**worker, "load": load}, agent_id=agent_id)
    caps = worker.get("capabilities") if isinstance(worker.get("capabilities"), dict) else {}
    return {
        "id": node_id,
        "label": model.node_label(worker, prefs, node_id),
        "os": model.os_family(worker.get("os")),
        "host": False,
        "state": state["state"],
        "reason": state["reason"],
        "agents": [str(a) for a in (caps.get("agents") or [])],
        "default_cwd": prefs["default_cwd"],
        "paths": prefs["paths"],
        "custom_label": prefs["label"],
    }


def host_row() -> Dict[str, Any]:
    import platform

    return {"id": model.HOST, "label": socket.gethostname(), "os": model.os_family(platform.platform()),
            "host": True, "state": model.STATE_ONLINE, "reason": "", "agents": [],
            "default_cwd": "", "paths": {}, "custom_label": ""}


def list_nodes(agent_id: str = "") -> List[Dict[str, Any]]:
    """The host first, then every paired machine with its state for ``agent_id``."""
    running = store.running_turns()
    rows = [_node_row(w, agent_id, running) for w in _workers()]
    rows.sort(key=lambda r: (r["state"] == model.STATE_UNAVAILABLE, r["label"].lower()))
    return [host_row(), *rows]


def node(node_id: str, agent_id: str = "") -> Optional[Dict[str, Any]]:
    nid = model.normalize_node_id(node_id)
    if nid == model.HOST:
        return host_row()
    running = store.running_turns()
    for worker in _workers():
        if str(worker.get("worker_id") or "") == nid:
            return {**_node_row(worker, agent_id, running), "os_full": str(worker.get("os") or "")}
    return None


def binding(session_id: Any) -> Dict[str, Any]:
    """A chat's node (the host unless it was bound) with that node's row."""
    sid = session_number(session_id)
    row = store.get_chat_node(sid) if sid is not None else None
    nid = model.normalize_node_id(row["node_id"]) if row else model.HOST
    info = node(nid) or {"id": nid, "label": nid, "os": "", "host": False,
                         "state": model.STATE_UNAVAILABLE, "reason": "no longer paired with this Cuttle host"}
    return {"node_id": nid, "cwd": (row or {}).get("cwd") or "", "node": info}


def set_binding(session_id: Any, node_id: Any, cwd: str = "") -> Dict[str, Any]:
    """Bind a chat to a node from its next turn (``host`` clears the binding)."""
    if not enabled():
        raise ExecNodeError("Enable Execution nodes in Settings → Experimental.")
    sid = session_number(session_id)
    if sid is None:
        raise ExecNodeError(f"not a chat: {session_id or '(empty)'}")
    nid = model.normalize_node_id(node_id)
    if nid == model.HOST:
        store.clear_chat_node(sid)
        return binding(sid)
    if node(nid) is None:
        raise ExecNodeError(f"node not found: {nid} (pair the machine in Jobs → Devices first)")
    store.set_chat_node(sid, nid, str(cwd or "").strip()[:1000])
    return binding(sid)


def set_node_prefs(node_id: str, *, label: Any = None, default_cwd: Any = None, paths: Any = None) -> Dict[str, Any]:
    nid = model.normalize_node_id(node_id)
    if nid == model.HOST or node(nid) is None:
        raise ExecNodeError(f"node not found: {nid}")
    prefs = store.get_prefs(nid)
    store.set_prefs(
        nid,
        label=prefs["label"] if label is None else str(label).strip()[:80],
        default_cwd=prefs["default_cwd"] if default_cwd is None else str(default_cwd).strip()[:1000],
        paths=prefs["paths"] if paths is None else model.clean_paths(paths),
    )
    return node(nid) or {}


def adapter_for_turn(manifest: Any, adapter: Any, chat_session_id: Any) -> Any:
    """Kernel seam: the adapter this turn executes through.

    The host's own adapter unless the chat is bound to a node. When an agent's
    turn lands on a different machine than its last one, its native CLI session
    is not there: the handoff resends the recent conversation and the next
    briefing is a full one. A chat that was never bound costs one indexed read.
    """
    if not enabled():
        return adapter
    sid = session_number(chat_session_id)
    if sid is None:
        return adapter
    row = store.get_chat_node(sid)
    nid = model.normalize_node_id(row["node_id"]) if row else model.HOST
    previous = store.swap_last_node(str(sid), manifest.id, nid)
    if (previous or model.HOST) != nid:
        from api.cuttle_brain.context_delta import clear_injected_snapshot
        from api.cuttle_brain.handoff import mark_workspace_moved

        # The kernel's own spelling of the id: the brain stores key on it.
        mark_workspace_moved(chat_session_id, manifest.id)
        clear_injected_snapshot(chat_session_id, manifest.id)
    if nid == model.HOST:
        return adapter
    from api.exec_nodes.remote import RemoteAdapter

    return RemoteAdapter(adapter, manifest, nid, chat_cwd=(row or {}).get("cwd") or "")

"""Live work tree: what each chat is running right now, and who owns it.

A read model over the owners that already track live work. Nothing here
registers, stores or kills anything:

- agent turns in flight: ``api.agent_events`` runs with status ``running``
- detached background waits: ``api.agent_harness.background_wait``
- watch jobs bound to a chat: ``api.chat_run_registry.job_bindings`` + ``api.job_watch``
- detached-job (unit) watches: ``api.job_watch.unit_watch_records`` + systemd SubState
- agent-owned OS processes, detached ones included: ``api.jobs.processes``
- supervision schedules: ``api.jobs.ledger.chat_op_jobs``
- mesh jobs (no chat owner): ``api.jobs.ledger``
- sub-agent parent links: ``api.subagents.store``

Consumers: Agent Feed's Running section (``GET /api/jobs/work-tree``), the
chat background-work bubble (``background_summary`` → live-status
``background``) and the session list ``background`` flag
(``background_session_ids``). Controls stay with their owners: chat Stop is
``/api/chat-cancel``, a process is ended by ``api.jobs.processes``.
"""

from __future__ import annotations

import os
import re
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Set

from api.jobs import processes

SCHEMA_VERSION = 1
# Live-status polls (every pane, every heartbeat) and the sessions list share
# one index. Reading other processes' environments is slower on Windows.
INDEX_TTL_SEC = 5.0 if os.name == "nt" else 2.0
# Owner ordering in the tree: live turns first, then waits, watches, loose
# processes, and schedules that are only waiting for their next check.
_STATE_RANK = {"running": 0, "waiting": 1, "watch": 2, "processes": 3, "scheduled": 4}

_lock = threading.Lock()
_build_lock = threading.Lock()  # one builder; concurrent readers wait for its result
_index_cache: Dict[str, Any] = {"at": 0.0, "index": {}}


def _handle(chat: str) -> str:
    return f"CH-{int(chat):06d}" if str(chat).isdigit() else str(chat)


def _bare(raw: Any) -> str:
    try:
        from api.session_keys import bare_chat_session_id

        return bare_chat_session_id(raw)
    except Exception:
        return str(raw or "").strip()


# ── sources (each failure degrades to "nothing from that source") ───────────


def _waits() -> List[Dict[str, Any]]:
    try:
        from api.agent_harness.background_wait import running_on_disk

        return running_on_disk(None)
    except Exception:
        return []


def _watches(skip: Set[str]) -> Dict[str, List[Dict[str, Any]]]:
    """Running watch jobs bound to each chat (Flask-process registry)."""
    try:
        from api.chat_run_registry import job_bindings
        from api.job_watch import read_status_reconciled
    except Exception:
        return {}
    out: Dict[str, List[Dict[str, Any]]] = {}
    for chat, job_ids in job_bindings().items():
        for job_id in job_ids:
            if job_id in skip:
                continue
            try:
                data = read_status_reconciled(job_id, persist=False) or {}
            except Exception:
                continue
            if str(data.get("state") or "") != "running":
                continue
            row = {"job_id": job_id, "label": str(data.get("label") or job_id)[:200],
                   "percent": data.get("percent"), "started_at": data.get("started_at") or data.get("time")}
            if isinstance(data.get("bars"), list):
                row["bars"] = data["bars"][:6]
            out.setdefault(_bare(chat), []).append(row)
    return out


# systemd SubState per unit, shared by every index build for this long: the
# index is rebuilt every couple of seconds and a ``systemctl show`` is a process.
UNIT_STATE_TTL_SEC = 10.0
_unit_state_cache: Dict[str, Any] = {}


def _unit_live(unit: str) -> Dict[str, Any]:
    """``{"sub_state", "scheduled", "starts_at"}`` for one unit, cached briefly.

    A unit waiting on its systemd timer (``systemd-run --on-calendar``) is
    ``scheduled`` with the time it starts: the card says so instead of
    "running" for a job that has not begun."""
    now = time.monotonic()
    with _lock:
        hit = _unit_state_cache.get(unit)
    if hit and now - hit[0] <= UNIT_STATE_TTL_SEC:
        return hit[1]
    try:
        from api.job_watch import unit_state

        st = unit_state(unit)
        sub = str(st.get("sub_state") or st.get("active_state") or "") if st.get("queried") else "status unavailable"
        live = {"sub_state": sub, "scheduled": bool(st.get("scheduled")), "starts_at": st.get("starts_at"),
                "subagents": _runs_subagents(int(st.get("pid") or 0))}
    except Exception:
        live = {"sub_state": "", "scheduled": False, "starts_at": None}
    with _lock:
        _unit_state_cache[unit] = (now, live)
    return live


_SUBAGENT_CMD = re.compile(r"\bapi\.subagents\b")


def _runs_subagents(pid: int) -> bool:
    """True when a detached job's process tree runs ``python -m api.subagents``.

    Such a job is a sub-agent run: its children's transcript cards show it, so
    the chat's background card does not list it a second time."""
    if pid <= 0:
        return False
    try:
        import psutil

        root = psutil.Process(pid)
        for proc in [root, *root.children(recursive=True)]:
            try:
                if _SUBAGENT_CMD.search(" ".join(proc.cmdline())):
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception:
        return False
    return False


def _unit_label(rec: Dict[str, Any], unit: str, pid: int) -> str:
    """The job's name for people: its title when the label is only the default "Running: <unit>"."""
    label = str(rec.get("label") or "")
    title = str(rec.get("title") or "")
    if title.startswith("Detached job — "):
        title = ""  # job_watch's default title names the unit again
    if title and (not label or label in (f"Running: {unit}", f"Running: pid {pid}", unit)):
        return title[:200]
    return (label or (f"Running: {unit}" if unit else f"Running: pid {pid}"))[:200]


def _unit_watches() -> Dict[str, List[Dict[str, Any]]]:
    """Pending detached-job watches (``job_watch watch-unit``), by the chat they resume.

    The record lives until the poller resumes the chat, so the row disappears
    when the watch fires or is removed."""
    try:
        from api.job_watch import unit_watch_records

        records = unit_watch_records()
    except Exception:
        return {}
    out: Dict[str, List[Dict[str, Any]]] = {}
    for rec in records:
        chat = _bare(rec.get("chat_session_id"))
        if not chat:
            continue
        unit = str(rec.get("unit") or "")
        pid = int(rec.get("pid") or 0)
        from api.job_watch import read_status

        status = read_status(rec["id"]) or {}
        live = _unit_live(unit) if unit else {"sub_state": "running", "scheduled": False, "starts_at": None}
        out.setdefault(chat, []).append({
            "job_id": rec.get("id"), "kind": "unit",
            "label": _unit_label(rec, unit, pid),
            "unit": unit, "pid": pid,
            "sub_state": live["sub_state"],
            "subagents": bool(live.get("subagents")),
            "scheduled": live["scheduled"], "starts_at": live["starts_at"],
            "percent": None, "started_at": rec.get("created_ts"),
            "activity": status.get("activity"), "state": status.get("state", "running"),
            "resume_chat": _handle(chat), "handoff": "on_exit",
            "resume_note": str(rec.get("resume_message") or "")[:400],
            "origin_query_id": str(rec.get("origin_query_id") or "")[:80],
        })
    return out


def _schedules() -> Dict[str, List[Dict[str, Any]]]:
    """Live supervision schedules / queued sends, by the chat that owns them."""
    try:
        from api.jobs.ledger import chat_op_jobs

        jobs = chat_op_jobs()
    except Exception:
        return {}
    out: Dict[str, List[Dict[str, Any]]] = {}
    for job in jobs:
        if job.get("status") not in ("running", "queued", "paused"):
            continue
        chat = _bare((job.get("origin") or {}).get("chat_session_id"))
        if not chat:
            continue
        out.setdefault(chat, []).append({
            "id": job["id"], "label": job.get("label") or "", "status": job.get("status"),
            "target": job.get("target") or "", "detail": (job.get("meta") or {}).get("detail") or "",
            "cancellable": "cancel" in (job.get("actions") or []),
            "kind": (job.get("meta") or {}).get("op_kind") or "",
            "every": (job.get("meta") or {}).get("every"), "next_at": (job.get("meta") or {}).get("next_at"),
            "seen": (job.get("meta") or {}).get("seen") or "",
            "waiting_since": (job.get("meta") or {}).get("waiting_since"),
        })
    return out


def _active_runs() -> List[Dict[str, Any]]:
    try:
        from api.agent_events.writer import get_writer

        return list(get_writer().active_runs() or [])
    except Exception:
        try:
            from api.agent_events.store import EventStore, state_dir

            return EventStore(state_dir()).active_runs()
        except Exception:
            return []


def _mesh_active() -> List[Dict[str, Any]]:
    try:
        from api.jobs.catalog import MESH_ACTIVE_STATUSES
        from api.jobs.ledger import _mesh_to_job, mesh_store

        return [_mesh_to_job(r) for r in mesh_store().list_jobs(limit=200)
                if (r.get("status") or "") in MESH_ACTIVE_STATUSES]
    except Exception:
        return []


def _chat_meta(chats: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    ids = [int(c) for c in chats if str(c).isdigit()]
    if not ids:
        return {}
    try:
        from api.auth_db import get_auth_db

        conn = get_auth_db()._get_connection()
        try:
            marks = ",".join("?" * len(ids))
            rows = conn.execute(
                f"SELECT id, session_name, project_path FROM chat_sessions WHERE id IN ({marks})", ids
            ).fetchall()
        finally:
            conn.close()
        return {str(r["id"]): {"title": r["session_name"] or "", "project": r["project_path"] or ""} for r in rows}
    except Exception:
        return {}


def _subagent_parents(chats: Iterable[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        from api.auth_db import get_auth_db
        from api.subagents import store

        db = get_auth_db()
        for chat in chats:
            if not str(chat).isdigit():
                continue
            child = store.get_child_by_session(db, int(chat))
            if not child:
                continue
            batch = store.get_batch(db, child.batch_id)
            if batch:
                out[str(chat)] = str(batch.parent_session_id)
    except Exception:
        pass
    return out


# ── per-chat index (cheap; shared by live-status and the sessions list) ──────


def _build_index() -> Dict[str, Dict[str, Any]]:
    forest = processes.forest()
    waits = _waits()
    units = _unit_watches()
    # A unit watch's card may also bind its job id to the chat: show it once.
    skip = {str(w.get("job_id") or "") for w in waits}
    skip |= {str(u.get("job_id") or "") for rows in units.values() for u in rows}
    watches = _watches(skip)
    schedules = _schedules()
    index: Dict[str, Dict[str, Any]] = {}

    def entry(chat: str) -> Dict[str, Any]:
        return index.setdefault(chat, {"waits": [], "watches": [], "schedules": [], "roots": [],
                                       "process_count": 0, "detached_count": 0, "spawned_by": {}})

    for chat, tree in forest.items():
        e = entry(chat)
        e.update(roots=tree["roots"], process_count=tree["process_count"],
                 detached_count=tree["detached_count"], spawned_by=tree["spawned_by"])
    for wait in waits:
        chat = _bare(wait.get("chat_session_id"))
        if chat:
            # Epoch when recorded: the ISO string is naive local time (ambiguous across DST).
            entry(chat)["waits"].append({**wait, "started_at": wait.get("started_ts") or wait.get("started_at")})
    for chat, rows in watches.items():
        entry(chat)["watches"].extend(rows)
    for chat, rows in units.items():
        entry(chat)["watches"].extend(rows)
    for chat, rows in schedules.items():
        entry(chat)["schedules"].extend(rows)
    return index


def _cached(max_age: float) -> Optional[Dict[str, Dict[str, Any]]]:
    with _lock:
        fresh = _index_cache["at"] and time.monotonic() - _index_cache["at"] <= max_age
        return _index_cache["index"] if fresh else None


def index(*, max_age: Optional[float] = None) -> Dict[str, Dict[str, Any]]:
    max_age = INDEX_TTL_SEC if max_age is None else max_age
    hit = _cached(max_age)
    if hit is not None:
        return hit
    with _build_lock:
        hit = _cached(max_age)  # built by the request we waited behind
        if hit is not None:
            return hit
        built = _build_index()
        with _lock:
            _index_cache.update(at=time.monotonic(), index=built)
        return built


def invalidate() -> None:
    processes.invalidate()
    with _lock:
        _unit_state_cache.clear()
        _index_cache["at"] = 0.0
        _subagent_cache["at"] = 0.0  # sub-agent parents are cached beside the index


def _working(e: Dict[str, Any]) -> bool:
    """Work that should light a chat's activity indicator (schedules excluded)."""
    return bool(e.get("waits") or e.get("watches") or e.get("process_count"))


# Parent chats with working sub-agent children, cached like the index: the
# sessions list polls often and this is a second DB round-trip.
_subagent_cache: Dict[str, Any] = {"at": 0.0, "ids": set()}


def _subagent_busy_parents() -> Set[str]:
    """Parent chats whose spawned sub-agent children are still working.

    A parent is idle while its children run (``spawn`` detaches), and a child
    chat is not a process or watch the parent owns, so without this a chat
    whose only live work is a sub-agent looks finished in the sessions list.
    Pending counts: a queued child of a live batch (``--collect serial``) is
    work the parent is still waiting on.
    """
    now = time.monotonic()
    with _lock:
        hit = _subagent_cache
        if hit["at"] and now - hit["at"] <= INDEX_TTL_SEC:
            return set(hit["ids"])
    ids: Set[str] = set()
    try:
        from api.auth_db import get_auth_db
        from api.subagents.types import STATUS_PENDING, STATUS_RUNNING, TERMINAL_BATCH

        conn = get_auth_db()._get_connection()
        try:
            marks = ",".join("?" * len(TERMINAL_BATCH))
            rows = conn.execute(
                "SELECT DISTINCT b.parent_session_id AS parent "
                "FROM subagent_children c JOIN subagent_batches b ON b.id = c.batch_id "
                f"WHERE c.status IN (?, ?) AND b.status NOT IN ({marks})",
                (STATUS_PENDING, STATUS_RUNNING, *TERMINAL_BATCH),
            ).fetchall()
        finally:
            conn.close()
        ids = {_bare(r["parent"]) for r in rows if r["parent"] is not None}
        ids.discard("")
    except Exception:
        return set()
    with _lock:
        _subagent_cache.update(at=now, ids=set(ids))
    return ids


def background_session_ids() -> Set[str]:
    """Chats that own live waits, watch jobs, processes or sub-agents right now.

    Unit (detached-job) watches arrive through ``_working`` as watches.
    """
    try:
        owned = {chat for chat, e in index().items() if _working(e)}
    except Exception:
        owned = set()
    return owned | _subagent_busy_parents()


def mark_background(sessions: List[Dict[str, Any]]) -> None:
    """Set ``background`` on session-list rows: idle chats that still own live work."""
    ids = background_session_ids()
    for row in sessions:
        sid = row.get("id")
        row["background"] = bool(sid is not None and _bare(sid) in ids and not row.get("generating"))


def _first_label(e: Dict[str, Any]) -> str:
    for key in ("waits", "watches"):
        for row in e.get(key) or ():
            if row.get("label"):
                return str(row["label"])
    n, d = int(e.get("process_count") or 0), int(e.get("detached_count") or 0)
    if n:
        noun = "process" if n == 1 else "processes"
        return f"{n} {noun} still running" + (f" ({d} detached)" if d else "")
    for row in e.get("schedules") or ():
        return " · ".join(x for x in (row.get("label"), row.get("detail")) if x)
    return ""


def _earliest_start(e: Dict[str, Any]) -> Optional[float]:
    times = [float(n.get("create_time") or 0) for n in e.get("roots") or () if n.get("create_time")]
    return min(times) if times else None


def _feed_enabled() -> bool:
    try:
        from api.experimental import is_enabled

        return bool(is_enabled("agent_feed"))
    except Exception:
        return False


# A delivered result stays on the card this long, so its end is seen.
RESULT_LINGER_SEC = 300


def _result_settled(chat: str, receipt: Dict[str, Any], *, now: Optional[float] = None) -> bool:
    """True when a finished job no longer belongs on the pinned card.

    Its follow-up was delivered, the chat is idle (that turn has finished, so
    the answer is in the transcript), and it ended more than a few minutes ago.
    Results that still need attention (failed, retrying, queued) stay; they
    used to stay for 7 days, so a chat kept a "Background job results" card
    for jobs its agent handled hours earlier."""
    if receipt.get("handoff") != "delivered":
        return False
    now = time.time() if now is None else now
    if now - float(receipt.get("finished_ts") or 0) < RESULT_LINGER_SEC:
        return False
    try:
        from api import chat_delivery

        return not chat_delivery.is_busy(chat)
    except Exception:
        return False


def _finished_label(label: str) -> str:
    """A finished job's label without the "Running: " it was registered with.

    Unit watches default to "Running: <unit>" (``job_watch``); next to the
    job's final state that reads as "Running: ch1821-adsbot · Finished"."""
    label = str(label or "")
    return label[len("Running: "):].strip() or label if label.startswith("Running: ") else label


def background_summary(session_id: Any) -> Optional[Dict[str, Any]]:
    """Compact background-work summary for one chat's live status, or None."""
    chat = _bare(session_id)
    try:
        e = index().get(chat)
    except Exception:
        return None
    from api.job_watch import recent_unit_results, read_activity

    # Terminal receipts do not light a running indicator or become live work.
    try:
        receipts = [r for r in recent_unit_results(chat) if not _result_settled(chat, r)]
    except Exception:
        receipts = []
    recent = [{"job_id": r["id"], "kind": "unit", "unit": r.get("unit", ""),
               "label": _finished_label(_unit_label(r, str(r.get("unit") or ""), int(r.get("pid") or 0)) or r["id"]), "state": r["outcome"].get("state", "unknown"),
               "started_at": r.get("created_ts"), "finished_at": r.get("finished_ts"),
               "handoff": r["handoff"], "resume_chat": _handle(chat),
               "resume_note": str(r.get("resume_message") or "")[:400],
               "origin_query_id": r.get("origin_query_id", ""),
               "followup_query_id": r.get("followup_query_id", ""),
               "activity": read_activity(r["id"])} for r in receipts]
    if not e and not recent:
        return None
    e = e or {"waits": [], "watches": [], "schedules": []}
    if not (_working(e) or e.get("schedules") or recent):
        return None
    # A failed enqueue can leave both a pending watch and a terminal receipt.
    recent_ids = {r["job_id"] for r in recent}
    watches = [w for w in e["watches"] if w.get("job_id") not in recent_ids]
    return {
        "label": _first_label(e),
        "waits": [{"job_id": w.get("job_id"), "label": w.get("label"), "started_at": w.get("started_at"),
                   "agent": w.get("agent") or ""}
                  for w in e["waits"]],
        "watches": [{k: w[k] for k in ("job_id", "label", "percent", "started_at", "kind", "unit", "sub_state",
                                       "scheduled", "starts_at", "subagents",
                                       "activity", "state", "resume_chat", "handoff", "resume_note", "origin_query_id") if k in w}
                    for w in watches],
        "recent": recent,
        "schedules": [{k: s.get(k) for k in ("id", "label", "detail", "status", "target", "kind", "every",
                                             "next_at", "seen", "waiting_since")} for s in e["schedules"]],
        "process_count": int(e.get("process_count") or 0),
        "detached_count": int(e.get("detached_count") or 0),
        "started_at": _earliest_start(e),
        "working": _working(e),
        "feed": _feed_enabled(),
    }


# ── full tree (Agent Feed) ───────────────────────────────────────────────────


_NODE_KEYS = ("pid", "ppid", "name", "status", "create_time", "cmdline", "rss", "cpu_sec",
              "run_id", "agent_id", "model", "detached", "parent_chat")


def _node(n: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: n.get(k) for k in _NODE_KEYS}
    out["children"] = [_node(c) for c in n.get("children") or ()]
    return out


def _owner_state(owner: Dict[str, Any]) -> str:
    if owner["run"]:
        return "running"
    if owner["waits"]:
        return "waiting"
    if owner["watches"]:
        return "watch"
    if owner["process_count"]:
        return "processes"
    return "scheduled"


def work_tree() -> Dict[str, Any]:
    """Every chat with live work, parents above their sub-agent chats."""
    idx = index()
    runs_by_chat: Dict[str, Dict[str, Any]] = {}
    for run in _active_runs():
        chat = _bare(run.get("chat_session_id"))
        if chat and (chat not in runs_by_chat or (run.get("started_at") or 0) > (runs_by_chat[chat].get("started_at") or 0)):
            runs_by_chat[chat] = run
    chats = set(idx) | set(runs_by_chat)
    meta = _chat_meta(chats)
    parents = _subagent_parents(chats)
    owners: Dict[str, Dict[str, Any]] = {}
    for chat in chats:
        e = idx.get(chat) or {}
        run = runs_by_chat.get(chat)
        info = meta.get(chat) or {}
        roots = [_node(n) for n in e.get("roots") or ()]
        spawned_by = e.get("spawned_by") or {}
        parent = next(iter(spawned_by), None) or parents.get(chat)
        owner = {
            "chat_session_id": chat,
            "handle": _handle(chat),
            "title": info.get("title") or "",
            "project": info.get("project") or (run or {}).get("project_root") or "",
            "run": ({"query_id": run.get("query_id"), "agent_id": run.get("agent_id"),
                     "model": run.get("model"), "started_at": run.get("started_at")} if run else None),
            "waits": list(e.get("waits") or []),
            "watches": list(e.get("watches") or []),
            "schedules": list(e.get("schedules") or []),
            "processes": roots,
            "process_count": int(e.get("process_count") or 0),
            "detached_count": int(e.get("detached_count") or 0),
            "parent": ({"chat_session_id": parent, "handle": _handle(parent),
                        "via_pid": spawned_by.get(parent)} if parent else None),
            "children": [],
        }
        owner["state"] = _owner_state(owner)
        owner["label"] = _first_label(e) if not run else f"{run.get('agent_id') or 'Agent'} is working"
        starts = [t for t in ((run or {}).get("started_at"), _earliest_start(e)) if t]
        owner["started_at"] = min(starts) if starts else None
        owners[chat] = owner

    def order_key(o: Dict[str, Any]):
        return (_STATE_RANK.get(o["state"], 9), -(o.get("started_at") or 0))

    def parent_of(chat: str) -> Optional[str]:
        parent = (owners[chat]["parent"] or {}).get("chat_session_id")
        return parent if parent in owners and parent != chat else None

    def cyclic(chat: str) -> bool:
        seen, cur = {chat}, parent_of(chat)
        while cur:
            if cur in seen:
                return True
            seen.add(cur)
            cur = parent_of(cur)
        return False

    top: List[Dict[str, Any]] = []
    for chat, owner in owners.items():
        parent = parent_of(chat)
        if parent and not cyclic(chat):
            owners[parent]["children"].append(owner)
        else:
            top.append(owner)
    for owner in owners.values():
        owner["children"].sort(key=order_key)
    top.sort(key=order_key)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": time.time(),
        "owners": top,
        "mesh": _mesh_active(),
        "totals": {
            "chats": len(owners),
            "processes": sum(o["process_count"] for o in owners.values()),
            "detached": sum(o["detached_count"] for o in owners.values()),
        },
    }

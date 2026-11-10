"""Aggregations for the Orchestration dashboard, plus the one-time backfill.

Pure over the event log except the model lookups, which read assistant rows
(``metadata.turn_id`` + the agent chip) so "which model orchestrated" needs
no extra recording: a spawn carries its parent turn id."""

from __future__ import annotations

import json
import re
import statistics
import time
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

from api.orchestration_telemetry import store

RANGES = [("7d", "7 days", 7), ("30d", "30 days", 30), ("90d", "90 days", 90), ("all", "All time", 0)]
WAKE_OUTCOMES = ("woke", "violation", "stalled", "checkpoint", "undelivered")
_META_MODEL = re.compile(r"\bmodel\s+([^\s·]+)")
_META_EFFORT = re.compile(r"\beffort\s+([^\s·]+)")


def _pct(n: float, d: float) -> Optional[float]:
    return round(100.0 * n / d, 1) if d else None


def _median(values: List[float]) -> Optional[float]:
    return round(statistics.median(values), 1) if values else None


def _p90(values: List[float]) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(round(0.9 * (len(ordered) - 1))))], 1)


def agent_label(agent: str, model: str) -> str:
    agent = (agent or "").strip()
    model = (model or "").strip()
    if agent and model:
        return f"{agent} · {model}"
    return agent or model or "unknown"


def identity_from_metadata(meta: Any) -> Tuple[str, str]:
    """(agent, model) of an assistant row, from its agent chip (and usage)."""
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except ValueError:
            meta = {}
    meta = meta if isinstance(meta, dict) else {}
    chips = ((meta.get("slash_command") or {}).get("chips") or []) if isinstance(meta.get("slash_command"), dict) else []
    chip = chips[0] if chips and isinstance(chips[0], dict) else {}
    agent = str(chip.get("category") or "").split("-")[0]
    m = _META_MODEL.search(str(chip.get("meta") or ""))
    model = (m.group(1) if m else "") or str((meta.get("usage") or {}).get("model") or "")
    return agent, model


def turn_identities(db, turn_ids: Iterable[str]) -> Dict[str, Tuple[str, str]]:
    ids = sorted({t for t in turn_ids if t})
    out: Dict[str, Tuple[str, str]] = {}
    if not ids or db is None:
        return out
    conn = db._get_connection()
    try:
        for start in range(0, len(ids), 400):
            chunk = ids[start:start + 400]
            rows = conn.execute(
                "SELECT metadata FROM chat_messages WHERE role = 'assistant' AND json_valid(metadata) "
                f"AND json_extract(metadata, '$.turn_id') IN ({','.join('?' * len(chunk))})", chunk).fetchall()
            for row in rows:
                meta = json.loads(row["metadata"] or "{}")
                ident = identity_from_metadata(meta)
                if ident != ("", ""):
                    out[str(meta.get("turn_id"))] = ident
    finally:
        conn.close()
    return out


def chat_identities(db, session_ids: Iterable[int], since: float) -> Dict[int, Tuple[str, str]]:
    """Most frequent (agent, model) of each chat's assistant rows since ``since``."""
    out: Dict[int, Tuple[str, str]] = {}
    sids = sorted({int(s) for s in session_ids if s is not None})
    if not sids or db is None:
        return out
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(since)) if since else "0000"
    conn = db._get_connection()
    try:
        for sid in sids:
            counts: Counter = Counter()
            for row in conn.execute(
                    "SELECT metadata FROM chat_messages WHERE chat_session_id = ? AND role = 'assistant' "
                    "AND timestamp >= ? ORDER BY id DESC LIMIT 200", (sid, stamp)):
                ident = identity_from_metadata(row["metadata"])
                if ident != ("", ""):
                    counts[ident] += 1
            if counts:
                out[sid] = counts.most_common(1)[0][0]
    finally:
        conn.close()
    return out


def report(*, user_id: Optional[int] = None, range_id: str = "30d", project: str = "", db=None) -> Dict[str, Any]:
    days = dict((r[0], r[2]) for r in RANGES).get(range_id, 30)
    since = time.time() - days * 86400 if days else 0.0
    evs = store.events(since=since, user_id=user_id)
    projects = sorted({e["project_path"] for e in evs if e["project_path"]})
    if project:
        sup_targets = {e["parent_session_id"] for e in evs if e["kind"] == "child_spawn" and e["project_path"] == project}
        evs = [e for e in evs if e["project_path"] == project
               or (e["kind"].startswith("sup_") and e["parent_session_id"] in sup_targets)]
    spawns = [e for e in evs if e["kind"] == "child_spawn"]
    ends = [e for e in evs if e["kind"] == "child_round_end"]
    checks = [e for e in evs if e["kind"] == "sup_check"]
    sends = [e for e in evs if e["kind"] == "sup_send"]
    stops = [e for e in evs if e["kind"] == "sup_stop"]
    escalations = [e for e in spawns if e["role"] == "escalation"]
    workers = [e for e in spawns if e["role"] == "worker"]

    turn_ident = turn_identities(db, [e["turn_id"] for e in spawns])
    orchestrated = sorted({e["parent_session_id"] for e in spawns if e["playbook_id"]})
    supervisors = sorted({e["session_id"] for e in checks + sends + stops if e["session_id"] is not None})
    chat_ident = chat_identities(db, orchestrated + supervisors, since)

    # --- sub-agents by tier · model -------------------------------------
    rows: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

    def bucket(e) -> Dict[str, Any]:
        key = (e["role"] or "untiered", e["agent"], e["model"])
        if key not in rows:
            rows[key] = {"role": key[0], "agent": key[1], "model": key[2], "label": agent_label(key[1], key[2]),
                         "spawns": 0, "runs": 0, "failed": 0, "cancelled": 0, "durations": [], "escalated_after": 0}
        return rows[key]

    for e in spawns:
        bucket(e)["spawns"] += 1
    for e in ends:
        b = bucket(e)
        b["runs"] += 1
        b["failed"] += e["status"] == "failed"
        b["cancelled"] += e["status"] == "cancelled"
        if e["duration_s"] is not None and e["status"] == "done":
            b["durations"].append(float(e["duration_s"]))
    for e in escalations:
        d = e["detail"] or {}
        if d.get("from_model") is not None or d.get("from_agent"):
            key = ("worker", d.get("from_agent") or "", d.get("from_model") or "")
            if key in rows:
                rows[key]["escalated_after"] += 1
    by_tier = []
    for b in rows.values():
        durs = b.pop("durations")
        b.update(fail_pct=_pct(b["failed"], b["runs"]), median_s=_median(durs), p90_s=_p90(durs),
                 escalation_pct=_pct(b["escalated_after"], b["spawns"]) if b["role"] == "worker" else None)
        by_tier.append(b)
    role_order = {"worker": 0, "escalation": 1, "expert": 2}
    by_tier.sort(key=lambda b: (role_order.get(b["role"], 3), -b["spawns"]))

    # --- orchestrators by model ------------------------------------------
    end_by_child: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for e in ends:
        end_by_child[e["child_id"]].append(e)
    interventions = Counter(e["parent_session_id"] for e in sends + stops)
    orch: Dict[str, Dict[str, Any]] = {}
    for e in spawns:
        if not e["playbook_id"]:
            continue
        ident = turn_ident.get(e["turn_id"]) or chat_ident.get(e["parent_session_id"]) or ("", "")
        label = agent_label(*ident)
        o = orch.setdefault(label, {"label": label, "chats": set(), "spawns": 0, "escalations": 0, "experts": 0,
                                    "runs": 0, "failed": 0, "durations": []})
        o["chats"].add(e["parent_session_id"])
        o["spawns"] += 1
        o["escalations"] += e["role"] == "escalation"
        o["experts"] += e["role"] == "expert"
        for r in end_by_child.get(e["child_id"], []):
            o["runs"] += 1
            o["failed"] += r["status"] == "failed"
            if r["duration_s"] is not None and r["status"] == "done":
                o["durations"].append(float(r["duration_s"]))
    orchestrators = []
    for o in orch.values():
        chats = len(o["chats"])
        orchestrators.append({
            "label": o["label"], "chats": chats, "spawns": o["spawns"],
            "spawns_per_chat": round(o["spawns"] / chats, 1) if chats else None,
            "escalations": o["escalations"], "escalations_per_chat": round(o["escalations"] / chats, 2) if chats else None,
            "experts": o["experts"], "child_fail_pct": _pct(o["failed"], o["runs"]),
            "median_child_s": _median(o["durations"]),
            "supervisor_interventions": sum(interventions[c] for c in o["chats"]),
        })
    orchestrators.sort(key=lambda o: -o["chats"])

    # --- escalation targets per project ------------------------------------
    esc = Counter((e["project_path"], agent_label(e["agent"], e["model"])) for e in escalations)
    esc_links = Counter((e["detail"] or {}).get("link") or "none" for e in escalations)
    escalation_rows = [{"project": p, "target": t, "count": n} for (p, t), n in esc.most_common()]

    # --- supervision --------------------------------------------------------
    sup: Dict[str, Dict[str, Any]] = {}
    for e in checks + sends + stops:
        label = agent_label(*(chat_ident.get(e["session_id"]) or (e["agent"], e["model"])))
        s = sup.setdefault(label, {"label": label, "schedules": set(), "checks": 0, "wakes": 0, "violations": 0,
                                   "stalls": 0, "skips": 0, "sends": 0, "stops": 0})
        s["schedules"].add(e["batch_id"])
        if e["kind"] == "sup_check":
            s["checks"] += 1
            s["wakes"] += e["status"] in WAKE_OUTCOMES
            s["violations"] += e["status"] == "violation"
            s["stalls"] += e["status"] == "stalled"
            s["skips"] += e["status"] == "skipped"
        elif e["kind"] == "sup_send":
            s["sends"] += 1
        else:
            s["stops"] += 1
    supervision = [{**s, "schedules": len(s["schedules"]), "wake_pct": _pct(s["wakes"], s["checks"])}
                   for s in sup.values()]
    supervision.sort(key=lambda s: -s["checks"])
    check_outcomes = Counter(e["status"] for e in checks)
    wake_states = Counter((e["detail"] or {}).get("state") or "?" for e in checks if e["status"] in WAKE_OUTCOMES)

    # --- daily spawns by role (one chart) ----------------------------------
    daily: Dict[str, Counter] = defaultdict(Counter)
    for e in spawns:
        daily[time.strftime("%Y-%m-%d", time.localtime(e["at"]))][e["role"] or "untiered"] += 1
    series = [{"day": d, **dict(c)} for d, c in sorted(daily.items())]

    failures = [{
        "at": e["at"], "child": f"CH-{int(e['session_id']):06d}" if e["session_id"] else "",
        "parent": f"CH-{int(e['parent_session_id']):06d}" if e["parent_session_id"] else "",
        "label": agent_label(e["agent"], e["model"]), "role": e["role"] or "untiered",
        "project": e["project_path"], "error": (e["detail"] or {}).get("error") or "",
    } for e in ends if e["status"] == "failed"][-25:][::-1]

    worker_durs = [float(e["duration_s"]) for e in ends
                   if e["role"] == "worker" and e["status"] == "done" and e["duration_s"] is not None]
    return {
        "success": True, "id": "orchestration", "selected_range": range_id, "project": project,
        "available_ranges": [{"id": r[0], "label": r[1]} for r in RANGES], "projects": projects,
        "backfilled": store.has_backfill(),
        "stats": {
            "orchestrated_chats": len(orchestrated), "spawns": len(spawns), "runs": len(ends),
            "failed_runs": sum(e["status"] == "failed" for e in ends),
            "escalations": len(escalations), "escalation_pct": _pct(len(escalations), len(workers)),
            "median_worker_s": _median(worker_durs), "checks": len(checks),
            "wakes": sum(check_outcomes[o] for o in WAKE_OUTCOMES), "sends": len(sends), "stops": len(stops),
            "untiered_spawns": sum(not e["role"] for e in spawns),
        },
        "by_tier": by_tier, "orchestrators": orchestrators, "escalations": escalation_rows,
        "escalation_links": dict(esc_links), "supervision": supervision,
        "check_outcomes": dict(check_outcomes), "wake_states": dict(wake_states),
        "daily": series, "failures": failures,
    }


def backfill(db, *, dry_run: bool = False) -> Dict[str, int]:
    """Import history recorded before the log existed: sub-agent children (one
    spawn + one final round each; tiers resolved from the chat's *current*
    playbook) and the retained supervision checks. Runs once."""
    from api.chat_ops import store as ops_store
    from api.orchestration_telemetry import record
    from api.subagents import store as sub_store

    if store.has_backfill():
        return {"skipped": 1}
    counts = {"spawns": 0, "rounds": 0, "checks": 0}
    conn = db._get_connection()
    try:
        ids = [r["id"] for r in conn.execute("SELECT id FROM subagent_children ORDER BY started_at, id")]
    finally:
        conn.close()
    for cid in ids:
        child = sub_store.get_child(db, cid)
        if not child:
            continue
        began = record._sqlite_epoch(child.started_at)
        ended = record._sqlite_epoch(child.finished_at)
        counts["spawns"] += 1
        if child.status in ("done", "failed", "cancelled") and ended:
            counts["rounds"] += 1
        if dry_run:
            continue
        batch = sub_store.get_batch(db, child.batch_id)
        if not batch:
            continue
        parent = db.get_chat_session_by_id(int(batch.parent_session_id)) or {}
        tier = record.resolve_tier(batch.user_id, batch.parent_session_id, profile_id=child.profile_id or "",
                                   agent=child.agent or "", model=child.model or "", effort=child.effort or "")
        base = dict(user_id=batch.user_id, session_id=child.session_id, parent_session_id=batch.parent_session_id,
                    project_path=parent.get("project_path") or "", profile_id=child.profile_id, agent=child.agent,
                    model=child.model, effort=child.effort, child_id=child.id, batch_id=batch.id, **tier)
        store.append("child_spawn", at=began or time.time(), turn_id=batch.turn_id,
                     detail={"title": (child.label or "")[:200], "backfilled": True}, **base)
        if child.status in ("done", "failed", "cancelled") and ended:
            detail = {"backfilled": True}
            if child.error:
                detail["error"] = child.error[:record.ERROR_SNIPPET]
            store.append("child_round_end", at=ended, status=child.status,
                         duration_s=max(0.0, ended - began) if began else None,
                         turn_id=child.query_id, detail=detail, **base)
    for op in ops_store.recent(limit=5000):
        if op.get("kind") != "supervise":
            continue
        for chk in ops_store.checks(op["id"]):
            counts["checks"] += 1
            if dry_run:
                continue
            d = chk.get("detail") or {}
            store.append("sup_check", at=chk["at"], status=chk["outcome"],
                         detail={**{k: d.get(k) for k in ("state", "trigger", "reason", "violations")
                                    if d.get(k) is not None}, "backfilled": True},
                         **record._schedule_meta(op))
    return counts

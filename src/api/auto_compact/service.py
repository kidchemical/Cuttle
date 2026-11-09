"""Auto-compact service: gather a chat's snapshot, decide, run the compact.

Entry points:

- ``evaluate(sid, trigger='turn_end')`` — Flask, a quiet period after a turn
  ends (``api.auto_compact.triggers``). Holds the chat's busy slot
  (``api.chat_delivery``) and publishes a live status while it compacts, so a
  send in the meantime waits like it would for a turn. Sub-agent children are
  skipped here: their turns may run in the parent's CLI process, out of
  Flask's sight, so they compact in ``preflight`` instead.
- ``preflight(sid, agent=…, cwd=…)`` — called by ``api.subagents.turns`` before
  a child turn runs, in whatever process runs it. The turn already owns the
  chat, so no busy slot is taken.

Both take the store's cross-process lease, so two compacts never overlap.
The compact itself is ``api.agent_context`` (native CLI summary or managed
fresh session); this module adds when, never how.
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from api.auto_compact import policy as pol
from api.auto_compact import store

FLAG = "auto_compact"
SETTING_KEY = "auto_compact"
# A native compact is a full model call (Claude /compact can take minutes).
LEASE_TTL = 900.0
PREFLIGHT_WAIT_SEC = 600.0
HEARTBEAT_SEC = 60.0
# Decisions that mean "not now, try again" (the milestone stays pending).
RETRY_REASONS = ("busy", "live_cli", "leased")
MAX_DONE_KEYS = 1000


# ── settings + policy ──────────────────────────────────────────────────────

def available() -> bool:
    try:
        from api.experimental import is_enabled

        return bool(is_enabled(FLAG))
    except Exception:
        return False


def settings() -> Dict[str, Any]:
    try:
        from managers.settings_manager import get_settings_manager

        stored = get_settings_manager().get_setting(SETTING_KEY, None)
    except Exception:
        stored = None
    return pol.merge_settings(stored)


def save_settings(patch: Dict[str, Any]) -> Dict[str, Any]:
    """Validate ``patch`` (raises ``PolicyError``) and merge it into the stored settings."""
    clean = pol.validate_patch(patch)
    from managers.settings_manager import get_settings_manager

    def merge(current):
        out = dict(current) if isinstance(current, dict) else {}
        for key, value in clean.items():
            if isinstance(value, dict):
                out[key] = {**(out.get(key) if isinstance(out.get(key), dict) else {}), **value}
            else:
                out[key] = value
        return out

    get_settings_manager().update_setting(SETTING_KEY, merge)
    return settings()


def _db(db=None):
    if db is not None:
        return db
    from api.auth_db import get_auth_db

    return get_auth_db()


def _sid(session_id: Any) -> int:
    return int(str(session_id).strip().removeprefix("db_session_").removeprefix("CH-"))


def managed_reason(db, row) -> str:
    """Why the chat counts as agent-driven ('' when it is an ordinary chat)."""
    sid = int(row["id"])
    if row.get("parent_session_id") not in (None, ""):
        return "sub-agent"
    try:
        from api.chat_ops import store as ops

        for op in ops.involving(sid, states=("pending", "dispatching")):
            if op.get("kind") == "supervise":
                return "supervised" if int(op["target"]) == sid else "supervisor"
    except Exception:
        pass
    try:
        from api.orchestrator import store as orch

        if orch.binding(sid):
            return "orchestrator"
    except Exception:
        pass
    return ""


def chat_policy(session_id: Any, *, db=None) -> Dict[str, Any]:
    """The chat's effective policy, its override and why it is managed."""
    db = _db(db)
    sid = _sid(session_id)
    row = db.get_chat_session_by_id(sid) or {}
    reason = managed_reason(db, row) if row else ""
    override = store.get_override(sid)
    return {
        "available": available(),
        "session_id": sid,
        "managed": bool(reason),
        "managed_reason": reason,
        "override": override,
        "policy": pol.effective(settings(), managed=bool(reason), override=override),
        "inherited": pol.effective(settings(), managed=bool(reason)),
    }


def set_chat_override(session_id: Any, raw: Optional[Dict[str, Any]], *, db=None) -> Dict[str, Any]:
    """Replace the chat's override (``{}``/None: inherit). Clears a failure streak."""
    sid = _sid(session_id)
    clean = pol.validate_override(raw)
    store.set_override(sid, clean)
    store.patch_state(sid, fail_streak=0, ineffective=0)
    store.record(sid, "override", {"override": clean})
    return chat_policy(sid, db=db)


# ── snapshot ───────────────────────────────────────────────────────────────

def _agent_and_cwd(db, row, agent=None, cwd=None) -> Tuple[str, str]:
    aid, path = (agent or "").strip().lstrip("/").split(" ")[0].lower(), (cwd or "").strip()
    if not aid or not path:
        from api.chat_ops import service as ops

        if not aid:
            aid = (ops.chat_agent(db, row) or "").strip().lower()
        if not path:
            path = ops.checkout_state(row, db)[0] or ""
    return aid, path


def _latest_assistant_id(db, sid: int) -> Optional[int]:
    try:
        rows = db.get_messages(sid, limit=20) or []
    except Exception:
        return None
    ids = [int(m["id"]) for m in rows if m.get("role") == "assistant" and m.get("id") is not None]
    return max(ids) if ids else None


def _done_items(items, out=None, prefix=""):
    out = out if out is not None else set()
    for item in items or []:
        if item.get("done"):
            out.add(f"{prefix}{item.get('id')}")
        _done_items(item.get("children") or [], out, prefix)
    return out


def done_keys(db, user_id: int, sid: int) -> List[str]:
    """Checked items of the chat's own Tasks lists (``gizmo:item``), any status."""
    try:
        from api.gizmos import tasks as gizmo_tasks

        # A list whose last item was just checked off archives itself.
        lists = [t for status in ("active", "archived")
                 for t in gizmo_tasks.list_tasks(user_id=int(user_id), session_id=sid, status=status, db=db)]
    except Exception:
        return []
    keys = set()
    for t in lists:
        own = t.get("session_id") == sid or sid in [int(x) for x in (t.get("linked_sessions") or [])]
        if not own or t.get("scope") == "project":
            continue
        _done_items((t.get("payload") or {}).get("items"), keys, prefix=f"{t.get('id')}:")
    return sorted(keys)


def _fresh_available(agent: str, cwd: str) -> bool:
    try:
        from api.agent_harness.catalog import get_agent

        pair = get_agent(agent, project_path=cwd or None)
        return bool(pair and pair[0].resume)
    except Exception:
        return False


def _live_cli(sid: int) -> bool:
    try:
        from api.chat_run_registry import has_live_process

        return bool(has_live_process(sid))
    except Exception:
        return False


def _busy(sid: int) -> bool:
    try:
        from api import chat_delivery

        return bool(chat_delivery.is_busy(sid))
    except Exception:
        return False


def _observe(db, row, policy: Dict[str, Any], status: Dict[str, Any], *, count_turn: bool) -> Dict[str, Any]:
    """Advance the chat's state for a turn that just ended (once per assistant row).

    Counts the turn, checks whether the previous compact brought the fill
    under the threshold, and notes a Tasks milestone (items checked off since
    the last look; the first look is the baseline)."""
    sid, uid = int(row["id"]), int(row["user_id"])
    state = store.get_state(sid)
    fields: Dict[str, Any] = {}
    latest = _latest_assistant_id(db, sid)
    turns = int(state.get("turns") or 0)
    if count_turn and latest is not None and latest != state.get("last_seen_message"):
        turns += 1
        fields.update(turns=turns, last_seen_message=latest)
        percent, used = float(status.get("percent") or 0), int(status.get("used_tokens") or 0)
        last_compact = state.get("last_compact_turn")
        over = pol.over_threshold(policy, percent, used)
        if last_compact is not None and turns == int(last_compact) + 1 and used > 0:
            fields["ineffective"] = int(state.get("ineffective") or 0) + 1 if over else 0
        elif used > 0 and not over:
            fields.update(ineffective=0, fail_streak=0)
    done = done_keys(db, uid, sid)
    prev = state.get("done_keys")
    if prev is None:
        fields["done_keys"] = done[-MAX_DONE_KEYS:]
    else:
        new = sorted(set(done) - set(prev))
        if new:
            fields["done_keys"] = sorted(set(prev) | set(done))[-MAX_DONE_KEYS:]
            fields["milestone_pending"] = True
            fields["milestone_items"] = new[-10:]
    if fields:
        state = store.patch_state(sid, **fields)
    return state


def _snapshot(state: Dict[str, Any], status: Dict[str, Any], *, agent: str, cwd: str,
              busy: bool, live_cli: bool) -> Dict[str, Any]:
    turns = int(state.get("turns") or 0)
    last_compact, last_attempt = state.get("last_compact_turn"), state.get("last_attempt_turn")
    return {
        "supported": bool(status.get("success") and status.get("supported", True)),
        "percent": float(status.get("percent") or 0),
        "used_tokens": int(status.get("used_tokens") or 0),
        "limit_tokens": int(status.get("limit_tokens") or 0),
        "token_source": status.get("token_source"),
        "native_available": bool(status.get("compact_available")),
        "fresh_available": _fresh_available(agent, cwd),
        "busy": busy,
        "live_cli": live_cli,
        "turns_since_compact": None if last_compact is None else turns - int(last_compact),
        "turns_since_attempt": None if last_attempt is None else turns - int(last_attempt),
        "fail_streak": int(state.get("fail_streak") or 0),
        "ineffective": int(state.get("ineffective") or 0),
        "milestone": bool(state.get("milestone_pending")),
    }


# ── evaluate + run ─────────────────────────────────────────────────────────

def _result(sid, **fields):
    return {"schema_version": 1, "handle": f"CH-{int(sid):06d}", "compact": False, **fields}


def evaluate(session_id: Any, *, trigger: str = "turn_end", db=None, agent: Optional[str] = None,
             cwd: Optional[str] = None, hold_busy: bool = True, count_turn: bool = True,
             dry_run: bool = False) -> Dict[str, Any]:
    """Check one chat and compact it when its policy says so. Never raises."""
    sid = _sid(session_id)
    try:
        return _evaluate(sid, trigger=trigger, db=_db(db), agent=agent, cwd=cwd, hold_busy=hold_busy,
                         count_turn=count_turn, dry_run=dry_run)
    except Exception as exc:  # noqa: BLE001 — a failed check must never reach a turn
        return _result(sid, reason="error", error=f"{type(exc).__name__}: {exc}")


def _evaluate(sid, *, trigger, db, agent, cwd, hold_busy, count_turn, dry_run):
    if not available():
        return _result(sid, reason="flag_off")
    row = db.get_chat_session_by_id(sid)
    if not row or not row.get("is_active", 1):
        return _result(sid, reason="no_chat")
    reason = managed_reason(db, row)
    if trigger == "turn_end" and reason == "sub-agent":
        return _result(sid, reason="preflight_only")
    policy = pol.effective(settings(), managed=bool(reason), override=store.get_override(sid))
    aid, path = _agent_and_cwd(db, row, agent, cwd)
    if not aid:
        return _result(sid, reason="no_agent", policy=policy)
    from api.agent_context import get_agent_context_status

    status = get_agent_context_status(chat_session_id=str(sid), agent_id=aid, cwd=path or None)
    state = _observe(db, row, policy, status, count_turn=count_turn and not dry_run)
    snap = _snapshot(state, status, agent=aid, cwd=path, busy=hold_busy and _busy(sid),
                     live_cli=hold_busy and _live_cli(sid))
    decision = pol.decide(policy, snap)
    base = dict(agent=aid, trigger=trigger, policy=policy, snapshot=snap, decision=decision,
                managed_reason=reason)
    if not decision["compact"]:
        if not dry_run:
            fields = {"last_decision": decision["reason"], "last_checked_at": time.time()}
            if decision["reason"] not in RETRY_REASONS:
                fields["milestone_pending"] = False
            if decision["reason"] == "failing" and state.get("last_decision") != "failing":
                store.record(sid, "gave_up", {"fail_streak": snap["fail_streak"]})
            store.patch_state(sid, **fields)
        return _result(sid, reason=decision["reason"], **base)
    if dry_run:
        return _result(sid, reason=decision["reason"], would_compact=True, **base)
    out = _run(db, row, aid, path, decision, snap, policy, state, hold_busy=hold_busy)
    return _result(sid, **base, **out)


def _label(agent: str) -> str:
    return (agent or "agent")[:1].upper() + (agent or "agent")[1:]


def _run(db, row, agent, cwd, decision, snap, policy, state, *, hold_busy):
    sid = int(row["id"])
    holder = f"{os.getpid()}:{uuid.uuid4().hex[:8]}"
    if not store.claim_lease(sid, holder, LEASE_TTL):
        return {"reason": "leased"}
    turn, claimed, beat = None, False, None
    from api import chat_delivery, chat_live_status

    try:
        if hold_busy:
            if not chat_delivery.try_begin(sid):
                return {"reason": "busy"}
            claimed = True
            turn = chat_delivery.current_turn(sid)
        before = round(snap["percent"])
        message = f"Auto-compacting {_label(agent)} context ({before}%)…"
        chat_live_status.set_live_status(sid, message, turn=turn, followup=True)
        # Clients treat a status older than 3 minutes as stale; a compact can run longer.
        beat = threading.Event()

        def heartbeat():
            # Stop (a newer turn token) ends the status; the CLI's compact still finishes.
            while not beat.wait(HEARTBEAT_SEC) and not (claimed and chat_delivery.is_stale_turn(sid, turn)):
                chat_live_status.set_live_status(sid, message, turn=turn, followup=True)

        threading.Thread(target=heartbeat, name=f"auto-compact-beat-{sid}", daemon=True).start()
        from api import agent_context

        run = (agent_context.fresh_compact_agent_context if decision["mode"] == "fresh"
               else agent_context.compact_agent_context)
        started = time.time()
        result = run(chat_session_id=str(sid), agent_id=agent, cwd=cwd or None) or {}
        ok = bool(result.get("success"))
        after_status = result.get("status") if isinstance(result.get("status"), dict) else {}
        after = after_status.get("percent") if after_status.get("success") else None
        turns = int(state.get("turns") or 0)
        detail = {"mode": decision["mode"], "trigger": decision["trigger"], "before": before,
                  "after": None if after is None else round(float(after)),
                  "used_tokens": snap["used_tokens"], "threshold": policy["percent"],
                  "elapsed_ms": int((time.time() - started) * 1000),
                  "milestone_items": (state.get("milestone_items") or []) if decision["trigger"] == "milestone" else []}
        if ok:
            store.patch_state(sid, last_compact_turn=turns, last_attempt_turn=turns, fail_streak=0,
                              milestone_pending=False, last_compact_at=time.time(), last_decision="compacted")
            store.record(sid, "compacted", detail)
        else:
            streak = int(state.get("fail_streak") or 0) + 1
            detail["error"] = str(result.get("error") or "compact failed")[:500]
            store.patch_state(sid, last_attempt_turn=turns, fail_streak=streak, last_decision="failed")
            store.record(sid, "failed", detail)
        if ok or int(state.get("fail_streak") or 0) == 0:
            _notice(db, sid, agent, ok, detail)
        return {"compact": ok, "reason": "compacted" if ok else "failed", "detail": detail,
                "error": None if ok else detail["error"]}
    finally:
        if beat is not None:
            beat.set()
        try:
            chat_live_status.clear_live_status(sid, turn=turn)
        except Exception:
            pass
        if claimed:
            try:
                chat_delivery.end(sid, turn=turn, publish=False)
            except Exception:
                pass
        store.release_lease(sid, holder)


def notice_detail(detail: Dict[str, Any]) -> str:
    """One-line detail of a compact notice ("72% → 15% · over 70%"). Pure."""
    parts = []
    before, after = detail.get("before"), detail.get("after")
    if before is not None and after is not None:
        parts.append(f"{before}% → {after}%")
    elif before is not None:
        parts.append(f"was {before}%")
    if detail.get("trigger") == "milestone":
        parts.append("Tasks milestone")
    elif detail.get("threshold") is not None:
        parts.append(f"over {detail['threshold']}%")
    if detail.get("mode") == "fresh":
        parts.append("fresh session")
    if detail.get("error"):
        parts.append(str(detail["error"])[:160])
    return " · ".join(parts)


def _notice(db, sid, agent, ok, detail):
    label = f"{_label(agent)} context auto-compacted" if ok else f"{_label(agent)} auto-compact failed"
    text = notice_detail(detail)
    try:
        db.add_message(sid, "system", f"{label} · {text}" if text else label, metadata={
            "kind": "context-compact", "auto_compact": True,
            "timeline_notice": {"type": "compact", "label": label, "detail": text,
                                "state": "done" if ok else "error"}})
    except Exception:
        pass  # the history row is the record; the notice is only the user's view


def preflight(session_id: Any, *, agent: str, cwd: str = "", db=None) -> Dict[str, Any]:
    """Before a sub-agent turn runs: wait out another process's compact of this
    chat, then compact it here if its policy says so. Never raises."""
    sid = _sid(session_id)
    if not available():
        return _result(sid, reason="flag_off")
    deadline = time.time() + PREFLIGHT_WAIT_SEC
    try:
        while store.lease_holder(sid) and time.time() < deadline:
            time.sleep(1.0)
    except Exception:
        pass
    return evaluate(sid, trigger="preflight", db=db, agent=agent, cwd=cwd, hold_busy=False)


def describe(session_id: Any, *, db=None, history_limit: int = 10) -> Dict[str, Any]:
    """Policy, state, history and a dry-run decision (CLI / gauge popover)."""
    sid = _sid(session_id)
    out = chat_policy(sid, db=db)
    state = store.get_state(sid)
    out["state"] = {k: state.get(k) for k in ("turns", "last_compact_at", "last_decision", "fail_streak",
                                              "ineffective", "milestone_pending")}
    out["history"] = store.history(sid, history_limit)
    return out

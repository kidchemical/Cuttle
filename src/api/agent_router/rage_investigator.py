"""Background rage investigation — delegated to a configurable agent.

Local correlation (repeats.py) flags the obvious matches instantly, but it is
still string matching. The deep pass asks a *real agent* (configurable, default
Cursor Auto — free tier) to read the session evidence and decide **exactly
which attempts failed to solve the user's problem**, then writes the verdicts
back into the outcomes store.

Key properties:
* fully parallel — started as a daemon thread, never blocks the escalated task
* one investigation per session at a time
* configured at ``settings.json → agent_router.rage.investigator``
  ``{enabled, agent, model, timeout_s}`` — editable on the Router page
* verdict JSON is tolerant-parsed; a garbage reply changes nothing
"""

from __future__ import annotations

import json
import re
import threading
from typing import Any, Dict, List, Optional

import api.agent_router.logging_events as log
from api.agent_router.frustration import load_rage_config
from api.agent_router.outcomes import list_outcomes, set_feedback
from api.agent_harness.kernel import run_agent_web_command

_LOCK = threading.Lock()
_ACTIVE: set = set()

_INVESTIGATOR_SYSTEM_NOTE = (
    "You are auditing routing quality, not doing the user's task. Below is a "
    "complaint the user sent after several agent attempts reported success, "
    "plus the prior asks and their outcome metadata. Decide which attempts "
    "FAILED to solve the actual problem. Reply with ONLY a JSON array like "
    '[{"decision_id":"abc123","verdict":"bad","reason":"short why"}] — verdict '
    'is "bad" (did not solve it), "good" (unrelated / actually fine), or '
    '"unclear". No other text.'
)


def build_evidence_prompt(
    complaint: str,
    phrase: str,
    asks: List[Dict[str, Any]],
    outcomes_by_decision: Dict[str, List[Dict[str, Any]]],
) -> str:
    lines = [f'User complaint: "{complaint}" (matched frustration phrase: {phrase!r})', "", "Prior attempts:"]
    for a in asks:
        did = str(a.get("decision_id") or "")
        rows = outcomes_by_decision.get(did) or []
        final = rows[0] if rows else {}
        fb = final.get("user_feedback")
        lines.append(
            f"- decision {did} | asked: {str(a.get('norm') or '')[:200]} | "
            f"target: {final.get('target_agent', '?')}/{final.get('target_model') or 'default'} | "
            f"failure_kind: {final.get('failure_kind', 'n/a')} | "
            f"latency_ms: {final.get('latency_ms', 'n/a')} | "
            f"feedback so far: {fb if fb is not None else 'none'}"
        )
    lines.append("")
    lines.append(_INVESTIGATOR_SYSTEM_NOTE)
    return "\n".join(lines)


def parse_verdicts(text: str) -> List[Dict[str, str]]:
    """Tolerant JSON-array extraction from an agent reply."""
    if not text:
        return []
    m = re.search(r"\[[\s\S]*\]", text)
    if not m:
        return []
    try:
        raw = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    out: List[Dict[str, str]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        did = str(item.get("decision_id") or "").strip()
        verdict = str(item.get("verdict") or "").strip().lower()
        if did and verdict in ("good", "bad", "unclear"):
            out.append({"decision_id": did, "verdict": verdict,
                        "reason": str(item.get("reason") or "")[:200]})
    return out


def apply_verdicts(verdicts: List[Dict[str, str]]) -> int:
    """Write good/bad feedback per decision. Cancelled finals are skipped."""
    applied = 0
    for v in verdicts:
        did = v.get("decision_id") or ""
        verdict = v.get("verdict") or ""
        if not did or verdict not in ("good", "bad"):
            continue
        try:
            rows = list_outcomes(decision_id=did, limit=1)
            if rows and str(rows[0].get("failure_kind") or "") == "cancelled":
                continue
            if set_feedback(verdict, decision_id=did):
                applied += 1
        except Exception:
            continue
    return applied


def maybe_start_investigation(
    *,
    session_id: Any,
    frustration_meta: Dict[str, Any],
    project_path: Optional[str] = None,
    _config: Optional[Dict[str, Any]] = None,
) -> bool:
    """Kick off a background investigation. Returns False if skipped/blocked.

    Never raises, never blocks: the thread is fire-and-forget. ``_config``
    overrides the loaded rage config (tests / callers with fresh state).
    """
    try:
        cfg = _config or load_rage_config()
        inv = cfg.get("investigator") or {}
        if not inv.get("enabled", True):
            return False
        asks = list((frustration_meta or {}).get("evidence") or [])
        if not asks:
            return False

        key = str(session_id)
        with _LOCK:
            if key in _ACTIVE:
                return False
            _ACTIVE.add(key)

        t = threading.Thread(
            target=_investigate,
            kwargs=dict(
                session_id=session_id,
                complaint=str(frustration_meta.get("complaint") or ""),
                phrase=str(frustration_meta.get("phrase") or ""),
                asks=asks,
                agent=str(inv.get("agent") or "jev"),
                model=str(inv.get("model") or "") or None,
                timeout_s=int(inv.get("timeout_s") or 30),
                project_path=project_path,
            ),
            daemon=True,
            name=f"rage-investigation-{key}",
        )
        t.start()
        return True
    except Exception as exc:
        log.router_log("rage_investigation_start_failed", error=str(exc)[:140])
        with _LOCK:
            _ACTIVE.discard(str(session_id))
        return False


def _investigate(
    *,
    session_id: Any,
    complaint: str,
    phrase: str,
    asks: List[Dict[str, Any]],
    agent: str,
    model: Optional[str],
    timeout_s: int,
    project_path: Optional[str],
) -> None:
    try:
        outcomes_by_decision: Dict[str, List[Dict[str, Any]]] = {}
        for a in asks:
            did = str(a.get("decision_id") or "")
            if did:
                outcomes_by_decision[did] = list_outcomes(decision_id=did, limit=5)

        verdicts: List[Dict[str, str]] = []
        used_agent = agent or "jev"
        if used_agent.strip().lower() == "jev":
            try:
                from api.jev.client import jev_available
                from api.jev.rage import investigate_attempts

                if jev_available():
                    verdicts = investigate_attempts(
                        complaint=complaint,
                        phrase=phrase,
                        asks=asks,
                        outcomes_by_decision=outcomes_by_decision,
                    )
                else:
                    used_agent = "cursor"
            except Exception as exc:
                log.router_log("rage_jev_failed", error=str(exc)[:160])
                used_agent = "cursor"
                verdicts = []

        if used_agent.strip().lower() != "jev":
            prompt = build_evidence_prompt(complaint, phrase, asks, outcomes_by_decision)
            result = run_agent_web_command(
                used_agent,
                prompt,
                None,
                project_path=project_path,
                model_override=model if used_agent != "cursor" else (model or "auto"),
                timeout=float(timeout_s if used_agent != "cursor" else max(float(timeout_s), 120.0)),
            )
            text = str((result or {}).get("response") or "")
            verdicts = parse_verdicts(text)

        applied = apply_verdicts(verdicts)
        log.router_log(
            "rage_investigation_done",
            session_id=str(session_id),
            agent=used_agent,
            model=model or "(default)",
            verdicts=len(verdicts),
            applied=applied,
        )
    except Exception as exc:
        log.router_log("rage_investigation_failed", error=str(exc)[:160])
    finally:
        with _LOCK:
            _ACTIVE.discard(str(session_id))

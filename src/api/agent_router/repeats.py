"""Silent-failure escalation via repeated prompts.

The hardest failure to see is the model completing successfully and being
wrong: exit code 0, no ``[FAIL]`` — nothing the outcome store can classify.
The evidence that exists is the user re-sending the same request ("fix this
bug" … again … and again). This module treats a near-identical re-prompt in
the same session as negative evidence for the prior attempt:

* the next attempt escalates one hop up the decision's target chain per repeat
* the prior (claimed-successful) attempt is marked ``user_feedback=bad`` in
  the outcomes store — real signal, no tokens, no judge model

Cancelled turns are never treated as quality evidence (hard router rule).
"""

from __future__ import annotations

import difflib
import re
import threading
import time
from typing import Any, Dict, Optional, Tuple

import api.agent_router.logging_events as log
from api.agent_router.frustration import detect_frustration
from api.agent_router.outcomes import list_outcomes, set_feedback
from api.agent_router.types import RouterConfig, RoutingDecision

# A re-prompt counts as "the same ask" at/above this similarity…
SIMILARITY_THRESHOLD = 0.90
# …or when the shorter prompt appears inside the longer one (≥ this length,
# so "run tests" inside "run tests for auth then deploy" does NOT trigger).
CONTAINMENT_MIN_CHARS = 14
# Repeats older than this no longer escalate (fresh ask, not a complaint).
REPEAT_WINDOW_SECONDS = 24 * 3600.0
# Registry entries kept per session (most recent prompts).
_MAX_ENTRIES = 8

_LOCK = threading.Lock()
# session_key -> list of {norm, ts, count, decision_id, target_key}
_REGISTRY: Dict[str, list] = {}


def normalize_prompt(text: str) -> str:
    s = (text or "").strip().lower()
    s = re.sub(r"^/[a-z0-9-]+\s+", "", s)  # strip a leading /agent slash
    s = re.sub(r"\s+", " ", s)
    return s[:500]


def _is_repeat(norm: str, entry: Dict[str, Any], now: float) -> bool:
    if now - float(entry.get("ts") or 0) > REPEAT_WINDOW_SECONDS:
        return False
    other = str(entry.get("norm") or "")
    if norm == other:
        return True
    if difflib.SequenceMatcher(None, norm, other).ratio() >= SIMILARITY_THRESHOLD:
        return True
    shorter, longer = sorted((norm, other), key=len)
    if (
        len(shorter) >= CONTAINMENT_MIN_CHARS
        and shorter in longer
        and len(shorter) / max(1, len(longer)) >= 0.5
    ):
        return True
    return False


def _prior_was_cancelled(decision_id: str) -> bool:
    try:
        rows = list_outcomes(decision_id=decision_id, limit=10)
        if not rows:
            return False
        last = rows[0]  # newest attempt of that decision
        return str(last.get("failure_kind") or "") == "cancelled"
    except Exception:
        return False


def _mark_prior_bad(decision_id: str) -> None:
    """Rate the prior attempt bad — unless it was cancelled (never evidence)."""
    if not decision_id:
        return
    try:
        rows = list_outcomes(decision_id=decision_id, limit=10)
        if rows and str(rows[0].get("failure_kind") or "") == "cancelled":
            return
        set_feedback("bad", decision_id=decision_id)
    except Exception as exc:
        log.router_log("repeat_feedback_failed", decision_id=decision_id, error=str(exc)[:120])


def asks_snapshot(key: str) -> list:
    """Recent asks (decision_id + normalized text) for investigator evidence."""
    with _LOCK:
        entries = list(_REGISTRY.get(key, []))
    return [
        {"decision_id": str(e.get("decision_id") or ""), "norm": str(e.get("norm") or "")[:300]}
        for e in entries
        if e.get("decision_id")
    ]


def reset_session(session_id: Any) -> None:
    with _LOCK:
        _REGISTRY.pop(_sid_key(session_id), None)


def _sid_key(session_id: Any) -> str:
    return str(session_id) if session_id is not None else "_none"


def evaluate_repeat(
    decision: Any,
    prompt: str,
    session_id: Any,
    *,
    cfg: Optional[RouterConfig] = None,
    now: Optional[float] = None,
) -> Tuple[Any, Dict[str, Any]]:
    """Detect a re-sent (or frustration-signalling) prompt and escalate.

    Two evidence paths, one chain-hop mechanism:

    * **Repeat** — a near-identical re-prompt in the same session escalates
      one hop per repeat (the prior "successful" attempt failed silently).
    * **Frustration** — "still not fixed" / "you said you fixed it" is
      explicit negative feedback even when the wording changed: flags the
      session's recent routed attempts bad and escalates too.

    Returns (decision, meta). meta keys: repeat_count, escalated_from,
    prior_decision_id, chain_exhausted, frustration. Never raises.
    """
    meta: Dict[str, Any] = {"repeat_count": 0, "escalated_from": None, "frustration": None}
    try:
        norm = normalize_prompt(prompt)
        if not norm:
            return decision, meta
        now_ts = float(now if now is not None else time.time())
        key = _sid_key(session_id)
        phrase = detect_frustration(prompt)

        with _LOCK:
            entries = _REGISTRY.setdefault(key, [])
            prior = next((e for e in reversed(entries) if _is_repeat(norm, e, now_ts)), None)
            prior_rage = max((int(e.get("rage_count") or 0) for e in entries), default=0)
            count = 0
            if prior is not None:
                count = int(prior.get("count") or 0) + 1
            elif phrase:
                # rephrased frustration still compounds across the session
                count = prior_rage + 1
            entries.append({
                "norm": norm, "ts": now_ts, "count": count,
                "rage_count": count if phrase else prior_rage,
                "decision_id": decision.decision_id,
                "target_key": decision.target.key(),
            })
            del entries[:-_MAX_ENTRIES]

        if count == 0:
            return decision, meta

        meta["repeat_count"] = count
        if prior is not None:
            prior_decision_id = str(prior.get("decision_id") or "")
        elif entries:
            prior_decision_id = str(entries[-2].get("decision_id") or "") if len(entries) >= 2 else ""
        else:
            prior_decision_id = ""
        meta["prior_decision_id"] = prior_decision_id

        if prior and _prior_was_cancelled(prior_decision_id) and not phrase:
            # A cancelled turn is not quality evidence — restart the count.
            # Explicit frustration still acts: direct user testimony outranks
            # the inference ban (feedback on cancelled attempts stays skipped).
            meta["repeat_count"] = 0
            with _LOCK:
                entries = _REGISTRY.get(key, [])
                if entries and entries[-1]["norm"] == norm:
                    entries[-1]["count"] = 0
            return decision, meta

        if phrase:
            # Local, free correlation: strip the rage phrase, match the
            # remainder against the session's prior asks — flag only attempts
            # actually about this issue (never a blind "last N" window).
            context = _complaint_context(norm, phrase)
            flagged_ids = _flag_correlated_attempts(key, context)
            meta["frustration"] = {
                "phrase": phrase,
                "complaint": norm[:300],
                "flagged_decisions": flagged_ids,
                "context": context[:120],
                "correlation": "matched" if flagged_ids else "fallback",
                "evidence": asks_snapshot(key),
            }
            log.router_log(
                "frustration_detected",
                decision_id=decision.decision_id,
                phrase=phrase,
                repeats=count,
                flagged=flagged_ids,
                correlation=meta["frustration"]["correlation"],
            )
        else:
            _mark_prior_bad(prior_decision_id)

        chain = [decision.target, decision.escalation_target,
                 *(decision.fallbacks if decision.fallbacks is not None
                   else (cfg.fallbacks.ordered if cfg else []))]
        k = min(count, len(chain) - 1)
        prior_target = decision.target
        decision.target = chain[k]
        decision.escalation_target = (
            chain[k + 1] if k + 1 < len(chain) else (cfg.escalation_target if cfg else prior_target)
        )
        decision.fallbacks = chain[k + 2:] if k + 2 < len(chain) else None
        meta["escalated_from"] = prior_target.key()
        meta["chain_exhausted"] = count >= len(chain) - 1
        if decision.raw is None:
            decision.raw = {}
        decision.raw["repeat_count"] = count
        decision.reason = (
            f"{'frustration signalled' if phrase else 'repeated request'} #{count + 1} — "
            f"prior attempts did not resolve the ask; escalated to {decision.target.key()}"
        )[:240]
        log.router_log(
            "repeated_request_escalation",
            decision_id=decision.decision_id,
            repeats=count,
            frustration=bool(phrase),
            escalated_from=prior_target.key(),
            escalated_to=decision.target.key(),
            exhausted=meta["chain_exhausted"],
        )
        return decision, meta
    except Exception as exc:
        log.router_log("repeat_evaluation_failed", error=str(exc)[:160])
        return decision, meta


def _complaint_context(norm: str, phrase: str) -> str:
    """The complaint minus the rage phrase — the issue description, if any."""
    ctx = norm.replace(str(phrase or "").lower(), " ")
    ctx = re.sub(r"\b(still|again|yet|ugh|wtf|wth|wth|please|just|even|still|really|actually)\b", " ", ctx)
    ctx = re.sub(r"[^\w\s-]", " ", ctx)
    return re.sub(r"\s+", " ", ctx).strip()


def _ask_matches_context(ask: str, context: str) -> bool:
    """Does a prior ask relate to the complaint's issue description?"""
    if not ask or not context or len(context) < 8:
        return False
    if ask == context:
        return True
    if difflib.SequenceMatcher(None, ask, context).ratio() >= 0.55:
        return True
    # significant overlap: most complaint keywords present in the ask
    words = [w for w in context.split() if len(w) >= 4]
    if not words:
        return False
    hits = sum(1 for w in words if w in ask)
    return hits >= max(2, int(len(words) * 0.6))


def _flag_correlated_attempts(key: str, context: str, *, max_flag: int = 3) -> list:
    """Flag attempts whose ask correlates with the complaint. Cancel-safe.

    Falls back to the single most recent prior attempt when nothing correlates
    (the complaint must be about *something* recent). Returns flagged decision ids.
    """
    flagged: list = []
    try:
        with _LOCK:
            entries = list(_REGISTRY.get(key, []))
        prior_entries = [e for e in entries[:-1] if e.get("decision_id")]  # exclude current
        if not prior_entries:
            return []

        # newest first, most-correlated first
        candidates: list = []
        remaining = list(reversed(prior_entries))
        if context:
            for e in remaining:
                if len(candidates) >= max_flag:
                    break
                if _ask_matches_context(str(e.get("norm") or ""), context):
                    candidates.append(e)
        if not candidates:
            # no correlation found — the single most recent prior attempt only
            candidates = [prior_entries[-1]]

        for e in candidates:
            did = str(e.get("decision_id") or "")
            if not did:
                continue
            rows = list_outcomes(decision_id=did, limit=1)
            if rows and str(rows[0].get("failure_kind") or "") == "cancelled":
                continue  # cancelled — never quality evidence
            set_feedback("bad", decision_id=did)
            flagged.append(did)
    except Exception as exc:
        log.router_log("frustration_flagging_failed", error=str(exc)[:120])
    return flagged


def build_repeat_handoff(prompt: str, meta: Dict[str, Any]) -> str:
    """Prepend escalation context so the next target knows why it is re-running."""
    count = int(meta.get("repeat_count") or 0)
    if count <= 0:
        return prompt
    prior = str(meta.get("escalated_from") or "the prior target")
    fr = meta.get("frustration") or {}
    note = (
        f"[Cuttle router: repeated request — attempt #{count + 1}. The prior attempt "
        f"({prior}) completed and reported success, but the user is re-asking, so treat "
        f"the earlier result as unverified. Find what was actually missed and verify the "
        f"fix end-to-end before claiming success.]"
    )
    if fr.get("phrase"):
        note = (
            f"[Cuttle router: the user is frustrated — they said {fr.get('phrase')!r} after "
            f"{count} prior attempt(s) that reported success. Prior attempts ({prior} and "
            f"before) did not actually resolve the problem. Re-diagnose from scratch instead "
            f"of trusting earlier conclusions, and verify the fix end-to-end before "
            f"claiming success.]"
        )
    return f"{prompt}\n\n---\n{note}"

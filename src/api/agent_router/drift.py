"""Phase B (basic) — "Is it sucking?" quality-regression detection.

Compares each target's recent fail rate against its own baseline. A sudden,
significant rise is a **quality regression signal** (never an accusation): the
target is demoted for a short TTL and the router temporarily routes around it.

Demotions persist under ``settings.json`` → ``agent_router.demotions`` so they
survive restarts and stay visible/editable to agents and the Router page.

Hard constraint honored here: ``cancelled`` attempts are never quality evidence.
"""

from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Optional, Tuple

from api.agent_router import logging_events as log
from api.agent_router.outcomes import outcomes_since

SETTINGS_KEY = "agent_router"
DEMOTION_SUBKEY = "demotions"

# fail kinds that count as quality evidence
_FAIL_KINDS = {"transport", "task"}


def _settings_raw() -> Dict[str, Any]:
    from managers.settings_manager import get_settings_manager

    sm = get_settings_manager()
    try:
        sm.reload()
    except Exception:
        pass
    raw = sm.get_setting(SETTINGS_KEY)
    return raw if isinstance(raw, dict) else {}


def _write_demotions(demotions: Dict[str, Any]) -> None:
    from managers.settings_manager import get_settings_manager

    raw = _settings_raw()
    raw[DEMOTION_SUBKEY] = demotions
    get_settings_manager().set_setting(SETTINGS_KEY, raw)


# ── statistics ───────────────────────────────────────────────────────────────

def _fail_stats(rows: List[Dict[str, Any]]) -> Tuple[int, int]:
    """(attempts, failures) — cancellations excluded from both."""
    attempts = 0
    failures = 0
    for row in rows:
        kind = str(row.get("failure_kind") or "")
        if kind == "cancelled":
            continue
        attempts += 1
        if kind in _FAIL_KINDS:
            failures += 1
    return attempts, failures


def _z_score(p_w: float, n_w: int, p_b: float, n_b: int) -> float:
    """Two-proportion z-score for window vs baseline fail rates."""
    if n_w <= 0 or n_b <= 0:
        return 0.0
    pooled = (p_w * n_w + p_b * n_b) / (n_w + n_b)
    denom = math.sqrt(max(pooled * (1.0 - pooled), 1e-9) * (1.0 / n_w + 1.0 / n_b))
    if denom <= 0:
        return 0.0
    return (p_w - p_b) / denom


# ── evaluation ───────────────────────────────────────────────────────────────

def compute_drift(
    db_path: Any = None,
    *,
    baseline_days: int = 14,
    window_hours: int = 6,
    min_baseline: int = 8,
    min_window: int = 4,
    min_drop: float = 0.25,
    absolute_floor: float = 0.5,
    z_threshold: float = 2.0,
    now: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Per-target regression report. Pure read — no state changes."""
    now_ts = float(now if now is not None else time.time())
    rows = outcomes_since(seconds=baseline_days * 86400.0, db_path=db_path)
    by_target: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        key = f"{row.get('target_agent')}:{row.get('target_model') or ''}"
        by_target.setdefault(key, []).append(row)

    report: List[Dict[str, Any]] = []
    window_seconds = max(1, int(window_hours)) * 3600.0
    for key, all_rows in by_target.items():
        agent, _, model = key.partition(":")
        # baseline excludes the rolling window — the regression must not
        # pollute the reference the signal is measured against
        window_rows = [
            r for r in all_rows
            if (now_ts - float(r.get("recorded_at") or 0)) <= window_seconds
        ]
        baseline_rows = [
            r for r in all_rows
            if (now_ts - float(r.get("recorded_at") or 0)) > window_seconds
        ]
        b_attempts, b_fails = _fail_stats(baseline_rows)
        w_attempts, w_fails = _fail_stats(window_rows)

        failing_window = [
            float(r.get("recorded_at") or 0)
            for r in window_rows
            if str(r.get("failure_kind") or "") in _FAIL_KINDS
        ]
        entry: Dict[str, Any] = {
            "target": {"agent": agent, "model": model},
            "key": key,
            "baseline_attempts": b_attempts,
            "baseline_failures": b_fails,
            "baseline_fail_rate": round(b_fails / b_attempts, 4) if b_attempts else None,
            "window_attempts": w_attempts,
            "window_failures": w_fails,
            "window_fail_rate": round(w_fails / w_attempts, 4) if w_attempts else None,
            "last_failure_at": max(failing_window) if failing_window else None,
            "drift": False,
            "reason": "",
        }

        if w_attempts < min_window:
            entry["reason"] = f"not enough recent attempts ({w_attempts}/{min_window})"
        elif b_attempts < min_baseline:
            entry["reason"] = f"not enough baseline history ({b_attempts}/{min_baseline})"
        else:
            p_w = w_fails / w_attempts
            p_b = b_fails / b_attempts
            z = _z_score(p_w, w_attempts, p_b, b_attempts)
            entry["z_score"] = round(z, 3)
            spike = (p_w >= absolute_floor and (p_w - p_b) >= min_drop)
            statistical = (z >= z_threshold and p_w > p_b)
            if spike or statistical:
                entry["drift"] = True
                entry["reason"] = (
                    f"quality regression signal — fail rate {p_w:.0%} recently "
                    f"vs {p_b:.0%} baseline (z={z:.1f})"
                )
            else:
                entry["reason"] = "within normal range"
        report.append(entry)

    report.sort(key=lambda e: (not e["drift"], -(e.get("window_fail_rate") or 0)))
    return report


def evaluate_drift(
    db_path: Any = None,
    *,
    demotion_ttl_minutes: int = 30,
    now: Optional[float] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Evaluate drift and update demotions. Returns {report, demotions, changes}."""
    now_ts = float(now if now is not None else time.time())
    report = compute_drift(db_path=db_path, now=now_ts, **kwargs)

    stored = _settings_raw().get(DEMOTION_SUBKEY)
    stored = stored if isinstance(stored, dict) else {}
    ttl = max(1, int(demotion_ttl_minutes)) * 60.0

    changes: List[Dict[str, Any]] = []

    # keep only non-expired entries; log expiry as recovery
    live: Dict[str, Any] = {}
    for key, info in stored.items():
        if not isinstance(info, dict):
            continue
        if float(info.get("until") or 0) > now_ts:
            live[key] = info
        else:
            changes.append({"key": key, "action": "recovered", "reason": "TTL expired"})
            log.log_quality_recovered(
                str(info.get("agent") or key.partition(":")[0]),
                str(info.get("model") or ""),
                source="ttl",
            )

    flagged_keys = set()
    for entry in report:
        if not entry["drift"]:
            continue
        key = entry["key"]
        existing = live.get(key)
        if existing:
            # Re-flagging requires NEW failure evidence since the demotion —
            # a routed-around target earns recovery by simply not failing again.
            prev_flagged = float(existing.get("flagged_at") or 0)
            last_fail = entry.get("last_failure_at")
            if last_fail is None or last_fail <= prev_flagged + 1.0:
                continue  # no fresh evidence → recover below
            existing["until"] = now_ts + ttl
            existing["reason"] = entry["reason"]
        else:
            live[key] = {
                "agent": entry["target"]["agent"],
                "model": entry["target"]["model"],
                "reason": entry["reason"],
                "flagged_at": now_ts,
                "until": now_ts + ttl,
                "window_fail_rate": entry.get("window_fail_rate"),
                "baseline_fail_rate": entry.get("baseline_fail_rate"),
            }
            changes.append({"key": key, "action": "demoted", "reason": entry["reason"]})
            log.log_quality_drift(
                entry["target"]["agent"],
                entry["target"]["model"],
                reason=entry["reason"],
                until=live[key]["until"],
            )
        flagged_keys.add(key)

    # previously demoted but no longer flagged → recovered
    for key, info in live.items():
        if key not in flagged_keys and not any(c["key"] == key for c in changes):
            changes.append({"key": key, "action": "recovered", "reason": "signal cleared"})
            log.log_quality_recovered(
                str(info.get("agent") or key.partition(":")[0]),
                str(info.get("model") or ""),
            )
    live = {k: v for k, v in live.items() if k in flagged_keys}

    _write_demotions(live)
    return {"report": report, "demotions": live, "changes": changes}


# ── queries / manual control ─────────────────────────────────────────────────

def active_demotions(*, now: Optional[float] = None) -> Dict[str, Dict[str, Any]]:
    """Non-expired demotions (read-only; expiry applied lazily)."""
    now_ts = float(now if now is not None else time.time())
    stored = _settings_raw().get(DEMOTION_SUBKEY)
    if not isinstance(stored, dict):
        return {}
    return {
        key: info
        for key, info in stored.items()
        if isinstance(info, dict) and float(info.get("until") or 0) > now_ts
    }


def clear_demotion(agent: str, model: str = "") -> bool:
    key = f"{(agent or '').strip().lower()}:{(model or '').strip()}"
    stored = _settings_raw().get(DEMOTION_SUBKEY)
    if not isinstance(stored, dict) or key not in stored:
        return False
    del stored[key]
    _write_demotions(stored)
    log.log_quality_recovered(agent, model, source="manual")
    return True


def apply_demotion_avoidance(
    decision: Any,
    cfg: Any,
) -> Tuple[Any, Dict[str, Any]]:
    """Route around actively demoted targets at decision time."""
    demoted = active_demotions()
    meta: Dict[str, Any] = {"demoted_skipped": []}
    if not demoted:
        return decision, meta

    chain: List[Any] = [decision.target, decision.escalation_target]
    chain.extend(decision.fallbacks if decision.fallbacks is not None else cfg.fallbacks.ordered)

    kept: List[Any] = []
    for cand in chain:
        if cand is None:
            continue
        if cand.key() in demoted:
            if cand.key() not in meta["demoted_skipped"]:
                meta["demoted_skipped"].append(cand.key())
            continue
        if cand.key() in {c.key() for c in kept}:
            continue
        kept.append(cand)

    if not kept:
        if cfg.default_target.key() in demoted:
            return decision, meta  # nothing healthy left — keep brain's choice
        kept = [cfg.default_target]

    if decision.target.key() in demoted and kept:
        log.log_quality_drift(
            decision.target.agent,
            decision.target.model,
            rerouted_to=kept[0].key(),
            decision_id=decision.decision_id,
        )
        meta["rerouted_from"] = decision.target.key()

    decision.target = kept[0]
    decision.escalation_target = kept[1] if len(kept) > 1 else cfg.escalation_target
    decision.fallbacks = kept[2:] if len(kept) > 2 else None
    return decision, meta

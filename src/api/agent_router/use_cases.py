"""Phase 0 — user-declared use-case routing table for CuttleRouter.

A use case maps a *kind of work* to the agent/model the user wants for it
("General chat", "Coding model", "Frontier coding model", …). The table is the
declared preference layer: when a turn matches a use case, its routing block
overrides the routing brain's target choice.

Source of truth: ``settings.json`` → ``agent_router.use_cases``. Agents (and
the Router page) edit the same JSON — load/save here always re-reads from disk
via ``SettingsManager.reload()`` so direct file edits are never clobbered.
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional, Tuple

from api.agent_router.registry import validate_execution_target
from api.agent_router.types import (
    VALID_DIFFICULTIES,
    VALID_TASK_TYPES,
    ExecutionTarget,
    RouterConfig,
    RoutingDecision,
)

SUBKEY = "use_cases"
SETTINGS_KEY = "agent_router"


# ── settings access (disk-truthful) ──────────────────────────────────────────

def _agent_router_raw() -> Dict[str, Any]:
    from managers.settings_manager import get_settings_manager

    sm = get_settings_manager()
    try:
        sm.reload()
    except Exception:
        pass
    raw = sm.get_setting(SETTINGS_KEY)
    return raw if isinstance(raw, dict) else {}


def _write_agent_router(raw: Dict[str, Any]) -> None:
    from managers.settings_manager import get_settings_manager

    get_settings_manager().set_setting(SETTINGS_KEY, raw)


# ── validation / normalization ───────────────────────────────────────────────

def _norm_target(raw: Any) -> Tuple[Optional[ExecutionTarget], Optional[str]]:
    if raw is None:
        return None, None
    if not isinstance(raw, dict):
        return None, f"target must be an object {{agent, model}}, got {raw!r}"
    t, err = validate_execution_target(
        str(raw.get("agent") or ""), str(raw.get("model") or ""), allow_empty_model=True
    )
    if err or not t:
        return None, err or "invalid target"
    return t, None


def _norm_target_list(raw: Any) -> Tuple[List[ExecutionTarget], Optional[str]]:
    if raw is None:
        return [], None
    if not isinstance(raw, list):
        return [], f"expected a list, got {raw!r}"
    out: List[ExecutionTarget] = []
    seen = set()
    for item in raw:
        t, err = _norm_target(item)
        if err:
            return [], err
        assert t is not None
        if t.key() in seen:
            continue
        seen.add(t.key())
        out.append(t)
    return out, None


def _norm_str_list(raw: Any, *, lower: bool = True) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [p.strip() for p in raw.split(",")]
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        s = str(item).strip()
        if not s:
            continue
        out.append(s.lower() if lower else s)
    return out


def normalize_use_case(raw: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Validate one use-case block. Returns (normalized_dict, error)."""
    if not isinstance(raw, dict):
        return None, "use case must be an object"
    name = str(raw.get("name") or "").strip()
    if not name:
        return None, "use case needs a `name`"
    uc_id = str(raw.get("id") or "").strip()
    if not uc_id:
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        uc_id = slug or f"uc-{int(time.time() * 1000) % 100000}"

    crit_raw = raw.get("criteria") if isinstance(raw.get("criteria"), dict) else {}
    task_types = [t for t in _norm_str_list(crit_raw.get("task_types")) if t in VALID_TASK_TYPES]
    difficulties = [d for d in _norm_str_list(crit_raw.get("difficulties")) if d in VALID_DIFFICULTIES]
    code_changes = crit_raw.get("code_changes")
    if code_changes not in (None, True, False):
        code_changes = None
    keywords = _norm_str_list(crit_raw.get("keywords"))

    route_raw = raw.get("routing") if isinstance(raw.get("routing"), dict) else {}
    never_raw = route_raw.get("never_use")
    never: List[Dict[str, str]] = []
    if isinstance(never_raw, str):
        never_raw = [p.strip() for p in never_raw.split(",")]
    if isinstance(never_raw, list):
        for item in never_raw:
            if isinstance(item, str):
                agent, _, model = item.partition(":")
                item = {"agent": agent, "model": model}
            if not isinstance(item, dict):
                continue
            agent = str(item.get("agent") or "").strip().lower()
            if not agent:
                continue
            never.append({"agent": agent, "model": str(item.get("model") or "").strip()})

    # Ordered target chain — first entry runs the task, the rest are tried in
    # order as earlier ones fail (several fallback layers, no fixed tiers).
    # Legacy preferred/escalation/fallbacks fields flatten into the chain.
    targets: List[ExecutionTarget] = []
    if "targets" in route_raw:
        targets, t_err = _norm_target_list(route_raw.get("targets"))
        if t_err:
            return None, f"targets: {t_err}"
    else:
        legacy: List[ExecutionTarget] = []
        for key in ("preferred", "escalation"):
            t, err = _norm_target(route_raw.get(key))
            if err:
                return None, f"{key} target: {err}"
            if t:
                legacy.append(t)
        fbs, fb_err = _norm_target_list(route_raw.get("fallbacks"))
        if fb_err:
            return None, f"fallbacks: {fb_err}"
        legacy.extend(fbs)
        seen_keys = set()
        for t in legacy:
            if t.key() in seen_keys:
                continue
            seen_keys.add(t.key())
            targets.append(t)

    try:
        priority = int(raw.get("priority") or 0)
    except (TypeError, ValueError):
        priority = 0

    return {
        "id": uc_id,
        "name": name,
        "description": str(raw.get("description") or "").strip(),
        "enabled": raw.get("enabled", True) is not False,
        "priority": priority,
        "criteria": {
            "task_types": task_types,
            "difficulties": difficulties,
            "code_changes": code_changes,
            "keywords": keywords,
        },
        "routing": {
            "targets": [t.to_dict() for t in targets],
            "never_use": never,
        },
    }, None


# ── defaults (seeded on fresh install) ───────────────────────────────────────

def default_use_cases() -> List[Dict[str, Any]]:
    """Three tested starter blocks, ordered simple → complex.

    Priorities encode complexity: smaller number = simpler work = matched
    first. Frontier must stay choosable for hard tasks, so its criteria
    (difficulty=high) do not overlap the ordinary coding block.
    """
    return [
        {
            "id": "general-chat",
            "name": "General chat / simple requests",
            "description": "Quick questions, summaries, small asks — no code changes.",
            "enabled": True,
            "priority": 10,
            "criteria": {
                "task_types": ["basic_ask"],
                "difficulties": ["low"],
                "code_changes": False,
                "keywords": [],
            },
            "routing": {
                "targets": [
                    {"agent": "cursor", "model": "auto"},
                    {"agent": "cursor", "model": "grok-4.6"},
                    {"agent": "codex", "model": ""},
                ],
                "never_use": [],
            },
        },
        {
            "id": "coding-model",
            "name": "Coding model",
            "description": "Ordinary low/medium coding and debugging work.",
            "enabled": True,
            "priority": 20,
            "criteria": {
                "task_types": ["coding", "debugging"],
                "difficulties": ["low", "medium"],
                "code_changes": None,
                "keywords": [],
            },
            "routing": {
                "targets": [
                    {"agent": "cursor", "model": "auto"},
                    {"agent": "cursor", "model": "grok-4.6"},
                    {"agent": "codex", "model": ""},
                ],
                "never_use": [],
            },
        },
        {
            "id": "frontier-coding",
            "name": "Frontier coding model",
            "description": "Hard coding / debugging / architecture work — spend the good model.",
            "enabled": True,
            "priority": 30,
            "criteria": {
                "task_types": ["coding", "debugging", "architecture"],
                "difficulties": ["high"],
                "code_changes": None,
                "keywords": [],
            },
            "routing": {
                "targets": [
                    {"agent": "cursor", "model": "grok-4.6"},
                    {"agent": "codex", "model": ""},
                ],
                "never_use": [],
            },
        },
    ]


# First seed run shipped complexity-inverted priorities (frontier=20,
# coding=30). Migrate untouched seed blocks to the complexity ordering.
_OLD_SEED_PRIORITIES = {"general-chat": 10, "frontier-coding": 20, "coding-model": 30}
_NEW_SEED_PRIORITIES = {"general-chat": 10, "coding-model": 20, "frontier-coding": 30}


def _migrate_seed_priorities(use_cases: List[Dict[str, Any]]) -> bool:
    """Update old seed priorities in place. True if anything changed."""
    changed = False
    for uc in use_cases:
        uc_id = uc.get("id")
        if (
            uc_id in _OLD_SEED_PRIORITIES
            and uc.get("priority") == _OLD_SEED_PRIORITIES[uc_id]
            and _NEW_SEED_PRIORITIES[uc_id] != uc["priority"]
        ):
            uc["priority"] = _NEW_SEED_PRIORITIES[uc_id]
            changed = True
    return changed


# ── load / save ──────────────────────────────────────────────────────────────

def load_use_cases(*, seed: bool = True) -> List[Dict[str, Any]]:
    raw = _agent_router_raw().get(SUBKEY)
    if raw is None and seed:
        seeded = default_use_cases()
        save_use_cases(seeded)
        return seeded
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    dirty = False
    for item in raw:
        uc, err = normalize_use_case(item)
        if err or not uc:
            print(f"[AGENT-ROUTER] event=use_case_skipped error={err!r}", flush=True)
            continue
        if _migrate_seed_priorities([uc]):
            dirty = True
        out.append(uc)
    if dirty:
        raw2 = _agent_router_raw()
        raw2[SUBKEY] = out
        _write_agent_router(raw2)
    return out


def save_use_cases(use_cases: List[Any]) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    normalized: List[Dict[str, Any]] = []
    seen_ids = set()
    for item in use_cases or []:
        uc, err = normalize_use_case(item)
        if err or not uc:
            return load_use_cases(seed=False), err
        base_id = uc["id"]
        n = 2
        while uc["id"] in seen_ids:
            uc["id"] = f"{base_id}-{n}"
            n += 1
        seen_ids.add(uc["id"])
        normalized.append(uc)
    raw = _agent_router_raw()
    raw[SUBKEY] = normalized
    _write_agent_router(raw)
    return normalized, None


# ── matching ─────────────────────────────────────────────────────────────────

def use_case_matches(
    uc: Dict[str, Any],
    *,
    task_type: str,
    difficulty: str,
    code_changes: Optional[bool],
    prompt: str,
) -> bool:
    crit = uc.get("criteria") or {}
    task_types = crit.get("task_types") or []
    if task_types and str(task_type or "").lower() not in task_types:
        return False
    difficulties = crit.get("difficulties") or []
    if difficulties and str(difficulty or "").lower() not in difficulties:
        return False
    cc = crit.get("code_changes")
    if cc is not None and bool(cc) != bool(code_changes):
        return False
    keywords = crit.get("keywords") or []
    if keywords:
        text = (prompt or "").lower()
        if not any(k in text for k in keywords):
            return False
    return True


def match_use_cases(
    use_cases: List[Dict[str, Any]],
    *,
    task_type: str,
    difficulty: str,
    code_changes: Optional[bool],
    prompt: str,
) -> List[Dict[str, Any]]:
    """All matching enabled use cases, declared-priority order (first wins)."""
    ranked = sorted(
        [(i, uc) for i, uc in enumerate(use_cases or []) if uc.get("enabled", True)],
        key=lambda pair: (int(pair[1].get("priority") or 0), pair[0]),
    )
    return [
        uc
        for _, uc in ranked
        if use_case_matches(
            uc,
            task_type=task_type,
            difficulty=difficulty,
            code_changes=code_changes,
            prompt=prompt,
        )
    ]


def _never_keys(uc: Dict[str, Any]) -> set:
    out = set()
    for entry in (uc.get("routing") or {}).get("never_use") or []:
        agent = str(entry.get("agent") or "").strip().lower()
        model = str(entry.get("model") or "").strip()
        if agent:
            out.add(f"{agent}:{model or '*'}")
    return out


def _is_never_used(never: set, target: ExecutionTarget) -> bool:
    if f"{target.agent}:{target.model}" in never:
        return True
    return f"{target.agent}:*" in never


def apply_table(
    decision: RoutingDecision,
    context: Any,
    cfg: RouterConfig,
) -> Tuple[RoutingDecision, Dict[str, Any]]:
    """Authority layer 3: the declared table overrides the brain's choice.

    The use case's ordered ``targets`` chain replaces the brain's target
    selection: chain[0] runs the task, chain[1] is the escalation hop
    (task failure), chain[2:] are the fallback layers (transport failure or
    continued failure). Returns (decision, meta). Never raises; on no match
    the decision is returned unchanged.
    """
    meta: Dict[str, Any] = {"use_case": None, "table_applied": False}
    try:
        table = load_use_cases()
    except Exception as exc:
        meta["table_error"] = str(exc)[:160]
        return decision, meta
    if not table:
        return decision, meta

    matches = match_use_cases(
        table,
        task_type=decision.task_type,
        difficulty=decision.difficulty,
        code_changes=getattr(context, "code_changes_requested", None),
        prompt=getattr(context, "user_request", "") or "",
    )
    if not matches:
        return decision, meta

    uc = matches[0]
    routing = uc.get("routing") or {}
    never = _never_keys(uc)

    chain_targets, _ = _norm_target_list(routing.get("targets"))

    # A declared chain fully replaces the brain's choice; the brain's targets
    # only fill in when the use case declares none (or all were never_use'd).
    source = chain_targets if chain_targets else [
        decision.target,
        decision.escalation_target,
        *(decision.fallbacks or []),
    ]
    chain: List[ExecutionTarget] = []
    for cand in source:
        if cand is None:
            continue
        if _is_never_used(never, cand):
            continue
        if cand.key() in {c.key() for c in chain}:
            continue
        chain.append(cand)

    if chain:
        decision.target = chain[0]
        decision.escalation_target = chain[1] if len(chain) > 1 else cfg.escalation_target
        decision.fallbacks = chain[2:] if len(chain) > 2 else None
        if decision.raw is None:
            decision.raw = {}
        decision.raw["use_case_id"] = uc.get("id")
        decision.reason = f"use case {uc.get('name')!r}: {decision.reason}"[:240]
        meta["use_case"] = uc.get("id")
        meta["table_applied"] = True
    return decision, meta

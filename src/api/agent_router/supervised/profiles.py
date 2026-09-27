"""Named supervised profiles and settings persistence."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from api.agent_router.supervised.types import (
    VALID_SUPERVISED_MODES,
    CoordinationBudget,
    CoordinatorProfile,
    EconomicSource,
    HarnessRef,
    SupervisedMode,
    WorkerProfile,
    normalize_reasoning_level,
)

SETTINGS_KEY = "supervised_coordinator"

# Soft-known Codex model ids (not inventing live discovery; overridable in settings).
CODEX_SOFT_MODELS = (
    "gpt-5.6-sol",
    "gpt-5.6-luna",
    "gpt-5.5",
    "gpt-5.3-codex",
    "gpt-5.1-codex-max",
    "o3",
    "o4-mini",
)


def diet_frontier_profile() -> Dict[str, Any]:
    """Initial diet-frontier: Codex GPT-5.6 Sol low → Cursor Auto."""
    return {
        "id": "diet-frontier",
        "label": "Diet frontier (Codex Sol low → Cursor Auto)",
        "coordinator": CoordinatorProfile(
            id="codex-sol-low",
            label="Codex GPT-5.6 Sol (low)",
            harness=HarnessRef(
                kind="agent",
                agent="codex",
                model="gpt-5.6-sol",
                reasoning="low",
                economic_source=EconomicSource.CODEX_CHATGPT_ALLOCATION.value,
            ),
            may_edit_repo=False,
            notes="Plans, delegates, reviews. Does not edit the repo in this profile.",
        ).to_dict(),
        "worker": WorkerProfile(
            id="cursor-auto",
            label="Cursor Agent Auto",
            harness=HarnessRef(
                kind="agent",
                agent="cursor",
                model="auto",
                reasoning="",
                economic_source=EconomicSource.CURSOR_AUTO_PROMO.value,
            ),
            notes="Implements the delegation packet.",
        ).to_dict(),
        "reviewer": "coordinator",
        "budget": CoordinationBudget(max_followups=1).to_dict(),
    }


def _default_settings() -> Dict[str, Any]:
    return {
        "mode": SupervisedMode.OFF.value,
        "active_profile": "diet-frontier",
        "profiles": {"diet-frontier": diet_frontier_profile()},
        # Policy hooks (inactive by default — do not change production routing).
        "prefer_supervised_for": [],  # e.g. ["architecture", "debugging:high"]
        "supervised_as_frontier_fallback": False,
        "require_paid_approval": True,
        "session_modes": {},  # session_id -> mode
    }


def load_supervised_settings() -> Dict[str, Any]:
    from managers.settings_manager import get_settings_manager

    sm = get_settings_manager()
    raw = sm.get_setting(SETTINGS_KEY) or {}
    if not isinstance(raw, dict):
        raw = {}
    base = _default_settings()
    out = dict(base)
    out.update({k: v for k, v in raw.items() if k != "profiles"})
    profiles = dict(base["profiles"])
    if isinstance(raw.get("profiles"), dict):
        profiles.update(raw["profiles"])
    if "diet-frontier" not in profiles:
        profiles["diet-frontier"] = diet_frontier_profile()
    out["profiles"] = profiles
    mode = str(out.get("mode") or SupervisedMode.OFF.value).strip().lower()
    if mode not in VALID_SUPERVISED_MODES:
        mode = SupervisedMode.OFF.value
    out["mode"] = mode
    if not isinstance(out.get("session_modes"), dict):
        out["session_modes"] = {}
    return out


def save_supervised_settings(data: Dict[str, Any]) -> Dict[str, Any]:
    from managers.settings_manager import get_settings_manager

    sm = get_settings_manager()
    sm.set_setting(SETTINGS_KEY, data)
    return load_supervised_settings()


def reset_supervised_settings() -> Dict[str, Any]:
    return save_supervised_settings(_default_settings())


def get_profile(profile_id: Optional[str] = None) -> Dict[str, Any]:
    settings = load_supervised_settings()
    pid = (profile_id or settings.get("active_profile") or "diet-frontier").strip()
    profiles = settings.get("profiles") or {}
    prof = profiles.get(pid) or profiles.get("diet-frontier") or diet_frontier_profile()
    return dict(prof)


def list_profile_ids() -> List[str]:
    return sorted((load_supervised_settings().get("profiles") or {}).keys())


def effective_mode(*, session_id: Any = None) -> str:
    settings = load_supervised_settings()
    if session_id is not None:
        smap = settings.get("session_modes") or {}
        sid = str(session_id)
        if sid in smap:
            m = str(smap[sid]).strip().lower()
            if m in VALID_SUPERVISED_MODES:
                return m
    return str(settings.get("mode") or SupervisedMode.OFF.value)


def set_mode(mode: str, *, session_id: Any = None) -> Tuple[Dict[str, Any], Optional[str]]:
    m = (mode or "").strip().lower()
    if m not in VALID_SUPERVISED_MODES:
        return load_supervised_settings(), (
            f"Invalid mode `{mode}`. Use: {', '.join(sorted(VALID_SUPERVISED_MODES))}."
        )
    settings = load_supervised_settings()
    if session_id is not None:
        smap = dict(settings.get("session_modes") or {})
        smap[str(session_id)] = m
        settings["session_modes"] = smap
    else:
        settings["mode"] = m
    return save_supervised_settings(settings), None


def set_active_profile(profile_id: str) -> Tuple[Dict[str, Any], Optional[str]]:
    settings = load_supervised_settings()
    pid = (profile_id or "").strip()
    if pid not in (settings.get("profiles") or {}):
        known = ", ".join(list_profile_ids()) or "(none)"
        return settings, f"Unknown profile `{pid}`. Known: {known}"
    settings["active_profile"] = pid
    return save_supervised_settings(settings), None


def set_review_loops(n: int) -> Tuple[Dict[str, Any], Optional[str]]:
    try:
        loops = max(0, min(5, int(n)))
    except (TypeError, ValueError):
        return load_supervised_settings(), "review-loops must be an integer 0–5."
    settings = load_supervised_settings()
    pid = settings.get("active_profile") or "diet-frontier"
    profiles = dict(settings.get("profiles") or {})
    prof = dict(profiles.get(pid) or diet_frontier_profile())
    budget = CoordinationBudget.from_dict(prof.get("budget"))
    budget.max_followups = loops
    prof["budget"] = budget.to_dict()
    profiles[pid] = prof
    settings["profiles"] = profiles
    return save_supervised_settings(settings), None


def set_worker(agent: str, model: str) -> Tuple[Dict[str, Any], Optional[str]]:
    from api.agent_router.registry import validate_execution_target

    validated, err = validate_execution_target(agent, model, allow_empty_model=True)
    if err or not validated:
        return load_supervised_settings(), err or "Invalid worker target."
    settings = load_supervised_settings()
    pid = settings.get("active_profile") or "diet-frontier"
    profiles = dict(settings.get("profiles") or {})
    prof = dict(profiles.get(pid) or diet_frontier_profile())
    worker = dict(prof.get("worker") or {})
    harness = dict(worker.get("harness") or {})
    harness["kind"] = "agent"
    harness["agent"] = validated.agent
    harness["model"] = validated.model
    if validated.agent == "cursor" and (validated.model or "").lower() in ("", "auto", "default"):
        harness["economic_source"] = EconomicSource.CURSOR_AUTO_PROMO.value
    worker["harness"] = harness
    worker["id"] = f"{validated.agent}-{(validated.model or 'default')}"
    worker["label"] = f"{validated.agent} / {validated.model or 'default'}"
    prof["worker"] = worker
    profiles[pid] = prof
    settings["profiles"] = profiles
    return save_supervised_settings(settings), None


def profile_objects(profile: Optional[Dict[str, Any]] = None) -> Tuple[
    CoordinatorProfile, WorkerProfile, CoordinationBudget
]:
    prof = profile or get_profile()
    coord = CoordinatorProfile.from_dict(prof.get("coordinator")) or CoordinatorProfile.from_dict(
        diet_frontier_profile()["coordinator"]
    )
    worker = WorkerProfile.from_dict(prof.get("worker")) or WorkerProfile.from_dict(
        diet_frontier_profile()["worker"]
    )
    assert coord is not None and worker is not None
    # Ensure reasoning normalized on coordinator
    h = coord.harness
    coord = CoordinatorProfile(
        id=coord.id,
        label=coord.label,
        harness=HarnessRef(
            kind=h.kind,
            agent=h.agent,
            model=h.model,
            reasoning=normalize_reasoning_level(h.reasoning, default="low"),
            economic_source=h.economic_source,
        ),
        may_edit_repo=coord.may_edit_repo,
        notes=coord.notes,
    )
    budget = CoordinationBudget.from_dict(prof.get("budget"))
    return coord, worker, budget


def soft_known_codex_models() -> List[str]:
    return list(CODEX_SOFT_MODELS)

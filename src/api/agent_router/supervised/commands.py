"""Slash commands: /coordinator (config) and /coordinate (tasks)."""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from api.agent_router.supervised.control import (
    coordinate_control_kind,
    followup_instruction,
    is_coordinate_control,
    is_supervised_control_message,
    strip_sticky_agent_prefix,
)
from api.agent_router.supervised.orchestrator import (
    cancel_task,
    start_supervised_task,
    status_for_session,
    user_followup,
)
from api.agent_router.supervised.profiles import (
    effective_mode,
    get_profile,
    list_profile_ids,
    load_supervised_settings,
    profile_objects,
    reset_supervised_settings,
    set_active_profile,
    set_mode,
    set_review_loops,
    set_worker,
    soft_known_codex_models,
)

_COORDINATOR_RE = re.compile(r"^/coordinator(?:\s+(.*))?$", re.I | re.DOTALL)
_COORDINATE_RE = re.compile(r"^/coordinate(?:\s+(.*))?$", re.I | re.DOTALL)


def parse_coordinator_command(message: str) -> Optional[str]:
    m = _COORDINATOR_RE.match(strip_sticky_agent_prefix(message or ""))
    if not m:
        return None
    return (m.group(1) or "").strip()


def parse_coordinate_command(message: str) -> Optional[str]:
    m = _COORDINATE_RE.match(strip_sticky_agent_prefix(message or ""))
    if not m:
        return None
    return (m.group(1) or "").strip()


def _ok(response: str, **extra: Any) -> Dict[str, Any]:
    body = {"success": True, "response": response, "type": "coordinator"}
    body.update(extra)
    return body


def _err(response: str) -> Dict[str, Any]:
    return {"success": True, "response": response, "type": "coordinator_error"}


def format_coordinator_status(*, session_id: Any = None) -> str:
    settings = load_supervised_settings()
    mode = effective_mode(session_id=session_id)
    prof = get_profile()
    coord, worker, budget = profile_objects(prof)
    lines = [
        "**Supervised coordinator — status**",
        "",
        f"- Mode: **{mode}** "
        f"(session override; global default `{settings.get('mode')}`)",
        f"- Active profile: `{settings.get('active_profile')}` (**global default**)",
        f"- Coordinator: `{coord.harness.agent}` / `{coord.harness.model}` "
        f"reasoning `{coord.harness.reasoning}` "
        f"({coord.harness.economic_source})",
        f"- Worker: `{worker.harness.agent}` / `{worker.harness.model or 'default'}` "
        f"({worker.harness.economic_source}) (**global profile**)",
        f"- Reviewer: `{prof.get('reviewer') or 'coordinator'}`",
        f"- Max follow-ups: {budget.max_followups} (**global profile budget**)",
        f"- Paid approval required: **{bool(settings.get('require_paid_approval', True))}**",
        f"- Supervised as frontier fallback (config only): "
        f"**{bool(settings.get('supervised_as_frontier_fallback'))}** — inactive unless enabled",
        f"- Prefer supervised for: {settings.get('prefer_supervised_for') or '(none)'}",
        "",
        "**Scope**",
        "- `/coordinator mode` — **session** (does not flip global mode)",
        "- `/coordinator profile|worker|review-loops|reset` — **global defaults**",
        "- Active tasks / control commands — **session-owned**",
        "- Starred agent chips — established “apply to new sessions” behavior unchanged",
        "",
        "**Routing relationship**",
        "- Strategy type: `supervised` (not a fake agent name)",
        "- Direct Auto → Grok → Codex fallback chain remains valid and unchanged",
        "- Explicit `/cursor`/`/codex` and starred stickies still bypass the router",
        "",
        f"Profiles: {', '.join(f'`{p}`' for p in list_profile_ids())}",
        f"Soft-known Codex models: {', '.join(f'`{m}`' for m in soft_known_codex_models()[:6])}…",
        "",
        status_for_session(session_id),
    ]
    return "\n".join(lines)


def handle_coordinator_command(args: str, *, session_id: Any = None) -> Dict[str, Any]:
    text = (args or "").strip()
    if not text or text.lower() in ("status", "show"):
        return _ok(
            format_coordinator_status(session_id=session_id),
            type="supervised_status",
            control_lane=True,
            model_calls=0,
        )

    parts = text.split()
    head = parts[0].lower()
    rest = parts[1:]

    if head == "mode":
        if not rest:
            return _err("Usage: `/coordinator mode off|supervised`")
        settings, err = set_mode(rest[0], session_id=session_id)
        if err:
            return _err(f"❌ {err}")
        return _ok(
            f"Coordinator mode set to **{effective_mode(session_id=session_id)}** "
            f"for this session (global `{settings.get('mode')}`).",
            control_lane=True,
        )

    if head == "profile":
        if not rest:
            return _err(
                "Usage: `/coordinator profile <id>`\n"
                f"Known: {', '.join(list_profile_ids())}\n"
                "_Note: profile selection is a **global default** (all sessions)._"
            )
        settings, err = set_active_profile(rest[0])
        if err:
            return _err(f"❌ {err}")
        return _ok(
            f"**Global default** active profile set to `{settings.get('active_profile')}`. "
            f"(Session mode remains `{effective_mode(session_id=session_id)}`.)",
            control_lane=True,
            scope="global_default",
        )

    if head == "worker":
        if len(rest) < 2:
            return _err(
                "Usage: `/coordinator worker <agent> <model>`\n"
                "_Note: worker selection updates the **global** active profile._"
            )
        settings, err = set_worker(rest[0], rest[1])
        if err:
            return _err(f"❌ {err}")
        return _ok(
            f"**Global default** worker updated on profile `{settings.get('active_profile')}`.",
            control_lane=True,
            scope="global_default",
        )

    if head in ("review-loops", "review_loops", "followups"):
        if not rest:
            return _err(
                "Usage: `/coordinator review-loops <n>`\n"
                "_Note: this updates the **global** active profile budget._"
            )
        settings, err = set_review_loops(rest[0])
        if err:
            return _err(f"❌ {err}")
        _, _, budget = profile_objects(get_profile())
        return _ok(
            f"**Global default** max follow-ups set to **{budget.max_followups}**.",
            control_lane=True,
            scope="global_default",
        )

    if head == "reset":
        reset_supervised_settings()
        return _ok(
            "**Global** supervised coordinator settings reset to defaults "
            "(mode **off**, session modes cleared).",
            control_lane=True,
            scope="global_default",
        )

    return _err(
        "Unknown `/coordinator` subcommand. Try:\n"
        "• `/coordinator status`\n"
        "• `/coordinator mode off|supervised`\n"
        "• `/coordinator profile diet-frontier`\n"
        "• `/coordinator worker cursor auto`\n"
        "• `/coordinator review-loops 1`\n"
        "• `/coordinator reset`"
    )


def handle_coordinate_command(
    args: str,
    *,
    session_id: Any = None,
    project_path: Optional[str] = None,
    status_queue=None,
    coordinator_runner=None,
    worker_runner=None,
    background: bool = True,
    control_request_id: Optional[str] = None,
) -> Dict[str, Any]:
    text = (args or "").strip()
    kind = coordinate_control_kind(text)

    if kind == "status":
        return _ok(
            status_for_session(session_id),
            type="supervised_status",
            control_lane=True,
            model_calls=0,
        )

    if kind == "cancel":
        body = cancel_task(session_id, control_request_id=control_request_id)
        body = dict(body)
        body["control_lane"] = True
        return body

    if kind == "followup":
        instr = followup_instruction(text)
        if not instr:
            return _err("Usage: `/coordinate followup <instruction>`")
        body = user_followup(
            session_id,
            instr,
            worker_runner=worker_runner,
            control_request_id=control_request_id,
        )
        body = dict(body)
        body["control_lane"] = True
        return body

    # Start a supervised task with the remainder as the user prompt.
    mode = effective_mode(session_id=session_id)
    result = start_supervised_task(
        text,
        parent_session_id=session_id,
        project_path=project_path or "",
        status_queue=status_queue,
        coordinator_runner=coordinator_runner,
        worker_runner=worker_runner,
        background=background,
    )
    result.setdefault("coordinator_mode", mode)
    return result


# Re-export for web_chat_api / frontend tests
__all__ = [
    "parse_coordinator_command",
    "parse_coordinate_command",
    "handle_coordinator_command",
    "handle_coordinate_command",
    "format_coordinator_status",
    "is_supervised_control_message",
    "is_coordinate_control",
]

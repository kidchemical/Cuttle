"""Parse child specs and build harness remainders (text after /cursor, /codex, …)."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any, Dict, List

from api.subagents.types import ChildSpec


_ROUTE_AGENTS = frozenset({"", "auto", "router", "route"})


def parse_child(raw: Any, *, index: int = 0) -> ChildSpec:
    if isinstance(raw, ChildSpec):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if text.startswith("{"):
            raw = json.loads(text)
        else:
            raw = {"message": text, "title": f"Subagent {index + 1}"}
    if not isinstance(raw, dict):
        raise ValueError("child spec must be an object or JSON string")
    title = str(raw.get("title") or raw.get("label") or "").strip()
    name = str(raw.get("name") or raw.get("display_name") or "").strip()
    if not title:
        title = name
    message = str(
        raw.get("message") or raw.get("prompt") or raw.get("text") or ""
    ).strip()
    label = title or name or f"child {index + 1}"
    if not message:
        raise ValueError(f"child {label!r} is missing message")
    agent_explicit = "agent" in raw or "harness" in raw
    model_explicit = "model" in raw
    effort_explicit = (
        "effort" in raw or "reasoning" in raw or "reasoning_effort" in raw
    )
    agent = str(raw.get("agent") or raw.get("harness") or "cursor").strip().lower()
    model = str(raw.get("model") or "").strip()
    effort = str(
        raw.get("effort") or raw.get("reasoning") or raw.get("reasoning_effort") or ""
    ).strip()
    avatar = str(raw.get("avatar") or raw.get("picture") or "").strip()
    display_name = str(raw.get("display_name") or "").strip() or name
    profile_id = ""
    ephemeral_profile = False
    profile_raw = raw.get("profile")
    if isinstance(profile_raw, str) and profile_raw.strip():
        profile_id = profile_raw.strip().lower()
    elif isinstance(profile_raw, dict):
        ephemeral_profile = True
        profile_id = str(profile_raw.get("id") or "").strip().lower()
        display_name = display_name or str(
            profile_raw.get("name") or profile_raw.get("display_name") or ""
        ).strip()
        avatar = avatar or str(profile_raw.get("avatar") or profile_raw.get("picture") or "").strip()
        inline_agent = str(
            profile_raw.get("agent") or profile_raw.get("harness") or ""
        ).strip().lower()
        inline_model = str(profile_raw.get("model") or "").strip()
        inline_effort = str(profile_raw.get("effort") or "").strip()
        if not agent_explicit and inline_agent:
            agent = inline_agent
            agent_explicit = True
        if not model_explicit and inline_model:
            model = inline_model
            model_explicit = True
        if not effort_explicit and inline_effort:
            effort = inline_effort
            effort_explicit = True
        if not title:
            title = display_name
    route = bool(raw.get("route"))
    if agent in _ROUTE_AGENTS:
        route = True
        agent = "cursor"
    return ChildSpec(
        title=title[:80],
        message=message,
        agent=agent or "cursor",
        model=model,
        effort=effort,
        route=route,
        profile_id=profile_id[:40],
        display_name=(display_name or title)[:80],
        avatar=avatar[:200],
        ephemeral_profile=ephemeral_profile,
        agent_explicit=agent_explicit,
        model_explicit=model_explicit,
        effort_explicit=effort_explicit,
    )


def parse_children(raw_list: Any) -> List[ChildSpec]:
    if raw_list is None:
        return []
    if isinstance(raw_list, dict):
        raw_list = [raw_list]
    if not isinstance(raw_list, (list, tuple)):
        raise ValueError("children must be a list")
    return [parse_child(item, index=i) for i, item in enumerate(raw_list)]


def harness_remainder(
    *,
    agent: str,
    model: str = "",
    effort: str = "",
    message: str = "",
) -> str:
    """Text after the slash token, matching how users type `/cursor grok-4.6 low …`."""
    bits: List[str] = []
    model_s = (model or "").strip()
    effort_s = (effort or "").strip()
    if model_s and model_s.lower() not in ("auto", "default"):
        bits.append(model_s)
    if effort_s:
        bits.append(effort_s)
    prefix = " ".join(bits)
    msg = (message or "").strip()
    if prefix and msg:
        return f"{prefix}\n\n{msg}"
    return prefix or msg


def sticky_user_text(spec: ChildSpec) -> str:
    remainder = harness_remainder(
        agent=spec.agent,
        model=spec.model,
        effort=spec.effort,
        message=spec.message,
    )
    slash = f"/{(spec.agent or 'cursor').strip().lstrip('/')}"
    return f"{slash} {remainder}".strip()


def apply_router(spec: ChildSpec, *, session_id: Any = None, project_path: str = "") -> ChildSpec:
    """Fill agent/model from the existing Cuttle router when the spec asked to route."""
    if not spec.route:
        return spec
    try:
        from api.agent_router.engine import decide_with_outcome
        from api.agent_router.types import RoutingContext
    except Exception:
        return spec
    ctx = RoutingContext(
        user_request=spec.message,
        project_path=project_path or "",
        session_id=session_id,
    )
    try:
        decision, _meta = decide_with_outcome(ctx)
    except Exception:
        return spec
    target = getattr(decision, "target", None)
    agent = str(getattr(target, "agent", "") or spec.agent or "cursor").strip().lower()
    model = str(getattr(target, "model", "") or spec.model or "").strip()
    return replace(
        spec,
        agent=agent or "cursor",
        model=model,
        route=True,
    )


def public_launcher(child: Dict[str, Any]) -> Dict[str, Any]:
    """Slim payload stored on the parent assistant bubble / live-status."""
    sid = int(child.get("session_id") or 0)
    handle = str(child.get("handle") or "")
    if not handle and sid:
        from api.subagents.types import chat_handle

        handle = chat_handle(sid)
    return {
        "id": child.get("id"),
        "session_id": sid,
        "handle": handle,
        "label": child.get("label") or child.get("title") or handle,
        "agent": child.get("agent") or "cursor",
        "model": child.get("model") or "",
        "effort": child.get("effort") or "",
        "status": child.get("status") or "",
        "generating": bool(child.get("generating")),
        "avatar": child.get("avatar") or "",
        "display_name": child.get("display_name") or child.get("label") or "",
        "profile_id": child.get("profile_id") or "",
    }

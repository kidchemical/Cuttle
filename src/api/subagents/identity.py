"""Badges, session pins, and model ids for sub-agent child chats.

Agent-to-agent turns do not follow the usual "user bubble = what I am invoking"
rule. The inbound bubble is from the **parent** harness; the assistant bubble
is from the **child** harness. Session pins follow the child so a human reply
in that chat resumes the same model (cache / resume).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from api.subagents.types import ChildRecord, ChildSpec


def resolve_child_model_id(agent: str, model: str = "", effort: str = "") -> str:
    """CLI / pin id for this child. Cursor effort is baked into the model id."""
    aid = str(agent or "cursor").strip().lower()
    mid = str(model or "").strip()
    eff = str(effort or "").strip().lower()
    if eff in ("none", "default", "reset", "clear"):
        eff = ""
    if aid != "cursor":
        return mid
    if not mid or mid.lower() in ("auto", "default"):
        return "auto"
    low = mid.lower()
    if low.startswith("cursor-") or low.endswith(("-low", "-high", "-medium", "-fast")):
        return mid
    if eff:
        return f"cursor-{mid}-{eff}"
    return mid


def _has_chips(slash: Any) -> bool:
    return (
        isinstance(slash, dict)
        and isinstance(slash.get("chips"), list)
        and bool(slash.get("chips"))
    )


def _meta_dict(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip().startswith("{"):
        try:
            import json

            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def parent_slash_command(db, parent_session_id: Optional[int]) -> Dict[str, Any]:
    """Chips for who sent the inbound bubble (the parent chat's harness)."""
    fallback = {
        "chips": [{
            "label": "Cursor - Auto",
            "meta": "/cursor · requested Auto",
            "category": "cursor",
        }]
    }
    if parent_session_id is None:
        return fallback
    try:
        messages = db.get_messages(int(parent_session_id), limit=40) or []
    except Exception:
        return fallback
    for role in ("user", "assistant"):
        for msg in reversed(messages):
            if str(msg.get("role") or "") != role:
                continue
            meta = _meta_dict(msg.get("metadata"))
            sc = meta.get("slash_command")
            if _has_chips(sc):
                return {"chips": list(sc["chips"])}
    return fallback


def child_slash_command(
    spec: Optional[ChildSpec] = None,
    child: Optional[ChildRecord] = None,
    *,
    session_id: Any = None,
) -> Dict[str, Any]:
    """Chips for who sent the child assistant bubble (child harness + model + effort)."""
    agent = ""
    model = ""
    effort = ""
    if spec is not None:
        agent = spec.agent
        model = spec.model
        effort = spec.effort
    if child is not None:
        agent = agent or child.agent
        model = model or child.model
        effort = effort or child.effort
    agent = str(agent or "cursor").strip().lower()
    model = resolve_child_model_id(agent, model, effort)
    identity = {"agent": agent, "model": model, "effort": str(effort or "").strip()}
    text = f"/{agent} ping"
    try:
        from api.web_chat_api import _user_badge_metadata

        badge = _user_badge_metadata(text, session_id, identity=identity)
        sc = (badge or {}).get("slash_command")
        if _has_chips(sc):
            return {"chips": list(sc["chips"])}
    except Exception:
        pass
    label_agent = {
        "muse": "Muse Code",
        "codex": "Codex",
        "hermes": "Hermes",
        "opencode": "OpenCode",
        "cursor": "Cursor",
    }.get(agent, agent.title() if agent else "Cursor")
    pretty = model or "Auto"
    if agent == "cursor" and pretty.lower() in ("auto", "default", ""):
        pretty = "Auto"
    elif agent == "cursor" and pretty.lower().startswith("cursor-"):
        pretty = pretty[7:].replace("-", " ").title()
    label = f"{label_agent} - {pretty}" if pretty else label_agent
    meta = f"/{agent}" + (f" · model {model}" if model else "")
    if effort and agent != "cursor":
        label += f" · {effort}"
        meta += f" · effort {effort}"
    return {
        "chips": [{
            "label": label,
            "meta": meta,
            "category": agent or "cursor",
        }]
    }


def pin_child_session(session_id: Any, spec: ChildSpec, *, project_path: str = "") -> None:
    """Pin this child chat to the child's harness so a human follow-up resumes it."""
    if session_id is None:
        return
    agent = str(spec.agent or "cursor").strip().lower()
    model = resolve_child_model_id(agent, spec.model, spec.effort)
    effort = str(spec.effort or "").strip().lower()
    if effort in ("none", "default", "reset", "clear"):
        effort = ""
    try:
        if agent == "cursor":
            from api.agent_harness.cwd import resolve_harness_cwd
            from scripts.utilities.cursor_cli_session_store import update_cursor_agent_options

            cwd = resolve_harness_cwd(project_path or "")
            update_cursor_agent_options(cwd, str(session_id), model=model)
            return
        if agent == "muse":
            from scripts.utilities.muse_cli_session_store import (
                save_muse_effort,
                save_muse_model,
            )
            if model:
                save_muse_model(session_id, model)
            if effort:
                save_muse_effort(session_id, effort)
            return
        if agent == "codex":
            from scripts.utilities.codex_cli_session_store import (
                save_codex_effort,
                save_codex_model,
            )
            if model:
                save_codex_model(session_id, model)
            if effort:
                save_codex_effort(session_id, effort)
            return
        if agent == "hermes":
            from scripts.utilities.hermes_cli_session_store import (
                save_hermes_effort,
                save_hermes_model,
            )
            if model:
                save_hermes_model(session_id, model)
            if effort:
                save_hermes_effort(session_id, effort)
            return
        if agent == "opencode":
            from api.agent_harness.agents.opencode.session_store import (
                save_opencode_effort,
                save_opencode_model,
            )
            if model:
                save_opencode_model(session_id, model)
            if effort:
                save_opencode_effort(session_id, effort)
    except Exception:
        return


def annotate_runner_result(
    result: Dict[str, Any],
    spec: ChildSpec,
    *,
    session_id: Any = None,
) -> Dict[str, Any]:
    """Ensure the harness result can build an assistant badge (model + effort)."""
    out = dict(result or {})
    agent = str(spec.agent or "cursor").strip().lower()
    model = resolve_child_model_id(agent, spec.model, spec.effort)
    effort = str(spec.effort or "").strip()
    if not out.get("type"):
        out["type"] = f"{agent}_command"
    if not out.get("agent_id"):
        out["agent_id"] = agent
    if model and not out.get("agent_model"):
        out["agent_model"] = model
    if effort and not out.get("agent_effort"):
        out["agent_effort"] = effort
    if session_id is not None and out.get("session_id") is None:
        out["session_id"] = session_id
    if agent == "cursor" and model:
        cr = out.get("cursor_run") if isinstance(out.get("cursor_run"), dict) else {}
        requested = str(cr.get("requested_model") or "").strip()
        if not requested or requested.lower() in ("auto", "default"):
            cr = dict(cr)
            cr["requested_model"] = model
            if not cr.get("reported_model"):
                cr["reported_model"] = model
            out["cursor_run"] = cr
    return out


def hydrate_subagent_message_badges(
    db,
    session_id: int,
    messages: List[Dict[str, Any]],
    *,
    pin_session: bool = False,
) -> None:
    """Repair agent-to-agent badges on already-saved child transcripts.

    Spawn-time bugs stored the *child* harness on the inbound bubble (because
    the body used to be ``/muse …`` / ``/codex …``) and either omitted the
    assistant chip or stamped Cursor Auto from ``cursor_run``. Always rewrite
    those two bubbles from the parent/child records — filling only *missing*
    chips would leave CH-000554…556 looking wrong.
    """
    if not messages:
        return
    try:
        row = db.get_chat_session_by_id(int(session_id)) or {}
    except Exception:
        return
    if str(row.get("origin") or "") != "subagent":
        return
    parent_sid = row.get("parent_session_id")
    parent_sc = parent_slash_command(db, parent_sid)
    child = None
    try:
        from api.subagents import store

        child = store.get_child_by_session(db, int(session_id))
    except Exception:
        child = None
    child_sc = child_slash_command(child=child, session_id=session_id) if child else None
    if pin_session and child is not None:
        try:
            pin_child_session(
                session_id,
                ChildSpec(
                    title=child.label or "child",
                    message=child.prompt or ".",
                    agent=child.agent,
                    model=child.model,
                    effort=child.effort,
                ),
                project_path=str(row.get("project_path") or ""),
            )
        except Exception:
            pass
    for msg in messages:
        meta = _meta_dict(msg.get("metadata"))
        role = str(msg.get("role") or "")
        if role == "user" and str(meta.get("speaker_kind") or "") == "parent":
            meta["slash_command"] = parent_sc
            msg["metadata"] = meta
        elif role == "assistant" and str(meta.get("origin") or "") == "subagent":
            if child_sc:
                meta["slash_command"] = child_sc
            if child and str(child.agent or "").lower() == "cursor":
                mid = resolve_child_model_id(child.agent, child.model, child.effort)
                if mid and mid.lower() not in ("auto", "default"):
                    cr = meta.get("cursor_run") if isinstance(meta.get("cursor_run"), dict) else {}
                    req = str(cr.get("requested_model") or "").strip()
                    if not req or req.lower() in ("auto", "default"):
                        cr = dict(cr)
                        cr["requested_model"] = mid
                        meta["cursor_run"] = cr
            msg["metadata"] = meta

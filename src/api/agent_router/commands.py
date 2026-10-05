"""Slash commands: /router, /route, /retry frontier|fallback."""

from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

from api.agent_router.config import (
    add_fallback,
    load_router_config,
    remove_fallback,
    reset_router_config,
    update_router_config,
)
from api.agent_router.engine import session_has_agent_selection, should_invoke_router
from api.agent_router.registry import list_agent_ids, normalize_agent_id, validate_execution_target
from api.agent_router.types import RouterMode

_ROUTER_RE = re.compile(r"^/router(?:\s+(.*))?$", re.I | re.DOTALL)
_ROUTE_RE = re.compile(r"^/route(?:\s+(.*))?$", re.I | re.DOTALL)
_RETRY_RE = re.compile(r"^/retry(?:\s+(.*))?$", re.I | re.DOTALL)


def parse_router_command(message: str) -> Optional[str]:
    m = _ROUTER_RE.match((message or "").strip())
    if not m:
        return None
    return (m.group(1) or "").strip()


def parse_route_command(message: str) -> Optional[str]:
    m = _ROUTE_RE.match((message or "").strip())
    if not m:
        return None
    return (m.group(1) or "").strip()


def parse_retry_command(message: str) -> Optional[str]:
    m = _RETRY_RE.match((message or "").strip())
    if not m:
        return None
    return (m.group(1) or "").strip()


def _fmt_target(agent: str, model: str) -> str:
    return f"`{agent}` / `{model or '(default)'}`"


def format_status(
    *,
    session_id: Any = None,
    message_hint: str = "",
) -> str:
    cfg = load_router_config()
    enabled = cfg.enabled()
    prov = cfg.provider
    lines = [
        "**Agent router — status**",
        "",
        f"- Routing enabled: **{'yes' if enabled else 'no'}** (mode `{prov.mode}`)",
    ]

    if prov.mode == RouterMode.API.value:
        brain = "Jev" if (prov.api_provider or "").lower() in ("jev", "typesafe") else "OpenAI API"
        lines.append(f"- Router provider: {brain} — model `{prov.api_model}`")
    elif prov.mode == RouterMode.LOCAL.value:
        lines.append(
            f"- Router provider: local — model `{prov.local_model or '(unset)'}` "
            f"endpoint `{prov.local_endpoint or '(unset)'}`"
        )
    elif prov.mode == RouterMode.AGENT.value:
        lines.append(
            f"- Router provider: agent CLI — `{prov.agent_id}` / `{prov.agent_model}`"
        )
    else:
        lines.append("- Router provider: _(disabled)_")

    lines.append(f"- Default execution target: {_fmt_target(cfg.default_target.agent, cfg.default_target.model)}")
    lines.append(
        f"- Escalation target: {_fmt_target(cfg.escalation_target.agent, cfg.escalation_target.model)}"
    )
    if cfg.fallbacks.ordered:
        lines.append("- Fallback chain:")
        for i, t in enumerate(cfg.fallbacks.ordered, 1):
            lines.append(f"  {i}. {_fmt_target(t.agent, t.model)}")
    else:
        lines.append("- Fallback chain: _(empty)_")

    from api.agent_router.classify import classifier_settings

    cls = classifier_settings()
    lines.append(
        f"- Classifier fast path: **{'on' if cls['fast_path'] else 'off'}** "
        f"(skips the routing brain at confidence ≥ {cls['fast_path_confidence']:.2f})"
    )
    import api.agent_router.quota as quota

    cooling = quota.snapshot()
    if cooling:
        lines.append("- Out-of-usage cooldowns (skipped when routing):")
        for c in cooling:
            tier = "all models" if c["scope"] == "all" else "premium models (Auto still used)"
            lines.append(f"  - `{c['agent']}` {tier} — ~{c['minutes_left']} min left")

    will, reason = should_invoke_router(message_hint or "hello world", session_id=session_id, config=cfg)
    bypass, agent, detail = session_has_agent_selection(message_hint or "", session_id)
    lines.append("")
    if not enabled:
        lines.append("- This session will invoke the router: **no** (mode off — existing Cuttle defaults apply)")
    elif bypass:
        lines.append(
            f"- This session will invoke the router: **no** — bypassed by selected agent "
            f"`{agent}` ({detail}). Starred and manually entered sticky commands are treated the same."
        )
    else:
        lines.append(
            f"- This session will invoke the router: **{'yes' if will else 'no'}** ({reason})"
        )

    lines.append("")
    lines.append(
        "Commands: `/router mode …`, `/router api model …`, `/router default …`, "
        "`/router fallback …`, `/router metrics summary`, `/router feedback good|bad`, "
        "`/router evaluate …`, `/router reset`, "
        "`/route <agent> <model> <prompt>`, `/retry frontier|fallback`"
    )
    return "\n".join(lines)


def handle_router_command(args: str, *, session_id: Any = None) -> Dict[str, Any]:
    text = (args or "").strip()
    if not text or text.lower() in ("status", "show", "?"):
        return {
            "success": True,
            "response": format_status(session_id=session_id),
            "type": "router_status",
        }

    parts = text.split()
    head = parts[0].lower()

    if head == "reset":
        cfg = reset_router_config()
        return {
            "success": True,
            "response": (
                "**Agent router:** Reset to defaults.\n\n" + format_status(session_id=session_id)
            ),
            "type": "router_config",
            "config": cfg.to_dict(),
        }

    if head == "mode":
        if len(parts) < 2:
            return _err("Usage: `/router mode off|api|local|agent`")
        cfg, err = update_router_config(mode=parts[1])
        if err:
            return _err(err)
        return _ok(f"Mode set to `{cfg.provider.mode}`.", session_id)

    if head == "api":
        if len(parts) >= 3 and parts[1].lower() == "model":
            cfg, err = update_router_config(api_model=parts[2])
            if err:
                return _err(err)
            return _ok(f"API router model set to `{cfg.provider.api_model}`.", session_id)
        return _err("Usage: `/router api model <model>`")

    if head == "local":
        if len(parts) >= 3 and parts[1].lower() == "model":
            cfg, err = update_router_config(local_model=parts[2])
            if err:
                return _err(err)
            return _ok(f"Local router model set to `{cfg.provider.local_model}`.", session_id)
        if len(parts) >= 3 and parts[1].lower() == "endpoint":
            cfg, err = update_router_config(local_endpoint=" ".join(parts[2:]))
            if err:
                return _err(err)
            return _ok(
                f"Local router endpoint set to `{cfg.provider.local_endpoint}`.", session_id
            )
        return _err("Usage: `/router local model <model>` or `/router local endpoint <url>`")

    if head == "agent":
        if len(parts) >= 3 and parts[1].lower() == "model":
            cfg, err = update_router_config(agent_model=parts[2])
            if err:
                return _err(err)
            return _ok(f"Router agent model set to `{cfg.provider.agent_model}`.", session_id)
        if len(parts) >= 2:
            cfg, err = update_router_config(agent_id=parts[1])
            if err:
                return _err(err)
            return _ok(f"Router agent set to `{cfg.provider.agent_id}`.", session_id)
        return _err("Usage: `/router agent <agent>` or `/router agent model <model>`")

    if head == "default":
        if len(parts) < 3:
            return _err("Usage: `/router default <agent> <model>`")
        cfg, err = update_router_config(default_target=(parts[1], parts[2]))
        if err:
            return _err(err)
        return _ok(
            f"Default target set to {_fmt_target(cfg.default_target.agent, cfg.default_target.model)}.",
            session_id,
        )

    if head == "fallback":
        if len(parts) < 2:
            return _err("Usage: `/router fallback list|add|remove …`")
        sub = parts[1].lower()
        if sub == "list":
            cfg = load_router_config()
            if not cfg.fallbacks.ordered:
                body = "**Fallbacks:** _(empty)_"
            else:
                rows = [
                    f"{i}. {_fmt_target(t.agent, t.model)}"
                    for i, t in enumerate(cfg.fallbacks.ordered, 1)
                ]
                body = "**Fallbacks:**\n" + "\n".join(rows)
            return {"success": True, "response": body, "type": "router_config"}
        if sub == "add":
            if len(parts) < 4:
                return _err("Usage: `/router fallback add <agent> <model>`")
            cfg, err = add_fallback(parts[2], parts[3])
            if err:
                return _err(err)
            return _ok("Fallback added.", session_id)
        if sub == "remove":
            if len(parts) < 4:
                return _err("Usage: `/router fallback remove <agent> <model>`")
            cfg, err = remove_fallback(parts[2], parts[3])
            if err:
                return _err(err)
            return _ok("Fallback removed.", session_id)
        return _err("Usage: `/router fallback list|add|remove …`")

    if head == "metrics":
        if len(parts) > 1 and parts[1].lower() not in ("summary", "show"):
            return _err("Usage: `/router metrics summary [days]`")
        days = 7
        if len(parts) > 2:
            try:
                days = max(1, min(int(parts[2]), 365))
            except ValueError:
                return _err("Days must be a number from 1 to 365.")
        from api.agent_router.outcomes import metrics_summary

        rows = metrics_summary(days=days)
        if not rows:
            body = f"**CuttleRouter metrics — last {days} days**\n\nNo routed attempts recorded yet."
        else:
            lines = [f"**CuttleRouter metrics — last {days} days**", ""]
            for row in rows:
                attempts = int(row.get("attempts") or 0)
                successes = int(row.get("successes") or 0)
                rate = (successes / attempts * 100.0) if attempts else 0.0
                total_tokens = int(row.get("total_tokens") or 0)
                total_cost = float(row.get("total_cost") or 0)
                avg_tokens = int(round(total_tokens / attempts)) if attempts and total_tokens else 0
                usage_bits = []
                if total_tokens:
                    usage_bits.append(f"avg {avg_tokens:,} tok/turn ({total_tokens:,} total)")
                if total_cost:
                    usage_bits.append(f"${total_cost:.4f}")
                usage_part = f"; {' · '.join(usage_bits)}" if usage_bits else ""
                lines.append(
                    f"- {_fmt_target(row['target_agent'], row['target_model'])}: "
                    f"{successes}/{attempts} worked ({rate:.0f}%), "
                    f"avg {float(row.get('avg_latency_ms') or 0):.0f} ms"
                    f"{usage_part}; "
                    f"feedback +{int(row.get('good_feedback') or 0)}"
                    f"/-{int(row.get('bad_feedback') or 0)}"
                )
            body = "\n".join(lines)
        return {
            "success": True,
            "response": body,
            "type": "router_metrics",
            "days": days,
            "metrics": rows,
        }

    if head == "feedback":
        if len(parts) < 2 or parts[1].lower() not in ("good", "bad"):
            return _err("Usage: `/router feedback good|bad [decision_id]`")
        from api.agent_router.outcomes import set_feedback

        decision_id = parts[2] if len(parts) > 2 else None
        chosen = set_feedback(
            parts[1],
            session_id=session_id,
            decision_id=decision_id,
        )
        if not chosen:
            return _err("No routed result was found to rate.")
        return {
            "success": True,
            "response": (
                f"**CuttleRouter:** Saved **{parts[1].lower()}** feedback "
                f"for decision `{chosen}`."
            ),
            "type": "router_feedback",
            "decision_id": chosen,
            "feedback": parts[1].lower(),
        }

    if head == "evaluate" or head == "eval":
        return _handle_evaluate(" ".join(parts[1:]), session_id=session_id)

    return _err(
        "Unknown `/router` subcommand. Try `/router status`.\n"
        f"Agents: {', '.join(list_agent_ids())}"
    )


def _handle_evaluate(args: str, *, session_id: Any = None) -> Dict[str, Any]:
    """Decision-only evaluation — never invokes Cursor/Codex/Hermes executors."""
    text = (args or "").strip()
    if not text:
        return _err(
            "Usage:\n"
            "• `/router evaluate <prompt>` — single decision\n"
            "• `/router evaluate batch [suite] [--repeat N] [--concurrency N] [--yes]` — suite"
        )

    from api.agent_router.config import load_router_config
    from api.agent_router.eval import (
        evaluate_prompt,
        format_batch_preview,
        format_batch_summary,
        format_single_eval,
        list_suite_names,
        load_suite,
        parse_batch_args,
        run_suite,
        save_eval_report,
    )

    low = text.lower()
    if low == "batch" or low.startswith("batch ") or low.startswith("batch\t"):
        rest = text[5:].strip()
        suite_name, concurrency, repeats, confirmed = parse_batch_args(rest)
        try:
            suite = load_suite(suite_name)
        except Exception as e:
            known = ", ".join(list_suite_names()) or "(none)"
            return _err(f"{e}\nAvailable suites: {known}")

        cfg = load_router_config()
        if not confirmed:
            return {
                "success": True,
                "response": format_batch_preview(
                    suite.name,
                    len(suite.cases),
                    cfg.provider.api_model or "(unset)",
                    concurrency,
                    repeats,
                ),
                "type": "router_eval_preview",
                "suite": suite.name,
                "case_count": len(suite.cases),
                "repeats": repeats,
            }

        report = run_suite(
            suite, concurrency=concurrency, repeats=repeats, config=cfg
        )
        json_path, _md_path, url = save_eval_report(report)
        summary = format_batch_summary(report, artifact_url=url)
        return {
            "success": True,
            "response": summary,
            "type": "router_eval",
            "report": report,
            "report_url": url,
            "report_path": str(json_path),
        }

    # Single prompt
    result = evaluate_prompt(text)
    return {
        "success": True,
        "response": format_single_eval(result),
        "type": "router_eval",
        "eval": result,
    }


def handle_route_command(args: str) -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[Any]]:
    """
    Returns (error_reply, prompt, ExecutionTarget) — if error_reply set, return it to chat.
    """
    text = (args or "").strip()
    if not text:
        return (
            _err("Usage: `/route <agent> <model> <prompt>`"),
            None,
            None,
        )
    parts = text.split(None, 2)
    if len(parts) < 3:
        return _err("Usage: `/route <agent> <model> <prompt>`"), None, None
    agent, model, prompt = parts[0], parts[1], parts[2]
    target, err = validate_execution_target(agent, model, allow_empty_model=True)
    if err or not target:
        return _err(err or "Invalid target"), None, None
    if not prompt.strip():
        return _err("Prompt required after agent/model."), None, None
    return None, prompt.strip(), target


def handle_retry_command(args: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """Returns (error_reply, mode) where mode is frontier|fallback."""
    text = (args or "").strip().lower()
    if text in ("frontier", "escalate", "escalation"):
        return None, "frontier"
    if text in ("fallback", "fallbacks"):
        return None, "fallback"
    return (
        _err("Usage: `/retry frontier` or `/retry fallback`"),
        "",
    )


def _ok(msg: str, session_id: Any = None) -> Dict[str, Any]:
    return {
        "success": True,
        "response": f"**Agent router:** {msg}\n\n" + format_status(session_id=session_id),
        "type": "router_config",
    }


def _err(msg: str) -> Dict[str, Any]:
    return {
        "success": True,
        "response": f"❌ **Agent router:** {msg}",
        "type": "router_error",
    }

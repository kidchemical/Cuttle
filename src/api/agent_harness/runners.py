"""Harness runner entry points (Phase 5 P5-B owner).

The ``_run_*_web_command`` helpers used to live in the Flask entry
module, forcing ``agent_router.dispatch`` / ``supervised/adapters`` to
import the monolith for runner fns. They are pure forwarders to
``kernel.run_agent_web_command`` (+ pinned-outcome recording), so they
belong here next to the kernel. The entry module keeps same-name
aliases; router code imports this owner directly.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional


def run_harness_web_command(
    agent_id: str,
    prompt: str,
    chat_session_id,
    status_queue=None,
    project_path=None,
    model_override=None,
    execute_kwargs=None,
) -> dict:
    """Shared entry for harness agents (Cursor, Codex, Muse, Claude, …)."""
    from api.agent_harness.kernel import run_agent_web_command

    return run_agent_web_command(
        agent_id,
        prompt,
        chat_session_id,
        status_queue=status_queue,
        project_path=project_path,
        model_override=model_override,
        execute_kwargs=execute_kwargs,
    )


def run_pinned_harness_turn(agent_id: str, prompt: str, chat_session_id, **kwargs) -> dict:
    """Pinned/starred slash-agent turn, recorded for My Cuttle Performance.

    Router turns record their own attempts in ``agent_router.dispatch``; only
    call this from paths that bypass the router.
    """
    started = time.perf_counter()
    body = run_harness_web_command(agent_id, prompt, chat_session_id, **kwargs)
    try:
        from api.agent_router.pinned_outcomes import record_pinned_turn

        record_pinned_turn(
            agent_id,
            body,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            session_id=chat_session_id,
            project_path=kwargs.get("project_path"),
        )
    except Exception as exc:
        print(f"[CHAT] pinned outcome record failed: {str(exc)[:200]}", flush=True)
    return body


def run_cursor_web_command(
    prompt: str,
    chat_session_id,
    status_queue=None,
    project_path=None,
    model_override=None,
) -> dict:
    """Deprecated shim — Cursor lives under ``agent_harness/agents/cursor/``."""
    return run_harness_web_command(
        "cursor",
        prompt,
        chat_session_id,
        status_queue=status_queue,
        project_path=project_path,
        model_override=model_override,
    )


def run_codex_web_command(
    prompt: str,
    chat_session_id,
    status_queue=None,
    project_path=None,
    model_override=None,
    reasoning_effort=None,
) -> dict:
    """Deprecated shim — Codex lives under ``agent_harness/agents/codex/``."""
    extra = {}
    if reasoning_effort:
        extra["reasoning_effort"] = reasoning_effort
    return run_harness_web_command(
        "codex",
        prompt,
        chat_session_id,
        status_queue=status_queue,
        project_path=project_path,
        model_override=model_override,
        execute_kwargs=extra or None,
    )


def run_muse_web_command(
    prompt: str,
    chat_session_id,
    status_queue=None,
    project_path=None,
    model_override=None,
) -> dict:
    """Deprecated shim — Muse lives under ``agent_harness/agents/muse/``."""
    return run_harness_web_command(
        "muse",
        prompt,
        chat_session_id,
        status_queue=status_queue,
        project_path=project_path,
        model_override=model_override,
    )


def run_hermes_web_command(
    prompt: str,
    chat_session_id,
    status_queue=None,
    project_path=None,
    model_override=None,
) -> dict:
    """Deprecated shim — Hermes lives under ``agent_harness/agents/hermes/``."""
    return run_harness_web_command(
        "hermes",
        prompt,
        chat_session_id,
        status_queue=status_queue,
        project_path=project_path,
        model_override=model_override,
    )


def run_claude_web_command(
    prompt: str,
    chat_session_id,
    status_queue=None,
    project_path=None,
    model_override=None,
) -> dict:
    """Deprecated shim — Claude Code lives under ``agent_harness/agents/claude/``."""
    return run_harness_web_command(
        "claude",
        prompt,
        chat_session_id,
        status_queue=status_queue,
        project_path=project_path,
        model_override=model_override,
    )

"""Live-status fields owned outside the live-status store.

``/api/chat-live-status`` and its batch route merge ``live_status_extras`` into
each chat's payload:

- ``subagents``: the parent's sub-agent launchers (``api.subagents.service``)
- ``background``: work the chat still owns while no turn runs — detached
  waits, watch jobs, supervision schedules and processes its agents left
  running (``api.jobs.work_tree.background_summary``), or ``None``

- ``orchestration``: ``''``, or ``'active'`` / ``'paused'`` / ``'stopped'`` for a
  chat bound to an orchestration (``api.orchestrator.control.state``); with
  ``background.watchers`` it lets the chat say what will wake it next

- ``thinking``: the running turn's reasoning so far, the tail of what its
  reply will carry in ``<think>`` (``api.agent_harness.reasoning_reply
  .live_thinking``); ``''`` while idle. The live bubble shows it collapsed
  (``chat_live_thinking.js``)

Each owner failing degrades to its empty value; live-status never fails on it.
"""

from __future__ import annotations

from typing import Any, Dict


def live_status_extras(session_id: Any) -> Dict[str, Any]:
    try:
        from api.subagents.service import public_parent_subagents

        subagents = public_parent_subagents(session_id) or []
    except Exception:
        subagents = []
    try:
        from api.jobs.work_tree import background_summary

        background = background_summary(session_id)
    except Exception:
        background = None
    try:
        from api.orchestrator.control import state

        orchestration = state(session_id)
    except Exception:
        orchestration = ""
    thinking = ""
    try:
        from api.chat_live_status import get_live_status

        live = get_live_status(session_id) or {}
        if live.get("active") and live.get("query_id"):
            from api.agent_harness.reasoning_reply import live_thinking

            thinking = live_thinking(live["query_id"])
    except Exception:
        thinking = ""
    return {"subagents": subagents, "background": background, "orchestration": orchestration,
            "thinking": thinking}

"""Live-status fields owned outside the live-status store.

``/api/chat-live-status`` and its batch route merge ``live_status_extras`` into
each chat's payload:

- ``subagents``: the parent's sub-agent launchers (``api.subagents.service``)
- ``background``: work the chat still owns while no turn runs — detached
  waits, watch jobs, supervision schedules and processes its agents left
  running (``api.jobs.work_tree.background_summary``), or ``None``

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
    return {"subagents": subagents, "background": background}

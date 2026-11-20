"""Bare turns: the agent gets the typed request and nothing else.

For plain-chat targets (a web-chat model behind a harness, a one-shot helper)
the Cuttle briefing is dead weight or worse: the model reads rules it cannot
follow. Two switches, either is enough:

* an agent manifest declares ``context: bare`` (every turn of that harness);
* ``settings.json → context.bare_models`` lists ``agent:model-glob`` patterns,
  e.g. ``"opencode:g4f/*"`` (only those models of that harness).

A bare turn skips the envelope, handoff, context delta and the per-agent
prompt preference. An unpinned turn has no model to match, so only the
manifest switch covers it.
"""

from __future__ import annotations

from fnmatch import fnmatchcase
from typing import Any, List, Optional

SETTINGS_KEY = "context"


def bare_model_patterns() -> List[str]:
    try:
        from managers.settings_manager import get_settings_manager

        sm = get_settings_manager()
        try:
            sm.reload()  # the setting may have been written by another process
        except Exception:
            pass
        raw = sm.get_setting(SETTINGS_KEY)
    except Exception:
        return []
    patterns = raw.get("bare_models") if isinstance(raw, dict) else None
    return [str(p).strip() for p in patterns or [] if isinstance(p, str) and ":" in p]


def is_bare_turn(manifest: Any, model: Optional[str]) -> bool:
    if str(getattr(manifest, "context", "") or "").strip().lower() == "bare":
        return True
    agent_id = str(getattr(manifest, "id", "") or "").strip().lower()
    name = str(model or "").strip()
    if not agent_id or not name:
        return False
    for pattern in bare_model_patterns():
        agent, _, glob = pattern.partition(":")
        if agent.strip().lower() == agent_id and fnmatchcase(name, glob.strip()):
            return True
    return False

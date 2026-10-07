"""
Agent Harness — plugin-style agent CLI integration for Cuttle web chat / Discord / router.

Layout (folder per agent)::

    agent_harness/
      kernel.py          shared web/Discord runner
      catalog.py         discover manifests + adapters (bundled + drop-ins)
      types.py
      agents/
        opencode/
        antigravity/
        cursor/
        codex/
        muse/
        claude/
        hermes/
        deepseek/

Drop-in roots (same manifest + adapter contract; cannot shadow bundled ids)::

    <home>/personal/agents/<id>/
    {Cuttle}/.cuttle_global/agents/<id>/
    {project}/.cuttle/agents/<id>/
    CUTTLE_AGENTS_DIR (extra search paths)

Adding an agent should mean filling that folder — not forking web_chat_api.py.
Bundled first-party connectors: cursor, codex, muse, claude, opencode,
antigravity, hermes, deepseek.
Shared context / hot-swap handoff: ``api.cuttle_brain`` (Context Compiler).
"""

from api.agent_harness.catalog import (
    get_agent,
    list_agent_manifests,
    list_agents,
    match_slash_command,
    public_catalog,
)
from api.agent_harness.kernel import run_agent_web_command
from api.agent_harness.types import AgentManifest, AgentResult, normalize_chat_session_id

__all__ = [
    "AgentManifest",
    "AgentResult",
    "get_agent",
    "list_agent_manifests",
    "list_agents",
    "match_slash_command",
    "normalize_chat_session_id",
    "public_catalog",
    "run_agent_web_command",
]

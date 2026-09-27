"""
Cuttle Brain — cognition services that sit *above* any one agent CLI.

The Brain is not a mega-agent and not a required CLI. It is a small package of
shared services the harness (and later tools) call so every agent sees the same
project truth.

First solid component: **Context Compiler** (`context_compiler.py`).

Related:
- `handoff.py` — hot-swap tracking + Cuttle-owned transcript deltas
- `context_delta.py` — resume-time rule/inventory deltas (no full re-inject)
- `cli.py` — optional thin CLI for agents/debugging (`python -m api.cuttle_brain`)
"""

from api.cuttle_brain.context_compiler import (
    CompiledContext,
    compile_context,
)
from api.cuttle_brain.handoff import (
    AgentHandoff,
    build_handoff,
    get_last_agent,
    record_last_agent,
)

__all__ = [
    "AgentHandoff",
    "CompiledContext",
    "build_handoff",
    "compile_context",
    "get_last_agent",
    "record_last_agent",
]

"""Environment boundary for independently authenticated guest agent CLIs."""

import os
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Mapping, Optional

_operation_context = ContextVar("cuttle_operation_context", default={})


def operation_actor(*, source="cli", session_id=None, agent_id=None, user_id=None):
    """Bounded provenance for local agent operations (identity, not credentials)."""
    env = dict(os.environ)
    env.update(_operation_context.get())
    return {
        "source": source,
        "session_id": str(session_id if session_id is not None else env.get("CUTTLE_CHAT_SESSION_ID", ""))[:80],
        "agent_id": str(agent_id if agent_id is not None else env.get("CUTTLE_AGENT_ID", ""))[:80],
        "model": env.get("CUTTLE_AGENT_MODEL", "")[:160] if source != "ui" else "",
        "run_id": env.get("CUTTLE_AGENT_RUN_ID", "")[:160] if source != "ui" else "",
        "project_path": env.get("CUTTLE_AGENT_PROJECT_PATH", "")[:2000] if source != "ui" else "",
        "user_id": user_id,
    }


@contextmanager
def agent_operation_context(*, session_id, agent_id, model=None, run_id=None, project_path=None):
    """Per-execution attribution, never mutate the Flask process environment."""
    values = {
        "CUTTLE_CHAT_SESSION_ID": session_id, "CUTTLE_AGENT_ID": agent_id,
        "CUTTLE_AGENT_MODEL": model, "CUTTLE_AGENT_RUN_ID": run_id,
        "CUTTLE_AGENT_PROJECT_PATH": project_path,
    }
    token = _operation_context.set({k: str(v) if v is not None else "" for k, v in values.items()})
    try:
        yield
    finally:
        _operation_context.reset(token)

# These override native CLI authentication or redirect provider requests.
_AUTH_OVERRIDES = frozenset({
    "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "OPENAI_ACCESS_TOKEN",
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
    "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_ACCESS_TOKEN",
    "ANTHROPIC_BASE_URL", "OPENAI_BASE_URL", "OPENAI_API_BASE",
    "DEEPSEEK_BASE_URL", "OPENROUTER_BASE_URL", "AZURE_OPENAI_ENDPOINT",
})


def agent_cli_env(source: Optional[Mapping[str, str]] = None) -> dict[str, str]:
    """Keep runtime/native config paths; never pass host provider credentials.

    CLI-managed credentials remain in the CLI's own files. The parent mapping
    is never mutated, so Cuttle's direct API services retain their credentials.
    """
    source = os.environ if source is None else source
    env = {
        key: value for key, value in source.items()
        if key.upper() not in _AUTH_OVERRIDES
        and not key.upper().endswith(("API_KEY", "API_TOKEN", "AUTH_TOKEN"))
    }
    env.update(_operation_context.get())
    return env

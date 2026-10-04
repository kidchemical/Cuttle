"""Environment boundary for independently authenticated guest agent CLIs."""

import os
from typing import Mapping, Optional

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
    return {
        key: value for key, value in source.items()
        if key.upper() not in _AUTH_OVERRIDES
        and not key.upper().endswith(("API_KEY", "API_TOKEN", "AUTH_TOKEN"))
    }

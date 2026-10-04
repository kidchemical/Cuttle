"""Load a Discord bot token for REST agent-ops. Does not start a gateway."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def load_discord_bot_token() -> Optional[str]:
    for key in ("DISCORD_TOKEN", "DISCORD_BOT_TOKEN"):
        val = (os.getenv(key) or "").strip().strip('"').strip("'")
        if val and not val.startswith("your_"):
            return val
    try:
        from core.runtime_paths import secrets_dir

        secret = secrets_dir() / "discord bot token.txt"
        if secret.is_file():
            tok = secret.read_text(encoding="utf-8").strip()
            if tok:
                return tok
    except OSError:
        pass
    try:
        env_path = Path(__file__).resolve().parents[2] / ".env"
        if env_path.is_file():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("DISCORD_TOKEN=") or line.startswith("DISCORD_BOT_TOKEN="):
                    tok = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if tok and not tok.startswith("your_"):
                        return tok
    except OSError:
        pass
    return None

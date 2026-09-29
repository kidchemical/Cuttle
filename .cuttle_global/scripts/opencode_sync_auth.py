"""Sync Cuttle provider API keys into OpenCode's credential store (no LLM).

Reads keys from process env (preferred) or ``src/.env``. Writes
``~/.local/share/opencode/auth.json`` and optionally pins a default model in
``~/.config/opencode/opencode.json``. Never prints secret values — only status.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, Optional, Tuple

# Provider id in OpenCode auth.json → env var name(s) to try, in order.
_PROVIDERS: Dict[str, Tuple[str, ...]] = {
    "openai": ("OPENAI_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "openrouter": ("OPENROUTER_API_KEY", "OPENROUTER_KEY"),
}

_DEFAULT_MODELS: Dict[str, str] = {
    "openai": "openai/gpt-4o-mini",
    "anthropic": "anthropic/claude-sonnet-4",
    "openrouter": "openrouter/z-ai/glm-5.3-flash",
}

# When syncing multiple providers, pin the first available default in this order.
_PIN_PRIORITY = ("openrouter", "openai", "anthropic")


def _cuttle_root() -> Path:
    env = (os.environ.get("CUTTLE_PROJECT_PATH") or "").strip()
    if env:
        return Path(env).resolve()
    # .cuttle_global/scripts/opencode_sync_auth.py → repo root
    return Path(__file__).resolve().parents[2]


def _env_file_path() -> Path:
    return _cuttle_root() / "src" / ".env"


def _auth_json_path() -> Path:
    override = (os.environ.get("OPENCODE_AUTH_PATH") or "").strip()
    if override:
        return Path(override)
    # OpenCode uses XDG-style ~/.local/share/opencode/auth.json on all platforms.
    return Path.home() / ".local" / "share" / "opencode" / "auth.json"


def _opencode_config_path() -> Path:
    override = (os.environ.get("OPENCODE_CONFIG_PATH") or "").strip()
    if override:
        return Path(override)
    return Path.home() / ".config" / "opencode" / "opencode.json"


def _read_dotenv_value(path: Path, key: str) -> Optional[str]:
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    # KEY=value / KEY="value" / export KEY=value — do not log the value.
    pat = re.compile(
        rf"^\s*(?:export\s+)?{re.escape(key)}\s*=\s*(.*)$",
        re.IGNORECASE | re.MULTILINE,
    )
    m = pat.search(text)
    if not m:
        return None
    raw = m.group(1).strip()
    if not raw or raw.startswith("#"):
        return None
    if (raw.startswith('"') and raw.endswith('"')) or (
        raw.startswith("'") and raw.endswith("'")
    ):
        raw = raw[1:-1]
    raw = raw.strip()
    return raw or None


def resolve_key(provider: str) -> Tuple[Optional[str], str]:
    """Return (key_or_none, source_label). Never logs the key."""
    names = _PROVIDERS.get(provider) or ()
    for name in names:
        val = (os.environ.get(name) or "").strip()
        if val:
            return val, f"env:{name}"
    dotenv = _env_file_path()
    for name in names:
        val = _read_dotenv_value(dotenv, name)
        if val:
            return val, f"dotenv:{name}"
    return None, "missing"


def key_present(provider: str) -> bool:
    key, _ = resolve_key(provider)
    return bool(key)


def load_auth() -> Dict[str, object]:
    path = _auth_json_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_auth(data: Dict[str, object]) -> Path:
    path = _auth_json_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def pin_default_model(model: str) -> Path:
    path = _opencode_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data: Dict[str, object] = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError):
            # Prefer not to clobber a JSONC/commented file — write sibling .json.
            pass
    data.setdefault("$schema", "https://opencode.ai/config.json")
    data["model"] = model
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def sync_provider(provider: str, *, pin_model: bool = True) -> str:
    provider = (provider or "").strip().lower()
    if provider not in _PROVIDERS:
        return f"Unknown provider '{provider}'. Use: {', '.join(sorted(_PROVIDERS))}"
    key, source = resolve_key(provider)
    if not key:
        names = " / ".join(_PROVIDERS[provider])
        return f"Missing {names} in process env and src/.env — nothing written."
    auth = load_auth()
    auth[provider] = {"type": "api", "key": key}
    path = save_auth(auth)
    parts = [f"Synced {provider} → OpenCode auth ({source}, file written)."]
    if pin_model:
        model = _DEFAULT_MODELS[provider]
        cfg = pin_default_model(model)
        parts.append(f"Default model pinned to {model}.")
        parts.append(f"Config: {cfg}")
    parts.append(f"Auth: {path}")
    return " ".join(parts)


def status_line() -> str:
    bits = []
    for provider in sorted(_PROVIDERS):
        present = key_present(provider)
        auth = load_auth()
        in_auth = isinstance(auth.get(provider), dict) and bool(
            (auth.get(provider) or {}).get("key")  # type: ignore[union-attr]
        )
        bits.append(
            f"{provider}: cuttle={'yes' if present else 'no'} opencode_auth={'yes' if in_auth else 'no'}"
        )
    cfg = _opencode_config_path()
    model = ""
    if cfg.is_file():
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                model = str(data.get("model") or "").strip()
        except (OSError, json.JSONDecodeError):
            pass
    model_bit = f" model={model}" if model else " model=(unset)"
    return "OpenCode auth status — " + "; ".join(bits) + model_bit


def main(argv: Optional[list] = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    # Action forms pass provider via CUTTLE_PARAM_PROVIDER.
    provider = (os.environ.get("CUTTLE_PARAM_PROVIDER") or "").strip().lower()
    if args:
        provider = (args[0] or provider).strip().lower()
    if not provider:
        provider = "status"

    pin_raw = (os.environ.get("CUTTLE_PARAM_PIN_MODEL") or "true").strip().lower()
    pin_model = pin_raw not in ("0", "false", "no", "off")

    if provider == "status":
        print(status_line())
        return 0
    if provider == "both":
        msgs = []
        for p in ("openai", "anthropic"):
            msgs.append(sync_provider(p, pin_model=False))
        if pin_model:
            for p in ("openai", "anthropic"):
                if key_present(p):
                    pin_default_model(_DEFAULT_MODELS[p])
                    msgs.append(f"Default model pinned to {_DEFAULT_MODELS[p]}.")
                    break
        print(" | ".join(msgs))
        return 0 if all("Synced" in m for m in msgs[:2]) else 1

    if provider == "all":
        msgs = []
        for p in _PIN_PRIORITY:
            msgs.append(sync_provider(p, pin_model=False))
        if pin_model:
            for p in _PIN_PRIORITY:
                if key_present(p):
                    pin_default_model(_DEFAULT_MODELS[p])
                    msgs.append(f"Default model pinned to {_DEFAULT_MODELS[p]}.")
                    break
        print(" | ".join(msgs))
        sync_msgs = [m for m in msgs if m.startswith("Synced") or m.startswith("Missing")]
        return 0 if any(m.startswith("Synced") for m in sync_msgs) else 1

    msg = sync_provider(provider, pin_model=pin_model)
    print(msg)
    return 0 if msg.startswith("Synced") else 1


if __name__ == "__main__":
    raise SystemExit(main())

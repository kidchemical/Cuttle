"""Machine settings via SettingsManager; shared secrets via .env/secret files."""

from __future__ import annotations

import os
import socket
from pathlib import Path
from typing import Any, Dict, List


def _read_env_file() -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        from core.runtime_paths import env_file

        env_path = env_file()
        if not env_path.is_file():
            return out
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            out[key.strip()] = val.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def _env(key: str, default: str = "") -> str:
    file_env = _read_env_file()
    return (os.getenv(key) or file_env.get(key) or default).strip()


def _settings_block() -> Dict[str, Any]:
    try:
        from managers.settings_manager import get_settings_manager

        sm = get_settings_manager()
        # Always re-read disk — workers/allowlists change without a Flask restart.
        try:
            sm.reload()
        except Exception:
            pass
        block = sm.get_setting("device_workers") or {}
        return block if isinstance(block, dict) else {}
    except Exception:
        return {}


def device_workers_enabled() -> bool:
    """Host coordinator + local worker loops honor this gate."""
    env = _env("CUTTLE_DEVICE_WORKERS_ENABLED", "").lower()
    if env in ("0", "false", "no", "off"):
        return False
    if env in ("1", "true", "yes", "on"):
        return True
    block = _settings_block()
    if "enabled" in block:
        return bool(block.get("enabled"))
    # Default on for host lab once the package is present (W1).
    return True


def worker_token() -> str:
    """Optional shared bearer override (legacy). Prefer auto-enroll per device."""
    explicit = _env("CUTTLE_DEVICE_WORKERS_TOKEN")
    if explicit:
        return explicit
    from core.runtime_paths import secrets_dir
    from managers.settings_storage import read_json
    stored = read_json(secrets_dir() / "worker_shared_token.json").get("token")
    # Existing installs remain compatible until the guarded cold-start split.
    return str(stored or _settings_block().get("token") or "").strip()


def coordinator_base_url() -> str:
    """Where a remote/sidecar worker registers. Empty → local loop uses in-process store."""
    explicit = _env("CUTTLE_DEVICE_WORKERS_COORDINATOR_URL")
    if explicit:
        return explicit.rstrip("/")
    block = _settings_block()
    url = str(block.get("coordinator_url") or "").strip().rstrip("/")
    return url


def worker_id() -> str:
    explicit = _env("CUTTLE_DEVICE_WORKER_ID")
    if explicit:
        return explicit
    block = _settings_block()
    wid = str(block.get("worker_id") or "").strip()
    if wid:
        return wid
    return socket.gethostname().lower().replace(" ", "-")


def poll_seconds() -> int:
    raw = _env("CUTTLE_DEVICE_WORKERS_POLL_SECONDS") or str(
        _settings_block().get("poll_seconds") or 5
    )
    try:
        return max(2, min(int(raw), 120))
    except ValueError:
        return 5


def lease_seconds() -> int:
    try:
        return max(60, min(int(_settings_block().get("lease_seconds") or 600), 7200))
    except (TypeError, ValueError):
        return 600


def heartbeat_stale_seconds() -> int:
    try:
        return max(30, min(int(_settings_block().get("stale_seconds") or 45), 600))
    except (TypeError, ValueError):
        return 45


def interactive_priority() -> str:
    raw = (
        _env("CUTTLE_DEVICE_WORKER_INTERACTIVE")
        or str(_settings_block().get("interactive_priority") or "low")
    ).strip().lower()
    return raw if raw in ("low", "high") else "low"


def allowed_path_prefixes() -> List[str]:
    """Absolute path prefixes (or UNC) workers may read/write for file_copy / blender."""
    block = _settings_block()
    raw = block.get("allowed_path_prefixes")
    prefixes: List[str] = []
    if isinstance(raw, list):
        prefixes.extend(str(p) for p in raw if str(p).strip())
    home = Path.home()
    defaults = [
        str(home / "Desktop"),
        str(home / "Documents"),
        str(home / "Downloads"),
        str(home / "Pictures"),
        str(home / "Dev" / "Cuttle"),
        str(Path(os.environ.get("LOCALAPPDATA") or "") / "cuttle-desktop"),
        str(Path(os.environ.get("TEMP") or os.environ.get("TMP") or "/tmp")),
    ]
    # Always allow user profile common folders + TEMP; settings can add shares.
    for d in defaults:
        if d and d not in prefixes:
            prefixes.append(d)
    return prefixes


def default_settings() -> Dict[str, Any]:
    return {
        "enabled": True,
        "coordinator_url": "",
        "worker_id": "",
        "poll_seconds": 5,
        "lease_seconds": 600,
        "stale_seconds": 45,
        "interactive_priority": "low",
        "local_worker": True,
        "allowed_path_prefixes": [],
        # When true, host may auto-suggest/enqueue mesh plans (still prefer explicit verbs).
        "auto_mesh": False,
        # Dangerous: free-form local shell on the worker (prefer recipes / cuttle_self_update).
        "execute_shell_unsafe_enabled": False,
        "execute_shell_enabled": False,  # legacy alias of execute_shell_unsafe_enabled
        # Optional SSH transport for shell (not used for enroll/claim/self-update).
        "execute_shell_ssh_enabled": False,
        "ssh_host": "",
        "ssh_user": "",
        "ssh_port": 22,
        "ssh_identity": "",
        # Optional command prefix allowlist for execute_shell_unsafe / execute_shell_ssh (empty = any).
        "execute_shell_prefixes": [],
        "shell_recipes": {},
        "ssh_approval_required": True,
        "ssh_approval_timeout_seconds": 300,
        # Queue hygiene: type TTLs / attempt budgets (see job_policy.py).
        "queue_ttl_seconds": {},
        "max_attempts": {},
        "default_queue_ttl_seconds": 21600,
        "default_max_attempts": 2,
        "no_requeue_types": [],
    }

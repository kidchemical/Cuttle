"""GitHub App authentication — the single owner for app-identity concerns.

Bring-your-own model: every Cuttle install stores its own credentials, so any
user can connect their own GitHub App. Nothing here is shared between
installs. The private key stays local; short-lived tokens authenticate GitHub requests.

Storage split (deliberate):
- ``settings.json`` key ``github_app`` holds only non-secret config
  (``app_id``, ``installation_id``). The open ``/api/app-settings``
  aggregate can expose settings.json, so key material must never land there.
- The RSA private key lives in the install-local secrets dir
  (``core.runtime_paths.secrets_dir()``, mode 0o600). Reads/writes of it
  stay in this module.

Token flow: mint a short-lived RS256 JWT from the private key, trade it for
an installation token (1h) via the GitHub REST API, and use that token for
app-identity operations. This module never imports ``web_chat_api``.
"""

from __future__ import annotations

import base64
import json
import re
import time
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from core.runtime_paths import secrets_dir

SETTINGS_KEY = "github_app"
KEY_FILENAME = "github_app_key.pem"
API_BASE = "https://api.github.com"

# Overridable in tests to avoid touching the real secrets dir.
KEY_PATH = secrets_dir() / KEY_FILENAME


def _key_path(key_path: Optional[Path] = None) -> Path:
    return Path(key_path) if key_path is not None else KEY_PATH


def get_config(settings, key_path: Optional[Path] = None) -> Dict[str, Any]:
    """Public config view. Never contains key material."""
    stored = settings.get_setting(SETTINGS_KEY) or {}
    if not isinstance(stored, dict):
        stored = {}
    verified = stored.get("last_verified") or {}
    if not isinstance(verified, dict):
        verified = {}
    return {
        "app_id": str(stored.get("app_id") or ""),
        "installation_id": str(stored.get("installation_id") or ""),
        "key_configured": _key_path(key_path).exists(),
        "connected": bool(
            stored.get("app_id")
            and stored.get("installation_id")
            and _key_path(key_path).exists()
        ),
        "last_verified": verified,
    }


def bot_identity(bot_user_id: str, app_slug: str) -> Dict[str, str]:
    """Author/committer identity that attributes commits to the app."""
    slug = (app_slug or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9-]+", slug) or not re.fullmatch(r"[1-9][0-9]*", str(bot_user_id)):
        raise ValueError("GitHub App bot identity is missing or invalid; re-check the connection")
    return {
        "name": f"{slug}[bot]",
        "email": f"{bot_user_id}+{slug}[bot]@users.noreply.github.com",
    }


def validate_save(
    data: Dict[str, Any], has_existing_key: bool = False
) -> Tuple[bool, str, Dict[str, str]]:
    """Validate a save payload. Returns (ok, error, clean).

    ``clean`` holds ``app_id``/``installation_id`` and, only when the client
    sent one, ``private_key``. An empty key field keeps the stored key.
    """
    if not isinstance(data, dict):
        return False, "No data provided", {}
    app_id = str(data.get("app_id") or "").strip()
    installation_id = str(data.get("installation_id") or "").strip()
    raw_key = data.get("private_key")
    private_key = str(raw_key or "").strip().replace("\r\n", "\n")

    if not app_id.isdigit():
        return False, "app_id must be the numeric App ID from the app settings page", {}
    if not installation_id.isdigit():
        return False, "installation_id must be the numeric installation id", {}
    if private_key:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        try:
            key = serialization.load_pem_private_key(private_key.encode("utf-8"), password=None)
        except (ValueError, TypeError):
            return False, "private_key must be a valid unencrypted RSA PEM private key", {}
        if not isinstance(key, rsa.RSAPrivateKey):
            return False, "private_key must be an RSA private key for RS256 authentication", {}
    elif not has_existing_key:
        return False, "private_key is required (paste the .pem contents)", {}
    clean = {"app_id": app_id, "installation_id": installation_id}
    if private_key:
        clean["private_key"] = private_key
    return True, "", clean


def save_config(
    settings,
    app_id: str,
    installation_id: str,
    private_key: Optional[str] = None,
    key_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Validate before mutation and atomically replace key bytes.

    Direct callers get the same validation as HTTP callers. A storage failure
    restores the prior key and settings instead of reporting a successful save.
    """
    import copy

    target = _key_path(key_path)
    ok, error, clean = validate_save(
        {"app_id": app_id, "installation_id": installation_id, "private_key": private_key},
        has_existing_key=target.exists(),
    )
    if not ok:
        raise ValueError(error)
    previous_config = copy.deepcopy(settings.get_setting(SETTINGS_KEY) or {})
    new_key = clean.get("private_key")
    previous_key = target.read_bytes() if new_key and target.exists() else None
    key_written = False
    settings_attempted = False
    try:
        if new_key:
            _write_key_atomically(target, (new_key + "\n").encode("utf-8"))
            key_written = True
        settings_attempted = True
        if settings.set_setting(SETTINGS_KEY, {
            "app_id": clean["app_id"], "installation_id": clean["installation_id"],
        }) is False:
            raise RuntimeError("Could not save GitHub App settings")
    except Exception:
        if key_written:
            if previous_key is None:
                target.unlink()
            else:
                _write_key_atomically(target, previous_key)
        if settings_attempted:
            settings.set_setting(SETTINGS_KEY, previous_config)
        raise
    return get_config(settings, key_path)


def _write_key_atomically(target: Path, data: bytes) -> None:
    import os
    import tempfile

    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".github-app-key-", dir=target.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def remove_config(settings, key_path: Optional[Path] = None) -> Dict[str, Any]:
    """Disconnect: drop stored ids and delete the key file. Returns the public view."""
    settings.set_setting(SETTINGS_KEY, {})
    try:
        target = _key_path(key_path)
        if target.exists():
            target.unlink()
    except OSError:
        pass
    return get_config(settings, key_path)


def read_private_key(key_path: Optional[Path] = None) -> str:
    """Load the stored private key. Raises RuntimeError when absent."""
    target = _key_path(key_path)
    if not target.exists():
        raise RuntimeError("No GitHub App private key saved yet")
    return target.read_text(encoding="utf-8").strip()


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def mint_jwt(app_id: str, private_pem: str, now: Optional[int] = None) -> str:
    """Mint a short-lived app JWT (RS256, ~9min expiry; GitHub caps at 10)."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    stamp = int(now) if now is not None else int(time.time())
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    payload = _b64url(
        json.dumps({"iat": stamp - 60, "exp": stamp + 540, "iss": str(app_id)}).encode()
    )
    signing_input = f"{header}.{payload}".encode("ascii")
    key = serialization.load_pem_private_key(private_pem.encode("utf-8"), password=None)
    sig = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{payload}.{_b64url(sig)}"


def _api(
    method: str,
    url: str,
    token: str,
    payload: Optional[Dict[str, Any]] = None,
    timeout: int = 20,
) -> Dict[str, Any]:
    body = json.dumps(payload or {}).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return json.loads(res.read().decode("utf-8") or "{}")
    except Exception as e:
        raise RuntimeError(f"GitHub API call failed: {e}")


def request_installation_token(
    app_id: str,
    installation_id: str,
    private_pem: str,
    api_call: Optional[Callable[..., Dict[str, Any]]] = None,
) -> Tuple[str, str]:
    """Trade the app JWT for a 1h installation token. Returns (token, expires_at)."""
    call = api_call or _api
    jwt_token = mint_jwt(app_id, private_pem)
    data = call(
        "POST",
        f"{API_BASE}/app/installations/{installation_id}/access_tokens",
        jwt_token,
        {},
    )
    token = str(data.get("token") or "")
    if not token:
        raise RuntimeError("GitHub refused the installation token request")
    return token, str(data.get("expires_at") or "")


def test_connection(
    settings,
    key_path: Optional[Path] = None,
    api_call: Optional[Callable[..., Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Mint a token and prove the installation exists.

    Returns app display facts only — never the token itself.
    """
    call = api_call or _api
    cfg = get_config(settings, key_path)
    if not cfg["app_id"] or not cfg["installation_id"]:
        raise RuntimeError("App ID and installation ID must be saved first")
    pem = read_private_key(key_path)
    jwt_token = mint_jwt(cfg["app_id"], pem)
    token, _ = request_installation_token(
        cfg["app_id"], cfg["installation_id"], pem, api_call=call
    )
    installs = call(
        "GET",
        f"{API_BASE}/app/installations/{cfg['installation_id']}",
        jwt_token,
        None,
    )
    account = installs.get("account") or {}
    app_slug = str(installs.get("app_slug") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9-]+", app_slug):
        raise RuntimeError("GitHub did not return a valid app slug")
    bot_login = app_slug + "[bot]"
    bot = call("GET", f"{API_BASE}/users/{urllib.parse.quote(bot_login, safe='')}", token, None)
    if bot.get("login") != bot_login or bot.get("type") != "Bot":
        raise RuntimeError("GitHub did not return the app's bot account")
    bot_user_id = str(bot.get("id") or "")
    identity = bot_identity(bot_user_id, app_slug)
    result = {
        "success": True,
        "app_slug": app_slug,
        "bot_user_id": bot_user_id,
        "account": str(account.get("login") or ""),
        "installation_id": cfg["installation_id"],
        "commit_as": f"{identity['name']} <{identity['email']}>",
        "token_valid": bool(token),
    }
    stored = dict(settings.get_setting(SETTINGS_KEY) or {})
    if str(stored.get("app_id")) != cfg["app_id"] or str(stored.get("installation_id")) != cfg["installation_id"]:
        raise RuntimeError("GitHub App settings changed during verification; re-check the connection")
    stored["last_verified"] = {
        "app_id": cfg["app_id"],
        "installation_id": cfg["installation_id"],
        "app_slug": result["app_slug"],
        "bot_user_id": bot_user_id,
        "account": result["account"],
        "commit_as": result["commit_as"],
        "verified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if settings.set_setting(SETTINGS_KEY, stored) is False:
        raise RuntimeError("Could not save verified GitHub App identity")
    return result


def commit_env(cwd: str, base_env: Dict[str, str], settings=None) -> Optional[Dict[str, str]]:
    """Identity for Cuttle-created commits in the verified GitHub account.

    No network, token minting, or gitconfig writes on the commit path. Other
    hosts/accounts retain their existing identity. A configured but unverified
    app rejects GitHub commits rather than silently using personal attribution.
    """
    import subprocess

    if settings is None:
        from managers.settings_manager import get_settings_manager
        settings = get_settings_manager()
    cfg = get_config(settings)
    if not cfg["connected"]:
        return None
    remote = subprocess.run(
        ["git", "remote", "get-url", "origin"], cwd=cwd, env=base_env,
        capture_output=True, text=True, timeout=5,
    )
    if remote.returncode:
        return None
    url = (remote.stdout or "").strip()
    scp = re.fullmatch(r"(?:[^@/:]+@)?github\.com:([^/]+)/([^/]+?)(?:\.git)?/?", url, re.I)
    if scp:
        owner = scp[1]
    else:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in ("https", "http", "ssh", "git") or parsed.hostname != "github.com":
            return None
        parts = parsed.path.strip("/").split("/")
        if len(parts) != 2 or not all(parts):
            return None
        owner = parts[0]
    verified = cfg["last_verified"]
    if (verified.get("app_id") != cfg["app_id"] or
        verified.get("installation_id") != cfg["installation_id"] or
        not verified.get("bot_user_id") or not verified.get("account")):
        raise ValueError("Re-check the GitHub App connection in Settings before committing to GitHub")
    if owner.casefold() != str(verified["account"]).casefold():
        return None
    identity = bot_identity(str(verified["bot_user_id"]), str(verified.get("app_slug") or ""))
    return {**base_env,
            "GIT_AUTHOR_NAME": identity["name"], "GIT_AUTHOR_EMAIL": identity["email"],
            "GIT_COMMITTER_NAME": identity["name"], "GIT_COMMITTER_EMAIL": identity["email"]}

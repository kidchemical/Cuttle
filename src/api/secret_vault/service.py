"""Cuttle adapter for the secret vault: metadata, backends config, approvals.

State: ``<home>/secret_vault/vault.json`` (names, descriptions, backend
choice, project scope, usage, approval grants, configured backends). Secret
*values* never enter this file, the settings file, chat history, or logs —
they live only in the chosen backend and in the child process that uses them.

Approval policy (``config.approval``):

* ``per_chat``  — a chat needs one grant per secret (default).
* ``every_use`` — each run consumes a one-shot grant.
* ``never``     — a stored secret may be used without asking.

Entering a value from a chat's secret field grants that chat the secret.
Approval is a consent record enforced by Cuttle's own CLI; it is not a
sandbox against an agent running as the same OS user (see the runbook).
"""

from __future__ import annotations

import copy
import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from api.secret_vault.core.backends import (
    COMMAND_PRESETS, Backend, BackendError, build_backend)
from api.secret_vault.core.names import require_name, valid_name

FLAG_ID = "secret_vault"
APPROVAL_POLICIES = ("per_chat", "every_use", "never")
GRANT_MODES = ("chat", "once")
BUILTIN_BACKENDS = ({"id": "keyring", "kind": "keyring", "label": "OS keyring", "options": {}},)
_DEFAULT_CONFIG = {"approval": "per_chat", "default_backend": "keyring",
                   "redact": True, "askpass_max": 3}
_ONCE_TTL_SEC = 30 * 60


class VaultError(RuntimeError):
    """User-facing vault failure (safe to show; never contains a value)."""


# ── storage ────────────────────────────────────────────────────────────────


def vault_path() -> Path:
    from core.runtime_paths import runtime_state_path
    return runtime_state_path("secret_vault", "vault.json")


def _empty() -> Dict[str, Any]:
    return {"version": 1, "config": dict(_DEFAULT_CONFIG),
            "backends": [], "secrets": {}, "grants": []}


def _read(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _empty()
    except (OSError, ValueError) as exc:
        raise VaultError(f"Vault metadata is unreadable ({path}): {exc}") from exc
    if not isinstance(data, dict):
        return _empty()
    base = _empty()
    base["config"].update(data.get("config") if isinstance(data.get("config"), dict) else {})
    for key in ("backends", "grants"):
        if isinstance(data.get(key), list):
            base[key] = data[key]
    if isinstance(data.get("secrets"), dict):
        base["secrets"] = data["secrets"]
    return base


@contextmanager
def _state(write: bool = False) -> Iterator[Dict[str, Any]]:
    from managers.settings_storage import atomic_write, settings_lock
    path = vault_path()
    with settings_lock(path.with_name(path.name + ".lock")):
        data = _read(path)
        before = copy.deepcopy(data) if write else None
        yield data
        if write and data != before:
            atomic_write(path, data)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass


def is_enabled() -> bool:
    from api.experimental import is_enabled as flag_enabled
    return bool(flag_enabled(FLAG_ID))


def require_enabled() -> None:
    if not is_enabled():
        raise VaultError("The Secret vault is an experimental feature that is turned off. "
                         "Enable it in Settings → Experimental.")


# ── backends ───────────────────────────────────────────────────────────────

_backend_cache: Dict[str, Backend] = {}
_backend_lock = threading.Lock()


def _backend_rows(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = [dict(r) for r in BUILTIN_BACKENDS]
    ids = {r["id"] for r in rows}
    for row in data.get("backends") or []:
        if isinstance(row, dict) and valid_name(row.get("id")) and row["id"] not in ids:
            rows.append(row)
            ids.add(row["id"])
    return rows


def _backend(data: Dict[str, Any], backend_id: str) -> Backend:
    row = next((r for r in _backend_rows(data) if r["id"] == backend_id), None)
    if row is None:
        raise VaultError(f"Unknown storage backend '{backend_id}'.")
    key = json.dumps(row, sort_keys=True)
    with _backend_lock:
        hit = _backend_cache.get(key)
        if hit is None:
            try:
                hit = build_backend(row)
            except (ValueError, KeyError) as exc:
                raise VaultError(f"Backend '{backend_id}' is misconfigured: {exc}") from exc
            _backend_cache[key] = hit
    return hit


def reset_backend_cache() -> None:
    with _backend_lock:
        _backend_cache.clear()


def list_backends() -> List[Dict[str, Any]]:
    with _state() as data:
        rows = _backend_rows(data)
        default = data["config"].get("default_backend") or "keyring"
    out = []
    for row in rows:
        try:
            backend = build_backend(row)
            ok, detail = backend.available()
            writable = backend.writable
        except Exception as exc:  # misconfigured row stays visible
            ok, detail, writable = False, str(exc), False
        out.append({"id": row["id"], "kind": row.get("kind"), "label": row.get("label") or row["id"],
                    "options": row.get("options") or {}, "builtin": row["id"] == "keyring",
                    "available": ok, "detail": detail, "writable": writable,
                    "default": row["id"] == default})
    return out


def command_presets() -> List[Dict[str, Any]]:
    return [{"id": p.id, "label": p.label, "get": list(p.get), "set": list(p.set),
             "delete": list(p.delete), "first_line": p.first_line, "ref_hint": p.ref_hint,
             "help": p.help, "platforms": list(p.platforms), "writable": bool(p.set)}
            for p in COMMAND_PRESETS.values()]


def save_backend(row: Dict[str, Any]) -> Dict[str, Any]:
    bid = require_name(row.get("id"), "backend")
    if bid == "keyring" and row.get("kind") != "keyring":
        raise VaultError("'keyring' is the built-in backend id.")
    kind = str(row.get("kind") or "")
    options: Dict[str, Any] = {}
    preset = str(row.get("preset") or "")
    if kind == "command":
        if preset:
            if preset not in COMMAND_PRESETS:
                raise VaultError(f"Unknown preset '{preset}'.")
            p = COMMAND_PRESETS[preset]
            options = {"get": list(p.get), "set": list(p.set), "delete": list(p.delete),
                       "first_line": p.first_line, "preset": preset}
        src = row.get("options") if isinstance(row.get("options"), dict) else {}
        for key in ("get", "set", "delete"):
            if key in src:
                val = src[key]
                if not isinstance(val, list) or not all(isinstance(x, str) for x in val):
                    raise VaultError(f"'{key}' must be a list of argv strings.")
                options[key] = val
        if "first_line" in src:
            options["first_line"] = bool(src["first_line"])
        if not options.get("get"):
            raise VaultError("A command backend needs a 'get' command.")
    elif kind == "keyring":
        src = row.get("options") if isinstance(row.get("options"), dict) else {}
        options = {k: str(src[k]) for k in ("service", "keyring_backend") if src.get(k)}
    else:
        raise VaultError("Backend kind must be 'keyring' or 'command'.")
    clean = {"id": bid, "kind": kind, "label": str(row.get("label") or bid)[:80], "options": options}
    with _state(write=True) as data:
        data["backends"] = [r for r in data["backends"] if r.get("id") != bid] + [clean]
    reset_backend_cache()
    return clean


def delete_backend(backend_id: str) -> None:
    if backend_id == "keyring":
        raise VaultError("The built-in OS keyring backend cannot be removed.")
    with _state(write=True) as data:
        users = [n for n, s in data["secrets"].items() if s.get("backend") == backend_id]
        if users:
            raise VaultError(f"Backend '{backend_id}' still holds: {', '.join(sorted(users))}.")
        data["backends"] = [r for r in data["backends"] if r.get("id") != backend_id]
        if data["config"].get("default_backend") == backend_id:
            data["config"]["default_backend"] = "keyring"
    reset_backend_cache()


# ── config ─────────────────────────────────────────────────────────────────


def get_config() -> Dict[str, Any]:
    with _state() as data:
        return dict(data["config"])


def update_config(patch: Dict[str, Any]) -> Dict[str, Any]:
    with _state(write=True) as data:
        cfg = data["config"]
        if "approval" in patch:
            if patch["approval"] not in APPROVAL_POLICIES:
                raise VaultError(f"approval must be one of {', '.join(APPROVAL_POLICIES)}.")
            cfg["approval"] = patch["approval"]
        if "default_backend" in patch:
            ids = {r["id"] for r in _backend_rows(data)}
            if patch["default_backend"] not in ids:
                raise VaultError(f"Unknown backend '{patch['default_backend']}'.")
            cfg["default_backend"] = patch["default_backend"]
        if "redact" in patch:
            cfg["redact"] = bool(patch["redact"])
        if "askpass_max" in patch:
            try:
                cfg["askpass_max"] = max(1, min(10, int(patch["askpass_max"])))
            except (TypeError, ValueError) as exc:
                raise VaultError("askpass_max must be a number from 1 to 10.") from exc
        return dict(cfg)


# ── secrets ────────────────────────────────────────────────────────────────


def _public(name: str, meta: Dict[str, Any]) -> Dict[str, Any]:
    return {"name": name, "backend": meta.get("backend") or "keyring",
            "ref": meta.get("ref") or name, "description": meta.get("description") or "",
            "project": meta.get("project") or "", "stored": bool(meta.get("stored")),
            "created": meta.get("created"), "updated": meta.get("updated"),
            "last_used": meta.get("last_used"), "use_count": int(meta.get("use_count") or 0)}


def list_secrets() -> List[Dict[str, Any]]:
    with _state() as data:
        return [_public(n, m) for n, m in sorted(data["secrets"].items())]


def get_secret(name: str) -> Optional[Dict[str, Any]]:
    with _state() as data:
        meta = data["secrets"].get(name)
        return _public(name, meta) if meta is not None else None


def put_secret(name: str, *, value: Optional[str] = None, backend: Optional[str] = None,
               ref: Optional[str] = None, description: Optional[str] = None,
               project: Optional[str] = None, grant_session: Optional[str] = None) -> Dict[str, Any]:
    """Create/update a secret's metadata and, when ``value`` is given, store it."""
    require_name(name)
    if value is not None and (not isinstance(value, str) or value == ""):
        raise VaultError("The secret value is empty.")
    if value is not None and len(value) > 65536:
        raise VaultError("The secret value is too large (64 KB max).")
    now = time.time()
    with _state(write=True) as data:
        meta = dict(data["secrets"].get(name) or {"created": now})
        meta["backend"] = backend or meta.get("backend") or data["config"].get("default_backend") or "keyring"
        if ref is not None:
            meta["ref"] = str(ref).strip() or name
        meta.setdefault("ref", name)
        if description is not None:
            meta["description"] = str(description)[:300]
        if project is not None:
            meta["project"] = str(project).strip()
        store = _backend(data, meta["backend"])
        if value is not None:
            try:
                store.set(meta["ref"], value)
            except BackendError as exc:
                raise VaultError(str(exc)) from exc
            meta["stored"] = True
        elif not store.writable:
            meta["stored"] = True  # read-only managers hold the value already
        meta["updated"] = now
        data["secrets"][name] = meta
        if grant_session:
            _add_grant(data, grant_session, name, "chat", now)
        return _public(name, meta)


def delete_secret(name: str, *, delete_value: bool = True) -> None:
    with _state(write=True) as data:
        meta = data["secrets"].pop(name, None)
        if meta is None:
            raise VaultError(f"No secret named '{name}'.")
        data["grants"] = [g for g in data["grants"] if g.get("secret") != name]
        if delete_value:
            try:
                store = _backend(data, meta.get("backend") or "keyring")
                if store.writable:
                    store.delete(meta.get("ref") or name)
            except (BackendError, VaultError) as exc:
                raise VaultError(f"Removed from Cuttle, but the stored value could not be "
                                 f"deleted: {exc}") from exc


def resolve_value(name: str) -> str:
    """Read a secret's value for injection. Callers must never print it."""
    with _state() as data:
        meta = data["secrets"].get(name)
        if meta is None:
            raise VaultError(f"No secret named '{name}'.")
        store = _backend(data, meta.get("backend") or "keyring")
        ref = meta.get("ref") or name
    try:
        value = store.get(ref)
    except BackendError as exc:
        raise VaultError(f"Secret '{name}': {exc}") from exc
    if value is None or value == "":
        raise VaultError(f"Secret '{name}' has no stored value.")
    return value


def record_use(names: List[str]) -> None:
    now = time.time()
    with _state(write=True) as data:
        for name in names:
            meta = data["secrets"].get(name)
            if meta is not None:
                meta["last_used"] = now
                meta["use_count"] = int(meta.get("use_count") or 0) + 1


def scope_violations(names: List[str], cwd: str) -> List[Tuple[str, str]]:
    """Secrets scoped to a project whose root does not contain ``cwd``."""
    out = []
    try:
        here = Path(cwd).resolve()
    except OSError:
        here = Path(cwd)
    with _state() as data:
        for name in names:
            project = (data["secrets"].get(name) or {}).get("project") or ""
            if not project:
                continue
            try:
                root = Path(project).resolve()
            except OSError:
                root = Path(project)
            if here != root and root not in here.parents:
                out.append((name, project))
    return out


# ── approvals ──────────────────────────────────────────────────────────────


def normalize_session(session: Any) -> str:
    if session in (None, ""):
        return ""
    try:
        from api.cuttle_ui_capabilities import numeric_chat_session_id
        num = numeric_chat_session_id(session)
    except Exception:
        num = None
    return str(num) if num is not None else str(session).strip()


def _live(grant: Dict[str, Any], now: float) -> bool:
    if grant.get("mode") == "once":
        return now - float(grant.get("created") or 0) < _ONCE_TTL_SEC
    return True


def _add_grant(data: Dict[str, Any], session: str, name: str, mode: str, now: float,
               command: str = "") -> None:
    sid = normalize_session(session)
    if not sid:
        raise VaultError("A chat session is required to approve a secret.")
    if mode not in GRANT_MODES:
        raise VaultError(f"Grant mode must be one of {', '.join(GRANT_MODES)}.")
    grants = [g for g in data["grants"] if _live(g, now)]
    if mode == "chat":
        grants = [g for g in grants if not (g.get("session") == sid and g.get("secret") == name)]
    grants.append({"session": sid, "secret": name, "mode": mode, "created": now,
                   "command": str(command or "")[:300]})
    data["grants"] = grants


def grant(session: str, names: List[str], mode: str = "chat", command: str = "") -> None:
    now = time.time()
    with _state(write=True) as data:
        for name in names:
            if name not in data["secrets"]:
                raise VaultError(f"No secret named '{name}'.")
            _add_grant(data, session, name, mode, now, command)


def list_grants(session: Optional[str] = None) -> List[Dict[str, Any]]:
    now = time.time()
    sid = normalize_session(session) if session else ""
    with _state() as data:
        return [dict(g) for g in data["grants"]
                if _live(g, now) and (not sid or g.get("session") == sid)]


def revoke(session: Optional[str] = None, name: Optional[str] = None) -> int:
    sid = normalize_session(session) if session else ""
    with _state(write=True) as data:
        keep = [g for g in data["grants"]
                if not ((not sid or g.get("session") == sid) and (not name or g.get("secret") == name))]
        removed = len(data["grants"]) - len(keep)
        data["grants"] = keep
        return removed


def missing_approvals(session: str, names: List[str]) -> List[str]:
    """Names that still need a user approval for ``session`` under the policy."""
    now = time.time()
    with _state() as data:
        policy = data["config"].get("approval") or "per_chat"
        if policy == "never":
            return []
        sid = normalize_session(session)
        if not sid:
            return list(names)
        out = []
        for name in names:
            mine = [g for g in data["grants"]
                    if g.get("session") == sid and g.get("secret") == name and _live(g, now)]
            if policy == "per_chat" and mine:
                continue
            if policy == "every_use" and any(g.get("mode") == "once" for g in mine):
                continue
            out.append(name)
        return out


def consume_once(session: str, names: List[str]) -> None:
    """Spend one "allow once" grant per secret (chat-wide grants are kept)."""
    sid = normalize_session(session)
    with _state(write=True) as data:
        for name in names:
            for i, g in enumerate(data["grants"]):
                if g.get("session") == sid and g.get("secret") == name and g.get("mode") == "once":
                    del data["grants"][i]
                    break


def status() -> Dict[str, Any]:
    return {"enabled": is_enabled(), "config": get_config(), "backends": list_backends(),
            "presets": command_presets(), "secret_count": len(list_secrets())}

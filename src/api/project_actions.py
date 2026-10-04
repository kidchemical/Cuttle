"""
Project-local Cuttle actions: ``{project}/.cuttle/actions/*.yaml``.

Used for confirm-gated, no-LLM side effects (Discord posts, shell recipes, …).
Agents emit ``<cuttle_confirm action="…" …>payload</cuttle_confirm>``; Cuttle
registers a pending action, rewrites the reply into Confirm/Cancel buttons, and
executes when the user clicks — without another agent round-trip.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore

_CONFIRM_RE = re.compile(
    r"<cuttle_confirm\b([^>]*)>([\s\S]*?)</cuttle_confirm>",
    re.IGNORECASE,
)
_ATTR_RE = re.compile(
    r"""(\w+)\s*=\s*(?:"([^"]*)"|'([^']*)')""",
    re.IGNORECASE,
)

_PENDING_TTL_SEC = 3600
_pending_lock = threading.RLock()
_pending: Dict[str, Dict[str, Any]] = {}  # action_id -> record
_HMAC_SECRET_PATH = Path(__file__).resolve().parents[1] / "data" / "db" / "action_hmac_secret"
_hmac_secret_cache: Optional[bytes] = None

_BUTTON_CONFIRM_PREFIX = "project-action-confirm"
_BUTTON_CANCEL_PREFIX = "project-action-cancel"


def _actions_dirs_for_project(
    project_path: str, *, include_global: bool = True
) -> List[Tuple[Path, Path]]:
    """Return ``(actions_dir, owning_project_root)`` pairs to scan.

    Includes the given path, optional ``source/.cuttle``, and parent folders
    (so opening the workspace on ``…/Cuttle/src`` still finds ``…/Cuttle/.cuttle``).
    When an owner is the Cuttle install itself, its global
    ``.cuttle_global/actions`` (+ personal) dirs are appended last so project
    actions win by name and global actions (flask.restart, git.push, workers.*)
    stay resolvable from any chat — unless ``include_global`` is False
    (GLOBAL.ini severed leg).
    """
    try:
        from core.runtime_paths import rewrite_windows_lab_path

        project_path = rewrite_windows_lab_path(project_path)
    except Exception:
        pass
    try:
        root = Path(project_path).resolve()
    except OSError:
        return []
    if not root.is_dir():
        return []

    out: List[Tuple[Path, Path]] = []
    seen: set = set()

    def _add(owner: Path) -> None:
        try:
            owner_r = owner.resolve()
        except OSError:
            return
        key = str(owner_r).lower()
        if key in seen:
            return
        # personal/actions first so install-local YAML wins by action name
        personal = owner_r / ".cuttle" / "personal" / "actions"
        if personal.is_dir():
            seen.add(key + "::personal")
            out.append((personal, owner_r))
        primary = owner_r / ".cuttle" / "actions"
        if primary.is_dir():
            seen.add(key)
            out.append((primary, owner_r))
        nested_personal = owner_r / "source" / ".cuttle" / "personal" / "actions"
        if nested_personal.is_dir():
            out.append((nested_personal, owner_r))
        nested = owner_r / "source" / ".cuttle" / "actions"
        if nested.is_dir():
            # Owning root for Unity-style nested .cuttle is still the project root.
            seen.add(key + "::source")
            out.append((nested, owner_r))
        if not include_global:
            return
        hub_personal = owner_r / ".cuttle_global" / "personal" / "actions"
        if hub_personal.is_dir():
            seen.add(key + "::global-personal")
            out.append((hub_personal, owner_r))
        hub_actions = owner_r / ".cuttle_global" / "actions"
        if hub_actions.is_dir():
            seen.add(key + "::global")
            out.append((hub_actions, owner_r))

    _add(root)
    cur = root
    for _ in range(4):
        parent = cur.parent
        if parent == cur:
            break
        _add(parent)
        cur = parent
    return out


def _parse_action_file(path: Path, project_path: str) -> Optional[Dict[str, Any]]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    meta: Dict[str, Any] = {}
    if yaml is not None:
        try:
            loaded = yaml.safe_load(raw) or {}
            if isinstance(loaded, dict):
                meta = loaded
        except Exception:
            return None
    else:
        for line in raw.splitlines():
            if ":" in line and not line.startswith(" "):
                k, _, v = line.partition(":")
                meta[k.strip()] = v.strip().strip('"').strip("'")
    name = str(meta.get("name") or path.stem).strip()
    if not name:
        return None
    action_type = str(meta.get("type") or meta.get("handler") or name).strip()
    title = str(meta.get("title") or meta.get("label") or name).strip()
    description = str(meta.get("description") or meta.get("desc") or "").strip()
    run = meta.get("run") or meta.get("shell") or None
    if run is not None:
        run = str(run).strip() or None
    run_posix = meta.get("run_posix") or meta.get("run_linux") or meta.get("run_unix")
    if run_posix is not None:
        run_posix = str(run_posix).strip() or None
    workdir = str(meta.get("workdir") or meta.get("cwd") or "").strip() or None
    channels = meta.get("channels") if isinstance(meta.get("channels"), dict) else {}
    channels = {str(k): str(v) for k, v in channels.items()}
    repos = meta.get("repos") if isinstance(meta.get("repos"), dict) else {}
    repos = {str(k): str(v) for k, v in repos.items()}
    guild_id = str(meta.get("guild_id") or meta.get("guild") or "").strip() or None
    return {
        "name": name,
        "type": action_type,
        "title": title,
        "description": description,
        "run": run,
        "run_posix": run_posix,
        "workdir": workdir,
        "channels": channels,
        "repos": repos,
        "guild_id": guild_id,
        "path": str(path),
        "project_path": str(Path(project_path).resolve()),
        "raw": meta,
    }


def list_project_actions(
    project_path: str, *, include_global: bool = True, _include_disabled: bool = False
) -> List[Dict[str, Any]]:
    if not project_path:
        return []
    seen: set = set()
    out: List[Dict[str, Any]] = []
    for actions_dir, owner_root in _actions_dirs_for_project(
        project_path, include_global=include_global
    ):
        try:
            files = sorted(actions_dir.glob("*.y*ml"), key=lambda p: p.name.lower())
        except OSError:
            continue
        for path in files:
            if path.suffix.lower() not in (".yaml", ".yml"):
                continue
            action = _parse_action_file(path, str(owner_root))
            if not action:
                continue
            key = action["name"]
            if key in seen:
                continue
            seen.add(key)
            if action["raw"].get("disabled") is True and not _include_disabled:
                continue
            parts = actions_dir.relative_to(owner_root).parts
            scope = "global" if ".cuttle_global" in parts else "project"
            if scope == "project" and "source" in actions_dir.relative_to(owner_root).parts:
                scope += "-nested"
            if "personal" in parts:
                scope += "-personal"
            action["source"] = scope
            action["ref"] = f"{scope}/{key}"
            out.append(action)
    out.sort(key=lambda a: (a.get("title") or a.get("name") or "").lower())
    return out


def find_project_action(
    project_path: str, name: str, *, include_global: bool = True
) -> Optional[Dict[str, Any]]:
    want = str(name or "").strip()
    if not want:
        return None
    for action in list_project_actions(project_path, include_global=include_global):
        if action["name"] == want:
            return action
    return None


def find_project_action_resolved(
    project_path: str,
    name: str,
    *,
    channel: Optional[str] = None,
    repo: Optional[str] = None,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """
    Find an allowlisted action. Prefer ``project_path``; if missing there,
    search other registered Cuttle projects.

    When ``channel`` is set (Discord alias), prefer the project whose
    ``discord.post`` allowlist contains that alias — so a Cuttle-chip chat
    posting to one project's ``general`` does not hit another project's
    ``feature-updates`` allowlist.

    When ``repo`` is set (Gitea alias), prefer the project whose ``gitea.issue``
    allowlist contains that alias.
    """
    want = str(name or "").strip()
    if not want:
        return None, str(project_path or "")

    channel_key = str(channel or "").strip()
    repo_key = str(repo or "").strip()
    primary = str(project_path or "").strip()
    try:
        from core.runtime_paths import rewrite_windows_lab_path

        primary = rewrite_windows_lab_path(primary)
    except Exception:
        pass

    candidates: List[Tuple[Dict[str, Any], str]] = []
    seen_paths: set = set()

    def _add_candidate(action: Optional[Dict[str, Any]], path: str) -> None:
        if not action:
            return
        key = str(action.get("project_path") or path or "").strip().lower()
        if not key or key in seen_paths:
            return
        seen_paths.add(key)
        candidates.append((action, str(action.get("project_path") or path)))

    try:
        from api.cuttle_brain.global_layers import load_global_layers

        include_global = load_global_layers(primary or None).actions
    except Exception:
        include_global = True

    if primary:
        # A local tombstone is terminal, including the cross-project fallback.
        for effective in list_project_actions(primary, include_global=include_global, _include_disabled=True):
            if effective["name"] == want and effective["raw"].get("disabled") is True:
                return None, primary
        _add_candidate(find_project_action(primary, want, include_global=include_global), primary)

    paths: List[str] = []
    try:
        from managers.project_manager import project_manager
    except Exception:
        project_manager = None  # type: ignore

    if project_manager is not None:
        try:
            for p in project_manager.get_projects() or []:
                path = str((p or {}).get("path") or "").strip()
                if path and path not in paths and path != primary:
                    paths.append(path)
        except Exception:
            pass

    # Optional sibling checkouts from gitignored .cuttle_global/personal/path-aliases.json.
    try:
        from core.runtime_paths import (
            personal_sibling_project_paths,
            rewrite_windows_lab_path as _rewrite_lab,
        )
    except Exception:
        _rewrite_lab = lambda p: p  # noqa: E731
        personal_sibling_project_paths = lambda: []  # noqa: E731
    for sibling in personal_sibling_project_paths():
        mapped = _rewrite_lab(sibling)
        if mapped not in paths and mapped != primary:
            paths.append(mapped)

    # Cuttle install fallback. The global leg is cut per-path below when the
    # chat project's GLOBAL.ini severs it (include_global=False).
    try:
        install = str(Path(__file__).resolve().parents[2])
        if install not in paths and install != primary:
            paths.append(install)
    except Exception:
        pass

    for path in paths:
        _add_candidate(
            find_project_action(path, want, include_global=include_global), path
        )

    if not candidates:
        return None, primary

    if channel_key:
        matching = [
            (action, path)
            for action, path in candidates
            if channel_key in (action.get("channels") or {})
        ]
        if len(matching) == 1:
            return matching[0]
        if len(matching) > 1:
            # Prefer the chat's project if it matched; else first match.
            for action, path in matching:
                if primary and path.lower() == primary.lower():
                    return action, path
            return matching[0]
        # Channel not on any allowlist — keep searching so the error lists
        # aliases from a plausible project rather than silently picking EP.

    if repo_key:
        matching = [
            (action, path)
            for action, path in candidates
            if repo_key in (action.get("repos") or {})
        ]
        if len(matching) == 1:
            return matching[0]
        if len(matching) > 1:
            for action, path in matching:
                if primary and path.lower() == primary.lower():
                    return action, path
            return matching[0]

    # Primary project owns the action and no channel disambiguation needed.
    if primary:
        for action, path in candidates:
            if path.lower() == Path(primary).resolve().as_posix().lower() or path.lower() == primary.lower():
                if (
                    (not channel_key or channel_key in (action.get("channels") or {}))
                    and (not repo_key or repo_key in (action.get("repos") or {}))
                ):
                    return action, path
        # Primary path may be nested (…/Cuttle/src) while owner is …/Cuttle
        primary_found = find_project_action(
            primary, want, include_global=include_global
        )
        if primary_found and (
            (not channel_key or channel_key in (primary_found.get("channels") or {}))
            and (not repo_key or repo_key in (primary_found.get("repos") or {}))
        ):
            return primary_found, str(primary_found.get("project_path") or primary)

    if len(candidates) == 1:
        return candidates[0]

    if len(candidates) > 1 and not channel_key and not repo_key:
        # Optional install-local preference when no channel hint (see path-aliases.json).
        try:
            from core.runtime_paths import personal_string_list as _personal_list
        except Exception:
            _personal_list = lambda *_a, **_k: []  # noqa: E731
        prefer = [s.lower() for s in _personal_list("action_prefer_substrings_no_channel")]
        if prefer:
            for action, path in candidates:
                low = path.lower().replace("_", "-")
                if any(marker in low for marker in prefer):
                    return action, path
        return candidates[0]

    # Channel was set but unmatched — return a candidate so execute can
    # report that project's allowlisted aliases.
    try:
        from core.runtime_paths import personal_string_list as _personal_list
    except Exception:
        _personal_list = lambda *_a, **_k: []  # noqa: E731
    skip = [s.lower() for s in _personal_list("action_skip_substrings_unmatched_channel")]
    if skip:
        for action, path in candidates:
            low = path.lower().replace("_", "-")
            if not any(marker in low for marker in skip):
                return action, path
    return candidates[0]


def _parse_attrs(attr_str: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for m in _ATTR_RE.finditer(attr_str or ""):
        key = m.group(1).lower()
        val = m.group(2) if m.group(2) is not None else (m.group(3) or "")
        out[key] = val
    return out


def _purge_expired_locked(now: Optional[float] = None) -> None:
    t = now if now is not None else time.time()
    dead = [
        aid
        for aid, rec in _pending.items()
        if float(rec.get("expires_at") or 0) < t
    ]
    for aid in dead:
        _pending.pop(aid, None)


def register_pending_action(
    *,
    session_id: str,
    project_path: str,
    action_name: str,
    params: Dict[str, Any],
    confirm_label: str = "Confirm",
    cancel_label: str = "Cancel",
) -> str:
    """Store a pending action; return its id."""
    action_id = uuid.uuid4().hex
    now = time.time()
    with _pending_lock:
        _purge_expired_locked(now)
        _pending[action_id] = {
            "id": action_id,
            "session_id": str(session_id),
            "project_path": str(project_path or ""),
            "action": str(action_name),
            "params": dict(params or {}),
            "confirm_label": confirm_label,
            "cancel_label": cancel_label,
            "created_at": now,
            "expires_at": now + _PENDING_TTL_SEC,
        }
    return action_id


def get_pending_action(action_id: str) -> Optional[Dict[str, Any]]:
    with _pending_lock:
        _purge_expired_locked()
        rec = _pending.get(action_id)
        return dict(rec) if rec else None


def pop_pending_action(action_id: str) -> Optional[Dict[str, Any]]:
    with _pending_lock:
        _purge_expired_locked()
        rec = _pending.pop(action_id, None)
        return dict(rec) if rec else None


def clear_pending_for_tests() -> None:
    """Test helper."""
    with _pending_lock:
        _pending.clear()


def rewrite_cuttle_confirms(
    text: str,
    *,
    session_id: str,
    project_path: str,
) -> Tuple[str, int]:
    """
    Replace ``<cuttle_confirm>`` blocks with preview + Confirm/Cancel buttons.
    Registers one pending action per block. Returns (new_text, count).
    """
    if not text or "<cuttle_confirm" not in text.lower():
        return text, 0
    if not session_id:
        return text, 0

    count = 0

    def _repl(match: re.Match) -> str:
        nonlocal count
        attrs = _parse_attrs(match.group(1) or "")
        body = (match.group(2) or "").strip()
        action_name = (attrs.get("action") or attrs.get("name") or "").strip()
        if not action_name:
            return match.group(0)
        confirm_label = (attrs.get("confirm_label") or attrs.get("confirm") or "Confirm").strip()
        cancel_label = (attrs.get("cancel_label") or attrs.get("cancel") or "Cancel").strip()

        params: Dict[str, Any] = {"content": body}
        # Copy all other attrs into params (channel, etc.) except UI labels.
        for k, v in attrs.items():
            if k in ("action", "name", "confirm_label", "confirm", "cancel_label", "cancel"):
                continue
            params[k] = v

        action_id = register_pending_action(
            session_id=session_id,
            project_path=project_path or "",
            action_name=action_name,
            params=params,
            confirm_label=confirm_label,
            cancel_label=cancel_label,
        )
        count += 1

        title = attrs.get("title") or confirm_label
        # Escape quotes in attributes for the pending tag.
        def _esc(s: str) -> str:
            return (
                str(s)
                .replace("&", "&amp;")
                .replace('"', "&quot;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )

        preview = body if body else "_(empty)_"
        # Self-contained fallback so Confirm still works after Flask restart
        # (in-memory pending ids are wiped).
        inline_token = encode_inline_action_payload(
            action_name=action_name,
            project_path=project_path or "",
            params=params,
            session_id=str(session_id),
        )
        return (
            f'<cuttle_confirm_pending id="{action_id}" '
            f'fallback="{_esc(inline_token)}" '
            f'title="{_esc(title)}" '
            f'confirm_label="{_esc(confirm_label)}" '
            f'cancel_label="{_esc(cancel_label)}">\n'
            f"{preview}\n"
            f"</cuttle_confirm_pending>"
        )

    new_text = _CONFIRM_RE.sub(_repl, text)
    return new_text, count


def prepare_assistant_text_for_actions(
    text: str,
    *,
    session_id: Optional[str],
    project_path: Optional[str],
) -> str:
    """Rewrite confirms + action forms in an assistant reply when session is known."""
    if not text or not session_id:
        return text
    try:
        from api.cuttle_ui_capabilities import strip_cuttle_ui_capabilities

        text = strip_cuttle_ui_capabilities(text)
    except Exception:
        pass
    rewritten, _ = rewrite_cuttle_confirms(
        text,
        session_id=str(session_id),
        project_path=str(project_path or ""),
    )
    try:
        from api.action_forms import rewrite_action_forms

        rewritten, _ = rewrite_action_forms(
            rewritten,
            session_id=str(session_id),
            project_path=str(project_path or ""),
        )
    except Exception as e:
        print(f"[CHAT] action form rewrite failed: {e}", flush=True)
    return rewritten


def parse_project_action_button(message: str) -> Optional[Tuple[str, str]]:
    """
    If ``message`` is a project-action button click, return (kind, token).

    ``token`` is either a pending action id, or ``inline.<base64url-json>`` for
    self-contained confirms (survives Flask restart / missed rewrite).

    Also accepts ``[button:…] <pendingId> <inline.…>`` — prefers the inline
    token so Confirm still works after a Flask restart wiped pending memory.
    """
    stripped = (message or "").strip()
    m = re.match(
        r"^\[button:(project-action-(?:confirm|cancel))\](?:\s+(\S+)(?:\s+(\S+))?)?\s*$",
        stripped,
        flags=re.I,
    )
    if not m:
        return None
    kind_raw = m.group(1).lower()
    token_a = (m.group(2) or "").strip()
    token_b = (m.group(3) or "").strip()
    if not token_a:
        return None
    kind = "confirm" if kind_raw.endswith("confirm") else "cancel"
    # Prefer HMAC-valid inline when both are present. Unsigned client tokens
    # fall through to the pending id so history recovery can run.
    if token_b.lower().startswith("inline."):
        if decode_inline_action_payload(token_b):
            return kind, token_b
        return kind, token_a
    if token_a.lower().startswith("inline."):
        return kind, token_a
    return kind, token_a


def _b64url_encode(data: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(token: str) -> bytes:
    import base64

    raw = (token or "").strip()
    pad = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode(raw + pad)


def _action_hmac_secret() -> bytes:
    global _hmac_secret_cache
    if _hmac_secret_cache:
        return _hmac_secret_cache
    env = (os.getenv("CUTTLE_ACTION_HMAC_SECRET") or "").strip()
    if env:
        _hmac_secret_cache = env.encode("utf-8")
        return _hmac_secret_cache
    try:
        if _HMAC_SECRET_PATH.is_file():
            raw = _HMAC_SECRET_PATH.read_bytes().strip()
            if raw:
                _hmac_secret_cache = raw
                return _hmac_secret_cache
        _HMAC_SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
        raw = secrets.token_bytes(32)
        _HMAC_SECRET_PATH.write_bytes(raw)
        try:
            os.chmod(_HMAC_SECRET_PATH, 0o600)
        except OSError:
            pass
        _hmac_secret_cache = raw
        return raw
    except OSError as exc:
        raise RuntimeError(
            "Cuttle action HMAC secret is unavailable. Set CUTTLE_ACTION_HMAC_SECRET "
            f"or make {_HMAC_SECRET_PATH} writable."
        ) from exc


def _sign_action_body(body: Dict[str, Any]) -> str:
    canonical = json.dumps(body, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hmac.new(_action_hmac_secret(), canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def _verify_action_hmac(body: Dict[str, Any], sig: str) -> bool:
    try:
        expected = _sign_action_body(body)
    except RuntimeError:
        return False
    return hmac.compare_digest(expected, sig)


_FORM_SPEC_SIGN_KEYS = (
    "id",
    "session_id",
    "project_path",
    "mode",
    "options",
    "fields",
    "submit",
    "resume",
    "reusable",
    "watch",
    "lock",
    "silent",
    "title",
    "content",
    "restartFormGroup",
)


def action_form_spec_signing_body(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Stable subset of a form spec covered by HMAC (excludes lock/toast/sig)."""
    body: Dict[str, Any] = {"kind": "__action_form_spec__"}
    for key in _FORM_SPEC_SIGN_KEYS:
        if key in spec:
            body[key] = spec[key]
    return body


def sign_action_form_spec(spec: Dict[str, Any]) -> str:
    return _sign_action_body(action_form_spec_signing_body(spec))


def verify_action_form_spec(spec: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(spec, dict):
        return False
    sig = str(spec.get("sig") or "").strip()
    if not sig:
        return False
    return _verify_action_hmac(action_form_spec_signing_body(spec), sig)


def encode_inline_action_payload(
    *,
    action_name: str,
    project_path: str,
    params: Dict[str, Any],
    session_id: Optional[str] = None,
    ttl_seconds: int = _PENDING_TTL_SEC,
) -> str:
    """Signed confirm token: ``inline.<base64url(json+hmac+exp)>``."""
    now = int(time.time())
    body: Dict[str, Any] = {
        "action": str(action_name or ""),
        "project_path": str(project_path or ""),
        "params": dict(params or {}),
        "iat": now,
        "exp": now + max(60, int(ttl_seconds or _PENDING_TTL_SEC)),
    }
    if session_id:
        body["session_id"] = str(session_id)
    signed = dict(body)
    signed["sig"] = _sign_action_body(body)
    blob = json.dumps(signed, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return "inline." + _b64url_encode(blob)


def decode_inline_action_payload(token: str) -> Optional[Dict[str, Any]]:
    t = (token or "").strip()
    if not t.lower().startswith("inline."):
        return None
    try:
        data = json.loads(_b64url_decode(t[7:]).decode("utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    action = str(data.get("action") or "").strip()
    if not action:
        return None
    params = data.get("params") if isinstance(data.get("params"), dict) else {}
    allow_unsigned = (os.getenv("CUTTLE_ALLOW_UNSIGNED_ACTION_TOKENS") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    sig = str(data.get("sig") or "").strip()
    if not sig:
        if not allow_unsigned:
            return None
    else:
        body = {k: v for k, v in data.items() if k != "sig"}
        if not _verify_action_hmac(body, sig):
            return None
        exp = body.get("exp")
        try:
            if exp is not None and int(exp) < int(time.time()):
                return None
        except (TypeError, ValueError):
            return None
    out: Dict[str, Any] = {
        "action": action,
        "project_path": str(data.get("project_path") or ""),
        "params": dict(params),
    }
    if data.get("session_id"):
        out["session_id"] = str(data.get("session_id"))
    return out


def execute_inline_action(
    token: str,
    *,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Run a self-contained confirm payload (no pending registry)."""
    parsed = decode_inline_action_payload(token)
    if not parsed:
        return {
            "success": False,
            "response": "That confirmation payload was invalid.",
            "type": "project_action",
        }
    bound = parsed.get("session_id")
    if bound and session_id:
        from api.cuttle_ui_capabilities import numeric_chat_session_id

        a = numeric_chat_session_id(bound)
        b = numeric_chat_session_id(session_id)
        if a is not None and b is not None:
            if a != b:
                return {
                    "success": False,
                    "response": "That confirmation belongs to a different chat.",
                    "type": "project_action",
                }
        elif str(bound) != str(session_id):
            return {
                "success": False,
                "response": "That confirmation belongs to a different chat.",
                "type": "project_action",
            }
    project_path = parsed.get("project_path") or ""
    action_name = parsed.get("action") or ""
    params = parsed.get("params") or {}
    channel_hint = str(params.get("channel") or params.get("channel_id") or "").strip() or None
    repo_hint = str(params.get("repo") or params.get("repository") or "").strip() or None
    action, resolved_path = find_project_action_resolved(
        project_path, action_name, channel=channel_hint, repo=repo_hint
    )
    if not action:
        return {
            "success": False,
            "response": (
                f"Unknown project action `{action_name}`. "
                f"Add it under `.cuttle/actions/` for this project "
                f"(and set the chat project chip)."
            ),
            "type": "project_action",
        }
    # Keep shell cwd / discord allowlist from the project that owns the action.
    if resolved_path and not action.get("project_path"):
        action = dict(action)
        action["project_path"] = resolved_path
    action_type = str(action.get("type") or "").strip().lower()
    if action_type in ("discord.post", "discord_post", "discord"):
        result = _execute_discord_post(action, params)
    elif action_type in ("gitea.issue", "gitea_issue", "gitea"):
        result = _execute_gitea_issue(action, params)
    elif action_type in ("shell", "run", "script") or action.get("run") or action.get("run_posix"):
        result = _execute_shell(action, params, session_id=session_id)
    else:
        return {
            "success": False,
            "response": f"Unsupported action type `{action_type or '(none)'}` for `{action_name}`.",
            "type": "project_action",
        }
    if result.get("success"):
        return {
            "success": True,
            "response": result.get("response") or "Done.",
            "type": "project_action",
            "action": action_name,
            "url": result.get("url"),
        }
    return {
        "success": False,
        "response": f"**Action failed** (`{action_name}`)\n\n{result.get('error') or 'Unknown error'}",
        "type": "project_action",
        "action": action_name,
    }


def _load_discord_bot_token() -> Optional[str]:
    """Re-export for tests that patch this name."""
    from api.discord_ops.token import load_discord_bot_token

    return load_discord_bot_token()


def _execute_discord_post(action: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
    from api.discord_ops.post import execute_discord_post

    return execute_discord_post(action, params)


def _parse_label_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    text = str(value).strip()
    if not text:
        return []
    if text.startswith("[") and text.endswith("]"):
        try:
            loaded = json.loads(text)
            if isinstance(loaded, list):
                return [str(x).strip() for x in loaded if str(x).strip()]
        except Exception:
            pass
    return [p.strip() for p in text.split(",") if p.strip()]


def _resolve_gitea_owner_repo(action: Dict[str, Any], params: Dict[str, Any]) -> Tuple[str, str]:
    from api.gitea_client import parse_owner_repo

    repo_key = str(params.get("repo") or params.get("repository") or "").strip()
    repos = action.get("repos") or {}
    spec = repos.get(repo_key) or repo_key
    if not spec:
        allowed = ", ".join(sorted(repos.keys())) or "(none configured)"
        raise ValueError(
            f"Unknown Gitea repo `{repo_key or '(missing)'}`. "
            f"Allowlisted aliases: {allowed} — or pass owner/repo."
        )
    return parse_owner_repo(spec)


def _execute_gitea_issue(action: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
    from api.gitea_client import (
        add_issue_comment,
        add_issue_labels,
        get_issue,
        issue_web_url,
        replace_issue_labels,
    )

    try:
        owner, repo = _resolve_gitea_owner_repo(action, params)
    except ValueError as e:
        return {"success": False, "error": str(e)}

    issue_raw = params.get("issue") or params.get("number") or params.get("index")
    try:
        issue_index = int(issue_raw)
    except (TypeError, ValueError):
        return {
            "success": False,
            "error": f"Invalid issue number `{issue_raw or '(missing)'}`.",
        }
    if issue_index <= 0:
        return {"success": False, "error": "Issue number must be positive."}

    comment = str(params.get("content") or params.get("comment") or params.get("body") or "").strip()
    labels_replace = _parse_label_list(params.get("labels"))
    labels_add = _parse_label_list(params.get("labels_add") or params.get("add_labels"))
    labels_remove = _parse_label_list(params.get("labels_remove") or params.get("remove_labels"))
    assign_self = str(params.get("assign_self") or params.get("assign-self") or "").lower() in (
        "1",
        "true",
        "yes",
    )
    assign_to = str(params.get("assign") or params.get("assignee") or "").strip()
    if assign_self and not assign_to:
        from api.gitea_client import default_agent_username

        assign_to = default_agent_username()

    if not comment and not labels_replace and not labels_add and not labels_remove and not assign_to:
        return {
            "success": False,
            "error": "Nothing to do — provide a comment body, assignee, and/or label params.",
        }

    steps: List[str] = []
    try:
        if comment:
            add_issue_comment(owner, repo, issue_index, comment)
            steps.append("comment posted")

        if labels_replace:
            replace_issue_labels(owner, repo, issue_index, labels_replace)
            steps.append(f"labels set to {', '.join(labels_replace)}")

        if labels_add:
            add_issue_labels(owner, repo, issue_index, labels_add)
            steps.append(f"labels added: {', '.join(labels_add)}")

        if labels_remove:
            issue = get_issue(owner, repo, issue_index)
            name_to_id = {
                str(lb.get("name") or ""): int(lb.get("id") or 0)
                for lb in (issue.get("labels") or [])
            }
            from api.gitea_client import delete_issue_label

            for name in labels_remove:
                lid = name_to_id.get(name)
                if lid:
                    delete_issue_label(owner, repo, issue_index, lid)
            steps.append(f"labels removed: {', '.join(labels_remove)}")

        if assign_to:
            from api.gitea_client import patch_issue

            patch_issue(owner, repo, issue_index, assignees=[assign_to])
            steps.append(f"assigned to {assign_to}")

        url = issue_web_url(owner, repo, issue_index)
        return {
            "success": True,
            "response": (
                f"**Gitea issue updated** (`{owner}/{repo}#{issue_index}`)\n\n"
                f"{', '.join(steps)}\n\n{url}"
            ),
            "url": url,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


_ENV_KEY_RE = re.compile(r"[^A-Z0-9_]")


def _param_env_vars(params: Dict[str, Any]) -> Dict[str, str]:
    """Expose scalar action params as ``CUTTLE_PARAM_<KEY>`` for shell recipes."""
    out: Dict[str, str] = {}
    for key, value in (params or {}).items():
        name = _ENV_KEY_RE.sub("_", str(key).strip().upper()).strip("_")
        if not name or name == "CONTENT":
            continue
        if isinstance(value, bool):
            text = "true" if value else "false"
        elif isinstance(value, (str, int, float)):
            text = str(value)
        else:
            continue
        out[f"CUTTLE_PARAM_{name}"] = text
    return out


_PS_LAUNCH_RE = re.compile(
    r"^(?:pwsh|powershell)(?:\.exe)?\b",
    re.IGNORECASE,
)
_PS_FILE_RE = re.compile(
    r"-File\s+(?:\"([^\"]+)\"|'([^']+)'|(\S+))",
    re.IGNORECASE,
)


def _quote_shell(part: str) -> str:
    if not part:
        return '""'
    if re.search(r"[\s\"'\\$]", part):
        return "'" + part.replace("'", "'\\''") + "'"
    return part


def rewrite_posix_shell_recipe(recipe: str, project_root: Path, python_bin: Path) -> str:
    """Map Windows action recipes onto POSIX (venv python, .ps1 → .py/.sh)."""
    text = (recipe or "").strip()
    if not text:
        return text
    py = str(python_bin)
    text = text.replace(".venv\\Scripts\\python.exe", py)
    text = text.replace(".venv/Scripts/python.exe", py)
    text = text.replace(".venv\\Scripts\\python", py)
    if text.startswith(".venv/bin/python"):
        rest = text[len(".venv/bin/python") :]
        if rest.startswith("3"):
            rest = rest[1:]
        text = _quote_shell(py) + rest

    if _PS_LAUNCH_RE.match(text):
        m = _PS_FILE_RE.search(text)
        if not m:
            return text
        script = (m.group(1) or m.group(2) or m.group(3) or "").replace("\\", "/")
        rest = text[m.end() :].strip()
        name = Path(script).name.lower()
        if name == "workers-cli.ps1":
            cmd = f"{_quote_shell(py)} -m api.device_workers.cli"
            return f"{cmd} {rest}".strip()
        rel = Path(script)
        for ext in (".py", ".sh"):
            cand = (project_root / rel).with_suffix(ext)
            if cand.is_file():
                if ext == ".py":
                    return f"{_quote_shell(py)} {_quote_shell(str(cand))} {rest}".strip()
                return f"bash {_quote_shell(str(cand))} {rest}".strip()
        posix_script = (project_root / rel).as_posix()
        return f"bash {_quote_shell(posix_script)} {rest}".strip()

    return text.replace("\\", "/")


def resolve_action_run(action: Dict[str, Any]) -> Optional[str]:
    """Pick run_windows / run_posix when present; rewrite PowerShell recipes on POSIX."""
    raw = action.get("raw") if isinstance(action.get("raw"), dict) else {}
    root = Path(action.get("project_path") or ".").resolve()
    try:
        from core.runtime_paths import is_windows, venv_python

        windows = is_windows()
        python_bin = venv_python(root)
    except Exception:
        windows = os.name == "nt"
        python_bin = Path(sys.executable)

    def _recipe(val: Any) -> Optional[str]:
        if val is None:
            return None
        s = str(val).strip()
        return s or None

    if windows:
        return _recipe(raw.get("run_windows")) or _recipe(action.get("run"))
    chosen = (
        _recipe(raw.get("run_posix"))
        or _recipe(raw.get("run_linux"))
        or _recipe(raw.get("run_unix"))
        or _recipe(action.get("run"))
    )
    if not chosen:
        return None
    return rewrite_posix_shell_recipe(chosen, root, python_bin)


def _execute_shell(
    action: Dict[str, Any],
    params: Dict[str, Any],
    *,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    recipe = resolve_action_run(action)
    if not recipe:
        return {"success": False, "error": f"Action `{action.get('name')}` has no run: recipe"}
    root = Path(action.get("project_path") or ".").resolve()
    wd = action.get("workdir")
    cwd = root / wd if wd and not Path(wd).is_absolute() else (Path(wd) if wd else root)
    cwd = cwd.resolve()
    if not cwd.is_dir():
        return {"success": False, "error": f"workdir not found: {cwd}"}

    env = os.environ.copy()
    src = str(root / "src")
    existing_pp = env.get("PYTHONPATH") or ""
    env["PYTHONPATH"] = src if not existing_pp else src + os.pathsep + existing_pp
    env["CUTTLE_ACTION"] = str(action.get("name") or "")
    env["CUTTLE_ACTION_CONTENT"] = str(params.get("content") or "")
    env["CUTTLE_ACTION_PARAMS_JSON"] = json.dumps(params, ensure_ascii=False)
    env["CUTTLE_PROJECT_PATH"] = str(root)
    env.update(_param_env_vars(params))
    if session_id:
        env["CUTTLE_SESSION_ID"] = str(session_id)
    try:
        from api.auth_session import get_request_session_token

        token = get_request_session_token()
        if token:
            env["CUTTLE_SESSION_TOKEN"] = str(token)
    except Exception:
        pass

    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        proc = subprocess.run(
            str(recipe),
            cwd=str(cwd),
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=int(action.get("raw", {}).get("timeout") or 300),
            env=env,
            creationflags=creationflags,
            input=str(params.get("content") or ""),
        )
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Action timed out"}
    except Exception as e:
        return {"success": False, "error": str(e)}

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    combined = out
    if err:
        combined = (combined + ("\n\n" if combined else "") + err).strip()
    if proc.returncode != 0:
        return {
            "success": False,
            "error": combined or f"exit {proc.returncode}",
            "exit_code": proc.returncode,
        }
    return {
        "success": True,
        "response": combined or f"**{action.get('title') or action.get('name')}** completed.",
        "exit_code": 0,
    }


def execute_pending_action(
    action_id: str,
    *,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Pop and run a pending action. Returns a chat-shaped result dict."""
    rec = pop_pending_action(action_id)
    if not rec:
        return {
            "success": False,
            "response": "That confirmation expired or was already used.",
            "type": "project_action",
        }
    if session_id and str(rec.get("session_id")) != str(session_id):
        # Put it back if session mismatch (don't steal another chat's action).
        with _pending_lock:
            _pending[action_id] = rec
        return {
            "success": False,
            "response": "That confirmation belongs to a different chat session.",
            "type": "project_action",
        }

    project_path = rec.get("project_path") or ""
    action_name = rec.get("action") or ""
    params = rec.get("params") or {}
    channel_hint = str(params.get("channel") or params.get("channel_id") or "").strip() or None
    repo_hint = str(params.get("repo") or params.get("repository") or "").strip() or None
    action, resolved_path = find_project_action_resolved(
        project_path, action_name, channel=channel_hint, repo=repo_hint
    )
    if not action:
        # Builtin discord.post can still run if channels were embedded in params
        # but we require a project action file for allowlisting.
        return {
            "success": False,
            "response": (
                f"Unknown project action `{action_name}`. "
                f"Add it under `.cuttle/actions/` for this project."
            ),
            "type": "project_action",
        }
    if resolved_path:
        action = dict(action)
        action["project_path"] = resolved_path

    effective_session = str(session_id or rec.get("session_id") or "")
    action_type = str(action.get("type") or "").strip().lower()
    if action_type in ("discord.post", "discord_post", "discord"):
        result = _execute_discord_post(action, params)
    elif action_type in ("gitea.issue", "gitea_issue", "gitea"):
        result = _execute_gitea_issue(action, params)
    elif action_type in ("shell", "run", "script"):
        result = _execute_shell(action, params, session_id=effective_session)
    else:
        # Default: if run: is set, treat as shell; else unknown.
        if action.get("run") or action.get("run_posix"):
            result = _execute_shell(action, params, session_id=effective_session)
        else:
            return {
                "success": False,
                "response": f"Unsupported action type `{action_type or '(none)'}` for `{action_name}`.",
                "type": "project_action",
            }

    if result.get("success"):
        return {
            "success": True,
            "response": result.get("response") or "Done.",
            "type": "project_action",
            "action": action_name,
            "url": result.get("url"),
        }
    return {
        "success": False,
        "response": f"**Action failed** (`{action_name}`)\n\n{result.get('error') or 'Unknown error'}",
        "type": "project_action",
        "action": action_name,
    }


def cancel_pending_action(action_id: str, *, session_id: Optional[str] = None) -> Dict[str, Any]:
    rec = get_pending_action(action_id)
    if not rec:
        return {
            "success": True,
            "response": "Okay — nothing left to cancel (already used or expired).",
            "type": "project_action",
        }
    if session_id and str(rec.get("session_id")) != str(session_id):
        return {
            "success": False,
            "response": "That confirmation belongs to a different chat session.",
            "type": "project_action",
        }
    pop_pending_action(action_id)
    label = rec.get("confirm_label") or rec.get("action") or "action"
    return {
        "success": True,
        "response": f"Cancelled — **{label}** was not run.",
        "type": "project_action",
    }


def load_confirm_inline_from_history(
    session_id: Optional[str],
    pending_id: Optional[str],
) -> Optional[str]:
    """HMAC ``fallback=`` token from a persisted ``cuttle_confirm_pending`` tag."""
    from api.cuttle_ui_capabilities import numeric_chat_session_id

    nid = numeric_chat_session_id(session_id)
    pid = str(pending_id or "").strip()
    if not nid or not pid:
        return None
    try:
        from api.auth_db import get_auth_db

        msgs = get_auth_db().find_messages_containing(
            int(nid), pid, role="assistant", limit=20
        )
    except Exception:
        return None
    id_re = re.compile(r'\bid=(["\'])([^"\']+)\1', re.I)
    fb_re = re.compile(r'\bfallback=(["\'])([^"\']*)\1', re.I)
    for msg in msgs or []:
        content = str(msg.get("content") or "")
        for m in re.finditer(r"<cuttle_confirm_pending\b([^>]*)>", content, re.I):
            attrs = m.group(1) or ""
            id_m = id_re.search(attrs)
            if not id_m or id_m.group(2) != pid:
                continue
            fb_m = fb_re.search(attrs)
            if not fb_m:
                continue
            token = html.unescape(fb_m.group(2) or "").strip()
            if decode_inline_action_payload(token):
                return token
    return None


def handle_project_action_button(
    message: str,
    *,
    session_id: Optional[str],
) -> Optional[Dict[str, Any]]:
    """
    If message is a project-action Confirm/Cancel click, handle it and return
    a chat response dict. Otherwise return None.
    """
    parsed = parse_project_action_button(message)
    if not parsed:
        return None
    kind, token = parsed
    if token.lower().startswith("inline."):
        if kind == "cancel":
            return {
                "success": True,
                "response": "Cancelled — action was not run.",
                "type": "project_action",
            }
        return execute_inline_action(token, session_id=session_id)

    rec = get_pending_action(token)
    if rec:
        if kind == "cancel":
            return cancel_pending_action(token, session_id=session_id)
        return execute_pending_action(token, session_id=session_id)

    hist = load_confirm_inline_from_history(session_id, token)
    if hist:
        if kind == "cancel":
            return {
                "success": True,
                "response": "Cancelled — action was not run.",
                "type": "project_action",
            }
        return execute_inline_action(hist, session_id=session_id)

    if kind == "cancel":
        return cancel_pending_action(token, session_id=session_id)
    return execute_pending_action(token, session_id=session_id)

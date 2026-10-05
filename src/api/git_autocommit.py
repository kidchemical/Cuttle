"""Auto-commit agent edits after each successful harness turn (opt-in).

Settings key ``git_auto_commit`` (bool, default **off**; Settings → Agents →
Git). When on, the kernel hands each successful, non-cancelled turn here and
Cuttle commits **only the files that turn changed** — the paths edit
attribution observed for that run's ``query_id`` — through the same
``commit_pending_changes`` path as the pending-changes panel (bot identity,
``Cuttle-Attributed`` trailers, secret-file guard). Other dirty files in the
tree are never swept in. It never pushes.

Runs on a background thread so the reply is not delayed by message
suggestion or ``git commit``. Failures are logged and toasted, never raised.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, Optional

SETTING_KEY = "git_auto_commit"

# One commit at a time per repo: concurrent turns would race the git index.
_repo_locks: Dict[str, threading.Lock] = {}
_repo_locks_guard = threading.Lock()


def is_enabled() -> bool:
    try:
        from managers.settings_manager import get_settings_manager

        return bool(get_settings_manager().get_setting(SETTING_KEY, False))
    except Exception:
        return False


def set_enabled(enabled: bool) -> bool:
    from managers.settings_manager import get_settings_manager

    return bool(get_settings_manager().set_setting(SETTING_KEY, bool(enabled)))


def _repo_lock(root: str) -> threading.Lock:
    with _repo_locks_guard:
        lock = _repo_locks.get(root)
        if lock is None:
            lock = _repo_locks[root] = threading.Lock()
        return lock


def _notify(chat_session_id: Optional[str], message: str, variant: str) -> None:
    if not chat_session_id:
        return
    try:
        from api.chat_vfx import toast

        toast(chat_session_id, message, variant)
    except Exception:
        pass


def commit_turn(
    *,
    cwd: str,
    query_id: Optional[str],
    chat_session_id: Optional[str] = None,
    prompt: str = "",
) -> Dict[str, Any]:
    """Commit the files this turn changed. Returns a status dict (never raises)."""
    try:
        from api.commit_message_suggester import suggest_commit_message
        from api.edit_attribution.journal import open_paths_for_query
        from scripts.utilities.git_pending_changes import (
            collect_commit_suggest_context,
            commit_pending_changes,
            resolve_git_workdir,
        )
    except Exception as exc:
        return {"committed": False, "reason": f"unavailable: {exc}"}

    root = resolve_git_workdir(cwd or "") if cwd else None
    if not root or not query_id:
        return {"committed": False, "reason": "not a git repo"}
    with _repo_lock(root):
        paths = open_paths_for_query(root, query_id)
        if not paths:
            return {"committed": False, "reason": "turn changed no files"}
        try:
            ctx = collect_commit_suggest_context(root, paths=paths)
            suggestion = suggest_commit_message(
                ctx, user_prompts=[prompt] if prompt.strip() else [], inference_mode="auto"
            )
            message = (suggestion.get("message") or suggestion.get("heuristic_message") or "").strip()
            if not message:
                message = f"Agent edits ({len(paths)} file{'s' if len(paths) != 1 else ''})"
            result = commit_pending_changes(root, message, paths=paths)
        except ValueError as exc:
            # Nothing left to commit (user already did), or a secret file.
            reason = str(exc)
            if "Nothing to commit" not in reason:
                _notify(chat_session_id, f"Auto-commit skipped: {reason}", "warning")
            return {"committed": False, "reason": reason, "paths": paths}
        except Exception as exc:
            print(f"[git_autocommit] commit failed in {root}: {exc}", flush=True)
            _notify(chat_session_id, f"Auto-commit failed: {exc}", "error")
            return {"committed": False, "reason": str(exc), "paths": paths}
    sha = str(result.get("commit") or "")[:8]
    n = len(result.get("files_committed") or paths)
    _notify(
        chat_session_id,
        f"Auto-committed {sha} · {n} file{'s' if n != 1 else ''} (not pushed)",
        "success",
    )
    return {"committed": True, "commit": sha, "paths": paths, "message": message}


def schedule_after_turn(
    *,
    cwd: str,
    query_id: Optional[str],
    chat_session_id: Optional[str] = None,
    prompt: str = "",
) -> bool:
    """Kernel hook: start a background commit when the setting is on."""
    if not is_enabled() or not query_id or not cwd:
        return False
    threading.Thread(
        target=commit_turn,
        kwargs={
            "cwd": cwd,
            "query_id": query_id,
            "chat_session_id": chat_session_id,
            "prompt": prompt,
        },
        name=f"git-autocommit-{query_id}",
        daemon=True,
    ).start()
    return True

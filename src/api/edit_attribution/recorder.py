"""Record harness-run worktree deltas into the edit attribution journal."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _resolve_repo_root(cwd: str) -> Optional[str]:
    try:
        from scripts.utilities.git_pending_changes import resolve_git_workdir

        return resolve_git_workdir(cwd)
    except Exception:
        try:
            from utilities.git_pending_changes import resolve_git_workdir  # type: ignore

            return resolve_git_workdir(cwd)
        except Exception:
            return None


def snapshot_for_attribution(cwd: str) -> Optional[Dict[str, Any]]:
    """Best-effort worktree snapshot; None if cwd is not a usable git repo."""
    root = _resolve_repo_root(cwd or "")
    if not root:
        return None
    try:
        from api.agent_router.supervised.evidence import snapshot_worktree

        snap = snapshot_worktree(root)
        if not snap.get("ok"):
            return None
        snap["_repo_root"] = root
        return snap
    except Exception as exc:
        print(f"[edit_attribution] snapshot failed: {exc}", flush=True)
        return None


def _model_for_run(
    *,
    agent_model: Optional[str],
    result_model: Optional[str],
    result_meta: Optional[Dict[str, Any]],
) -> str:
    meta = result_meta if isinstance(result_meta, dict) else {}
    cursor_run = meta.get("cursor_run") if isinstance(meta.get("cursor_run"), dict) else {}
    for candidate in (
        cursor_run.get("reported_model"),
        cursor_run.get("requested_model"),
        result_model,
        agent_model,
        meta.get("preferred_model"),
        meta.get("agent_model"),
    ):
        s = str(candidate or "").strip()
        if s:
            return s
    return "unknown"


def record_run_deltas(
    baseline: Optional[Dict[str, Any]],
    *,
    cwd: str,
    agent_id: str,
    agent_model: Optional[str] = None,
    result_model: Optional[str] = None,
    result_meta: Optional[Dict[str, Any]] = None,
    query_id: Optional[str] = None,
    chat_session_id: Optional[str] = None,
) -> int:
    """Compare end snapshot to baseline; append proven path events. Returns count."""
    if not baseline or not baseline.get("ok"):
        return 0
    repo_root = str(baseline.get("_repo_root") or baseline.get("root") or "").strip()
    if not repo_root:
        repo_root = _resolve_repo_root(cwd) or ""
    if not repo_root:
        return 0

    try:
        from api.agent_router.supervised.evidence import (
            compare_worktree_snapshots,
            snapshot_worktree,
        )
        from api.edit_attribution.journal import append_events, normalize_rel_path

        ending = snapshot_worktree(repo_root)
        if not ending.get("ok"):
            return 0
        delta = compare_worktree_snapshots(baseline, ending)
        caused = list(delta.get("worker_caused_paths") or [])
        if not caused:
            return 0

        base_paths = dict(baseline.get("paths") or {})
        end_paths = dict(ending.get("paths") or {})
        model = _model_for_run(
            agent_model=agent_model,
            result_model=result_model,
            result_meta=result_meta,
        )
        aid = str(agent_id or "").strip() or "unknown"
        events: List[Dict[str, Any]] = []
        for rel in caused:
            nr = normalize_rel_path(rel)
            if not nr:
                continue
            b = base_paths.get(rel) or base_paths.get(nr) or {}
            e = end_paths.get(rel) or end_paths.get(nr) or {}
            events.append(
                {
                    "repo_root": repo_root,
                    "rel_path": nr,
                    "agent_id": aid,
                    "model": model,
                    "query_id": query_id,
                    "chat_session_id": chat_session_id,
                    "digest_before": b.get("digest"),
                    "digest_after": e.get("digest"),
                }
            )
        return append_events(events)
    except Exception as exc:
        print(f"[edit_attribution] record failed: {exc}", flush=True)
        return 0

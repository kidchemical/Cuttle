"""Independent programmatic evidence for supervised review (no model calls).

Worker output is untrusted. This module collects narrowly allowlisted checks
and labels each finding with an evidence source.

Worktree attribution uses a task-start baseline vs task-end snapshot so
pre-existing dirty paths are not mistaken for worker-caused changes.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

EVIDENCE_SOURCES = frozenset(
    {
        "worker_claimed",
        "cuttle_verified",
        "coordinator_inferred",
        "not_verified",
    }
)

_TRAVERSAL_RE = re.compile(r"(^|[\\/])\.\.([\\/]|$)")


@dataclass
class EvidenceItem:
    key: str
    source: str  # worker_claimed | cuttle_verified | coordinator_inferred | not_verified
    detail: str
    ok: Optional[bool] = None  # None = uncertain

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EvidenceBundle:
    items: List[EvidenceItem] = field(default_factory=list)
    workspace: str = ""
    collected_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workspace": self.workspace,
            "collected_at": self.collected_at,
            "items": [i.to_dict() for i in self.items],
        }

    def summary_lines(self) -> List[str]:
        lines = ["**Independent evidence** (programmatic — not model judgment)"]
        if not self.items:
            lines.append("- _(no programmatic checks collected)_")
            return lines
        for item in self.items:
            flag = (
                "yes"
                if item.ok is True
                else ("no" if item.ok is False else "uncertain")
            )
            lines.append(
                f"- `{item.key}` · source=`{item.source}` · {flag} — {item.detail}"
            )
        return lines


def normalize_workspace_path(workspace: str, relative: str) -> Optional[Path]:
    """Resolve a workspace-relative path; reject traversal / escape."""
    rel = (relative or "").strip().replace("\\", "/")
    if not rel or rel.startswith("/") or re.match(r"^[A-Za-z]:/", rel):
        return None
    if _TRAVERSAL_RE.search(rel) or "\x00" in rel:
        return None
    root = Path(workspace).resolve()
    try:
        candidate = (root / rel).resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _run_git(workspace: str, *args: str, timeout: float = 15.0) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        out = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
        return int(proc.returncode), out.strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def _file_content_digest(path: Path, *, limit: int = 2_000_000) -> Optional[str]:
    try:
        if not path.is_file():
            return None
        h = hashlib.sha256()
        with path.open("rb") as f:
            remaining = limit
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                h.update(chunk)
                remaining -= len(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _parse_porcelain_paths(status_out: str) -> Dict[str, str]:
    """Map relative path -> porcelain XY status code."""
    out: Dict[str, str] = {}
    for ln in (status_out or "").splitlines():
        if not ln.strip():
            continue
        # porcelain v1: XY PATH or XY ORIG -> PATH
        code = ln[:2] if len(ln) >= 2 else "??"
        rest = ln[3:] if len(ln) > 3 else ln.strip()
        if " -> " in rest:
            rest = rest.split(" -> ", 1)[-1]
        path = rest.strip().strip('"')
        if path:
            out[path.replace("\\", "/")] = code
    return out


def snapshot_worktree(workspace: str) -> Dict[str, Any]:
    """Read-only worktree snapshot (paths + content digests for dirty files)."""
    from datetime import datetime, timezone

    ws = (workspace or "").strip()
    snap: Dict[str, Any] = {
        "workspace": ws,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "ok": False,
        "paths": {},
        "path_count": 0,
        "error": "",
    }
    if not ws or not Path(ws).is_dir():
        snap["error"] = "workspace missing"
        return snap
    root = Path(ws).resolve()
    rc, status_out = _run_git(ws, "status", "--porcelain")
    if rc != 0:
        snap["error"] = status_out[:200]
        return snap
    paths = _parse_porcelain_paths(status_out)
    digests: Dict[str, Any] = {}
    for rel, code in paths.items():
        entry: Dict[str, Any] = {"status": code, "digest": None}
        cand = normalize_workspace_path(ws, rel)
        if cand is not None and cand.is_file():
            entry["digest"] = _file_content_digest(cand)
        elif cand is not None and cand.exists():
            entry["digest"] = f"dir:{cand.stat().st_mtime_ns}"
        digests[rel] = entry
    snap["ok"] = True
    snap["paths"] = digests
    snap["path_count"] = len(digests)
    snap["root"] = str(root)
    return snap


def compare_worktree_snapshots(
    baseline: Optional[Dict[str, Any]],
    ending: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Attribute dirty paths relative to a task-start baseline.

    Concurrent edits by other processes cannot be proven; label uncertainty.
    """
    base_paths = dict((baseline or {}).get("paths") or {})
    end_paths = dict((ending or {}).get("paths") or {})
    pre_existing = sorted(base_paths.keys())
    new_paths = sorted(p for p in end_paths if p not in base_paths)
    disappeared = sorted(p for p in base_paths if p not in end_paths)
    further_changed: List[str] = []
    unchanged_dirty: List[str] = []
    for p in sorted(set(base_paths) & set(end_paths)):
        b = base_paths.get(p) or {}
        e = end_paths.get(p) or {}
        if b.get("digest") and e.get("digest") and b.get("digest") != e.get("digest"):
            further_changed.append(p)
        elif b.get("status") != e.get("status"):
            further_changed.append(p)
        else:
            unchanged_dirty.append(p)

    worker_caused_observed = sorted(set(new_paths) | set(further_changed) | set(disappeared))
    summary = {
        "pre_existing_dirty_paths": len(pre_existing),
        "new_paths_during_task": len(new_paths),
        "existing_paths_further_changed": len(further_changed),
        "disappeared_paths": len(disappeared),
        "unchanged_pre_existing_dirty": len(unchanged_dirty),
        "worker_caused_changes_observed": len(worker_caused_observed),
        "concurrent_attribution": "not_provable",
        # Full path lists for edit-attribution journal (samples below stay capped).
        "worker_caused_paths": worker_caused_observed,
        "new_paths": new_paths[:40],
        "further_changed": further_changed[:40],
        "disappeared": disappeared[:40],
        "pre_existing_sample": pre_existing[:40],
    }
    lines = [
        f"Pre-existing dirty paths: {summary['pre_existing_dirty_paths']}",
        f"New paths during task: {summary['new_paths_during_task']}",
        f"Existing paths further changed during task: {summary['existing_paths_further_changed']}",
        (
            "Worker-caused changes: none observed"
            if not worker_caused_observed
            else f"Worker-caused changes observed: {len(worker_caused_observed)}"
        ),
        "Concurrent attribution: not provable",
    ]
    summary["summary_lines"] = lines
    summary["summary_text"] = "\n".join(lines)
    return summary


def collect_programmatic_evidence(
    *,
    workspace: str,
    worker_report: Any = None,
    worker_result: Optional[Dict[str, Any]] = None,
    acceptance_criteria: Optional[Sequence[str]] = None,
    baseline_snapshot: Optional[Dict[str, Any]] = None,
) -> EvidenceBundle:
    """Collect safe, allowlisted evidence. Never executes worker-supplied commands."""
    from datetime import datetime, timezone

    bundle = EvidenceBundle(
        workspace=workspace or "",
        collected_at=datetime.now(timezone.utc).isoformat(),
    )

    # Worker claims (untrusted labels)
    if worker_report is not None:
        claimed_files = list(getattr(worker_report, "files_changed", None) or [])
        if claimed_files:
            bundle.items.append(
                EvidenceItem(
                    key="worker_files_changed",
                    source="worker_claimed",
                    detail=", ".join(str(f) for f in claimed_files[:20]),
                    ok=None,
                )
            )
        if getattr(worker_report, "acceptance_satisfied", None) is True:
            bundle.items.append(
                EvidenceItem(
                    key="worker_acceptance_claim",
                    source="worker_claimed",
                    detail="Worker claims acceptance criteria satisfied",
                    ok=None,
                )
            )
        outcome = str(getattr(worker_report, "outcome", "") or "")
        if outcome:
            bundle.items.append(
                EvidenceItem(
                    key="worker_outcome_claim",
                    source="worker_claimed",
                    detail=f"outcome={outcome}",
                    ok=None,
                )
            )

    # Process / runner result (host-observed)
    if isinstance(worker_result, dict):
        success = worker_result.get("success")
        if success is not None:
            bundle.items.append(
                EvidenceItem(
                    key="worker_process_success",
                    source="cuttle_verified",
                    detail=f"runner success={bool(success)}",
                    ok=bool(success),
                )
            )
        err = worker_result.get("error")
        if err:
            bundle.items.append(
                EvidenceItem(
                    key="worker_process_error",
                    source="cuttle_verified",
                    detail=str(err)[:300],
                    ok=False,
                )
            )

    ws = (workspace or "").strip()
    if not ws or not Path(ws).is_dir():
        bundle.items.append(
            EvidenceItem(
                key="workspace",
                source="not_verified",
                detail="Workspace missing or not a directory — git checks skipped",
                ok=None,
            )
        )
        return bundle

    end_snap = snapshot_worktree(ws)
    if baseline_snapshot:
        delta = compare_worktree_snapshots(baseline_snapshot, end_snap)
        bundle.items.append(
            EvidenceItem(
                key="worktree_task_delta",
                source="cuttle_verified",
                detail=delta.get("summary_text") or "",
                ok=True,
            )
        )
        bundle.items.append(
            EvidenceItem(
                key="worktree_delta_detail",
                source="cuttle_verified",
                detail=str(
                    {
                        "pre_existing_dirty_paths": delta.get("pre_existing_dirty_paths"),
                        "new_paths_during_task": delta.get("new_paths_during_task"),
                        "existing_paths_further_changed": delta.get(
                            "existing_paths_further_changed"
                        ),
                        "worker_caused_changes_observed": delta.get(
                            "worker_caused_changes_observed"
                        ),
                        "concurrent_attribution": delta.get("concurrent_attribution"),
                        "new_paths": delta.get("new_paths"),
                        "further_changed": delta.get("further_changed"),
                    }
                )[:800],
                ok=True,
            )
        )
    else:
        bundle.items.append(
            EvidenceItem(
                key="worktree_baseline_missing",
                source="not_verified",
                detail=(
                    "No task-start baseline — end-state dirty paths cannot be "
                    "attributed to this worker"
                ),
                ok=None,
            )
        )

    # Absolute end-state porcelain (informational; not attribution)
    rc, status_out = _run_git(ws, "status", "--porcelain")
    if rc == 0:
        lines = [ln for ln in status_out.splitlines() if ln.strip()]
        bundle.items.append(
            EvidenceItem(
                key="git_status_porcelain_end",
                source="cuttle_verified",
                detail=(f"{len(lines)} dirty path(s) at task end" if lines else "clean at task end")
                + (f" (sample: {status_out[:200]})" if status_out else ""),
                ok=True,
            )
        )
    else:
        bundle.items.append(
            EvidenceItem(
                key="git_status_porcelain_end",
                source="not_verified",
                detail=f"git status failed: {status_out[:200]}",
                ok=None,
            )
        )

    rc, diff_stat = _run_git(ws, "diff", "--stat")
    if rc == 0:
        bundle.items.append(
            EvidenceItem(
                key="git_diff_stat_end",
                source="cuttle_verified",
                detail=(diff_stat[:500] if diff_stat else "(no unstaged diff at end)"),
                ok=True,
            )
        )

    # Validate claimed paths (existence only — never run commands from report)
    claimed = list(getattr(worker_report, "files_changed", None) or []) if worker_report else []
    for raw_path in claimed[:30]:
        path = normalize_workspace_path(ws, str(raw_path))
        if path is None:
            bundle.items.append(
                EvidenceItem(
                    key="path_rejected",
                    source="cuttle_verified",
                    detail=f"Rejected unsafe/non-relative path from worker: {str(raw_path)[:120]}",
                    ok=False,
                )
            )
            continue
        exists = path.exists()
        bundle.items.append(
            EvidenceItem(
                key="file_exists",
                source="cuttle_verified",
                detail=f"{path.relative_to(Path(ws).resolve())} exists={exists}",
                ok=exists,
            )
        )
        rel = str(path.relative_to(Path(ws).resolve())).replace("\\", "/")
        irc, iout = _run_git(ws, "check-ignore", "-v", rel)
        if irc == 0 and iout.strip():
            bundle.items.append(
                EvidenceItem(
                    key="git_check_ignore",
                    source="cuttle_verified",
                    detail=f"{rel} is ignored: {iout[:200]}",
                    ok=True,
                )
            )

    # Acceptance criteria mentioning explicit file paths → existence check
    for crit in list(acceptance_criteria or [])[:20]:
        for m in re.finditer(r"`([^`]+)`|([\w./\\-]+\.\w{1,8})", str(crit)):
            cand = (m.group(1) or m.group(2) or "").strip()
            if not cand or "/" not in cand and "\\" not in cand and "." not in cand:
                continue
            path = normalize_workspace_path(ws, cand)
            if path is None:
                continue
            exists = path.exists()
            bundle.items.append(
                EvidenceItem(
                    key="acceptance_path",
                    source="cuttle_verified" if exists else "not_verified",
                    detail=f"Criterion path `{cand}` exists={exists}",
                    ok=exists if exists else None,
                )
            )

    return bundle


def reject_worker_supplied_commands(report: Any) -> List[str]:
    """Surface (do not execute) any command-like strings in worker output."""
    rejected: List[str] = []
    tests = list(getattr(report, "tests_run", None) or [])
    for t in tests:
        s = str(t).strip()
        # We never execute these — record that they remain worker_claimed only.
        if s:
            rejected.append(s)
    return rejected

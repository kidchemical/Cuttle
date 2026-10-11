"""Model-catalog diffs across ``/<agent> model refresh`` gestures.

A refresh re-reads the live CLI / model list. To tell the user what actually
changed — models added or retired — Cuttle keeps a small per-agent snapshot of
the last catalog it saw under ``<home>/cache/model_catalogs/``. Each refresh
diffs the fresh list against that snapshot, persists the new one, and renders a
compact added/removed block for the reply.

Snapshots are replaceable caches: deleting one only makes the next refresh
report a fresh baseline. A missing or read-only Cuttle home degrades to no
delta, never a failed refresh.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.runtime_paths import runtime_cache_path

_CACHE_VERSION = 1
_MAX_LISTED = 25


def _safe_agent(agent: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", str(agent or "").strip().lower()).strip("-")
    return slug or "agent"


def _snapshot_path(agent: str) -> Path:
    return runtime_cache_path(f"model_catalogs/{_safe_agent(agent)}.json")


def _normalize(models: Any) -> List[Dict[str, str]]:
    """Coerce rows (dicts with ``id``/``label`` or bare ids) to unique rows.

    ``resolved_model`` is kept because an alias can keep its id while pointing
    at a new model (``haiku`` → ``claude-haiku-4-5-…`` becomes
    ``claude-haiku-5-5``); an id-only diff would miss that.
    """
    rows: List[Dict[str, str]] = []
    seen = set()
    for item in models or []:
        if isinstance(item, dict):
            mid = str(item.get("id") or "").strip()
            label = str(item.get("label") or item.get("displayName") or "").strip()
            resolved = str(item.get("resolved_model") or item.get("resolvedModel") or "").strip()
        else:
            mid = str(item or "").strip()
            label = ""
            resolved = ""
        key = mid.lower()
        if not mid or key in seen:
            continue
        seen.add(key)
        rows.append({"id": mid, "label": label, "resolved_model": resolved})
    return rows


def _normalize_full(models: Any) -> List[Dict[str, Any]]:
    """Keep discovered metadata needed to reconstruct effective capabilities.

    The id/label/resolved fields remain the stable input to catalog diffs;
    this companion shape also retains only known JSON-safe CLI metadata.
    """
    allowed = (
        "description", "efforts", "variant_levels", "supports_variant",
        "supports_effort", "reasoning", "context", "cost", "current",
        "favorite", "provider", "resolved_model", "displayName",
    )
    rows: List[Dict[str, Any]] = []
    source_rows = list(models or [])
    by_id = {
        str(r.get("id") or "").strip().lower(): r
        for r in source_rows if isinstance(r, dict) and str(r.get("id") or "").strip()
    }
    for base in _normalize(source_rows):
        raw = by_id.get(base["id"].lower(), {})
        row: Dict[str, Any] = dict(base)
        for key in allowed:
            value = raw.get(key)
            if isinstance(value, (str, int, float, bool, list, dict)):
                row[key] = value
        rows.append(row)
    return rows


def load_snapshot(agent: str) -> Optional[List[Dict[str, str]]]:
    """The last catalog snapshot for ``agent``, or ``None`` when absent/stale."""
    path = _snapshot_path(agent)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        version = int(data.get("version") or 1)
    except (TypeError, ValueError):
        version = 1
    if version < _CACHE_VERSION:
        return None
    raw = data.get("models")
    if not isinstance(raw, list):
        return None
    return _normalize(raw)


def load_catalog(agent: str) -> Optional[Dict[str, Any]]:
    """Read the last successful full catalog and timestamp, if present."""
    path = _snapshot_path(agent)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("models"), list):
        return None
    rows = _normalize_full(data["models"])
    if not rows:
        return None
    return {"models": rows, "fetched_at": float(data.get("fetched_at") or 0)}


def save_snapshot(agent: str, models: Any) -> None:
    """Persist ``models`` as the new snapshot; failures are non-fatal."""
    path = _snapshot_path(agent)
    payload = {
        "version": _CACHE_VERSION,
        "fetched_at": time.time(),
        "models": _normalize_full(models),
    }
    try:
        fd, staging = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, indent=2)
            os.replace(staging, path)
        finally:
            Path(staging).unlink(missing_ok=True)
    except OSError:
        pass


def diff(previous: Any, current: Any) -> Dict[str, List[Dict[str, Any]]]:
    """Added / removed / changed rows between two catalogs (id-keyed).

    ``changed`` holds ids present in both whose label or resolved model moved —
    e.g. the ``haiku`` alias resolving to a new model without a new id.
    """
    prev = {row["id"].lower(): row for row in _normalize(previous)}
    cur = {row["id"].lower(): row for row in _normalize(current)}
    added = [cur[key] for key in cur if key not in prev]
    removed = [prev[key] for key in prev if key not in cur]
    changed: List[Dict[str, Any]] = []
    for key, after in cur.items():
        before = prev.get(key)
        if before is None:
            continue
        if (
            (before.get("label") or "") != (after.get("label") or "")
            or (before.get("resolved_model") or "") != (after.get("resolved_model") or "")
        ):
            changed.append({"id": after["id"], "before": before, "after": after})
    return {"added": added, "removed": removed, "changed": changed}


def record_and_diff(agent: str, current: Any) -> Dict[str, Any]:
    """Diff a fresh catalog against the stored snapshot and store the new one."""
    previous = load_snapshot(agent)
    delta = diff(previous, current)
    save_snapshot(agent, current)
    delta["first"] = previous is None
    delta["count"] = len(_normalize(current))
    return delta


def _bullet(row: Dict[str, str]) -> str:
    mid = row["id"]
    label = str(row.get("label") or "").strip()
    if label and label.lower() != mid.lower():
        return f"`{mid}` — {label}"
    return f"`{mid}`"


def _change_text(entry: Dict[str, Any]) -> str:
    mid = str(entry.get("id") or "")
    before = entry.get("before") or {}
    after = entry.get("after") or {}
    before_label = str(before.get("label") or "").strip()
    after_label = str(after.get("label") or "").strip()
    if before_label != after_label:
        return f"`{mid}` — {before_label or mid} → {after_label or mid}"
    before_res = str(before.get("resolved_model") or "").strip()
    after_res = str(after.get("resolved_model") or "").strip()
    return f"`{mid}` — resolves {before_res or mid} → {after_res or mid}"


def render_delta(delta: Dict[str, Any]) -> str:
    """Compact markdown block for a ``record_and_diff`` result."""
    if delta.get("first"):
        return (
            f"_First refresh — captured {int(delta.get('count') or 0)} models "
            "as the baseline (no earlier catalog to compare)._"
        )
    added = list(delta.get("added") or [])
    removed = list(delta.get("removed") or [])
    changed = list(delta.get("changed") or [])
    if not added and not removed and not changed:
        return "_No catalog changes since the last refresh._"
    lines = [
        f"**Catalog changes:** ➕ {len(added)} added · ➖ {len(removed)} removed "
        f"· ✎ {len(changed)} changed"
    ]
    for row in added[:_MAX_LISTED]:
        lines.append(f"- ➕ {_bullet(row)}")
    if len(added) > _MAX_LISTED:
        lines.append(f"- … and {len(added) - _MAX_LISTED} more added")
    for row in removed[:_MAX_LISTED]:
        lines.append(f"- ➖ {_bullet(row)}")
    if len(removed) > _MAX_LISTED:
        lines.append(f"- … and {len(removed) - _MAX_LISTED} more removed")
    for entry in changed[:_MAX_LISTED]:
        lines.append(f"- ✎ {_change_text(entry)}")
    if len(changed) > _MAX_LISTED:
        lines.append(f"- … and {len(changed) - _MAX_LISTED} more changed")
    return "\n".join(lines)

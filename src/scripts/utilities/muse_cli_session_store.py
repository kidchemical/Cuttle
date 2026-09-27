"""
Map Cuttle chat session + working directory → Muse Code session UUID for --session-id,
plus the per-chat Muse model preference set from the slash palette.

Stored under src/data/workspace/ (gitignored workspace data). Thread-safe.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any, Dict, Optional

_lock = threading.Lock()

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.I,
)


def _repo_root() -> Path:
    # .../Cuttle/src/scripts/utilities/this_file.py -> Cuttle
    return Path(__file__).resolve().parents[3]


def _map_file() -> Path:
    d = _repo_root() / "src" / "data" / "workspace"
    d.mkdir(parents=True, exist_ok=True)
    return d / "muse_cli_session_map.json"


def _normalize_session_id(cuttle_session_id: Optional[Any]) -> str:
    """Auth chat ids are ints; slash handlers may pass str — normalize for map keys."""
    if cuttle_session_id is None:
        return ""
    return str(cuttle_session_id).strip()


def _session_key(cwd: str, cuttle_session_id: Any) -> str:
    cwd_norm = str(Path(cwd).resolve())
    sid = _normalize_session_id(cuttle_session_id)
    if not sid:
        sid = "_no_session"
    return f"{cwd_norm}\x1f{sid}"


def _load_all() -> Dict[str, Any]:
    path = _map_file()
    if not path.is_file():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_all(data: Dict[str, Any]) -> None:
    path = _map_file()
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    tmp.replace(path)


def load_muse_resume_id(cwd: str, cuttle_session_id: Optional[Any]) -> Optional[str]:
    if not _normalize_session_id(cuttle_session_id):
        return None
    key = _session_key(cwd, cuttle_session_id)
    with _lock:
        data = _load_all()
    raw = data.get(key)
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s or not _UUID_RE.match(s):
        return None
    return s


def save_muse_resume_id(
    cwd: str,
    cuttle_session_id: Optional[Any],
    muse_session_uuid: Optional[str],
) -> None:
    if not _normalize_session_id(cuttle_session_id):
        return
    if not muse_session_uuid or not str(muse_session_uuid).strip():
        return
    g = str(muse_session_uuid).strip()
    if not _UUID_RE.match(g):
        return
    key = _session_key(cwd, cuttle_session_id)
    with _lock:
        data = _load_all()
        data[key] = g
        _write_all(data)


def _model_key(cuttle_session_id: Any) -> str:
    """Model preference is per chat, not per directory — key on the session only."""
    sid = _normalize_session_id(cuttle_session_id) or "_no_session"
    return f"model\x1f{sid}"


def load_muse_model(cuttle_session_id: Optional[Any]) -> Optional[str]:
    """Model this chat pinned via the slash palette, or None for the default."""
    if not _normalize_session_id(cuttle_session_id):
        return None
    with _lock:
        data = _load_all()
    raw = data.get(_model_key(cuttle_session_id))
    if not isinstance(raw, str):
        return None
    return raw.strip() or None


def save_muse_model(cuttle_session_id: Optional[Any], model: Optional[str]) -> Optional[str]:
    """Pin (or, with a falsy model, unpin) the Muse model for this chat."""
    if not _normalize_session_id(cuttle_session_id):
        return None
    key = _model_key(cuttle_session_id)
    m = str(model or "").strip()
    with _lock:
        data = _load_all()
        if m:
            data[key] = m
        elif key in data:
            del data[key]
        else:
            return None
        _write_all(data)
    return m or None


def _effort_key(cuttle_session_id: Any) -> str:
    """Reasoning-effort preference is per chat — key on the session only."""
    sid = _normalize_session_id(cuttle_session_id) or "_no_session"
    return f"effort\x1f{sid}"


def load_muse_effort(cuttle_session_id: Optional[Any]) -> Optional[str]:
    """Reasoning effort this chat pinned, or None for the CLI default."""
    if not _normalize_session_id(cuttle_session_id):
        return None
    with _lock:
        data = _load_all()
    raw = data.get(_effort_key(cuttle_session_id))
    if not isinstance(raw, str):
        return None
    return raw.strip().lower() or None


def save_muse_effort(cuttle_session_id: Optional[Any], effort: Optional[str]) -> Optional[str]:
    """Pin (or, with a falsy effort, unpin) the Muse reasoning effort for this chat."""
    if not _normalize_session_id(cuttle_session_id):
        return None
    key = _effort_key(cuttle_session_id)
    e = str(effort or "").strip().lower()
    with _lock:
        data = _load_all()
        if e:
            data[key] = e
        elif key in data:
            del data[key]
        else:
            return None
        _write_all(data)
    return e or None


def muse_data_home() -> Path:
    """Muse Code on-disk home (session logs + MSP views).

    Prefers ``$XDG_DATA_HOME/muse`` when set (same as ``agent_usage``), else
    ``~/.local/share/muse`` (Linux/WSL and current Windows Muse layout).
    """
    import os

    xdg = (os.environ.get("XDG_DATA_HOME") or "").strip()
    if xdg:
        return Path(xdg) / "muse"
    return Path.home() / ".local" / "share" / "muse"


def muse_msp_view_dir(muse_session_uuid: Optional[str]) -> Optional[Path]:
    sid = str(muse_session_uuid or "").strip()
    if not sid or not _UUID_RE.match(sid):
        return None
    d = muse_data_home() / "sessions" / ".msp-view-v1" / sid
    return d if d.is_dir() else None


def _load_latest_msp_snapshot(view_dir: Path) -> Optional[Dict[str, Any]]:
    head = view_dir / "HEAD.json"
    snap_id = None
    if head.is_file():
        try:
            head_data = json.loads(head.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            head_data = None
        if isinstance(head_data, dict):
            snap_id = str(head_data.get("latest_snapshot_id") or "").strip() or None
    if snap_id:
        path = view_dir / f"snapshot-{snap_id}.json"
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8-sig"))
                return data if isinstance(data, dict) else None
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                pass
    snaps = sorted(
        view_dir.glob("snapshot-*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for path in snaps[:3]:
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            continue
        if isinstance(data, dict):
            return data
    return None


def read_muse_msp_context(muse_session_uuid: Optional[str]) -> Optional[Dict[str, Any]]:
    """Read live context occupancy from Muse's MSP view snapshot.

    ``muse exec --json`` no longer emits usage events, so Cuttle cannot rely on
    stdout. Muse still writes ``tokenUsage`` / ``context_anchor`` into the
    ``.msp-view-v1/<session>/snapshot-*.json`` projection.
    """
    view = muse_msp_view_dir(muse_session_uuid)
    if not view:
        return None
    snap = _load_latest_msp_snapshot(view)
    if not isinstance(snap, dict):
        return None
    cont = snap.get("continuation") if isinstance(snap.get("continuation"), dict) else {}
    state = cont.get("state") if isinstance(cont.get("state"), dict) else {}
    mat = cont.get("view_materialization") if isinstance(cont.get("view_materialization"), dict) else {}
    cur = mat.get("current_state") if isinstance(mat.get("current_state"), dict) else {}
    tu = cur.get("tokenUsage") if isinstance(cur.get("tokenUsage"), dict) else {}
    usage = tu.get("usage") if isinstance(tu.get("usage"), dict) else {}

    def _i(*vals: Any) -> int:
        for v in vals:
            if v is None:
                continue
            try:
                n = int(v)
            except (TypeError, ValueError):
                continue
            if n >= 0:
                return n
        return 0

    # Live window fill — prefer Muse's own context_anchor / totalTokens.
    context_tokens = _i(
        state.get("context_anchor"),
        tu.get("totalTokens"),
        tu.get("total_tokens"),
    )
    prompt_tokens = _i(
        usage.get("inputTokens"),
        usage.get("input_tokens"),
        tu.get("promptTokens"),
        tu.get("prompt_tokens"),
    )
    completion_tokens = _i(
        usage.get("outputTokens"),
        usage.get("output_tokens"),
        tu.get("outputTokens"),
    )
    cached_tokens = _i(
        usage.get("cachedTokens"),
        usage.get("cacheReadTokens"),
        usage.get("cache_read_tokens"),
    )
    if context_tokens <= 0 and prompt_tokens > 0:
        # Fallback: uncached + cached input ≈ one-call occupancy.
        context_tokens = prompt_tokens + cached_tokens
    model = str(tu.get("modelId") or tu.get("model_id") or "").strip() or None
    if not model:
        # Some projections stash model on current_state.
        model = str(cur.get("model") or "").strip() or None
    if context_tokens <= 0 and prompt_tokens <= 0:
        return None
    out: Dict[str, Any] = {
        "context_tokens": int(context_tokens or prompt_tokens or 0),
        "prompt_tokens": int(prompt_tokens or context_tokens or 0),
        "completion_tokens": int(completion_tokens or 0),
        "total_tokens": int(context_tokens or (prompt_tokens + completion_tokens) or 0),
        "source": "msp_view",
    }
    if cached_tokens:
        out["cached_tokens"] = cached_tokens
    if model:
        out["model"] = model
    return out


def clear_muse_resume_id(cwd: str, cuttle_session_id: Optional[Any]) -> None:
    if not _normalize_session_id(cuttle_session_id):
        return
    key = _session_key(cwd, cuttle_session_id)
    with _lock:
        data = _load_all()
        if key in data:
            del data[key]
            _write_all(data)

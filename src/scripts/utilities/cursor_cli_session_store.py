"""
Map Cuttle chat session + working directory → Cursor Agent CLI session state.

Stores resume UUID for --resume plus optional per-chat agent options
(model, mode, sandbox). Stored under <home>/sessions/ (gitignored). Thread-safe.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from core.runtime_paths import runtime_state_path

_lock = threading.Lock()

_VALID_MODES = frozenset({"plan", "ask"})
_VALID_SANDBOX = frozenset({"enabled", "disabled"})
_MAX_RECENT_RUNS = 20



def _map_file() -> Path:
    return runtime_state_path("sessions", "cursor_cli_session_map.json")


def _normalize_session_id(cuttle_session_id: Optional[str]) -> str:
    """Canonical form so int 64 and 'db_session_64' share one map entry."""
    if cuttle_session_id is None:
        return ""
    s = str(cuttle_session_id).strip()
    if not s:
        return ""
    if s.startswith("db_session_"):
        bare = s[len("db_session_") :].strip()
        return f"db_session_{bare}" if bare else ""
    if s.isdigit():
        return f"db_session_{s}"
    return s


def _path_norm(cwd: str) -> str:
    try:
        return str(Path(cwd).resolve())
    except Exception:
        return str(cwd or "")


def _session_key(cwd: str, cuttle_session_id: str) -> str:
    cwd_norm = _path_norm(cwd)
    sid = _normalize_session_id(cuttle_session_id) or "_no_session"
    return f"{cwd_norm}\x1f{sid}"


def cwd_alias_candidates(cwd: str) -> list:
    """Same-repo aliases so Cuttle git root and ``…/src`` share a Cursor --resume.

    Cursor's ``--resume`` UUID is bound to the ``--workspace`` that created it.
    The chat chip can say "Cuttle" while cwd flips between ``C:\\Projects\\Cuttle``
    (Flask fallback) and ``C:\\Projects\\Cuttle\\src`` (registered project / Cursor
    workspace) without the user changing projects.
    """
    out: list = []
    seen = set()

    def _add(raw: str) -> None:
        n = _path_norm(raw)
        if not n:
            return
        key = n.lower()
        if key in seen:
            return
        seen.add(key)
        out.append(n)

    _add(cwd)
    try:
        p = Path(cwd).resolve()
    except Exception:
        return out
    # Cuttle git root ↔ src/; Unity (Escape Purgatory) git root ↔ source/
    if p.name.lower() in ("src", "source") and p.parent.exists():
        _add(str(p.parent))
    for child_name in ("src", "source"):
        child = p / child_name
        if child.is_dir():
            _add(str(child))
    return out


def _session_key_suffixes(cuttle_session_id: Optional[str]) -> list:
    sid = _normalize_session_id(cuttle_session_id)
    if not sid:
        return []
    suffixes = [f"\x1f{sid}"]
    if sid.startswith("db_session_"):
        bare = sid[len("db_session_") :]
        if bare:
            suffixes.append(f"\x1f{bare}")
    elif sid.isdigit():
        suffixes.append(f"\x1fdb_session_{sid}")
    # Dedupe
    seen = set()
    out = []
    for s in suffixes:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.I,
)


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


def _lookup_keys(cwd: str, cuttle_session_id: Optional[str]) -> list:
    if not cuttle_session_id or not str(cuttle_session_id).strip():
        return []
    keys = [_session_key(cwd, str(cuttle_session_id))]
    bare = str(cuttle_session_id).strip()
    if bare.startswith("db_session_"):
        keys.append(_session_key(cwd, bare[len("db_session_") :]))
    elif bare.isdigit():
        keys.append(f"{_path_norm(cwd)}\x1f{bare}")
    # Dedupe while preserving order
    seen = set()
    out = []
    for k in keys:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def _coerce_run_meta(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    out: Dict[str, Any] = {}
    ts = raw.get("ts")
    if isinstance(ts, (int, float)):
        out["ts"] = float(ts)
    elif isinstance(ts, str) and ts.strip():
        out["ts"] = ts.strip()
    for key in ("requested_model", "reported_model", "request_id", "cwd"):
        val = raw.get(key)
        if isinstance(val, str) and val.strip():
            out[key] = val.strip()
    usage = raw.get("usage")
    if isinstance(usage, dict):
        clean_usage: Dict[str, Any] = {}
        for uk, uv in usage.items():
            if isinstance(uv, (int, float)) and not isinstance(uv, bool):
                clean_usage[str(uk)] = uv
            elif isinstance(uv, str) and uv.strip():
                clean_usage[str(uk)] = uv.strip()
        if clean_usage:
            out["usage"] = clean_usage
    return out if out else None


def _coerce_entry(raw: Any) -> Dict[str, Any]:
    """Normalize legacy string resume ids and dict entries."""
    if isinstance(raw, str):
        s = raw.strip()
        return {"resume_id": s} if s else {}
    if isinstance(raw, dict):
        out: Dict[str, Any] = {}
        rid = raw.get("resume_id")
        if isinstance(rid, str) and rid.strip():
            out["resume_id"] = rid.strip()
        model = raw.get("model")
        if isinstance(model, str) and model.strip():
            out["model"] = model.strip()
        mode = raw.get("mode")
        if isinstance(mode, str) and mode.strip().lower() in _VALID_MODES:
            out["mode"] = mode.strip().lower()
        sandbox = raw.get("sandbox")
        if isinstance(sandbox, str) and sandbox.strip().lower() in _VALID_SANDBOX:
            out["sandbox"] = sandbox.strip().lower()
        last_reported = raw.get("last_reported_model")
        if isinstance(last_reported, str) and last_reported.strip():
            out["last_reported_model"] = last_reported.strip()
        runs_raw = raw.get("recent_runs")
        if isinstance(runs_raw, list):
            runs: list = []
            for item in runs_raw:
                coerced = _coerce_run_meta(item)
                if coerced:
                    runs.append(coerced)
            if runs:
                out["recent_runs"] = runs[-_MAX_RECENT_RUNS:]
        return out
    return {}


def _entry_resume_id(raw: Any) -> Optional[str]:
    entry = _coerce_entry(raw)
    s = str(entry.get("resume_id") or "").strip()
    if s and _UUID_RE.match(s):
        return s
    return None


def load_cursor_resume_binding(
    cwd: str, cuttle_session_id: Optional[str]
) -> Dict[str, Any]:
    """Return ``{resume_id, workspace}`` for Cursor ``agent --resume``.

    Lookup order:
    1. This cwd
    2. Same-repo aliases (git root ↔ ``src/`` / ``source/``)

    Never follow a resume saved under a *different* project. The chat project
    chip is cwd; switching Cuttle → Escape Purgatory must start a new CLI
    session rather than pinning ``--workspace`` to the old repo (CH-000164).
    Model prefs may still inherit across cwd via ``load_cursor_agent_options``.
    """
    if not cuttle_session_id or not str(cuttle_session_id).strip():
        return {}
    with _lock:
        data = _load_all()

    def _from_cwd(c: str) -> Optional[Dict[str, Any]]:
        for key in _lookup_keys(c, cuttle_session_id):
            rid = _entry_resume_id(data.get(key))
            if rid:
                return {"resume_id": rid, "workspace": _path_norm(c)}
        return None

    for candidate in cwd_alias_candidates(cwd):
        hit = _from_cwd(candidate)
        if hit:
            return hit
    return {}


def load_cursor_resume_id(cwd: str, cuttle_session_id: Optional[str]) -> Optional[str]:
    """Return Cursor Agent CLI session UUID for --resume, or None if unknown / no Cuttle session."""
    return load_cursor_resume_binding(cwd, cuttle_session_id).get("resume_id")


def _same_project(left: str, right: str) -> bool:
    if not left or not right:
        return False
    a = {p.lower() for p in cwd_alias_candidates(left)}
    b = {p.lower() for p in cwd_alias_candidates(right)}
    return bool(a & b)


def apply_cursor_resume_workspace(
    cwd: str, cuttle_session_id: Optional[str]
) -> tuple:
    """Return ``(workspace, resume_id)`` pinning --workspace to the resume owner.

    Pin only within the requested project (git root ↔ src/source). A resume
    from another chip is ignored so ``--workspace`` follows the project chip.
    """
    requested = _path_norm(cwd) if cwd else ""
    binding = load_cursor_resume_binding(cwd, cuttle_session_id)
    rid = binding.get("resume_id")
    ws = binding.get("workspace") or requested or cwd
    if rid and requested and ws and not _same_project(requested, str(ws)):
        return requested, None
    return str(ws), rid


def prepare_cursor_agent_workspace(
    cwd: str, cuttle_session_id: Optional[str]
) -> tuple:
    """Workspace for ``agent --workspace``: nested git (``source/``) when new, else resume pin.

    Pending-changes already walks Unity ``source/``. ``/cursor`` must do the same
    or the agent sees no repo at the chip path.
    """
    git_ws = cwd
    try:
        from scripts.utilities.git_pending_changes import resolve_git_workdir

        found = resolve_git_workdir(cwd)
        if found:
            git_ws = found
    except Exception:
        pass
    pinned, rid = apply_cursor_resume_workspace(git_ws, cuttle_session_id)
    if rid:
        return pinned, rid
    return str(git_ws), None


def load_cursor_agent_options_for_session(cuttle_session_id: Optional[str]) -> Dict[str, Any]:
    """Best-effort prefs for a chat session across any cwd key."""
    sid = _normalize_session_id(cuttle_session_id)
    if not sid:
        return {}
    suffix = "\x1f" + sid
    with _lock:
        data = _load_all()
    best: Dict[str, Any] = {}
    for key, raw in data.items():
        if not isinstance(key, str) or not key.endswith(suffix):
            continue
        other = _coerce_entry(raw)
        if not other:
            continue
        if not best:
            best = dict(other)
            continue
        # Prefer an entry that actually has a model set.
        if other.get("model") and not best.get("model"):
            best = dict(other)
            continue
        # Prefer richer recent_runs if models tie.
        if other.get("model") == best.get("model"):
            if len(other.get("recent_runs") or []) > len(best.get("recent_runs") or []):
                best = dict(other)
    return best


def load_cursor_agent_options(cwd: str, cuttle_session_id: Optional[str]) -> Dict[str, Any]:
    """Return per-chat Cursor Agent options (model, mode, sandbox, resume_id).

    Prefs are stored per (cwd, session). If this cwd has no ``model`` yet but
    another cwd for the same chat session does (e.g. project chip flipped),
    inherit that model so badges don't snap back to Auto.
    """
    keys = _lookup_keys(cwd, cuttle_session_id)
    if not keys:
        return load_cursor_agent_options_for_session(cuttle_session_id)
    sid = _normalize_session_id(cuttle_session_id)
    with _lock:
        data = _load_all()
    entry: Dict[str, Any] = {}
    for key in keys:
        found = _coerce_entry(data.get(key))
        if found:
            entry = found
            break
    if entry.get("model") or not sid:
        return entry

    # Inherit model / last_reported from any other cwd for this session.
    inherited = load_cursor_agent_options_for_session(cuttle_session_id)
    if inherited.get("model"):
        entry = dict(entry)
        entry["model"] = inherited["model"]
        if not entry.get("last_reported_model") and inherited.get("last_reported_model"):
            entry["last_reported_model"] = inherited["last_reported_model"]
        if not entry.get("recent_runs") and inherited.get("recent_runs"):
            entry["recent_runs"] = inherited["recent_runs"]
    return entry


def update_cursor_agent_options(
    cwd: str,
    cuttle_session_id: Optional[str],
    **patch: Any,
) -> None:
    """Merge per-chat Cursor Agent options. Pass mode=None / model=None to clear."""
    if not cuttle_session_id or not str(cuttle_session_id).strip():
        return
    key = _session_key(cwd, cuttle_session_id)
    with _lock:
        data = _load_all()
        entry = _coerce_entry(data.get(key))
        if "model" in patch:
            model = patch.get("model")
            if model is None or (isinstance(model, str) and not model.strip()):
                entry.pop("model", None)
            elif isinstance(model, str):
                entry["model"] = model.strip()
        if "mode" in patch:
            mode = patch.get("mode")
            if mode is None or (isinstance(mode, str) and not str(mode).strip()):
                entry.pop("mode", None)
            elif isinstance(mode, str) and mode.strip().lower() in _VALID_MODES:
                entry["mode"] = mode.strip().lower()
        if "sandbox" in patch:
            sandbox = patch.get("sandbox")
            if sandbox is None or (isinstance(sandbox, str) and not sandbox.strip()):
                entry.pop("sandbox", None)
            elif isinstance(sandbox, str) and sandbox.strip().lower() in _VALID_SANDBOX:
                entry["sandbox"] = sandbox.strip().lower()
        if "resume_id" in patch:
            rid = patch.get("resume_id")
            if rid is None or (isinstance(rid, str) and not rid.strip()):
                entry.pop("resume_id", None)
            elif isinstance(rid, str) and _UUID_RE.match(rid.strip()):
                entry["resume_id"] = rid.strip()
        if entry:
            data[key] = entry
        elif key in data:
            del data[key]
        _write_all(data)


def save_cursor_resume_id(
    cwd: str,
    cuttle_session_id: Optional[str],
    cursor_session_uuid: Optional[str],
) -> None:
    """Persist the Cursor Agent session_id from stream-json output for future --resume."""
    # Auth chats pass the numeric DB id, so this must not assume a str.
    if not str(cuttle_session_id or "").strip():
        return
    if not cursor_session_uuid or not str(cursor_session_uuid).strip():
        return
    g = str(cursor_session_uuid).strip()
    if not _UUID_RE.match(g):
        return
    key = _session_key(cwd, cuttle_session_id)
    with _lock:
        data = _load_all()
        entry = _coerce_entry(data.get(key))
        entry["resume_id"] = g
        data[key] = entry
        _write_all(data)


def clear_cursor_resume_id(cwd: str, cuttle_session_id: Optional[str]) -> None:
    """Remove stored resume UUID for this chat across all cwd keys (keeps prefs)."""
    suffixes = _session_key_suffixes(cuttle_session_id)
    if not suffixes:
        return
    with _lock:
        data = _load_all()
        changed = False
        for key in list(data.keys()):
            if not isinstance(key, str) or not any(key.endswith(s) for s in suffixes):
                continue
            entry = _coerce_entry(data.get(key))
            if "resume_id" not in entry:
                continue
            entry.pop("resume_id", None)
            changed = True
            if entry:
                data[key] = entry
            else:
                del data[key]
        if changed:
            _write_all(data)
    # cwd kept in the signature for callers; lookup is by chat id.


def append_cursor_run_meta(
    cwd: str,
    cuttle_session_id: Optional[str],
    meta: Optional[Dict[str, Any]],
) -> None:
    """Append one Cursor Agent run snapshot (model / request_id / usage) for this chat."""
    if not cuttle_session_id or not str(cuttle_session_id).strip():
        return
    coerced = _coerce_run_meta(meta or {})
    if not coerced:
        return
    if "ts" not in coerced:
        import time as _time

        coerced["ts"] = _time.time()
    if "cwd" not in coerced and cwd:
        try:
            coerced["cwd"] = str(Path(cwd).resolve())
        except Exception:
            coerced["cwd"] = str(cwd)
    key = _session_key(cwd, cuttle_session_id)
    with _lock:
        data = _load_all()
        entry = _coerce_entry(data.get(key))
        runs = list(entry.get("recent_runs") or [])
        runs.append(coerced)
        entry["recent_runs"] = runs[-_MAX_RECENT_RUNS:]
        reported = coerced.get("reported_model")
        if isinstance(reported, str) and reported.strip():
            entry["last_reported_model"] = reported.strip()
        data[key] = entry
        _write_all(data)


def format_recent_cursor_runs_markdown(
    opts: Optional[Dict[str, Any]],
    *,
    limit: int = 8,
) -> str:
    """Markdown block summarizing recent Cursor Agent runs for /model and /about."""
    opts = opts or {}
    runs = list(opts.get("recent_runs") or [])
    if not runs:
        return ""
    lines = ["**Recent Cursor runs** (this chat)", ""]
    for run in reversed(runs[-max(1, limit) :]):
        requested = run.get("requested_model") or "auto"
        reported = run.get("reported_model") or "?"
        rid = run.get("request_id") or ""
        rid_short = (rid[:8] + "…") if len(rid) > 8 else rid
        usage = run.get("usage") if isinstance(run.get("usage"), dict) else {}
        inn = usage.get("inputTokens") or usage.get("input_tokens")
        out = usage.get("outputTokens") or usage.get("output_tokens")
        tok = ""
        if isinstance(inn, (int, float)) or isinstance(out, (int, float)):
            tok = f" · {int(inn or 0)}→{int(out or 0)} tok"
        ts = run.get("ts")
        when = ""
        if isinstance(ts, (int, float)):
            try:
                from datetime import datetime

                when = datetime.fromtimestamp(float(ts)).strftime("%m/%d %H:%M")
            except Exception:
                when = ""
        elif isinstance(ts, str) and ts.strip():
            when = ts.strip()[:16]
        prefix = f"`{when}` " if when else ""
        rid_bit = f" · req `{rid_short}`" if rid_short else ""
        lines.append(
            f"- {prefix}requested `{requested}` · reported `{reported}`{rid_bit}{tok}"
        )
    lines.append("")
    lines.append(
        "_Auto usually reports as `Auto` — Cursor does not expose the underlying routed model to the CLI._"
    )
    return "\n".join(lines)

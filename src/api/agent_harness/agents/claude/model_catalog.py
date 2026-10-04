"""Claude Code model catalog — SDK ``initialize`` control request (no user turn).

``claude -p --input-format stream-json`` answers an ``initialize`` control
request with the account's model list (``value`` / ``resolvedModel`` /
``supportedEffortLevels``) and exits on stdin EOF without sampling. Loads use a
short in-process cache; ``refresh=True`` always re-asks the CLI. Falls back to
the manifest snapshot when the CLI is missing or not signed in.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from core.agent_cli_env import agent_cli_env

# The CLI's own "Default (recommended)" row. Unpinned turns omit ``--model``,
# so it is not a pinnable palette row; its efforts govern unpinned turns.
_DEFAULT_ROW_ID = "default"
_EFFORT_ORDER = ("low", "medium", "high", "xhigh", "max")
_REQUEST_ID = "cuttle-models"
_CATALOG_CACHE_TTL_SEC = 120.0


def _read_declared_model_efforts() -> Tuple[List[str], Dict[str, List[str]], List[str]]:
    """Verified offline model/effort snapshot from this adapter's manifest."""
    try:
        raw = yaml.safe_load(
            Path(__file__).with_name("manifest.yaml").read_text(encoding="utf-8")
        ) or {}
    except (OSError, yaml.YAMLError):
        return [], {}, []
    models = [str(m).strip() for m in (raw.get("models") or []) if str(m).strip()]
    model_efforts: Dict[str, List[str]] = {}
    for model, levels in (raw.get("model_efforts") or {}).items():
        mid = str(model or "").strip().lower()
        values = [levels] if isinstance(levels, str) else (levels or [])
        if mid:
            model_efforts[mid] = [str(v).strip().lower() for v in values if str(v).strip()]
    default_efforts = [
        str(v).strip().lower() for v in (raw.get("efforts") or []) if str(v).strip()
    ]
    return models, model_efforts, default_efforts


_DECLARED_MODELS, CLAUDE_MODEL_EFFORTS, _DECLARED_DEFAULT_EFFORTS = _read_declared_model_efforts()


def _known_model_label(model_id: str) -> str:
    mid = str(model_id or "").strip()
    if mid.lower().startswith("claude-"):
        parts = mid[7:].split("-")
        family = parts[0].capitalize() if parts else mid
        version = ".".join(p for p in parts[1:] if p.isdigit() and len(p) <= 2)
        return f"{family} {version}".strip()
    return mid.capitalize()


CLAUDE_KNOWN_MODELS: List[Dict[str, Any]] = [
    {
        "id": model_id,
        "label": _known_model_label(model_id),
        "description": "Claude Code model (offline manifest snapshot)",
        "efforts": list(CLAUDE_MODEL_EFFORTS.get(model_id.lower(), [])),
    }
    for model_id in _DECLARED_MODELS
]

_LOCK = threading.Lock()
# (fetched_at, rows, default_efforts, source, error)
_CATALOG_CACHE: Optional[Tuple[float, List[Dict[str, Any]], List[str], str, Optional[str]]] = None


def clear_claude_catalog_cache() -> None:
    global _CATALOG_CACHE
    _CATALOG_CACHE = None


def _ordered_efforts(levels: Any) -> List[str]:
    values = {str(v).strip().lower() for v in (levels or []) if str(v).strip()}
    ordered = [e for e in _EFFORT_ORDER if e in values]
    ordered.extend(sorted(values.difference(ordered)))
    return ordered


def _run_claude_initialize() -> Tuple[List[Dict[str, Any]], Optional[List[str]], Optional[str]]:
    """Ask the installed CLI for its model list. Returns ``(rows, default_efforts, error)``."""
    from scripts.utilities.claude_cli_tool import claude_executable

    exe = claude_executable()
    if not exe:
        return [], None, "Claude Code CLI not found"
    request = {
        "type": "control_request",
        "request_id": _REQUEST_ID,
        "request": {"subtype": "initialize"},
    }
    try:
        r = subprocess.run(
            [exe, "-p", "--input-format", "stream-json",
             "--output-format", "stream-json", "--verbose"],
            input=json.dumps(request) + "\n",
            capture_output=True,
            text=True,
            # Home, not a project: project .claude/ settings must not leak into
            # the account-level catalog, and no turn ever runs here.
            cwd=str(Path.home()),
            env=agent_cli_env(),
            timeout=45,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return [], None, str(exc)[:200]

    for line in (r.stdout or "").splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if not isinstance(item, dict) or item.get("type") != "control_response":
            continue
        response = item.get("response") or {}
        if response.get("request_id") != _REQUEST_ID:
            continue
        rows: List[Dict[str, Any]] = []
        default_efforts: Optional[List[str]] = None
        seen = set()
        for row in (response.get("response") or {}).get("models") or []:
            if not isinstance(row, dict):
                continue
            mid = str(row.get("value") or "").strip()
            efforts = _ordered_efforts(row.get("supportedEffortLevels")) if row.get("supportsEffort") else []
            if mid.lower() == _DEFAULT_ROW_ID:
                default_efforts = efforts
                continue
            if not mid or mid.lower() in seen:
                continue
            seen.add(mid.lower())
            rows.append({
                "id": mid,
                "label": str(row.get("displayName") or mid),
                "description": str(row.get("description") or ""),
                "resolved_model": str(row.get("resolvedModel") or mid),
                "efforts": efforts,
            })
        if rows:
            return rows, default_efforts, None
        return [], None, "Claude Code returned no models"
    # Auth/config stderr can carry account details — never surface it raw.
    return [], None, "Claude Code catalog unavailable (check `claude auth login`)"


def list_claude_catalog_models(*, refresh: bool = False) -> Dict[str, Any]:
    """Return palette-ready Claude Code models."""
    global _CATALOG_CACHE
    with _LOCK:
        now = time.monotonic()
        if refresh:
            clear_claude_catalog_cache()
        elif _CATALOG_CACHE is not None:
            ts, cached, default_efforts, source, err = _CATALOG_CACHE
            if now - ts < _CATALOG_CACHE_TTL_SEC and cached:
                return {
                    "models": [dict(m) for m in cached],
                    "default_efforts": list(default_efforts),
                    "count": len(cached),
                    "source": source,
                    "error": err,
                    "fetched_at": ts,
                }

        live, default_efforts, err = _run_claude_initialize()
        if live:
            if default_efforts is None:
                default_efforts = list(_DECLARED_DEFAULT_EFFORTS)
            _CATALOG_CACHE = (now, list(live), list(default_efforts), "cli", None)
            return {
                "models": [dict(m) for m in live],
                "default_efforts": list(default_efforts),
                "count": len(live),
                "source": "cli",
                "error": None,
                "fetched_at": now,
            }

        static = [dict(m) for m in CLAUDE_KNOWN_MODELS]
        _CATALOG_CACHE = (now, list(static), list(_DECLARED_DEFAULT_EFFORTS), "static_fallback", err)
        return {
            "models": static,
            "default_efforts": list(_DECLARED_DEFAULT_EFFORTS),
            "count": len(static),
            "source": "static_fallback",
            "error": err,
            "fetched_at": now,
        }


def refresh_claude_catalog() -> Dict[str, Any]:
    """Force a fresh ``initialize`` round-trip against the installed CLI."""
    return list_claude_catalog_models(refresh=True)


def claude_model_label(model: Optional[str]) -> str:
    mid = str(model or "").strip()
    if not mid:
        return ""
    rows = _CATALOG_CACHE[1] if _CATALOG_CACHE is not None else CLAUDE_KNOWN_MODELS
    for row in list(rows) + CLAUDE_KNOWN_MODELS:
        if mid.lower() in (str(row.get("id") or "").lower(), str(row.get("resolved_model") or "").lower()):
            return str(row.get("label") or mid)
    return mid


def _row_efforts(rows: Optional[List[Dict[str, Any]]], mid: str) -> Optional[List[str]]:
    for row in rows or []:
        if mid in (str(row.get("id") or "").lower(), str(row.get("resolved_model") or "").lower()):
            return list(row.get("efforts") or [])
    return None


def claude_efforts_for_model(model: Optional[str] = None) -> List[str]:
    """Verified ``--effort`` levels for a model; unpinned → the CLI default model's.

    Live catalog (cached) first, then the manifest snapshot. A model in neither
    loads the CLI catalog once, because the manifest lags new CLI models.
    Ids the CLI does not list stay empty.
    """
    mid = str(model or "").strip().lower()
    if not mid or mid == _DEFAULT_ROW_ID:
        return list(list_claude_catalog_models().get("default_efforts") or [])
    fresh = None
    if _CATALOG_CACHE is not None and time.monotonic() - _CATALOG_CACHE[0] < _CATALOG_CACHE_TTL_SEC:
        fresh = _CATALOG_CACHE[1]
    found = _row_efforts(fresh, mid)
    if found is None:
        found = _row_efforts(CLAUDE_KNOWN_MODELS, mid)
    if found is None:
        found = _row_efforts(list_claude_catalog_models().get("models"), mid)
    return found or []


def list_claude_palette_models(
    *, refresh: bool = False, q: Optional[str] = None, limit: Optional[int] = None
) -> Dict[str, Any]:
    """Catalog wrapper used by ``/api/claude/models``."""
    result = list_claude_catalog_models(refresh=refresh)
    models = list(result.get("models") or [])
    needle = (q or "").strip().lower()
    if needle:
        models = [
            m for m in models
            if needle in str(m.get("id") or "").lower()
            or needle in str(m.get("label") or "").lower()
            or needle in str(m.get("description") or "").lower()
        ]
    if limit is not None and limit > 0:
        models = models[: int(limit)]
    return {**result, "models": models, "returned": len(models)}

"""Codex model catalog — ``codex debug models`` (+ optional remote refresh).

Normal loads prefer a short in-process cache, then ``codex debug models --bundled``.
``refresh=True`` runs ``codex debug models`` (no ``--bundled``) so Codex can pull
its remote catalog. Falls back to the soft-known list when the CLI is missing.
"""

from __future__ import annotations

from core.agent_cli_env import agent_cli_env

import json
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml


def _read_declared_model_efforts() -> Tuple[List[str], Dict[str, List[str]]]:
    """Read the verified offline model/effort snapshot from this adapter manifest."""
    try:
        raw = yaml.safe_load(
            Path(__file__).with_name("manifest.yaml").read_text(encoding="utf-8")
        ) or {}
    except (OSError, yaml.YAMLError):
        return [], {}
    models_raw = raw.get("models") or []
    models = [str(model).strip() for model in models_raw if str(model).strip()]
    efforts_raw = raw.get("model_efforts") or {}
    model_efforts: Dict[str, List[str]] = {}
    if isinstance(efforts_raw, dict):
        for model, levels in efforts_raw.items():
            mid = str(model or "").strip()
            if not mid:
                continue
            values = [levels] if isinstance(levels, str) else levels
            if isinstance(values, (list, tuple)):
                model_efforts[mid.lower()] = [
                    str(level).strip().lower()
                    for level in values
                    if str(level).strip()
                ]
    return models, model_efforts


_DECLARED_MODELS, CODEX_MODEL_EFFORTS = _read_declared_model_efforts()
_EFFORT_ORDER = ("minimal", "low", "medium", "high", "xhigh", "max", "ultra")
CODEX_REASONING_EFFORTS = tuple(
    effort
    for effort in _EFFORT_ORDER
    if any(effort in levels for levels in CODEX_MODEL_EFFORTS.values())
)


def _known_model_label(model_id: str) -> str:
    mid = str(model_id or "").strip()
    if mid.lower().startswith("gpt-"):
        tail = "-".join(part.capitalize() for part in mid[4:].split("-"))
        return f"GPT-{tail}"
    return "-".join(part.capitalize() for part in mid.split("-"))


# Soft-known rows are generated from manifest.yaml so offline palette behavior
# follows the same per-model effort declaration exposed by /api/agents.
CODEX_KNOWN_MODELS: List[Dict[str, Any]] = [
    {
        "id": model_id,
        "label": _known_model_label(model_id),
        "description": "Verified Codex CLI model (offline manifest snapshot)",
        "efforts": list(CODEX_MODEL_EFFORTS.get(model_id.lower(), [])),
    }
    for model_id in _DECLARED_MODELS
]

_CATALOG_CACHE: Optional[Tuple[float, List[Dict[str, Any]], str, Optional[str]]] = None
_CATALOG_CACHE_TTL_SEC = 120.0


def clear_codex_catalog_cache() -> None:
    global _CATALOG_CACHE
    _CATALOG_CACHE = None


def codex_model_label(model: Optional[str]) -> str:
    mid = str(model or "").strip()
    if not mid:
        return ""
    for known in CODEX_KNOWN_MODELS:
        if known["id"].lower() == mid.lower():
            return known["label"]
    # Prefer live cache labels when present.
    if _CATALOG_CACHE is not None:
        for row in _CATALOG_CACHE[1]:
            if str(row.get("id") or "").lower() == mid.lower() and row.get("label"):
                return str(row["label"])
    return mid


def _run_codex_debug_models(*, refresh: bool) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    from scripts.utilities.codex_cli_tool import codex_executable

    exe = codex_executable()
    if not exe:
        return [], "Codex CLI not found"
    cmd = [exe, "debug", "models"]
    if not refresh:
        cmd.append("--bundled")
    try:
        r = subprocess.run(
            cmd,
                env=agent_cli_env(),
            capture_output=True,
            text=True,
            timeout=90 if refresh else 45,
        )
    except Exception as exc:
        return [], str(exc)[:200]
    raw = (r.stdout or "").strip()
    if not raw:
        err = (r.stderr or "").strip()[:200] or f"exit {r.returncode}"
        return [], err
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return [], "invalid JSON from codex debug models"
    rows = payload.get("models") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return [], "unexpected codex debug models shape"
    out: List[Dict[str, Any]] = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        mid = str(row.get("slug") or row.get("id") or "").strip()
        if not mid:
            continue
        key = mid.lower()
        if key in seen:
            continue
        seen.add(key)
        label = str(row.get("display_name") or row.get("name") or mid).strip() or mid
        desc = str(row.get("description") or "").strip()
        efforts: List[str] = []
        for lvl in row.get("supported_reasoning_levels") or []:
            if isinstance(lvl, dict):
                e = str(lvl.get("effort") or "").strip().lower()
            else:
                e = str(lvl or "").strip().lower()
            if e and e not in efforts:
                efforts.append(e)
        visibility = str(row.get("visibility") or "").strip().lower()
        # Keep listable models; still include if visibility is empty/unknown.
        if visibility and visibility not in ("list", "shown", "visible", "default"):
            # "hidden" / "never" — skip unless it was soft-known.
            if key not in {k["id"].lower() for k in CODEX_KNOWN_MODELS}:
                if visibility in ("hidden", "never", "internal"):
                    continue
        out.append(
            {
                "id": mid,
                "label": label,
                "description": desc,
                "efforts": efforts,
                "default_effort": str(
                    row.get("default_reasoning_level") or ""
                ).strip().lower(),
                "priority": row.get("priority"),
            }
        )
    if not out:
        return [], "codex debug models returned no rows"
    return out, None


def list_codex_catalog_models(*, refresh: bool = False) -> Dict[str, Any]:
    """Return palette-ready Codex models."""
    global _CATALOG_CACHE
    now = time.monotonic()
    if refresh:
        clear_codex_catalog_cache()
    elif _CATALOG_CACHE is not None:
        ts, cached, source, err = _CATALOG_CACHE
        if now - ts < _CATALOG_CACHE_TTL_SEC and cached:
            return {
                "models": [dict(m) for m in cached],
                "count": len(cached),
                "source": source,
                "error": err,
                "fetched_at": ts,
            }

    live, err = _run_codex_debug_models(refresh=refresh)
    if live:
        source = "cli_refresh" if refresh else "cli_bundled"
        _CATALOG_CACHE = (now, list(live), source, None)
        return {
            "models": [dict(m) for m in live],
            "count": len(live),
            "source": source,
            "error": None,
            "fetched_at": now,
        }

    static = [dict(m) for m in CODEX_KNOWN_MODELS]
    _CATALOG_CACHE = (now, list(static), "static_fallback", err)
    return {
        "models": static,
        "count": len(static),
        "source": "static_fallback",
        "error": err,
        "fetched_at": now,
    }


def refresh_codex_catalog() -> Dict[str, Any]:
    """Force ``codex debug models`` (remote refresh when the CLI supports it)."""
    return list_codex_catalog_models(refresh=True)


def _fresh_catalog_rows() -> Optional[List[Dict[str, Any]]]:
    if _CATALOG_CACHE is not None:
        cached_at, cached_rows, _source, _err = _CATALOG_CACHE
        if time.monotonic() - cached_at < _CATALOG_CACHE_TTL_SEC and cached_rows:
            return cached_rows
    return None


def _row_efforts(rows: Optional[List[Dict[str, Any]]], mid: str) -> Optional[List[str]]:
    for row in rows or []:
        if str(row.get("id") or "").strip().lower() == mid:
            return [
                str(level).strip().lower()
                for level in (row.get("efforts") or [])
                if str(level).strip()
            ]
    return None


def codex_efforts_for_model(model: Optional[str] = None) -> List[str]:
    """Return verified effort levels for a model, or the all-model intersection.

    Recent live `codex debug models` metadata wins, then the manifest snapshot
    (no CLI process). A model in neither loads the CLI catalog once (cached),
    because the manifest lags new CLI models and the live cache expires —
    otherwise a valid pin is refused whenever the palette has not been opened
    in the last two minutes. Ids the CLI does not list stay empty.
    """
    mid = str(model or "").strip().lower()
    if mid:
        found = _row_efforts(_fresh_catalog_rows(), mid)
        if found is None:
            found = _row_efforts(CODEX_KNOWN_MODELS, mid)
        if found is None:
            found = _row_efforts(list_codex_catalog_models().get("models"), mid)
        return found or []

    rows = _fresh_catalog_rows() or CODEX_KNOWN_MODELS
    rows = [row for row in rows if isinstance(row, dict)]
    if not rows:
        return []
    available = [
        {
            str(level).strip().lower()
            for level in (row.get("efforts") or [])
            if str(level).strip()
        }
        for row in rows
    ]
    common = set.intersection(*available) if available else set()
    ordered = [effort for effort in CODEX_REASONING_EFFORTS if effort in common]
    ordered.extend(sorted(common.difference(ordered)))
    return ordered


def codex_common_reasoning_efforts() -> List[str]:
    """Levels verified across every model in the active Codex catalog."""
    return codex_efforts_for_model()


def list_codex_palette_models(
    *, refresh: bool = False, q: Optional[str] = None, limit: Optional[int] = None
) -> Dict[str, Any]:
    """Catalog wrapper used by ``/api/codex/models``."""
    result = list_codex_catalog_models(refresh=refresh)
    models = list(result.get("models") or [])
    needle = (q or "").strip().lower()
    if needle:
        models = [
            m
            for m in models
            if needle in str(m.get("id") or "").lower()
            or needle in str(m.get("label") or "").lower()
            or needle in str(m.get("description") or "").lower()
        ]
    total = len(models)
    if limit is not None and limit > 0:
        models = models[: int(limit)]
    return {
        **result,
        "models": models,
        "count": int(result.get("count") or total),
        "returned": len(models),
    }

"""One live model-catalog store per agent, shared by every Cuttle surface.

The chat palette already reads each harness's live catalog (CLI / API). Settings
and the Agent Router used the manifest ``models:`` snapshot and hardcoded sets
instead, so they drifted — e.g. OpenCode offered 5 curated ids while the palette
had the full ``opencode models`` list. This module is the single dispatcher:
:func:`load_agent_catalog` mirrors the chat palette loaders, with the manifest
snapshot as the offline fallback, and the bulk helpers fan out in parallel.

Callers: ``api.agent_cost`` (``/cost``), ``GET /api/agents?live_models=1``
(Settings), and ``GET /api/agent-router/options?live=1`` (Router editor).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Tuple

_Catalog = Tuple[List[Dict[str, Any]], str]


def _rows_from_ids(ids: List[str]) -> List[Dict[str, Any]]:
    return [{"id": m, "label": m} for m in ids if str(m or "").strip()]


def _cursor_catalog(refresh: bool) -> _Catalog:
    from api.cursor_agent_commands import list_cursor_agent_models, refresh_cursor_agent_models

    result = refresh_cursor_agent_models() if refresh else None
    models = result.get("models") if result else list_cursor_agent_models()
    return (
        [dict(m) for m in (models or []) if isinstance(m, dict)],
        "cli_refresh" if refresh else "cli",
    )


def _codex_catalog(refresh: bool) -> _Catalog:
    from api.agent_harness.agents.codex.model_catalog import list_codex_catalog_models

    result = list_codex_catalog_models(refresh=refresh)
    return list(result.get("models") or []), str(result.get("source") or "cli")


def _claude_catalog(refresh: bool) -> _Catalog:
    from api.agent_harness.agents.claude.model_catalog import list_claude_catalog_models

    result = list_claude_catalog_models(refresh=refresh)
    return list(result.get("models") or []), str(result.get("source") or "cli")


def _muse_catalog(refresh: bool) -> _Catalog:
    from scripts.utilities.muse_cli_tool import list_muse_catalog_models

    result = list_muse_catalog_models(refresh=refresh)
    return list(result.get("models") or []), str(result.get("source") or "static")


def _hermes_catalog(refresh: bool) -> _Catalog:
    from scripts.utilities.hermes_cli_tool import list_hermes_known_models

    return [dict(m) for m in list_hermes_known_models()], "config"


def _opencode_catalog(refresh: bool) -> _Catalog:
    from api.agent_harness.agents.opencode.model_catalog import list_opencode_catalog_models

    result = list_opencode_catalog_models(refresh=refresh)
    return list(result.get("models") or []), str(result.get("source") or "cli")


_CATALOG_LOADERS: Dict[str, Callable[[bool], _Catalog]] = {
    "cursor": _cursor_catalog,
    "codex": _codex_catalog,
    "claude": _claude_catalog,
    "muse": _muse_catalog,
    "hermes": _hermes_catalog,
    "opencode": _opencode_catalog,
}
_PERSISTED_AGENT_CATALOGS = frozenset(_CATALOG_LOADERS)


def load_agent_catalog(
    agent_id: str, fallback_models: Optional[List[str]], refresh: bool = False
) -> _Catalog:
    """Live rows for ``agent_id``; manifest ids when the harness has no loader or fails.

    A harness with no live loader (drop-ins, DeepSeek, Antigravity) *is* its
    manifest snapshot — that is not an error, so ``source`` stays ``manifest``.
    """
    loader = _CATALOG_LOADERS.get(str(agent_id or "").strip().lower())
    rows: List[Dict[str, Any]] = []
    source = ""
    if loader is not None:
        try:
            rows, source = loader(refresh)
        except Exception as exc:  # noqa: BLE001 — one bad harness must not sink the rest
            print(f"[agent_models] {agent_id} catalog failed: {exc}", flush=True)
            rows = []
    aid = str(agent_id or "").strip().lower()
    # Persist successful discoveries in the Cuttle home. These enrich this
    # install without rewriting the shipped adapter manifest.
    static_sources = ("static", "static_fallback", "favorites_fallback", "manifest", "cli_empty")
    durable_sources = ("cli", "cli_refresh", "cli_bundled", "config")
    discovered_rows = (
        aid in _PERSISTED_AGENT_CATALOGS and bool(rows)
        and source not in static_sources
        and (source in durable_sources or source.startswith("cli_"))
    )
    if aid in _PERSISTED_AGENT_CATALOGS and source in ("cache", "cache_stale"):
        try:
            from api.model_catalog_delta import load_catalog
            discovered_rows = discovered_rows or load_catalog(aid) is None
        except Exception:  # noqa: BLE001
            pass
    if discovered_rows:
        try:
            from api.model_catalog_delta import save_snapshot
            save_snapshot(aid, rows)
        except Exception:  # noqa: BLE001 — caching must not block catalog use
            pass
    if (
        not rows or source in ("static", "static_fallback", "favorites_fallback", "cli_empty")
    ) and aid in _CATALOG_LOADERS:
        try:
            from api.model_catalog_delta import load_catalog

            saved = load_catalog(aid)
            if saved and (not rows or source in ("static", "static_fallback", "favorites_fallback", "cli_empty")):
                rows = saved["models"]
                source = "last_successful_cache"
        except Exception:  # noqa: BLE001
            pass
    if not rows:
        rows = _rows_from_ids(list(fallback_models or []))
        if rows:
            source = "manifest"
    rows = normalize_catalog_rows(aid, rows)
    return rows, source


def list_agent_models(
    agent_id: str, *, refresh: bool = False, fallback_models: Optional[List[str]] = None
) -> Dict[str, Any]:
    """``{models, source, count}`` for one agent from the shared live store."""
    rows, source = load_agent_catalog(agent_id, fallback_models, refresh)
    return {"models": rows, "source": source, "count": len(rows)}


def list_agent_models_bulk(
    agent_ids: List[str],
    *,
    fallbacks: Optional[Dict[str, List[str]]] = None,
    refresh: bool = False,
) -> Dict[str, Dict[str, Any]]:
    """Fetch several agents' catalogs in parallel (Settings / Router page loads)."""
    fallbacks = fallbacks or {}
    ids = list(dict.fromkeys(str(a).strip() for a in agent_ids if str(a).strip()))

    def _one(aid: str) -> Tuple[str, Dict[str, Any]]:
        return aid, list_agent_models(
            aid, refresh=refresh, fallback_models=fallbacks.get(aid) or []
        )

    out: Dict[str, Dict[str, Any]] = {}
    if not ids:
        return out
    if len(ids) == 1:
        aid, res = _one(ids[0])
        out[aid] = res
        return out
    with ThreadPoolExecutor(max_workers=min(6, len(ids))) as pool:
        for aid, res in pool.map(_one, ids):
            out[aid] = res
    return out


def catalog_model_ids(rows: Any) -> List[str]:
    """Ordered, de-duplicated ids from catalog rows (dicts or bare ids)."""
    out: List[str] = []
    seen = set()
    for row in rows or []:
        mid = str((row.get("id") if isinstance(row, dict) else row) or "").strip()
        if not mid or mid.lower() in seen:
            continue
        seen.add(mid.lower())
        out.append(mid)
    return out


def catalog_model_efforts(rows: Any) -> Dict[str, List[str]]:
    """Per-model effort levels explicitly reported by a CLI catalog.

    OpenCode calls these ``variant_levels`` and pairs them with
    ``supports_variant``.
    """
    out: Dict[str, List[str]] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        mid = str(row.get("id") or "").strip()
        if "efforts" in row and isinstance(row.get("efforts"), (list, tuple)):
            levels = row["efforts"]
        elif row.get("supports_variant") is True and isinstance(
            row.get("variant_levels"), (list, tuple)
        ):
            levels = row["variant_levels"]
        elif row.get("supports_variant") is False:
            levels = []
        elif row.get("supports_effort") is False:
            levels = []
        else:
            continue
        if mid:
            out[mid] = list(
                dict.fromkeys(
                    str(level).strip().lower() for level in levels if str(level).strip()
                )
            )
    return out


def normalize_catalog_rows(agent_id: str, rows: Any) -> List[Dict[str, Any]]:
    """Give vendor rows one model-capability shape while retaining source data."""
    aid = str(agent_id or "").strip().lower()
    out: List[Dict[str, Any]] = []
    seen = set()
    for item in rows or []:
        if isinstance(item, dict):
            row = dict(item)
            mid = str(row.get("id") or "").strip()
        else:
            row = {"id": str(item or "").strip()}
            mid = row["id"]
        key = mid.lower()
        if not mid or key in seen:
            continue
        seen.add(key)
        row["id"] = mid
        if aid == "opencode" and "supports_variant" in row:
            row["supports_effort"] = bool(row["supports_variant"])
            if "variant_levels" in row:
                row["efforts"] = [
                    str(level).strip().lower()
                    for level in (row.get("variant_levels") or [])
                    if str(level).strip()
                ]
        elif "efforts" in row and isinstance(row.get("efforts"), (list, tuple)):
            row["supports_effort"] = bool(row["efforts"])
        out.append(row)
    return out


def catalog_effort_summary(
    rows: Any, fallback_supported: bool, fallback_levels: Any
) -> Tuple[bool, List[str], Dict[str, List[str]]]:
    """Effective effort support, default levels, and explicit model levels."""
    per_model = catalog_model_efforts(rows)
    if per_model:
        levels = list(dict.fromkeys(level for values in per_model.values() for level in values))
        return any(per_model.values()), levels, per_model
    return bool(fallback_supported), list(fallback_levels or []), {}


def effective_effort_options(
    live_model_levels: Dict[str, List[str]],
    model: str,
    fallback_supported: bool,
    fallback_levels: Any,
    fallback_model_levels: Optional[Dict[str, List[str]]] = None,
) -> Tuple[bool, List[str]]:
    """Resolve model-specific live effort facts, then manifest defaults."""
    model = str(model or "").strip()
    fallback_model_levels = fallback_model_levels or {}
    if model and model in live_model_levels:
        levels = list(live_model_levels[model])
        return bool(levels), levels
    if model and model in fallback_model_levels:
        levels = list(fallback_model_levels[model] or [])
        return bool(fallback_supported and levels), levels
    if live_model_levels:
        levels = list(dict.fromkeys(
            level for values in live_model_levels.values() for level in values
        ))
        return bool(levels), levels
    return bool(fallback_supported), list(fallback_levels or [])


def enrich_agent_catalog(agents: List[Dict[str, Any]], *, refresh: bool = False) -> List[Dict[str, Any]]:
    """Overwrite each available agent's ``models`` with its live catalog (Settings).

    ``GET /api/agents?live_models=1`` calls this so per-agent dropdowns match the
    chat palette instead of the manifest snapshot. Manifest ``models`` stay as
    the fallback the store returns when the harness has no live list.
    """
    available_ids = [a.get("id") for a in agents if a.get("available")]
    fallbacks = {a.get("id"): list(a.get("models") or []) for a in agents if a.get("id")}
    live = list_agent_models_bulk(available_ids, fallbacks=fallbacks, refresh=refresh)
    by_id = {a.get("id"): a for a in agents}
    for aid, res in live.items():
        row = by_id.get(aid)
        if not row:
            continue
        model_ids = catalog_model_ids(res.get("models"))
        if model_ids:
            row["models"] = model_ids
            row["models_source"] = res.get("source") or "manifest"
        efforts = catalog_model_efforts(res.get("models"))
        if efforts:
            merged = dict(row.get("model_efforts") or {})
            merged.update(efforts)
            row["model_efforts"] = merged
            row["supports_effort"] = any(efforts.values())
            row["efforts"] = list(dict.fromkeys(
                level for levels in efforts.values() for level in levels
            ))
    return agents


def enrich_router_models(
    agent_models: Dict[str, List[str]],
    agent_efforts: Dict[str, Dict[str, Any]],
    fallbacks: Dict[str, List[str]],
    *,
    refresh: bool = False,
) -> None:
    """Replace each router agent's model list with its live catalog, in place.

    ``GET /api/agent-router/options?live=1`` calls this so the Router editor's
    target pickers offer what is actually available, not the static sets.
    """
    merged_fallbacks = {k: list(v) for k, v in (fallbacks or {}).items()}
    live = list_agent_models_bulk(
        list(agent_models.keys()), fallbacks=merged_fallbacks, refresh=refresh
    )
    for aid, res in live.items():
        model_ids = catalog_model_ids(res.get("models"))
        if model_ids:
            agent_models[aid] = model_ids
        efforts = catalog_model_efforts(res.get("models"))
        if efforts:
            slot = agent_efforts.setdefault(
                aid, {"supported": True, "levels": [], "model_levels": {}}
            )
            slot.setdefault("model_levels", {}).update(efforts)
            levels = list(dict.fromkeys(
                level for values in efforts.values() for level in values
            ))
            slot["supported"] = bool(levels)
            slot["levels"] = levels

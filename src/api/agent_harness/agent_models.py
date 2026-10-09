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

    models = (
        refresh_cursor_agent_models().get("models") if refresh else list_cursor_agent_models()
    ) or []
    return [dict(m) for m in models if isinstance(m, dict)], "`agent models`"


def _codex_catalog(refresh: bool) -> _Catalog:
    from api.agent_harness.agents.codex.model_catalog import list_codex_catalog_models

    result = list_codex_catalog_models(refresh=refresh)
    return list(result.get("models") or []), "`codex debug models`"


def _claude_catalog(refresh: bool) -> _Catalog:
    from api.agent_harness.agents.claude.model_catalog import list_claude_catalog_models

    result = list_claude_catalog_models(refresh=refresh)
    return list(result.get("models") or []), "Claude Code CLI"


def _muse_catalog(refresh: bool) -> _Catalog:
    from scripts.utilities.muse_cli_tool import list_muse_catalog_models

    result = list_muse_catalog_models(refresh=refresh)
    return list(result.get("models") or []), "Meta Model API"


def _hermes_catalog(refresh: bool) -> _Catalog:
    from scripts.utilities.hermes_cli_tool import list_hermes_known_models

    return [dict(m) for m in list_hermes_known_models()], "Hermes config + curated list"


def _opencode_catalog(refresh: bool) -> _Catalog:
    from api.agent_harness.agents.opencode.model_catalog import list_opencode_catalog_models

    result = list_opencode_catalog_models(refresh=refresh)
    return list(result.get("models") or []), "`opencode models --verbose`"


_CATALOG_LOADERS: Dict[str, Callable[[bool], _Catalog]] = {
    "cursor": _cursor_catalog,
    "codex": _codex_catalog,
    "claude": _claude_catalog,
    "muse": _muse_catalog,
    "hermes": _hermes_catalog,
    "opencode": _opencode_catalog,
}


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
    if not rows:
        rows = _rows_from_ids(list(fallback_models or []))
        if rows:
            source = "manifest"
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
    """Per-model effort levels from live rows that carry them (Claude / Codex)."""
    out: Dict[str, List[str]] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        mid = str(row.get("id") or "").strip()
        levels = row.get("efforts")
        if mid and isinstance(levels, (list, tuple)) and levels:
            out[mid] = [str(level).strip().lower() for level in levels if str(level).strip()]
    return out

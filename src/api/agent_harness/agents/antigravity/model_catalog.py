"""Antigravity model catalog — ``agy models`` grouped into model + effort rows.

``agy models`` prints one ``slug<TAB>label`` row per *variant*
(``gemini-3.8-flash-low  Gemini 3.8 Flash (Low)``). ``agy -p`` accepts the
family slug plus ``--effort`` (``--model gemini-3.8-flash --effort low``) and
rejects a suffixed slug combined with ``--effort``. Cuttle therefore lists one
row per family with its verified effort levels — the same model + effort shape
as Codex and Claude — and passes ``--effort`` only for families that have one.
"""

from __future__ import annotations

import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from core.agent_cli_env import agent_cli_env

EFFORT_ORDER = ("low", "medium", "high", "xhigh", "max")
_EFFORT_SUFFIX = re.compile(r"^(?P<family>.+)-(?P<effort>low|medium|high|xhigh|max)$", re.I)
_LABEL_SUFFIX = re.compile(r"\s*\((low|medium|high|xhigh|max)\)\s*$", re.I)
_CATALOG_CACHE_TTL_SEC = 300.0
_LOCK = threading.Lock()
_CATALOG_CACHE: Optional[Tuple[float, List[Dict[str, Any]], str, Optional[str]]] = None


def _ordered(levels: Any) -> List[str]:
    seen = {str(level).strip().lower() for level in (levels or [])}
    return [level for level in EFFORT_ORDER if level in seen]


def split_effort_slug(slug: Optional[str]) -> Tuple[str, str]:
    """``gemini-3.8-flash-low`` → (``gemini-3.8-flash``, ``low``); otherwise (slug, "")."""
    text = str(slug or "").strip()
    m = _EFFORT_SUFFIX.match(text)
    if not m:
        return text, ""
    return m.group("family"), m.group("effort").lower()


def parse_agy_models(text: str) -> List[Dict[str, Any]]:
    """Group ``agy models`` variant rows into family rows (order preserved)."""
    rows: Dict[str, Dict[str, Any]] = {}
    for line in (text or "").splitlines():
        if "\t" not in line:
            continue
        slug, _tab, label = line.partition("\t")
        slug, label = slug.strip(), label.strip()
        if not slug or " " in slug:
            continue
        family, effort = split_effort_slug(slug)
        row = rows.setdefault(family, {
            "id": family,
            "label": _LABEL_SUFFIX.sub("", label) or family,
            "efforts": [],
        })
        if effort:
            row["efforts"].append(effort)
    out = []
    for row in rows.values():
        row["efforts"] = _ordered(row["efforts"])
        out.append(row)
    return out


def _manifest_rows() -> List[Dict[str, Any]]:
    """Verified offline model/effort snapshot from this adapter's manifest."""
    try:
        manifest = yaml.safe_load(
            Path(__file__).with_name("manifest.yaml").read_text(encoding="utf-8")
        ) or {}
    except (OSError, yaml.YAMLError):
        manifest = {}
    model_efforts = manifest.get("model_efforts") if isinstance(manifest.get("model_efforts"), dict) else {}
    rows = []
    for mid in manifest.get("models") or []:
        mid = str(mid or "").strip()
        if mid:
            rows.append({"id": mid, "label": mid, "efforts": _ordered(model_efforts.get(mid))})
    return rows


def _run_agy_models() -> Tuple[List[Dict[str, Any]], Optional[str]]:
    from api.agent_harness.agents.antigravity.adapter import antigravity_executable

    exe = antigravity_executable()
    if not exe:
        return [], "Antigravity CLI not found"
    try:
        proc = subprocess.run(
            [exe, "models"], capture_output=True, text=True, timeout=45,
            stdin=subprocess.DEVNULL, env=agent_cli_env(),
            encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [], f"`agy models` failed: {type(exc).__name__}"
    text = (proc.stdout or "") + "\n" + (proc.stderr or "")
    # ``agy models`` exits 0 while signed out; the message is the only signal.
    if "sign in" in text.lower():
        return [], "Antigravity is not signed in (run `agy` once interactively)"
    rows = parse_agy_models(proc.stdout or "")
    if rows:
        return rows, None
    return [], "`agy models` returned no models"


def clear_antigravity_catalog_cache() -> None:
    global _CATALOG_CACHE
    _CATALOG_CACHE = None


def list_antigravity_catalog_models(*, refresh: bool = False) -> Dict[str, Any]:
    """Palette-ready family rows (live ``agy models``; manifest offline fallback)."""
    global _CATALOG_CACHE
    with _LOCK:
        now = time.monotonic()
        if refresh:
            clear_antigravity_catalog_cache()
        elif _CATALOG_CACHE is not None and now - _CATALOG_CACHE[0] < _CATALOG_CACHE_TTL_SEC:
            ts, rows, source, err = _CATALOG_CACHE
            return {"models": [dict(r) for r in rows], "count": len(rows),
                    "source": source, "error": err, "fetched_at": ts}
        live, err = _run_agy_models()
        rows, source = (live, "cli") if live else (_manifest_rows(), "static_fallback")
        _CATALOG_CACHE = (now, list(rows), source, err)
        return {"models": [dict(r) for r in rows], "count": len(rows),
                "source": source, "error": err, "fetched_at": now}


def refresh_antigravity_catalog() -> Dict[str, Any]:
    return list_antigravity_catalog_models(refresh=True)


def _cached_rows() -> List[Dict[str, Any]]:
    if _CATALOG_CACHE is not None:
        return list(_CATALOG_CACHE[1])
    return _manifest_rows()


def antigravity_model_label(model: Optional[str]) -> str:
    mid = str(model or "").strip()
    if not mid:
        return ""
    family, effort = split_effort_slug(mid)
    for row in _cached_rows():
        if str(row.get("id") or "").lower() == family.lower():
            label = str(row.get("label") or mid)
            return f"{label} ({effort.title()})" if effort else label
    return mid


def antigravity_efforts_for_model(model: Optional[str] = None) -> List[str]:
    """Verified ``--effort`` levels for a family; a suffixed slug takes none.

    Unpinned (the CLI's own default model) → the union of known levels, since
    ``agy`` validates the pair itself and rejects an unsupported one for free.
    """
    mid = str(model or "").strip().lower()
    if not mid:
        levels: List[str] = []
        for row in list_antigravity_catalog_models().get("models") or []:
            levels.extend(row.get("efforts") or [])
        return _ordered(levels)
    family, suffix = split_effort_slug(mid)
    # Validation must use the installed CLI's levels: a cold cache loads the
    # live catalog (manifest only when `agy models` is unavailable).
    rows_live = list_antigravity_catalog_models().get("models") or []
    for rows in (rows_live, _manifest_rows()):
        for row in rows:
            rid = str(row.get("id") or "").lower()
            if rid == mid:
                return list(row.get("efforts") or [])
            if suffix and rid == family:
                return []  # effort is baked into the slug
    return []


"""OpenCode model catalog for the Cuttle slash palette.

Source of truth: ``opencode models --verbose`` (models.dev). Curated
``OPENCODE_KNOWN_MODELS`` are soft favorites pinned to the top — not an
allowlist. Refresh with ``opencode models --refresh --verbose``.

Per-model ``supports_variant`` is baked from each entry's ``variants`` map
(OpenCode ``--variant`` / reasoning-effort overlays). Manifest
``model_capabilities`` remain optional overrides for known CLI bugs.
"""

from __future__ import annotations

from core.agent_cli_env import agent_cli_env

import json
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.runtime_paths import runtime_cache_path

_lock = threading.Lock()

# Soft TTL for the on-disk cache; ``refresh=True`` bypasses and rewrites.
_CACHE_TTL_SEC = 6 * 60 * 60
_CACHE_VERSION = 3
_CLI_TIMEOUT_SEC = 90

_MODEL_ID_RE = re.compile(
    r"^(?P<id>[a-zA-Z0-9][\w.~+/-]*[a-zA-Z0-9]|[a-zA-Z0-9]+)$"
)



def _cache_path() -> Path:
    return runtime_cache_path("opencode_models_cache.json")


def _label_from_id(model_id: str) -> str:
    mid = (model_id or "").strip()
    if not mid:
        return ""
    # openrouter/deepseek/deepseek-v4.1-flash → Deepseek V4.1 Flash
    leaf = mid.split("/")[-1]
    # Drop trailing :free / :nitro style suffixes from the display leaf only.
    leaf = leaf.split(":", 1)[0]
    parts = re.split(r"[-_]+", leaf)
    nice: List[str] = []
    for part in parts:
        if not part:
            continue
        if part.lower() in ("gpt", "glm", "ai"):
            nice.append(part.upper())
        elif re.fullmatch(r"v?\d+(\.\d+)*", part, flags=re.I):
            nice.append(part.upper() if part[0].lower() != "v" else "V" + part[1:])
        else:
            nice.append(part[:1].upper() + part[1:])
    return " ".join(nice) or mid


def _row_from_meta(full_id: str, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    mid = (full_id or "").strip()
    meta = meta if isinstance(meta, dict) else {}
    caps = meta.get("capabilities") if isinstance(meta.get("capabilities"), dict) else {}
    variants = meta.get("variants") if isinstance(meta.get("variants"), dict) else {}
    has_variants = bool(variants)
    reasoning = bool(caps.get("reasoning")) if caps else False
    name = str(meta.get("name") or "").strip()
    levels = [str(k) for k in variants.keys() if str(k).strip()]
    row: Dict[str, Any] = {
        "id": mid,
        "label": name or _label_from_id(mid),
        "description": "OpenCode catalog (models.dev)",
        "supports_variant": has_variants,
        "reasoning": reasoning or has_variants,
        "variant_levels": levels,
    }
    cost = _cost_from_meta(meta.get("cost"))
    if cost:
        row["cost"] = cost
    limit = meta.get("limit") if isinstance(meta.get("limit"), dict) else {}
    try:
        ctx = int(limit.get("context") or 0)
    except (TypeError, ValueError):
        ctx = 0
    if ctx > 0:
        row["context"] = ctx
    return row


def _cost_from_meta(raw: Any) -> Dict[str, float]:
    """``opencode models --verbose`` cost block → flat USD-per-1M rates."""
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, float] = {}
    for key in ("input", "output"):
        try:
            if raw.get(key) is not None:
                out[key] = float(raw[key])
        except (TypeError, ValueError):
            continue
    cache = raw.get("cache") if isinstance(raw.get("cache"), dict) else {}
    for src, dst in (("read", "cache_read"), ("write", "cache_write")):
        val = cache.get(src, raw.get(dst))
        try:
            if val is not None:
                out[dst] = float(val)
        except (TypeError, ValueError):
            continue
    if "input" not in out or "output" not in out:
        return {}
    return out


def _parse_models_stdout(text: str) -> List[Dict[str, Any]]:
    """Parse plain ``opencode models`` (one id per line) or ``--verbose`` blocks."""
    raw = text or ""
    # Verbose: ``provider/model`` line followed by a JSON object.
    if '"providerID"' in raw or '"variants"' in raw or '"capabilities"' in raw:
        return _parse_verbose_models_stdout(raw)
    rows: List[Dict[str, Any]] = []
    seen = set()
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.lower().startswith("available"):
            continue
        if " " in s and "/" not in s.split()[0]:
            continue
        mid = s.split()[0].strip()
        if not mid or mid.lower() in ("provider", "id", "model"):
            continue
        if not _MODEL_ID_RE.match(mid):
            continue
        key = mid.lower()
        if key in seen:
            continue
        seen.add(key)
        rows.append(_row_from_meta(mid))
    return rows


def _parse_verbose_models_stdout(text: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    seen = set()
    lines = (text or "").splitlines()
    i = 0
    decoder = json.JSONDecoder()
    while i < len(lines):
        s = lines[i].strip()
        i += 1
        if not s or s.startswith("{") or s.startswith("}"):
            continue
        if not _MODEL_ID_RE.match(s.split()[0]):
            continue
        mid = s.split()[0].strip()
        # Skip until a JSON object starts (tolerate blank lines).
        while i < len(lines) and not lines[i].strip():
            i += 1
        if i >= len(lines) or not lines[i].strip().startswith("{"):
            key = mid.lower()
            if key not in seen:
                seen.add(key)
                rows.append(_row_from_meta(mid))
            continue
        blob_start = i
        # Accumulate until we can raw_decode a full object.
        buf = ""
        parsed = None
        while i < len(lines):
            buf += lines[i] + "\n"
            i += 1
            try:
                parsed, _end = decoder.raw_decode(buf.strip())
                break
            except json.JSONDecodeError:
                if i - blob_start > 4000:
                    break
                continue
        meta = parsed if isinstance(parsed, dict) else None
        key = mid.lower()
        if key in seen:
            continue
        seen.add(key)
        rows.append(_row_from_meta(mid, meta))
    return rows


def _read_cache() -> Optional[Dict[str, Any]]:
    path = _cache_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        ver = int(data.get("version") or 1)
    except (TypeError, ValueError):
        ver = 1
    if ver < _CACHE_VERSION:
        return None
    return data


def _write_cache(models: Sequence[Dict[str, Any]], *, refreshed: bool) -> None:
    payload = {
        "version": _CACHE_VERSION,
        "fetched_at": time.time(),
        "refreshed": bool(refreshed),
        "count": len(models),
        "models": list(models),
    }
    path = _cache_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


def _run_opencode_models(*, refresh: bool = False) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    from api.agent_harness.agents.opencode.adapter import opencode_executable

    exe = opencode_executable()
    if not exe:
        return [], "OpenCode CLI not found on PATH"
    # Always verbose so we bake supports_variant from the ``variants`` map.
    cmd = [exe, "models", "--verbose"]
    if refresh:
        cmd.append("--refresh")
    try:
        proc = subprocess.run(
            cmd,
                   env=agent_cli_env(),
            capture_output=True,
            text=True,
            timeout=_CLI_TIMEOUT_SEC,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return [], "opencode models timed out"
    except OSError as exc:
        return [], f"opencode models failed: {exc}"
    out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    rows = _parse_models_stdout(out)
    if not rows and proc.returncode:
        err = (proc.stderr or proc.stdout or f"exit {proc.returncode}").strip()
        return [], err[:300] or f"opencode models failed (exit {proc.returncode})"
    return rows, None


def _curated_favorites() -> List[Dict[str, Any]]:
    from api.agent_harness.agents.opencode.adapter import OPENCODE_KNOWN_MODELS

    out: List[Dict[str, Any]] = []
    for row in OPENCODE_KNOWN_MODELS:
        mid = str(row.get("id") or "").strip()
        if not mid:
            continue
        out.append(
            {
                "id": mid,
                "label": str(row.get("label") or _label_from_id(mid)),
                "description": "Curated OpenCode favorite",
                "favorite": "1",
            }
        )
    return out


def _merge_favorites(
    catalog: Sequence[Dict[str, Any]], favorites: Sequence[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    by_id: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for row in list(favorites) + list(catalog):
        mid = str(row.get("id") or "").strip()
        if not mid:
            continue
        key = mid.lower()
        if key not in by_id:
            order.append(key)
            by_id[key] = dict(row)
            continue
        existing = by_id[key]
        if row.get("favorite") and not existing.get("favorite"):
            existing["favorite"] = row["favorite"]
        if row.get("label") and str(existing.get("description") or "").startswith(
            "OpenCode catalog"
        ):
            existing["label"] = str(row["label"])
        if row.get("description") and existing.get("favorite"):
            existing["description"] = str(row["description"])
        # Catalog capability fields always win when the catalog row provides them.
        if "supports_variant" in row:
            existing["supports_variant"] = row["supports_variant"]
        if "reasoning" in row:
            existing["reasoning"] = row["reasoning"]
        if row.get("variant_levels"):
            existing["variant_levels"] = row["variant_levels"]
        for key in ("cost", "context"):
            if row.get(key) and not existing.get(key):
                existing[key] = row[key]
    return [by_id[k] for k in order]


def list_opencode_catalog_models(
    *,
    refresh: bool = False,
    provider: Optional[str] = None,
    q: Optional[str] = None,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """Return ``{models, source, error, fetched_at, count}`` for the palette/API."""
    favorites = _curated_favorites()
    err: Optional[str] = None
    source = "cache"
    fetched_at = 0.0
    models: List[Dict[str, Any]] = []

    with _lock:
        cached = None if refresh else _read_cache()
        if cached and isinstance(cached.get("models"), list):
            age = time.time() - float(cached.get("fetched_at") or 0)
            if age <= _CACHE_TTL_SEC and cached["models"]:
                models = [m for m in cached["models"] if isinstance(m, dict)]
                fetched_at = float(cached.get("fetched_at") or 0)
                source = "cache"

        if refresh or not models:
            live, live_err = _run_opencode_models(refresh=refresh)
            if live:
                models = live
                fetched_at = time.time()
                source = "cli_refresh" if refresh else "cli"
                try:
                    _write_cache(models, refreshed=refresh)
                except OSError:
                    pass
            else:
                err = live_err
                if not models and cached and isinstance(cached.get("models"), list):
                    models = [m for m in cached["models"] if isinstance(m, dict)]
                    fetched_at = float(cached.get("fetched_at") or 0)
                    source = "cache_stale"
                if not models:
                    models = favorites
                    source = "favorites_fallback"
                    fetched_at = time.time()

    merged = _merge_favorites(models, favorites)

    prov = (provider or "").strip().lower().rstrip("/")
    if prov:
        merged = [
            m
            for m in merged
            if str(m.get("id") or "").lower().startswith(prov + "/")
            or str(m.get("id") or "").lower() == prov
        ]

    query = (q or "").strip().lower()
    if query:
        tokens = [t for t in re.split(r"\s+", query) if t]
        filtered: List[Dict[str, Any]] = []
        for m in merged:
            hay = " ".join(
                [
                    str(m.get("id") or ""),
                    str(m.get("label") or ""),
                    str(m.get("description") or ""),
                ]
            ).lower()
            compact = re.sub(r"[\s._/-]+", "", hay)
            if all(
                tok in hay or re.sub(r"[\s._/-]+", "", tok) in compact for tok in tokens
            ):
                filtered.append(m)
        merged = filtered

    total = len(merged)
    if limit is not None and limit > 0:
        merged = merged[: int(limit)]

    return {
        "models": merged,
        "source": source,
        "error": err,
        "fetched_at": fetched_at,
        "count": total,
        "returned": len(merged),
    }


def refresh_opencode_catalog() -> Dict[str, Any]:
    """Force ``opencode models --refresh --verbose`` and rewrite the cache."""
    result = list_opencode_catalog_models(refresh=True)
    # Same refresh gesture also refreshes models.dev pricing for bubble cost estimates.
    try:
        from api.model_pricing import refresh_models_dev_pricing

        pricing = refresh_models_dev_pricing(force=True)
        result["pricing_count"] = pricing.get("count")
        result["pricing_refreshed"] = True
    except Exception as exc:
        result["pricing_refreshed"] = False
        result["pricing_error"] = str(exc)[:200]
    return result


def _normalize_model_key(model: Optional[str]) -> str:
    return (str(model or "").strip()).lower()


def lookup_catalog_model(model: Optional[str]) -> Optional[Dict[str, Any]]:
    """Return the cached catalog row for ``model``, or None."""
    key = _normalize_model_key(model)
    if not key:
        return None
    # Ensure we have a catalog (may hit CLI once).
    result = list_opencode_catalog_models()
    for row in result.get("models") or []:
        if not isinstance(row, dict):
            continue
        mid = _normalize_model_key(row.get("id"))
        if mid == key:
            return row
        # Allow lookup by leaf id (z-ai/glm-5.3-flash vs openrouter/z-ai/…).
        if mid.endswith("/" + key) or key.endswith("/" + mid):
            return row
    return None


def catalog_supports_variant(model: Optional[str]) -> Optional[bool]:
    """True/False from baked catalog; None if unknown / not listed."""
    row = lookup_catalog_model(model)
    if row is None:
        return None
    if "supports_variant" not in row:
        return None
    return bool(row.get("supports_variant"))

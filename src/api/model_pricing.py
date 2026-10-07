"""models.dev pricing cache for per-turn cost estimates.

Prefer CLI-reported ``cost`` when present. When only tokens are available,
estimate USD from the models.dev catalog (``cost.input`` / ``cost.output`` /
``cost.cache_read`` / ``cost.cache_write`` per 1M tokens). Cache lives on disk
so chat turns never depend on a live network call; refresh explicitly
(e.g. OpenCode model refresh) or lazily in the background when the cache is
missing/stale.
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.runtime_paths import runtime_cache_path

_MODELS_DEV_URL = "https://models.dev/api.json"
_CACHE_VERSION = 2
_CACHE_TTL_SEC = 24 * 60 * 60
_FETCH_TIMEOUT_SEC = 20

# Fallback multipliers when models.dev omits cache rates (OpenAI/Anthropic norms).
_DEFAULT_CACHE_READ_MULT = 0.1
_DEFAULT_CACHE_WRITE_MULT = 1.25

_lock = threading.Lock()
_index_lock = threading.Lock()
_refresh_lock = threading.Lock()
_refresh_thread: Optional[threading.Thread] = None

# In-memory index: normalized key → rates dict
# {input, output, provider, model_id, cache_read?, cache_write?}
_index: Dict[str, Dict[str, Any]] = {}
# Parallel index: normalized key → context limit tokens
_context_index: Dict[str, int] = {}
_index_loaded_at: float = 0.0

_SKIP_MODEL_IDS = frozenset(
    {
        "",
        "auto",
        "default",
        "composer",
        "composer-1",
        "cursor",
        "cursor-small",
    }
)


def _repo_root() -> Path:
    # .../src/api/model_pricing.py → Cuttle
    return Path(__file__).resolve().parents[2]


def _cache_path() -> Path:
    return runtime_cache_path("models_dev_pricing_cache.json", project_root=_repo_root())


def _normalize_key(value: str) -> str:
    s = (value or "").strip().lower()
    s = s.replace("\\", "/")
    s = re.sub(r"[_\s]+", "-", s)
    return s


def _leaf(model_id: str) -> str:
    mid = (model_id or "").strip()
    if "/" in mid:
        mid = mid.rsplit("/", 1)[-1]
    if ":" in mid:
        mid = mid.split(":", 1)[0]
    return mid


def _read_cache() -> Optional[Dict[str, Any]]:
    path = _cache_path()
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8-sig")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        ver = int(data.get("version") or 0)
    except (TypeError, ValueError):
        ver = 0
    if ver < _CACHE_VERSION:
        return None
    return data


def _write_cache(entries: List[Dict[str, Any]], *, source: str) -> None:
    payload = {
        "version": _CACHE_VERSION,
        "fetched_at": time.time(),
        "source": source,
        "count": len(entries),
        "entries": entries,
    }
    path = _cache_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


def _parse_catalog(catalog: Dict[str, Any]) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    if not isinstance(catalog, dict):
        return entries
    for provider_id, provider in catalog.items():
        if not isinstance(provider, dict):
            continue
        models = provider.get("models")
        if not isinstance(models, dict):
            continue
        pid = str(provider.get("id") or provider_id or "").strip()
        for model_id, meta in models.items():
            if not isinstance(meta, dict):
                continue
            cost = meta.get("cost")
            if not isinstance(cost, dict):
                continue
            try:
                inp = float(cost.get("input"))
                out = float(cost.get("output"))
            except (TypeError, ValueError):
                continue
            if inp < 0 or out < 0:
                continue
            # Zero/zero is usually a placeholder — skip for estimates.
            if inp == 0 and out == 0:
                continue
            mid = str(model_id or "").strip()
            if not mid:
                continue
            name = str(meta.get("name") or "").strip()
            limit = meta.get("limit") if isinstance(meta.get("limit"), dict) else {}
            context_limit = None
            try:
                ctx = limit.get("context") if isinstance(limit, dict) else None
                if ctx is not None:
                    context_limit = int(ctx)
                    if context_limit <= 0:
                        context_limit = None
            except (TypeError, ValueError):
                context_limit = None
            row = {
                "provider": pid,
                "model_id": mid,
                "name": name,
                "input": inp,
                "output": out,
            }
            for cache_key in ("cache_read", "cache_write"):
                raw_c = cost.get(cache_key)
                if raw_c is None:
                    continue
                try:
                    rate = float(raw_c)
                except (TypeError, ValueError):
                    continue
                if rate >= 0:
                    row[cache_key] = rate
            if context_limit:
                row["context"] = context_limit
            entries.append(row)
    return entries


def fetch_models_dev_catalog() -> Dict[str, Any]:
    req = urllib.request.Request(
        _MODELS_DEV_URL,
        headers={"User-Agent": "Cuttle-ModelPricing/1.0", "Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT_SEC) as resp:
        raw = resp.read()
    text = raw.decode("utf-8-sig", errors="replace")
    data = json.loads(text) if text.strip() else {}
    if not isinstance(data, dict):
        raise ValueError("models.dev catalog was not a JSON object")
    return data


def refresh_models_dev_pricing(*, force: bool = True) -> Dict[str, Any]:
    """Download models.dev and rewrite the on-disk pricing cache."""
    with _refresh_lock:
        catalog = fetch_models_dev_catalog()
        entries = _parse_catalog(catalog)
        _write_cache(entries, source=_MODELS_DEV_URL)
        _rebuild_index(entries)
        return {
            "success": True,
            "count": len(entries),
            "fetched_at": time.time(),
            "force": bool(force),
        }


def ensure_models_dev_pricing(*, refresh_if_stale: bool = True) -> bool:
    """Load cache into memory; optionally kick a background refresh if stale."""
    data = _read_cache()
    now = time.time()
    stale = True
    if data:
        try:
            fetched = float(data.get("fetched_at") or 0)
        except (TypeError, ValueError):
            fetched = 0.0
        stale = (now - fetched) > _CACHE_TTL_SEC
        entries = data.get("entries")
        if isinstance(entries, list) and entries:
            _rebuild_index(entries)
            if not stale:
                return True
    if refresh_if_stale:
        _schedule_background_refresh(blocking_if_empty=not bool(_index))
    return bool(_index)


def _schedule_background_refresh(*, blocking_if_empty: bool = False) -> None:
    global _refresh_thread
    with _lock:
        if _refresh_thread and _refresh_thread.is_alive():
            t = _refresh_thread
        else:
            t = threading.Thread(
                target=_safe_refresh,
                name="models-dev-pricing-refresh",
                daemon=True,
            )
            _refresh_thread = t
            t.start()
    if blocking_if_empty and t is not None:
        t.join(timeout=_FETCH_TIMEOUT_SEC + 2)


def _safe_refresh() -> None:
    try:
        refresh_models_dev_pricing(force=True)
    except Exception as exc:
        print(f"[model_pricing] refresh failed: {exc}", flush=True)


def _rebuild_index(entries: List[Dict[str, Any]]) -> None:
    global _index, _context_index, _index_loaded_at
    # Prefer canonical providers when leaf names collide.
    preferred = {
        "anthropic": 0,
        "openai": 1,
        "google": 2,
        "meta": 3,
        "xai": 4,
        "mistral": 5,
    }
    ranked: Dict[str, Tuple[int, Dict[str, Any]]] = {}
    ctx_ranked: Dict[str, Tuple[int, int]] = {}
    for row in entries:
        if not isinstance(row, dict):
            continue
        try:
            inp = float(row.get("input"))
            out = float(row.get("output"))
        except (TypeError, ValueError):
            continue
        provider = str(row.get("provider") or "").strip()
        mid = str(row.get("model_id") or "").strip()
        name = str(row.get("name") or "").strip()
        if not mid:
            continue
        rank = preferred.get(provider.lower(), 50)
        provider_key = _normalize_key(f"{provider}/{mid}")
        keys = {
            _normalize_key(mid),
            provider_key,
            _normalize_key(_leaf(mid)),
        }
        if name:
            keys.add(_normalize_key(name))
            keys.add(_normalize_key(name.replace(" ", "-")))
        context_limit = None
        try:
            raw_ctx = row.get("context")
            if raw_ctx is not None:
                context_limit = int(raw_ctx)
                if context_limit <= 0:
                    context_limit = None
        except (TypeError, ValueError):
            context_limit = None
        rates: Dict[str, Any] = {
            "input": inp,
            "output": out,
            "provider": provider,
            "model_id": mid,
        }
        if name:
            rates["name"] = name
        if context_limit:
            rates["context"] = context_limit
        for cache_key in ("cache_read", "cache_write"):
            if row.get(cache_key) is None:
                continue
            try:
                rates[cache_key] = float(row.get(cache_key))
            except (TypeError, ValueError):
                continue
        for key in keys:
            if not key:
                continue
            # ``deepseek/deepseek-v4-flash`` is both the DeepSeek provider key and
            # a reseller's slash-bearing model id — the real provider must win.
            key_rank = rank - 1000 if key == provider_key else rank
            prev = ranked.get(key)
            if prev is None or key_rank < prev[0]:
                ranked[key] = (key_rank, rates)
            if context_limit:
                prev_ctx = ctx_ranked.get(key)
                if prev_ctx is None or key_rank < prev_ctx[0]:
                    ctx_ranked[key] = (key_rank, context_limit)
    built = {k: dict(rates) for k, (_rank, rates) in ranked.items()}
    built_ctx = {k: ctx for k, (_rank, ctx) in ctx_ranked.items()}
    with _index_lock:
        _index = built
        _context_index = built_ctx
        _index_loaded_at = time.time()


def lookup_model_rates(model: str) -> Optional[Dict[str, Any]]:
    """Return rates per 1M tokens, or None.

    Keys: ``input``, ``output``, ``provider``, ``model_id``, and when known
    ``cache_read`` / ``cache_write``.
    """
    ensure_models_dev_pricing(refresh_if_stale=True)
    mid = (model or "").strip()
    if not mid or _normalize_key(mid) in _SKIP_MODEL_IDS:
        return None
    candidates = [
        _normalize_key(mid),
        _normalize_key(_leaf(mid)),
    ]
    # Drop trailing context/size suffixes Cursor sometimes appends.
    leaf = _leaf(mid)
    leaf_base = re.sub(
        r"[-_](\d+k|\d+m|thinking|fast|high|medium|low|max)$",
        "",
        leaf,
        flags=re.I,
    )
    if leaf_base and leaf_base != leaf:
        candidates.append(_normalize_key(leaf_base))
    with _index_lock:
        idx = dict(_index)
    for key in candidates:
        hit = idx.get(key)
        if hit:
            return dict(hit)
    return None


# Trailing id tokens harnesses bake into model ids (effort / speed / thinking /
# context size) that models.dev lists under the bare model id.
_VARIANT_SUFFIX_TOKENS = frozenset(
    {
        "fast",
        "thinking",
        "nothink",
        "none",
        "minimal",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
        "ultra",
    }
)


def _is_variant_token(token: str) -> bool:
    t = (token or "").strip().lower()
    return t in _VARIANT_SUFFIX_TOKENS or bool(re.fullmatch(r"\d+[km]", t))


def strip_variant_suffixes(model_id: str) -> str:
    """``cursor-grok-4.6-high-fast`` → ``grok-4.6`` (id tokens only, no lookup)."""
    base = re.sub(r"\[.*\]$", "", (model_id or "").strip()).strip()
    base = _leaf(base)
    if base.lower().startswith("cursor-"):
        base = base[len("cursor-") :]
    tokens = base.split("-")
    while len(tokens) > 1 and _is_variant_token(tokens[-1]):
        tokens.pop()
    return "-".join(tokens)


def pricing_lookup_candidates(model_id: str) -> List[str]:
    """Most-specific-first ids to try: exact → leaf → progressively stripped."""
    raw = re.sub(r"\[.*\]$", "", (model_id or "").strip()).strip()
    if not raw:
        return []
    out: List[str] = []

    def _add(value: str) -> None:
        key = _normalize_key(value)
        if key and key not in out:
            out.append(key)

    bases = [raw, _leaf(raw)]
    for base in list(bases):
        if _leaf(base).lower().startswith("cursor-"):
            bases.append(_leaf(base)[len("cursor-") :])
    for base in bases:
        _add(base)
        tokens = base.split("-")
        while len(tokens) > 1 and _is_variant_token(tokens[-1]):
            tokens.pop()
            _add("-".join(tokens))
    return out


def lookup_model_pricing(
    model: str,
    *,
    providers: Optional[List[str]] = None,
    refresh_if_stale: bool = True,
) -> Optional[Dict[str, Any]]:
    """Rates per 1M tokens for a harness model id, preferring ``providers``.

    Unlike :func:`lookup_model_rates`, this tolerates harness-baked suffixes
    (``-high``, ``-fast``, ``-thinking``, ``cursor-`` prefix) by trying
    progressively stripped ids, and checks ``{provider}/{id}`` keys first so a
    Codex model resolves to OpenAI list price rather than a reseller row.
    Adds ``matched`` (the index key that hit).
    """
    ensure_models_dev_pricing(refresh_if_stale=refresh_if_stale)
    mid = (model or "").strip()
    if not mid or _normalize_key(mid) in _SKIP_MODEL_IDS:
        return None
    provs = [_normalize_key(p) for p in (providers or []) if str(p or "").strip()]
    with _index_lock:
        idx = dict(_index)
    for cand in pricing_lookup_candidates(mid):
        if cand in _SKIP_MODEL_IDS:
            continue
        for prov in provs:
            # Vendors often list only the ``-preview`` id (Google Gemini).
            for key in (f"{prov}/{cand}", f"{prov}/{cand}-preview"):
                hit = idx.get(key)
                if hit and _normalize_key(str(hit.get("provider") or "")) == prov:
                    return {**hit, "matched": key}
        hit = idx.get(cand)
        if hit:
            return {**hit, "matched": cand}
    return None


def list_provider_pricing(provider: str) -> List[Dict[str, Any]]:
    """Unique models.dev rows for one provider (e.g. resolve Claude aliases)."""
    ensure_models_dev_pricing(refresh_if_stale=True)
    prov = _normalize_key(provider)
    if not prov:
        return []
    with _index_lock:
        idx = dict(_index)
    seen: Dict[str, Dict[str, Any]] = {}
    for key, rates in idx.items():
        if not key.startswith(prov + "/"):
            continue
        if _normalize_key(str(rates.get("provider") or "")) != prov:
            continue
        mid = str(rates.get("model_id") or "")
        if mid and mid not in seen:
            seen[mid] = dict(rates)
    return list(seen.values())


def pricing_cache_info() -> Dict[str, Any]:
    """``{fetched_at, count, source}`` of the on-disk models.dev cache (or empty)."""
    data = _read_cache() or {}
    try:
        fetched = float(data.get("fetched_at") or 0)
    except (TypeError, ValueError):
        fetched = 0.0
    return {
        "fetched_at": fetched,
        "count": int(data.get("count") or 0),
        "source": str(data.get("source") or ""),
    }


def extract_cache_token_counts(usage: Optional[Dict[str, Any]]) -> Tuple[int, int]:
    """Return ``(cache_read_tokens, cache_write_tokens)`` from heterogeneous CLI keys."""
    u = usage if isinstance(usage, dict) else {}

    def _pick(*keys: str) -> int:
        for k in keys:
            if u.get(k) is None:
                continue
            try:
                return max(0, int(u.get(k) or 0))
            except (TypeError, ValueError):
                continue
        return 0

    cache_read = _pick(
        "cache_read_tokens",
        "cacheReadTokens",
        "cached_input_tokens",
        "cached_tokens",
        "cache_read_input_tokens",
        "cacheReadInputTokens",
        "cache_read",
    )
    cache_write = _pick(
        "cache_write_tokens",
        "cacheWriteTokens",
        "cache_write_input_tokens",
        "cache_creation_input_tokens",
        "cacheCreationInputTokens",
        "cache_write",
    )
    # Nested OpenCode-style ``tokens.cache.{read,write}``
    nested = u.get("cache")
    if isinstance(nested, dict):
        if cache_read <= 0 and nested.get("read") is not None:
            try:
                cache_read = max(0, int(nested.get("read") or 0))
            except (TypeError, ValueError):
                pass
        if cache_write <= 0 and nested.get("write") is not None:
            try:
                cache_write = max(0, int(nested.get("write") or 0))
            except (TypeError, ValueError):
                pass
    return cache_read, cache_write


def lookup_model_context_limit(model: str) -> Optional[int]:
    """Return models.dev ``limit.context`` tokens for ``model``, or None."""
    ensure_models_dev_pricing(refresh_if_stale=True)
    mid = (model or "").strip()
    if not mid or _normalize_key(mid) in _SKIP_MODEL_IDS:
        return None
    # Strip Cursor bracket overrides: model[context=1m,…]
    bare = re.sub(r"\[.*\]$", "", mid).strip() or mid
    candidates = [
        _normalize_key(bare),
        _normalize_key(_leaf(bare)),
        _normalize_key(mid),
        _normalize_key(_leaf(mid)),
    ]
    leaf = _leaf(bare)
    leaf_base = re.sub(
        r"[-_](\d+k|\d+m|thinking|fast|high|medium|low|max)$",
        "",
        leaf,
        flags=re.I,
    )
    if leaf_base and leaf_base != leaf:
        candidates.append(_normalize_key(leaf_base))
    with _index_lock:
        idx = dict(_context_index)
    for key in candidates:
        hit = idx.get(key)
        if hit and int(hit) > 0:
            return int(hit)
    return None


def estimate_cost_usd(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    *,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    cache_inclusive: Optional[bool] = None,
) -> Optional[float]:
    """Estimate USD from models.dev rates. None when model/rates unavailable.

    ``prompt_tokens`` semantics vary by provider:
    - Inclusive (OpenAI/Codex): cached reads are a subset of prompt → subtract.
    - Exclusive (Anthropic/Cursor/OpenCode/Muse): prompt is uncached only → add.
    Heuristic: when ``0 < cache_read <= prompt``, treat as inclusive. Pass
    ``cache_inclusive`` explicitly when the harness semantics are known
    (e.g. Cursor's additive usage would be mispriced by the heuristic).
    """
    rates = lookup_model_rates(model)
    if not rates:
        return None
    try:
        pt = max(0, int(prompt_tokens or 0))
        ct = max(0, int(completion_tokens or 0))
        cr = max(0, int(cache_read_tokens or 0))
        cw = max(0, int(cache_write_tokens or 0))
    except (TypeError, ValueError):
        return None
    if pt <= 0 and ct <= 0 and cr <= 0 and cw <= 0:
        return None

    input_rate = float(rates["input"])
    output_rate = float(rates["output"])
    cache_read_rate = rates.get("cache_read")
    if cache_read_rate is None:
        cache_read_rate = input_rate * _DEFAULT_CACHE_READ_MULT
    else:
        cache_read_rate = float(cache_read_rate)
    cache_write_rate = rates.get("cache_write")
    if cache_write_rate is None:
        cache_write_rate = input_rate * _DEFAULT_CACHE_WRITE_MULT
    else:
        cache_write_rate = float(cache_write_rate)

    if cache_inclusive is None:
        cache_inclusive = cr > 0 and cr <= pt
    if cache_inclusive:
        uncached = max(0, pt - cr)
    else:
        uncached = pt

    cost = (
        (uncached / 1_000_000.0) * input_rate
        + (cr / 1_000_000.0) * cache_read_rate
        + (cw / 1_000_000.0) * cache_write_rate
        + (ct / 1_000_000.0) * output_rate
    )
    return round(cost, 6)


def attach_estimated_cost(
    usage: Optional[Dict[str, Any]],
    model: str,
    *,
    cache_inclusive: Optional[bool] = None,
) -> Optional[Dict[str, Any]]:
    """Fill ``usage['cost']`` from token counts when the harness reported none.

    Marks the result with ``cost_estimated=True``. Returns ``usage`` unchanged
    (and ``None``) when there is nothing to price — no tokens, a cost already
    present, or a model with no public rate (e.g. Cursor 'auto').
    """
    if not isinstance(usage, dict) or usage.get("cost") is not None:
        return None
    try:
        pt = int(usage.get("prompt_tokens") or 0)
        ct = int(usage.get("completion_tokens") or 0)
        has_tokens = bool(int(usage.get("total_tokens") or 0)) or pt or ct
    except (TypeError, ValueError):
        return None
    if not has_tokens:
        return None
    est = estimate_cost_usd(
        model,
        pt,
        ct,
        cache_read_tokens=int(usage.get("cache_read_tokens") or 0),
        cache_write_tokens=int(usage.get("cache_write_tokens") or 0),
        cache_inclusive=cache_inclusive,
    )
    if est is None:
        return None
    usage["cost"] = float(est)
    usage["cost_estimated"] = True
    return usage


def enrich_usage_for_display(
    usage: Optional[Dict[str, Any]],
    *,
    model: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Normalize usage + fill estimated cost when CLI did not report one."""
    u = usage if isinstance(usage, dict) else {}
    try:
        pt = int(
            u.get("prompt_tokens")
            or u.get("input_tokens")
            or u.get("inputTokens")
            or 0
        )
    except (TypeError, ValueError):
        pt = 0
    try:
        ct = int(
            u.get("completion_tokens")
            or u.get("output_tokens")
            or u.get("outputTokens")
            or 0
        )
    except (TypeError, ValueError):
        ct = 0
    try:
        total = int(u.get("total_tokens") or u.get("totalTokens") or 0)
    except (TypeError, ValueError):
        total = 0
    if total <= 0 and (pt or ct):
        total = pt + ct

    cache_read, cache_write = extract_cache_token_counts(u)

    cost = u.get("cost")
    cost_estimated = bool(u.get("cost_estimated"))
    try:
        if cost is not None:
            cost = float(cost)
            if cost < 0:
                cost = None
    except (TypeError, ValueError):
        cost = None

    model_name = str(model or u.get("model") or "").strip()
    if cost is None and model_name and (pt or ct or cache_read or cache_write):
        est = estimate_cost_usd(
            model_name,
            pt,
            ct,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
        )
        if est is not None:
            cost = est
            cost_estimated = True

    if not pt and not ct and cost is None and not cache_read and not cache_write:
        return None

    out: Dict[str, Any] = {
        "prompt_tokens": pt,
        "completion_tokens": ct,
        "total_tokens": total,
    }
    if cache_read:
        out["cache_read_tokens"] = cache_read
    if cache_write:
        out["cache_write_tokens"] = cache_write
    if model_name:
        out["model"] = model_name
    if cost is not None:
        out["cost"] = float(cost)
        out["cost_estimated"] = bool(cost_estimated)
    if u.get("reported_cost") is not None:
        try:
            out["reported_cost"] = float(u["reported_cost"])
        except (TypeError, ValueError):
            pass
    for key in ("context_tokens", "peak_context_tokens"):
        raw = u.get(key)
        if raw is None:
            continue
        try:
            n = int(raw)
        except (TypeError, ValueError):
            continue
        if n >= 0:
            out[key] = n
    return out

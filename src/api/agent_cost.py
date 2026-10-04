"""
Per-harness model pricing for ``/cost`` (``/cursor /cost``, ``/codex /cost``, …).

Rate source per model, first hit wins:

1. Harness-reported rates — manifest ``pricing_source: cli`` (OpenCode
   ``opencode models --verbose`` carries a ``cost`` block per model).
2. Manifest ``pricing`` overrides (ids no catalog lists; drop-in harnesses).
3. models.dev cache (:mod:`api.model_pricing`), trying the manifest's
   ``pricing_providers`` first so e.g. Codex resolves to OpenAI list price.

Cursor (``AvailableModels`` has no price fields), Codex (``codex debug models``
has none), Muse, Claude Code, DeepSeek, Antigravity and Hermes expose no
per-model price API, so they use (2)/(3).

The active model (chat pin → per-turn override → starred default → CLI default)
is listed first and highlighted.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from api.agent_harness.types import AgentManifest

_COST_SLASH_RE = re.compile(r"^/(cost|costs|pricing|prices)\b(.*)$", re.IGNORECASE | re.DOTALL)
_BARE_COST_RE = re.compile(r"^(cost|costs|pricing|prices)$", re.IGNORECASE)

_DEFAULT_ROW_CAP = 40

_SOURCE_LABELS = {
    "session": "pinned for this chat",
    "override": "per-turn override",
    "starred": "starred default",
    "cli_default": "CLI default",
}

_FOOTNOTES = {
    "cursor": (
        "Cursor bills named models against included usage at the vendor's API "
        "rates. **Auto** uses Cursor's own pool pricing (no single rate), and "
        "`-fast` variants run on a priority tier billed at a premium."
    ),
    "codex": (
        "Codex on a ChatGPT plan draws from plan windows (`/usage`), not "
        "per-token billing — these are OpenAI API rates for comparison."
    ),
    "muse": (
        "Meta Model API list rates. Muse Code plan allowance is at "
        "[dev.meta.ai/usage](https://dev.meta.ai/usage)."
    ),
    "claude": (
        "`haiku` / `sonnet` / `opus` resolve to the newest matching Anthropic "
        "model on models.dev. Claude Code on a Pro/Max plan draws from plan "
        "limits instead of per-token billing."
    ),
    "deepseek": "DeepSeek API list rates.",
    "antigravity": (
        "Effort suffixes (`-high`, `-medium`) share the base model's rate. "
        "A Google sign-in quota may not bill at these rates."
    ),
    "hermes": (
        "Rates follow each model's Hermes provider (`config.yaml`); local / "
        "custom backends cost nothing per token."
    ),
    "opencode": "Rates reported by OpenCode itself (`opencode models --verbose`).",
}

_LOCAL_PROVIDERS = frozenset({"custom", "local", "llamacpp", "llama.cpp", "ollama", "lmstudio"})


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_cost_slash(prompt: str) -> Optional[str]:
    """If ``prompt`` is ``/cost [args]`` (or bare ``cost``), return args."""
    text = (prompt or "").strip()
    if not text:
        return None
    m = _COST_SLASH_RE.match(text)
    if m:
        return (m.group(2) or "").strip()
    # Bare word only when it is the whole prompt — "cost of this refactor?" is a task.
    if _BARE_COST_RE.match(text):
        return ""
    return None


_BENCH_ARGS = {
    "cursorbench": "cursorbench",
    "deepswe": "deepswe",
    "swebench": "swebench",
    "swe-bench": "swebench",
}


def _parse_cost_args(args: str) -> Dict[str, Any]:
    refresh = False
    show_all = False
    bench = ""
    terms: List[str] = []
    for tok in (args or "").split():
        low = tok.strip().lower()
        if low in ("refresh", "reload", "sync", "--refresh"):
            refresh = True
        elif low in ("all", "--all"):
            show_all = True
        elif low in _BENCH_ARGS:
            bench = _BENCH_ARGS[low]
        elif low:
            terms.append(low)
    return {"refresh": refresh, "all": show_all, "bench": bench, "terms": terms}


# ---------------------------------------------------------------------------
# Active model (pin → override → starred → CLI default)
# ---------------------------------------------------------------------------


def _session_pin(agent_id: str, sid: Optional[str], cwd: str) -> Tuple[str, str]:
    """Return ``(model, effort)`` pinned on this chat for ``agent_id``."""
    try:
        if agent_id == "cursor":
            from scripts.utilities.cursor_cli_session_store import (
                load_cursor_agent_options,
                load_cursor_agent_options_for_session,
            )

            opts = (
                load_cursor_agent_options(cwd, sid)
                if cwd
                else load_cursor_agent_options_for_session(sid)
            ) or {}
            return str(opts.get("model") or ""), ""
        if agent_id == "codex":
            from scripts.utilities.codex_cli_session_store import (
                load_codex_effort,
                load_codex_model,
            )

            return load_codex_model(sid) or "", load_codex_effort(sid) or ""
        if agent_id == "claude":
            from scripts.utilities.claude_cli_session_store import (
                load_claude_effort,
                load_claude_model,
            )

            return load_claude_model(sid) or "", load_claude_effort(sid) or ""
        if agent_id == "muse":
            from scripts.utilities.muse_cli_session_store import (
                load_muse_effort,
                load_muse_model,
            )

            return load_muse_model(sid) or "", load_muse_effort(sid) or ""
        if agent_id == "hermes":
            from scripts.utilities.hermes_cli_session_store import (
                load_hermes_effort,
                load_hermes_model,
            )

            return load_hermes_model(sid) or "", load_hermes_effort(sid) or ""
        if agent_id == "opencode":
            from api.agent_harness.agents.opencode.session_store import (
                load_opencode_effort,
                load_opencode_model,
            )

            return load_opencode_model(sid) or "", load_opencode_effort(sid) or ""
        if agent_id == "claude":
            from scripts.utilities.claude_cli_session_store import load_claude_model

            return load_claude_model(sid) or "", ""
    except Exception as exc:
        print(f"[agent_cost] pin lookup failed for {agent_id}: {exc}", flush=True)
    return "", ""


def _cli_default_model(manifest: AgentManifest) -> str:
    aid = manifest.id
    try:
        if aid == "cursor":
            return "auto"
        if aid == "muse":
            from scripts.utilities.muse_cli_tool import resolve_muse_default_model

            return resolve_muse_default_model()
        if aid == "hermes":
            from scripts.utilities.hermes_cli_tool import resolve_hermes_default_model

            return resolve_hermes_default_model()
        if aid == "opencode":
            from api.agent_harness.agents.opencode.adapter import DEFAULT_OPENCODE_MODEL

            return DEFAULT_OPENCODE_MODEL
        if aid == "deepseek":
            from api.agent_harness.agents.deepseek.adapter import _DEFAULT_MODEL

            return _DEFAULT_MODEL
    except Exception:
        pass
    return manifest.default_model or ""


def resolve_active_model(
    manifest: AgentManifest,
    *,
    chat_session_id: Optional[str],
    cwd: str = "",
    model_override: Optional[str] = None,
) -> Dict[str, str]:
    """``{model, effort, source}`` for the model this chat's next turn would use."""
    from api.agent_harness.agent_defaults import (
        resolve_effective_effort,
        resolve_effective_model,
    )

    pin_model, pin_effort = _session_pin(manifest.id, chat_session_id, cwd)
    model, source = resolve_effective_model(
        manifest.id,
        session_model=pin_model,
        kernel_override=model_override,
        cli_default=_cli_default_model(manifest),
    )
    effort = ""
    if manifest.supports_effort:
        eff, _ = resolve_effective_effort(manifest.id, session_effort=pin_effort)
        effort = eff or ""
    return {"model": model or "", "effort": effort, "source": source if model else ""}


# ---------------------------------------------------------------------------
# Model catalogs (live CLI / API where the harness has one)
# ---------------------------------------------------------------------------


def _rows_from_ids(ids: List[str]) -> List[Dict[str, Any]]:
    return [{"id": m, "label": m} for m in ids if str(m or "").strip()]


def _cursor_catalog(refresh: bool) -> Tuple[List[Dict[str, Any]], str]:
    from api.cursor_agent_commands import list_cursor_agent_models, refresh_cursor_agent_models

    models = (
        refresh_cursor_agent_models().get("models") if refresh else list_cursor_agent_models()
    ) or []
    return [dict(m) for m in models if isinstance(m, dict)], "`agent models`"


def _codex_catalog(refresh: bool) -> Tuple[List[Dict[str, Any]], str]:
    from api.agent_harness.agents.codex.model_catalog import list_codex_catalog_models

    result = list_codex_catalog_models(refresh=refresh)
    return list(result.get("models") or []), "`codex debug models`"


def _claude_catalog(refresh: bool) -> Tuple[List[Dict[str, Any]], str]:
    from api.agent_harness.agents.claude.model_catalog import list_claude_catalog_models

    result = list_claude_catalog_models(refresh=refresh)
    return list(result.get("models") or []), "Claude Code CLI"


def _muse_catalog(refresh: bool) -> Tuple[List[Dict[str, Any]], str]:
    from scripts.utilities.muse_cli_tool import list_muse_catalog_models

    result = list_muse_catalog_models(refresh=refresh)
    return list(result.get("models") or []), "Meta Model API"


def _hermes_catalog(refresh: bool) -> Tuple[List[Dict[str, Any]], str]:
    from scripts.utilities.hermes_cli_tool import list_hermes_known_models

    return [dict(m) for m in list_hermes_known_models()], "Hermes config + curated list"


def _opencode_catalog(refresh: bool) -> Tuple[List[Dict[str, Any]], str]:
    from api.agent_harness.agents.opencode.model_catalog import list_opencode_catalog_models

    result = list_opencode_catalog_models(refresh=refresh)
    return list(result.get("models") or []), "`opencode models --verbose`"


_CATALOG_LOADERS: Dict[str, Callable[[bool], Tuple[List[Dict[str, Any]], str]]] = {
    "cursor": _cursor_catalog,
    "codex": _codex_catalog,
    "claude": _claude_catalog,
    "muse": _muse_catalog,
    "hermes": _hermes_catalog,
    "opencode": _opencode_catalog,
}


def _load_catalog(manifest: AgentManifest, refresh: bool) -> Tuple[List[Dict[str, Any]], str]:
    loader = _CATALOG_LOADERS.get(manifest.id)
    rows: List[Dict[str, Any]] = []
    source = ""
    if loader is not None:
        try:
            rows, source = loader(refresh)
        except Exception as exc:
            print(f"[agent_cost] {manifest.id} catalog failed: {exc}", flush=True)
            rows = []
    if not rows:
        rows = _rows_from_ids(list(manifest.models or []))
        source = "manifest"
    return rows, source


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------


def _claude_alias_target(alias: str) -> Optional[str]:
    """``sonnet`` → newest ``claude-sonnet-*`` on models.dev (dated snapshots skipped)."""
    fam = (alias or "").strip().lower()
    if fam not in ("haiku", "sonnet", "opus"):
        return None
    from api.model_pricing import list_provider_pricing

    best: Optional[Tuple[Tuple[int, ...], str]] = None
    prefix = f"claude-{fam}-"
    for row in list_provider_pricing("anthropic"):
        mid = str(row.get("model_id") or "").lower()
        if not mid.startswith(prefix):
            continue
        parts = mid[len(prefix) :].split("-")
        if not parts or not all(p.isdigit() and len(p) <= 3 for p in parts):
            continue
        version = tuple(int(p) for p in parts)
        if best is None or version > best[0]:
            best = (version, mid)
    return best[1] if best else None


def _manifest_override(manifest: AgentManifest, model_id: str) -> Optional[Dict[str, Any]]:
    if not manifest.pricing:
        return None
    from api.model_pricing import strip_variant_suffixes

    wanted = {model_id.lower(), strip_variant_suffixes(model_id).lower()}
    for key, spec in manifest.pricing.items():
        if str(key).lower() in wanted:
            return spec
    return None


def _num(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def price_model(
    manifest: AgentManifest,
    model_id: str,
    *,
    catalog_row: Optional[Dict[str, Any]] = None,
    provider_hint: str = "",
) -> Dict[str, Any]:
    """Rates for one model: ``{input, output, cache_read, cache_write, context, source, provider, resolved}``."""
    out: Dict[str, Any] = {
        "input": None,
        "output": None,
        "cache_read": None,
        "cache_write": None,
        "context": None,
        "source": "",
        "provider": "",
        "resolved": "",
    }
    row = catalog_row or {}
    if row.get("context"):
        out["context"] = int(row["context"])

    cli_cost = row.get("cost") if isinstance(row.get("cost"), dict) else None
    if manifest.pricing_source == "cli" and cli_cost:
        for key in ("input", "output", "cache_read", "cache_write"):
            out[key] = _num(cli_cost.get(key))
        out["source"] = "cli"
        return out

    override = _manifest_override(manifest, model_id)
    if override:
        for key in ("input", "output", "cache_read", "cache_write"):
            out[key] = _num(override.get(key))
        if override.get("context"):
            out["context"] = int(override["context"])
        out["source"] = "manifest"
        out["note"] = str(override.get("note") or "")
        return out

    prov = (provider_hint or "").strip().lower()
    if prov in _LOCAL_PROVIDERS or prov.startswith("custom:"):
        out.update({"input": 0.0, "output": 0.0, "source": "local", "provider": prov})
        return out

    lookup_id = model_id
    if manifest.id == "claude":
        target = _claude_alias_target(model_id)
        if target:
            lookup_id = target
            out["resolved"] = target

    from api.model_pricing import lookup_model_pricing

    providers = ([prov] if prov else []) + list(manifest.pricing_providers or [])
    rates = lookup_model_pricing(lookup_id, providers=providers)
    if rates:
        for key in ("input", "output", "cache_read", "cache_write"):
            if rates.get(key) is not None:
                out[key] = _num(rates.get(key))
        if not out["context"] and rates.get("context"):
            out["context"] = int(rates["context"])
        out["source"] = "models.dev"
        out["provider"] = str(rates.get("provider") or "")
        if rates.get("name"):
            out["name"] = str(rates["name"])
    return out


# ---------------------------------------------------------------------------
# Table assembly
# ---------------------------------------------------------------------------

_EFFORT_ORDER = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")

# Token profile of one typical agentic coding task (read files, edit, run
# tests): ~300K prompt tokens across tool loops, mostly cache hits, plus the
# model's own output. A fixed yardstick for comparing models — not a quote.
TASK_PROFILE = {
    "input": 60_000,
    "cache_read": 240_000,
    "cache_write": 30_000,
    "output": 15_000,
}

# Output (incl. reasoning) scales with effort; ``medium`` is the baseline.
_EFFORT_OUTPUT_SCALE = {
    "none": 0.5,
    "minimal": 0.5,
    "low": 0.6,
    "medium": 1.0,
    "high": 1.6,
    "xhigh": 2.2,
    "max": 3.0,
    "ultra": 3.0,
}


def _fmt_tokens(n: int) -> str:
    return f"{round(n / 1000)}K" if n >= 1000 else str(n)


def task_profile_basis() -> str:
    p = TASK_PROFILE
    prompt = p["input"] + p["cache_read"]
    cached = round(100 * p["cache_read"] / prompt) if prompt else 0
    return (
        f"~{_fmt_tokens(prompt)} prompt tokens ({cached}% cache hits), "
        f"{_fmt_tokens(p['cache_write'])} cache writes, "
        f"{_fmt_tokens(p['output'])} output at medium effort"
    )


def estimate_task_cost(rates: Dict[str, Any], effort: str = "") -> Optional[float]:
    """USD for one :data:`TASK_PROFILE` task at ``rates`` (USD / 1M tokens).

    Models without a cache rate pay full input price for cached/written tokens.
    """
    inp, out = rates.get("input"), rates.get("output")
    if inp is None or out is None:
        return None
    inp, out = float(inp), float(out)
    cache_read = rates.get("cache_read")
    cache_write = rates.get("cache_write")
    cr = inp if cache_read is None else float(cache_read)
    cw = inp if cache_write is None else float(cache_write)
    scale = _EFFORT_OUTPUT_SCALE.get((effort or "").strip().lower(), 1.0)
    p = TASK_PROFILE
    usd = (
        p["input"] * inp
        + p["cache_read"] * cr
        + p["cache_write"] * cw
        + p["output"] * scale * out
    ) / 1_000_000
    return round(usd, 6)


def _group_key(model_id: str) -> str:
    from api.model_pricing import strip_variant_suffixes

    mid = (model_id or "").strip()
    prefix = mid[: mid.rfind("/") + 1] if "/" in mid else ""
    return (prefix + strip_variant_suffixes(mid)).lower()


def _variant_summary(ids: List[str], base: str) -> str:
    efforts: List[str] = []
    fast = False
    thinking = False
    for mid in ids:
        leaf = mid.rsplit("/", 1)[-1].lower()
        if leaf.startswith("cursor-"):
            leaf = leaf[len("cursor-") :]
        rest = leaf[len(base) :] if leaf.startswith(base) else ""
        for tok in [t for t in rest.split("-") if t]:
            if tok == "fast":
                fast = True
            elif tok == "thinking":
                thinking = True
            elif tok in _EFFORT_ORDER and tok not in efforts:
                efforts.append(tok)
    bits: List[str] = []
    if efforts:
        ordered = [e for e in _EFFORT_ORDER if e in efforts]
        bits.append("efforts " + " · ".join(ordered))
    if thinking:
        bits.append("thinking")
    if fast:
        bits.append("+fast")
    return ", ".join(bits)


_LABEL_VARIANT_WORDS = frozenset({"low", "medium", "high", "extra", "max", "fast", "thinking"})


def _clean_label(label: str) -> str:
    """Drop effort/speed words Cursor appends to variant labels."""
    text = (label or "").replace("\u200b", "").strip()
    words = [w for w in re.split(r"\s+", text) if w.lower() not in _LABEL_VARIANT_WORDS]
    return " ".join(words).strip()


def _matches_terms(row: Dict[str, Any], terms: List[str]) -> bool:
    if not terms:
        return True
    hay = " ".join(
        str(row.get(k) or "") for k in ("model", "label", "note", "variants")
    ).lower()
    compact = re.sub(r"[\s._/-]+", "", hay)
    return all(t in hay or re.sub(r"[\s._/-]+", "", t) in compact for t in terms)


def build_cost_table(
    manifest: AgentManifest,
    *,
    active: Dict[str, str],
    args: str = "",
) -> Dict[str, Any]:
    """Assemble ``{rows, shown, total, catalog_source, active_row}`` for ``manifest``."""
    opts = _parse_cost_args(args)
    if opts["refresh"]:
        try:
            from api.model_pricing import refresh_models_dev_pricing

            refresh_models_dev_pricing(force=True)
        except Exception as exc:
            print(f"[agent_cost] models.dev refresh failed: {exc}", flush=True)
    catalog, catalog_source = _load_catalog(manifest, opts["refresh"])

    active_id = (active.get("model") or "").strip()
    active_effort = (active.get("effort") or "").strip() or _id_effort(active_id)
    known_ids = {str(r.get("id") or "").lower() for r in catalog}
    if active_id and active_id.lower() not in known_ids:
        catalog = [{"id": active_id, "label": active_id}] + list(catalog)

    provider_hints: Dict[str, str] = {}
    if manifest.id == "hermes":
        try:
            from scripts.utilities.hermes_cli_tool import resolve_hermes_runtime

            for r in catalog:
                mid = str(r.get("id") or "")
                provider_hints[mid] = str(
                    r.get("provider") or resolve_hermes_runtime(mid).get("provider") or ""
                )
        except Exception:
            pass
    elif manifest.pricing_source == "cli":
        # OpenCode ids are ``provider/model`` — use that provider when the CLI
        # catalog lacks a row (favorite for an unconfigured provider).
        for r in catalog:
            mid = str(r.get("id") or "")
            if "/" in mid:
                provider_hints[mid] = mid.split("/", 1)[0]

    group = manifest.pricing_source != "cli"
    groups: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for r in catalog:
        mid = str(r.get("id") or "").strip()
        if not mid:
            continue
        key = _group_key(mid) if group else mid.lower()
        if key not in groups:
            order.append(key)
            groups[key] = {"ids": [], "rows": []}
        groups[key]["ids"].append(mid)
        groups[key]["rows"].append(r)

    listed_keys = {
        _group_key(m) if group else m.lower() for m in manifest.models or [] if m
    }
    rows: List[Dict[str, Any]] = []
    for key in order:
        g = groups[key]
        ids: List[str] = g["ids"]
        rep_idx = min(range(len(ids)), key=lambda i: (len(ids[i]), ids[i]))
        rep_id = ids[rep_idx]
        rep_row = g["rows"][rep_idx]
        price = price_model(
            manifest,
            rep_id,
            catalog_row=rep_row,
            provider_hint=provider_hints.get(rep_id, ""),
        )
        label = str(rep_row.get("label") or "").strip()
        if len(ids) > 1:
            label = price.get("name") or _clean_label(label) or rep_id
        elif not label or label == rep_id:
            label = price.get("name") or rep_id

        base_leaf = key.rsplit("/", 1)[-1]
        variants = _variant_summary(ids, base_leaf) if len(ids) > 1 else ""
        display_id = rep_id
        if len(ids) > 1:
            display_id = key if "/" in key else base_leaf
        is_active = bool(active_id) and active_id.lower() in {i.lower() for i in ids}

        notes: List[str] = []
        if price.get("resolved"):
            notes.append(f"→ `{price['resolved']}`")
        if price.get("note"):
            notes.append(str(price["note"]))
        if price["source"] == "local":
            notes.append("local backend")
        elif price["source"] == "models.dev" and manifest.pricing_source == "cli":
            notes.append("not in CLI catalog — models.dev rate")
        elif price["source"] == "models.dev" and price.get("provider"):
            pref = [p.lower() for p in manifest.pricing_providers or []]
            hint = provider_hints.get(rep_id, "").lower()
            if price["provider"].lower() not in pref and price["provider"].lower() != hint:
                notes.append(f"via {price['provider']}")
        if price["input"] is None:
            low = rep_id.lower()
            if manifest.id == "cursor" and low in ("auto", "default"):
                notes.append("Cursor pool pricing")
            elif manifest.id == "cursor" and low.startswith("composer"):
                notes.append("Cursor in-house model — no public per-token rate")
            else:
                notes.append("no public rate found")
        if is_active:
            bits = []
            if active_id.lower() != display_id.lower():
                bits.append(f"`{active_id}`")
            if active.get("effort"):
                bits.append(f"effort `{active['effort']}`")
            src = _SOURCE_LABELS.get(active.get("source") or "", "")
            if src:
                bits.append(src)
            if bits:
                notes.insert(0, "active: " + ", ".join(bits))

        rows.append(
            {
                "model": display_id,
                "label": label,
                "input": price["input"],
                "output": price["output"],
                "cache_read": price["cache_read"],
                "cache_write": price["cache_write"],
                "context": price["context"],
                "task": estimate_task_cost(price, active_effort if is_active else ""),
                "source": price["source"],
                "provider": price.get("provider") or "",
                "variants": variants,
                "note": " · ".join(n for n in notes if n),
                "active": is_active,
                "favorite": bool(rep_row.get("favorite")),
                "_listed": key in listed_keys,
                "_bench_cands": _bench_candidates(display_id, rep_id, label, price),
                "_bench_effort": (
                    active_effort if is_active else (_id_effort(rep_id) if len(ids) == 1 else "")
                ),
            }
        )

    bench = _attach_benchmarks(
        rows, opts["bench"], opts["refresh"], preferred=manifest.pricing_benchmark
    )
    active_row = next((r for r in rows if r["active"]), None)
    total = len(rows)

    filtered = [r for r in rows if _matches_terms(r, opts["terms"])]
    if manifest.pricing_source == "cli" and not opts["terms"] and not opts["all"]:
        # OpenCode lists every provider's catalog — default to curated + pinned.
        filtered = [r for r in filtered if r["active"] or r["favorite"] or r["_listed"]]

    def _sort_key(r: Dict[str, Any]) -> Tuple[int, int, float, float]:
        priced = r["input"] is not None and r["output"] is not None
        return (
            0 if r["active"] else 1,
            0 if priced else 1,
            float(r["output"] or 0) if priced else 0.0,
            float(r["input"] or 0) if priced else 0.0,
        )

    filtered.sort(key=_sort_key)
    if not opts["all"] and len(filtered) > _DEFAULT_ROW_CAP:
        filtered = filtered[:_DEFAULT_ROW_CAP]
    for r in rows:
        for k in ("_listed", "_bench_cands", "_bench_effort"):
            r.pop(k, None)
    return {
        "rows": filtered,
        "shown": len(filtered),
        "total": total,
        "catalog_source": catalog_source,
        "active_row": active_row,
        "terms": opts["terms"],
        "bench": bench,
    }


def _id_effort(model_id: str) -> str:
    for tok in reversed(re.split(r"[-_]", (model_id or "").lower())):
        if tok in _EFFORT_ORDER:
            return tok
        if tok not in ("fast", "thinking"):
            break
    return ""


def _bench_candidates(display_id: str, rep_id: str, label: str, price: Dict[str, Any]) -> List[str]:
    from api.model_pricing import pricing_lookup_candidates

    out: List[str] = []
    for c in [display_id, price.get("resolved") or "", rep_id, *pricing_lookup_candidates(rep_id),
              price.get("name") or "", label]:
        c = str(c or "").strip()
        if c and c not in out:
            out.append(c)
    return out


def _attach_benchmarks(
    rows: List[Dict[str, Any]], forced: str, refresh: bool, *, preferred: str = ""
) -> Optional[Dict[str, Any]]:
    """Fill ``bench_*`` from one benchmark: ``forced``, else the manifest's
    ``preferred`` when it covers any row, else the one covering the active row
    and then the most rows."""
    try:
        from api.task_benchmarks import (
            BENCHMARK_ORDER,
            BENCHMARKS,
            index_by_bench,
            load_entries,
            match,
        )

        data = load_entries(force_refresh=refresh)
    except Exception as exc:
        print(f"[agent_cost] task benchmarks unavailable: {exc}", flush=True)
        return None
    idx = index_by_bench(data.get("entries") or [])
    hits: Dict[str, List[Optional[Dict[str, Any]]]] = {
        b: [match(idx.get(b) or {}, r["_bench_cands"], r["_bench_effort"]) for r in rows]
        for b in BENCHMARK_ORDER
    }
    counts = {b: sum(1 for h in hits[b] if h) for b in BENCHMARK_ORDER}
    active_hit = {
        b: any(h and r.get("active") for r, h in zip(rows, hits[b])) for b in BENCHMARK_ORDER
    }
    if forced:
        chosen = forced
    elif preferred in counts and counts[preferred]:
        chosen = preferred
    else:
        chosen = max(
            BENCHMARK_ORDER,
            key=lambda b: (active_hit[b], counts[b], -BENCHMARK_ORDER.index(b)),
        )
        if not counts[chosen]:
            return None
    matched = 0
    for r, hit in zip(rows, hits.get(chosen) or []):
        if not hit:
            continue
        matched += 1
        r["bench_cost"] = hit["cost"]
        if hit.get("score") is not None:
            r["bench_score"] = hit["score"]
        if hit.get("effort"):
            r["bench_effort"] = hit["effort"]
    meta = BENCHMARKS.get(chosen, {})
    return {
        "id": chosen,
        "label": meta.get("label", chosen),
        "credit": meta.get("credit", ""),
        "url": meta.get("url", ""),
        "matched": matched,
        "fetched_at": float(data.get("fetched_at") or 0),
    }


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------


def fmt_rate(value: Optional[float]) -> str:
    if value is None:
        return "—"
    v = float(value)
    if v == 0:
        return "Free"
    if v >= 1 or abs(v * 100 - round(v * 100)) < 1e-9:
        return f"${v:,.2f}"
    return "$" + f"{v:.4f}".rstrip("0").rstrip(".")


def _fmt_context(tokens: Optional[int]) -> str:
    if not tokens:
        return ""
    n = int(tokens)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".") + "M"
    if n >= 1000:
        return f"{round(n / 1000)}K"
    return str(n)


def cuttle_pricing_markdown(
    rows: List[Dict[str, Any]],
    *,
    title: str = "",
    unit: str = "USD / 1M tokens",
    bench: Optional[Dict[str, Any]] = None,
) -> str:
    """Emit a ``<cuttle_pricing>`` block (rendered as a highlighted table in chat)."""
    keep = (
        "model",
        "label",
        "input",
        "output",
        "cache_read",
        "cache_write",
        "context",
        "task",
        "bench_cost",
        "bench_score",
        "bench_effort",
        "variants",
        "note",
        "active",
        "source",
    )
    clean = [
        {k: r.get(k) for k in keep if r.get(k) is not None and r.get(k) != "" and r.get(k) is not False}
        for r in rows
    ]
    if not clean:
        return ""
    payload = {"title": title, "unit": unit, "rows": clean}
    if any("task" in r for r in clean):
        payload["task_basis"] = task_profile_basis()
    if bench and any("bench_cost" in r for r in clean):
        payload["bench"] = {k: bench.get(k) for k in ("id", "label", "credit", "url")}
    return (
        "<cuttle_pricing>\n"
        + json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        + "\n</cuttle_pricing>"
    )


def _age_label(fetched_at: float) -> str:
    if not fetched_at:
        return ""
    secs = max(0, int(time.time() - fetched_at))
    if secs < 3600:
        return f"{max(1, secs // 60)}m ago"
    if secs < 172800:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


def format_cost_markdown(manifest: AgentManifest, table: Dict[str, Any], active: Dict[str, str]) -> str:
    label = manifest.label or manifest.id
    rows = table.get("rows") or []
    lines = [f"**{label} — model pricing** · USD per 1M tokens", ""]

    act = table.get("active_row")
    if act:
        bits = [f"**{act['label']}** `{active.get('model') or act['model']}`"]
        if active.get("effort"):
            bits.append(f"effort `{active['effort']}`")
        src = _SOURCE_LABELS.get(active.get("source") or "", "")
        price = (
            f"{fmt_rate(act['input'])} in / {fmt_rate(act['output'])} out"
            if act.get("input") is not None
            else "no public rate"
        )
        bench = table.get("bench") or {}
        if act.get("bench_cost") is not None:
            detail = [b for b in (act.get("bench_effort") or "",
                                  f"{act['bench_score']:g}% solved" if act.get("bench_score") is not None else "") if b]
            price += (
                f" · {fmt_rate(act['bench_cost'])} / task on {bench.get('label', 'benchmark')}"
                + (f" ({', '.join(detail)})" if detail else "")
            )
        elif act.get("task") is not None:
            price += f" · ≈ {fmt_rate(act['task'])} / task"
        lines.append(
            f"- Active: {' · '.join(bits)}" + (f" ({src})" if src else "") + f" — {price}"
        )
    else:
        lines.append("- Active: no model pinned — the CLI picks its default")

    if manifest.pricing_source == "cli":
        source_line = f"- Rates: reported by the harness ({table.get('catalog_source') or 'CLI'})"
    else:
        from api.model_pricing import pricing_cache_info

        info = pricing_cache_info()
        age = _age_label(float(info.get("fetched_at") or 0))
        provs = ", ".join(manifest.pricing_providers or []) or "any provider"
        source_line = (
            f"- Rates: models.dev ({provs}{'; cached ' + age if age else ''}) — "
            f"{label} has no pricing API"
        )
    lines.append(source_line)
    if any(r.get("task") is not None for r in rows):
        effort_bit = (
            f"; active row at effort `{active['effort']}`" if act and active.get("effort") else ""
        )
        lines.append(
            f"- ≈ / task: one typical agentic coding task — {task_profile_basis()}"
            f"{effort_bit}. A yardstick for comparing models, not a quote."
        )
    bench = table.get("bench")
    if bench:
        others = " / ".join(
            f"`/cost {b}`" for b in ("cursorbench", "deepswe", "swebench") if b != bench["id"]
        )
        credit = f"[{bench['credit']}]({bench['url']})" if bench.get("url") else bench.get("credit", "")
        lines.append(
            f"- Measured $ / task: **{bench['label']}** via {credit} — "
            f"{bench['matched']} of {int(table.get('total') or 0)} models benchmarked, at the effort shown "
            f"(your pinned effort when available). Switch with {others}."
        )
    if table.get("catalog_source"):
        lines.append(f"- Models: {table['catalog_source']}")
    shown, total = int(table.get("shown") or 0), int(table.get("total") or 0)
    if table.get("terms"):
        lines.append(f"- Filter: `{' '.join(table['terms'])}` — {shown} of {total} models")
    elif shown < total:
        lines.append(
            f"- Showing {shown} of {total} — `/cost all` for every model, "
            "or `/cost <filter>` to search"
        )

    block = cuttle_pricing_markdown(rows, title=f"{label} pricing", bench=table.get("bench"))
    lines.append("")
    if block:
        lines.append(block)
    else:
        lines.append("_No models matched._")

    foot = _FOOTNOTES.get(manifest.id)
    if foot:
        lines.append("")
        lines.append(f"_{foot}_")
    lines.append("")
    lines.append("`/cost refresh` re-fetches rates and the model list.")
    return "\n".join(lines)


def run_agent_cost(
    manifest: AgentManifest,
    args: str = "",
    *,
    chat_session_id: Optional[str] = None,
    cwd: str = "",
    model_override: Optional[str] = None,
) -> str:
    active = resolve_active_model(
        manifest, chat_session_id=chat_session_id, cwd=cwd, model_override=model_override
    )
    table = build_cost_table(manifest, active=active, args=args)
    return format_cost_markdown(manifest, table, active)


def handle_agent_cost_slash(
    manifest: AgentManifest,
    prompt: str,
    *,
    chat_session_id: Optional[str] = None,
    cwd: str = "",
    model_override: Optional[str] = None,
) -> Optional[str]:
    """Markdown reply when ``prompt`` is ``/cost …`` for this harness, else None."""
    args = parse_cost_slash(prompt)
    if args is None:
        return None
    try:
        return run_agent_cost(
            manifest,
            args,
            chat_session_id=chat_session_id,
            cwd=cwd,
            model_override=model_override,
        )
    except Exception as exc:
        return f"❌ **{manifest.label or manifest.id} pricing**\n\n{exc}"

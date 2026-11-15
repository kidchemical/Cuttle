"""Per-turn cost from Claude Code's session-wide usage rollup.

A root ``result`` event's ``usage`` covers only the turn that just ended, but
its ``modelUsage`` and ``total_cost_usd`` are running totals for the resumed
Claude session (the CLI restores them on ``--resume``). Pricing those totals
made every bubble show the whole session so far, and the composer sum counted
each earlier turn again.

``TurnCostLedger.turn`` rewrites one result to the difference from the
previous result of the same Claude session. The last totals are kept in the
session store, so the next run's first result has a baseline too.

The CLI restarts its counters when it does not restore them (another session
ran in the project meanwhile). A difference that comes out smaller than the
turn's own ``usage`` means the baseline no longer applies; so does a resumed
session with no saved baseline. In both cases the turn is priced from its own
``usage`` tokens on its main model instead.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

_NON_ADDITIVE = ("contextWindow", "maxOutputTokens")
_OUT_KEYS = ("outputTokens", "output_tokens")
_CACHE_READ_KEYS = ("cacheReadInputTokens", "cache_read_input_tokens", "cacheReadTokens")


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _field(row: Dict[str, Any], keys) -> float:
    for key in keys:
        n = _num(row.get(key))
        if n is not None:
            return n
    return 0


def _rows(model_usage: Any) -> Dict[str, Dict[str, Any]]:
    if not isinstance(model_usage, dict):
        return {}
    return {str(k): v for k, v in model_usage.items() if isinstance(v, dict)}


def _diff(cum: Dict[str, Dict[str, Any]], base: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Dict[str, Any]]]:
    """Per-model differences, or None when any counter went backwards."""
    out: Dict[str, Dict[str, Any]] = {}
    for model, row in cum.items():
        prev = base.get(model) or {}
        delta: Dict[str, Any] = {}
        for key, value in row.items():
            n = _num(value)
            if n is None or key in _NON_ADDITIVE:
                delta[key] = value
                continue
            d = n - (_num(prev.get(key)) or 0)
            if d < -1e-9:
                return None
            delta[key] = round(d, 9) if isinstance(d, float) else d
        if any(_num(delta.get(k)) for k in delta if k not in _NON_ADDITIVE):
            out[model] = delta
    if any(model not in cum for model in base):
        return None
    return out


def _turn_tokens(result: Dict[str, Any]) -> Dict[str, float]:
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    return {
        "inputTokens": _field(usage, ("input_tokens", "inputTokens")),
        "outputTokens": _field(usage, ("output_tokens", "outputTokens")),
        "cacheReadInputTokens": _field(usage, ("cache_read_input_tokens", "cacheReadInputTokens")),
        "cacheCreationInputTokens": _field(usage, ("cache_creation_input_tokens", "cacheCreationInputTokens")),
    }


def _covers_turn(delta: Dict[str, Dict[str, Any]], turn: Dict[str, float]) -> bool:
    """A real per-turn difference holds at least the turn's own tokens."""
    out = sum(_field(r, _OUT_KEYS) for r in delta.values())
    cache_read = sum(_field(r, _CACHE_READ_KEYS) for r in delta.values())
    return out >= turn["outputTokens"] and cache_read >= turn["cacheReadInputTokens"]


def _main_model(cum: Dict[str, Dict[str, Any]]) -> Optional[str]:
    if not cum:
        return None
    return max(cum, key=lambda m: _field(cum[m], _OUT_KEYS) + _field(cum[m], _CACHE_READ_KEYS))


def per_turn_result(
    result: Dict[str, Any],
    baseline: Optional[Dict[str, Any]],
    *,
    resumed: bool,
) -> Dict[str, Any]:
    """Return ``result`` with ``modelUsage`` / ``total_cost_usd`` for this turn only."""
    cum = _rows(result.get("modelUsage"))
    if not cum:
        return result
    out = dict(result)
    cum_cost = _num(result.get("total_cost_usd"))
    base_rows = _rows((baseline or {}).get("modelUsage"))
    if baseline is None and not resumed:
        base_rows, base_cost = {}, 0.0  # a fresh session: totals are this turn
    else:
        base_cost = _num((baseline or {}).get("total_cost_usd"))
    delta = _diff(cum, base_rows) if (baseline is not None or not resumed) else None
    turn = _turn_tokens(result)
    if delta is not None and _covers_turn(delta, turn):
        out["modelUsage"] = delta
        if cum_cost is not None and base_cost is not None and cum_cost >= base_cost:
            out["total_cost_usd"] = round(cum_cost - base_cost, 9)
        else:
            out.pop("total_cost_usd", None)
        return out
    model = _main_model(cum)
    out["modelUsage"] = {model: turn} if model and any(turn.values()) else {}
    out.pop("total_cost_usd", None)
    return out


def _snapshot(result: Dict[str, Any]) -> Dict[str, Any]:
    return {"modelUsage": _rows(result.get("modelUsage")), "total_cost_usd": _num(result.get("total_cost_usd"))}


class TurnCostLedger:
    """Running totals per Claude session for one CLI run (persisted between runs)."""

    def __init__(self, resume_id: Optional[str]):
        self.resume_id = (resume_id or "").strip() or None
        self._last: Dict[str, Dict[str, Any]] = {}

    def turn(self, result: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(result, dict) or not _rows(result.get("modelUsage")):
            return result
        sid = str(result.get("session_id") or self.resume_id or "").strip()
        if sid in self._last:
            baseline: Optional[Dict[str, Any]] = self._last[sid]
        else:
            baseline = _load(sid) if sid else None
        resumed = bool(self.resume_id) and sid == self.resume_id or sid in self._last
        priced = per_turn_result(result, baseline, resumed=resumed)
        snap = _snapshot(result)
        if sid:
            self._last[sid] = snap
            _save(sid, snap)
        return priced


def _load(claude_session_id: str) -> Optional[Dict[str, Any]]:
    try:
        from scripts.utilities.claude_cli_session_store import load_claude_cost_snapshot
        return load_claude_cost_snapshot(claude_session_id)
    except Exception:
        return None


def _save(claude_session_id: str, snapshot: Dict[str, Any]) -> None:
    try:
        from scripts.utilities.claude_cli_session_store import save_claude_cost_snapshot
        save_claude_cost_snapshot(claude_session_id, snapshot)
    except Exception:
        pass

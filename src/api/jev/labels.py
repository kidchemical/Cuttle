"""Turn labels for My Cuttle Performance.

Jev is used as a cheap batched classifier: turns whose outcome is already
known (thumbs, errors, cancels) never reach it, and the rest go ``BATCH_SIZE``
per System One call with the ask, a trimmed reply, and the user's next message.
"""

from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

from api.jev.client import JevError, get_client, jev_available
from api.jev.types import choice, noul

CACHE_NAME = "jev_turn_labels.json"
LABEL_VERSION = 2
BATCH_SIZE = 10
WORKERS = 4
# Accept nouls inside this band are too close to call; the turn falls back to run success.
UNSURE_BAND = (0.4, 0.6)

MISS_CRITERIA = {
    "ok": "Turn succeeded; no miss",
    "routing": "Wrong harness or model was chosen",
    "model": "Right harness, but the model did a poor job",
    "skill": "Missing skill, rule, or runbook caused the miss",
    "context": "Prompt/context was incomplete or misleading",
    "user": "User changed the ask or rejected a valid result",
    "transport": "Auth, quota, crash, or CLI failure — not quality",
}
# Transport is decided by rule_label from failure_kind, never asked.
_JEV_MISS_CRITERIA = {k: v for k, v in MISS_CRITERIA.items() if k != "transport"}

_CACHE_LOCK = threading.Lock()


def _cache_path() -> Path:
    override = os.environ.get("CUTTLE_JEV_LABEL_CACHE", "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "output" / "dashboards" / CACHE_NAME


def cache_key(row: Dict[str, Any]) -> str:
    return f"{row.get('decision_id')}:{row.get('attempt_index', 0)}"


def load_label_cache(path: Optional[Path] = None) -> Dict[str, Any]:
    p = path or _cache_path()
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_label_cache(cache: Dict[str, Any], path: Optional[Path] = None) -> None:
    p = path or _cache_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def _merge_into_cache(new: Dict[str, Any], path: Optional[Path]) -> None:
    if not new:
        return
    with _CACHE_LOCK:
        cache = load_label_cache(path)
        cache.update(new)
        try:
            save_label_cache(cache, path)
        except OSError:
            pass


def _valid_cached(entry: Any) -> bool:
    return (
        isinstance(entry, dict)
        and not entry.get("skipped")
        and int(entry.get("v") or 0) >= LABEL_VERSION
    )


def rule_label(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Label turns whose outcome is already known, without calling Jev."""
    failure = row.get("failure_kind")
    fb = row.get("user_feedback")
    base = {"v": LABEL_VERSION, "source": "rule", "skipped": False, "confidence": 1.0}
    if failure == "cancelled":
        return {**base, "miss_kind": "user", "accepted": None, "note": "cancelled"}
    if failure == "transport":
        return {**base, "miss_kind": "transport", "accepted": False}
    if fb == "good":
        return {**base, "miss_kind": "ok", "accepted": True, "note": "thumbs"}
    if fb == "bad":
        return {**base, "miss_kind": "model", "accepted": False, "note": "thumbs"}
    if not row.get("success"):
        return {**base, "miss_kind": "model", "accepted": False, "note": "failed"}
    return None


def _fallback(row: Dict[str, Any], note: str) -> Dict[str, Any]:
    return {
        "v": LABEL_VERSION,
        "miss_kind": "ok" if row.get("success") else "model",
        "accepted": None,
        "confidence": 0.0,
        "skipped": True,
        "note": note,
    }


def _turn_state(i: int, row: Dict[str, Any], ctx: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    ctx = ctx or {}
    latency = row.get("latency_ms")
    state = {
        "id": f"t{i}",
        "target": f"{row.get('target_agent')}/{row.get('target_model') or 'default'}",
        "duration_s": round(float(latency) / 1000.0) if latency else None,
        "ask": ctx.get("ask") or None,
        "reply": ctx.get("reply") or None,
        "next_user": ctx.get("next_user"),
        "next_gap_s": ctx.get("next_gap_s"),
    }
    if not state["ask"] and not state["reply"]:
        state["reason"] = str(row.get("reason") or "")[:300]
    return state


def label_batch(
    rows: List[Dict[str, Any]],
    contexts: Optional[Dict[str, Dict[str, Any]]] = None,
    *,
    client=None,
) -> Dict[str, Any]:
    """One System One call for up to ``BATCH_SIZE`` turns.

    Returns ``{"labels": [...], "cost": float, "error": str|None}``; never raises.
    """
    contexts = contexts or {}
    turns = [_turn_state(i, r, contexts.get(str(r.get("query_id") or ""))) for i, r in enumerate(rows)]
    questions: Dict[str, Any] = {}
    for t in turns:
        tid = t["id"]
        if t["next_user"]:
            questions[f"pushback_{tid}"] = noul(
                f"Turn `{tid}`: does `next_user` push back on the reply — say it is wrong, broken, "
                "incomplete, or missed the point, or repeat the same ask? Answering the agent's "
                "question, form answers ([form-answers] / [form-selection]), adding detail, "
                "praise, or moving on to the next step are NOT push-back.",
                true="Pushed back",
                false="Continued or moved on",
            )
        else:
            questions[f"accept_{tid}"] = noul(
                f"Turn `{tid}` was the last message in its chat. Does `reply` plausibly solve `ask`?",
                true="Solves it",
                false="Does not",
            )
        questions[f"miss_{tid}"] = choice(
            f"Turn `{tid}`: if it missed, what kind of miss? Use ok when it worked.",
            _JEV_MISS_CRITERIA,
        )
    try:
        c = client or get_client(timeout_s=20.0)
        result = c.system_one({"turns": turns}, questions)
    except (JevError, Exception) as exc:
        return {"labels": [_fallback(r, "jev_error") for r in rows], "cost": 0.0, "error": str(exc)[:200]}
    labels = []
    now = time.time()
    for t, row in zip(turns, rows):
        miss_ans = result.get(f"miss_{t['id']}")
        if t["next_user"]:
            raw = result.get(f"pushback_{t['id']}").noul
            n = None if raw is None else 1.0 - float(raw)
        else:
            raw = result.get(f"accept_{t['id']}").noul
            n = None if raw is None else float(raw)
        if n is None:
            labels.append(_fallback(row, "no_answer"))
            continue
        accepted: Optional[bool] = None if UNSURE_BAND[0] < n < UNSURE_BAND[1] else n >= UNSURE_BAND[1]
        miss = (miss_ans.choice or "").strip().lower()
        if miss not in _JEV_MISS_CRITERIA:
            miss = "ok"
        if accepted is True:
            miss = "ok"
        elif accepted is False and miss == "ok":
            miss = "model"
        labels.append({
            "v": LABEL_VERSION,
            "source": "jev",
            "miss_kind": miss,
            "accepted": accepted,
            "accept_noul": round(n, 3),
            "confidence": round(abs(n - 0.5) * 2, 3),
            "had_follow_up": t["next_user"] is not None,
            "skipped": False,
            "labeled_at": now,
        })
    cost = float((result.usage or {}).get("cost") or 0.0)
    return {"labels": labels, "cost": cost, "error": None}


def label_turn(
    row: Dict[str, Any],
    *,
    client=None,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Label one outcome row. Never raises."""
    ruled = rule_label(row)
    if ruled:
        return ruled
    if client is None and not jev_available():
        return _fallback(row, "jev_unavailable")
    qid = str(row.get("query_id") or "")
    return label_batch([row], {qid: context} if context else {}, client=client)["labels"][0]


def attach_cached(rows: List[Dict[str, Any]], *, cache_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Attach rule labels and cached Jev labels in place (no network)."""
    cache = load_label_cache(cache_path)
    for row in rows:
        lab = rule_label(row)
        if lab is None:
            cached = cache.get(cache_key(row))
            lab = cached if _valid_cached(cached) else None
        if lab is not None:
            row["jev"] = lab
    return rows


def pending_rows(rows: List[Dict[str, Any]], *, cache_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Rows that still need a Jev call (no rule label, no current cache entry)."""
    cache = load_label_cache(cache_path)
    return [r for r in rows if rule_label(r) is None and not _valid_cached(cache.get(cache_key(r)))]


def label_rows(
    rows: List[Dict[str, Any]],
    *,
    client=None,
    cache_path: Optional[Path] = None,
    force: bool = False,
    limit: int = 80,
    batch_size: int = BATCH_SIZE,
    workers: int = WORKERS,
    auth_db_path: Optional[Path] = None,
    stats: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Attach ``jev`` labels to outcome rows; up to ``limit`` new Jev labels.

    Batches run in parallel and are merged into the on-disk cache as they land.
    """
    out = [dict(r) for r in rows]
    stats = stats if stats is not None else {}
    stats.setdefault("labeled", 0)
    stats.setdefault("cost_usd", 0.0)
    stats.setdefault("errors", 0)
    cache = {} if force else load_label_cache(cache_path)
    todo = []
    for item in out:
        lab = rule_label(item)
        if lab is None:
            cached = cache.get(cache_key(item))
            if _valid_cached(cached):
                lab = cached
        if lab is not None:
            item["jev"] = lab
        else:
            todo.append(item)
    budget, todo = todo[: max(0, int(limit))], todo[max(0, int(limit)):]
    for item in todo:
        item["jev"] = _fallback(item, "label_budget")
    stats["pending"] = len(budget) + len(todo)
    if not budget or (client is None and not jev_available()):
        for item in budget:
            item["jev"] = _fallback(item, "jev_unavailable")
        return out

    from api.agent_router.pinned_outcomes import turn_contexts

    contexts = turn_contexts(budget, auth_db_path=auth_db_path)
    size = max(1, int(batch_size))
    batches = [budget[i:i + size] for i in range(0, len(budget), size)]
    with ThreadPoolExecutor(max_workers=max(1, min(int(workers), len(batches)))) as pool:
        futures = {pool.submit(label_batch, b, contexts, client=client): b for b in batches}
        for fut in as_completed(futures):
            batch = futures[fut]
            res = fut.result()
            fresh = {}
            for item, lab in zip(batch, res["labels"]):
                item["jev"] = lab
                if not lab.get("skipped"):
                    fresh[cache_key(item)] = lab
            if res["error"]:
                stats["errors"] += 1
                stats["last_error"] = res["error"]
            stats["labeled"] += len(fresh)
            stats["pending"] -= len(fresh)
            stats["cost_usd"] += res["cost"]
            _merge_into_cache(fresh, cache_path)
            if stats["errors"] >= 3:
                for f in futures:
                    f.cancel()
                break
    return out


# ── Background labeling (Flask-owned) ─────────────────────────────────────

_BG_LOCK = threading.Lock()
_BG_STATE: Dict[str, Any] = {"running": False}


def labeling_status() -> Dict[str, Any]:
    with _BG_LOCK:
        return dict(_BG_STATE)


def label_pending_async(
    *,
    db_path: Optional[Path] = None,
    cache_path: Optional[Path] = None,
    auth_db_path: Optional[Path] = None,
    budget: int = 5000,
    client=None,
    wait: bool = False,
) -> Dict[str, Any]:
    """Label every unlabeled outcome in the background. Idempotent while running."""
    if client is None:
        if not jev_available():
            return {"running": False, "available": False}
        try:
            from api.jev.config import load_jev_config

            if not load_jev_config().label_turns:
                return {"running": False, "available": False, "disabled": True}
        except Exception:
            pass
    with _BG_LOCK:
        if _BG_STATE.get("running"):
            return dict(_BG_STATE)
        _BG_STATE.clear()
        _BG_STATE.update({"running": True, "available": True, "started_at": time.time(),
                          "labeled": 0, "cost_usd": 0.0, "errors": 0})

    def _run() -> None:
        # Shared with labeling_status() so the dashboard sees progress mid-run.
        stats = _BG_STATE
        try:
            from api.agent_router.outcomes import all_outcomes

            rows = pending_rows(all_outcomes(db_path=db_path), cache_path=cache_path)
            with _BG_LOCK:
                _BG_STATE["pending"] = len(rows)
            if rows:
                label_rows(rows, client=client, cache_path=cache_path, limit=budget,
                           auth_db_path=auth_db_path, stats=stats)
        except Exception as exc:
            stats["last_error"] = str(exc)[:200]
        finally:
            with _BG_LOCK:
                _BG_STATE.update({"running": False, "finished_at": time.time()})
            if stats.get("labeled"):
                print(f"[JEV] labeled {stats['labeled']} turns "
                      f"(${stats.get('cost_usd', 0.0):.4f}, {stats.get('errors', 0)} batch errors)", flush=True)

    if wait:
        _run()
    else:
        threading.Thread(target=_run, name="cuttle-jev-labels", daemon=True).start()
    return labeling_status()

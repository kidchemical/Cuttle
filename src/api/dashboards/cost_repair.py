"""Audited repair of historical Claude API-equivalent estimates.

Only saved concrete model ids or versioned badge labels are eligible. Original
values are retained; aliases without a saved version are never resolved using
 today's CLI defaults. This is a maintenance operation, not chat lookup.
"""
from __future__ import annotations

import copy
import json
import math
import re
import sqlite3
from collections import Counter
from pathlib import Path

from api.model_pricing import estimate_cost_usd, lookup_model_rates

BASIS = "api-equivalent-backfill-v1"


def estimate_saved(meta):
    usage = meta.get("usage") or {}
    chips = (meta.get("slash_command") or {}).get("chips") or []
    if not chips or not isinstance(chips[0], dict) or chips[0].get("category") != "claude":
        return None, "other_agent"
    if usage.get("cost_basis") == BASIS or usage.get("cost_estimated"):
        return None, "already_estimated"
    model = str(usage.get("model") or "")
    if not model.startswith("claude-"):
        match = re.search(r"\b(Opus|Sonnet|Haiku)\s+(\d+)\.(\d+)\b", str(chips[0].get("label") or ""), re.I)
        if not match:
            return None, "ambiguous_model"
        model = "claude-" + "-".join(match.groups()).lower()
    rates = lookup_model_rates(model)
    # No heuristic cache multipliers for a historical repair.
    if not rates or any(rates.get(k) is None for k in ("cache_read", "cache_write")):
        return None, "missing_rates"
    if not all(k in usage for k in ("prompt_tokens", "completion_tokens", "cache_read_tokens")):
        return None, "missing_tokens"
    try:
        counts = [int(usage.get(k) or 0) for k in ("prompt_tokens", "completion_tokens", "cache_read_tokens", "cache_write_tokens")]
        old = float(usage["cost"])
        if any(n < 0 for n in counts) or not math.isfinite(old) or old < 0:
            return None, "invalid_usage"
        cost = estimate_cost_usd(model, counts[0], counts[1], cache_read_tokens=counts[2], cache_write_tokens=counts[3], cache_inclusive=False)
    except (KeyError, TypeError, ValueError):
        return None, "invalid_usage"
    if cost is None:
        return None, "missing_tokens"
    repaired = dict(usage, cost=cost, cost_estimated=True, reported_cost=usage.get("reported_cost", old),
                    cost_basis=BASIS, pricing_model=model, pricing_rates=dict(rates))
    return repaired, "eligible"


def repair_log(data, old_usage, new_usage):
    """Repair exactly one Claude tool and its matching LLM projection."""
    updated = copy.deepcopy(data)
    tools = [t for t in updated.get("tool_calls", []) if t.get("tool_name") == "Claude Code"]
    if len(tools) != 1:
        return None
    tool = tools[0]
    tokens = tool.get("tokens") or {}
    if any(int(tokens.get(k) or 0) != int(old_usage.get(k) or 0) for k in ("prompt_tokens", "completion_tokens", "cache_read_tokens", "cache_write_tokens")):
        return None
    old_cost = float(tool.get("cost") or 0)
    if not math.isclose(old_cost, float(old_usage["cost"]), abs_tol=1e-6):
        return None
    llms = [r for r in updated.get("llm_calls", []) if r.get("model") == tool.get("model")
            and r.get("start_time") == tool.get("start_time") and r.get("end_time") == tool.get("end_time")]
    if len(llms) != 1 or not math.isclose(float(llms[0].get("cost") or 0), old_cost, abs_tol=1e-6):
        return None
    for row in (tool, llms[0]):
        row["reported_cost"] = row.get("reported_cost", row["cost"])
        row["cost"] = new_usage["cost"]
        row["cost_is_estimated"] = True
        row["cost_basis"] = BASIS
        row["pricing_model"] = new_usage["pricing_model"]
    tool["tokens"].update({k: v for k, v in new_usage.items() if k in ("cost_estimated", "cost_basis", "pricing_model", "pricing_rates", "reported_cost")})
    updated["reported_total_cost"] = updated.get("reported_total_cost", updated.get("total_cost"))
    updated["total_cost"] = round(float(updated.get("total_cost") or 0) - old_cost + new_usage["cost"], 6)
    updated["cost_basis"] = BASIS
    return updated


def repair(*, auth_path, outcomes_path, logs_path, backup_dir=None, apply=False):
    auth_path, outcomes_path, logs_path = map(Path, (auth_path, outcomes_path, logs_path))
    conn = sqlite3.connect(auth_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("ATTACH DATABASE ? AS outcomes", (str(outcomes_path),))
    reasons = Counter()
    plans = []
    for row in conn.execute("SELECT id, chat_session_id, metadata FROM chat_messages WHERE role='assistant' AND metadata IS NOT NULL"):
        try:
            meta = json.loads(row["metadata"])
            new, reason = estimate_saved(meta)
        except (ValueError, TypeError, AttributeError):
            reasons["invalid_metadata"] += 1
            continue
        reasons[reason] += 1
        if new is None:
            continue
        qid = str(meta.get("query_id") or "")
        if not re.fullmatch(r"[a-zA-Z0-9-]{1,36}", qid):
            reasons["missing_query_id"] += 1
            continue
        old = meta["usage"]
        outcomes = [dict(r) for r in conn.execute("SELECT id, cost FROM outcomes.router_outcomes WHERE query_id=? AND target_agent='claude' AND session_id=?", (qid, str(row["chat_session_id"]))) ]
        # Multiple attempts cannot be allocated from a single chat aggregate.
        if len(outcomes) > 1 or any(r["cost"] is None or not math.isclose(float(r["cost"]), float(old["cost"]), abs_tol=1e-6) for r in outcomes):
            reasons["ambiguous_outcomes"] += 1
            continue
        files = []
        unmatched = False
        for path in logs_path.glob(f"query_data_{qid}*.json"):
            original = path.read_bytes()
            try:
                data = json.loads(original)
                changed = repair_log(data, old, new)
            except (TypeError, ValueError, KeyError):
                changed = None
            if changed is None:
                reasons["unmatched_log"] += 1
                unmatched = True
                continue
            files.append((path, original, changed))
        if unmatched:
            reasons["skipped_log_mismatch"] += 1
            continue
        meta["usage"] = new
        plans.append(dict(id=row["id"], session=row["chat_session_id"], qid=qid, original=row["metadata"],
                          metadata=meta, old=old["cost"], new=new["cost"], outcomes=outcomes, files=files))
    summary = dict(apply=apply, messages=len(plans), outcomes=sum(len(p["outcomes"]) for p in plans),
                   log_files=sum(len(p["files"]) for p in plans), old_cost=round(sum(p["old"] for p in plans), 6),
                   new_cost=round(sum(p["new"] for p in plans), 6), reasons=dict(reasons))
    if not apply:
        conn.close()
        return summary
    if backup_dir is None:
        raise ValueError("A backup directory is required")
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=False)
    for source in (auth_path, outcomes_path):
        with sqlite3.connect(source) as src, sqlite3.connect(backup_dir / source.name) as dst:
            src.backup(dst)
    journal = [{k: v for k, v in p.items() if k != "files"} for p in plans]
    (backup_dir / "audit.json").write_text(json.dumps(dict(summary=summary, records=journal), indent=2))
    for p in plans:
        for path, original, changed in p["files"]:
            (backup_dir / path.name).write_bytes(original)
    # One transaction across both DBs; compare-and-swap preserves live edits.
    with conn:
        for p in plans:
            count = conn.execute("UPDATE chat_messages SET metadata=? WHERE id=? AND metadata=?", (json.dumps(p["metadata"]), p["id"], p["original"])).rowcount
            if count != 1:
                raise RuntimeError("Chat changed during repair; database transaction rolled back")
            for outcome in p["outcomes"]:
                count = conn.execute("UPDATE outcomes.router_outcomes SET cost=? WHERE id=? AND cost=?", (p["new"], outcome["id"], outcome["cost"])).rowcount
                if count != 1:
                    raise RuntimeError("Outcome changed during repair; database transaction rolled back")
    conn.close()
    written = set()
    for p in plans:
        for path, original, changed in p["files"]:
            if path in written:
                continue
            if path.read_bytes() != original:
                raise RuntimeError(f"Log changed during repair: {path}; backup retained")
            tmp = path.with_suffix(".repair.tmp")
            tmp.write_text(json.dumps(changed, indent=2))
            tmp.replace(path)
            written.add(path)
    summary["backup_dir"] = str(backup_dir)
    (backup_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def resume_logs(*, backup_dir, logs_path):
    """Finish sidecars after an interrupted repair using the saved audit."""
    backup_dir, logs_path = Path(backup_dir), Path(logs_path)
    audit = json.loads((backup_dir / "audit.json").read_text())
    repaired = set()
    already = set()
    for p in audit["records"]:
        old = json.loads(p["original"])["usage"]
        new = p["metadata"]["usage"]
        for backup in backup_dir.glob(f"query_data_{p['qid']}*.json"):
            path = logs_path / backup.name
            if path in repaired or path in already:
                continue
            original = backup.read_bytes()
            changed = repair_log(json.loads(original), old, new)
            if changed is None:
                raise RuntimeError(f"Audit does not match backup: {backup}")
            current = path.read_bytes()
            if json.loads(current) == changed:
                already.add(path)
                continue
            if current != original:
                raise RuntimeError(f"Log changed outside repair: {path}")
            tmp = path.with_suffix(".repair.tmp")
            tmp.write_text(json.dumps(changed, indent=2))
            tmp.replace(path)
            repaired.add(path)
    summary = dict(audit["summary"], backup_dir=str(backup_dir), unique_log_files=len(repaired | already),
                   resumed_logs=len(repaired), already_repaired_logs=len(already))
    (backup_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def verify_repair(*, backup_dir, auth_path, outcomes_path, logs_path):
    audit = json.loads((Path(backup_dir) / "audit.json").read_text())
    errors = []
    unique = {}
    with sqlite3.connect(auth_path) as auth, sqlite3.connect(outcomes_path) as outcomes:
        for p in audit["records"]:
            row = auth.execute("SELECT metadata FROM chat_messages WHERE id=?", (p["id"],)).fetchone()
            if not row or json.loads(row[0]).get("usage") != p["metadata"]["usage"]:
                errors.append(f"message:{p['id']}")
            for outcome in p["outcomes"]:
                row = outcomes.execute("SELECT cost FROM router_outcomes WHERE id=?", (outcome["id"],)).fetchone()
                if not row or not math.isclose(row[0], p["new"], abs_tol=1e-6):
                    errors.append(f"outcome:{outcome['id']}")
            unique[p["qid"]] = p
    for p in unique.values():
        for backup in Path(backup_dir).glob(f"query_data_{p['qid']}*.json"):
            expected = repair_log(json.loads(backup.read_bytes()), json.loads(p["original"])["usage"], p["metadata"]["usage"])
            if json.loads((Path(logs_path) / backup.name).read_bytes()) != expected:
                errors.append(f"log:{backup.name}")
    return dict(unique_turns=len(unique), old_cost=round(sum(p["old"] for p in unique.values()), 6),
                new_cost=round(sum(p["new"] for p in unique.values()), 6), errors=errors)

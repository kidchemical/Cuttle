"""Rank skills / docs for Context Compiler injection."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from api.jev import thresholds as T
from api.jev.client import JevError, get_client, jev_available
from api.jev.types import choice, noul


def _doc_summaries(project_path: Optional[str], inventory: Dict[str, List[str]]) -> List[Dict[str, str]]:
    names = list(inventory.get("docs") or [])
    hub_names: List[str] = []
    try:
        from api.cuttle_brain.context_compiler import _cuttle_hub_root, _list_names

        hub = _cuttle_hub_root()
        if hub:
            hub_names = _list_names(hub / ".cuttle" / "docs", ("*.md",))
    except Exception:
        hub = None
    out: List[Dict[str, str]] = []
    seen = set()
    for name in hub_names + names:
        if name in seen:
            continue
        seen.add(name)
        out.append(
            {
                "id": f"doc:{name}",
                "kind": "doc",
                "name": name,
                "summary": f"Cuttle runbook `{name}`",
            }
        )
        if len(out) >= 40:
            break
    return out


def _skill_summaries() -> List[Dict[str, str]]:
    try:
        from api.markdown_skills import list_markdown_skills
    except Exception:
        return []
    out: List[Dict[str, str]] = []
    for s in list_markdown_skills()[:80]:
        ref = str(s.get("ref") or "")
        if not ref:
            continue
        desc = str(s.get("description") or s.get("name") or ref)[:180]
        out.append({"id": f"skill:{ref}", "kind": "skill", "name": ref, "summary": desc})
    return out


def _read_doc_body(name: str, project_path: Optional[str]) -> str:
    from api.cuttle_brain.context_compiler import _cuttle_dirs, _cuttle_hub_root
    from api.cuttle_brain.personal_overlay import resolve_cuttle_file

    roots: List[Path] = []
    for d in _cuttle_dirs(project_path):
        if d not in roots:
            roots.append(d)
    hub = _cuttle_hub_root()
    if hub:
        hub_cuttle = hub / ".cuttle"
        if hub_cuttle not in roots:
            roots.append(hub_cuttle)
    for root in roots:
        path = resolve_cuttle_file(root, "docs", name)
        if path and path.is_file():
            try:
                return path.read_text(encoding="utf-8")
            except OSError:
                continue
    return ""


def _read_skill_body(ref: str) -> str:
    try:
        from api.markdown_skills import get_markdown_skill
    except Exception:
        return ""
    got = get_markdown_skill(ref)
    if not got:
        return ""
    return str(got.get("body_markdown") or "")


def rank_context(
    user_prompt: str,
    *,
    project_path: Optional[str] = None,
    inventory: Optional[Dict[str, List[str]]] = None,
    client=None,
    max_items: int = T.MAX_INJECT_ITEMS,
) -> Dict[str, Any]:
    """Return {items: [{id, kind, name, body}], meta}. Fail-open: empty items."""
    empty = {"items": [], "meta": {"skipped": True}}
    if not (user_prompt or "").strip():
        return empty
    if client is None and not jev_available():
        return empty

    inv = inventory or {}
    candidates = _doc_summaries(project_path, inv) + _skill_summaries()
    if not candidates:
        return empty
    criteria = {c["id"]: c["summary"] for c in candidates[:80]}
    state = {
        "user_request": (user_prompt or "")[:1500],
        "candidate_count": len(criteria),
    }
    questions = {
        "needs_extra": noul(
            "Would injecting one of the listed runbooks or skills materially help this turn, "
            "or is the always-on rule set already enough?",
            true="Extra skill/doc would help",
            false="No extra context needed",
        ),
        "pick": choice(
            "Which listed runbook or skill is the best extra context for `user_request`?",
            criteria,
        ),
    }
    try:
        c = client or get_client(timeout_s=8.0)
        result = c.system_one(state, questions)
    except (JevError, Exception) as e:
        return {"items": [], "meta": {"error": str(e)[:200], "skipped": True}}

    needs = float(result.get("needs_extra").noul or 0.0)
    pick = result.get("pick")
    conf = float(pick.confidence) if pick.confidence is not None else 0.0
    meta = {
        "skipped": False,
        "needs_extra": needs,
        "confidence": conf,
        "choice": pick.choice,
        "probabilities": dict(pick.probabilities),
        "model": result.model,
        "usage": result.usage,
    }
    if needs < T.NEEDS_EXTRA_CONTEXT_NOUL or conf < T.RANK_CONFIDENCE_FLOOR or not pick.choice:
        return {"items": [], "meta": meta}

    # Winner plus up to two runners-up from the distribution.
    ranked_ids = [pick.choice]
    rest = sorted(
        ((k, v) for k, v in pick.probabilities.items() if k != pick.choice),
        key=lambda kv: kv[1],
        reverse=True,
    )
    for k, _p in rest:
        if k not in ranked_ids:
            ranked_ids.append(k)
        if len(ranked_ids) >= max_items:
            break

    by_id = {c["id"]: c for c in candidates}
    items: List[Dict[str, str]] = []
    used = 0
    for cid in ranked_ids:
        info = by_id.get(cid)
        if not info:
            continue
        if info["kind"] == "doc":
            body = _read_doc_body(info["name"], project_path)
        else:
            body = _read_skill_body(info["name"])
        body = (body or "").strip()
        if not body:
            continue
        if len(body) > T.PER_ITEM_CHARS:
            body = body[: T.PER_ITEM_CHARS].rstrip() + "\n…"
        if used + len(body) > T.MAX_INJECT_CHARS:
            remain = T.MAX_INJECT_CHARS - used
            if remain < 400:
                break
            body = body[:remain].rstrip() + "\n…"
        items.append(
            {
                "id": cid,
                "kind": info["kind"],
                "name": info["name"],
                "body": body,
            }
        )
        used += len(body)
        if len(items) >= max_items:
            break
    meta["injected"] = [i["id"] for i in items]
    return {"items": items, "meta": meta}


def format_ranked_block(items: List[Dict[str, str]]) -> str:
    if not items:
        return ""
    parts = [
        "## Suggested runbooks / skills (ranked for this turn)",
        "",
        "Cuttle selected these because they likely apply. Follow them when relevant; "
        "ignore sections that do not. Always-on rules above still win on conflict.",
        "",
    ]
    for it in items:
        parts.append(f"### {it.get('kind', 'item')} `{it.get('name')}`")
        parts.append(it.get("body") or "")
        parts.append("")
    return "\n".join(parts).strip()


def rank_skill_refs(
    user_prompt: str,
    summaries: List[Dict[str, Any]],
    *,
    max_skills: int = 6,
    client=None,
) -> List[Dict[str, Any]]:
    """Reorder markdown-skill summaries. Fail-open → original order sliced."""
    if not summaries:
        return []
    if client is None and not jev_available():
        return summaries[:max_skills]
    criteria = {}
    for s in summaries[:80]:
        ref = str(s.get("ref") or "")
        if not ref:
            continue
        criteria[ref] = (str(s.get("description") or s.get("name") or ref))[:180]
    if not criteria:
        return summaries[:max_skills]
    questions = {
        "needs_a_skill": noul(
            "Does this user request need any of these skills injected, rather than a direct answer?",
        ),
        "pick": choice("Which skill best serves the user request?", criteria),
    }
    try:
        c = client or get_client(timeout_s=8.0)
        result = c.system_one({"user_request": (user_prompt or "")[:1500]}, questions)
    except Exception:
        return summaries[:max_skills]
    if float(result.get("needs_a_skill").noul or 0.0) < T.NEEDS_EXTRA_CONTEXT_NOUL:
        return []
    pick = result.get("pick")
    by_ref = {str(s.get("ref")): s for s in summaries}
    ordered: List[Dict[str, Any]] = []
    if pick.choice and pick.choice in by_ref:
        ordered.append(by_ref[pick.choice])
    rest = sorted(
        ((k, v) for k, v in pick.probabilities.items() if k != pick.choice),
        key=lambda kv: kv[1],
        reverse=True,
    )
    for k, _p in rest:
        if k in by_ref and by_ref[k] not in ordered:
            ordered.append(by_ref[k])
        if len(ordered) >= max_skills:
            break
    return ordered[:max_skills] if ordered else summaries[:max_skills]

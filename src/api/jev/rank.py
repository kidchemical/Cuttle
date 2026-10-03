"""Rank skills / docs for Context Compiler injection."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from api.jev import thresholds as T
from api.jev.client import JevError, get_client, jev_available
from api.jev.types import choice, noul

# Truncation marker: an injected body that withholds source text must say so
# and point back at the full runbook/skill before any procedure is acted on.
EXCERPT_MARKER = "\n\n… (excerpt — open the full runbook/skill source before acting)"

# Candidate summaries stay informative, not huge: a bounded title plus the
# first meaningful intro line when it adds a useful distinction.
_SUMMARY_TITLE_CHARS = 80
_SUMMARY_INTRO_CHARS = 140


def _doc_title_intro(name: str, project_path: Optional[str]) -> Tuple[str, str]:
    """(title, intro) from the merged doc body (project-first, overlays in).

    Title is the first content line; intro is the next distinct content
    line, or "" when nothing usefully distinguishes it from the title.
    Neither claims to carry the whole document (overlays included).
    """
    distinct: List[str] = []
    for line in (_read_doc_body(name, project_path) or "").splitlines():
        text = line.strip().lstrip("#").strip()
        if text and text not in distinct:
            distinct.append(text)
        if len(distinct) >= 2:
            break
    title = distinct[0][:_SUMMARY_TITLE_CHARS].rstrip() if distinct else ""
    intro = distinct[1][:_SUMMARY_INTRO_CHARS].rstrip() if len(distinct) > 1 else ""
    return title, intro


def _doc_summaries(project_path: Optional[str], inventory: Dict[str, List[str]]) -> List[Dict[str, str]]:
    names = list(inventory.get("docs") or [])
    global_names: List[str] = []
    try:
        from api.cuttle_brain.context_compiler import _cuttle_global_config, _list_names
        from api.cuttle_brain.global_layers import load_global_layers

        global_config = _cuttle_global_config()
        if global_config and load_global_layers(project_path).docs:
            global_names = _list_names(global_config / "docs", ("*.md",))
    except Exception:
        global_config = None
    out: List[Dict[str, str]] = []
    seen = set()
    # Project-first ownership at candidate selection (not just body
    # resolution): project names lead, then remaining global names, under
    # the same cap — so a full global catalog can never crowd out a
    # distinct project runbook. Twins are listed once (project wins).
    for name in names + global_names:
        if name in seen:
            continue
        seen.add(name)
        title, intro = _doc_title_intro(name, project_path)
        summary = f"Cuttle runbook `{name}`"
        if title:
            summary += f" — {title}"
        if intro:
            summary += f" — {intro}"
        out.append(
            {
                "id": f"doc:{name}",
                "kind": "doc",
                "name": name,
                "summary": summary,
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
    from api.cuttle_brain.context_compiler import _cuttle_dirs, _cuttle_global_config
    from api.cuttle_brain.personal_overlay import read_cuttle_file_merged
    from api.cuttle_brain.global_layers import load_global_layers

    try:
        docs_allowed = load_global_layers(project_path).docs
    except Exception:
        docs_allowed = True
    roots: List[Path] = []
    for d in _cuttle_dirs(project_path):
        if d not in roots:
            roots.append(d)
    global_cuttle = _cuttle_global_config()
    if global_cuttle and docs_allowed:
        if global_cuttle not in roots:
            roots.append(global_cuttle)
    for root in roots:
        try:
            body = read_cuttle_file_merged(root, "docs", name)
        except OSError:
            continue
        if body:
            return body
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
    if max_items is not None and max_items <= 0:
        # No room for even the winner: skip before any judge call or read.
        return {"items": [], "meta": {"skipped": True, "injected": [], "excerpt": False}}
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

    # Winner only. The choice distribution holds alternative hypotheses, not
    # independent relevance evidence, so runners-up are never injected no
    # matter how high their probability is.
    meta["excerpt"] = False
    by_id = {c["id"]: c for c in candidates}
    winner = by_id.get(pick.choice)
    if winner is None:
        meta["injected"] = []
        return {"items": [], "meta": meta}
    if winner["kind"] == "doc":
        body = _read_doc_body(winner["name"], project_path)
    else:
        body = _read_skill_body(winner["name"])
    body = (body or "").strip()
    if not body:
        meta["injected"] = []
        return {"items": [], "meta": meta}
    # Marker space is reserved inside the budgets: the whole injected body
    # stays within min(per-item, total). When the budgets cannot hold even
    # the marker, the body stays within them and the excerpt status below
    # carries the signal instead of a silently re-truncated marker.
    cap = min(T.PER_ITEM_CHARS, T.MAX_INJECT_CHARS)
    excerpted = len(body) > cap
    if excerpted:
        if cap > len(EXCERPT_MARKER):
            body = body[: cap - len(EXCERPT_MARKER)].rstrip() + EXCERPT_MARKER
        else:
            body = body[:cap]
    item = {
        "id": pick.choice,
        "kind": winner["kind"],
        "name": winner["name"],
        "body": body,
        "excerpt": excerpted,
    }
    meta["excerpt"] = excerpted
    meta["injected"] = [item["id"]]
    return {"items": [item], "meta": meta}


def format_ranked_block(items: List[Dict[str, Any]]) -> str:
    if not items:
        return ""
    parts = [
        "## Suggested runbooks / skills (ranked for this turn)",
        "",
        "Cuttle selected these because they likely apply. Follow them when relevant; "
        "ignore sections that do not. Always-on rules above still win on conflict.",
        "Injected bodies can be excerpts (marked …); open the named runbook or "
        "skill source before acting on a procedure.",
        "",
    ]
    for it in items:
        body = it.get("body") or ""
        # A tiny budget can hold the excerpt status but not the textual
        # marker: label from the explicit flag so the agent sees it, not
        # just the query logger.
        excerpt_label = (
            " (excerpt — open the full source before acting)"
            if it.get("excerpt") and "(excerpt" not in body
            else ""
        )
        parts.append(f"### {it.get('kind', 'item')} `{it.get('name')}`{excerpt_label}")
        parts.append(body)
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

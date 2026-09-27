"""
Inject Cursor-style markdown skills (SKILL.md) into LLM system context for pipeline runs.

Supports static (all in scope) and dynamic selection via keyword overlap or regex.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from api.markdown_skills import get_markdown_skill, list_markdown_skills

_TOKEN_RE = re.compile(r"[a-z0-9]+", re.I)


def _tokenize(text: str) -> Set[str]:
    return set(_TOKEN_RE.findall((text or "").lower()))


def _parse_refs_blob(blob: str) -> List[str]:
    if not blob or not str(blob).strip():
        return []
    parts = re.split(r"[\s,;]+", str(blob).strip())
    return [p.strip() for p in parts if p.strip()]


def expand_skillset_node_config(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize tool-markdown-skillset node config (preset + custom fields)."""
    preset = (raw or {}).get("preset") or "custom"
    if preset == "cuttle_default":
        out: Dict[str, Any] = {
            "preset": "cuttle_default",
            "skillScope": "all",
            "selectionMode": "dynamic",
            "dynamicStrategy": "keyword",
            "maxSkills": 12,
            "maxInjectChars": 24000,
            "perSkillBodyChars": 6000,
            "skillFilterRegex": "",
            "selectedRefs": [],
        }
        for k in ("maxSkills", "maxInjectChars", "perSkillBodyChars"):
            v = (raw or {}).get(k)
            if v is not None and v != "":
                try:
                    out[k] = int(v)
                except (TypeError, ValueError):
                    pass
        return out

    scope = (raw or {}).get("skillScope") or "all"
    if scope not in ("all", "selected"):
        scope = "all"
    mode = (raw or {}).get("selectionMode") or "dynamic"
    if mode not in ("static", "dynamic"):
        mode = "dynamic"
    strat = (raw or {}).get("dynamicStrategy") or "keyword"
    if strat not in ("keyword", "regex", "jev"):
        strat = "keyword"

    selected = (raw or {}).get("selectedRefs")
    if isinstance(selected, list):
        refs = [str(x).strip() for x in selected if str(x).strip()]
    else:
        refs = _parse_refs_blob(selected or "")

    max_skills = 12
    try:
        max_skills = int((raw or {}).get("maxSkills", 12) or 12)
    except (TypeError, ValueError):
        pass
    max_skills = max(1, min(max_skills, 64))

    max_chars = 24000
    try:
        max_chars = int((raw or {}).get("maxInjectChars", 24000) or 24000)
    except (TypeError, ValueError):
        pass
    max_chars = max(2000, min(max_chars, 120000))

    per_skill = 6000
    try:
        per_skill = int((raw or {}).get("perSkillBodyChars", 6000) or 6000)
    except (TypeError, ValueError):
        pass
    per_skill = max(500, min(per_skill, 32000))

    return {
        "preset": "custom",
        "skillScope": scope,
        "selectionMode": mode,
        "dynamicStrategy": strat,
        "maxSkills": max_skills,
        "maxInjectChars": max_chars,
        "perSkillBodyChars": per_skill,
        "skillFilterRegex": str((raw or {}).get("skillFilterRegex") or "").strip(),
        "selectedRefs": refs,
    }


def _candidate_summaries(cfg: Dict[str, Any], all_skills: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if cfg["skillScope"] == "selected":
        want: Set[str] = set(cfg["selectedRefs"])
        return [s for s in all_skills if s.get("ref") in want]
    return list(all_skills)


def _filter_regex_pool(cfg: Dict[str, Any], summaries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    pat = cfg.get("skillFilterRegex") or ""
    if not pat:
        return summaries
    try:
        rx = re.compile(pat, re.I | re.DOTALL)
    except re.error:
        return summaries
    out: List[Dict[str, Any]] = []
    for s in summaries:
        blob = " ".join(
            [
                str(s.get("ref") or ""),
                str(s.get("name") or ""),
                str(s.get("description") or ""),
                " ".join(s.get("top_headings") or []),
            ]
        )
        if rx.search(blob):
            out.append(s)
    return out if out else summaries


def _rank_keyword(user_prompt: str, summaries: List[Dict[str, Any]], max_skills: int) -> List[Dict[str, Any]]:
    ptoks = _tokenize(user_prompt)
    if not ptoks:
        sorted_all = sorted(summaries, key=lambda x: str(x.get("ref") or ""))
        return sorted_all[:max_skills]
    scored: List[Tuple[int, str, Dict[str, Any]]] = []
    for s in summaries:
        blob = " ".join(
            [
                str(s.get("ref") or ""),
                str(s.get("name") or ""),
                str(s.get("description") or ""),
                " ".join(s.get("top_headings") or []),
            ]
        )
        stoks = _tokenize(blob)
        score = len(ptoks & stoks)
        scored.append((score, str(s.get("ref") or ""), s))
    scored.sort(key=lambda x: (-x[0], x[1]))
    if scored and scored[0][0] == 0:
        # No keyword overlap — don't flood vague/short prompts (e.g. "sup", "hi")
        # with the entire skill library; that bloats the context and steers local
        # models into narrating tool/Ollama errors instead of chatting.
        if len(ptoks) <= 3:
            return []
        sorted_all = sorted(summaries, key=lambda x: str(x.get("ref") or ""))
        return sorted_all[:max_skills]
    return [t[2] for t in scored[:max_skills]]


def _pick_summaries(
    cfg: Dict[str, Any], user_prompt: str, all_skills: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    pool = _candidate_summaries(cfg, all_skills)
    pool = _filter_regex_pool(cfg, pool)
    if not pool:
        return []

    if cfg["selectionMode"] == "static":
        return pool

    # dynamic
    if cfg["dynamicStrategy"] == "regex" and cfg.get("skillFilterRegex"):
        return pool[: cfg["maxSkills"]]

    if cfg["dynamicStrategy"] == "jev":
        try:
            from api.jev.rank import rank_skill_refs

            return rank_skill_refs(user_prompt, pool, max_skills=cfg["maxSkills"])
        except Exception:
            return _rank_keyword(user_prompt, pool, cfg["maxSkills"])

    return _rank_keyword(user_prompt, pool, cfg["maxSkills"])


def _format_skill_block(ref: str, name: str, description: str, body: str) -> str:
    desc = (description or "").strip()
    head = f"### Skill `{ref}` — {name}\n"
    if desc:
        head += f"{desc}\n\n"
    head += body.rstrip() + "\n"
    return head


def build_skills_system_addon(user_prompt: str, skillset_configs: List[Dict[str, Any]]) -> Tuple[str, Dict[str, Any]]:
    """
    Build markdown to append to the system prompt.

    skillset_configs: list of raw node configs from tool-markdown-skillset nodes.
    """
    meta: Dict[str, Any] = {"refs": [], "chars": 0, "blocks": len(skillset_configs), "files": []}
    if not skillset_configs:
        return "", meta

    all_skills = list_markdown_skills()
    expanded = [expand_skillset_node_config(c) for c in skillset_configs]

    seen_refs: Set[str] = set()
    ordered_summaries: List[Dict[str, Any]] = []
    for cfg in expanded:
        picked = _pick_summaries(cfg, user_prompt, all_skills)
        for s in picked:
            r = s.get("ref")
            if r and r not in seen_refs:
                seen_refs.add(r)
                ordered_summaries.append(s)

    if not ordered_summaries:
        return "", {**meta, "refs": [], "reason": "no_skills_matched"}

    max_total = max(expanded, key=lambda x: x["maxInjectChars"])["maxInjectChars"]
    per_skill = max(expanded, key=lambda x: x["perSkillBodyChars"])["perSkillBodyChars"]

    parts: List[str] = [
        "\n\n---\n\n## Cuttle markdown skills (injected)\n\n"
        "The following SKILL.md documents apply to this request. Follow them when relevant; "
        "ignore sections that do not apply.\n\n"
    ]
    used = 0
    refs_out: List[str] = []
    files_out: List[Dict[str, Any]] = []

    for s in ordered_summaries:
        ref = s.get("ref")
        if not ref:
            continue
        detail = get_markdown_skill(ref)
        if not detail:
            continue
        fm = detail.get("frontmatter") or {}
        name = fm.get("name") or s.get("name") or ref
        desc = fm.get("description") or s.get("description") or ""
        if isinstance(desc, list):
            desc = " ".join(str(x) for x in desc)
        elif not isinstance(desc, str):
            desc = str(desc)
        desc = " ".join(desc.split())
        body = (detail.get("body_markdown") or "").strip()
        if len(body) > per_skill:
            body = body[:per_skill] + "\n\n… *(truncated)*\n"

        block = _format_skill_block(ref, str(name), desc, body)
        if used + len(block) > max_total:
            break
        parts.append(block)
        parts.append("\n---\n\n")
        used += len(block)
        refs_out.append(ref)
        display_name = detail.get("path_relative") or f"{ref}/SKILL.md"
        files_out.append({
            "name": display_name,
            "path": display_name,
            "kind": "skill",
            "ref": ref,
            "content": block.strip(),
        })

    if len(parts) <= 1:
        return "", {**meta, "refs": [], "reason": "over_budget_or_empty"}

    addon = "".join(parts).rstrip() + "\n"
    meta["refs"] = refs_out
    meta["chars"] = len(addon)
    meta["files"] = files_out
    return addon, meta

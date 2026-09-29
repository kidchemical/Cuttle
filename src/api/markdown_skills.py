"""
Discover Cuttle markdown skills (SKILL.md) under `.cuttle_global/skills`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore

SRC_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = SRC_DIR.parent
GLOBAL_SKILLS_DIR = REPO_ROOT / ".cuttle_global" / "skills"

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


def _split_frontmatter(raw: str) -> Tuple[Dict[str, Any], str]:
    raw = raw.lstrip("\ufeff")
    if not raw.startswith("---"):
        return {}, raw
    end = raw.find("\n---", 3)
    if end == -1:
        return {}, raw
    block = raw[3:end].strip("\n")
    body = raw[end + 4 :].lstrip("\n")
    if yaml is not None:
        try:
            meta = yaml.safe_load(block) or {}
            if not isinstance(meta, dict):
                return {}, body
            return meta, body
        except Exception:
            return {}, body
    # Minimal fallback: name:/description: single lines only
    meta: Dict[str, Any] = {}
    for line in block.splitlines():
        if ":" in line and not line.startswith(" "):
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip().strip('"')
    return meta, body


def _slugify_heading(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9\s-]", "", text.lower())
    return re.sub(r"\s+", "-", s).strip("-") or "section"


def _outline(body: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for m in _HEADING_RE.finditer(body):
        level = len(m.group(1))
        title = m.group(2).strip()
        out.append({"level": level, "text": title, "slug": _slugify_heading(title)})
    return out


def _read_skill_file(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _scan_dir(root: Path, source: str) -> List[Dict[str, Any]]:
    if not root.is_dir():
        return []
    found: List[Dict[str, Any]] = []
    for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir():
            continue
        skill_md = child / "SKILL.md"
        if not skill_md.is_file():
            continue
        raw = _read_skill_file(skill_md)
        if raw is None:
            continue
        fm, body = _split_frontmatter(raw)
        outline = _outline(body)
        sid = child.name
        name = fm.get("name") or sid.replace("-", " ").title()
        desc = fm.get("description") or ""
        if isinstance(desc, str):
            desc = " ".join(desc.split())
        else:
            desc = str(desc)
        found.append(
            {
                "ref": f"{source}/{sid}",
                "id": sid,
                "source": source,
                "name": name,
                "description": desc[:500] + ("…" if len(desc) > 500 else ""),
                "path_relative": str(skill_md.relative_to(REPO_ROOT)).replace("\\", "/"),
                "heading_count": len(outline),
                "top_headings": [h["text"] for h in outline[:5]],
            }
        )
    return found


def list_markdown_skills() -> List[Dict[str, Any]]:
    """Lightweight list of global Cuttle skills (no full body)."""
    return _scan_dir(GLOBAL_SKILLS_DIR, "global")


def get_markdown_skill(skill_ref: str) -> Optional[Dict[str, Any]]:
    """
    skill_ref is 'global/<dir>'.
    """
    skill_ref = skill_ref.strip().strip("/")
    if "/" not in skill_ref:
        return None
    source, sid = skill_ref.split("/", 1)
    if source != "global" or not sid or "/" in sid or ".." in sid:
        return None
    base = GLOBAL_SKILLS_DIR
    path = (base / sid / "SKILL.md").resolve()
    try:
        path.relative_to(base.resolve())
    except ValueError:
        return None
    if not path.is_file():
        return None
    raw = _read_skill_file(path)
    if raw is None:
        return None
    fm, body = _split_frontmatter(raw)
    outline = _outline(body)
    return {
        "ref": skill_ref,
        "id": sid,
        "source": source,
        "path_relative": str(path.relative_to(REPO_ROOT)).replace("\\", "/"),
        "frontmatter": fm,
        "structure": {"headings": outline},
        "body_markdown": body,
        "char_count": len(body),
    }

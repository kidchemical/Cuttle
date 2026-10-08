"""
Resolve scoped Cuttle skills; project/personal units precede global defaults.
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
    except (OSError, UnicodeError):
        return None


def _resolved_skills(project_path: Optional[str] = None) -> List[Tuple[Dict[str, Any], Path]]:
    from api.cuttle_brain.personal_overlay import scoped_unit_dirs, unit_disabled
    from api.cuttle_brain.global_layers import load_global_layers

    found = []
    seen = set()
    layers = load_global_layers(project_path)
    for root, source in scoped_unit_dirs(project_path, "skills", GLOBAL_SKILLS_DIR.parent,
                                         include_global=layers.skills):
        if not root.is_dir():
            continue
        try:
            children = sorted(root.iterdir(), key=lambda p: (p.name.lower(), p.name))
        except OSError:
            continue
        for child in children:
            path = child / "SKILL.md"
            # Reject symlink escapes; a skill is owned by its configured root.
            try:
                path.resolve().relative_to(root.resolve())
            except (ValueError, OSError):
                continue
            raw = _read_skill_file(path)
            if raw is None:
                continue
            sid = child.name
            key = f"{source}/{sid}".lower() if source.startswith("feature/") else sid.lower()
            if key in seen:
                continue
            seen.add(key)
            fm, body = _split_frontmatter(raw)
            if unit_disabled(fm):
                continue
            integration = str(fm.get("integration") or "").strip().lower()
            if integration and integration not in layers.integrations:
                continue
            outline = _outline(body)
            desc = " ".join(str(fm.get("description") or "").split())
            try:
                relative = path.relative_to(REPO_ROOT).as_posix()
            except ValueError:
                relative = path.as_posix()
            summary = {
                "ref": f"{source}/{sid}", "id": sid, "source": source,
                "name": fm.get("name") or sid.replace("-", " ").title(),
                "description": desc[:500] + ("…" if len(desc) > 500 else ""),
                "path_relative": relative, "path": str(path),
                "heading_count": len(outline), "top_headings": [h["text"] for h in outline[:5]],
            }
            found.append((summary, path))
    return found


def list_markdown_skills(project_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Effective skills, project first; personal replaces the whole skill unit."""
    return [summary for summary, _ in _resolved_skills(project_path)]


def get_markdown_skill(skill_ref: str, project_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Read only an effective ref returned by list for the same project scope."""
    skill_ref = skill_ref.strip().strip("/")
    for summary, path in _resolved_skills(project_path):
        if summary["ref"] != skill_ref:
            continue
        raw = _read_skill_file(path)
        if raw is None:
            return None
        fm, body = _split_frontmatter(raw)
        return {**summary, "frontmatter": fm, "structure": {"headings": _outline(body)},
                "body_markdown": body, "char_count": len(body)}
    return None

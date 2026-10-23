"""Curated Obsidian wiki from a code graph — one note per subsystem, not per node.

Pure rendering (:func:`render`) over a provider-neutral node-link graph plus a
small, marker-guarded writer (:func:`write`). Generated notes carry
``generated: cuttle-knowledge-graph`` in their frontmatter; the writer only
ever rewrites or removes files carrying that marker, so hand-written notes —
even inside the generated folders — survive every regeneration.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from api.knowledge_graph.subsystems import (
    SEP, Subsystem, SubsystemView, build_view, is_file_node,
)

MARKER = "generated: cuttle-knowledge-graph"
SUBSYSTEMS_DIR = "Subsystems"
OVERVIEW = "Architecture/Overview.md"
GENERATED_DIRS = ("Architecture", SUBSYSTEMS_DIR)

MAX_DEPS = 8
MAX_SYMBOLS = 12
MAX_DOCS = 6
MAX_DIAGRAM_EDGES = 24
_DESIGN_DOC = re.compile(r"(^|/)(architecture|adr|adrs|decisions?|design)(/|$)", re.I)
_ROOT_DOC = re.compile(r"^(README|AGENTS|CLAUDE|ARCHITECTURE|DESIGN)\b[^/]*\.md$", re.I)

Doc = Tuple[str, str, str]   # (repo-relative path, absolute path, text)


def _frontmatter(**fields: Any) -> List[str]:
    lines = ["---", MARKER]
    for key, value in fields.items():
        if isinstance(value, list):
            lines.append(f"{key}: [{', '.join(value)}]")
        else:
            lines.append(f'{key}: "{value}"' if isinstance(value, str) else f"{key}: {value}")
    return lines + ["---", ""]


def _one_line(text: str, limit: int = 160) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _doc_link(doc: Doc) -> str:
    return f"[{doc[0]}]({Path(doc[1]).as_uri()})"


def _docs_for(sub: Subsystem, docs: Iterable[Doc]) -> List[Doc]:
    if sub.key.endswith("_*"):
        return []
    needle = sub.key[4:] if sub.key.startswith("src/") else sub.key
    if "/" not in needle:          # "api", "scripts": too generic to match prose
        return []
    pattern = re.compile(rf"(^|[^\w/])(src/)?{re.escape(needle)}(/|\b)")
    return [d for d in docs if pattern.search(d[2])][:MAX_DOCS]


def _deps(counter, heading: str) -> List[str]:
    if not counter:
        return []
    lines = [f"## {heading}", ""]
    for title, n in counter.most_common(MAX_DEPS):
        lines.append(f"- [[{title}]] — {n} reference{'s' if n != 1 else ''}")
    if len(counter) > MAX_DEPS:
        lines.append(f"- …and {len(counter) - MAX_DEPS} more")
    return lines + [""]


def _subsystem_note(sub: Subsystem, docs: List[Doc]) -> str:
    where = sub.key[:-2] + "*" if sub.key.endswith("_*") else (sub.key + "/" if sub.key else "(repository root)")
    lines = _frontmatter(path=where, files=len(sub.files), symbols=len(sub.symbols),
                         tags=["subsystem"])
    lines += [f"# {sub.title}", ""]
    if sub.summary:
        lines += [f"> {_one_line(sub.summary, 300)}", ""]
    lines += [f"`{where}` · {len(sub.files)} files · {len(sub.symbols)} symbols · "
              "[[Overview]]", ""]
    lines += _deps(sub.uses, "Depends on")
    lines += _deps(sub.used_by, "Used by")
    if sub.symbols:
        lines += ["## Key symbols", ""]
        for label, path, loc, degree in sub.symbols[:MAX_SYMBOLS]:
            lines.append(f"- `{label}` — `{path}{':' + loc if loc else ''}` ({degree} links)")
        lines.append("")
    lines += ["## Modules", ""]
    for path in sub.files:
        summary = sub.summaries.get(path)
        lines.append(f"- `{path}`" + (f" — {_one_line(summary)}" if summary else ""))
    lines.append("")
    related = _docs_for(sub, docs)
    if related:
        lines += ["## Documented in", ""] + [f"- {_doc_link(d)}" for d in related] + [""]
    return "\n".join(lines)


def _mermaid_id(title: str, ids: Dict[str, str]) -> str:
    return ids.setdefault(title, f"s{len(ids)}")


def _overview(view: SubsystemView, project: str, meta: Dict[str, Any], docs: List[Doc]) -> str:
    subs = view.subsystems
    symbols = sum(len(s.symbols) for s in subs.values())
    files = sum(len(s.files) for s in subs.values())
    lines = _frontmatter(project=project, subsystems=len(subs), tags=["overview"])
    lines += [f"# {project} — architecture overview", ""]
    head = str(meta.get("head") or "")[:8]
    stamp = ", ".join(x for x in (f"`{head}`" if head else "", meta.get("branch") or "",
                                  meta.get("indexed_at") or "") if x)
    lines += [f"{files} source files · {symbols} symbols · {len(subs)} subsystems"
              + (f" — code graph at {stamp}" if stamp else "") + ".", "",
              "Generated from the code graph; regenerate with "
              "`python -m api.knowledge_graph export`. Write your own notes in "
              "`Notes/` and `Decisions/` — generated notes are replaced.", ""]

    areas: Dict[str, List[Subsystem]] = defaultdict(list)
    for sub in subs.values():
        areas[sub.title.split(SEP)[0]].append(sub)
    lines += ["## Subsystems", ""]
    for area in sorted(areas, key=lambda a: (-sum(len(s.files) for s in areas[a]), a)):
        lines += [f"### {area}", ""]
        for sub in sorted(areas[area], key=lambda s: s.title):
            summary = f" — {_one_line(sub.summary, 110)}" if sub.summary else ""
            lines.append(f"- [[{sub.title}]] ({len(sub.files)} files){summary}")
        lines.append("")

    core = sorted(
        (nid for nid, n in view.nodes.items()
         if nid in view.node_title and n.get("file_type") == "code" and not is_file_node(n)),
        key=lambda nid: (-view.degree.get(nid, 0), nid))[:MAX_SYMBOLS]
    if core:
        lines += ["## Core abstractions", "",
                  "Most-connected symbols — the things most of the code depends on.", ""]
        for nid in core:
            n = view.nodes[nid]
            loc = f":{n['source_location']}" if n.get("source_location") else ""
            lines.append(f"- `{n.get('label')}` — [[{view.node_title[nid]}]] · "
                         f"`{n.get('source_file')}{loc}` ({view.degree.get(nid, 0)} links)")
        lines.append("")

    edges = sorted(((a, b, n) for a, s in subs.items() for b, n in s.uses.items()),
                   key=lambda e: (-e[2], e[0], e[1]))[:MAX_DIAGRAM_EDGES]
    if edges:
        ids: Dict[str, str] = {}
        lines += ["## Main dependencies", "", "```mermaid", "flowchart LR"]
        for a, b, n in edges:
            lines.append(f'  {_mermaid_id(a, ids)}["{a}"] -->|{n}| {_mermaid_id(b, ids)}["{b}"]')
        lines += ["```", ""]

    design = [d for d in docs if _DESIGN_DOC.search(d[0]) or _ROOT_DOC.match(d[0])]
    if design:
        lines += ["## Design docs", "", "Linked from the repository (the source of truth), not copied.", ""]
        lines += [f"- {_doc_link(d)}" for d in sorted(design)] + [""]
    return "\n".join(lines)


def render(graph: Dict[str, Any], *, project: str, meta: Optional[Dict[str, Any]] = None,
           docs: Optional[List[Doc]] = None) -> Dict[str, str]:
    """``{vault-relative path: markdown}`` for one project's generated notes."""
    view = build_view(graph, project)
    docs = list(docs or [])
    notes = {f"{SUBSYSTEMS_DIR}/{sub.title}.md": _subsystem_note(sub, docs)
             for sub in view.subsystems.values()}
    notes[OVERVIEW] = _overview(view, project, dict(meta or {}), docs)
    return notes


def is_generated(path: Path) -> bool:
    try:
        with path.open(encoding="utf-8") as fh:
            return fh.readline().strip() == "---" and fh.readline().strip() == MARKER
    except (OSError, UnicodeDecodeError):
        return False


def write(project_dir: Path, notes: Dict[str, str]) -> Dict[str, int]:
    """Write ``notes`` under ``project_dir``; drop stale generated notes only.

    Unchanged files are left untouched so Obsidian does not re-index them. A
    hand-written note whose name collides with a generated one is kept and the
    generated note is skipped.
    """
    stats = {"written": 0, "unchanged": 0, "removed": 0, "kept_manual": 0}
    for rel, text in notes.items():
        path = project_dir / rel
        if path.exists():
            if not is_generated(path):
                stats["kept_manual"] += 1
                continue
            if path.read_text(encoding="utf-8") == text:
                stats["unchanged"] += 1
                continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        stats["written"] += 1
    wanted = {(project_dir / rel).resolve() for rel in notes}
    for sub in GENERATED_DIRS:
        for path in (project_dir / sub).glob("*.md"):
            if path.resolve() not in wanted and is_generated(path):
                path.unlink()
                stats["removed"] += 1
    return stats

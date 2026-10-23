"""Opt-in AI "How it works" sections for subsystem wiki notes.

Prototype: run explicitly, one subsystem at a time
(``python -m api.knowledge_graph enrich --subsystem …``); indexing and export
never call a model. The pieces:

- :func:`build_input` assembles the prompt deterministically — the subsystem's
  own documentation first (a doc named after it in full; elsewhere only the
  sections headed by it, or the single lines that name it), then module
  docstrings, a symbol outline with line numbers, symbol-level graph edges and
  numbered source of the entry points other subsystems call. It records every
  line and doc section it showed, and fingerprints exactly those inputs.
- :func:`assess_citations` runs once, after generation. For each ``[path:L123]``
  / ``[doc.md#Heading]`` it records whether the model was shown that source
  (*grounded*), whether the cited code or section shares the claim's
  identifiers or key terms (*supported* — a lexical check, not proof), and an
  anchor (line text + enclosing symbol) to find the line again later.
- :func:`render_citations` runs on every export: anchors are re-resolved
  against the current checkout, so links follow code that moved and references
  whose code changed are marked instead of silently pointing at the wrong line.
- :func:`agent_view` is the CLI retrieval shape: explanation + citations, with
  stale fingerprints, outdated refs and missing required facts called out
  instead of silently presented as current.
- Cached as JSON beside the graph (``<store>/explanations/``); the raw model
  text and citation records are kept, links are rendered fresh each export.
  :func:`save` is the only write: it always re-assesses citations, required
  facts and the current fingerprint (including ``--markdown-file``). A short
  ``history`` of prior regenerations tracks omissions and corrections.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

import api.knowledge_graph.sources as sources
from api.knowledge_graph.subsystems import (
    DEPENDENCY_RELATIONS, Subsystem, SubsystemView, is_file_node,
)

PROMPT_VERSION = 3
CACHE_DIR = "explanations"
BEGIN = "<!-- cuttle-kg:explanation begin -->"
END = "<!-- cuttle-kg:explanation end -->"
DISCREPANCIES = "Documentation vs code"
SECTIONS = ("Purpose", "Execution flow", "Key decisions", "Fallback and failure behavior",
            "Interactions", DISCREPANCIES)

PRIMARY_DOC_CHARS = 40_000
SECTION_CHARS = 6_000
LINE_CHARS = 300
MAX_SECONDARY_DOCS = 4
MAX_ENTRYPOINTS = 6
EXCERPT_LINES = 140
MAX_NEIGHBORS = 8
EDGES_PER_NEIGHBOR = 5
_SKIP_DOC = re.compile(r"(todo|backlog)", re.I)

SYSTEM = f"""You write the "How it works" section of one subsystem's note in a software \
project's architecture wiki, for an engineer (or coding agent) new to the code.

Rules:
- The documentation provided is the primary source. Summarize documented behavior; do \
not re-derive it from code.
- Use the module docstrings, symbol outline, graph relationships and code excerpts to \
verify the documentation and fill gaps it leaves.
- State only what the sources support; say so when something is unclear.
- Cite every claim inline with square brackets, placed right after the claim: code as \
[path:L123], docs as [path#Exact heading]. Cite only lines you were shown (a numbered \
excerpt line or a line listed in the outline/relationships) and the specific line that \
shows the behavior, not just the enclosing function's first line when a better one is \
visible. Use only paths and headings that appear in the input.
- Under "{DISCREPANCIES}", neutrally list where the code differs from or goes beyond the \
documentation. Do not judge which is right or recommend changes: some differences are \
intentional. Write "None found." if there are none.
- If the input lists Required facts, each one must appear in the relevant section when \
the sources support it. If they do not, say so under "{DISCREPANCIES}" — do not omit the item.
- Output Markdown with exactly these level-3 headings, in order: \
{", ".join(SECTIONS)}. No other headings, no preamble, no closing summary. \
"Execution flow" is a numbered list. Aim for 450-850 words."""


@dataclass(frozen=True)
class CriticalFact:
    """A behavior a regeneration of this subsystem must not omit."""
    id: str
    summary: str
    needles: Tuple[str, ...]  # all must appear, case-insensitive


# Keyed by Subsystem.key. Keep this small; facts are a checklist, not a spec.
CRITICAL_FACTS: Dict[str, Tuple[CriticalFact, ...]] = {
    "src/api/agent_router": (
        CriticalFact(
            "cancellation_is_terminal",
            "Cancellation is terminal: a [CANCELLED] result from Stop or chat deletion "
            "never escalates and never spawns a fallback.",
            ("cancelled", "never escalat"),
        ),
        CriticalFact(
            "chat_entry_star",
            "The default sticky agent is applied server-side in /api/chat, so a first "
            "turn without the client chip still runs the starred agent instead of "
            "falling through to the router.",
            ("/api/chat", "starred"),
        ),
    ),
    "src/api/knowledge_graph": (
        CriticalFact(
            "enrich_is_opt_in",
            "Model-written explanations are opt-in: index, update, export and sync never "
            "call a model; only `enrich` does.",
            ("opt-in", "enrich"),
        ),
        CriticalFact(
            "store_isolation",
            "Each project and worktree has its own store under "
            "<home>/<store_root>/<project-key>/<checkout-key>/; graphs are never shared "
            "across checkouts.",
            ("project-key", "checkout-key"),
        ),
        CriticalFact(
            "vault_location",
            "Each project's Obsidian vault is "
            "<home>/<store_root>/vault/Cuttle Projects/<Project>/ "
            "(CUTTLE_OBSIDIAN_VAULT overrides the parent folder).",
            ("cuttle projects", "cuttle_obsidian_vault"),
        ),
    ),
}


def facts_for(key: str) -> Tuple[CriticalFact, ...]:
    return CRITICAL_FACTS.get(key) or ()


def missing_facts(key: str, markdown: str) -> List[Dict[str, str]]:
    hay = markdown.lower()
    return [{"id": f.id, "summary": f.summary}
            for f in facts_for(key) if not all(n.lower() in hay for n in f.needles)]


@dataclass
class EnrichInput:
    title: str
    key: str
    prompt: str
    fingerprint: str
    docs: List[str] = field(default_factory=list)
    code_files: List[str] = field(default_factory=list)
    seen_lines: Dict[str, List[Tuple[int, int]]] = field(default_factory=dict)
    seen_sections: Dict[str, Dict[str, str]] = field(default_factory=dict)  # doc -> heading -> text

    @property
    def stats(self) -> Dict[str, Any]:
        return {"prompt_chars": len(self.prompt) + len(SYSTEM),
                "approx_tokens": (len(self.prompt) + len(SYSTEM)) // 4,
                "docs": self.docs, "code_excerpts": self.code_files,
                "fingerprint": self.fingerprint}


def _needle(sub: Subsystem) -> re.Pattern:
    leaf = sub.directory.rstrip("/").rsplit("/", 1)[-1] or sub.title
    parts = [re.escape(p) for p in re.split(r"[_\-\s]+", leaf) if p]
    return re.compile(r"[\s_\-]?".join(parts), re.I)


def _path_re(sub: Subsystem) -> re.Pattern:
    rel = sub.directory[4:] if sub.directory.startswith("src/") else sub.directory
    return re.compile(rf"(^|[^\w/])(src/)?{re.escape(rel)}(/|\b)")


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def select_docs(sub: Subsystem, docs) -> List[Tuple[str, str, str, Dict[str, str]]]:
    """``(path, kind, excerpt, {heading: shown text})`` — docs first, tightly.

    A doc named after the subsystem goes in whole. Elsewhere a section goes in
    whole only when its heading names the subsystem; otherwise only the lines
    that name it or its package path. A doc that never names the package path
    or heads a section with it is passing mention only and is skipped.
    """
    leaf = sub.directory.rstrip("/").rsplit("/", 1)[-1]
    needle, path_re = _needle(sub), _path_re(sub)
    primary, secondary = [], []
    for rel, _abs, text in docs:
        if _SKIP_DOC.search(Path(rel).stem):
            continue
        if leaf and _norm(Path(rel).stem) == _norm(leaf):
            body = text[:PRIMARY_DOC_CHARS]
            primary.append((rel, "full", body,
                            {h: b for h, _l, b in sources.doc_sections(body) if h}))
            continue
        if not path_re.search(text):
            continue
        blocks, shown, score = [], {}, 0
        for heading, level, body in sources.doc_sections(text):
            if needle.search(heading):
                body = body[:SECTION_CHARS]
                blocks.append(f"{'#' * max(level, 2)} {heading}\n{body}".strip())
                shown[heading] = body
                score += 10 + len(needle.findall(body))
                continue
            lines = [ln.strip()[:LINE_CHARS] for ln in body.splitlines()
                     if needle.search(ln) or path_re.search(ln)]
            if lines:
                blocks.append(f"{'#' * max(level, 2)} {heading} (matching lines only)\n" + "\n".join(lines))
                shown[heading] = "\n".join(lines)
                score += len(lines)
        if blocks:
            secondary.append((score, rel, "\n\n".join(blocks), shown))
    secondary.sort(key=lambda s: (-s[0], s[1]))
    return primary + [(rel, "sections", text, shown)
                      for _n, rel, text, shown in secondary[:MAX_SECONDARY_DOCS]]


def _loc(node: Dict[str, Any]) -> int:
    match = re.match(r"L(\d+)", str(node.get("source_location") or ""))
    return int(match.group(1)) if match else 0


def _ref(node: Dict[str, Any]) -> str:
    return f"`{node.get('source_file')}:L{_loc(node)}` {node.get('label')}"


def _cross_edges(view: SubsystemView, graph: Dict[str, Any], sub: Subsystem):
    out: Dict[str, List[Tuple[str, str, str]]] = {}
    inc: Dict[str, List[Tuple[str, str, str]]] = {}
    for link in graph.get("links") or []:
        rel = link.get("relation")
        if rel not in DEPENDENCY_RELATIONS:
            continue
        a, b = str(link.get("source")), str(link.get("target"))
        ta, tb = view.node_title.get(a), view.node_title.get(b)
        if not ta or not tb or ta == tb or sub.title not in (ta, tb):
            continue
        bucket = out.setdefault(tb, []) if ta == sub.title else inc.setdefault(ta, [])
        bucket.append((a, rel, b))
    return out, inc


def _entrypoints(view: SubsystemView, sub: Subsystem, incoming) -> List[str]:
    called_from_outside: Dict[str, int] = {}
    for edges in incoming.values():
        for _a, _rel, b in edges:
            called_from_outside[b] = called_from_outside.get(b, 0) + 1
    candidates = [nid for nid, t in view.node_title.items()
                  if t == sub.title and view.nodes[nid].get("file_type") == "code"
                  and not is_file_node(view.nodes[nid]) and _loc(view.nodes[nid])]
    candidates.sort(key=lambda n: (-(called_from_outside.get(n, 0) * 3 + view.degree.get(n, 0)), n))
    chosen, files = [], set()
    for nid in candidates:
        path = view.nodes[nid].get("source_file")
        if path in files:
            continue
        chosen.append(nid)
        files.add(path)
        if len(chosen) >= MAX_ENTRYPOINTS:
            break
    return chosen


def build_input(view: SubsystemView, graph: Dict[str, Any], sub: Subsystem, root: Path,
                docs, project: str = "Project") -> EnrichInput:
    selected = select_docs(sub, docs)
    outgoing, incoming = _cross_edges(view, graph, sub)
    seen: Dict[str, List[Tuple[int, int]]] = {}

    def saw(path: str, start: int, end: Optional[int] = None) -> None:
        if path and start:
            seen.setdefault(path, []).append((start, end or start))

    where = sub.key[:-2] + "*" if sub.key.endswith("_*") else sub.key + "/"
    parts = [f"Project: {project}", f"Subsystem: {sub.title} (`{where}`, {len(sub.files)} files)", ""]

    parts.append("# Documentation (primary source)")
    if not selected:
        parts.append("(No documentation mentions this subsystem; rely on the code.)")
    for rel, kind, text, _shown in selected:
        label = "full document" if kind == "full" else "only the parts that name this subsystem"
        parts += ["", f"=== DOC {rel} ({label}) ===", text.strip()]

    parts += ["", "# Modules (path — module docstring)"]
    for path in sub.files:
        doc = sub.summaries.get(path, "")
        saw(path, 1)
        parts.append(f"- `{path}`" + (f" — {sources.first_paragraph(doc, 600)}" if doc else ""))

    parts += ["", "# Symbol outline (every symbol in this subsystem, file:line)"]
    by_file: Dict[str, List[Tuple[int, str]]] = {}
    for label, path, loc, _deg in sub.symbols:
        line = _loc({"source_location": loc})
        if line:
            by_file.setdefault(path, []).append((line, label))
    for path in sorted(by_file):
        entries = sorted(set(by_file[path]))
        for line, _label in entries:
            saw(path, line)
        parts.append(f"- `{path}`: " + ", ".join(f"L{line} {label}" for line, label in entries))

    signature: List[str] = []
    for heading, groups in (("Depends on", outgoing), ("Used by", incoming)):
        parts += ["", f"# Graph relationships — {heading} (from the code graph)"]
        for other, edges in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:MAX_NEIGHBORS]:
            parts.append(f"- {other} ({len(edges)} references), e.g.:")
            pairs = set()
            for a, rel, b in edges:
                na, nb = view.nodes[a], view.nodes[b]
                pair = (na.get("label"), rel, nb.get("label"))
                if pair in pairs:
                    continue
                pairs.add(pair)
                signature.append(f"{na.get('source_file')}::{na.get('label')} {rel} "
                                 f"{nb.get('source_file')}::{nb.get('label')}")
                saw(na.get("source_file"), _loc(na))
                saw(nb.get("source_file"), _loc(nb))
                parts.append(f"  - {_ref(na)} {rel} {_ref(nb)}")
                if len(pairs) >= EDGES_PER_NEIGHBOR:
                    break

    excerpts = []
    parts += ["", "# Code excerpts (entry points other subsystems call; numbered lines)"]
    for nid in _entrypoints(view, sub, incoming):
        node = view.nodes[nid]
        start, end, text = sources.symbol_source(root / node["source_file"], _loc(node),
                                                 max_lines=EXCERPT_LINES)
        if not text:
            continue
        saw(node["source_file"], start, min(end, start + EXCERPT_LINES - 1))
        excerpts.append(node["source_file"])
        parts += ["", f"=== {node['source_file']} L{start}-L{end} {node.get('label')} ===", text]

    required = facts_for(sub.key)
    if required:
        parts += ["", "# Required facts (must appear in the explanation if the sources support them)"]
        for i, fact in enumerate(required, 1):
            parts.append(f"{i}. {fact.summary}")

    fingerprint = hashlib.sha256(json.dumps({
        "v": PROMPT_VERSION,
        "files": [(f, sources.file_sha(root / f)) for f in sub.files],
        "docs": [(rel, hashlib.sha256(text.encode()).hexdigest()) for rel, _k, text, _s in selected],
        "edges": sorted(set(signature)),
        "facts": [f.id for f in required],
    }, sort_keys=True).encode()).hexdigest()[:16]
    return EnrichInput(sub.title, sub.key, "\n".join(parts).strip() + "\n", fingerprint,
                       [rel for rel, _k, _t, _s in selected], excerpts, seen,
                       {rel: shown for rel, _k, _t, shown in selected})


# ---------------------------------------------------------------------------
# output cleanup
# ---------------------------------------------------------------------------

_CODE_REF = re.compile(r"\[`?((?:[\w.\-]+/)*[\w.\-]+\.[A-Za-z0-9]+):L(\d+)(?:-L?(\d+))?`?\](?!\()")
_DOC_REF = re.compile(r"\[`?((?:[\w.\-]+/)*[\w.\-]+\.md)#([^\]`]+)`?\](?!\()")
_ANY_REF = re.compile(f"{_CODE_REF.pattern}|{_DOC_REF.pattern}")


def clean_output(text: str) -> str:
    text = text.strip()
    fence = re.match(r"^```(?:markdown|md)?\s*\n(.*)\n```$", text, re.S)
    if fence:
        text = fence.group(1).strip()
    out = []
    for line in text.splitlines():
        match = re.match(r"^(#{1,6})\s+(.*)$", line)
        if match:
            title = match.group(2).strip()
            if len(match.group(1)) <= 2 and title.lower() == "how it works":
                continue
            line = f"### {title}" if len(match.group(1)) <= 3 else f"#### {title}"
        out.append(line)
    return "\n".join(out).strip()


def _refs(markdown: str) -> Iterator[re.Match]:
    return _ANY_REF.finditer(markdown)


# ---------------------------------------------------------------------------
# citation assessment (once, after generation)
# ---------------------------------------------------------------------------

_STOP = frozenset("""about above after again against because before being below between
both could does doing during each every first from further have having here into itself
more most other over same should some such than that their them then there these they
this those through under until very what when where which while with would your only
also just like used uses using without within across returns return calls called""".split())
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _claim(markdown: str, pos: int) -> str:
    """The sentence (bullet) a citation backs: text before it on its line,
    extended to the previous sentence when the citation opens a sentence."""
    start = markdown.rfind("\n", 0, pos) + 1
    end = markdown.find("\n", pos)
    line = markdown[start:end if end != -1 else len(markdown)]
    before = _ANY_REF.sub("", line[: pos - start])
    sentences = re.split(r"(?<=[.!?])\s+", before.strip())
    claim = sentences[-1] if sentences else ""
    if len(claim) < 25 and len(sentences) > 1:
        claim = sentences[-2] + " " + claim
    elif len(claim) < 40 and start > 0:
        # a short sub-bullet ("  - there is no provider") leans on its parent:
        # a less-indented line or a lead-in ending with ":" — never a sibling
        prev_start = markdown.rfind("\n", 0, start - 1) + 1
        prev = markdown[prev_start:start - 1]
        indent = lambda text: len(text) - len(text.lstrip())
        if indent(prev) < indent(line) or _ANY_REF.sub("", prev).rstrip().endswith(":"):
            claim = _ANY_REF.sub("", prev).strip() + " " + claim
    return claim.strip()


def _terms(claim: str) -> Tuple[Set[str], Set[str]]:
    code = set()
    for chunk in re.findall(r"`([^`]+)`", claim):
        code |= {t for t in _IDENT.findall(chunk) if len(t) >= 3}
    for token in _IDENT.findall(claim):
        if "_" in token.strip("_") or re.search(r"[a-z][A-Z]", token):
            code.add(token)
    words = {w.lower()[:6] for w in re.findall(r"[A-Za-z]{5,}", re.sub(r"`[^`]*`", " ", claim))
             if w.lower() not in _STOP}
    return code, words


def _supported(claim: str, region: str) -> bool:
    code, words = _terms(claim)
    hay = region.lower()
    flat = hay.replace("_", "")          # SettingsManager ~ settings_manager
    if any(t.lower().replace("_", "") in flat for t in code):
        return True
    return sum(1 for w in words if w in hay or w in flat) >= 2


def _code_region(path: Path, line: int) -> str:
    lines = sources.lines_of(path)
    sym = sources.enclosing_symbol(path, line)
    lo, hi = (max(sym[1], line - 30), min(sym[2], line + 40)) if sym else (line - 15, line + 15)
    return "\n".join(lines[max(lo, 1) - 1:max(hi, 1)])


def assess_citations(markdown: str, root: Path, inp: EnrichInput) -> List[Dict[str, Any]]:
    """One record per citation, in order of appearance."""
    records = []
    for match in _refs(markdown):
        claim = _claim(markdown, match.start())
        if match.group(1):                                   # code
            rel, line = match.group(1), int(match.group(2))
            path = root / rel
            lines = sources.lines_of(path) if path.is_file() else []
            rec: Dict[str, Any] = {"kind": "code", "path": rel, "line": line, "claim": claim}
            if not (1 <= line <= len(lines)):
                rec.update(exists=False, grounded=False, supported=False)
            else:
                sym = sources.enclosing_symbol(path, line)
                rec.update(
                    exists=True,
                    grounded=any(a <= line <= b for a, b in inp.seen_lines.get(rel, [])),
                    supported=_supported(claim, _code_region(path, line)),
                    anchor={"text": lines[line - 1].strip(),
                            "symbol": sym[0] if sym else None,
                            "offset": line - sym[1] if sym else None})
        else:                                                # doc
            rel, heading = match.group(4), match.group(5).strip()
            rec = {"kind": "doc", "path": rel, "heading": heading, "claim": claim}
            shown = {_norm(h): body for h, body in (inp.seen_sections.get(rel) or {}).items()}
            body = shown.get(_norm(heading))
            exists = _doc_heading_exists(root / rel, heading)
            rec.update(exists=exists, grounded=body is not None,
                       supported=bool(body) and _supported(claim, f"{heading}\n{body}"))
        records.append(rec)
    return records


def _doc_heading_exists(path: Path, heading: str) -> bool:
    if not path.is_file():
        return False
    return _norm(heading) in {_norm(h) for h, _l, _b in sources.doc_sections(sources.read_text(path))}


# ---------------------------------------------------------------------------
# citation rendering (every export, against the current checkout)
# ---------------------------------------------------------------------------

def _relocate(path: Path, anchor: Dict[str, Any], line: int) -> Optional[int]:
    """Where the cited line is now: same line, same offset in the same symbol,
    or the same text within that symbol / the file (nearest to the old line)."""
    lines = sources.lines_of(path)
    text = str(anchor.get("text") or "")
    if 1 <= line <= len(lines) and lines[line - 1].strip() == text:
        return line
    span = sources.find_symbol(path, anchor["symbol"]) if anchor.get("symbol") else None
    if span and anchor.get("offset") is not None:
        cand = span[0] + int(anchor["offset"])
        if 1 <= cand <= len(lines) and lines[cand - 1].strip() == text:
            return cand
    if len(text) < 8:                 # "}", "else:", … say nothing on their own
        return None
    lo, hi = span if span else (1, len(lines))
    hits = [n for n in range(lo, hi + 1) if lines[n - 1].strip() == text]
    return min(hits, key=lambda n: abs(n - line)) if hits else None


def render_citations(markdown: str, root: Path, records: Optional[List[Dict[str, Any]]],
                     *, style: str = "wiki") -> Tuple[str, Dict[str, Any]]:
    """Resolve citations against the current checkout.

    ``wiki``: Obsidian links, ⚠ for gone sources, ``<sup>?</sup>`` for doubtful
    claims. ``agent``: ``[path:Lnow]`` (or ``(outdated)`` / ``(?)``) — no
    ``file://``, no HTML, no callouts.
    """
    counts: Dict[str, Any] = {"total": 0, "supported": 0, "unsupported": 0,
                              "moved": 0, "outdated": 0}
    outdated_refs: List[str] = []
    matches = list(_refs(markdown))
    if records is None or len(records) != len(matches):
        records = [None] * len(matches)          # pre-v2 cache: existence only
    out, last = [], 0
    for match, rec in zip(matches, records):
        out.append(markdown[last:match.start()])
        last = match.end()
        counts["total"] += 1
        doubt = bool(rec) and not (rec.get("grounded") and rec.get("supported"))
        if match.group(1):
            rel, line = match.group(1), int(match.group(2))
            path = root / rel
            if rec and rec.get("anchor"):
                now = _relocate(path, rec["anchor"], line) if path.is_file() else None
            else:
                now = line if path.is_file() and 1 <= line <= sources.line_count(path) else None
            ref = f"{rel}:L{line}"
            if now is None:
                counts["outdated"] += 1
                outdated_refs.append(ref)
                out.append(f"`{rel}:L{line}` (outdated)" if style == "agent" else f"`{rel}:L{line}` ⚠")
                continue
            if now != line:
                counts["moved"] += 1
            label, uri = f"{rel}:L{now}", path.resolve().as_uri()
        else:
            rel, heading = match.group(4), match.group(5).strip()
            path = root / rel
            ref = f"{rel}#{heading}"
            if not _doc_heading_exists(path, heading):
                counts["outdated"] += 1
                outdated_refs.append(ref)
                out.append(f"`{rel} § {heading}` (outdated)" if style == "agent"
                           else f"`{rel} § {heading}` ⚠")
                continue
            label, uri = f"{rel} § {heading}", path.resolve().as_uri()
        counts["unsupported" if doubt else "supported"] += 1
        if style == "agent":
            out.append(f"[{label}]" + (" (?)" if doubt else ""))
        else:
            out.append(f"[{label}]({uri})" + (" <sup>?</sup>" if doubt else ""))
    out.append(markdown[last:])
    if style == "agent":
        counts["outdated_refs"] = outdated_refs
    return "".join(out), counts


# ---------------------------------------------------------------------------
# cache + note block
# ---------------------------------------------------------------------------

def _slug(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", key).strip("-").lower() or "root"


def cache_path(store_root: Path, key: str) -> Path:
    return store_root / CACHE_DIR / f"{_slug(key)}.json"


def load(store_root: Path, key: str) -> Optional[Dict[str, Any]]:
    try:
        entry = json.loads(cache_path(store_root, key).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return entry if isinstance(entry, dict) and entry.get("markdown") else None


def cached_keys(store_root: Path) -> List[Dict[str, Any]]:
    entries = []
    for path in sorted((store_root / CACHE_DIR).glob("*.json")):
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(entry, dict) and entry.get("key") is not None and entry.get("markdown"):
            entries.append(entry)
    return entries


HISTORY_LIMIT = 12


def _fact_ids(value: Any) -> List[str]:
    out = []
    for item in value or []:
        if isinstance(item, dict) and item.get("id"):
            out.append(str(item["id"]))
        elif isinstance(item, str):
            out.append(item)
    return out


def _snapshot(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Compact prior-generation row (ids and counts, not the prose)."""
    cites = entry.get("citations")
    summary = citation_summary(cites) if isinstance(cites, list) else {}
    ex = entry.get("executor") or {}
    return {
        "generated_at": entry.get("generated_at"),
        "fingerprint": entry.get("fingerprint"),
        "executor": {"target": ex.get("target"), "model": ex.get("model")},
        "missing_facts": _fact_ids(entry.get("missing_facts")),
        "citations": {"total": summary.get("total", 0),
                      "grounded": summary.get("grounded", 0),
                      "supported": summary.get("supported", 0)},
    }


def _corrections(previous: Optional[Dict[str, Any]], now_ids: List[str]) -> Dict[str, List[str]]:
    prev = set(_fact_ids((previous or {}).get("missing_facts")))
    now = set(now_ids)
    return {"fixed": sorted(prev - now), "still_missing": sorted(prev & now), "new": sorted(now - prev)}


def save(store_root: Path, inp: EnrichInput, markdown: str,
         executor: Dict[str, Any], usage: Dict[str, Any], *, root: Path) -> Dict[str, Any]:
    """Persist one explanation. Always re-assesses citations, facts, fingerprint.

    ``root`` is the checkout used for citation checks. Callers cannot pass a
    fingerprint or citation records — those come from ``inp`` and the text.
    """
    markdown = clean_output(markdown)
    if not markdown:
        raise ValueError("explanation is empty")
    records = assess_citations(markdown, root, inp)
    omitted = missing_facts(inp.key, markdown)
    previous = load(store_root, inp.key)
    history = list((previous or {}).get("history") or [])
    if previous and previous.get("markdown"):
        snap = _snapshot(previous)
        if not history or history[-1].get("generated_at") != snap.get("generated_at"):
            history.append(snap)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    now_ids = [f["id"] for f in omitted]
    entry = {
        "version": PROMPT_VERSION, "key": inp.key, "title": inp.title,
        "fingerprint": inp.fingerprint,
        "generated_at": generated_at,
        "executor": executor, "usage": usage, "docs": inp.docs,
        "code_excerpts": inp.code_files, "citations": records, "markdown": markdown,
        "missing_facts": omitted,
        "corrections": _corrections(previous, now_ids),
        "history": history[-HISTORY_LIMIT:],
    }
    path = cache_path(store_root, inp.key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entry, indent=1, ensure_ascii=False), encoding="utf-8")
    return entry


def citation_summary(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"total": len(records),
            "grounded": sum(1 for r in records if r.get("grounded")),
            "supported": sum(1 for r in records if r.get("supported")),
            "missing": [f"{r['path']}:L{r.get('line')}" if r["kind"] == "code"
                        else f"{r['path']}#{r.get('heading')}" for r in records if not r.get("exists")],
            "doubtful": [{"ref": f"{r['path']}:L{r.get('line')}" if r["kind"] == "code"
                          else f"{r['path']}#{r.get('heading')}", "claim": r.get("claim"),
                          "grounded": r.get("grounded"), "supported": r.get("supported")}
                         for r in records if r.get("exists") and not (r.get("grounded") and r.get("supported"))]}


def _split_discrepancies(markdown: str) -> Tuple[str, str]:
    match = re.search(rf"^###\s+(?:{re.escape(DISCREPANCIES)}|Documentation gaps)\s*$", markdown, re.M)
    if not match:
        return markdown.strip(), ""
    return markdown[:match.start()].strip(), markdown[match.end():].strip()


def note_section(entry: Dict[str, Any], current_fingerprint: Optional[str], root: Path) -> List[str]:
    """The managed block: "How it works" plus a separate "Documentation vs code"."""
    ex = entry.get("executor") or {}
    who = " ".join(x for x in (ex.get("target"), f"({ex['model']})" if ex.get("model") else "") if x)
    records = entry.get("citations") if isinstance(entry.get("citations"), list) else None
    body, counts = render_citations(str(entry.get("markdown") or ""), root, records)
    explanation, discrepancies = _split_discrepancies(body)
    sources_used = ", ".join(f"`{d}`" for d in entry.get("docs") or []) or "no docs"
    omitted = missing_facts(str(entry.get("key") or ""), str(entry.get("markdown") or ""))

    lines = [BEGIN, "## How it works", ""]
    if current_fingerprint and current_fingerprint != entry.get("fingerprint"):
        lines += ["> [!warning] Out of date",
                  "> The code or docs this was written from have changed. Refresh with "
                  f"`python -m api.knowledge_graph enrich --subsystem \"{entry.get('title')}\"`.", ""]
    if omitted:
        lines += ["> [!warning] Missing required facts",
                  "> " + "; ".join(f["summary"] for f in omitted), ""]
    notes = [f"{counts['total']} citations"]
    if records is not None:
        notes.append(f"{counts['supported']} checked against the cited source")
        if counts["unsupported"]:
            notes.append(f"{counts['unsupported']} not clearly supported by it (?)")
    if counts["moved"]:
        notes.append(f"{counts['moved']} followed code that moved since writing")
    if counts["outdated"]:
        notes.append(f"{counts['outdated']} no longer found (⚠)")
    lines += [f"> [!info] AI-written · {who or 'model'} · {str(entry.get('generated_at', ''))[:10]}",
              f"> From {sources_used} plus {len(entry.get('code_excerpts') or [])} code excerpts, "
              "a symbol outline and graph relationships. " + "; ".join(notes) + ". "
              "The check is lexical (shared identifiers/terms), not proof.", "",
              explanation, ""]
    if discrepancies:
        lines += [f"## {DISCREPANCIES}", "",
                  "> [!note] For review",
                  "> Where the code differs from or goes beyond the docs, found while writing the "
                  "section above. Some differences may be intentional; nothing is changed "
                  "automatically.", "", discrepancies, ""]
    return lines + [END, ""]


def agent_view(entry: Dict[str, Any], current_fingerprint: Optional[str], root: Path
               ) -> Dict[str, Any]:
    """Token-efficient retrieval payload: explanation + citations, no Obsidian chrome."""
    records = entry.get("citations") if isinstance(entry.get("citations"), list) else None
    raw = str(entry.get("markdown") or "")
    body, counts = render_citations(raw, root, records, style="agent")
    explanation, discrepancies = _split_discrepancies(body)
    stale = bool(current_fingerprint and current_fingerprint != entry.get("fingerprint"))
    omitted = missing_facts(str(entry.get("key") or ""), raw)
    outdated = list(counts.pop("outdated_refs", []) or [])
    payload: Dict[str, Any] = {
        "subsystem": entry.get("title"),
        "key": entry.get("key"),
        "status": "stale" if stale else "current",
        "generated_at": entry.get("generated_at"),
        "docs": list(entry.get("docs") or []),
        "approx_tokens": max(1, (len(explanation) + len(discrepancies)) // 4) if (explanation or discrepancies) else 0,
        "explanation": explanation,
        "discrepancies": discrepancies or None,
        "citations": counts,
        "outdated_citations": outdated,
        "missing_facts": omitted,
        "corrections": dict(entry.get("corrections") or {}),
        "history": list(entry.get("history") or [])[-HISTORY_LIMIT:],
        "authoritative": "Source code and documentation remain authoritative.",
    }
    warnings = []
    if stale:
        warnings.append(
            "This explanation is out of date: the code or docs have changed since it was "
            "written. Do not treat it as current. Refresh with "
            f'`python -m api.knowledge_graph enrich --subsystem "{entry.get("title")}"`.'
        )
    if outdated:
        warnings.append(
            f"{len(outdated)} citation(s) no longer resolve against the current checkout: "
            + ", ".join(outdated[:8]) + ("…" if len(outdated) > 8 else "")
        )
    if omitted:
        warnings.append(
            "Required behavioral facts are missing from this explanation: "
            + "; ".join(f["summary"] for f in omitted)
        )
    if warnings:
        payload["warning"] = " ".join(warnings)
    return payload

"""Subsystem view of a code graph — pure, provider-neutral, deterministic.

Input is a node-link graph dict (``{"nodes": [...], "links": [...]}``) whose
nodes carry ``id``, ``label``, ``source_file``, ``source_location`` and
``file_type``, and optionally ``community`` — the shape Graphify (and networkx
``node_link_data``) emits. Output groups source files into human-sized
subsystems (directories, small dirs rolled up, crowded flat dirs split by
filename prefix) and aggregates symbols, module docstrings and cross-subsystem
dependencies. Used for meaningful community names and for the curated wiki.
No I/O, no Cuttle imports.
"""

from __future__ import annotations

import posixpath
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

MIN_FILES = 3          # a directory with fewer files rolls up into its parent
FLAT_SPLIT = 20        # a directory with more direct files splits by prefix
PREFIX_GROUP = 3       # files sharing a stem prefix needed to form a group

DEPENDENCY_RELATIONS = frozenset({
    "calls", "imports", "imports_from", "references", "uses", "inherits",
    "implements", "indirect_call", "re_exports",
})
SEP = " › "
# Path segments that name a build convention rather than a part of the system.
_BOILERPLATE = frozenset({"src", "main", "java", "kotlin", "com", "org", "net", "app",
                          "App", "lib", "res"})
_ENTRY_STEMS = ("__init__", "index", "mod", "main")


def is_file_node(node: Dict[str, Any]) -> bool:
    """Graphify keeps one node per file (label ``a.py`` or ``pkg/a.py``)."""
    path = str(node.get("source_file") or "")
    label = str(node.get("label") or "")
    return bool(path) and (label in (path, posixpath.basename(path)) or path.endswith("/" + label))


@dataclass
class Subsystem:
    key: str                       # repo-relative dir, or "dir/prefix_*"
    title: str                     # unique, filename-safe display name
    files: List[str] = field(default_factory=list)
    symbols: List[Tuple[str, str, str, int]] = field(default_factory=list)  # label, file, loc, degree
    summaries: Dict[str, str] = field(default_factory=dict)  # file -> module docstring line
    uses: Counter = field(default_factory=Counter)      # other title -> edge count
    used_by: Counter = field(default_factory=Counter)

    @property
    def directory(self) -> str:
        return self.key[:-len("_*")].rsplit("/", 1)[0] if self.key.endswith("_*") else self.key

    @property
    def summary(self) -> str:
        """The package docstring (``__init__``/``index``); never a random module's."""
        for name in _ENTRY_STEMS:
            for path, text in self.summaries.items():
                if (posixpath.splitext(posixpath.basename(path))[0] == name
                        and posixpath.dirname(path) == self.directory):
                    return text
        return ""


@dataclass
class SubsystemView:
    subsystems: Dict[str, Subsystem]     # title -> subsystem
    file_to_title: Dict[str, str]
    node_title: Dict[str, str]           # node id -> subsystem title
    degree: Dict[str, int]
    nodes: Dict[str, Dict[str, Any]]


def _stem_prefix(path: str, tokens: int) -> str:
    stem = posixpath.splitext(posixpath.basename(path))[0]
    parts = re.split(r"[_\-.]", stem)
    return "_".join(parts[:tokens]) if len(parts) > tokens and all(parts[:tokens]) else ""


def _prefix_groups(direct: List[str]) -> Dict[str, List[str]]:
    """Filename-prefix groups; a prefix every file shares (``chat_*`` in
    ``js/chat``) says nothing, so it deepens to the next token."""
    for tokens in (1, 2):
        groups: Dict[str, List[str]] = defaultdict(list)
        for f in direct:
            prefix = _stem_prefix(f, tokens)
            if prefix:
                groups[prefix].append(f)
        groups = {p: m for p, m in groups.items() if len(m) >= PREFIX_GROUP}
        if not any(len(m) > 0.6 * len(direct) for m in groups.values()):
            return groups
    return {}


def subsystem_keys(files: Iterable[str]) -> Dict[str, str]:
    """Map each repo-relative file to its subsystem key."""
    files = sorted({f.replace("\\", "/") for f in files if f})
    subtree: Counter = Counter()
    for f in files:
        d = posixpath.dirname(f)
        while True:
            subtree[d] += 1
            if not d:
                break
            d = posixpath.dirname(d)

    keys: Dict[str, str] = {}
    for f in files:
        d = posixpath.dirname(f)
        while d and subtree[d] < MIN_FILES:
            d = posixpath.dirname(d)
        keys[f] = d

    # A rolled-up parent can keep a stray file or two; fold those into the
    # nearest ancestor subsystem so no note covers a single leftover file.
    changed = True
    while changed:
        changed = False
        sizes = Counter(keys.values())
        for d in sorted(sizes, key=lambda k: -k.count("/")):
            if not d or sizes[d] >= MIN_FILES:
                continue
            parent = posixpath.dirname(d)
            while parent and parent not in sizes:
                parent = posixpath.dirname(parent)
            if parent in sizes:
                for f, k in keys.items():
                    if k == d:
                        keys[f] = parent
                changed = True
                break

    by_dir: Dict[str, List[str]] = defaultdict(list)
    for f, d in keys.items():
        if posixpath.dirname(f) == d:
            by_dir[d].append(f)
    for d, direct in by_dir.items():
        if len(direct) <= FLAT_SPLIT:
            continue
        for prefix, members in _prefix_groups(direct).items():
            for f in members:
                keys[f] = posixpath.join(d, f"{prefix}_*") if d else f"{prefix}_*"
    return keys


def _title(key: str, project: str) -> List[str]:
    """Candidate titles, shortest first (later ones disambiguate collisions)."""
    if not key:
        return [f"{project} (root)"]
    group = key.endswith("_*")
    parts = key[:-2].split("/") if group else key.split("/")
    if group:
        parts[-1] = f"{parts[-1]} modules"
    if parts and parts[0] == "src" and len(parts) > 1:
        parts = parts[1:]
    lean = [p for i, p in enumerate(parts) if i == 0 or p not in _BOILERPLATE]
    candidates = []
    for segs in (lean, parts):
        if len(segs) > 4:
            candidates.append(SEP.join([segs[0], "…", *segs[-2:]]))
        candidates.append(SEP.join(segs))
    return list(dict.fromkeys(candidates))


def _safe(title: str) -> str:
    # Obsidian hides dotfiles, so ".cuttle › scripts" would vanish from the vault.
    return "".join(c for c in title if c not in '<>:"/\\|?*#^[]').strip().lstrip(".") or "untitled"


def build_view(graph: Dict[str, Any], project: str = "Project") -> SubsystemView:
    nodes = {str(n.get("id")): n for n in graph.get("nodes") or [] if n.get("id") is not None}
    links = graph.get("links") or graph.get("edges") or []

    degree: Counter = Counter()
    for link in links:
        degree[str(link.get("source"))] += 1
        degree[str(link.get("target"))] += 1

    code_files = {n["source_file"] for n in nodes.values()
                  if n.get("source_file") and n.get("file_type") != "concept"}
    keys = subsystem_keys(code_files)

    titles: Dict[str, str] = {}
    taken: Dict[str, str] = {}
    for key in sorted(set(keys.values()), key=lambda k: (k.count("/"), k)):
        for candidate in _title(key, project):
            if candidate not in taken:
                break
        else:
            candidate = f"{candidate} ({key})"
        candidate = _safe(candidate)
        taken[candidate] = key
        titles[key] = candidate

    subsystems = {t: Subsystem(key=k, title=t) for k, t in titles.items()}
    file_to_title = {f: titles[k] for f, k in keys.items()}
    for f, t in sorted(file_to_title.items()):
        subsystems[t].files.append(f)

    node_title: Dict[str, str] = {}
    for nid, n in nodes.items():
        title = file_to_title.get(n.get("source_file") or "")
        if not title:
            continue
        node_title[nid] = title
        label = str(n.get("label") or "")
        ftype = n.get("file_type")
        if ftype == "rationale":
            if str(n.get("source_location") or "") == "L1":
                subsystems[title].summaries.setdefault(n["source_file"], label)
            continue
        if ftype != "code" or is_file_node(n):
            continue
        subsystems[title].symbols.append(
            (label, n["source_file"], str(n.get("source_location") or ""), degree[nid]))

    for link in links:
        if link.get("relation") not in DEPENDENCY_RELATIONS:
            continue
        a = node_title.get(str(link.get("source")))
        b = node_title.get(str(link.get("target")))
        if a and b and a != b:
            subsystems[a].uses[b] += 1
            subsystems[b].used_by[a] += 1

    for sub in subsystems.values():
        sub.symbols.sort(key=lambda s: (-s[3], s[0]))
    return SubsystemView(subsystems, file_to_title, node_title, dict(degree), nodes)


def community_labels(graph: Dict[str, Any], view: Optional[SubsystemView] = None,
                     project: str = "Project") -> Dict[int, str]:
    """Name each community after the subsystem most of its members live in.

    Communities sharing a subsystem get their hub symbol appended; the label is
    never the bare ``Community N`` placeholder.
    """
    view = view or build_view(graph, project)
    members: Dict[int, List[str]] = defaultdict(list)
    for nid, n in view.nodes.items():
        cid = n.get("community")
        if isinstance(cid, int):
            members[cid].append(nid)

    base: Dict[int, str] = {}
    hubs: Dict[int, str] = {}
    for cid, ids in members.items():
        votes = Counter(view.node_title[i] for i in ids if i in view.node_title)
        # Unresolved imports (``pathlib``, ``Any``) are hubs of everything;
        # a community is named by its own code, not by what it imports.
        local = [i for i in ids if i in view.node_title] or ids
        hub = min(local, key=lambda i: (-view.degree.get(i, 0), i))
        hubs[cid] = str(view.nodes[hub].get("label") or hub).removesuffix("()")
        base[cid] = votes.most_common(1)[0][0] if votes else f"external{SEP}{hubs[cid]}"

    counts = Counter(base.values())
    labels: Dict[int, str] = {}
    used: set = set()
    for cid in sorted(members):
        label = base[cid]
        if counts[label] > 1 and hubs[cid] != label:
            label = f"{label} · {hubs[cid]}"
        if label in used:
            label = f"{label} ({cid})"
        used.add(label)
        labels[cid] = label
    return labels

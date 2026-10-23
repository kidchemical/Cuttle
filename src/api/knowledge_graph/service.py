"""Knowledge-graph facade — provider-neutral verbs over the registry.

Consumers (agents, the skill, tests) call these; the *provider* (Graphify today,
something else tomorrow) owns the engine. Identity, storage location, freshness,
and the Obsidian vault layout are decided here so every provider behaves the same.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.runtime_paths import cuttle_home

import api.knowledge_graph.enrich as enrich_mod
from api.knowledge_graph.sources import module_docstrings
from api.knowledge_graph.subsystems import SubsystemView, build_view
from api.knowledge_graph.wiki import SUBSYSTEMS_DIR, render as render_wiki, write as write_wiki
from api.knowledge_graph.catalog import (
    default_provider_id,
    get_provider,
    get_provider_info,
    provider_status,
)
from api.knowledge_graph.protocol import (
    ExportResult,
    GraphStore,
    IndexResult,
    KnowledgeGraphProvider,
)
from api.knowledge_graph.resolve import ResolvedContext, find_project, resolve
from api.knowledge_graph.store import (
    build_store,
    git_branch,
    git_head,
    list_stored,
    store_root_path,
)

VAULT_ENV = "CUTTLE_OBSIDIAN_VAULT"
VAULT_NAME = "Cuttle Projects"
VAULT_SUBDIRS = ("Architecture", "Decisions", "Notes", SUBSYSTEMS_DIR)

_CODE_EXTS = frozenset({
    ".py", ".pyi", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".go", ".rs",
    ".java", ".kt", ".kts", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".rb",
    ".php", ".swift", ".lua", ".scala", ".sh", ".sql", ".vue", ".dart",
})
_SKIP_DIRS = frozenset({
    ".git", "node_modules", "__pycache__", ".venv", ".venv_wsl", "venv",
    "dist", "build", ".cache", ".next", "target", "Library", "Temp", "obj", "bin",
})


class KnowledgeGraphError(RuntimeError):
    pass


def provider_for(provider_id: Optional[str] = None) -> KnowledgeGraphProvider:
    provider = get_provider(provider_id)
    if provider is None:
        pid = provider_id or default_provider_id()
        raise KnowledgeGraphError(
            f"knowledge-graph provider {pid!r} is not registered "
            f"(available: {[p['id'] for p in provider_status()]})"
        )
    return provider


def is_indexable(path: Optional[str], *, probe_limit: int = 4000) -> bool:
    """True when a path is an accessible codebase worth indexing (never ~)."""
    if not path:
        return False
    try:
        real = Path(path).expanduser().resolve()
    except (OSError, RuntimeError):
        return False
    if not real.is_dir():
        return False
    home = Path.home().resolve()
    if real == home or real in home.parents:
        return False
    if (real / ".git").exists():
        return True
    seen = 0
    for root, dirs, files in os.walk(real):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
        for name in files:
            seen += 1
            if Path(name).suffix.lower() in _CODE_EXTS:
                return True
            if seen >= probe_limit:
                return False
    return False


def store_for(ctx: ResolvedContext, provider_id: Optional[str] = None) -> GraphStore:
    info = get_provider_info(provider_id)
    if info is None:
        raise KnowledgeGraphError(f"provider {provider_id or default_provider_id()!r} not registered")
    return build_store(info, project=ctx.project, execution_path=ctx.execution_path,
                       checkout_uid=ctx.checkout_uid)


def _record_meta(store: GraphStore, extra: Dict[str, Any]) -> Dict[str, Any]:
    meta = {
        "provider": store.provider_id,
        "project_id": store.project.get("id"),
        "project_name": store.project.get("name"),
        "path": store.execution_path,
        "checkout_uid": store.checkout_uid,
        "branch": git_branch(store.execution_path),
        "head": git_head(store.execution_path),
        "indexed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **{k: v for k, v in extra.items() if v is not None},
    }
    store.write_meta(meta)
    return meta


def is_stale(store: GraphStore) -> bool:
    recorded = str(store.read_meta().get("head") or "")
    return bool(recorded) and recorded != git_head(store.execution_path)


def index(ctx: ResolvedContext, *, provider_id: Optional[str] = None,
          force: bool = False) -> Dict[str, Any]:
    store = store_for(ctx, provider_id)
    result: IndexResult = provider_for(provider_id).index(store, force=force)
    if not result.ok:
        raise KnowledgeGraphError(result.error or "index failed")
    meta = _record_meta(store, result.meta)
    return {"ok": True, "artifact": result.artifact, "meta": meta, "stdout": result.stdout}


def update(ctx: ResolvedContext, *, provider_id: Optional[str] = None) -> Dict[str, Any]:
    store = store_for(ctx, provider_id)
    result: IndexResult = provider_for(provider_id).update(store)
    if not result.ok:
        raise KnowledgeGraphError(result.error or "update failed")
    meta = _record_meta(store, result.meta)
    return {"ok": True, "artifact": result.artifact, "meta": meta, "stdout": result.stdout}


def query(ctx: ResolvedContext, question: str, *, budget: int = 1500,
          provider_id: Optional[str] = None) -> str:
    store = store_for(ctx, provider_id)
    return provider_for(provider_id).query(store, question, budget=budget)


def explain(ctx: ResolvedContext, node: str, *, all_connections: bool = False,
            provider_id: Optional[str] = None) -> str:
    store = store_for(ctx, provider_id)
    return provider_for(provider_id).explain(store, node, all_connections=all_connections)


def node_path(ctx: ResolvedContext, source: str, target: str,
              provider_id: Optional[str] = None) -> str:
    store = store_for(ctx, provider_id)
    return provider_for(provider_id).node_path(store, source, target)


# ---------------------------------------------------------------------------
# Obsidian vault (provider-neutral layout; curated wiki from the provider graph)
# ---------------------------------------------------------------------------

def vault_root(provider_id: Optional[str] = None) -> Path:
    """Folder holding one self-contained Obsidian vault per project."""
    override = (os.environ.get(VAULT_ENV) or "").strip()
    if override:
        return Path(override).expanduser()
    info = get_provider_info(provider_id)
    base = store_root_path(info) if info else cuttle_home() / "knowledge_graph"
    return base / "vault" / VAULT_NAME


def _safe_name(name: str) -> str:
    cleaned = "".join(c for c in str(name) if c not in '<>:"/\\|?*').strip()
    return cleaned or "Project"


def project_vault(project: Dict[str, Any], provider_id: Optional[str] = None) -> Path:
    return vault_root(provider_id) / _safe_name(project.get("name") or "Project")


def _project_note(project: Dict[str, Any]) -> str:
    name = _safe_name(project.get("name") or "Project")
    path = project.get("resolved_path") or project.get("path") or ""
    return "\n".join([
        f"# {name}", "", f"- Repository: `{path}`", f"- Project id: `{project.get('id')}`", "",
        "Start at [[Overview]] — the generated subsystem map (`Architecture/`, `Subsystems/`).",
        "Your own notes go in `Notes/` and `Decisions/`; regeneration never touches them.",
        "", "## Notes", "",
    ])


# The pointer line older exports wrote into the (otherwise manual) project note.
_LEGACY_GRAPH_LINE = re.compile(r"^See \[\[[^\]]*(Graph Report|GRAPH_REPORT)\]\].*$", re.M)


def _ensure_project_vault(project: Dict[str, Any], project_dir: Path) -> None:
    for sub in VAULT_SUBDIRS:
        (project_dir / sub).mkdir(parents=True, exist_ok=True)
    # Its own .obsidian makes each project an isolated vault (open the folder).
    (project_dir / ".obsidian").mkdir(exist_ok=True)
    note = project_dir / f"{_safe_name(project.get('name') or 'Project')}.md"
    if not note.exists():
        note.write_text(_project_note(project), encoding="utf-8")
        return
    text = note.read_text(encoding="utf-8")
    if _LEGACY_GRAPH_LINE.search(text):  # only the one generated pointer line
        note.write_text(_LEGACY_GRAPH_LINE.sub(
            "Start at [[Overview]] — the generated subsystem map.", text), encoding="utf-8")


def _archive_legacy_export(project_dir: Path) -> Optional[str]:
    """Move the old per-node ``Graphify/`` export and copied graph report aside."""
    legacy = [p for p in (project_dir / "Graphify",) if p.is_dir()]
    legacy += sorted((project_dir / "Architecture").glob("* Graph Report.md"))
    if not legacy:
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = cuttle_home() / "archive" / "knowledge-graph-legacy" / f"{project_dir.name}-{stamp}"
    dest.mkdir(parents=True, exist_ok=True)
    for path in legacy:
        shutil.move(str(path), str(dest / path.name))
    return str(dest)


def _project_docs(root: str, *, limit: int = 400, max_bytes: int = 512_000) -> List[tuple]:
    """Tracked Markdown docs ``(rel, abs, text)`` the wiki links to (never copies)."""
    base = Path(root)
    try:
        out = subprocess.run(["git", "-C", str(base), "ls-files", "-z", "--", "*.md"],
                             capture_output=True, text=True, timeout=15)
        rels = [r for r in out.stdout.split("\0") if r] if out.returncode == 0 else []
    except (OSError, subprocess.SubprocessError):
        rels = []
    docs = []
    for rel in sorted(rels):
        if any(part in _SKIP_DIRS or part in ("tests", "test") for part in Path(rel).parts[:-1]):
            continue
        path = base / rel
        try:
            if path.stat().st_size > max_bytes:
                continue
            docs.append((rel, str(path), path.read_text(encoding="utf-8", errors="replace")))
        except OSError:
            continue
        if len(docs) >= limit:
            break
    return docs


def export_obsidian(ctx: ResolvedContext, *, provider_id: Optional[str] = None) -> Dict[str, Any]:
    """Regenerate one project's curated vault: Overview + one note per subsystem."""
    provider = provider_for(provider_id)
    store = store_for(ctx, provider_id)
    project_dir = project_vault(ctx.project, provider_id)
    _ensure_project_vault(ctx.project, project_dir)
    graph_data = getattr(provider, "graph_data", None)
    if not callable(graph_data):  # provider without a graph: its own export
        result: ExportResult = provider.export(store, project_dir / "Graph")
        return {"ok": result.ok, "vault": str(project_dir), "reason": result.reason,
                "error": result.error, "stdout": result.stdout}
    archived = _archive_legacy_export(project_dir)
    graph = graph_data(store) if provider.is_indexed(store) else None
    if not graph:
        return {"ok": False, "vault": str(project_dir), "reason": "no graph indexed yet",
                "archived": archived}
    name = _safe_name(ctx.project.get("name") or "Project")
    view = _view(graph, name, ctx.execution_path)
    docs = _project_docs(ctx.execution_path)
    explanations = _explanations(store, view, graph, docs, ctx.execution_path, name)
    notes = render_wiki(graph, project=name, meta=store.read_meta(), docs=docs, view=view,
                        explanations={t: lines for t, (lines, _s) in explanations.items()})
    stats = write_wiki(project_dir, notes)
    return {"ok": True, "vault": str(project_dir), "notes": len(notes), **stats,
            "explanations": {t: state for t, (_l, state) in explanations.items()},
            "archived": archived}


def _view(graph: Dict[str, Any], name: str, root: str) -> SubsystemView:
    files = {n.get("source_file") for n in graph.get("nodes") or [] if n.get("source_file")}
    return build_view(graph, name, module_docstrings(Path(root), sorted(files)))


def _bundle(ctx: ResolvedContext, provider_id: Optional[str] = None):
    """Indexed graph + subsystem view + docs. Raises if nothing is indexed."""
    provider = provider_for(provider_id)
    store = store_for(ctx, provider_id)
    graph_data = getattr(provider, "graph_data", None)
    graph = graph_data(store) if callable(graph_data) and provider.is_indexed(store) else None
    if not graph:
        raise KnowledgeGraphError("no graph indexed yet; run `index` first")
    name = _safe_name(ctx.project.get("name") or "Project")
    view = _view(graph, name, ctx.execution_path)
    docs = _project_docs(ctx.execution_path)
    return store, graph, view, docs, name


def _explanations(store: GraphStore, view: SubsystemView, graph: Dict[str, Any], docs,
                  root: str, name: str) -> Dict[str, tuple]:
    """Cached AI sections by subsystem title, each with ``current``/``stale``.

    Only reads the cache and recomputes fingerprints (no model call); a stale
    entry still renders, under an out-of-date banner.
    """
    by_key = {sub.key: sub for sub in view.subsystems.values()}
    out: Dict[str, tuple] = {}
    for entry in enrich_mod.cached_keys(store.root):
        sub = by_key.get(entry.get("key"))
        if sub is None:
            continue
        current = enrich_mod.build_input(view, graph, sub, Path(root), docs, name).fingerprint
        state = "current" if current == entry.get("fingerprint") else "stale"
        out[sub.title] = (enrich_mod.note_section(entry, current, Path(root)), state)
    return out


def _find_subsystem(view: SubsystemView, query: str):
    q = query.strip().strip("/")
    exact = [s for s in view.subsystems.values()
             if q in (s.title, s.key, s.key.removeprefix("src/"))]
    if exact:
        return exact[0]
    loose = [s for s in view.subsystems.values() if s.key.endswith("/" + q) or s.title.endswith(" " + q)]
    if len(loose) == 1:
        return loose[0]
    names = sorted(s.title for s in (loose or view.subsystems.values()))[:12]
    raise KnowledgeGraphError(f"subsystem {query!r} is {'ambiguous' if loose else 'unknown'}; "
                              f"e.g. {names}")


def _executor_chain(via: Optional[str], model: Optional[str], effort: Optional[str]) -> List[Dict[str, Any]]:
    """``--via kind:target`` or the user's configured ``wiki`` background-helper chain.

    Never the generic default chain: that one is tuned for one-line helpers.
    """
    if via:
        kind, _, target = via.partition(":")
        if kind not in ("api", "cli") or not target:
            raise KnowledgeGraphError("--via must look like cli:claude or api:openai")
        return [{"kind": kind, "target": target, "model": model or "", "effort": effort,
                 "temperature": None, "auth": "subscription" if kind == "cli" else ""}]
    from api import background_helpers

    chain = (background_helpers.preferences().get("tasks") or {}).get("wiki") or []
    if not chain:
        raise KnowledgeGraphError(
            "no executor: pass --via cli:claude (or api:openai …), or set the "
            "'Knowledge wiki explanations' background helper in Settings")
    return chain


def enrich(ctx: ResolvedContext, subsystem: str, *, via: Optional[str] = None,
           model: Optional[str] = None, effort: Optional[str] = None, force: bool = False,
           dry_run: bool = False, show_prompt: bool = False, timeout: float = 600.0,
           markdown: Optional[str] = None, provider_id: Optional[str] = None) -> Dict[str, Any]:
    """Write (or refresh) one subsystem's AI "How it works" section — opt-in.

    Skips the model call when the cached fingerprint still matches its inputs.
    ``markdown`` (CLI ``--markdown-file``) commits supplied text through the
    same citation / required-fact / fingerprint checks as a model reply.
    """
    store, graph, view, docs, name = _bundle(ctx, provider_id)
    sub = _find_subsystem(view, subsystem)
    inp = enrich_mod.build_input(view, graph, sub, Path(ctx.execution_path), docs, name)
    cached = enrich_mod.load(store.root, sub.key)
    result: Dict[str, Any] = {"ok": True, "subsystem": sub.title, **inp.stats}
    if show_prompt:
        result["system"], result["prompt"] = enrich_mod.SYSTEM, inp.prompt
    if markdown is None and cached and cached.get("fingerprint") == inp.fingerprint and not force:
        return {**result, "status": "unchanged", "generated_at": cached.get("generated_at")}
    if dry_run:
        return {**result, "status": "would_generate", "cached": bool(cached)}

    if markdown is not None:
        text, usage = markdown, {}
        executor: Dict[str, Any] = {"target": "markdown", "model": model or "", "effort": effort}
    else:
        from api import background_helpers
        from api.model_providers.types import TextRequest

        chain = _executor_chain(via, model, effort)
        request = TextRequest(user=inp.prompt, system=enrich_mod.SYSTEM, max_tokens=4000,
                              temperature=0.2, timeout=timeout)
        reply = background_helpers.generate("wiki", request, chain=chain)
        if reply is None or not reply.text.strip():
            raise KnowledgeGraphError("no executor in the chain produced an explanation "
                                      "(see the Cuttle log for the per-target failure)")
        text = reply.text
        usage = dict(reply.usage or {})
        executor = {"target": f"{chain[0]['kind']}:{chain[0]['target']}" if len(chain) == 1
                    else (usage.get("agent") or "chain"),
                    "model": usage.get("model") or chain[0].get("model") or "",
                    "effort": chain[0].get("effort")}
    try:
        entry = enrich_mod.save(
            store.root, inp, text, executor,
            {k: v for k, v in usage.items() if isinstance(v, (int, float, str))},
            root=Path(ctx.execution_path))
    except ValueError as exc:
        raise KnowledgeGraphError(str(exc)) from exc
    exported = export_obsidian(ctx, provider_id=provider_id)
    return {**result, "status": "generated",
            "citations": enrich_mod.citation_summary(entry.get("citations") or []),
            "missing_facts": entry.get("missing_facts") or [],
            "corrections": entry.get("corrections") or {},
            "executor": executor, "vault": exported.get("vault")}


def list_notes(ctx: ResolvedContext, *, provider_id: Optional[str] = None) -> Dict[str, Any]:
    """Enriched subsystem notes an agent can retrieve, with freshness."""
    store, graph, view, docs, name = _bundle(ctx, provider_id)
    by_key = {sub.key: sub for sub in view.subsystems.values()}
    notes = []
    for entry in enrich_mod.cached_keys(store.root):
        sub = by_key.get(entry.get("key"))
        if sub is None:
            notes.append({"title": entry.get("title"), "key": entry.get("key"),
                          "status": "orphaned", "generated_at": entry.get("generated_at")})
            continue
        current = enrich_mod.build_input(view, graph, sub, Path(ctx.execution_path),
                                         docs, name).fingerprint
        stale = current != entry.get("fingerprint")
        omitted = enrich_mod.missing_facts(sub.key, str(entry.get("markdown") or ""))
        notes.append({
            "title": sub.title, "key": sub.key,
            "status": "stale" if stale else "current",
            "generated_at": entry.get("generated_at"),
            "approx_tokens": max(1, len(str(entry.get("markdown") or "")) // 4),
            "missing_facts": [f["id"] for f in omitted],
        })
    def _rank(note: Dict[str, Any]) -> tuple:
        status = note.get("status")
        return (0 if status == "stale" else 1 if status == "orphaned" else
                2 if note.get("missing_facts") else 3, note.get("title") or "")
    notes.sort(key=_rank)
    summary = {
        "total": len(notes),
        "current": sum(1 for n in notes if n.get("status") == "current"),
        "stale": sum(1 for n in notes if n.get("status") == "stale"),
        "orphaned": sum(1 for n in notes if n.get("status") == "orphaned"),
        "missing_facts": sum(1 for n in notes if n.get("missing_facts")),
    }
    return {"ok": True, "project": ctx.project.get("name"), "notes": notes,
            "summary": summary,
            "stale": [n["title"] for n in notes if n.get("status") == "stale"],
            "authoritative": "Source code and documentation remain authoritative."}


def retrieve_note(ctx: ResolvedContext, subsystem: str, *,
                  provider_id: Optional[str] = None) -> Dict[str, Any]:
    """One enriched explanation, stripped of Obsidian formatting."""
    store, graph, view, docs, name = _bundle(ctx, provider_id)
    sub = _find_subsystem(view, subsystem)
    entry = enrich_mod.load(store.root, sub.key)
    if entry is None:
        return {"ok": False, "status": "missing", "subsystem": sub.title, "key": sub.key,
                "error": f"no enriched explanation for {sub.title!r}",
                "hint": 'python -m api.knowledge_graph enrich --subsystem '
                        f'"{sub.title}" --via cli:claude'}
    current = enrich_mod.build_input(view, graph, sub, Path(ctx.execution_path),
                                     docs, name).fingerprint
    payload = enrich_mod.agent_view(entry, current, Path(ctx.execution_path))
    return {"ok": True, **payload}


def sync_obsidian(*, index_missing: bool = False,
                  provider_id: Optional[str] = None) -> Dict[str, Any]:
    from api.knowledge_graph.resolve import _projects

    exported, skipped = [], []
    for project in _projects():
        if not (project.get("available") and (project.get("resolved_path") or project.get("path"))):
            continue
        ctx = resolve(project_id=project.get("id"))
        if ctx is None or not is_indexable(ctx.execution_path):
            skipped.append({"project": project.get("name"), "reason": "not a codebase"})
            continue
        store = store_for(ctx, provider_id)
        if not provider_for(provider_id).is_indexed(store):
            if not index_missing:
                skipped.append({"project": project.get("name"), "reason": "not indexed"})
                continue
            index(ctx, provider_id=provider_id)
        result = export_obsidian(ctx, provider_id=provider_id)
        exported.append({"project": project.get("name"),
                         **{k: v for k, v in result.items() if k != "stdout"}})
    return {"ok": True, "vault": str(vault_root(provider_id)),
            "providers": [p["id"] for p in provider_status()],
            "exported": exported, "skipped": skipped}


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------

def status(provider_id: Optional[str] = None) -> Dict[str, Any]:
    from api.knowledge_graph.resolve import _projects

    info = get_provider_info(provider_id)
    active = info.id if info else (provider_id or default_provider_id())
    stored = list_stored(info) if info else []
    by_project: Dict[Any, List[Dict[str, Any]]] = {}
    for row in stored:
        by_project.setdefault(row.get("project_id"), []).append(row)

    rows: List[Dict[str, Any]] = []
    for p in _projects():
        ctx = ResolvedContext(p, p.get("resolved_path") or p.get("path") or "", None)
        try:
            store = store_for(ctx, provider_id)
            indexed = provider_for(provider_id).is_indexed(store)
            meta = store.read_meta()
            stale = is_stale(store)
        except KnowledgeGraphError:
            indexed, meta, stale = False, {}, False
        rows.append({
            "id": p.get("id"), "name": p.get("name"),
            "available": bool(p.get("available")),
            "path": p.get("resolved_path") or p.get("path"),
            "indexed": indexed,
            "artifact": provider_for(provider_id).artifact(store) if indexed else None,
            "branch": meta.get("branch"), "nodes": meta.get("nodes"),
            "edges": meta.get("edges"), "indexed_at": meta.get("indexed_at"),
            "stale": stale,
            "worktree_indexes": sorted(r["checkout_key"] for r in by_project.get(p.get("id"), [])
                                       if r["checkout_key"] != "primary"),
        })
    return {"provider": active, "providers": provider_status(),
            "home": str(store_root_path(info)) if info else "",
            "vault": str(vault_root(provider_id)), "stored_locations": len(stored),
            "projects": rows}

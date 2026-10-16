"""Knowledge-graph facade — provider-neutral verbs over the registry.

Consumers (agents, the skill, tests) call these; the *provider* (Graphify today,
something else tomorrow) owns the engine. Identity, storage location, freshness,
and the Obsidian vault layout are decided here so every provider behaves the same.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.runtime_paths import cuttle_home

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
VAULT_SUBDIRS = ("Architecture", "Decisions", "Notes", "Graphify")

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
# Obsidian vault (provider-neutral layout; provider-owned export)
# ---------------------------------------------------------------------------

def vault_root(provider_id: Optional[str] = None) -> Path:
    override = (os.environ.get(VAULT_ENV) or "").strip()
    if override:
        return Path(override).expanduser()
    info = get_provider_info(provider_id)
    base = store_root_path(info) if info else cuttle_home() / "knowledge_graph"
    return base / "vault" / VAULT_NAME


def _safe_name(name: str) -> str:
    cleaned = "".join(c for c in str(name) if c not in '<>:"/\\|?*').strip()
    return cleaned or "Project"


def _ensure_vault_scaffold(vault: Path, projects: List[Dict[str, Any]]) -> List[str]:
    created: List[str] = []
    vault.mkdir(parents=True, exist_ok=True)
    home_note = vault / f"{VAULT_NAME}.md"
    if not home_note.exists():
        lines = ["# Cuttle Projects", "",
                 "Per-project notes; generated graph notes live in each project's `Graphify/`.",
                 "", "## Projects", ""]
        lines += [f"- [[{_safe_name(p.get('name') or 'Project')}]]" for p in projects]
        home_note.write_text("\n".join(lines) + "\n", encoding="utf-8")
        created.append(str(home_note))
    return created


def _project_note(project: Dict[str, Any]) -> str:
    name = _safe_name(project.get("name") or "Project")
    path = project.get("resolved_path") or project.get("path") or ""
    lines = [f"# {name}", "", f"- Repository: `{path}`",
             f"- Project id: `{project.get('id')}`", "",
             "Existing project documentation is the source of truth and is **linked**, not copied.",
             "", "## Existing documentation"]
    for rel in (".cuttle/docs", "docs/architecture", "docs", "README.md", "AGENTS.md"):
        candidate = Path(path) / rel if path else None
        if candidate and candidate.exists():
            lines.append(f"- [[{rel}]] — `{candidate}`")
    lines += ["", "## Graph", f"See [[{name} Graph Report]] and the `Graphify/` folder.",
              "", "## Notes", "- (manual notes stay here; regeneration never touches them)", ""]
    return "\n".join(lines)


def export_obsidian(ctx: ResolvedContext, *, provider_id: Optional[str] = None) -> Dict[str, Any]:
    provider = provider_for(provider_id)
    store = store_for(ctx, provider_id)
    project_dir = vault_root(provider_id) / _safe_name(ctx.project.get("name") or "Project")
    graph_dir = project_dir / "Graphify"
    graph_dir.mkdir(parents=True, exist_ok=True)
    for sub in VAULT_SUBDIRS:
        (project_dir / sub).mkdir(parents=True, exist_ok=True)
    note = project_dir / f"{_safe_name(ctx.project.get('name') or 'Project')}.md"
    if not note.exists():
        note.write_text(_project_note(ctx.project), encoding="utf-8")
    result: ExportResult = provider.export(store, graph_dir)
    name = _safe_name(ctx.project.get("name") or "Project")
    report_written = None
    gen_report = getattr(provider, "report", None)
    if callable(gen_report):
        try:
            report_path = gen_report(store)
        except Exception:
            report_path = None
        if report_path and Path(report_path).is_file():
            import shutil

            dest = project_dir / "Architecture" / f"{name} Graph Report.md"
            shutil.copyfile(report_path, dest)
            report_written = str(dest)
    return {"ok": result.ok, "project_dir": result.project_dir or str(project_dir),
            "graph_dir": result.graph_dir or str(graph_dir), "report": report_written,
            "reason": result.reason, "error": result.error, "stdout": result.stdout}


def sync_obsidian(*, index_missing: bool = False,
                  provider_id: Optional[str] = None) -> Dict[str, Any]:
    from api.knowledge_graph.resolve import _projects

    projects = _projects()
    created = _ensure_vault_scaffold(vault_root(provider_id), projects)
    exported, skipped = [], []
    for project in projects:
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
    return {"ok": True, "vault": str(vault_root(provider_id)), "created": created,
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

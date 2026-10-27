"""``python -m api.knowledge_graph <verb>`` — provider-neutral graph queries.

One-shot output (see ``.cuttle_global/docs/headless-turns.md``). Default provider
is Graphify; ``--provider`` selects another registered engine.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, List, Optional

from api.knowledge_graph import catalog, service
from api.knowledge_graph.resolve import _projects, find_project, resolve


def _emit(payload: Any) -> int:
    print(json.dumps(payload, indent=2, default=str))
    return 0


def _common(p):
    p.add_argument("--provider", help="provider id (default: configured/graphify)")
    p.add_argument("--project", help="project name or id")
    p.add_argument("--path", help="execution path to resolve")
    p.add_argument("--checkout", help="worktree checkout_uid")


def _ctx(args) -> Optional[service.ResolvedContext]:
    if getattr(args, "project", None):
        project = find_project(project_name=args.project)
        if project is None and str(args.project).isdigit():
            project = find_project(project_id=int(args.project))
        if project is None:
            return None
        return resolve(project_id=project.get("id"), checkout_uid=getattr(args, "checkout", None))
    return resolve(getattr(args, "path", None), checkout_uid=getattr(args, "checkout", None))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m api.knowledge_graph")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("providers", help="registered knowledge-graph providers")
    sub.add_parser("status", help="graph inventory across registered projects")
    sub.add_parser("list", help="alias of status")
    sub.add_parser("vault", help="print the folder holding the per-project Obsidian vaults")
    _common(sub.add_parser("where", help="resolve the active graph for a path/project"))

    p_index = sub.add_parser("index", help="build/refresh a project graph")
    _common(p_index)
    p_index.add_argument("--force", action="store_true", help="full rebuild")
    p_index.add_argument("--all", action="store_true", help="index every accessible project")

    _common(sub.add_parser("update", help="incremental re-extract"))

    p_query = sub.add_parser("query", help="scoped subgraph for a question")
    p_query.add_argument("question")
    _common(p_query)
    p_query.add_argument("--budget", type=int, default=1500)
    p_query.add_argument("--refresh", action="store_true", help="incremental update first")

    p_explain = sub.add_parser("explain", help="explain a node and its neighbors")
    p_explain.add_argument("node")
    _common(p_explain)
    p_explain.add_argument("--all", action="store_true")

    p_path = sub.add_parser("node-path", help="shortest path between two nodes")
    p_path.add_argument("source")
    p_path.add_argument("target")
    _common(p_path)

    _common(sub.add_parser("export", help="regenerate a project's curated Obsidian vault"))

    p_enrich = sub.add_parser(
        "enrich", help="opt-in: AI 'How it works' section for ONE subsystem note (prototype)")
    _common(p_enrich)
    p_enrich.add_argument("--subsystem", required=True,
                          help='note title or path, e.g. "api › agent_router" or src/api/agent_router')
    p_enrich.add_argument("--via", help="executor kind:target, e.g. cli:claude or api:openai "
                                        "(default: the 'wiki' background-helper chain)")
    p_enrich.add_argument("--model")
    p_enrich.add_argument("--effort")
    p_enrich.add_argument("--force", action="store_true", help="regenerate even if inputs are unchanged")
    p_enrich.add_argument("--dry-run", action="store_true", help="assemble inputs only; no model call")
    p_enrich.add_argument("--show-prompt", action="store_true", help="include the assembled prompt")
    p_enrich.add_argument("--timeout", type=float, default=600.0)
    p_enrich.add_argument("--markdown-file",
                          help="commit this file through the same citation/fact/fingerprint "
                               "checks as a model reply (no model call)")

    p_notes = sub.add_parser("notes", help="list enriched subsystem explanations (agent-facing)")
    _common(p_notes)

    p_note = sub.add_parser("note", help="retrieve one enriched explanation (no Obsidian chrome)")
    p_note.add_argument("subsystem")
    _common(p_note)

    p_sync = sub.add_parser("sync", help="regenerate the vaults of all indexed projects")
    p_sync.add_argument("--provider")
    p_sync.add_argument("--index", action="store_true", help="index missing projects first")

    args = parser.parse_args(argv)
    provider_id = getattr(args, "provider", None)

    if args.cmd == "providers":
        return _emit({"providers": catalog.provider_status(),
                      "default": catalog.default_provider_id()})

    if args.cmd in (None, "status", "list"):
        return _emit(service.status(provider_id))

    if args.cmd == "vault":
        return _emit({"vault": str(service.vault_root(provider_id))})

    if args.cmd == "sync":
        return _emit(service.sync_obsidian(index_missing=getattr(args, "index", False),
                                           provider_id=provider_id))

    if args.cmd == "index" and getattr(args, "all", False):
        results = []
        for project in _projects():
            if not (project.get("available") and (project.get("resolved_path") or project.get("path"))):
                continue
            ctx = resolve(project_id=project.get("id"))
            if ctx is None:
                continue
            if not service.is_indexable(ctx.execution_path):
                results.append({"project": project.get("name"), "ok": False, "error": "not a codebase"})
                continue
            try:
                results.append({"project": project.get("name"),
                                **service.index(ctx, provider_id=provider_id, force=args.force)})
            except service.KnowledgeGraphError as exc:
                results.append({"project": project.get("name"), "ok": False, "error": str(exc)})
        return _emit(results)

    ctx = _ctx(args)
    if ctx is None:
        return _emit({"ok": False, "error": "no matching Cuttle project for this path"})

    try:
        if args.cmd == "where":
            store = service.store_for(ctx, provider_id)
            from api.knowledge_graph.catalog import get_provider

            provider = get_provider(provider_id)
            return _emit({
                "provider": provider.id, "project": ctx.project.get("name"),
                "project_id": ctx.project.get("id"), "path": ctx.execution_path,
                "checkout": "primary" if not ctx.checkout_uid else ctx.checkout_uid,
                "artifact": provider.artifact(store), "indexed": provider.is_indexed(store),
                "stale": service.is_stale(store),
            })
        if args.cmd == "index":
            return _emit(service.index(ctx, provider_id=provider_id, force=args.force))
        if args.cmd == "update":
            return _emit(service.update(ctx, provider_id=provider_id))
        if args.cmd == "export":
            return _emit(service.export_obsidian(ctx, provider_id=provider_id))
        if args.cmd == "enrich":
            markdown = None
            if args.markdown_file:
                path = Path(args.markdown_file)
                try:
                    markdown = path.read_text(encoding="utf-8")
                except OSError as exc:
                    return _emit({"ok": False, "error": f"cannot read --markdown-file: {exc}"})
            return _emit(service.enrich(
                ctx, args.subsystem, via=args.via, model=args.model, effort=args.effort,
                force=args.force, dry_run=args.dry_run, show_prompt=args.show_prompt,
                timeout=args.timeout, markdown=markdown, provider_id=provider_id))
        if args.cmd == "notes":
            return _emit(service.list_notes(ctx, provider_id=provider_id))
        if args.cmd == "note":
            return _emit(service.retrieve_note(ctx, args.subsystem, provider_id=provider_id))
        if args.cmd == "query":
            if getattr(args, "refresh", False):
                store = service.store_for(ctx, provider_id)
                from api.knowledge_graph.catalog import get_provider

                if get_provider(provider_id).is_indexed(store):
                    service.update(ctx, provider_id=provider_id)
            out = service.query(ctx, args.question, budget=args.budget, provider_id=provider_id)
            return _emit({"ok": True, "project": ctx.project.get("name"), "result": out})
        if args.cmd == "explain":
            out = service.explain(ctx, args.node, all_connections=getattr(args, "all", False),
                                  provider_id=provider_id)
            return _emit({"ok": True, "result": out})
        if args.cmd == "node-path":
            out = service.node_path(ctx, args.source, args.target, provider_id=provider_id)
            return _emit({"ok": True, "result": out})
    except service.KnowledgeGraphError as exc:
        return _emit({"ok": False, "error": str(exc)})

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())

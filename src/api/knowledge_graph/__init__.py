"""Knowledge-graph protocol + registry + facade (provider-neutral).

Graphify is the bundled provider; see ``src/api/knowledge_graph/providers/``.
Everything agent-facing flows through ``python -m api.knowledge_graph`` so an
alternative engine drops in without changing the skill, docs, or the Brain.
"""

from __future__ import annotations

from api.knowledge_graph.catalog import (
    default_provider_id,
    get_provider,
    list_provider_infos,
    provider_status,
    reset_cache,
)
from api.knowledge_graph.protocol import (
    ExportResult,
    GraphStore,
    IndexResult,
    KnowledgeGraphProvider,
)
from api.knowledge_graph.resolve import ResolvedContext, resolve as resolve_context
from api.knowledge_graph.service import (
    KnowledgeGraphError,
    explain,
    export_obsidian,
    index,
    is_indexable,
    is_stale,
    node_path,
    query,
    status,
    store_for,
    sync_obsidian,
    update,
    vault_root,
)

__all__ = [
    "GraphStore", "IndexResult", "ExportResult", "KnowledgeGraphProvider",
    "ResolvedContext", "resolve_context", "KnowledgeGraphError", "index", "update",
    "query", "explain", "node_path", "export_obsidian", "sync_obsidian",
    "status", "vault_root", "store_for", "is_indexable", "is_stale",
    "get_provider", "provider_status", "default_provider_id",
    "list_provider_infos", "reset_cache",
]

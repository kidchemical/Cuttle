"""Knowledge-graph provider contract — a transport-neutral "protocol" + registry.

Third-party knowledge/code-graph engines integrate by declaring a ``manifest.yaml``
and implementing :class:`KnowledgeGraphProvider`. The facade (``api.knowledge_graph``)
resolves the active project + checkout, hands the provider a provider-owned
:class:`GraphStore` directory, and never assumes any engine's CLI or file format.
Graphify is the bundled default provider; alternatives drop in without touching
the facade, the skill, or the Brain.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Protocol, runtime_checkable


@dataclass(frozen=True)
class GraphStore:
    """Provider-owned artifact directory for one project + checkout.

    The facade decides *where* (stable per-project/per-checkout identity); the
    provider decides *what* lives inside ``root``.
    """

    provider_id: str
    store_root: Path           # <home>/<store_root>  (provider-wide)
    project_key: str
    checkout_key: str
    project: Dict[str, Any]
    execution_path: str
    checkout_uid: Optional[str]

    @property
    def root(self) -> Path:
        return self.store_root / self.project_key / self.checkout_key

    def sub(self, *parts: str) -> Path:
        return self.root.joinpath(*parts)

    @property
    def meta_path(self) -> Path:
        return self.root / "meta.json"

    def read_meta(self) -> Dict[str, Any]:
        import json
        try:
            data = json.loads(self.meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def write_meta(self, meta: Dict[str, Any]) -> None:
        import json
        self.root.mkdir(parents=True, exist_ok=True)
        self.meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n",
                                  encoding="utf-8")


@dataclass
class IndexResult:
    ok: bool
    artifact: Optional[str] = None          # provider-defined primary artifact path
    meta: Dict[str, Any] = field(default_factory=dict)  # counts etc. merged into meta.json
    stdout: str = ""
    error: str = ""


@dataclass
class ExportResult:
    ok: bool
    project_dir: Optional[str] = None
    graph_dir: Optional[str] = None
    stdout: str = ""
    reason: str = ""
    error: str = ""


@runtime_checkable
class KnowledgeGraphProvider(Protocol):
    """Every provider implements this. The facade owns identity + resolution."""

    id: str
    label: str

    def available(self) -> bool:
        """True when the engine is installed/usable on this host."""
        ...

    def is_indexed(self, store: GraphStore) -> bool:
        """True when a usable index exists for this store."""
        ...

    def artifact(self, store: GraphStore) -> Optional[str]:
        """Path to the primary artifact (graph file), or None."""
        ...

    def index(self, store: GraphStore, *, force: bool = False) -> IndexResult:
        """Build or incrementally refresh the index (code-only, local by default)."""
        ...

    def update(self, store: GraphStore) -> IndexResult:
        """Re-index only what changed."""
        ...

    def query(self, store: GraphStore, question: str, *, budget: int) -> str:
        """A scoped (token-bounded) result for a natural-language question."""
        ...

    def explain(self, store: GraphStore, node: str, *, all_connections: bool = False) -> str:
        ...

    def node_path(self, store: GraphStore, source: str, target: str) -> str:
        ...

    def export(self, store: GraphStore, target_dir: Path) -> ExportResult:
        """Export human-facing notes (e.g. Obsidian) into ``target_dir``."""
        ...

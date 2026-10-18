"""Graphify provider — the bundled default knowledge-graph adapter.

Implements :class:`api.knowledge_graph.protocol.KnowledgeGraphProvider` by
invoking the official ``graphify`` CLI against a provider-owned store directory.
Everything engine-specific (binary name, flags, artifact layout, Obsidian
export) lives here; the facade, skill, and Brain stay provider-neutral.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

from api.knowledge_graph.protocol import ExportResult, GraphStore, IndexResult

DEFAULT_BUDGET = 1500
DEFAULT_TIMEOUT = 60.0
INDEX_TIMEOUT = 1800.0

ARTIFACT = ("graphify-out", "graph.json")

DEFAULT_EXCLUDES = (
    "**/node_modules/**", "**/dist/**", "**/build/**", "**/.venv/**",
    "**/venv/**", "**/__pycache__/**", "**/vendor/**", "**/third_party/**",
    "**/ThirdParty/**", "**/site-packages/**", "**/target/**", "**/obj/**",
    "**/.cache/**", "**/coverage/**", "**/.next/**", "**/Library/**",
    "**/Temp/**",
)


class GraphifyError(RuntimeError):
    pass


def graphify_bin() -> Optional[str]:
    found = shutil.which("graphify")
    if found:
        return found
    for candidate in (Path.home() / ".local" / "bin" / "graphify",
                      Path.home() / ".local" / "bin" / "graphify.exe"):
        if candidate.is_file():
            return str(candidate)
    return None


def _run(args: List[str], *, cwd: Optional[str] = None,
         timeout: float = DEFAULT_TIMEOUT) -> subprocess.CompletedProcess:
    binary = graphify_bin()
    if not binary:
        raise GraphifyError("graphify CLI not found — install with `uv tool install graphifyy`")
    env = dict(os.environ)
    env.setdefault("GRAPHIFY_QUERY_LOG_DISABLE", "1")
    return subprocess.run([binary, *args], cwd=cwd, env=env, capture_output=True,
                          text=True, timeout=timeout)


def _artifact(store: GraphStore) -> Path:
    return store.sub(*ARTIFACT)


def _counts(store: GraphStore) -> dict:
    counts = {"nodes": 0, "edges": 0, "code_files": 0}
    try:
        data = json.loads(_artifact(store).read_text(encoding="utf-8"))
        counts["nodes"] = len(data.get("nodes") or [])
        counts["edges"] = len(data.get("links") or [])
    except (OSError, ValueError):
        pass
    try:
        manifest = json.loads(store.sub("graphify-out", "manifest.json").read_text(encoding="utf-8"))
        if isinstance(manifest, dict):
            counts["code_files"] = len(manifest)
    except (OSError, ValueError):
        pass
    return counts


def _exclude_patterns(store: GraphStore) -> List[str]:
    patterns: List[str] = list(DEFAULT_EXCLUDES)
    env = (os.environ.get("CUTTLE_GRAPHIFY_EXCLUDE") or "").strip()
    if env:
        patterns += [p.strip() for p in env.replace(os.pathsep, ",").split(",") if p.strip()]
    for path in (store.store_root / "exclude.txt", store.root / "exclude.txt"):
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    patterns.append(line)
        except OSError:
            continue
    seen, unique = set(), []
    for p in patterns:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


class GraphifyProvider:
    id = "graphify"
    label = "Graphify"

    def available(self) -> bool:
        return graphify_bin() is not None

    def is_indexed(self, store: GraphStore) -> bool:
        return _artifact(store).is_file()

    def artifact(self, store: GraphStore) -> Optional[str]:
        path = _artifact(store)
        return str(path) if path.is_file() else None

    def index(self, store: GraphStore, *, force: bool = False) -> IndexResult:
        if not store.execution_path or not os.path.isdir(store.execution_path):
            return IndexResult(ok=False, error=f"project path is not accessible: {store.execution_path!r}")
        store.root.mkdir(parents=True, exist_ok=True)
        args = ["extract", store.execution_path, "--code-only", "--out", str(store.root)]
        for pattern in _exclude_patterns(store):
            args += ["--exclude", pattern]
        if force:
            args.append("--force")
        try:
            proc = _run(args, timeout=INDEX_TIMEOUT)
        except subprocess.TimeoutExpired:
            return IndexResult(ok=False, error=f"graphify extract timed out after {INDEX_TIMEOUT:.0f}s")
        if proc.returncode != 0:
            return IndexResult(ok=False, error=(proc.stderr or proc.stdout or "graphify extract failed").strip())
        stdout = proc.stdout or ""
        return IndexResult(
            ok=True, artifact=self.artifact(store),
            meta={**_counts(store), "incremental": "incremental scan" in stdout,
                  "engine_version": self._version()},
            stdout=stdout.strip(),
        )

    def update(self, store: GraphStore) -> IndexResult:
        return self.index(store, force=False)

    def _require(self, store: GraphStore) -> str:
        path = self.artifact(store)
        if not path:
            raise GraphifyError(
                f"no graph for {store.project.get('name')!r} "
                f"(checkout={store.checkout_key}); run `index` first"
            )
        return path

    def query(self, store: GraphStore, question: str, *, budget: int = DEFAULT_BUDGET) -> str:
        proc = _run(["query", question, "--graph", self._require(store), "--budget", str(int(budget))])
        if proc.returncode not in (0, 3, 4):
            raise GraphifyError((proc.stderr or proc.stdout or "graphify query failed").strip())
        return proc.stdout.strip()

    def explain(self, store: GraphStore, node: str, *, all_connections: bool = False) -> str:
        args = ["explain", node, "--graph", self._require(store)]
        if all_connections:
            args.append("--all")
        proc = _run(args)
        if proc.returncode not in (0, 3, 4):
            raise GraphifyError((proc.stderr or proc.stdout or "graphify explain failed").strip())
        return proc.stdout.strip()

    def node_path(self, store: GraphStore, source: str, target: str) -> str:
        proc = _run(["path", source, target, "--graph", self._require(store)])
        if proc.returncode not in (0, 3, 4):
            raise GraphifyError((proc.stderr or proc.stdout or "graphify path failed").strip())
        return proc.stdout.strip()

    def export(self, store: GraphStore, target_dir: Path) -> ExportResult:
        artifact = self.artifact(store)
        if not artifact:
            return ExportResult(ok=False, reason="no graph indexed yet",
                                project_dir=str(target_dir.parent))
        target_dir.mkdir(parents=True, exist_ok=True)
        proc = _run(["export", "obsidian", "--graph", artifact, "--dir", str(target_dir)])
        if proc.returncode != 0:
            return ExportResult(ok=False, error=(proc.stderr or proc.stdout or "graphify export failed").strip())
        return ExportResult(ok=True, project_dir=str(target_dir.parent),
                            graph_dir=str(target_dir), stdout=(proc.stdout or "").strip())

    def report(self, store: GraphStore) -> Optional[str]:
        """Human-readable architecture report (LLM-free) + refreshed graph.html.

        Runs ``cluster-only --no-label`` (deterministic community ids, no naming
        LLM); that drops graph.html, so the aggregated view is rebuilt after.
        Returns the ``GRAPH_REPORT.md`` path, or None.
        """
        if not self.artifact(store):
            return None
        proc = _run(["cluster-only", str(store.root), "--no-label", "--no-viz"],
                    timeout=INDEX_TIMEOUT)
        if proc.returncode != 0:
            return None
        _run(["export", "html", "--graph", str(_artifact(store)), "--node-limit", "1200"])
        path = store.sub("graphify-out", "GRAPH_REPORT.md")
        return str(path) if path.is_file() else None

    def _version(self) -> str:
        try:
            out = _run(["--version"])
        except (GraphifyError, subprocess.SubprocessError):
            return ""
        lines = (out.stdout or "").strip().splitlines()
        return lines[0].strip() if lines else ""


def build_provider() -> GraphifyProvider:
    return GraphifyProvider()

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
from api.knowledge_graph.subsystems import community_labels

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
    # Tests are a third of a typical repo's symbols and crowd architecture
    # queries ("configuration" -> test_oauth_status_reports_configuration);
    # agents find tests with grep. Per-project exclude.txt can't re-include.
    "**/tests/**", "**/test/**", "**/Tests/**", "**/__tests__/**", "**/e2e/**",
    "**/test_*.py", "**/*_test.py", "**/*_test.go", "**/*.test.*", "**/*.spec.*",
    # Cuttle's scratch convention ({project}/temp). Graphify matches .gitignore
    # case-sensitively, so a Unity "/Temp/" rule doesn't hide temp/ copies of
    # the whole source tree.
    "temp/**", "tmp/**",
)
LABELS = ".graphify_labels.json"


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
    # Labels are regenerated deterministically on every index, so graphify's
    # per-day graph snapshots (graphify-out/<date>/) would only pile up.
    env.setdefault("GRAPHIFY_NO_BACKUP", "1")
    return subprocess.run([binary, *args], cwd=cwd, env=env, capture_output=True,
                          text=True, timeout=timeout)


def _artifact(store: GraphStore) -> Path:
    return store.sub(*ARTIFACT)


def _counts(store: GraphStore) -> dict:
    # graphify's manifest also lists skipped media, so count files with symbols.
    counts = {"nodes": 0, "edges": 0, "code_files": 0}
    try:
        data = json.loads(_artifact(store).read_text(encoding="utf-8"))
        nodes = data.get("nodes") or []
        counts["nodes"] = len(nodes)
        counts["edges"] = len(data.get("links") or [])
        counts["code_files"] = len({n.get("source_file") for n in nodes if n.get("source_file")})
    except (OSError, ValueError):
        pass
    return counts


def _exclude_patterns(store: GraphStore) -> List[str]:
    patterns: List[str] = list(DEFAULT_EXCLUDES)
    env = (os.environ.get("CUTTLE_GRAPHIFY_EXCLUDE") or "").strip()
    if env:
        patterns += [p.strip() for p in env.replace(os.pathsep, ",").split(",") if p.strip()]
    try:  # a CUTTLE_HOME inside the project must never index its own store
        inside = store.store_root.resolve().relative_to(Path(store.execution_path).resolve())
        patterns.append(f"{inside.as_posix()}/**")
    except (ValueError, OSError):
        pass
    # machine-wide, per-project (all checkouts), per-checkout
    for path in (store.store_root / "exclude.txt", store.root.parent / "exclude.txt",
                 store.root / "exclude.txt"):
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
        self._label(store)
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

    def graph_data(self, store: GraphStore) -> Optional[dict]:
        """The node-link graph (``nodes``/``links``) the curated wiki reads."""
        try:
            return json.loads(_artifact(store).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _label(self, store: GraphStore) -> None:
        """Name communities after their subsystem instead of ``Community N``.

        Writes graphify's own ``.graphify_labels.json`` and lets ``cluster-only``
        apply it to ``graph.json``. With a labels file present graphify takes its
        reuse path (hub-naming any community that changed) and never calls an
        LLM; without one it would auto-detect a paid backend, so the file is
        always written first.
        """
        graph = self.graph_data(store)
        if not graph:
            return
        name = str(store.project.get("name") or "Project")
        labels = community_labels(graph, project=name)
        if not labels:
            return
        out = store.sub("graphify-out")
        (out / LABELS).write_text(json.dumps({str(k): v for k, v in labels.items()},
                                             ensure_ascii=False), encoding="utf-8")
        (out / f"{LABELS}.sig").unlink(missing_ok=True)
        try:
            _run(["cluster-only", str(store.root), "--no-viz"], timeout=INDEX_TIMEOUT)
        except subprocess.TimeoutExpired:
            pass

    def _version(self) -> str:
        try:
            out = _run(["--version"])
        except (GraphifyError, subprocess.SubprocessError):
            return ""
        lines = (out.stdout or "").strip().splitlines()
        return lines[0].strip() if lines else ""


def build_provider() -> GraphifyProvider:
    return GraphifyProvider()

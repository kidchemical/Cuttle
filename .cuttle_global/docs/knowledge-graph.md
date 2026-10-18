# Knowledge graphs — provider protocol, registry, and agent ops

Cuttle treats a knowledge/code-graph engine as a **pluggable 3P provider**. The
protocol + registry live in `src/api/knowledge_graph/`; the agent-facing verb is
`python -m api.knowledge_graph`. **Graphify** (`uv tool install graphifyy`) is
the bundled default provider. An alternative engine drops in without touching
the facade, the skill, or the Cuttle Brain.

## The contract (what a provider ships)

A provider is a directory with a `manifest.yaml` + `adapter.py`:

```yaml
id: graphify            # must equal the folder name
label: Graphify
store_root: graphify    # state namespace: <home>/graphify/…
capabilities: [index, update, query, explain, node_path, export]
executable_names: [graphify]
install_hint: "uv tool install graphifyy"
```

`adapter.py` exposes `build_provider()` returning an object implementing
`KnowledgeGraphProvider` (`api/knowledge_graph/protocol.py`): `available`,
`is_indexed`, `artifact`, `index`, `update`, `query`, `explain`, `node_path`,
`export`. The facade hands it a **provider-owned `GraphStore` directory** and
never assumes the CLI or file format.

## Discovery + selection

Discovery order (first definition of an id wins; validated before any import):

1. bundled — `src/api/knowledge_graph/providers/<id>/`
2. user — `<home>/knowledge_graph/providers/<id>/`
3. `CUTTLE_KG_PROVIDERS_DIR` (`os.pathsep`-separated)

Default provider: `settings.json → knowledge_graph.provider`, else
`CUTTLE_KG_PROVIDER`, else `graphify`. List with
`python -m api.knowledge_graph providers`.

## Identity, isolation, worktrees

- **`<project-key>`** = `api.worktrees.identity.repo_dir_id(name, project_id)` —
  stable across branches; two same-named repos get different keys.
- **`<checkout-key>`** = `primary` or the worktree `checkout_uid` (the id
  `chat_sessions.checkout_uid` binds), matching the **longest** path prefix.
- Store = `<home>/<store_root>/<project-key>/<checkout-key>/`. Reads always pass
  an explicit artifact path, so one graph can never feed another and a worktree
  never silently reuses another branch's graph.

## CLI

```bash
PYTHONPATH=src .venv/bin/python -m api.knowledge_graph <verb> [--provider ID] [--path P|--project NAME|ID|--checkout UID]
```

| Verb | Does |
|---|---|
| `providers` / `status` / `list` | registered engines / inventory (indexed?, nodes/edges, stale?, worktree indexes) |
| `where` | resolve active project → artifact path (+ `stale`) |
| `index` (`--force`, `--all`) / `update` | build/refresh (incremental, local AST) / re-extract changed |
| `query "…" [--budget N] [--refresh]` | scoped subgraph for a question (default 1500 tokens) |
| `explain "X" [--all]` / `node-path A B` | one node's neighbors / shortest path |
| `export` / `sync [--index]` / `vault` | Obsidian export / all / vault path |

## Graphify provider specifics

- Local only: indexing passes `--code-only` (tree-sitter AST, no API key). Docs/
  PDF/media would need a backend key and are skipped.
- Excludes: `.gitignore` + `DEFAULT_EXCLUDES` (`node_modules`, `dist`, `.venv`,
  `vendor`, `third_party`, Unity `Library/`/`Temp/`, …); add per-machine/per-project
  globs in `<home>/graphify/exclude.txt` or `<home>/graphify/<project-key>/exclude.txt`,
  or `CUTTLE_GRAPHIFY_EXCLUDE`. Nothing is written into a checkout.
- Incremental via graphify's manifest + AST cache; `--force` after refactors that
  delete code. Freshness = recorded HEAD vs current; no daemon/watcher.

## Obsidian vault

`export`/`sync` write into
`<home>/<store_root>/vault/Cuttle Projects/<Project>/` (`CUTTLE_OBSIDIAN_VAULT`
overrides). Layout per project:

| Dir | Source |
|---|---|
| `Architecture/<Project> Graph Report.md` | **generated** — deterministic god nodes, communities, surprising connections, import cycles (`graphify cluster-only`, no LLM) |
| `Graphify/` | **generated** — the provider's Obsidian export (one note per node + community notes + `graph.canvas`) |
| `Decisions/`, `Notes/`, `<Project>.md` | **manual** — never overwritten; regeneration only touches the two generated dirs |

`export`/`sync` regenerate the report + notes; a provider without a `report()`
method still exports its notes. Plain Markdown — agents read it like any files;
open in the GUI with Obsidian (*Open folder as vault*, or the launcher).

## New projects

No registration step: `status`/`sync`/`index --all` read the live `projects`
registry, so any newly registered project with an accessible codebase is
automatically eligible. Home and non-code paths are skipped (`is_indexable`).

## Per-agent registration

Every Cuttle agent receives the **`knowledge-graph`** skill in its context
envelope and runs the same shell verb, so no per-vendor config format is needed.
Clients with their own skill systems may also run `graphify install`; MCP is not
used — Cuttle-owned verbs are `python -m api.*`.

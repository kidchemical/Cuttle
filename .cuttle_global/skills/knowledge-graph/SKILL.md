---
name: knowledge-graph
description: "Query-first codebase navigation. Use for architecture, 'how does X work', symbol/relationship, or 'where is X' questions in the active project — before broad grep/reads. Persistent per-project (and per-worktree) knowledge graphs via python -m api.knowledge_graph (default provider: Graphify); 1–2k-token scoped results, incremental local AST indexing."
---

# Knowledge graphs — query-first code navigation

Each Cuttle project (and each git worktree) has its own persisted knowledge graph
under the Cuttle home. Graphs are built locally with tree-sitter AST (no API key,
no paid calls) and are **not** dumped into context — you query a small scoped
subgraph on demand. Graphify is the bundled default; the verb is provider-neutral.

Use it for **code navigation and architecture** questions. Read source files
directly when you need implementation detail or to verify.

## Resolve the active graph first

`python -m api.knowledge_graph` auto-resolves the active project + worktree from
your working directory (`CUTTLE_AGENT_PROJECT_PATH`), so you usually pass nothing:

```bash
python -m api.knowledge_graph where      # project, checkout, artifact, stale?
python -m api.knowledge_graph status     # inventory across projects + providers
python -m api.knowledge_graph providers  # registered engines (graphify, …)
```

Requires the Cuttle venv and `src` on `PYTHONPATH` (see `agent-ops-cli.md`).

## Query before you grep

```bash
python -m api.knowledge_graph query "how does the agent router fall back to another harness" --budget 1500
python -m api.knowledge_graph explain "submit_agent_turn"        # a symbol + its neighbors
python -m api.knowledge_graph node-path "APIRouter" "ModelField" # how two things connect
```

- Keep the budget around **1000–2000 tokens**; narrow the question instead of raising it.
- Results carry `file:line` and `EXTRACTED`/`INFERRED` tags. Follow `EXTRACTED`
  edges to the real file when you need to confirm behavior.
- Exit `3` (ambiguous name) / `4` (no match) surface as JSON, never a silent empty answer.

## Keep it fresh (only when needed)

```bash
python -m api.knowledge_graph index            # build/refresh (incremental; local AST only)
python -m api.knowledge_graph update           # re-extract changed files
python -m api.knowledge_graph query "…" --refresh   # incremental update, then query
```

Do **not** rebuild continuously. If `where`/`status` reports `stale: true`
(recorded HEAD ≠ current HEAD) or you just changed code, run `update` (or
`query --refresh`) once.

## Obsidian

Graph notes export to the shared vault as plain Markdown
(`python -m api.knowledge_graph vault`). Manually maintained `Architecture/`,
`Decisions/`, `Notes/` notes are never overwritten. Regenerate with `export`/`sync`.

## When not to use it

Skip it for trivial edits, when the graph is missing (index would be slow), or in
a non-code project. Fall back to normal exploration freely — never block real work
on a missing/stale graph.

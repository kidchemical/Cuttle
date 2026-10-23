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
| `notes` / `note "api › …"` | list / retrieve **enriched** subsystem explanations (agent-facing; see below) |
| `export` / `sync [--index]` / `vault` | regenerate one project's vault / all projects / vaults parent folder |
| `enrich --subsystem S [--via cli:claude] [--model M] [--markdown-file F] [--dry-run] [--force]` | **opt-in, prototype** — AI "How it works" section for one subsystem note (see below) |

## Graphify provider specifics

- Local only: indexing passes `--code-only` (tree-sitter AST, no API key). Docs/
  PDF/media would need a backend key and are skipped.
- Excludes: `.gitignore` + `DEFAULT_EXCLUDES` (`node_modules`, `dist`, `.venv`,
  `vendor`, `third_party`, Unity `Library/`/`Temp/`, tests (`tests/`, `test_*.py`,
  `*.test.*`, …), and the scratch dirs `temp/`/`tmp/`); add globs in
  `<home>/graphify/exclude.txt` (machine), `<home>/graphify/<project-key>/exclude.txt`
  (project), `<project-key>/<checkout-key>/exclude.txt` (one checkout), or
  `CUTTLE_GRAPHIFY_EXCLUDE`. Nothing is written into a checkout; graphify itself
  never ingests `graphify-out/` or `.obsidian/`, and a `CUTTLE_HOME` inside a project
  is excluded.
- Community names: after each extract the adapter writes graphify's own
  `.graphify_labels.json` (each community named after the subsystem most of its
  members live in, e.g. `api › agent_router · RoutingDecision`) and runs
  `cluster-only` to apply it. With a labels file present graphify never calls an
  LLM; never run `graphify label` against a Cuttle store unless you want paid naming.
- Incremental via graphify's manifest + AST cache; `--force` after refactors that
  delete code. Freshness = recorded HEAD vs current; no daemon/watcher.

## Obsidian vault

Each project is its **own vault**: `export`/`sync` write a small curated wiki into
`<home>/<store_root>/vault/Cuttle Projects/<Project>/` (`CUTTLE_OBSIDIAN_VAULT`
overrides the parent folder). Open that project folder in Obsidian (*Open folder as
vault*). It is built from the graph (`subsystems.py` → `wiki.py`), not graphify's
per-node `export obsidian`:

| Path | Source |
|---|---|
| `Architecture/Overview.md` | **generated** — subsystems by area, core abstractions, dependency diagram (Mermaid), links to the repo's design docs |
| `Subsystems/<area › package>.md` | **generated** — one note per subsystem (directory; tiny dirs roll up, crowded flat dirs split by filename prefix): package docstring, depends-on / used-by links with reference counts, key symbols with `file:line`, modules with their docstrings, repo docs that mention it |
| `Decisions/`, `Notes/`, `<Project>.md` | **manual** — never overwritten |

Note layout: summary (the package's full docstring, first paragraph) → optional
**How it works** → modules (first sentence of each docstring) → depends on / used by →
key symbols → docs. No `# title` line — Obsidian shows the file name.

### AI "How it works" sections (opt-in prototype)

`enrich` is the only thing that calls a model; `index`/`export`/`sync` never do.
It runs one subsystem at a time (prototype: Agent Router and the knowledge-graph
package itself; still opt-in, not every subsystem or project):

```bash
python -m api.knowledge_graph enrich --subsystem "api › agent_router" --dry-run --show-prompt
python -m api.knowledge_graph enrich --subsystem "api › agent_router" --via cli:claude
python -m api.knowledge_graph enrich --subsystem "api › agent_router" --markdown-file path.md
```

- **Inputs, docs first:** a doc named after the subsystem (`docs/guides/AGENT_ROUTER.md`)
  in full; elsewhere a section only when its heading names the subsystem
  (`AGENTS.md` → *Agent Router*), otherwise just the lines that name it or its
  package path — passing mentions in generic README sections are not sent. Then
  module docstrings, a symbol outline (every symbol with its line), symbol-level graph
  edges, and numbered source of the entry points other subsystems call. Docs are
  primary; code is for verification and gaps.
- **Executor:** `--via kind:target` (`cli:claude`, `cli:codex`, `api:openai`, …) or the
  *Knowledge wiki explanations* background-helper chain in Settings — never the
  generic default chain. Calls go through `api.background_helpers.generate`.
  Opus is not required; pass any model the chosen CLI accepts (`--model haiku`,
  a Mini API id, …) and compare `missing_facts` / citation support. There is no
  scheduler: generation stays an explicit `enrich` run.
- **One write path:** model output and `--markdown-file` both go through `save`,
  which always re-assesses citations, required facts, and the current
  fingerprint. There is no side door that can skip those checks.
- **Citations, checked once after generation:** for each `[path:L123]` /
  `[doc.md#Heading]` Cuttle records whether the model was shown that line or section
  (*grounded*) and whether the cited code/section shares the claim's identifiers or
  key terms (*supported*). Ungrounded or unsupported citations render with `?`. The
  check is lexical — it catches citations pointing at unrelated code, not subtle
  misreadings — and errs toward `?`.
- **Citations, resolved on every export:** each code citation keeps an anchor (line
  text, enclosing symbol, offset). Links follow code that moved; a citation whose
  line text is gone renders as `` `path:L123` ⚠ `` and is counted in the callout, even
  while the prose may still be right.
- **Documentation vs code:** a separate `## Documentation vs code` section ("For
  review") lists neutral differences between docs and code. Some may be intentional;
  nothing changes docs or code automatically.
- **Separately managed:** cached at `<store>/explanations/<subsystem>.json` (raw model
  text + citation records) and rendered between `<!-- cuttle-kg:explanation begin/end -->`.
  The rest of the note stays deterministic.
- **Incremental:** the fingerprint covers the subsystem's file hashes, the doc
  excerpts used, its symbol-level cross-subsystem edges, required-fact ids and the
  prompt version. Unchanged → `enrich` skips the call (`--force` overrides). Changed →
  `export` keeps showing the cached text under an *Out of date* banner until `enrich`
  is re-run.
- **Required facts:** a small per-subsystem checklist (Agent Router: cancellation is
  terminal; the `/api/chat` starred-agent entry path). Regenerations that omit a fact
  are flagged; nothing auto-edits docs or code. Keep this list tiny — it is a
  safeguard, not a specification of the subsystem.
- **Regeneration history:** each cache file keeps a short `history` of prior
  generations (missing-fact ids + citation counts) and a `corrections` delta
  (`fixed` / `still_missing` / `new`). `note` returns both so omissions can be
  tracked over time without a separate store.
- **Agent retrieval:** `notes` lists cached explanations with `current` / `stale` /
  `orphaned`, a `summary` count, and a `stale` title list (stale first). `note
  "api › agent_router"` returns the explanation, discrepancies, and source
  references — no Obsidian callouts, wikilinks, `file://` URIs, or symbol dumps.
  `status`/`warning` call out a stale fingerprint, citations that no longer
  resolve, and missing required facts; never treat a note as current when those fire.
  Source code and documentation remain authoritative. For architecture questions,
  `note` is the preferred first retrieval when `status` is `current`; then
  `query` / source for symbols or anything the note omitted.

```bash
python -m api.knowledge_graph notes
python -m api.knowledge_graph note "api › agent_router"
```

Generated notes start with `generated: cuttle-knowledge-graph` in frontmatter;
regeneration only rewrites or deletes files carrying that marker (so a hand-written
note, even inside `Subsystems/`, survives) and skips unchanged files. Repo docs are
linked (`file://`), never copied. Symbol-level detail stays in the graph for agents
(`query`/`explain`). The first export moves an older per-node `Graphify/` folder and
copied `* Graph Report.md` to `<home>/archive/knowledge-graph-legacy/`.

## New projects

No registration step: `status`/`sync`/`index --all` read the live `projects`
registry, so any newly registered project with an accessible codebase is
automatically eligible. Home and non-code paths are skipped (`is_indexable`).

## Per-agent registration

Every Cuttle agent receives the **`knowledge-graph`** skill in its context
envelope and runs the same shell verb, so no per-vendor config format is needed.
Clients with their own skill systems may also run `graphify install`; MCP is not
used — Cuttle-owned verbs are `python -m api.*`.

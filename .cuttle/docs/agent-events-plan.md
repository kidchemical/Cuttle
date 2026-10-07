# Agent Events, Agent Feed & Databases — design

One event store records everything a harness turn does, down to the exact lines
it changed. Three surfaces read it:

1. **Query log inspector** — one turn, full fidelity (replaces the capped JSON sidecars).
2. **Agent Feed** (new app) — every turn across the fleet, live, filterable.
3. **Commit attribution** (today's edit journal) — agent/model trailers on commits.

A fourth piece, **Databases** in Settings → 💾 Data, makes retention, quotas,
sizes and resets for every Cuttle store visible and editable.

Status: **design, not started** (2026-10-06). Nothing here is implemented yet.

Decisions taken (owner calls, 2026-10-06):

| Question | Decision |
|---|---|
| Design doc before code? | **Yes** — this file. |
| Full-detail retention | **90 days** by default, changeable from Settings. |
| Where do quotas/sizes/resets live? | A **Databases** section in Settings → 💾 Data (§8 explains why not a separate app). |
| Journal: fold in or keep separate? | **Proposed: fold in** (§5). The owner asked for clarification; confirm before Phase 3. |

Architecture rules held: owned slices under `src/api/`, no reverse imports of
`web_chat_api`, settings via `settings_routes.py` + `settings_manager`, agent
verbs as `python -m api.*`, client logic as pure `Cuttle*` namespaces, new UI
behind an experimental flag (`.cuttle/docs/experimental-features.md`).

---

## 0. Findings that shaped the design

| Question | Answer | Consequence |
|---|---|---|
| What does the journal record? | Path + content digest before/after, one comparison per **turn** (`edit_attribution/journal.py`, snapshots at `agent_harness/kernel.py:843` / `:896`). No line content. | Line-level history needs actual content captured, not digests. |
| What does the query log keep? | JSON sidecars in `src/web/logs/`, `MAX_EVENTS = 400` per turn (oldest dropped), tool args cut to a 4k preview, results 24k, live view only the last 120 events (`query_events.py`, `query_tracker.py`). 163 MB across 4,354 turns (each turn is written twice: timestamped + stable copy). | Caps and duplicate files go. Large payloads move to compressed blobs instead of being truncated. |
| Do tool events carry arguments today? | **Claude:** 22/22 tools have args + results. **Cursor:** edits carry `streamContent`, which is the new text only, no before-text. **Codex, Muse:** **0** tools have args. They reach the log only as status-bar strings via `QueryStatusTee` → `ingest_status_message`. | Codex and Muse need structured capture. That's the largest gap. |
| Do the CLIs expose real diffs? | **Codex** app-server (verified against `codex app-server generate-json-schema`, v0.159.3): `FileUpdateChange {path, kind, diff}` on file-change items and patch updates, plus `TurnDiffUpdatedNotification` for the turn's running diff. Today `codex_app_server_turn.py` turns these into a status line. **Claude Code** (verified in session JSONL): edit results carry `toolUseResult {filePath, structuredPatch, originalFile, userModified}`. `claude_stream.py` ignores them. **OpenCode** (verified in `opencode.db`): tool part `state.metadata.filediff {file, patch, additions, deletions}` + `metadata.diff`. **Cursor, Muse, Hermes, DeepSeek, Antigravity:** unverified, probe in Phase 0. | Native per-step diffs are available for three CLIs. The universal snapshot layer (§2A) covers the rest. |
| Is the journal accurate? | **Path bug:** `_run_git` strips its whole output (`supervised/evidence.py:102`), which eats the leading space of the first `" M path"` porcelain line, and `ln[3:]` then drops the path's first char. Rows like `lectron/main.js` and `EADME.md` exist. Quoted paths (spaces, non-ASCII) are C-escaped and also unparsed. | Fix in Phase 0 with `git status --porcelain -z`. It also affects the supervised-coordinator evidence. |
| Do journal rows get matched to commits? | 32,560 rows, **26,209 never matched to a commit**, 20,224 older than 7 days. Settlement runs only when committing through Cuttle's Git UI (`git_pending_changes.py:1642-1700`). Commits from a terminal or by an agent never settle. | Stale rows can credit an agent on a later, unrelated commit of the same path. Settlement must work by content, not by commit path (§5). |
| Who reads the journal? | `git_pending_changes.py` (`build_commit_attribution`, `settle_events`) and `git_autocommit.py` (`open_paths_for_query`). | Keep `api.edit_attribution` as the public interface; swap its storage. |
| Is there any retention today? | None for query logs, journal or `router_outcomes`. Brain has `cuttle_brain prune`, and auth sessions expire. | New registry + daily maintenance job (§8). |
| Volume? | Turns/day from sidecars: typically 100–800, peaks of ~1,800 (2026-10-01). | Full fidelity at ~100 KB/turn uncompressed is about 3.6 GB per 90 days at 400 turns/day, roughly 1 GB gzipped. Measure in Phase 1 before fixing the default quota. |

---

## 1. Goals / non-goals

**Goals**
- Exact line-level record of what each turn changed, for **every** harness.
- Per-step attribution (which tool call made which hunk) where the CLI reports it.
- No silent truncation: big payloads go to blobs and are loaded on demand.
- One store powers the inspector, the Agent Feed and commit trailers.
- Retention and quotas the owner can see and change.

**Non-goals**
- Recording raw stream deltas forever. Streaming text is stored as item
  snapshots that update in place, not as individual chunks.
- Cross-machine aggregation (mesh workers keep their own stores; revisit later).
- Replacing git. Snapshots live in git's object store so normal git tooling can read them.
- Undo/rollback UI. The data makes it possible later; not in scope now.

---

## 2. Capturing changes — two layers

### A. Turn snapshots (universal ground truth)

At turn start and turn end (the existing journal points in `kernel.py`), the
repo state is written into git's own object database. Nothing is added to
the user's index, HEAD or branches:

1. `git status --porcelain=v1 -z --untracked-files=all` lists dirty paths (respects `.gitignore`).
2. Each dirty path under the size cap (default 2 MB; larger files and binaries are
   recorded as `skipped:size` / `binary` with digest only) → `git hash-object -w`.
3. Temporary index (`GIT_INDEX_FILE=<tmp>`): `read-tree HEAD`, then
   `update-index --add --cacheinfo` for each hashed path / `--force-remove` for deletions.
4. `write-tree` → tree SHA. `commit-tree <tree> -p HEAD` → snapshot commit.
5. Pinned by `refs/cuttle/turns/<query_id>/{start,end}` so `git gc` keeps it until retention removes the ref.

The turn's exact diff is `git diff <start> <end>`, which works in any git tool.
Per-file hunks and `+/-` counts go into the `edits` table (§3).

Properties:
- Catches **every** write: editor tools, `sed`, `python - <<EOF`, codegen, formatters.
- Hashing only dirty paths keeps it cheap; unchanged files are never read.
- Deduped by git. A file that didn't change between turns costs nothing.
- `refs/cuttle/*` aren't pushed by a normal `git push`. A `--mirror` push would include
  them; Phase 1 must confirm the `git.push` action never uses `--mirror` / `--all` refspecs.
- Non-git workspaces fall back to a digest-only snapshot (today's behavior) and
  are marked `source=digest`.

**Step snapshots** (opt-in per harness): for CLIs with no native diff, also
snapshot after each edit-class tool completes (`edit_file`, `write`, `apply_patch`, …).
That gives per-step attribution at the cost of one dirty-path hash pass per edit.
It's enabled only where §2B has nothing to offer.

**Concurrency:** if two runs overlap in the same repo, one run's snapshot
diff contains the other's edits. The kernel already knows active runs per
repo (`active_executions`). Overlapping windows record `overlap: [query_ids]`, and
paths are attributed only from native events or non-overlapping changes.
Anything else is marked **ambiguous**, never guessed, following the journal's
current "never invent contributors" rule.

### B. Native per-step diffs (when the CLI reports them)

| Harness | Source | Status |
|---|---|---|
| Codex | `FileUpdateChange.diff` on file-change items and patch updates; `TurnDiffUpdatedNotification` | verified schema |
| Claude Code | `toolUseResult.structuredPatch` (+ `originalFile`) on Edit/Write/MultiEdit results | verified |
| OpenCode | tool part `state.metadata.filediff.patch` / `metadata.diff` | verified |
| Cursor | `editToolCall` result (fields unknown; args carry `streamContent` only) | **probe** |
| Muse | `muse serve` item stream | **probe** |
| Hermes, DeepSeek, Antigravity | adapter streams | **probe** |

Each adapter maps its vendor shape to one call on `ToolActivityLog` (new
`record_edit(tool_id, path, patch, kind, source="native")`). The kernel never
parses vendor JSON; adapters own it, as they do today.

Native diffs are labelled `source=native`, snapshot diffs `source=snapshot`. When
both exist, the inspector shows the native per-step hunks and checks their sum
against the snapshot. A mismatch (e.g. a shell `sed` the CLI didn't report)
shows as "unreported changes" for that turn, which is useful on its own.

### C. Structured tool capture for Codex and Muse

Separate from diffs, Codex and Muse tool calls must go through
`ToolActivityLog.record(...)` with real args and results instead of status
strings. That alone fixes "0 of 17 tools have args" for Codex. The status
strip stays unchanged (it already reads from the emitter).

---

## 3. The event store

New owned slice **`src/api/agent_events/`**:

| Module | Role |
|---|---|
| `catalog.py` | Event kind vocabulary + payload schema per kind |
| `store.py` | SQLite (WAL) + blob store; imperative, idempotent DDL |
| `writer.py` | Single background writer thread, batched commits |
| `snapshots.py` | §2A git snapshot/diff helpers (owns the porcelain `-z` parser) |
| `query.py` | Filtered reads, cursor pagination, full-text search |
| `routes.py` | `/api/agent-events*` blueprint (self-registering, like `chat_tts`) |
| `cli.py` / `__main__.py` | `python -m api.agent_events tail\|get\|search\|diff\|stats` |

Location: `runtime_state_path("agent_events")` → `agent_events.sqlite3` + `blobs/`.

### Schema (sketch)

```sql
runs(
  query_id TEXT PRIMARY KEY, chat_session_id TEXT, parent_query_id TEXT,
  agent_id TEXT, model TEXT, effort TEXT, project_root TEXT, repo_root TEXT,
  started_at REAL, finished_at REAL, status TEXT,          -- running|ok|failed|cancelled
  resumed INTEGER, snap_start TEXT, snap_end TEXT,          -- git snapshot commit SHAs
  tokens INTEGER, cost REAL, overlap TEXT                   -- JSON list of query_ids
);
events(
  id INTEGER PRIMARY KEY, query_id TEXT, seq INTEGER, ts REAL, ts_end REAL,
  kind TEXT, block_id TEXT, rev INTEGER,                    -- rev bumps on in-place update
  agent_id TEXT, model TEXT, chat_session_id TEXT, project_root TEXT,  -- denormalized for feed filters
  summary TEXT, text TEXT,                                  -- inline up to 8 KB
  payload_ref TEXT,                                         -- blob hash for larger args/results/text
  failed INTEGER DEFAULT 0,
  UNIQUE(query_id, block_id)
);
edits(
  id INTEGER PRIMARY KEY, event_id INTEGER, query_id TEXT,
  repo_root TEXT, rel_path TEXT, change TEXT,               -- add|modify|delete|rename
  old_path TEXT, source TEXT,                               -- native|snapshot|digest
  patch_ref TEXT, additions INTEGER, deletions INTEGER,
  digest_before TEXT, digest_after TEXT,
  ambiguous INTEGER DEFAULT 0,
  commit_sha TEXT, settled_at REAL                          -- the journal's job (§5)
);
events_fts USING fts5(summary, text, content='events', content_rowid='id');
```

Indexes: `events(ts)`, `events(agent_id, ts)`, `events(chat_session_id, ts)`,
`events(kind, ts)`, `edits(repo_root, rel_path, commit_sha)`.

**Blobs:** content-addressed gzip files (`blobs/ab/cdef….gz`). Payloads over 8 KB
(tool args/results, full thinking, patches) are stored there, so rows stay small
and the feed stays fast.

**Kinds:** `run.start`, `run.resume`, `run.finish`, `run.cancel`, `sent`, `brain`,
`thinking`, `writing`, `tool`, `edit`, `steer`, `question`, `status`,
`router.route`, `router.fallback`, `subagent.spawn`. Heartbeats are never stored.

**Write path:** `QueryTracker.add_event` and the `record_agent_*` helpers in
`query_events.py` keep their signatures and also enqueue into
`agent_events.writer`. Streaming items (thinking/writing/tool) are upserted
by `(query_id, block_id)` with `rev += 1`, debounced to at most one write per item
per 500 ms. The writer batches commits (250 ms or 200 rows). Harness threads
never block on SQLite.

**Live fan-out:** the writer publishes each committed row to an in-process
pub/sub. The inspector and the Feed subscribe over SSE (route transport only).
The in-memory tracker snapshot stays the source for the run that's in progress
until its rows are committed.

**JSON sidecars:** Phase 3 stops writing the duplicate timestamped file and keeps
the stable `query_data_<id>.json` read-only for old links. The inspector reads
the store first and falls back to the sidecar for pre-store turns.

---

## 4. Query log inspector

`src/web/js/queries/query_log_inspector.js` reads `GET /api/agent-events/runs/<qid>`.

- **No cap:** a virtualized timeline; paged by `seq` for very long runs.
- **Full payloads:** args/results/thinking fetched lazily from blobs when a step is expanded.
- **Edit steps** render a unified diff (side-by-side toggle), with `native` / `snapshot` badges.
- **Changes tab:** the turn's aggregate snapshot diff, per-file `+/-`, "unreported
  changes" when native and snapshot disagree, and an "ambiguous" marker for overlaps.
- **Deep links:** `/query_log.html?id=<qid>&seq=<n>` opens and scrolls to a step.
  The Feed and commit trailers use these links.
- **Search inside the run** (FTS scoped to `query_id`).

---

## 5. The journal — "fold in" explained

The journal does one thing the query log can't: **at commit time, say which
agent/model changed each committed file**, and mark those rows as used so they
aren't credited twice. That's what writes the agent trailers on commits.

- **Keep separate** = a second SQLite file with its own copy of edit data, written
  by its own code. Two records of the same edit can disagree, which is what the
  path bug and the 26k unsettled rows show today.
- **Fold in** (proposed) = the journal's database goes away. Its job keeps working
  but reads the `edits` table above. `commit_sha` / `settled_at` are just columns there.
  `api.edit_attribution` stays as the public interface, so
  `git_pending_changes.py` and `git_autocommit.py` don't change.

The **behavior you rely on stays**, and only the separate storage goes.
Settlement also gets fixed:

- **Settle by content, not by commit path.** On each turn snapshot and in the
  daily maintenance job, open edits whose `digest_after` matches the blob at a
  commit reachable from HEAD are settled to that commit, wherever the commit was
  made (Git UI, terminal, or an agent).
- **Supersede:** an open edit whose path has since been overwritten by a later
  attributed edit, or reverted to its `digest_before`, is closed as
  `superseded` / `reverted` rather than staying open forever.
- **Commit trailers** only credit edits whose `digest_after` matches the staged blob.
  Exact content proof replaces "any open row on this path".

**Migration:** existing journal rows are imported as `edits` with
`source=digest` (no patch). Rows whose path can't be resolved (the first-char
bug) are dropped. Rows older than 7 days that never settled are imported as
`stale` and never credited.

---

## 6. Agent Feed (new app)

Apps → **Agent Feed** (`src/web/agent_feed.html`, client `CuttleAgentFeed.*` pure
logic + a thin page module), behind experimental flag **`agent_feed`** (off by default).

**Data:** `GET /api/agent-events?filters&cursor=` (history, newest first) +
`GET /api/agent-events/stream?filters` (SSE, live tail). Same store, same rows.

**Rows** are steps, not chunks. A thinking/writing/tool row grows in place
(`rev`) while it streams, the way a terminal redraws the current line.
Consecutive rows from one run are grouped under a run header (agent, model,
chat `CH-` handle, project), and interleaved runs keep their own colors.

**Filters** (chips + URL state so views are linkable):
- kind: tools · edits · thinking · writing · start/resume · finish · failures · router
- agent · model · chat(s) · project · status
- text search (FTS5 over summaries + text)
- "only runs with edits", "only failures"

**Diving in:** expand a row → full payload/diff inline. Click the run header → the
inspector at that `seq`. Click the `CH-` handle → the chat. An edit row → its diff, plus a
"view in Changes" link.

**Behavior:** follow mode (auto-scroll) pauses when you scroll up and shows
"N new" to resume. The list is virtualized with DOM rows capped, so it stays smooth during
a 1,800-turn day.

**Agent CLI:** `python -m api.agent_events tail --agent codex --kind tool,edit --json`
lets agents watch the fleet too (e.g. a supervisor sub-agent).

---

## 7. Retention & compaction

Default **90 days full detail**, set from Settings (§8) or
`python -m api.storage set agent_events --retention-days N`.

| Age | What's kept |
|---|---|
| ≤ retention | Everything: events, blobs, snapshot refs, patches. |
| > retention | **Compact:** runs + event skeleton (kind, summary, ts, agent, model, failed) + `edits` stats (path, `+/-`, digests, commit). Payload/patch blobs deleted; `refs/cuttle/turns/<qid>` removed so `git gc` can reclaim. |
| > retention × 4 (optional, off by default) | Delete rows entirely. |

Proposed exceptions (confirm): runs in **starred chats** stay at full detail,
and settled edits keep their patch as long as the commit exists (it's cheap
and is the "who changed this line" answer).

The order is retention first, then quota. If the store is still over its quota
after retention, the oldest runs are compacted first, never in-progress runs.

---

## 8. Databases surface (Settings → 💾 Data)

**Why Settings, not an app:** these are infrequent configuration tasks
(set a quota, look at sizes, reset). Settings → 💾 Data already exists and owns
data management. A new app would mean another rail icon for something
opened once a month. If an ops-style view is wanted later (growth graphs,
query console), it can become an app reading the same registry.

**Registry:** new slice `src/api/storage/` with one `StoreSpec` row per store,
following the `FlagSpec` pattern. Each owning slice registers its own row, and the
registry never knows store internals:

```python
StoreSpec(
  id="agent_events", label="Agent events", owner="api.agent_events",
  paths=lambda: [...],                 # db + wal + blobs dir (size = sum)
  stats=callable,                      # rows, oldest, newest
  retention=RetentionSpec(default_days=90, min_days=7),
  quota=QuotaSpec(default_mb=2048),
  prune=callable, vacuum=callable,
  reset=ResetPolicy.TYPED_CONFIRM,     # ALLOWED | TYPED_CONFIRM | NEVER
)
```

Initial rows:

| Store | Size today | Retention | Reset |
|---|---|---|---|
| Agent events (new) | — | 90 d | typed confirm |
| Query log sidecars (legacy, read-only after Phase 3) | 163 MB | 90 d | typed confirm |
| Chats & accounts (`cuttle_auth.db`) | 20 MB | none (use `/cleanup-sessions`) | **never** (vacuum only) |
| Edit journal (legacy; removed after Phase 3) | 10 MB | — | typed confirm |
| Router outcomes | 0.9 MB | configurable | typed confirm (warn: feeds achievements + routing) |
| Device workers | 0.7 MB | — | never |
| Brain metrics / inject snapshots | 1.5 MB | `cuttle_brain prune` | allowed |
| Shared output / uploads (`src/output`) | 67 MB | existing 7 d TTL for `/output/shared` | allowed |

**UI:** a "Databases" group in the Data panel (scope badge "This server"), with one row
per store: label, owner, size (bar against quota), rows, oldest record,
retention (days), quota (MB), and actions **Prune now · Vacuum · Reset**.
Reset requires typing the store name. `NEVER` stores show no Reset button.

**Settings:** key `storage: {<store_id>: {retention_days, quota_mb}}`, routed
through `settings_routes.py` (`SETTING_FAMILIES`), never `web_chat_api.py`.
Unknown ids are dropped (the registry is the allowlist, as with flags).

**Maintenance job:** a daily background pass started with Flask (plus a pass 5 min
after startup): retention, then quota, then content-based settlement (§5), then
`VACUUM` for stores whose free pages are over 25%. It reports to the Tasks/notify
surface only when it fails.

**Agent CLI:** `python -m api.storage list|stats|set|prune|vacuum|reset --json`.
`reset` requires `--confirm <id>` and agents should emit a confirm form first.

---

## 9. Rollout & phases

| Phase | Scope | Flag |
|---|---|---|
| **0 — Fixes & probes** | Porcelain `-z` parser (fixes first-char bug + quoted paths) in `supervised/evidence.py`; capture one real edit turn from Cursor, Muse, Hermes, DeepSeek, Antigravity and fill in §2B; measure bytes/turn. | none |
| **1 — Store + snapshots** | `api.agent_events` store/writer/snapshots; tracker writes go to both JSON sidecars and the store; turn snapshots + `edits` rows; `python -m api.agent_events`. | none (invisible) |
| **2 — Native capture** | Codex + Claude + OpenCode `record_edit`; Codex/Muse structured tools (§2C); step snapshots for CLIs with no native diff. | none |
| **3 — Inspector + journal fold-in** | Inspector on the store (no caps, diffs, Changes tab); `api.edit_attribution` reads `edits`; content-based settlement; journal import; stop timestamped sidecars. | `agent_events_inspector` (on in dev first) |
| **4 — Databases** | `api.storage` registry, Settings → Data "Databases" group, maintenance job, retention/compaction. | none (it's settings) |
| **5 — Agent Feed** | App, SSE tail, filters, FTS, deep links. | `agent_feed` |

Phase 4 can come before Phase 3 if disk growth from Phase 1 becomes a concern.

---

## 10. Risks

- **Hot-path latency:** all writes go through the background writer; a full queue
  drops `status` events first, never `edit`/`run.*`. A test asserts the harness
  thread never touches SQLite.
- **Snapshot cost on huge repos:** only dirty paths are hashed; the size cap and
  binary skip bound the worst case. Snapshot time is logged per turn, and
  snapshots turn off automatically above a threshold (marked `source=digest`).
- **Secrets in payloads:** tool output can contain `.env` contents. The store is
  local-only and stays in the same trust class as chat transcripts. It's never
  posted to Discord or pushed. Optional redaction of known key patterns at write time.
- **Windows:** `GIT_INDEX_FILE` and `hash-object -w` behave the same; path
  normalization uses the existing helpers.
- **`git gc` vs refs:** retention removes refs before compaction reports success, so
  no snapshot object is left with no ref holding it.
- **Two agents in one repo:** handled by `overlap`/`ambiguous` (§2A), never by guessing.

---

## 11. Tests (by phase)

- Porcelain `-z` parser: leading-space first line, renames, quoted/unicode paths.
- Snapshot round-trip: add/modify/delete/rename/untracked/binary/oversize → exact `git diff`.
- Overlapping runs → `ambiguous`, no false credit.
- Native vs snapshot reconciliation → "unreported changes".
- Adapter fixtures: recorded Codex/Claude/OpenCode streams → expected `edits` rows.
- Settlement: terminal commit settles by digest; revert closes `reverted`; stale never credited.
- Writer: back-pressure, `block_id` upsert, `rev` ordering, crash-safe batch.
- Retention/quota: compaction keeps skeleton, removes blobs + refs; starred exemption.
- Storage registry: unknown ids dropped, `NEVER` resets refused (API + CLI).
- Boundary: `agent_events` / `storage` never import `web_chat_api`
  (`test_architecture_boundaries.py`).
- Feed client: pure `CuttleAgentFeed` filter/grouping logic (JS suite).

---

## 12. Open questions

1. Confirm **fold in** for the journal (§5).
2. Keep full **thinking** text, or summaries only past N days?
3. Exempt **starred chats** from compaction?
4. Default **quota** for agent events (proposed 2 GB, revisit after Phase 0 measurement).
5. Should mesh workers' runs (remote turns) report into the Host's feed? Out of scope now; the schema has room (`project_root`, `agent_id`).

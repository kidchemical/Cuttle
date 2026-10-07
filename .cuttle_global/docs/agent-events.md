# Agent events and database settings

New harness activity lives in `api.agent_events`: SQLite headers plus compressed,
content-addressed full payloads under `src/data/agent_events/`. Harness adapters
own vendor shapes; the event owner owns persistence, snapshots and retention.

- Query inspector: paged steps, lazy full payloads, Changes tab and unified / side
  by side patches. Existing query links still work; pre-store turns read their
  legacy JSON files. Old truncation cannot be recovered from those files.
- Apps → Agent Feed: enable **Agent Feed** in Settings → Experimental for live
  SSE. Shows this server's agents; remote-worker aggregation is deferred.
- Settings → Data → Databases: sizes for known stores, plus agent-events policy,
  prune, vacuum and typed-confirm reset. Other stores currently expose sizes;
  their owning subsystems retain their existing maintenance interfaces.

Defaults are editable: 90 days full detail, 14 days full CLI-exposed thinking
then a short excerpt, 5 GB (5,000 decimal MB). Starred-chat days `-1` follows the
ordinary retention policy; positive values set a different window; `0` exempts
starred activity from age expiry. Quota still applies. Summary-only thinking is
also supported. Increasing retention cannot restore deleted payloads.

Maintenance runs five minutes after the first capture, then daily. It reconciles
commits by content, compacts old completed activity, removes unreferenced blobs
and private Git refs, and reclaims SQLite pages. Active runs are protected. Under
quota pressure older completed detail may expire early, with a visible reason.
Commit attribution metadata survives compaction. Reset removes pending attribution
as well as events, requires typing `agent_events`, and refuses active runs.

Git snapshots use a temporary index inside the project `temp/` and private
`refs/cuttle/turns/` tree refs. Text up to 2 MB gets exact net-turn patches; skipped
binary/oversize files are labelled. Overlapping turns are ambiguous and cannot
receive inferred commit credit. Native patches provide edit-step history;
edit-class completion snapshots supplement vendors without native patches.
Non-Git workspaces currently have tool activity but no line snapshot.

Commit attribution uses the same SQLite store through `api.edit_attribution`.
The old journal imports incrementally when attribution is first used after
restart; its file remains as a rollback copy. Unsettled imported rows older than
seven days are stale and cannot receive credit. Terminal commits can be settled
by content later; existing terminal commits cannot gain trailers retroactively.

Run from the Cuttle root with the project venv:

```bash
PYTHONPATH=src .venv/bin/python -m api.agent_events stats --json
PYTHONPATH=src .venv/bin/python -m api.agent_events tail --agent codex --kind tool,edit --json
PYTHONPATH=src .venv/bin/python -m api.agent_events get QUERY_ID --limit 100 --after 0 --json
PYTHONPATH=src .venv/bin/python -m api.agent_events diff QUERY_ID --json
PYTHONPATH=src .venv/bin/python -m api.agent_events search 'example' --json
PYTHONPATH=src .venv/bin/python -m api.storage list --json
PYTHONPATH=src .venv/bin/python -m api.storage set agent_events --policy '{"retention_days":180,"quota_mb":6000}' --json
PYTHONPATH=src .venv/bin/python -m api.storage prune agent_events --json
PYTHONPATH=src .venv/bin/python -m api.storage vacuum agent_events --json
```

`reset agent_events --confirm agent_events` is destructive: agents must use a
confirmation form before invoking it. Library/API validation also rejects wrong
confirmations and resets of protected stores. No CLI verb invokes an agent or
spends model budget. Thinking summaries are labelled excerpts, not model calls.

Tests must set `CUTTLE_AGENT_EVENTS_DIR` to an isolated directory. The shared
pytest fixture does this and drains/closes the writer at teardown. UI checks
mock all APIs; they do not start or restart the hosting Flask process.

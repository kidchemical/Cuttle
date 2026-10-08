# Release audit: legacy code disposition

This is the evidence for audit item 11. Cuttle has no tagged release to support.
There is no older-client compatibility commitment. Saved data and live callers
still have owners; removing an obsolete interface must not discard that data.

## Removed

- Seed-priority rewrites, unused LAN port snapshots, supervised card builders,
  unused helper aliases and shared worker-token authentication (earlier audit commits).
- Automatic renaming of the old default project and implicit machine-alias
  substitution in project registry reads. Installed single-path rows are
  migrated once to an explicit location list; saved locations determine resolution.
- Flat session model/effort response readers. The current messages endpoint
  already supplies `agent_pins`; a blank pin stays blank.
- Tiered use-case routing (`preferred`/`escalation`/`fallbacks`) and the duplicate
  client converter. The installed routing table was checked: every saved row
  uses `targets`. Obsolete writes are rejected without replacing saved routes.
- `api.widgets_cli`, `/api/widgets` routes and the `CuttleChatWidgets` alias.
  Tasks use `api.gizmos.tasks` and `/api/gizmos/tasks`. Existing CLI and HTTP
  regressions now exercise those owners. The auth-owned storage table is reused.
- The obsolete single-panel pending-change DOM converter, terminal
  sessionStorage tab migration and hardcoded sticky-agent fallback list.
  Current pages use the current host element, durable layout and harness catalog.
- Brain context convenience functions used only by tests. Tests now seed
  snapshots and inspect preparation through the interfaces used by the kernel.

## Retained with a concrete reason

| Area / owner | Reason |
| --- | --- |
| `core.runtime_data`, `core.runtime_paths`, settings storage and database schema upgrades | Move existing mutable files out of an old checkout and preserve installed databases, secrets and settings. These are transactional installed-data migrations, not an alternate execution path. |
| Brain `key_store` / `handoff` | Import installed JSON stores once, then read SQLite. Existing handoff rows describe already-delivered context; discarding them would replay or omit context. |
| Project registry location migration | The installed Cuttle project was still a single-path row using a personal alias. Persist the resolved path and original path once as ordered locations. Subsequent reads and restarts use that list; later alias changes cannot redirect it. |
| Chat messages, attachments, usage and badge readers | Saved transcripts contain older metadata and inline attachment notes. Readers preserve history; new turns write the current shape. |
| Composer selection and project history association | Existing sessions may lack the newer preference/project-ID columns. History supplies a read-only initial preference and project association until an explicit current value exists. |
| Query events, edit journal and storage services | Old query artifacts remain user-owned inspectable records. New events use `agent_events`; the old journal importer is already removed. |
| `chat_widgets` markdown decoding | Retains persisted Tasks markup and decodes agent output through the same Tasks owner. It does not retain the obsolete HTTP or CLI mutation interfaces. |
| UI saved layout, wallpaper and terminal handle stores | Preserve user-chosen layout, media and existing terminal handles. They are stored preferences, not an older-client protocol. The obsolete sessionStorage tab reader is removed. |
| Action forms / job-watch metadata | Already-persisted cards and job files must remain viewable and cancellable. Old PID fields are relevant to cleanup of a recorded job; they do not start retired integrations. |
| LAN firewall cleanup | Deletes previously installed broad rules so upgrading cannot leave a Public/Any exception. This is security repair, not acceptance of old trust policy. |
| Auth/OAuth accounts and retired-secret scrubbing | Existing account identifiers and secrets require protection. Scrubbing an obsolete settings key does not authorize it. |
| Android artifact metadata | Uses the actually served APK to repair installed update metadata; preserves the disk alias during replacement while the previous Flask generation may still serve it. |
| Vendor response variants, CLI discovery and current coordinator/status adapters | Support current vendor contracts and current callers. “Legacy” comments here describe provenance; these are not versioned Cuttle compatibility APIs. |

The retained readers have no requirement to keep an old Cuttle binary, route or
shared-auth scheme working. No retired integration is restored by this audit.

## PR composition-root growth review

PR #20 adds 301 net lines to `src/web/js/shell/app_shell.js` relative to
`origin/main` at `39abb15`. The reviewed diff implements pane parking/restoration:
DOM classes and live iframe titles, saved collapsed state, splitter/divider
restore affordances, and pointer gesture sizing around parked panes. It does not
add backend, harness, or independent Spaces domain decisions.

The existing shell owns the pane tree, `columnState`, layout persistence and
splitter effects. `docs/architecture/repository-map.md` explicitly records that
boundary as retained after the E1/E2 review. The new parking behavior stays with
that owner so parking preserves live iframe identity, existing event teardown,
and the saved layout. A separate controller extraction is not part of this
release fix. This is a scoped justification for the accumulated pane changes,
recorded by the commit trailer required by the PR growth guard; the guard and
8,200-line shell ceiling remain enforced for subsequent work.

The real browser layout suite covers parking/reloading, click and drag restore,
vertical/horizontal layouts, divider selection, cancellation and live iframe
preservation.

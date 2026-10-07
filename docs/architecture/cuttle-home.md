# The Cuttle home: where runtime state lives

The install tree is code only. A checkout, `C:\Program Files\Cuttle`, or a
read-only AppImage mount is never written at runtime. Every mutable byte
Cuttle owns lives in one per-user folder, the **Cuttle home**, resolved by
`core.runtime_paths.cuttle_home()`:

| Platform | Default |
|---|---|
| Windows | `%LOCALAPPDATA%\Cuttle` |
| Linux / macOS | `$XDG_DATA_HOME/cuttle`, else `~/.local/share/cuttle` |
| Any | `CUTTLE_HOME` overrides (tests, shadow instances, a second checkout that must not share history) |

Code asks `runtime_paths` for a path (`data_db_dir()`, `runtime_state_path()`,
`settings_path()`, `output_dir()`, `logs_dir()`, `secrets_dir()`,
`personal_dir()`, `env_file()`). Never join a path onto `__file__` to reach
state.

## Layout

```text
<home>/
  .env                 provider keys + env config (daemon loads it before children)
  config/              settings.json (server prefs), machine_settings.json, ui_state.json
  db/                  SQLite stores: cuttle_auth, projects, device_workers,
                       router_outcomes, achievements, gizmos, chat_vfx
  sessions/            vendor resume maps, last-agent handoff
  brain/               context injection snapshots, context metrics
  agent_events/        agent event store + blobs (also the edit journal)
  supervised_tasks/    durable task records and forensic snapshots
  cache/               replaceable pricing / model / benchmark caches
  output/              served at /output/: uploads/, shared/ (7-day TTL), job status, dashboards
  logs/                daemon.log, flask.log, flask_restart_events.jsonl
  logs/queries/        per-query sidecars, served at /logs/
  secrets/             file-shaped secrets: TLS cert/key, GitHub App key, token files,
                       action_hmac_secret, Android keystores
  personal/            install-local overlay of the shared .cuttle_global/ layer
  pairing_store.json   approved LAN identities
```

Per-user IPC (notify queues, Flask restart request/status) stays in
`user_state_dir()` (`~/.local/state/cuttle`, or `%LOCALAPPDATA%\Cuttle` on
Windows). Electron's own profile stays in its `userData` folder.

Sensitive files: `db/cuttle_auth.db` holds password hashes, login-session
tokens and OAuth state; `db/device_workers.db` holds worker-enrollment bearer
tokens; `secrets/` and `.env` hold credentials. Back up the whole home to keep
chat history and signed action cards valid; deleting `cache/` only causes a
refresh.

## Personal overlays

Two overlays exist, and each belongs to its owner:

- **Shared layer**: `<home>/personal/` mirrors tracked `.cuttle_global/`
  (`commands/ rules/ actions/ docs/ scripts/ skills/ agents/`). It is
  per-user because the install tree may be read-only.
- **Project layer**: `{project}/.cuttle/personal/` stays beside the project
  (gitignored). The project, not the install, owns it. This includes the
  Cuttle repo's own `.cuttle/personal/` (learnings, dev-only docs).

Resolution (`api.cuttle_brain.personal_overlay`): rule and doc Markdown twins
append a marked personal delta after the tracked text. Commands and actions
replace the whole unit by declared name, and skills replace by directory id.
Personal-only units are supported. An explicit boolean `disabled: true` hides
lower-priority units with the same identity. Script recipes must point at a
personal script by literal path.

Put machine-specific material in a personal overlay: absolute paths, LAN
hosts, guild examples, and dated private notes. `<home>/personal/path-aliases.json`
holds foreign-path rewrites and sibling-project hints:

```json
{
  "windows_cuttle_prefixes": ["C:/OldCheckout/Cuttle"],
  "path_mappings": {"E:/Projects": ["~/Projects", "/mnt/data/Projects"]},
  "sibling_project_paths": ["E:/Projects/DemoGame"],
  "action_prefer_substrings_no_channel": ["demogame"],
  "action_skip_substrings_unmatched_channel": ["demogame"]
}
```

Secrets never go in an overlay: use `.env` or `secrets/`.

## Moving an older checkout's state

Older checkouts kept state in `src/data/`, `src/settings.json`, `src/output/`,
`src/web/logs/`, `src/.env`, `.cuttle/personal/secrets/`, and
`.cuttle_global/personal/`, and logs in `~/cuttle_logs/`. On cold start, before
any service or log opens a file in the home, the daemon moves all of it there
(`core.runtime_data`). `~/cuttle_logs/` moves only into the default home, never
into an explicit `CUTTLE_HOME`, and old logs never block a boot. The move:

- refuses to run while another Cuttle process uses the checkout;
- never overwrites: a home file in the way (for example one written by a
  harness CLI that already ran the new code) is renamed
  `<name>.pre-migration-<timestamp>`, and so are stray SQLite sidecars of an
  incoming database;
- rolls back every move, including those renames, if any step fails;
- keeps SQLite sidecars with their databases.

If the move cannot run, the daemon refuses to start. Flask also refuses to boot
while unmigrated state remains, so a Flask-only restart can never open empty
stores beside the real ones. To apply the move, exit Cuttle from the tray and
relaunch.

Preview or apply by hand with Cuttle stopped:

```bash
PYTHONPATH=src .venv/bin/python -m core.runtime_data          # preview
PYTHONPATH=src .venv/bin/python -m core.runtime_data --apply
```


# Runtime data

This checkout's **installation-wide** runtime persistence, shared across registered
projects. Project instructions/extensions belong in `{project}/.cuttle/`; file-shaped
secrets belong in `.cuttle/personal/secrets/`. Runtime files are gitignored; only
READMEs and the home automation example ship.

- `db/`: SQLite application stores.
- `config/`: model/runtime preferences (`runtime_config.json`), owned by `core.config.RuntimeConfig`.
  The former `BotConfig` name is a compatibility alias. `machine_settings.json`
  stores worker/SSH/filesystem policy and LAN discovery. `ui_state.json` stores
  rail layout and workspace snapshots. SettingsManager remains the shared interface;
  its storage owner locks updates and atomically replaces JSON. Server preferences
  (router, agents, wallpaper, integrations, flags) stay in `src/settings.json`.
  These files contain configuration/state, not provider credentials.
- `sessions/`: vendor resume mappings, per-chat pins, and last-agent handoff state.
- `brain/`: context injection snapshots for resumed turns.
- `supervised_tasks/`: durable task records, worker reports, and forensic snapshots.
- `edit_attribution/`: observed worktree edit journal (SQLite and its sidecars).
- `cache/`: replaceable pricing, model catalog, and benchmark caches.
- `home_automation/`: device inventory, schedule, auto state, heartbeat, and API lock.
- `archive/`: retired agent memory and orphaned Gemini resume mapping; recovery only.

Paths are owned by `core.runtime_paths`. Session records include chat/project keys;
being stored centrally does not make them guest-project configuration. Deleting
session maps loses native resume bindings; deleting caches only causes a refresh.

## Existing installations

Old `workspace/`, `src/bot_config.json`, and root-level `home_automation_*.json` files remain authoritative
while present. Path resolution does **not** move live files, copy them, or maintain
competing stores. New installations immediately use the owner directories above.

Preview the migration from the checkout root:

```bash
PYTHONPATH=src .venv/bin/python -m core.runtime_data
```

Exit Cuttle through the tray so the daemon, Flask, and guest turns have stopped.
The next daemon launch migrates automatically **before** starting Flask and
scheduler threads, provided no other host is running. To migrate manually before
starting Cuttle again:

```bash
PYTHONPATH=src .venv/bin/python -m core.runtime_data --apply
```

Windows: set `$env:PYTHONPATH = 'src'` and use `.venv\Scripts\python.exe`.
The command refuses a running Cuttle instance and existing destination conflicts.
It moves whole supervised/attribution directories, preserving SQLite sidecars,
never merges files, and leaves unknown legacy files untouched. A Flask-only restart
is insufficient for home automation: its scheduler lives in the daemon.

Adapter code no longer belongs under data. Install-local packs go in
`.cuttle_global/personal/agents/<id>/`; shared packs in `.cuttle_global/agents/<id>/`;
project packs in `{project}/.cuttle/agents/<id>/`. Legacy `harness_agents/` packs remain
discoverable and migrate to the personal instance root without overriding packs.

## Settings schema and credentials

The daemon's guarded cold-start migration splits an existing unversioned
`src/settings.json`, preserves live and unknown keys, removes retired graph/node
settings, and writes `schema_version: 1` last. This integer is a storage schema,
not the application release version. Destination conflicts refuse the split;
reads never migrate files. Existing installations keep the legacy generation
until migration succeeds. New installations use scoped storage on their first
explicit write; reads keep defaults in memory.

The split changes storage only: server UI state still applies installation-wide.
Browser-local preferences stay in localStorage. Do not copy machine configuration
between operating systems without reviewing SSH paths and allowed prefixes.

Provider/environment credentials belong in `src/.env`; file-shaped secrets belong
in `.cuttle/personal/secrets/`. The cold-start migration moves the legacy
`db/action_hmac_secret` there without rotating its bytes and extracts a nonempty
legacy `device_workers.token` to `worker_shared_token.json`. The public settings
aggregate never returns the legacy shared token. Do not copy provider secrets
into settings JSON.

SQLite databases are **sensitive**, even though they are not provider-key stores:
`db/cuttle_auth.db` contains password hashes, login-session tokens and temporary
OAuth state; `db/device_workers.db` contains worker-enrollment bearer tokens.
These remain owned by the auth/worker services, rather than being moved as part
of a settings migration. Vendor CLI authentication is managed by each vendor.

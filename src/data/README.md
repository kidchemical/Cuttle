# Runtime data

This checkout's **installation-wide** runtime persistence, shared across registered
projects. Project instructions/extensions belong in `{project}/.cuttle/`; file-shaped
secrets belong in `.cuttle/personal/secrets/`. Runtime files are gitignored; only
READMEs and the home automation example ship.

- `db/`: SQLite application stores.
- `config/`: model/runtime preferences (`runtime_config.json`), owned by `core.config.RuntimeConfig`.
  The former `BotConfig` name is a compatibility alias; `src/settings.json` remains
  the separate shell/router/LAN settings store.
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

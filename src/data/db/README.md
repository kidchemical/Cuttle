# SQLite (gitignored)

| File | Store |
|---|---|
| `cuttle_auth.db` | Users + web chat |
| `projects.db` | Project chip registry |
| `device_workers.db` | Mesh workers / jobs |
| `router_outcomes.db` | Router telemetry |

Paths are resolved from `core.runtime_paths.data_db_dir()`.

Additional active stores: `achievements.db` (achievement progress/unlocks) and
`chat_vfx.db` (transient chat effect delivery). The edit attribution SQLite journal
is owned separately under `src/data/edit_attribution/`. `action_hmac_secret` is
existing signing state; preserve it when backing up/restoring chat action cards.

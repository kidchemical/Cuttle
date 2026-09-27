# SQLite (gitignored)

| File | Store |
|---|---|
| `cuttle_auth.db` | Users + web chat |
| `projects.db` | Project chip registry |
| `device_workers.db` | Mesh workers / jobs |
| `router_outcomes.db` | Router telemetry |
| `tasks.db` | Legacy local kanban UI |

Paths are resolved from `core.runtime_paths.data_db_dir()`.

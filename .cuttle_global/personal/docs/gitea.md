# Gitea — install-local delta

Tracked runbook is canonical. This file adds only what is specific to this install.

## Server + auth (do not commit)

- LAN Gitea: `http://192.168.4.38:3000`, agent user `cuttle`
- Credentials in `/home/kidchemical/Desktop/Cuttle/src/.env`: `GITEA_BASE_URL`, `GITEA_TOKEN`
  (scoped token preferred over password). `GITEA_AGENT_USERNAME` overrides the assignee default.
- Restart Flask after changing `.env` (some paths read env at process start).

## Polling / auto-triage (Gitea → kcsserver → Cuttle)

Remote `@cuttle` commands go through the **Cuttle Jobs** stack, not the
DigitalOcean bug-report relay:

1. Gitea `issue_comment` / `issue_assign` webhook → `cuttle-jobs` on kcsserver
2. Durable SQLite queue + claim/lease API; Cuttle daemon polls / claims / executes

On the Cuttle PC (`src/.env`):

```env
CUTTLE_JOBS_ENABLED=1
CUTTLE_JOBS_BASE_URL=http://<jobs-host>:8091
CUTTLE_JOBS_WORKER_TOKEN=same_as_server
```

Supported triggers: assign issue to **cuttle** → investigate; `@cuttle investigate|solve …`;
`@cuttle yes` → solve / `@cuttle no` → decline; `@cuttle …` free text → converse.

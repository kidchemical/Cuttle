# Cuttle Workers (hub pointer)

Multi-device compute mesh — **W1 + file_copy live**; Blender CLI farm (W2b) not yet.

Canonical design: `/home/kidchemical/Desktop/Cuttle/docs/guides/CUTTLE_WORKERS.md`

APIs: `GET/POST /api/workers`, `POST /api/workers/enroll` (auto token on Client connect), `/api/workers/jobs`, claim/complete/fail.

Jobs UI: `/jobs_page.html` → **Devices** tab.

**No manual worker token.** Client Electron enrolls automatically when it connects to the host (LAN / same trust as the UI).

Also: `ROADMAP_2026.md` Phase 4 (W0–W4), `.cuttle/learnings/FEATURE_REQUESTS.md` `[FEAT-20260917-001]`.

Do not confuse with:

- Gitea `@cuttle` claim workers → `cuttle-jobs.md`
- Supervised Cursor “worker” (same machine) → `docs/guides/SUPERVISED_COORDINATOR.md`
- Electron Host vs Client (UI) vs **workerMode** sidecar (Client compute)

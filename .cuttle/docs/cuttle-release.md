# Cuttle release versioning (this repo only)

Two version concepts — do not conflate them:

- **Release version** (`electron/package.json`, e.g. `0.2.26`): user-facing SemVer. Bump **only at release time**, never per push. Patch = bugfix, Minor = feature, Major = breaking. Use `.cuttle/scripts/bump-cuttle-version.ps1` (`-Minor` / `-Major` / `-Set X.Y.Z`).
- **Git hash**: exact commit identity. This is what client "Update" badges and mesh worker `cuttle_version` mismatch checks compare — it changes every push with no manual step. Never route staleness checks through the hand-bumped release version.

Release flow: bump version → commit → `git tag vX.Y.Z` → push (emit the `git.push` form) → publish GitHub Release notes off the tag.

## Mesh-visible version bump

Workers advertise `electron/package.json` `version` as `cuttle_version`. Before pushing Client/worker runtime changes, bump it:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .cuttle\scripts\bump-cuttle-version.ps1
# or -Minor / -Major / -Set 0.3.0
```

Bump when any of these change: device_workers executor/store/claim/HITL, client daemon, Electron sidecar/`main.js` worker spawn, self-update script. Jobs → Devices shows host version, flags mismatches (⚠), and **Update** queues `cuttle_self_update`. Clients pull from the **git remote** (`git fetch` + `reset --hard @{u}`), not the Host working tree — unpushed Host commits never appear in Update.

**After bump → commit → push (reload ladder, least disruptive first):**

| Order | When | What |
|---|---|---|
| 1 | Only HTML/JS/CSS / static web UI | Hard-refresh the Cuttle shell (no process kill) |
| 2 | Host titlebar / `CUTTLE_PACKAGE_VERSION` still stale after bump | `electron.host-restart` or `workers.self-update` `target=<host worker_id>` with `no_daemon` (UI only; Flask stays up) |
| 3 | Outdated Client workers | `workers.self-update` `target=<client>` (mesh; do it yourself) |
| 4 | Python that Flask imports changed | Emit the **`flask.restart` action form** (prefer graceful). Do **not** autonomously `/restart when-idle` or force — the user may be mid-chat and needs to see/choose |

Do **not** skip Host Electron when you bumped `electron/package.json` — Clients will show the new version while Host Electron still advertises the old badge until step 2.

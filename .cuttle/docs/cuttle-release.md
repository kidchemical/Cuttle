# Cuttle release versioning (this repo only)

Two version concepts — do not conflate them:

- **Release version** (`electron/package.json`, e.g. `0.2.26`): user-facing SemVer. Bump **only at release time**, never per push. Patch = bugfix, Minor = feature, Major = breaking. Use `.cuttle/scripts/bump-cuttle-version.ps1` (`-Minor` / `-Major` / `-Set X.Y.Z`).
- **Git hash**: exact commit identity, advertised as `cuttle_git_rev`. This is what client "Update" badges and mesh `needs_update` compare first — it changes every push with no manual step. (`cuttle_version` SemVer mismatch is only one of three `needs_update` inputs in `device_workers/platform.py`, alongside git-rev mismatch and stale boot rev.) Never route staleness checks through the hand-bumped release version.

Release flow: bump version → commit → `git tag vX.Y.Z` → push (emit the `git.push` form) → publish GitHub Release notes off the tag.

## Mesh-visible changes (no per-push bump)

Workers advertise `electron/package.json` `version` as `cuttle_version`, but mesh
staleness does NOT wait for a bump: pushing Client/worker runtime changes is enough —
git-rev mismatch flags the Clients behind (`platform.py`, including updatable Clients
with no rev yet). Bump SemVer at release time only:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .cuttle\scripts\bump-cuttle-version.ps1
# or -Minor / -Major / -Set 0.3.0
```

Jobs → Devices shows host version, flags mismatches (⚠), and **Update** queues `cuttle_self_update`. Clients pull from the **git remote** (`git fetch` + `reset --hard @{u}`), not the Host working tree — unpushed Host commits never appear in Update.

**After bump → commit → push (reload ladder, least disruptive first):**

| Order | When | What |
|---|---|---|
| 1 | Only HTML/JS/CSS / static web UI | Hard-refresh the Cuttle shell (no process kill) |
| 2 | Host titlebar / `CUTTLE_PACKAGE_VERSION` still stale after bump | `electron.host-restart` or `workers.self-update` `target=<host worker_id>` with `no_daemon` (UI only; Flask stays up) |
| 3 | Outdated Client workers | `workers.self-update` `target=<client>` (mesh; do it yourself) |
| 4 | Python that Flask imports changed | Emit the **`flask.restart` action form** (prefer graceful). Do **not** autonomously `/restart when-idle` or force — the user may be mid-chat and needs to see/choose |

Do **not** skip Host Electron when you bumped `electron/package.json` — Clients will show the new version while Host Electron still advertises the old badge until step 2.

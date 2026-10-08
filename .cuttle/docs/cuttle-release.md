# Cuttle release versioning (this repo only)

Two version concepts — do not conflate them:

- **Release version** (`electron/package.json`, e.g. `0.2.26`): user-facing SemVer. Bump **only at release time**, never per push. Patch = bugfix, Minor = feature, Major = breaking. Use `.cuttle/scripts/bump-cuttle-version.py` (`--minor` / `--major` / `--set X.Y.Z`; any OS).
- **Git hash**: exact commit identity, advertised as `cuttle_git_rev`. This is what client "Update" badges and mesh `needs_update` compare first — it changes every push with no manual step. (`cuttle_version` SemVer mismatch is only one of three `needs_update` inputs in `device_workers/platform.py`, alongside git-rev mismatch and stale boot rev.) Never route staleness checks through the hand-bumped release version.

Release flow: bump version → commit → `git tag vX.Y.Z` → push the branch (`git.push` form) → push the tag (`git.push` form with `"tag":"vX.Y.Z"`; a branch push does not carry tags) → publish GitHub Release notes off the tag. Never push with `--tags` or `--all`: this checkout's older local tags/branches predate the public history and must stay local.

## Mesh-visible changes (no per-push bump)

Workers advertise `electron/package.json` `version` as `cuttle_version`, but mesh
staleness does NOT wait for a bump: pushing Client/worker runtime changes is enough —
git-rev mismatch flags the Clients behind (`platform.py`, including updatable Clients
with no rev yet). Bump SemVer at release time only:

```bash
.venv/bin/python .cuttle/scripts/bump-cuttle-version.py
# or --minor / --major / --set 0.3.0   (Windows: .venv\Scripts\python.exe)
```

Jobs → Devices shows host version, flags mismatches (⚠), and **Update** queues `cuttle_self_update`. Clients update from the **git remote** (`git fetch --all --prune` + guarded `git merge --ff-only --no-overwrite-ignore` of the configured upstream), not the Host working tree — unpushed Host commits never appear in Update. The updater refuses dirty/untracked files, detached HEAD, missing upstream and local commits/divergence before stopping Client processes; it does not stash or hard-reset local work. Older updater revisions used reset-hard: refresh those scripts before relying on this guard.

**After bump → commit → push (reload ladder, least disruptive first):**

| Order | When | What |
|---|---|---|
| 1 | Only HTML/JS/CSS / static web UI | Hard-refresh the Cuttle shell (no process kill) |
| 2 | Host titlebar / `CUTTLE_PACKAGE_VERSION` still stale after bump | `electron.host-restart` or `workers.self-update` `target=<host worker_id>` with `no_daemon` (UI only; Flask stays up) |
| 3 | Outdated Client workers | `workers.self-update` `target=<client>` (mesh; do it yourself) |
| 4 | Python that Flask imports changed | Emit the **`flask.restart` action form** (prefer graceful). Do **not** autonomously `/restart when-idle` or force — the user may be mid-chat and needs to see/choose |

Do **not** skip Host Electron when you bumped `electron/package.json` — Clients will show the new version while Host Electron still advertises the old badge until step 2.

Client self-update requires a clean tracking branch. It refuses tracked edits,
staged changes, untracked files, detached HEAD, missing upstream, and local or
diverged commits before stopping clients. Ignored personal files are retained;
collisions with incoming tracked files are refused. Resolve/save local work
explicitly and retry; there is no automatic stash, hard reset, or clean.

## Release discovery and notes

Settings → General → **Cuttle updates** shows the server checkout's existing
SemVer and Git revision (click to copy for a bug report), a **Check for updates**
button, last successful check time, and expandable latest published release
notes with a GitHub link. This is a general-use enhancement to the established
update surface, so it ships without an experimental toggle.

`api.releases` owns read-only GitHub release discovery; Settings exposes
`GET /api/settings/releases` (authenticated; `?force=1` refreshes). It uses the
public latest-release endpoint without credentials, a shared six-hour process
cache, bounded network timeouts, and a five-minute retry interval after errors.
Manual checks bypass the TTL. Notes from a previous successful check survive
network failures within the process and are clearly marked as saved results.
Restarting Flask clears the cache. Remote notes render as literal text; follow
**View release on GitHub** for formatted Markdown and any release downloads.

Published-release comparison is separate from mesh Git revision checks. Equal
release versions do not prove every device is current; Jobs → Devices remains
the installation/update surface. No updater or automatic restart is triggered
by checking or reading release notes.

When publishing a release, write the GitHub Release body before considering the
release complete. Include user-visible additions, fixes, and any migration or
restart instructions; avoid a raw commit-log dump. Tags alone have no release
notes and do not appear in this feed. Keep the version bump at release time.

## Packaged artifacts and release CI

Pushing a `vX.Y.Z` tag that matches `electron/package.json` runs
`.github/workflows/release.yml`: Quality, Desktop smoke and the packaged Host
build (`packaged-host.yml`) must all pass, then a **draft** GitHub Release is
created with the Linux AppImage, Windows NSIS installer and portable exe plus a
combined `SHA256SUMS`. Review the draft, write the notes, then publish it by
hand — CI never publishes.

Packaged Hosts are self-contained: `electron/bundle-python.js` ships a
python-build-standalone CPython pinned by SHA-256 in
`electron/python-runtime.json`, with `src/requirements/requirements.txt` held to
`electron/python-constraints.txt` (wheels only). They never use system Python or
a repo `.venv`. To move the runtime, update every field of
`python-runtime.json` from the release `SHA256SUMS`; after changing runtime
requirements run `node electron/bundle-python.js --lock` and commit the new
constraints. `electron/tests/packaged-host-e2e.cjs` is the clean-environment
check CI runs against the installed artifact (local runs need an isolated
display and must not share ports or a process namespace with a live Host).

**Merge gate.** Mark these checks as required on `main` (repository settings →
branch protection): `Quality / python (ubuntu-latest)`, `Quality / python
(windows-latest)`, `Quality / browser`, `Quality / android`,
`Desktop smoke / desktop (ubuntu-latest)`, `Desktop smoke / desktop
(windows-latest)` and both `Desktop smoke / packaged-host / packaged-host`
matrix jobs. Desktop smoke has no path filter so required checks never wait on
a skipped workflow.

**Windows signing.** Set repository secrets `WIN_CSC_LINK` (base64 `.pfx` or
URL) and `WIN_CSC_KEY_PASSWORD`; electron-builder then Authenticode-signs the
Windows artifacts. Without them the artifacts are unsigned and the checksums
are the only verification — say so in the release notes.

**Desktop updates.** Packaged Hosts report `updateSource: "release"` and do not
build Client updates; update every device from the release. Source-checkout
Hosts may still build an `app.asar` for their Clients, which accept it only over
the Host's pinned HTTPS identity. Linux Clients never swap in place.

# Cuttle versioning

How versions work today, and how desktop auto-update relates to them.

## Components

| Component | Where | What users see | What drives updates |
|---|---|---|---|
| **Electron desktop shell** | `electron/package.json` → `version` | `npm start` prints `cuttle-desktop@X.Y.Z`; also `packageVersion` in `/api/desktop/electron` | **Content hash** of `main.js` + `preload.js` + `connect.html` (not semver). Client Update button when remote hash ≠ local. |
| **Web UI / Flask** | served from `src/web` on the host | Cache-busted `?v=` query strings on JS/CSS | Host restart / hard refresh — not a separate semver channel |
| **Android** | `apps/mobile/package.json` + `android/app/build.gradle` | `versionName` (e.g. `1.0`); npm package `0.1.0` | **`versionCode`** = unix timestamp on each Gradle build + APK content hash via `/api/mobile/android` |

## Desktop auto-update (same idea as Android)

Packaged Client Electron already:

1. Asks host `GET /api/desktop/electron` for `hash` + `packageVersion` + `app.asar` URL  
2. Compares **hash** to local `localDesktopHash()`  
3. Downloads `app.asar` and swaps when newer  

So Electron can update like Android **without** bumping semver — hash changes when shell files change. Semver (`0.2.0`, …) is for humans and release notes; bump it when you ship a meaningful desktop release.

## Rules of thumb

1. **Bump `electron/package.json` version** when you intentionally ship a desktop shell release (workers mesh, titlebar UX, connect flow, etc.).
2. Do **not** rely on semver alone for Client update detection — keep hashing `HASH_FILES` in `desktop_electron.py` / `pack-desktop-update.js`.
3. **Web** changes ride the host; Clients load UI from the host, so they pick up web updates without an asar bump (only shell/preload/connect need asar).
4. **Android**: prefer bumping `versionName` in lockstep with meaningful releases; `versionCode` already auto-increments.

## Current pins

- Desktop: `0.2.0` (was stuck at `1.0.0` forever — cosmetic; updates were hash-based)
- Mobile npm: `0.1.0` / Android `versionName` `1.0` — still lagging; bump on next mobile ship

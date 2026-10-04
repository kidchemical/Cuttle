# Desktop build and launch troubleshooting

Start with the [desktop README](README.md). Build from `electron/` using the
Windows or Linux target scripts in `package.json`; output is under `dist/`.

## Missing dependencies or Python

Run `npm ci` in `electron/`. Host requires Python 3.11+ and a root `.venv` with
runtime dependencies; use the OS-specific commands in the desktop README.
Packaged artifacts exclude Python, venvs, secrets and dependencies. Client
worker mode also needs Python. Vendor CLI login is separate from Cuttle API keys.

## Locked files or port conflict

Inspect the build error or desktop/daemon logs to identify the owner. Close the
UI if it locks build output; UI close leaves the daemon running. If daemon
shutdown is necessary, use Host tray **Exit**. Never kill arbitrary Python
processes or use the removed `kill_bots.py`. Restart Flask through the restart
card/native `/restart` path in [action forms](../.cuttle_global/docs/action-forms.md).
Rebuild when the owned output files are released.

## Blank window or unreachable Host

Check Developer Tools (`Ctrl+Shift+I`) and the launched process's console/logs.
Host uses the daemon, not direct Flask startup. Client needs a reachable Host
with Phone/LAN access enabled. Electron prefers HTTP :8000; HTTPS :8080 is the
fallback. Do not enter `http://<host>:8080`. See [remote access](../docs/guides/REMOTE_ACCESS.md)
for binding, firewall and certificate trust requirements.

## Package too large or permission errors

Review `extraResources` filters in `package.json`. Venvs, caches, tests, output,
logs and `.env` files should be excluded. Keep the development venv at the repo
root. Inspect unpacked resources under `dist/win-unpacked/resources/app/src`
or the corresponding Linux unpacked directory. Avoid packaging live state.

## Long paths or memory pressure

Use a shorter checkout path on Windows if a build reports path length errors.
For a heap error, increase Node memory for this build only:

```powershell
# Windows PowerShell, from electron/
$env:NODE_OPTIONS = "--max-old-space-size=4096"
npm run build
```

```bash
# POSIX, from electron/
NODE_OPTIONS=--max-old-space-size=4096 npm run build:linux
```

## Version and update behavior

Bump SemVer at release time only; see the [release runbook](../.cuttle/docs/cuttle-release.md).
Git/hash update checks already ship. UI updates preserve the Host daemon.
Client self-update uses detached updater scripts; consult the
[workers guide](../docs/guides/CUTTLE_WORKERS.md) before updating a checkout with local work.

When reporting a failure, include the platform, Node/npm/Python versions,
exact build command, full error and relevant logs, with secrets removed.

# Cuttle desktop: Host and Client

The Electron shell runs on Windows and Linux. **Host** connects to the local
Cuttle daemon, starting it when needed. **Client** connects to an existing Host
on a trusted LAN and can enroll a device worker; it does not start a local Flask
chat server. Client worker mode is enabled by default and can be disabled in
`desktop-config.json` (`workerMode: false`).

## Prerequisites

For Host, set up Python **3.11+** and the repository venv using the
[root quick start](../README.md#quick-start). Python and its dependencies are not
bundled in the desktop artifacts. A Client that runs a device worker also needs
Python; a UI-only Client connects to the remote Host.

Source development/builds need Node and npm. Install the chosen vendor CLI and
authenticate it separately: Codex uses `codex login`, Cursor its CLI login, and
Claude account login or an Anthropic key. Optional keys in `src/.env` serve
Cuttle's routing brain, direct LLM calls and vision; they do not log every CLI in.

From the repository root, install Python runtime dependencies:

```bash
# POSIX
.venv/bin/python -m pip install -r src/requirements/requirements.txt
```

```powershell
# Windows PowerShell
.\.venv\Scripts\python.exe -m pip install -r src/requirements/requirements.txt
```

## Launch from source

Install desktop dependencies from `electron/`:

```bash
npm ci
npm start -- --mode=host
# Or connect to an existing Host:
npm start -- --mode=client
```

Linux repository launchers (from the repository root) handle the Chromium sandbox:

```bash
./.cuttle/scripts/launch-cuttle-host.sh
./.cuttle/scripts/launch-cuttle-client.sh
```

The Client connection screen selects the Host. Enable **Phone/LAN access** on
that Host (`discovery.lan_access_enabled`) before connecting from another device.
Electron prefers the same-app HTTP portal at `http://<host>:8000`, with HTTPS
`https://<host>:8080` as fallback. HTTP is cleartext: use it only on a trusted
network. The default HTTPS certificate is self-signed; Electron's local handling
does not establish browser or native Android trust. See [remote access](../docs/guides/REMOTE_ACCESS.md).

## Build

Run from `electron/` on the target platform:

```bash
# Windows
npm run build           # NSIS and portable targets
npm run build:dir       # unpacked Windows directory
npm run build:portable  # portable executable
# Linux
npm run build:linux     # AppImage
npm run build:linux:dir  # unpacked Linux directory
```

Artifacts are under `electron/dist/`, with names derived from `package.json`,
for example `Cuttle-<version>-portable.exe` and `Cuttle-<version>-x64.AppImage`.
Use the package's current version; do not hardcode a release number in commands.
Bump SemVer only for a release, following the [release runbook](../.cuttle/docs/cuttle-release.md).

## Lifetime and updates

Closing/relaunching the UI or applying an update preserves the daemon and active
turns. Only Host tray **Exit** is the explicit path to stop a daemon it spawned;
it asks first when agent turns are running. Do not terminate Python processes
in Task Manager as a restart procedure. Flask restarts are daemon-owned: use the
Cuttle restart card or native `/restart` control, as described in
[action forms](../.cuttle_global/docs/action-forms.md).

Desktop update/hash checks and Client worker self-update already exist. Workers
can be stale because of SemVer mismatch, git revision mismatch or stale boot
revision. Updates come from the configured git remote, not uncommitted Host
files. See the [workers guide](../docs/guides/CUTTLE_WORKERS.md) for checkout
preservation and updater behavior.

For launch and packaging failures, see [troubleshooting](BUILD_TROUBLESHOOTING.md).

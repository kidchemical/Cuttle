# Desktop launch guide

Follow the [desktop README](README.md) for the supported Host/Client setup,
Python 3.11+ prerequisites, OS-specific venv commands, vendor CLI authentication,
and Windows/Linux builds.

- **Host:** connects to or starts the local daemon; the daemon owns Flask.
- **Client:** selects an existing trusted-LAN Host; enable Phone/LAN access on
  the Host before connecting. Worker mode requires Python and is optional.
- **Transport:** Electron prefers HTTP :8000 and falls back to HTTPS :8080.
  The default certificate is self-signed; HTTP is for trusted networks only.
- **Exit:** UI close/relaunch/update preserves the daemon. Host tray Exit is
  the explicit daemon shutdown path. Use the restart card for Flask restart.

From `electron/`, after `npm ci`, choose:

```bash
npm start -- --mode=host
npm start -- --mode=client
```

Linux users can instead use the repository's
`.cuttle/scripts/launch-cuttle-host.sh` or `launch-cuttle-client.sh` from the root.
See [build troubleshooting](BUILD_TROUBLESHOOTING.md) if startup fails.

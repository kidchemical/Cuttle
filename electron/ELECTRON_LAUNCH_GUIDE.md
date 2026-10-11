# Desktop launch guide

Follow the [desktop README](README.md) for the supported Host/Client setup,
Python prerequisites, OS-specific venv commands, vendor CLI authentication,
transport/ports, lifetime, and Windows/Linux builds.

From `electron/`, after `npm ci`:

```bash
npm start -- --mode=host
npm start -- --mode=client
```

Linux users can instead use the repository's
`.cuttle/scripts/launch-cuttle-host.sh` or `launch-cuttle-client.sh` from the root.
See [build troubleshooting](BUILD_TROUBLESHOOTING.md) if startup fails.

# Home automation runtime state

Owned by `managers.home_automation`; scoped to this Cuttle installation.
Copy `devices.example.json` to `devices.json` or use device discovery.

`devices.json` and `schedule.json` are local configuration. `auto_state.json` records
schedule application; `daemon_heartbeat.json` records scheduler health;
`govee_api_batch.lock` serializes API bursts across Flask and the daemon.
Only this README and the example are tracked. See `../README.md` for migration.

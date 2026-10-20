# Agent process resource limits

Optional Linux resource controls live in `api.agent_harness.resource_scope`.
They are **disabled by default**. Cuttle launches installed vendor CLIs through
their existing adapters; resource controls do not change executable discovery,
credentials, working directories, arguments or transport protocols.

## Platform support

- Linux: requires cgroup v2, `busctl` and a reachable systemd user manager with
  permission to manage the memory and task controllers. Missing support leaves
  agent execution unchanged. Failed attachment logs a bounded stderr warning.
- Windows and macOS: no resource attachment is attempted. This feature provides
  no memory protection on those platforms.
- Packaged builds: the same checks apply; there is no checkout, `.venv`, extra
  Python package, privilege escalation or installation step in the runtime hook.
  Restricted package environments may deny systemd access; execution continues.

## Configuration

In the Cuttle home's `config/machine_settings.json`, merge this machine setting
into the existing settings object (preserve the file's schema and other keys):

```json
"agent_resource_limits": {
  "enabled": true,
  "memory_high": "6G",
  "memory_max": "8G",
  "memory_swap_max": "512M",
  "tasks_max": 4096,
  "slice_memory_high": "60%",
  "slice_memory_max": "70%"
}
```

These are starting values for hosts that explicitly opt in, not universal
workload recommendations. Account for build tools and other descendants as well
as the agent itself. Percentages use physical host RAM, not available memory or
a container's allowance. Choose smaller absolute limits in constrained hosts.
Blank or `infinity` removes a memory ceiling. Set `enabled` to false or use
`CUTTLE_AGENT_SCOPES=0` to stop attaching new launches.

`MemoryHigh` requests reclaim/throttling; `MemoryMax` is a final ceiling that can
terminate processes. Per-run scopes collect descendants; the shared
`cuttle-agents.slice` bounds their aggregate use. Concurrent runs can exhaust the
aggregate budget even when each run is below its individual ceiling. Existing
scopes retain their limits when attachment is disabled; they disappear after
their processes exit. Re-enabling updates aggregate limits on the next attachment.

Attachment occurs synchronously after process creation at
`agent_process.attach_to_chat_run`, before the caller continues. Processes may
execute before attachment, so this is best-effort resource management. It does
not guarantee host stability or identify every OOM termination in chat replies.
No host service or existing turn is stopped to apply settings.

The single registration hook is the removal/replacement boundary; no transcript
or session migration is needed. Unit tests cover defaults, platform guards,
configuration, D-Bus requests and failure fallback. Live systemd enforcement and
packaged-platform behavior require explicit integration validation before
considering default-on rollout.

References: [systemd resource controls](https://www.freedesktop.org/software/systemd/man/latest/systemd.resource-control.html),
[systemd scopes](https://www.freedesktop.org/software/systemd/man/latest/systemd.scope.html).

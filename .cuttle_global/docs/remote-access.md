# Remote access — running a command on another machine

Use when a task needs a command run on a machine other than the one the agent
is on (another PC, a server, a NAS, a Raspberry Pi). Not for mesh jobs Cuttle
already owns (renders, file copy, Client updates): those are in
`cuttle-workers.md`.

Cuttle does not ship its own remote shell. It uses whichever tool the user
already trusts for this, so access control, approval prompts and audit stay
with that tool.

## 1. Find the user's tool and hosts

Read the install-local delta first: `<home>/personal/docs/remote-access.md`
(appended below when present). It names the tool, host aliases and users.
Without it, discover; never guess a host or user:

| Tool | Discover | Run a command |
|---|---|---|
| OpenSSH | `~/.ssh/config` `Host` entries | `ssh -o BatchMode=yes -o ConnectTimeout=10 <host> '<cmd>'` |
| Tailscale SSH | `tailscale status` | `tailscale ssh <user>@<machine> '<cmd>'` |
| Teleport | `tsh status`, `tsh ls` | `tsh ssh <user>@<node> '<cmd>'` |
| MeshCentral | `meshctrl ListDevices` | `meshctrl RunCommand --id <nodeid> --run "<cmd>" --reply` |

If none is set up, say so and point the user to §4. Do not install or
configure a remote-access server yourself.

## 2. Before running

- If the user did not explicitly ask for this command on this machine, confirm first
  with a `cuttle_confirm` card (`action-forms.md`) naming the machine and the exact command.
- Prefer read-only commands. Ask again before anything that changes the remote machine.
- Credentials never go on the command line or into chat. Passwords, key
  passphrases and tokens → `secret-vault.md` (e.g. `--askpass` for ssh) when that
  feature is on; otherwise let the tool's own agent/keychain handle them.
- Keep it non-interactive: no TTY, no password prompts (`BatchMode=yes`), a timeout.
  Long jobs follow `headless-turns.md`.
- Mind the remote shell: Windows OpenSSH defaults to `cmd.exe`; quote for it, or run
  `powershell -NoProfile -Command "…"`.

## 3. Approval lives in the tool

Cuttle's confirm card is agent etiquette, not enforcement. For a hard gate, use
the tool's own:

- **OpenSSH**: key-only auth; per-key restrictions in `authorized_keys`
  (`command=`, `from=`, `no-pty`).
- **Tailscale SSH**: ACL `ssh` rules; `"action": "check"` requires the user to
  re-authenticate in a browser before a session.
- **Teleport**: roles, access requests, per-session MFA, session recording.
- **MeshCentral**: device groups and user consent prompts on the target.

## 4. One-time setup (the user, on the target machine)

- **OpenSSH server**: Windows `Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0`
  then `Start-Service sshd`; macOS: Sharing → Remote Login; Linux: the distro's
  `openssh-server`. Then add the controlling machine's public key.
- **Tailscale SSH**: `tailscale set --ssh` on the target (Linux and macOS targets
  only; Windows machines use OpenSSH over the tailnet instead).
- **Teleport / MeshCentral**: enroll the machine with that product's agent.

Record the result in `<home>/personal/docs/remote-access.md`, for example:

```markdown
- Tool: OpenSSH over Tailscale
- laptop → `ssh laptop` (Windows, cmd.exe), user `me`
- nas → `tailscale ssh admin@nas` (read-only unless asked)
```

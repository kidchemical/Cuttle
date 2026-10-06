# Headless Linux server Host

Run the Cuttle daemon on an always-on Linux box (a spare laptop or a mini PC)
with no desktop session. Other machines reach it over the LAN through a browser,
the Electron Client, or the phone app. The server is the **Host**: it runs Flask,
cron, the worker coordinator, and the vendor agent CLIs.

Without `DISPLAY` or `WAYLAND_DISPLAY`, the daemon skips the tray icon and the UI
auto-open on its own. Stop it with SIGTERM (systemd) instead of tray **Exit**.

## 1. Install the checkout

Follow the Ubuntu steps in the [README](../../README.md#install-and-run) (clone,
`.venv`, requirements, `src/.env`). Electron is not needed on the server.

## 2. Configure `src/.env`

```bash
CUTTLE_LAN_ACCESS=1          # listen on 0.0.0.0 instead of loopback (see REMOTE_ACCESS.md)
OWNER_USER_EMAIL=you@example.com
# + any provider keys (router brain, vision, direct LLM)
```

`CUTTLE_LAN_ACCESS=1` overrides `discovery.lan_access_enabled`. Set it here,
because Settings can't be reached from another machine until the LAN is on.
Alternatively, tunnel once (`ssh -L 8080:127.0.0.1:8080 server`) and toggle
**Settings → Phone/LAN access**.

The first (owner) account can only be registered from the host itself, so a
LAN peer cannot claim a fresh install. On a headless server, register it
through the same tunnel (`https://127.0.0.1:8080`). Registration then closes;
`settings.json` → `auth.allow_registration: true` reopens it for extra
(non-owner) accounts.

## 3. Authenticate the vendor CLIs on the server

Cuttle hosts CLIs but never installs or logs them in. Over SSH, install each
harness you use and complete its login (device-code or API key). Examples:
`codex login`, the Cursor CLI login, `claude` login or an Anthropic key. The
prerequisites are in each manifest under
[`src/api/agent_harness/agents/`](../../src/api/agent_harness/agents/).

## 4. Install the service

From a normal login shell (the installer saves your current `PATH` so the CLIs
resolve the same way under systemd):

```bash
.cuttle/scripts/cuttle-service.sh print      # preview the unit
.cuttle/scripts/cuttle-service.sh install    # write, enable, start, enable linger
.cuttle/scripts/cuttle-service.sh status     # status + last 30 journal lines
.cuttle/scripts/cuttle-service.sh uninstall
```

It installs a **user** unit (`~/.config/systemd/user/cuttle.service`) and turns
on `loginctl enable-linger` so it starts at boot without a login. After
installing a new CLI or changing `PATH`, re-run `install`; the refreshed environment
takes effect at the next service restart.

Restarts keep their normal owners:

| Need | Use |
|---|---|
| Python changed (Flask only) | `/restart` in chat or the restart card |
| Whole daemon | `.cuttle/scripts/restart-daemon.sh` (defers to `systemctl --user restart cuttle` when the unit is active) |
| Stop | `systemctl --user stop cuttle` |

Logs: `journalctl --user -u cuttle -f` and the daemon log under `~/cuttle_logs`.

## 5. Host prep

- **Lid and sleep:** in `/etc/systemd/logind.conf` set `HandleLidSwitch=ignore`
  (and `HandleLidSwitchExternalPower=ignore`), then
  `sudo systemctl restart systemd-logind`. Disable suspend:
  `sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target`.
- **Firewall:** allow only your LAN:
  `sudo ufw allow from <YOUR_LAN_CIDR> to any port 8080,8888,8000 proto tcp`.
  Replace `<YOUR_LAN_CIDR>` with your LAN's actual subnet.
- **Stable address:** a DHCP reservation, so Clients and phones keep finding the Host.
- **TLS:** Flask's self-signed certificate (it includes the LAN IP) is fine for a
  trusted LAN. Native clients that need real trust → the HTTPS reverse-proxy
  option in [Remote access](REMOTE_ACCESS.md).

## 6. Connect

- Browser: `https://<server-ip>:8080` (check the certificate before accepting).
- Electron Client on a desktop: `.cuttle/scripts/launch-cuttle-client.sh` /
  installed Cuttle Desktop, then pick the Host. It enrolls as a worker
  ([Cuttle Workers](CUTTLE_WORKERS.md)).
- Phone: [`apps/mobile/README.md`](../../apps/mobile/README.md).

## Limits

Features that need a desktop on the Host don't run there: the tray menu, desktop
notifications, auto-opening the UI, and GUI-driving recipes. Use a Client
for those. The server does not need Electron or the Chromium sandbox fix.

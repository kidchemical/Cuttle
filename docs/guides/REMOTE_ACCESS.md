# Remote access and LAN discovery

Cuttle is designed for trusted local users and devices. Its primary listener is
**HTTPS :8080**, using a self-signed certificate by default. The same Flask app
also exposes **HTTP :8000**; this is Electron's preferred portal and is cleartext.
An optional phone HTTPS listener uses :8888. Port 8080 is not cleartext HTTP.

## LAN access

Enable **Phone/LAN access** in Settings (`discovery.lan_access_enabled: true`).
With LAN disabled, listeners bind to loopback; enabling it binds for remote
access. Apply binding changes through the daemon-owned restart path, and permit
only the intended network/devices in the firewall. In Cuttle chat use the
restart card; see [action forms](../../.cuttle_global/docs/action-forms.md).
On a headless server, set `CUTTLE_LAN_ACCESS=1` in `src/.env` instead; see
[Headless Linux server Host](HEADLESS_SERVER.md).

For a browser, use `https://<host>:8080`. Verify the Host certificate before
accepting a self-signed warning. Native apps need their own certificate trust;
accepting a warning in Chrome does not configure OkHttp or Android app trust.
`http://<host>:8000` is an alternative only on a trusted network where cleartext
credentials and traffic are acceptable.

## mDNS discovery (optional)

Install the optional dependency from the repository root:

```bash
# POSIX
.venv/bin/python -m pip install zeroconf
```

```powershell
# Windows PowerShell
.\.venv\Scripts\python.exe -m pip install zeroconf
```

It is also listed in `src/requirements/requirements-optional.txt`.
Enable both `discovery.lan_access_enabled` and `discovery.mdns_enabled` in
Settings. `discovery.mDNS.enabled` is not a recognized key. Apply startup changes
through the restart card. The advertiser publishes `_cuttle._tcp.local.`;
clients need mDNS support on the same LAN. Discovery does not authenticate peers
or make a self-signed certificate trusted. Manual hostname/IP entry still works
without mDNS.

## VPN, reverse proxy or SSH tunnel

A VPN such as Tailscale or an SSH tunnel can restrict network reachability.
For TLS-only native clients, use an HTTPS reverse proxy with a certificate whose
hostname and issuing CA are trusted by the client, forwarding to the loopback
HTTP portal `http://127.0.0.1:8000`. Keep the backend bound to loopback when the
proxy is the only remote entry point; ensure it supports SSE streaming without
buffering and forwards the required auth/session traffic.

[Tailscale Serve](https://tailscale.com/docs/reference/tailscale-cli/serve) is tailnet-only; [Funnel](https://tailscale.com/docs/reference/tailscale-cli/funnel) makes a service public. This guide does
not prescribe an unverified Serve/Funnel CLI command: check the installed CLI's
help for backend scheme and external port. A proxy's HTTPS address/port is not
necessarily the backend's :8080. Public exposure is outside the trusted-LAN
assumption; pairing alone does not protect other API routes or isolate agent execution.

For an SSH local tunnel:

```bash
ssh -L 8080:127.0.0.1:8080 user@cuttle-machine
```

Then open `https://localhost:8080` on the client. The tunnel preserves the
backend certificate; it does not establish trust. SSH remote forwarding is
not an automatic public listener and depends on the server's bind policy.

## Authorization

Configure owner/authentication and restrict network access before sharing a
Host. Review CORS and [pairing/allowlist scope](PAIRING_AND_ALLOWLIST.md).
Do not treat discovery, a VPN, or a browser certificate exception as permission
to execute agent turns. Keep tokens and provider secrets out of proxy logs.

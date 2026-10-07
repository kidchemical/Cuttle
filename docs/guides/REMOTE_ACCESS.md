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
On a headless server, set `CUTTLE_LAN_ACCESS=1` in `<home>/.env` instead; see
[Headless Linux server Host](HEADLESS_SERVER.md).

For a browser, use `https://<host>:8080`. Verify the Host certificate before
accepting a self-signed warning. Native apps need their own certificate trust;
accepting a warning in Chrome does not configure OkHttp or Android app trust.
`http://<host>:8000` is an alternative only on a trusted network where cleartext
credentials and traffic are acceptable.

## Custom ports (env-only)

The three listener ports default to 8080 (primary HTTPS), 8000 (companion
HTTP), and 8888 (phone HTTPS). Override in `<home>/.env`:

```ini
CUTTLE_HTTPS_PORT=8443
CUTTLE_HTTP_PORT=8001
CUTTLE_PHONE_HTTPS_PORT=8890
```

Empty means unset (file, then default, applies). Malformed, zero,
out-of-range (1–65535), or duplicate effective values refuse startup — the
daemon and Flask exit instead of serving the defaults, because the defaults
may still be live on another instance. A file value overridden by a valid
env value for the same key is ignored, not rejected. There is no
settings/UI knob for ports.

Port changes require a **daemon cold restart** (tray Exit, then start again):
the daemon resolves the primary port for its health/conflict checks and
spawns Flask as a child, so a Flask-only restart cannot move the daemon's
side of the same triple.

Covered by the owner: daemon health/conflict checks, Flask listeners,
mDNS advertisement, CORS, generated portal/QR URLs, OAuth redirect fallback,
terminal URLs, and the Python CLIs (`panes_cli`, device-worker approval
paths) — standalone CLIs read the same `<home>/.env` with process-env
precedence, so no exports are needed. The dev shadow refuses both the
defaults and the configured ports.

Custom ports on mobile clients: the Capacitor mobile shell accepts a custom
port in Server settings (8000 / 8888 presets or Custom 1–65535) and uses the
entered scheme + port for the WebView, reachability probes, notifications,
and update downloads; invalid ports are rejected before anything is saved.
The native Android companion takes a full base URL and uses the entered
scheme + port verbatim for SSE and replies, but still requires
platform-trusted HTTPS (a valid proxy certificate, as before) — the app does
not opt into cleartext or bypass certificate validation, and failed HTTPS
connections never rewrite the saved server to HTTP.

Custom ports on desktop Electron: the local Host resolves listener ports
from `api.server_ports` via `python -m api.server_ports` (read-only,
fail-closed — Electron never parses `<home>/.env` itself) and loads the UI
from the configured ports, so a custom-port Flask is recognized as already
running. A malformed port configuration shows a connect error with no
probing of defaults and no daemon spawn. An unavailable local Python or
port query also shows the connection error instead of guessing defaults. LAN client mode probes the entered host
and ports as given and persists a selected-endpoint policy with the saved
target, reapplied on launch:

- An explicitly selected endpoint (typed `http(s)://` URL, or documented
  bare `host:port` HTTP) is used **singly** — probing, UI load, API/update/
  enrollment/worker requests, and chat pool-switch redirects touch only it.
  An explicit URL without a port means the protocol default (443/80), never
  8080/8000. Invalid ports, unsupported schemes, and URL credentials are
  rejected before anything is saved.
- Explicit connections ignore historical companion metadata and never discover
  or try another listener. If the selected endpoint is down, the connection
  fails there; it does not silently switch ports or downgrade HTTPS to HTTP.
  Local Host mode uses the configured listener pair, HTTP first. Bare-host
  legacy targets keep the default HTTP-first pair.
- The Host certificate exception likewise covers exactly the selected
  endpoint host + effective HTTPS port (an omitted port counts as 443).

Residual limitations with custom ports: standalone scripts keep literal defaults: `diagnose_lan.bat`
and the `src/scripts/utilities/* --base` helpers (pass `--base`
explicitly). Web-UI hint strings that name a port render the configured one
from the server; other hardcoded examples in docs/samples may still show
8080. OAuth provider redirect URIs are port-sensitive and must be updated in
the provider console. Pre-policy Electron configs (saved before the
selected-endpoint policy) restore as legacy pairs until the next explicit
connect writes a policy; explicit URLs remain single endpoints regardless of host version. Packaged-app Host Python resolution and a real custom-port
daemon boot remain runtime-unverified (no live runtime in this change);
coverage is behavioral suites with fakes plus the temp-env CLI smoke.

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

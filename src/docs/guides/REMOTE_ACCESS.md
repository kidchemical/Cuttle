# Remote Access and Discovery

You can reach the Cuttle web backend from other devices using **Tailscale**, **SSH tunnels**, or **mDNS** (LAN discovery).

## Tailscale

If you use [Tailscale](https://tailscale.com), you can expose the Cuttle web server (e.g. port 8080) to your tailnet.

1. Install Tailscale on the machine running Cuttle.
2. Use **Tailscale Serve** (tailnet-only):
   ```bash
   tailscale serve --bg 8080
   ```
   Or expose with Funnel (public HTTPS; use auth):
   ```bash
   tailscale funnel 8080
   ```
3. Access Cuttle via your machine's Tailscale hostname (e.g. `https://your-machine:8080`).

Keep the Flask backend bound to `127.0.0.1` or `0.0.0.0` as needed; Tailscale serves as a reverse proxy.

## SSH tunnel

From another device, create an SSH tunnel to the machine running Cuttle:

```bash
ssh -L 8080:127.0.0.1:8080 user@cuttle-machine
```

Then open `http://localhost:8080` in your browser on that device; traffic is forwarded to Cuttle.

For remote port forwarding (expose Cuttle to a server):

```bash
ssh -R 8080:127.0.0.1:8080 user@jump-server
```

Then access Cuttle via the jump server’s address on port 8080.

## mDNS (LAN discovery)

On the same LAN, other devices can discover Cuttle if mDNS is enabled.

- **Option A**: Enable “Advertise Cuttle on LAN” in Settings (or set `discovery.mDNS.enabled: true` in settings). The backend will advertise a service type `_cuttle._tcp` with the local URL (e.g. `http://hostname.local:8080`). Phones/tablets that support mDNS/Bonjour can then discover “Cuttle” in the list of local services.
- **Option B**: Manually note the machine’s hostname (e.g. `cuttle-pc.local` on macOS/Linux or the Windows name) and port 8080, and open `http://cuttle-pc.local:8080` from another device on the LAN.

If mDNS advertisement is implemented, it runs only when the web server is running and the setting is enabled.

## Security

- Prefer Tailscale or SSH over exposing the backend directly to the internet.
- Use pairing and allowFrom (see [Pairing and Allowlist](PAIRING_AND_ALLOWLIST.md)) when allowing untrusted users.
- If using Funnel or a public URL, enable authentication and keep the backend and API keys locked down.

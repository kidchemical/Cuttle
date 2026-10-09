# Cuttle Companion — Android notification prototype

This optional native app receives SSE at `GET /api/mobile/events`, displays
`chat_complete`/`interaction` notifications, and posts answers to
`POST /api/mobile/reply`. Compatible watches may mirror the phone notifications.
It is a prototype, separate from the main [mobile app](../mobile/README.md).

## Backend and transport

Set a random `CUTTLE_MOBILE_TOKEN` in the Host's `<home>/.env` and apply it through
the daemon-owned restart path. Enter that exact token in the app: an empty field
is not valid. An unset or blank Host token disables these companion routes
(HTTP 401); there is no development fallback.

Cuttle uses HTTPS :8080, HTTP :8000, and optional phone HTTPS :8888. This app's
stock OkHttp clients verify TLS normally. The Host's default self-signed HTTPS
certificate is not trusted by this app, and accepting it in Chrome does not
change app trust. The manifest does not opt into cleartext; `http://<host>:8080`
is both the wrong scheme and an unsupported cleartext setup.

Use an **HTTPS reverse proxy with a valid hostname certificate trusted by Android's
system CA store**, for example `https://cuttle.example.net` (replace with your
own private deployment). Forward it to the Host's loopback `http://127.0.0.1:8000`
with SSE buffering disabled. Keep access private via LAN/VPN and avoid logging
token query strings. This app does not implement certificate bypass, pinning,
or trust for user-installed CAs. See [remote access](../../docs/guides/REMOTE_ACCESS.md).

A same-Host loopback proxy works with LAN access disabled. For a proxy on another
device, enable **Phone/LAN access** (`discovery.lan_access_enabled`) and restrict
the firewall to the proxy. Direct LAN access also requires that setting but does
not solve the default certificate trust problem. Use the restart card for
listener changes; do not start a second Flask instance.

## Build and connect

1. Open `apps/android_companion` in Android Studio with JDK 17 and Android SDK 34.
2. Build/run on a phone (minimum API 26) and grant notification permission.
3. Enter the trusted HTTPS proxy base URL and configured token; tap **Start**.
4. Verify the SSE connection and an interaction reply on your installation.

The transport contract above is based on manifest, OkHttp and backend source
inspection. No APK/device/proxy integration check is claimed; the app is not a
ready-to-use direct self-signed LAN client.

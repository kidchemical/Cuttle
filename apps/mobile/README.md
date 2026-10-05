# Cuttle Mobile — native LAN client

Android/iOS **native shell** for Cuttle — same idea as the desktop **Electron** app:

```
Electron (PC):     BrowserWindow → http://127.0.0.1:8000/app_shell.html (HTTPS :8080 fallback)
Cuttle Mobile:     WebView       → http(s)://<pc-lan-ip>:<port>/app_shell.html
```

The phone does **not** run the Python daemon. It is a full-screen native window onto the Cuttle UI on your PC (chat, spaces, settings, dashboards, etc.), with:

- Cold start → opens Cuttle directly (when a server is saved)
- In-app loading / error screens (not the system browser)
- Native bridge (`window.cuttleMobile`) for **Server** settings + reload
- Android HTTPS uses platform certificate trust and validates the server address

## Prerequisites

1. Cuttle daemon running on the PC  
2. **Settings → Phone / LAN access** enabled  
3. Phone on the same Wi‑Fi  

## Build / install (Android)

Build prerequisites (both platforms): **Node.js 20 or newer**, **JDK 21**
(`JAVA_HOME` pointing to that JDK), and Android SDK **Platform 35** with SDK
licenses accepted. Create the repository-root `.venv` and install
`src/requirements/requirements.txt` as described in the [root README](../../README.md)
first; Gradle uses that venv for shell hashing and APK publication, never PATH
Python. Configure the SDK with `ANDROID_HOME` or `android/local.properties`
(see `android/local.properties.example`).

From the repository root, Windows PowerShell:

```powershell
cd apps/mobile
npm ci
npm run sync
.\build-android-debug.bat
```

POSIX (Android SDK and JDK 21 configured):

```bash
cd apps/mobile
npm ci
npm run sync
cd android
./gradlew assembleDebug
```

APK: `android/app/build/outputs/apk/debug/app-debug.apk`

Use HTTP port **8000** and your PC IP from Settings → Phone / LAN access.
HTTP sends credentials and chat traffic in cleartext; this client is for a trusted
LAN. Android rejects untrusted, expired, or mismatched HTTPS certificates in both
the WebView and native probes, notifications, and update downloads. The default
Cuttle self-signed certificate is not automatically trusted. For HTTPS, use an
endpoint with a certificate trusted by Android and a matching hostname (for
example, a private HTTPS proxy). Accepting a browser warning does not establish
app trust. Failed HTTPS connections never change the saved server to HTTP;
select HTTP explicitly in Server settings if appropriate for your LAN.
See [remote access](../../docs/guides/REMOTE_ACCESS.md) for private proxy/VPN options.

## First launch

1. Enter PC IP + port → **Open Cuttle**  
2. App loads `app_shell` inside the native WebView  
3. Later launches skip setup and open Cuttle immediately  
4. Tap **Server** (bottom-right) to change PC / port  

## Safe area (notch / gesture bar)

The native shell uses `@capacitor-community/safe-area` plus `viewport-fit=cover` on
the setup screen and on the PC-served UI (`app_shell`, chat). Insets apply only when
the app identifies as Cuttle Mobile (`is-cuttle-mobile` class) so desktop Electron
and browser layouts are unchanged.

Rebuild the phone APK after changing `apps/mobile/` native deps; hard-refresh or
restart Flask is enough for CSS/JS served from the PC.

## iOS

Project under `ios/` — build on a Mac with Xcode (`npm run open:ios`).

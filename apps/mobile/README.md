# Cuttle Mobile — native LAN client

Android/iOS **native shell** for Cuttle — same idea as the Windows **Electron** app:

```
Electron (PC):     BrowserWindow → https://127.0.0.1:8080/app_shell.html
Cuttle Mobile:     WebView       → http(s)://<pc-lan-ip>:<port>/app_shell.html
```

The phone does **not** run the Python daemon. It is a full-screen native window onto the Cuttle UI on your PC (chat, node editor, settings, etc.), with:

- Cold start → opens Cuttle directly (when a server is saved)
- In-app loading / error screens (not the system browser)
- Native bridge (`window.cuttleMobile`) for **Server** settings + reload
- Self-signed HTTPS accepted inside the app WebView

## Prerequisites

1. Cuttle daemon running on the PC  
2. **Settings → Phone / LAN access** enabled  
3. Phone on the same Wi‑Fi  

## Build / install (Android)

```bash
cd apps/mobile
npm install
npm run sync
build-android-debug.bat
```

APK: `android/app/build/outputs/apk/debug/app-debug.apk`

Use HTTP port **8000** and your PC IP from Settings → Phone / LAN access.

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

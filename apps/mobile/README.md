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

Build prerequisites (both platforms): **Node.js 22.12 or newer**, **JDK 21**
(`JAVA_HOME` pointing to that JDK), and Android SDK **Platform 36** and **Build Tools 36.0.0**, with
SDK licenses accepted. Create the repository-root `.venv` and install
`src/requirements/requirements.txt` as described in the [root README](../../README.md)
first; Gradle uses that venv for shell hashing and APK publication, never PATH
Python. Configure the SDK with `ANDROID_HOME` or `android/local.properties`
(see `android/local.properties.example`).

From the repository root, Windows PowerShell:

```powershell
cd apps/mobile
npm ci
npm run sync:android
.\build-android-debug.bat
```

POSIX (Android SDK and JDK 21 configured):

```bash
cd apps/mobile
npm ci
npm run sync:android
cd android
./gradlew assembleDebug
```

APK: `android/app/build/outputs/apk/debug/app-debug.apk`

For isolated worktree/CI validation, set `CUTTLE_ANDROID_PUBLISH=0` to skip
automatic LAN update publication. `CUTTLE_ANDROID_PYTHON` can point to an existing
Cuttle venv interpreter when the worktree has no `.venv`; build hashing still uses
the worktree sources. Keep Gradle and Android user caches private to that build.

The Android toolchain follows the [Capacitor 8 migration guide](https://capacitorjs.com/docs/updating/8-0):
AGP 8.13.0, Gradle 8.14.3, minimum Android API 24, compile/target API 36.

### Development signing

Debug APKs are for development, not public releases. Gradle normally uses its
local debug keystore. To retain an existing development installation's signing
identity, explicitly set `CUTTLE_ANDROID_DEBUG_KEYSTORE` to its original keystore
under the Cuttle home's `secrets/` directory (`~/.local/share/cuttle/secrets/`,
`%LOCALAPPDATA%\Cuttle\secrets\`, or `$CUTTLE_HOME/secrets/`). Cuttle never searches
other installations or disks for keys. Changing the signer requires an explicit
uninstall/reinstall (which can remove local app data); the updater never does that.

### Public releases and updates

No APK is committed to this repository; `dist/` is gitignored. Today each host
builds its own APK (as with the Electron app) and serves it to its phones over
the LAN. Signed release APKs attached to GitHub Releases are planned, not yet
published; the steps below are for whoever produces them.

The publisher maintains a private release keystore, backs it up securely, and
uses the same signing identity for updates. Never commit a keystore or its
passwords. Users installing published APKs do not need build tools or the
publisher's private key. This updater supports one stable signer; signing-key
rotation needs a separate migration design.

Place the keystore under the Cuttle home's `secrets/` and supply these environment
variables through your private build environment:

- `CUTTLE_ANDROID_KEYSTORE`: absolute keystore path
- `CUTTLE_ANDROID_STORE_PASSWORD`, `CUTTLE_ANDROID_KEY_ALIAS`,
  `CUTTLE_ANDROID_KEY_PASSWORD`: release signing credentials
- `CUTTLE_ANDROID_VERSION_CODE`: positive integer, higher than every prior release
- `CUTTLE_ANDROID_VERSION_NAME`: display version (optional)

From `apps/mobile`, run `npm ci`, `npm run sync:android`, then
`cd android` and `./gradlew assembleRelease` (Windows: `gradlew.bat`). Release
builds refuse missing signing configuration and the standard debug alias.
Development APKs currently use timestamp version codes; a release replacing an
existing development installation must exceed that installed version code and
retain its signer. Public releases should use deliberately assigned increasing
version codes with a dedicated release key.

Publish a verified release from the repository root:

```bash
PYTHONPATH=src .venv/bin/python -m api.mobile_android_update publish \
  --apk apps/mobile/android/app/build/outputs/apk/release/app-release.apk
```

Publication requires the SDK Build Tools (`apksigner`, `aapt`) and Java. It checks
the APK signature, package, embedded build identity, signer continuity, and
version progression before replacing the manifest. Distribute the resulting
`apps/mobile/dist/update/manifest.json` and its named APK together; no private
key is distributed. The hosting checkout must match the build's mobile sources.

Downloads use immutable SHA-256 URLs, so an in-progress download cannot silently
switch to a newer artifact. The phone verifies the checksum, embedded identity,
package, signing certificate, and higher version code before opening Android's
installer. Android still requires the user's installation confirmation. Opening
the installer never counts as installation success; explicit checks can retry,
and the background installer hint expires after two minutes.

Automatic local debug builds are opt-in with `CUTTLE_MOBILE_AUTO_REBUILD=1` and
require the development toolchain, npm dependencies, and the intended debug key.
The default serves verified published artifacts without building on update
checks. Source changes without a matching APK report that the artifact is not
ready rather than offering a stale APK.

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

Project under `ios/` requires Xcode 26+ and iOS 15+. Build on a Mac
(`npm run open:ios`). The optional Capacitor 8.5 scene lifecycle migration for
Xcode 27 has not been applied to the custom native notification bridge.

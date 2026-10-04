# Cuttle Local Voice – Android companion app

Legacy **TCP voice prototype**, not a supported Cuttle voice integration. The repository no longer ships its PC STT/LLM/TTS backend or the `start_local_bt_voice` / `local_bt_voice_start` entry points. Cuttle does not host an MCP server. No maintained external backend is specified here; using this prototype requires supplying a compatible backend yourself. The optional phone HTTPS listener on :8888 is unrelated and cannot speak this TCP protocol.

## Requirements

- Android 5.0+ (API 21+)
- Same WiFi as the PC (or Bluetooth SPP if you add it later)
- An independently supplied TCP voice backend implementing the framing below
- A trusted network: the prototype TCP protocol provides no TLS or peer authentication

## Permissions

- `RECORD_AUDIO` – mic to PC  
- `MODIFY_AUDIO_SETTINGS` – playback  
- `INTERNET` – TCP to PC  
- `BLUETOOTH` / `BLUETOOTH_CONNECT` – optional for future BT  
- `CALL_PHONE` – optional, only if you want the app to place the call directly (otherwise uses `ACTION_DIAL` so user taps Call)

## Setup

There is no working PC setup recipe in this checkout. If developing a replacement
backend, select an unused TCP port and point the app at it; do not use Cuttle’s
HTTPS :8888 listener as a voice endpoint. Bluetooth SPP remains a future extension.

## Protocol (TCP)

Frames: **1 byte type** + **4 bytes length (big-endian)** + **payload**.

- **Type 0** (phone → PC): mic audio, PCM 16 kHz 16-bit mono.
- **Type 1** (PC → phone): TTS audio, PCM 16 kHz 16-bit mono (or MP3 if PC sends it; app can decode).
- **Type 2** (control): UTF-8 text, e.g. `dial:+15551234` (PC → phone: open dialer / place call).

## Build

**Requirements:** **JDK 17** (the pinned Android Gradle Plugin 8.2.0 requires JDK 17 to run Gradle). Android SDK (installed with Android Studio or command-line tools).

### Option 1: Android Studio (recommended)

1. Open the `apps/android_bt_voice` folder in **Android Studio**.
2. Android Studio uses its bundled JDK 17 and Android SDK. Sync Gradle and build.
3. Run on a device or emulator.

### Option 2: Command line

1. Set **JAVA_HOME** to a JDK 17 installation (e.g. Android Studio’s bundled JDK or a standalone JDK 17).
2. Set **ANDROID_HOME** (or **ANDROID_SDK_ROOT**) to your Android SDK path.
3. From the project root (`apps/android_bt_voice/`):
   - **Windows:** `.\gradlew.bat assembleDebug`
   - **Mac/Linux:** `./gradlew assembleDebug`
4. Debug APK: `app/build/outputs/apk/debug/app-debug.apk`

## Dial

When the PC sends `dial:<number>`, the app uses `Intent.ACTION_DIAL` (opens dialer; user taps Call). With `CALL_PHONE` permission you can switch to `ACTION_CALL` to place the call directly.

The pinned AGP 8.2 JDK requirement is documented in the
[Android release notes](https://developer.android.com/build/releases/agp-8-2-0-release-notes).

# Cuttle Local Voice – Android companion app

Companion app for Cuttle’s **local BT/TCP voice** (no VAPI telephony). Connects to your PC over **WiFi (TCP)** so the PC runs STT/LLM/TTS and this app streams mic audio to the PC and plays TTS back. Optional: PC can send a **dial** command so the phone opens the dialer or places a call (your carrier number).

## Requirements

- Android 5.0+ (API 21+)
- Same WiFi as the PC (or Bluetooth SPP if you add it later)
- PC running Cuttle with local voice server started (e.g. `local_bt_voice_start`)

## Permissions

- `RECORD_AUDIO` – mic to PC  
- `MODIFY_AUDIO_SETTINGS` – playback  
- `INTERNET` – TCP to PC  
- `BLUETOOTH` / `BLUETOOTH_CONNECT` – optional for future BT  
- `CALL_PHONE` – optional, only if you want the app to place the call directly (otherwise uses `ACTION_DIAL` so user taps Call)

## Setup

1. **PC**: Start the local voice server (from Cuttle MCP or script): `start_local_bt_voice(port=8888)`.
2. **Phone**: In the app, set the PC’s IP (e.g. `192.168.1.100`) and port (default `8888`). Find PC IP with `ipconfig` (Windows) or `ifconfig` (Mac/Linux).
3. Tap **Connect**. When connected, speak; the PC will transcribe, run the LLM, and play TTS back.

## Protocol (TCP)

Frames: **1 byte type** + **4 bytes length (big-endian)** + **payload**.

- **Type 0** (phone → PC): mic audio, PCM 16 kHz 16-bit mono.
- **Type 1** (PC → phone): TTS audio, PCM 16 kHz 16-bit mono (or MP3 if PC sends it; app can decode).
- **Type 2** (control): UTF-8 text, e.g. `dial:+15551234` (PC → phone: open dialer / place call).

## Build

**Requirements:** **JDK 11 or higher** (Android Gradle Plugin 8.x needs Java 11+ to run). Android SDK (installed with Android Studio or command-line tools).

### Option 1: Android Studio (recommended)

1. Open the `apps/android_bt_voice` folder in **Android Studio**.
2. Android Studio uses its bundled JDK 11+ and Android SDK. Sync Gradle and build.
3. Run on a device or emulator.

### Option 2: Command line

1. Set **JAVA_HOME** to a JDK 11+ installation (e.g. Android Studio’s bundled JDK or a standalone JDK 11+).
2. Set **ANDROID_HOME** (or **ANDROID_SDK_ROOT**) to your Android SDK path.
3. From the project root (`apps/android_bt_voice/`):
   - **Windows:** `.\gradlew.bat assembleDebug`
   - **Mac/Linux:** `./gradlew assembleDebug`
4. Debug APK: `app/build/outputs/apk/debug/app-debug.apk`

## Dial

When the PC sends `dial:<number>`, the app uses `Intent.ACTION_DIAL` (opens dialer; user taps Call). With `CALL_PHONE` permission you can switch to `ACTION_CALL` to place the call directly.

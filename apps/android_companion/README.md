# Cuttle Companion – Android notifications app

Android companion app for Cuttle on your home Wi‑Fi.

## What it does (v0)

- Connects to Cuttle over LAN via **SSE**: `GET /api/mobile/events`
- Shows Android notifications for:
  - `chat_complete`
  - `interaction` (with **Yes/No** action buttons)
- Posts replies back to Cuttle:
  - `POST /api/mobile/reply`

Your Galaxy Watch will mirror these notifications automatically (and the action buttons work from the watch).

## Cuttle setup

1. Ensure Cuttle is running on your PC (Flask on port 8080).
2. Set a token (recommended) in `src/.env`:
   - `CUTTLE_MOBILE_TOKEN=choose-a-random-string`

## App setup

1. Open `apps/android_companion` in Android Studio.
2. Run the app on your phone.
3. Enter:
   - **Base URL**: e.g. `http://192.168.1.50:8080`
   - **Token**: same as `CUTTLE_MOBILE_TOKEN`
4. Tap **Start**.

## Notes

- This is LAN-first. Remote access (away from home) is best done later via VPN (Tailscale/WireGuard).


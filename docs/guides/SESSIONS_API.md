# Sessions API

One chat session can send a message into another.

## Endpoints

- **GET /api/sessions/list** — session IDs
- **POST /api/sessions/send** — body `{ "target_session": "<id>", "message": "…" }`

> Terse endpoint pointer, not a full contract. `POST /api/sessions/send` is the
> compatibility entry into the shared coordinator (see `AGENTS.md` → "Important
> Conventions"); auth/ownership and response shape follow the normal chat API.
> Do not treat this as a general session-to-session bus.

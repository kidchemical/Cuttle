# Sessions API

One chat session can send a message into another.

## Endpoints

- **GET /api/sessions/list** — session IDs
- **POST /api/sessions/send** — body `{ "target_session": "<id>", "message": "…" }`

# Sessions API

One chat session can send a message into another.

## Endpoints

- **GET /api/sessions/list** — session IDs (and an empty pipelines list)
- **POST /api/sessions/send** — body `{ "target_session": "<id>", "message": "…" }`

`target_pipeline` is not supported.

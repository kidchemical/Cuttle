# Sessions API (Agent-to-Agent)

Cuttle provides a **Sessions API** so one pipeline or session can discover and send messages to another (orchestrator → specialist pipelines).

## Endpoints

- **GET /api/sessions/list** – List active pipelines (running) and session IDs (chat_sessions). Returns `{ pipelines: [ { name, triggers } ], sessions: [ session_id, ... ] }`.
- **POST /api/sessions/send** – Send a message to another pipeline or session. Body:
  - **target_pipeline** (optional) – Pipeline name. That pipeline must be running and have a trigger-webchat (or compatible) trigger. The message is executed as if it came from Web Chat.
  - **target_session** (optional) – Session ID (from chat_sessions). The message is processed in that session.
  - **message** (required) – Text to send.

Exactly one of `target_pipeline` or `target_session` is required.

## Example

1. Run two pipelines: "Orchestrator" (trigger-webchat) and "Specialist" (trigger-webchat).
2. From a tool node or external caller: `POST /api/sessions/send` with `{ "target_pipeline": "Specialist", "message": "Do task X" }`.
3. The Specialist pipeline runs with that message and returns a response; the caller gets `{ success, response, pipeline }`.

This enables orchestrator pipelines that delegate to specialist pipelines by name.

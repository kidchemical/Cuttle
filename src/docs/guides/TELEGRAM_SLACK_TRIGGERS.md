# Telegram and Slack Triggers

Cuttle supports **trigger-telegram** and **trigger-slack** so a pipeline can handle messages from Telegram or Slack via HTTP.

## Trigger nodes

In the Node Editor you can add:

- **trigger-telegram** – Listens for messages when your Telegram bot/webhook POSTs to Cuttle.
- **trigger-slack** – Listens for messages when your Slack app POSTs to Cuttle.

Both expose `message` and `session` outputs like trigger-webchat and trigger-discord.

## API endpoints

- **POST /api/pipeline-trigger-telegram** – Body: `{ "message": "…", "user_context": { "id": "telegram_user_id", … }, "session": { "user_id": "…", "chat_id": "…" } }`.
- **POST /api/pipeline-trigger-slack** – Body: `{ "message": "…", "user_context": { "id": "slack_user_id", … }, "session": { "user_id": "…", "channel_id": "…" } }`.

Pipeline execution uses the same executor: start a pipeline that has a trigger-telegram or trigger-slack node, then POST to the corresponding endpoint from your bot.

## Channel config and pairing

Channel config and pairing (dmPolicy, allowFrom) apply to `telegram` and `slack`:

- **GET/POST /api/settings/channels** – Use `channel: "telegram"` or `channel: "slack"` to set dmPolicy and allowFrom.
- Unknown users get a pairing code when dmPolicy is `"pairing"`; approve via **POST /api/pairing/approve**.

## Wiring your bot

1. Run a Telegram bot (e.g. with python-telegram-bot or webhook) or Slack app (Events API or Slash commands).
2. When you receive a message, POST to `http://localhost:8080/api/pipeline-trigger-telegram` or `.../api/pipeline-trigger-slack` with `message`, `user_context.id`, and `session` (e.g. user_id, chat_id/channel_id).
3. Use the JSON `response` in the reply to the user (Telegram sendMessage, Slack post message).

# Channel-Level Security: Pairing and Allowlist

Cuttle supports **DM pairing** and **allowFrom** for **Web Chat** so you can safely expose the assistant to untrusted users.

**Historical:** inbound Discord DM pairing (`channels.discord`) belonged to the retired Discord gateway. Settings keys may still exist; they do not enable a Discord chat surface. Discord **agent-ops** (read/post) use project `discord-post.yaml` allowlists, not this pairing API.

## Concepts

- **dmPolicy**: `"open"` (everyone allowed if in allowFrom) or `"pairing"` (unknown users must complete pairing).
- **allowFrom**: List of allowed identities. Use `["*"]` to allow everyone when dmPolicy is `"open"`. For Discord, use Discord user IDs; for Web Chat, use `web_user_<id>` or session identifiers.
- **Pairing**: When dmPolicy is `"pairing"` and a user is not in allowFrom, they receive a one-time **pairing code**. An admin approves the code via API or Settings; the user is then added to the allowlist.

## Configuration

Stored in `settings.json` under `channels`:

```json
{
  "channels": {
    "webchat": {
      "dmPolicy": "open",
      "allowFrom": ["*"]
    },
    "discord": {
      "dmPolicy": "pairing",
      "allowFrom": ["123456789012345678"]
    }
  }
}
```

- **webchat**: `identity` is `web_user_<user_id>` for logged-in users, or session id / IP for guests.
- **discord**: `identity` is the Discord user ID (`user_context.id`).

## API

- **POST /api/pairing/approve** – Approve a code. Body: `{ "code": "ABC123" }`.
- **GET /api/pairing/pending** – List pending pairing requests (for admin UI).
- **GET /api/pairing/status?channel=webchat&identity=...** – Check if identity is allowed.
- **GET /api/settings/channels** – Get channel config (dmPolicy, allowFrom).
- **POST /api/settings/channels** – Update channel config. Body: `{ "channel": "discord", "dmPolicy": "pairing", "allowFrom": ["id1", "id2"] }`.

## Flow

1. Set `channels.discord.dmPolicy` to `"pairing"` and `allowFrom` to your Discord user ID(s).
2. When an unknown user DMs the bot, they receive a message like: "Pairing required. Your code is: XYZ789. An admin must approve this code in Settings or via API."
3. You run: `POST /api/pairing/approve` with `{ "code": "XYZ789" }` (or approve in Settings UI).
4. That user is now allowed for future messages.

## Data

- Approved and pending pairings are stored in `src/data/pairing_store.json`.
- Pairing codes expire after 10 minutes if not approved.

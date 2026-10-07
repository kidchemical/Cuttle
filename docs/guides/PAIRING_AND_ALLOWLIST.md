# Web Chat pairing and allowlist

Channel admission adds a restriction to authenticated `POST /api/chat`
requests. It runs before native controls, project shell commands, attachment
analysis, routing, and harness execution, including streaming requests.
Authentication and chat-session ownership checks still apply. Pairing is not
an isolation boundary for agents or a substitute for restricting network access,
owner privileges, action execution, or other API routes.

Discord is REST agent-ops only; the retired inbound Discord DM pairing flow
is not supported. Worker enrollment uses a separate identity/token system.

## Configuration

An owner can update channel settings in Settings or through authenticated
`POST /api/settings/channels`:

```json
{"channel":"webchat","dmPolicy":"pairing","allowFrom":["web_user_1"]}
```

These values are stored under `channels.webchat` in `<home>/config/settings.json`.
Identities are `web_user_<authenticated user id>`, including authenticated
guest accounts. Client-provided chat/session ids do not identify the sender.

- `dmPolicy: "open"` and `allowFrom: ["*"]` admit every authenticated user.
- An explicit empty `allowFrom: []` admits no unapproved identities.
- `dmPolicy: "pairing"` requires unknown users to obtain owner approval;
  wildcard entries do not open this policy.
- Specific allowlist identities and previously approved pairings are admitted
  under either policy. A pairing-store/configuration error returns HTTP 503.

## Flow and API

1. The owner enables pairing and includes their own `web_user_<id>` identity.
2. An authenticated unknown user sends a chat message. HTTP 403 returns
   `error: "pairing_required"` and a `pairing_code`; no agent work starts.
3. The owner reads `GET /api/pairing/pending` and approves the code with
   `POST /api/pairing/approve`, body `{"code":"ABC123"}`.
4. The user resends their message; approval permits normal chat dispatch.

All pairing endpoints require an owner session cookie or owner session bearer
token, including `GET /api/pairing/status?channel=webchat&identity=web_user_2`.
Channel settings GET/POST also require an owner. In multi-user deployments,
configure `OWNER_USER_EMAIL`; without it, the oldest active non-guest account
is the owner and later accounts are not. Pairing alone does not change that
role policy.

Open-policy allowlist rejection returns `error: "allowlist_denied"` without a
pairing code. Approved/pending identities live in `<home>/pairing_store.json`;
unapproved codes expire after ten minutes. Approval is stored separately from
the settings allowlist, so removing an allowlist entry alone does not revoke an
existing approval.

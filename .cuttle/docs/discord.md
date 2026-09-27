# Discord (Cuttle hub — all projects)

Native runbook for **reading** and **posting** via the Discord bot configured for
this Cuttle install. Every registered project shares this doc; per-project guild /
channel IDs live in that project's `.cuttle/actions/discord-post.yaml` (and optional
project `.cuttle/docs/discord.md`).

**Do not** reverse-engineer Cuttle's Discord stack or hunt for tokens when this file exists.
Install-local path notes may live in `.cuttle/personal/docs/discord.md`.

## When the user mentions Discord

1. Read **this file** (you are here). Prefer the personal overlay if present.
2. Resolve the **active project** path (chat chip / `project_path` / workspace).
3. Open `{project}/.cuttle/actions/discord-post.yaml` for guild + channel aliases.
4. Optional: `{project}/.cuttle/docs/discord.md` for project-specific notes.

## Auth (read + post)

Bot token — **do not commit**:

1. `{CuttleInstall}/src/.env` → `DISCORD_TOKEN=` or `DISCORD_BOT_TOKEN=`
2. Optional fallback file under a local secrets dir (never commit)

## Read channel history (agent ops CLI)

Prefer the CLI over inventing `curl` / `python -c` Discord REST:

```powershell
cd <CuttleInstall>
.venv\Scripts\python.exe -m api.discord_cli aliases --project "<project>" --json
.venv\Scripts\python.exe -m api.discord_cli channels --project "<project>" --json
.venv\Scripts\python.exe -m api.discord_cli messages feature-updates --project "<project>" --limit 20 --json
```

- `--project` loads aliases + `guild_id` from that project's `discord-post.yaml`.
- `messages` accepts an **alias** or a channel snowflake.
- This CLI is **read-only**. Outbound posts stay on confirm forms (below).

Summarize into `{project}/.cuttle/docs/` only when the user wants persistent mirrored context.

## Post to Discord (preferred)

**Do not** call Discord REST for outbound posts unless confirm UI is broken.

1. Draft the message (project runbook may specify title emoji, bullets, etc.).
2. End with `<cuttle_confirm action="discord.post" channel="<alias>">` or `<cuttle_action_form>` with `"action": "discord.post"`.
3. Human clicks Post → Flask runs the allowlisted `{project}` action — **no LLM**, no second agent turn.

Channel aliases must match `{project}/.cuttle/actions/discord-post.yaml`.

## What Cuttle does *not* inject automatically

| Mechanism | Role |
|---|---|
| **Context Compiler** | Lists doc names in inventory; agents open this runbook when Discord is mentioned |
| **Per-project templates** | Not required — this hub doc + project's `discord-post.yaml` are enough |

## Notes

- Retries help on Windows (`WinError 10054` TLS resets).
- Never force-kill the Discord bot from an agent hosted by Flask — use `/restart` commands.
- Message links: `https://discord.com/channels/<guild_id>/<channel_id>/<message_id>`

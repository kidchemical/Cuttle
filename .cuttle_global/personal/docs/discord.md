# Discord — install-local delta

Tracked runbook is canonical (CLI-first). This file adds only what is specific
to this install.

## Auth (do not commit)

JamBit OS bot token:

1. `/home/kidchemical/Desktop/Cuttle/src/.env` → `DISCORD_TOKEN=` or `DISCORD_BOT_TOKEN=`
2. Fallback: `/home/kidchemical/Desktop/Cuttle/src/.secret_DONOTSHIP/discord bot token.txt`

Run snippets from the Cuttle tree with its venv (`.venv\Scripts\python.exe`).

## Channel-alias → project mapping

Cuttle resolves `discord.post` across registered projects by channel alias:

- `general` / `brainstorm` → Epochs
- `feature-updates` / `prompt-lab` → Escape Purgatory

Always use the alias from the target project's `discord-post.yaml`.

## Reference projects

| Project | Notes doc |
|---|---|
| Escape Purgatory | `/path/to/Escape-Purgatory\.cuttle\docs\discord.md` |
| Epochs | `E:\Game Dev\Epochs\.cuttle\docs\discord.md` |

## Notes

- Retries help on Windows (`WinError 10054` TLS resets).

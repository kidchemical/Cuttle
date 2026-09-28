# Discord cleanup and extension boundaries (2026-09-27)

Working tree cleanup of the inbound Discord **gateway**. This file is the stream 1c deliverable. It does not replace the earlier repository audit; it records what was removed vs kept.

Architecture: [`../architecture/extension-boundaries.md`](../architecture/extension-boundaries.md).

---

## 1. Removed gateway functionality and exclusive dependencies

| Item | Role (historical) |
|---|---|
| `src/bots/discord_bot.py` | discord.py `Client` gateway: DMs/mentions → Flask |
| `src/bots/__init__.py` | Package for that module |
| `src/api/discord_chat_bridge.py` | Discord-shaped chat session bridge |
| `POST /api/pipeline-trigger-discord` | Discord-only execution ingress |
| `GET /api/bot-status` | Gateway LED consumed by no remaining caller |
| Daemon `start_discord_bot` / respawn / tray Restart Bot | Default child process when token set |
| Web `/invite` palette + invite handler | Bind Discord identity to a Cuttle chat |
| `discord.py` in `src/requirements/requirements.txt` | Exclusive to the gateway Client |
| Tests: `test_discord_chat_bridge.py`, `unit/test_discord_integration.py` | Gateway/bridge coverage |
| Launcher / debug launcher gateway start | Spawned bot scripts |

**Not removed (not exclusive to the gateway):** `src/bot.py` (`OWNER_ID` / `is_owner` for unit tests), `bot_config.json` / `get_config()`, pairing HTTP for **webchat**, process-control **410** cluster (`/api/start-discord` still 410 like start-webapi), SQLite `discord_*` columns (schema), `landing_page_backup.html` (unrouted backup).

---

## 2. Retained Discord agent operations

| Contract | Implementation |
|---|---|
| `python -m api.discord_cli` `{aliases,channels,messages}` | `src/api/discord_cli/` — REST GET |
| Action id `discord.post` (also `discord_post`, `discord`) | `src/api/discord_ops/post.py` via `project_actions` wrappers |
| Token load `DISCORD_TOKEN` / `DISCORD_BOT_TOKEN` | `src/api/discord_ops/token.py` — **does not start a gateway** |
| Project allowlist | `{project}/.cuttle/actions/discord-post.yaml` |
| Settings UI token test | `GET https://discord.com/api/v10/users/@me` (REST) |

**Post authorization:** aliases, or a snowflake **only if it is a value** in `channels`. Arbitrary numeric IDs are rejected (`resolve_post_channel_id`).

**Read CLI:** with `--project`, same allowlist. Without `--project`, a raw snowflake is still allowed (read-only; bot token permissions apply).

---

## 3. Core dependency analysis (source tree)

| Layer | Depends on gateway? |
|---|---|
| `cuttle_daemon.py` | **No.** Spawns Flask (and optional llama.cpp). No `discord_bot.py` / `start_discord_bot`. |
| `web_chat_api.py` chat path | **No.** `POST /api/chat` only. No pipeline-trigger-discord route on in-process `app.url_map`. |
| Agent harness / router | **No.** |
| `discord_ops` / `discord_cli` | REST `requests` only. Optional token. |
| `project_actions` | Thin wrappers; public action ids unchanged. |

`DISCORD_TOKEN` in `src/.env` is loaded by the daemon for child env and by REST helpers. It is **not** a spawn condition in current `cuttle_daemon.py`.

---

## 4. Extension boundaries

Documented in [`../architecture/extension-boundaries.md`](../architecture/extension-boundaries.md): **A** harness adapters, **B** optional surface adapters, **C** agent-ops. Discord read/post is **C**. A future Discord/Slack/Telegram **chat** surface is **B** and must use a shared authenticated ingress, not a new `pipeline-trigger-*`.

---

## 5. Deferred (surface-adapter framework)

- Authenticated common ingress (identity, session, project, attachments, agent/model, execution, progress, approvals).
- Independently enabled long-lived gateways (Discord/Slack/Telegram) as **optional** processes, not daemon defaults.
- Drop or migrate SQLite `discord_*` pairing columns if unused after a soak.
- Relocate remaining telegram/slack **settings channel keys** (workstream 1b).
- Delete unrouted `landing_page_backup.html` (cleanup-plan 3.2).
- Move `src/bot.py` tests off the `bot` shim when convenient (`test_security.py` still imports it).
- Do **not** implement the full adapter framework in this stream.

---

## 6. Test commands and results

From repo root:

```bash
.venv/bin/python -m pytest \
  src/tests/test_discord_ops.py \
  src/tests/test_discord_cli.py \
  src/tests/test_discord_gateway_retired.py \
  src/tests/test_project_actions.py \
  src/tests/test_starred_slash.py \
  src/tests/test_restart_native_command.py \
  src/tests/integration/test_discord_remote_execution.py \
  src/tests/integration/test_discord_api_e2e.py \
  src/tests/test_action_forms.py \
  -q
```

**Result (this machine, 2026-09-27):** `108 passed, 2 skipped` in 1.41s.

Skips: live `/api/health` e2e ran; `test_pipeline_trigger_discord_gone` **skipped** because the **running** Flask still reports `discord_connected` as `null` (pre-cleanup process). In-process `app.url_map` has **no** `pipeline-trigger-discord` (`test_discord_gateway_retired`).

Also verified:

- `python -c` imports of `api.discord_ops` and `api.discord_cli` succeed without `discord.py`.
- `.venv/bin/python -m api.discord_cli --help` lists aliases/channels/messages.
- `cuttle_daemon.py` source has no `discord_bot.py` spawn.
- Token is configured on this host (`load_discord_bot_token()` true) **and** that does not appear in current daemon source as a start trigger.

**Not executed:** full `src/tests/` suite; live `discord.post` HTTP to Discord; Flask/daemon restart (not done from this agent).

---

## 7. Uncertainties / compatibility

- **Live processes lag the tree.** PID **78219** `cuttle_daemon.py` (started before this cleanup) still has child **78226** `/…/src/bots/discord_bot.py` even though the file is **deleted** from the tree. Current daemon **source** will not spawn it. Recycle the **daemon** (not only Flask) to drop that child. Do not `taskkill` it from an agent session.
- Live Flask still answers `POST /api/pipeline-trigger-discord` with **200** until Flask is restarted onto this tree.
- `discord_cli messages` without `--project` still accepts any snowflake the bot token can read (read-only). Posts cannot.
- `channels.discord` pairing keys remain in settings HTTP; they do not start a gateway.
- Inventory file `docs/reviews/repository-inventory.md` still lists `discord_bot.py` until regenerated (not overwritten here).
- `coverage-ledger.md` / `repository-audit.md` were already dirty from the prior audit pass; this stream only patched the gateway bullets, not a full re-audit.

---

## 8. Git (this working tree)

`git diff --stat` (tracked): **49 files**, **+390 / −3039** (includes earlier audit doc edits already in the tree).

Untracked from this stream: `docs/architecture/extension-boundaries.md`, `src/api/discord_ops/`, `src/tests/test_discord_ops.py`, `src/tests/test_discord_gateway_retired.py`, this file. `docs/reviews/graph-discord-consumers.md` was also untracked (audit) and gained a 1c-complete banner.

No commit from this task.

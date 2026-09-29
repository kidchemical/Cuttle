# Chat history — install-local delta

Tracked runbook is canonical (always prefer `python -m api.chat_cli` over raw SQL).
This file adds only what is specific to this install.

- Chat DB (verified on disk): `/home/kidchemical/Desktop/Cuttle/src/data/db/cuttle_auth.db`
  (if missing, resolve via `api.auth_db.DB_PATH` — never guess a second path).

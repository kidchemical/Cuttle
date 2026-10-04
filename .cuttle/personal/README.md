# `.cuttle/personal/` — install-local project overlay (Cuttle repo)

Gitignored twin of this repo's tracked project `.cuttle/` (not the global tree — global
overrides live in `.cuttle_global/personal/`). Rule/doc Markdown here appends a personal delta after tracked text;
commands/actions replace by normalized declared name and skills by directory id.
Script recipes use explicit literal paths. Put LAN hosts, absolute paths, and machine notes here — not
in tracked docs. See the global overlay contract at `.cuttle_global/personal/README.md`.

`secrets/` holds this install's file-shaped secrets (TLS cert/key, GitHub App key,
token files); resolve it via `core.runtime_paths.secrets_dir()`. Environment-variable
secrets stay in `src/.env`.

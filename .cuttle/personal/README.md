# `.cuttle/personal/` — install-local project overlay (Cuttle repo)

Gitignored twin of this repo's tracked project `.cuttle/`. Rule/doc Markdown here
appends a personal delta after tracked text; commands/actions replace by normalized
declared name and skills by directory id. Script recipes use explicit literal paths.
Put LAN hosts, absolute paths, and machine notes here — not in tracked docs.

This is the *project* overlay only. The shared `.cuttle_global/` overlay, secrets,
`.env`, and all runtime state live in the per-user Cuttle home
(`core.runtime_paths.cuttle_home()`); see `docs/architecture/cuttle-home.md`.

## Not allowed here (recurrence guard)

Do **not** put caches, build toolchains, DB/JSON backups, or any runtime/session
state in this overlay (or in `.cuttle_global/`). Those belong in the Cuttle home
(`<home>/` — e.g. `cache/`, `db/`, `output/`). The install tree must stay
write-free for a release install. Quarantined 2026-10-10: `backups/`,
`toolchains/`.

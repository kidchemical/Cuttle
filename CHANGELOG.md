# Changelog

User-visible changes to Cuttle. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/) (the release version lives in
`electron/package.json`). Each release also gets notes on its
[GitHub Release](https://github.com/kidchemical/Cuttle/releases) page.

## [Unreleased]

### Added
- Configurable listener ports: set `CUTTLE_HTTPS_PORT`, `CUTTLE_HTTP_PORT` and
  `CUTTLE_PHONE_HTTPS_PORT` in `src/.env` (defaults 8080/8000/8888; needs a
  full daemon restart). Desktop and mobile clients keep the port you enter.
- Agent router turn classification: it labels the kind of work and routes
  around accounts that are out of quota.
- Projects app with a project registry and ordered host locations.
- Headless Linux hosts run as a systemd user service.
- Shared skills appear in the compiled context inventory without needing
  ranked context.
- `git.push` can push a single release tag.
- Continuous integration on Linux and Windows, with browser and Android build
  jobs.
- `SECURITY.md`, `CONTRIBUTING.md`, this changelog, and issue and PR templates.

### Changed
- The Android app now enforces TLS certificate validation and no longer falls
  back from HTTPS to HTTP silently. Self-signed HTTPS needs its CA installed on
  the phone; plain HTTP on the LAN still works.
- Android builds use the project's own `.venv` and document their real
  prerequisites (Node 20+, JDK 21, Android SDK 35).
- Queued local-LLM (Ollama) requests honor Stop and give up after
  `LOCAL_LLM_QUEUE_TIMEOUT_SEC` (default 120 s) instead of waiting forever.

### Fixed
- POSIX launchers and helper scripts are executable in fresh clones, and
  Windows `.cmd` scripts check out with CRLF.
- Agent handoff reports when long messages were truncated and points to the
  full transcript; all project rules now load, not just the first 24.
- When the project briefing or handoff transcript cannot be built, the agent
  is told what is missing instead of silently getting a bare prompt.
- The Electron Client daemon's status file can no longer be left corrupted
  by concurrent writes.
- Android LAN updates: published APKs are verified, old ones are pruned, and a
  refused publication no longer fails a debug build.
- Shadow dev instances delete their checkout snapshot when they stop, and
  `python -m api.dev_instance prune` clears stale ones.

### Removed
- The legacy runtime config (`bot_config.json`, later `runtime_config.json`)
  and its `GET/POST /api/settings` route. Only the local Ollama model setting
  was still used; it now comes from `OLLAMA_MODEL` or Settings (completion
  models → Local). Leftover files from old installs are ignored.
- The unused repository-root `bot_config.json` and the orphaned
  `vendor/claw-code` gitlink.

Earlier changes predate this changelog; see the git history.

[Unreleased]: https://github.com/kidchemical/Cuttle/commits/main

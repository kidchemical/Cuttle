# `.cuttle_global/personal/` — install-local global overlay

Gitignored twin of tracked `.cuttle_global/`. Same subdirs:

```text
.cuttle_global/personal/
  commands/   rules/   actions/   docs/   scripts/   skills/
```

**Resolution:** rule/doc Markdown twins append a marked personal delta after
tracked text. Personal-only docs are readable too. Commands/actions replace the
whole unit by normalized declared name; skills replace by directory id. The
winning source is returned by list/get. Use explicit boolean `disabled: true`
to suppress lower-priority command/action/skill copies. Script recipes resolve
literal paths: they must explicitly point to a personal script to execute it.
Guest projects have their own twin at `{project}/.cuttle/personal/`.

## What belongs here

- Absolute install paths (`C:\Users\…`, LAN IPs, hostnames)
- **`path-aliases.json`** — Windows→this-checkout prefixes and sibling-project lookup hints:

```json
{
  "windows_cuttle_prefixes": ["C:/OldCheckout/Cuttle"],
  "path_mappings": {"E:/Projects": ["~/Projects", "/mnt/data/Projects"]},
  "sibling_project_paths": ["E:/Projects/DemoGame"],
  "action_prefer_substrings_no_channel": ["demogame"],
  "action_skip_substrings_unmatched_channel": ["demogame"]
}
```

  The public rewriter already maps any path **segment** named like this repo (`Cuttle`) onto the checkout. Prefixes are only needed when the Windows path does *not* contain that folder name. `path_mappings` maps other Windows folder prefixes onto candidate local roots (first root where the path exists wins). `sibling_project_paths` is the install-local list used when looking up Discord project actions outside the registered project list. `action_prefer_substrings_no_channel` / `action_skip_substrings_unmatched_channel` are optional path-substring hints for the same lookup; omit them on a fresh clone.
- Your Discord guild / workspace examples
- Mesh SSH / worker host notes for *this* LAN
- Dated strategy dumps (`docs/roadmap-2026.md`) that must not ship on GitHub
- Anything that would break a fresh clone for someone else

## What does **not** belong here

- Code Cuttle needs to boot (keep that tracked)
- Secrets (env keys in `src/.env`; file-shaped secrets in `.cuttle/personal/secrets/`)
- Optional workflows used across projects belong in this global personal tree;
  workflows for one project belong in that project's `.cuttle/personal/`.

## Fresh install

This folder is empty except this README in the public tree. After clone, add
overrides as needed — scaffold may create the subdirs; it never overwrites your files.

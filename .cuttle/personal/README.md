# `.cuttle/personal/` — install-local overlay

Gitignored twin of tracked `.cuttle/`. Same subdirs:

```text
.cuttle/personal/
  commands/   rules/   actions/   docs/   scripts/
```

**Rule:** when the same relative path exists here *and* under tracked `.cuttle/`,
**this file wins** (Context Compiler rules/docs inventory, action YAML lookup).

## What belongs here

- Absolute install paths (`C:\Users\…`, LAN IPs, hostnames)
- **`path-aliases.json`** — Windows→this-checkout prefixes and leftover pipeline `project:` keys:

```json
{
  "windows_cuttle_prefixes": ["C:/OldCheckout/Cuttle"],
  "game_dev_windows_prefix": "E:/Projects",
  "sibling_project_paths": ["E:/Projects/DemoGame"],
  "action_prefer_substrings_no_channel": ["demogame"],
  "action_skip_substrings_unmatched_channel": ["demogame"],
  "remote_agent_projects": {
    "demo_game": "E:/Projects/DemoGame"
  }
}
```

  The public rewriter already maps any path **segment** named like this repo (`Cuttle`) onto the checkout. Prefixes are only needed when the Windows path does *not* contain that folder name. `sibling_project_paths` is the install-local list used when looking up Discord/Gitea project actions outside the registered project list. `action_prefer_substrings_no_channel` / `action_skip_substrings_unmatched_channel` are optional path-substring hints for the same lookup; omit them on a fresh clone.
- Your Discord guild / Gitea workspace examples
- Mesh SSH / worker host notes for *this* LAN
- Dated strategy dumps (`docs/roadmap-2026.md`) that must not ship on GitHub
- Anything that would break a fresh clone for someone else

## What does **not** belong here

- Code Cuttle needs to boot (keep that tracked)
- Secrets (use `src/.env` / `.secret_DONOTSHIP/`)
- Non-Cuttle dogfood (Instacart skills, etc.) → repo-root `_personal/`

## Fresh install

This folder is empty except this README in the public tree. After clone, add
overrides as needed — scaffold may create the subdirs; it never overwrites your files.

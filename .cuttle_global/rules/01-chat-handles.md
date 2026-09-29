# Cuttle chat handles (agent-agnostic)

When the user pastes a `CH-…` token, treat it as a Cuttle web-chat pointer — not a
repo path, Discord id, or mystery code.

| Token | Meaning |
|---|---|
| `CH-000182` | Chat session → numeric id `182` |
| `CH-000182-23` | Same session `182`, 1-based bubble `23` among **user+assistant only** (system / Stop notices do not count) |

Lookup: `.cuttle_global/docs/chat-history.md`. Prefer
`python -m api.chat_cli get CH-…-N --json` (not hand-rolled SQL).

## Citing handles in chat replies

Emit the **bare token** as plain text (e.g. `CH-000405` or `CH-000405-12`).
Cuttle linkifies it into a clickable chat-handle badge that opens that session.

**Do not** wrap handles in markdown links — especially not file/editor URLs:

| Bad | Why |
|---|---|
| `[CH-000405](file:////path/to/Cuttle)` | Becomes a file chip → reveal folder, not open chat |
| `[CH-000405](vscode://file/…)` / `[CH-000405](https://…)` | Same — wrong chip / dead navigation |
| `` `CH-000405` `` | Inline code skips linkify |

`file://` / `vscode://` hyperlinks are only for **real file paths** (see AGENTS.md
terminal hyperlink rule), never for `CH-` tokens.
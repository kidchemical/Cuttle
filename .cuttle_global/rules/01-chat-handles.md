# Cuttle chat handles (agent-agnostic)

A `CH-…` token is a Cuttle web-chat pointer, not a repo path, Discord id or mystery
code: `CH-000182` is chat session 182, `CH-000182-23` its 23rd bubble (user and
assistant bubbles only). Look it up with `python -m api.chat_cli get CH-…-N --json`
(`chat-history.md`), not hand-rolled SQL.

In replies write the bare token as plain text (CH-000405-12): Cuttle turns it into
a clickable badge. Never wrap a handle in a markdown link, a `file://` / `vscode://`
URL or backticks: each of those breaks the badge.

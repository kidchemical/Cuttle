# Sub-agents (child chats)

Fan-out, voting, mixed harnesses, or a task split across agents → spawn **real
child chats** with `python -m api.subagents`, not background CLI subagents. Supervised
children keep their owned `--wait` path. How-to: `.cuttle_global/docs/subagents.md`.

## When to spawn (do this without being asked)

Use sub-agents when **two or more independent agent turns** would clearly beat
one sequential reply, for example:

- Several options / votes / critiques of the same prompt (dinner, design, review)
- Parallel investigation (logs vs code vs docs) that you will synthesize
- A job that splits cleanly across harnesses (one Cursor Auto, one Grok, one Codex)
- Multi-step work you want visible as separate chats the user can open

Do **not** spawn for a single straightforward question, a tiny edit, or work you
can finish this turn yourself.

## How

```text
python -m api.subagents spawn --parent CH-…… --wait --json \
  --collect all \
  --child "{\"title\":\"A\",\"agent\":\"cursor\",\"message\":\"…\"}" \
  --child "{\"title\":\"B\",\"agent\":\"cursor\",\"model\":\"grok-4.6\",\"effort\":\"low\",\"message\":\"…\"}"
```

`profile` on a child (`"scout"` or `{"name":"Judge","avatar":"⚖️","agent":"codex"}`)
sets the speaker name/avatar and optional harness. `python -m api.subagents profiles list`.

Then synthesize **one** parent reply. Include the child `CH-` handles as bare
tokens (not markdown links). Honor the user's collect/lifetime/harness asks when
they name them; otherwise pick:

| User intent | Flags |
|---|---|
| Vote / gather every opinion | `--collect all` (default), `--lifetime one_shot`, `--wait` |
| First good answer is enough | `--collect first` |
| One after another (shared context / rate limits) | `--collect serial` |
| Keep chatting with the children | `--lifetime conversational` (then `message` / `close`) |
| Let the router pick harnesses | `--route` |
| Long split job | `--watch` (progress bars) and/or `--tasks` (parent Tasks widget) |

Granular todos belong in **each child chat's** Tasks widget; parent Tasks stay
high-level. Configs + verbs: `subagents.md`.

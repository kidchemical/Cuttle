# Sub-agents (child chats)

Fan-out, voting, mixed harnesses, or a task split across agents → spawn **real
child chats** with `python -m api.subagents`. That CLI is the only durable
subagent path: never a subagent facility built into your own CLI — whatever it
runs lives inside this turn and dies with it. Supervised children keep their
owned `--wait` path.

Spawn without being asked when **two or more independent agent turns** would
clearly beat one sequential reply (several opinions or votes, parallel
investigation you will synthesize, a job that splits across harnesses). Not for
a single question, a tiny edit, or work you can finish this turn yourself.

Before spawning, open `.cuttle_global/docs/subagents.md` (spawn command,
`--collect` / `--lifetime` / `--route` / `--watch` choices, profiles). Then
synthesize **one** parent reply naming the child `CH-` handles as bare tokens.
Granular todos belong in each child chat's Tasks list; parent Tasks stay
high-level.

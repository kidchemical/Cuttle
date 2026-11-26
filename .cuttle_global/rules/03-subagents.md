# Sub-agents (child chats)

Fan-out, voting, mixed harnesses, or a task split across agents → spawn **real
child chats** with `python -m api.subagents`. Never a subagent facility built into
your own CLI: whatever it runs dies with this turn. Supervised children keep their
owned `--wait` path.

Spawn without being asked when **two or more independent agent turns** would
clearly beat one sequential reply; not for a single question, a tiny edit, or work
you can finish this turn yourself.

Open `.cuttle_global/docs/subagents.md` before spawning (spawn command, `--collect` /
`--lifetime` / `--route` / `--watch` choices, profiles). Then give **one** parent
reply naming the child `CH-` handles as bare tokens; granular todos belong in each
child chat's Tasks list.

# Agent ops CLIs

Cuttle-owned data or platform verbs (chat transcripts, another chat, panes, workers,
worktrees, dashboards, Discord reads, Jev, context compile) → `python -m api.<module>
<verb>`, never hand-rolled SQL, ad-hoc curl, minted tokens or throwaway scripts. The
verb table is in `agent-ops-cli.md`: open it instead of guessing a module.

- Multi-step agent work → create or reuse a Tasks gizmo early and patch it as work
  progresses (`gizmos.md`); no Tasks markdown tags. Skip for quick questions or small edits.
- Before reporting work tracked in a Tasks gizmo complete, patch finished items and
  read back the list; leave unfinished items open with a reason.
- Long-running OS jobs → a status-backed watch card with measured progress
  (`action-forms.md` / `headless-turns.md`). A Tasks list does not replace it.
- Worktrees → `python -m api.worktrees`, never raw `git worktree add`.
- Sub-agent child chats → `python -m api.subagents` (`03-subagents.md`).

Agent-facing features ship as library + `python -m` verb + runbook entry. Do **not**
add a Cuttle-hosted MCP tool server; guest harnesses own MCP.

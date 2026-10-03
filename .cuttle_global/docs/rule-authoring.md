# Authoring rules (on-demand)

Read this before writing or changing any rule file under `.cuttle_global/rules/`,
`{project}/.cuttle/rules/`, or a `personal/` overlay. Rule files compile into the
fresh/full context and ride change-deltas on resume — keep them small and load-bearing.

## Scope: global vs project vs personal

- **Global** (`.cuttle_global/rules/`): true for every project and every harness.
  Agent-agnostic, portable paths only (`{project}/…`, never a real checkout path,
  never Cuttle-architecture/worktree/shadow detail — that stays project-local).
- **Project** (`{project}/.cuttle/rules/`): true only for chats targeting that project.
  Never duplicate a global rule; point at it when overlap tempts you.
- **Personal** (`personal/` beside either tree, install-local preferences): your
  deltas, which may contain local paths. Personal *markdown* appends after the
  tracked file; other personal files replace by basename. Open the tracked file
  first, then the personal delta. Keep `personal/` gitignored and secrets out of
  it — tracked public guidance must stay portable.

## Invariants vs procedures

- **Rules hold hard invariants and intent→doc pointers** ("never X", "X → runbook.md").
  A rule answers: what must never happen, and which doc owns the how.
- **Procedures, examples, shapes, and recovery steps belong in the matching runbook**
  under `.cuttle_global/docs/` (or the project's docs tree). If no runbook matches,
  say so in review instead of bloating the rule.
- Never replace a rule with a vague good-practice slogan ("be careful", "handle
  errors gracefully"). If it cannot name the forbidden act or the owning doc, it is
  not a rule — put it in the runbook or drop it.

## One owner, no duplication

- Each behavior has exactly one owning file. New guidance goes in the existing owner;
  a new file needs a distinct trigger no current file covers.
- Only `00-safety.md` is mechanically non-severable (it survives explicit GLOBAL.ini
  shadow/off policy): host-process safety AND cross-project write confirmation
  both live there. Other always-on controls — child-supervision, no-shell-push,
  restart controls — are important but CAN be disabled by explicit policy, so never
  weaken them for size: shrink around them, not through them, and say so when
  policy would drop them.

## Triggers: narrow, positive and negative

- State the positive trigger ("when the user pastes a `CH-…` token…") AND the
  negative boundary ("…not a repo path or Discord id").
- Avoid universal triggers ("always validate everything", "for any file operation"):
  they fire on irrelevant prompts and teach the ranker nothing. Prefer the smallest
  intent phrase that still catches the real cases.

## Budgets and tests

- Always-on text is paid on the fresh compile and can recur on deltas: the Brain
  sends full context once and resume deltas afterward, so rules are not necessarily
  paid verbatim on every turn — but every byte still costs the turns that carry it.
  Aim for pointer-density, not coverage. Measure before/after always-on characters
  on a fresh `hello` envelope when trimming.
- Test every rule change against relevant AND irrelevant prompts (a coding task, a
  plain question, a guest project, a `personal/` overlay and a `GLOBAL.ini`
  shadow/off case) with optional judging disabled, and confirm the safety rules
  still survive shadowing. Record what you measured; report uncertain instructions
  instead of silently dropping them.

## Portable examples (public context)

Good (portable, narrow trigger, names the owner):

- "When the user pastes a `CH-…` token, treat it as a web-chat pointer — not a
  repo path. Lookup: `python -m api.chat_cli get CH-…-N --json` (`chat-history.md`)."
- "Never `git push` from the agent shell — emit the `git.push` action form
  (`git.md`)."

Bad (leaks one install, fires everywhere, or owns nothing):

- "Before touching `C:/Projects/Cuttle/src/api/...`, check the worktree diff…"
  (real checkout path — use `{project}/…`, and worktree/shadow detail stays
  project-local, never global).
- "Always validate everything thoroughly." (universal trigger, no owner, no
  forbidden act.)
- A rule that pastes the full action-form JSON schema instead of pointing at
  `action-forms.md` (procedure belongs in the runbook).

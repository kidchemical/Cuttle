# Claude native delegation: November 10, 2026

## Finding

Cuttle supplied the rule prohibiting native subagents. Claude nevertheless
used its available native `Agent` tool. The confirmed Cuttle defect was an
unenforced host boundary: rules prohibited native delegation, but the launcher
only disabled `AskUserQuestion`. Native delegation remained in Claude's tool
pool on fresh and resumed runs, under `bypassPermissions`.

Brain's `resume_delta` describes changed context, not a replay of all rules.
It cannot guarantee that an older instruction remains influential. However,
missing rule injection or loss at the last compaction does not explain either
observed call: both have full rule-bearing briefings on their actual parent
chain after the last compact boundary.

This establishes the delivery and enforcement failure. It does not establish
why Opus changed its tool choice recently, nor a particular vendor release as
the trigger. Long-chat instruction dilution and repetition of earlier native
delegation are plausible contributors, not demonstrated causes.

## Evidence

Both native calls ran with Claude CLI `2.1.321`. Following the transcript’s
`parentUuid` chain showed two full briefings carrying the delegation rule
between each call and its preceding compaction. On the later turn, the
resume delta contained only a scheduling change. The handoff included the
earlier delegation, while the launcher still offered the native tool.

The Brain step is a summary. `python -m api.agent_events get <query-id> --json`
exposes the recorded Brain metadata, sent text and first event page. The `sent`
event retains the full assembled prompt; the run metadata's `sent.text` preview
can be capped for larger prompts. Page events to inspect the full `sent` payload.

## Correction

`scripts.utilities.claude_cli_tool.ClaudeCliTool.execute_prompt` now always
passes `--disallowedTools AskUserQuestion,Agent,Task,TeamCreate`. `Task` covers
the old delegation tool name; the separate `TaskStop` and `TaskOutput` controls
are preserved. This uses Claude's native tool filtering, independent of
conversation length or a Brain receipt. Cuttle child chats still launch via
`python -m api.subagents`, whose child Claude processes use this same launcher.

Each launch records `harness_policy` with a policy version, delegation owner,
deny list and resume flag. The shared launch path covers ordinary, steerable,
detached and subscription/API-fallback runs. It preserves background shell
work and the existing picker-to-action-form recovery.

No general change to Brain delta semantics is justified by these two traces.
This is tool-boundary enforcement, not an OS sandbox against an agent
deliberately launching another unrestricted CLI through its shell.

## Validation and activation

Regression tests exercise fresh/resumed sessions, argv/stdin prompts and
steerable/non-steerable launches under `bypassPermissions`, and verify that
the launch policy is recorded. Brain delivery, compaction, Claude background
waits, API fallback and native question recovery suites cover adjacent behavior.
The targeted suites passed: 242 tests total.

An explicit installed-CLI smoke against a loopback fake Anthropic endpoint
(synthetic prompt, isolated CLI configuration, disabled hooks/MCP, no vendor
model request) reproduced `Agent` in the baseline model tool list. With the
new deny list, `Agent` was absent while `Bash` remained. Both requests finished
successfully. The ignored scratch reproduction is not part of the repository.

Load the Python change through the daemon-owned Flask restart controller.
New Claude processes then receive the policy, including resumes of existing
chats. A live process retains its old tool pool until it exits. Do not terminate
an active user's work merely to retrofit this policy, and do not clear their
chat history or native resume state.

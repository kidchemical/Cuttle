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

Source: native Claude session
`e0d7b243-3a42-409b-9ffa-b4c791b01a1d`, chat CH-001822. All times below
are November 10, PST. Both native calls report CLI `2.1.321`, model
`claude-opus-5-5`.

| Event | First native call | Later call, query `59f439fc` |
| --- | --- | --- |
| Last compaction on parent chain | 01:59:48 | 04:34:19 |
| Full rule-bearing briefings after compact | 02:00:18, 03:29:02 | 04:59:13, 07:29:39 |
| Native `Agent` call | 04:15:49, RCA laptop SSH name failures | 12:18:42, Network drop RCA + monitors |
| Native call UUID | `7185006d-65d4-4c1b-b143-33982948de74` | `5dda2ac1-9d7f-4028-a71d-16931b325086` |

The 12:17 turn's recorded sent prompt has 13,146 characters. Its 819-character
delta updates supervision scheduling only; the 11,445-character handoff has
nine intervening messages, including the earlier Fable delegation request and
the assistant's report that it started an agent. The unchanged subagent rule
is absent from this immediate delta, as designed, and present in the earlier
briefings above. Walking `parentUuid` establishes these are ancestors of the
actual calls, not unrelated records elsewhere in the JSONL.

The Brain step is a summary. `python -m api.agent_events get 59f439fc --json`
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
successfully. Scratch reproduction: `temp/claude-policy-smoke.py` (ignored);
`--baseline` selects the old launcher policy.

Load the Python change through the daemon-owned Flask restart controller.
New Claude processes then receive the policy, including resumes of existing
chats. A live process retains its old tool pool until it exits. Do not terminate
an active user's work merely to retrofit this policy, and do not clear their
chat history or native resume state.

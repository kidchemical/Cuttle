# Guards — plan (not implemented)

Status: proposal, 2026-10-30, revised down to the minimum. Supervision guards
today (`src/api/chat_ops/rules.py`, `triggers.py`, `worker.py`) detect violations
and wake the schedule's supervisor chat. Two gaps, two small changes. No new
package, schema, engine, or UI.

## 1. Block forbidden-branch updates: a plain git hook

Git already solves blocking. A `reference-transaction` hook (git ≥ 2.28) sees
every branch update — commit, merge, rebase, reset, `branch -f`, `update-ref` —
and aborts the transaction by exiting non-zero in the `prepared` phase.

- **Which branches**: standard git config, per repository, shared by all its
  worktrees, never in the tree: `git config --add cuttle.protectBranch dev/core`.
- **Whose updates**: only processes carrying `CUTTLE_CHAT_SESSION_ID`, which
  Cuttle already sets for every agent turn. The user's terminal, Unity, and the
  Git UI pass untouched.
- **No Cuttle call** from the hook: it reads git config and the environment, so
  it works when Cuttle is down and cannot hang a commit.
- **Install**: `python -m api.chat_ops install-hook <repo>` copies a ~30-line
  script into the repo's hooks dir. If a hook of that name exists it refuses and
  prints how to chain it, never overwriting (Git LFS installs other hooks, not
  this one). `uninstall-hook` removes only Cuttle's script.
- Bypass (`--no-verify` does not skip this hook; `core.hooksPath` changes or
  unsetting the env var do) stays visible to the existing supervision guard.

## 2. Choose what a violation does: `on_violation` on the schedule

The schedule gets one optional list, set with `chat_cli supervise
--on-violation …` and stored in the schedule payload (Cuttle home, personal).
Each entry reuses an existing owner:

| Entry | Existing owner |
|---|---|
| `wake` (default) | today's supervisor check-in |
| `stop` | `chat_ops.service.stop` (same as Stop) |
| `action:<name>` | an allowlisted `.cuttle/actions` recipe — scripts, Discord posts, phone pings already live there |
| `supervisor:<profile>` | `service.start_supervisor` with an agent profile (agent/model/effort), used when the schedule has no supervisor yet |

Entries run in order on a new violation. The existing "report once until it
changes" dedupe still applies, so nothing repeats on every tick.

## Not doing (until a real need shows up)

Guard files and their own schema, a separate engine, expression languages,
template variables, project-wide watches with no chat, queue holds, and a guard
UI. Supervision schedules already carry the targets and rules; `.cuttle/actions`
already carries scripts and notifications.

## Size

Hook + install verb + tests: about 100 lines. `on_violation` + CLI flag + tests:
about 100 lines. Removing either: delete the code and run `uninstall-hook`;
saved schedules without `on_violation` keep the default `wake`.

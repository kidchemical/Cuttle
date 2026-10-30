# Guards — design plan (not implemented)

Status: **proposal for review**, 2026-10-30. Nothing below exists yet except the
supervision guard it generalizes (`src/api/chat_ops/rules.py`, `triggers.py`,
`worker.py`). Build only after review; build in the stages at the end.

## Problem

Supervision guards today have one fixed shape: *a supervised chat's tool call or
interval tick → built-in conditions (required branch/path, forbidden branch moved
by the watched chats) → wake the existing supervisor chat with an LLM turn.*

Users want to choose what happens on a violation (a notification, stopping the
offending chat, spawning a fresh supervisor on a chosen agent/model/effort, a
project action, a Discord post, or several of these), to attach guards to chats
that have no supervisor, and to block some violations before they land (a git
hook) instead of reporting them after.

## Model

A **guard** = **triggers** → **conditions** → **actions**.

- **Trigger**: an event that makes Cuttle evaluate the guard. Cheap; no LLM.
- **Condition**: a pure predicate over the event plus observed state (git refs,
  checkout, chat lineage). All conditions must hold (AND). No expression
  language in v1 — a fixed, named vocabulary.
- **Action**: an effect, run in order, each through an existing Cuttle owner.

A supervision schedule becomes a guard host: today's behavior is the built-in
guard `{conditions: current guard rules, actions: [wake_supervisor]}`.

### Schema v1 (YAML)

```yaml
schema_version: 1
name: protect-main                 # unique per scope
enabled: true
watch:                             # what the guard is about (one of)
  chat: CH-000123                  # a chat and its sub-agents
  # project: /path/to/repo         # any chat whose checkout is in this repo
triggers: [tool_finished, worktree_bound, interval, ref_update]
interval: 15m                      # only for the interval trigger
when:
  forbidden_branch_moved: [main, release]
  attributed_to_watched: true      # trailer | git-write command | protected history
  # required_branch: dev/feature
  # required_path: /path/to/worktree
  # forbidden_paths: '^(Networking|Editor/Multiplayer)/'
  # no_progress_for: 45m
actions:
  - notify: {level: warning}
  - stop_target: true
  - spawn:
      profile: expert-claude       # or agent/model/effort explicitly
      effort: high
      prompt: "Guard {guard} tripped on {target}: {violations}. Investigate; do not push."
  - action: notify-phone           # allowlisted .cuttle/actions recipe by name
  - service_op: {type: discord.post, params: {channel: dev-alerts}}
  # - wake_supervisor: true        # today's behavior
  # - block: true                  # ref_update trigger only (git hook)
cooldown: 15m                      # per (guard, violation fingerprint)
```

Template variables in action text: `{guard}`, `{target}` (CH- handle),
`{violations}` (bulleted), `{trigger}`, `{branch}`, `{checkout}`. Values are
inserted as data, never shell-interpolated.

### Triggers (v1)

| Trigger | Source today | Notes |
|---|---|---|
| `tool_finished` | `api.session_events` `tool` (now carries the command) | debounced per chat, lineage-aware |
| `worktree_bound` | `api.session_events` | |
| `interval` | `chat_ops` worker tick | also owns stall detection |
| `turn_finished` / `turn_failed` | new publish in `chat_turn_persist` | small seam |
| `ref_update` | git `reference-transaction` hook (stage 3) | the only trigger that can `block` |

### Conditions (v1)

Existing, lifted unchanged from `chat_ops.rules`: `required_branch`,
`required_path`, `forbidden_branch_moved` + `attributed_to_watched`
(trailer / git-write command naming the branch or its worktree / protected
branch history brought in), `forbidden_paths`, `no_progress_for`.
New, cheap: `target_state` (busy/idle/error/awaiting_input), `command_matches`
(regex over a watched chat's tool command, e.g. `--no-verify`).

### Actions (v1) and their owners

| Action | Owner it calls | Unattended-safe |
|---|---|---|
| `wake_supervisor` | `chat_ops` check-in | yes |
| `notify` | new host notice: Jobs row + attention badge (see open question 1) | yes |
| `stop_target` | `chat_ops.service.stop` (same path as Stop) | yes |
| `spawn` | `api.subagents` + profiles; pin checked by `check_agent_pin` | yes (costs a turn) |
| `action` | `project_actions` allowlisted recipe by name | only recipes marked `unattended: true` |
| `service_op` | `api.service_ops` registry | per op (`discord.post` yes) |
| `block` | hook exit status | `ref_update` only |

## Safety

1. **No raw shell in guard files.** "Run a script" names an allowlisted
   `.cuttle/actions` recipe. Guards fire unattended, so a free-form command field
   would be an unattended code-execution path. Recipes opt in with
   `unattended: true`; others are refused at load time with a clear error.
2. **Cooldown + dedupe** per (guard, violation fingerprint), Alertmanager-style:
   a violation that stays true cannot spawn a chat per tick. `spawn` additionally
   refuses while a previous spawn from the same guard is still running.
3. **Loop guard**: chats spawned by a guard are tagged and excluded from that
   guard's `watch`, so a spawned supervisor tripping the guard cannot recurse.
4. **Fail open for hooks**: if Cuttle is down or the check errors, the git hook
   allows the update and logs. A guard must never brick a repository.
5. **Hook bypass is not prevented**, only made visible: `command_matches` can
   flag `--no-verify`, `core.hooksPath` changes, or hook-file edits.
6. Validation at load: unknown keys, bad regexes, unknown profiles/actions and
   invalid model/effort pins are rejected with the file and line.

## Where things live

- **Shipped (repo)**: `src/api/guards/` — `model` (schema v1 parse/validate),
  `conditions` (pure; no Cuttle imports, testable with a fake state reader),
  `engine` (evaluate + cooldown bookkeeping), `actions` (thin adapter onto the
  owners above), `hook` (installer + `check` verb), `__main__`
  (`python -m api.guards list|validate|test|install-hook|uninstall-hook|check`).
  Feature flag `guards` (default off) in `api.experimental`.
- **Personal (never shipped)**: guard files in `<home>/personal/guards/*.yaml`
  (all projects) and `{project}/.cuttle/personal/guards/*.yaml` (one project);
  fired-guard state and cooldowns in the Cuttle home SQLite.
- **Tracked project guards** (optional): `{project}/.cuttle/guards/*.yaml` for
  rules a team wants to share, same as `.cuttle/actions`.
- Supervision schedules keep their CLI/UI; their guard fields compile to an
  in-memory guard with `wake_supervisor`.

## Git hook (stage 3)

- `reference-transaction` (git ≥ 2.28) sees every ref update — commit, merge,
  rebase, reset, `branch -f`, `update-ref` — and can abort in the `prepared`
  phase. `pre-commit` alone misses most of these.
- Identity comes from the environment Cuttle already gives agent processes:
  `CUTTLE_CHAT_SESSION_ID`, `CUTTLE_AGENT_ID`, `CUTTLE_AGENT_RUN_ID`. A process
  without them (the user's terminal, Unity, the Git UI) is not blocked.
- Installed once per repository (`.git/hooks` is shared by all worktrees). The
  installer **chains** any existing hook (e.g. Git LFS) and uninstall restores it.
- The hook calls `python -m api.guards check --ref … --old … --new …` with a
  short timeout; exit 1 only on a matching `block` guard.

## Prior art (adopt / adapt / build)

- **Alertmanager** (Prometheus): grouping, repeat interval, inhibition → adapt the
  cooldown/dedupe semantics; no dependency.
- **Falco rules / GitHub Actions** (`on` / `if` / `steps`): familiar file shape →
  adapt the layout; no dependency.
- **CEL / Open Policy Agent**: general policy languages → not in v1. A fixed
  predicate vocabulary is enough and keeps files reviewable; a `cel:` condition is
  the extension point if needed later (`cel-python`, optional dependency).
- **pre-commit framework**: aimed at linters on staged files, not ref policy by
  agent identity → not adopted.

## Migration and removal

- Existing supervision schedules keep working unchanged; stage 1 only moves their
  rule evaluation behind the guard engine with a `wake_supervisor` action.
- Flag off → no personal guard files are loaded, no hooks are checked (installed
  hooks fail open), supervision guards behave exactly as today.
- Removal: delete `src/api/guards/`, run `uninstall-hook` per repo; supervision
  keeps its own rule path from before stage 1.

## Stages

1. **Engine + schema**, supervision migrated behind it; actions `wake_supervisor`,
   `notify`, `stop_target`. Tests: the existing `chat_ops` guard suite unchanged,
   plus engine/cooldown tests with a fake state reader.
2. **`spawn`, `action`, `service_op`**, template variables, loop guard, guards
   without a supervisor chat (`watch: project`).
3. **Git hook** (`ref_update` trigger, `block` action, installer with chaining).
4. **UI**: list/enable/disable guards and see recent firings (Apps → Orchestrator
   or Jobs), surfaced like Jobs rows.

## Decisions (were open questions; decided 2026-10-30 against Cuttle's positioning)

Grounding: Cuttle is a self-hosted "harness of harnesses" — one host, shared
history across web, desktop and the Android app; agents act through
`python -m api.*` verbs; parts are sockets; personal tweaks stay out of git.

1. **`notify` is host-side first.** A guard firing writes a durable host record
   (Jobs row + the chat's attention badge + an entry in the guard's firing
   history). Because state lives on the host, every client — browser, Electron,
   Android — sees it without an open page. Off-host delivery is an explicit
   `service_op` (Discord today; ntfy or similar as a future `service_ops` drop-in),
   not a new channel inside guards. Browser push stays owned by completion
   notifications; it can later subscribe to guard firings as one more event kind.
2. **A spawned supervisor is incident-scoped.** `spawn` creates a chat that
   receives an incident brief (guard, violations, target snapshot, target handle)
   and acts through the normal verbs (`chat_cli status|snapshot|send|stop`). It is
   not silently made the target's long-term supervisor: one schedule keeps one
   supervisor, so two LLMs never steer the same chat. The firing record links the
   two chats; promoting it is an explicit `chat_cli supervise` call. Lifetime is
   `conversational` so the user can talk to it; it is closed when the incident
   resolves or after an idle timeout.
3. **`watch: project` evaluates on `ref_update`, `interval`, and events from any
   chat whose checkout is in that repository** (worktree binds already record the
   checkout). Conditions are cheap and LLM-free, so wide triggers cost nothing
   until an action runs; attribution uses the chat that raised the event and its
   lineage, exactly as chat guards do.
4. **Yes, a stronger stop exists, opt-in.** `stop_target: {hold_queue: true}`
   also pauses the target's queued `chat_ops` sends and its supervision schedule
   (existing `pause-operation` semantics) until someone resumes them. Default is
   the plain Stop. Holds are listed on the firing record and in Jobs, and
   `python -m api.guards release <firing>` resumes everything it paused.

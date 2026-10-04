# Desktop audit repairs — 2026-10-03

Inputs: `cuttle-docs-audit.md` and `cuttle-personal-scope-audit.md` supplied on the
Desktop. Repairs are prepared in the local checkout; no commit, push, release,
service restart, dependency install, device update or integration activation was
performed. Existing live Python code requires a daemon-owned Flask restart to
load these changes.

## Runtime and scope outcomes

- Authenticated `/api/chat` admission now checks Web Chat pairing/allowlists
  before controls, shell commands, vision, routing and harness execution, for
  both sync and streaming requests. Errors fail closed with HTTP 503. Explicit
  empty allowlists remain empty. Other API authorization is separate.
- Windows/POSIX client updaters share `client-update-checkout.py` as their Git
  preservation owner. It refuses tracked/staged edits, untracked files, detached
  HEAD, missing upstream, local/diverged commits, and incoming paths that collide
  with ignored local files. It fetches and fast-forwards before lifecycle effects;
  no hard reset, clean or implicit stash. Ignored-file collision protection is
  explicit because a regression showed the Git merge flag alone was insufficient
  on the tested fast-forward path. This is a pre-update snapshot check, not a lock
  against another process writing the checkout during the update.
- Commands and skills use project personal → project tracked → nested source
  personal → nested source tracked → global personal → global tracked precedence.
  Commands/actions replace by normalized declared name; skills by directory id.
  Discovery records winning source/ref/path. Skill list/get/ranking share project
  scope; shadowed refs cannot bypass the effective winner.
- Personal-only docs are readable by the merged ranking reader. Rules/docs
  retain additive tracked-first deltas. Nested personal actions are supported.
  Explicit boolean `disabled: true` suppresses lower-priority structured units;
  local action disables also block alternate-project resolution fallback.
- GLOBAL.ini adds `skills` and `commands` layer controls and default-off optional
  guidance via `[integrations] gitea = on`. Gitea runbook inventory/ranking/body
  reads and capability injection honor the gate. An integration-tagged skill
  uses the same opt-in. Runtime fallback URL/user settings do not enable guidance;
  the flag neither starts a service nor grants access. Project-owned docs remain
  independently project-owned.
- Scaffolding creates tracked/personal skills directories and uses the current
  scratch-rule pointer. Script recipes retain explicit literal paths; no heuristic
  shell rewriting or transparent executable-script overlay is claimed.
- Twelve original TRELLIS/Govee workflow files were hash-verified in ignored
  local storage before their tracked copies were removed. Active commands,
  actions, scripts, docs and skills now live under `.cuttle_global/personal/`;
  exact originals remain under its `scope-migration-originals/` archive. Active
  references were updated, including the relocated publisher's root calculation.
  Cuttle roadmap/Electron debug skills moved into `.cuttle/skills/`.
- ComfyUI requires `COMFYUI_ROOT`, reports unconfigured status without network
  effects and no longer asserts an F-drive path or a specific GPU. Supported
  Python/guest MCP interfaces remain; guest MCP dependencies/setup are explicit.
  Govee/Home Automation and Gitea/Jobs runtime integrations remain supported.

## Documentation audit disposition

| Finding | Resolution |
|---|---|
| 1: Linux executable permissions | Startup/Host/Client scripts have tracked 100755 modes; both Android Gradle wrappers corrected too. |
| 2: Retired process-killing instructions | Electron docs use daemon-owned lifetime and restart procedures. |
| 3: Broken README/review links | Global runbook paths corrected; historical findings retained. |
| 4: Stale Electron prerequisites/startup | Host/Client, Python 3.11+, Linux support and listener schemes documented. |
| 5: Old build artifacts/navigation/update claims | Version placeholders, current navigation/update behavior and dependency paths corrected. |
| 6: Stale AGENTS implementation pointers | Slash registry owner and Discord REST-only process model corrected. |
| 7: Removed Cursor rules | README describes AGENTS plus project/global rules and runbooks. |
| 8: Duplicated procedures/Windows-only links | Brief points to owning runbooks and uses OS-appropriate file-reference guidance; the old duplication assertion now checks owner/pointer. |
| 9: Global/project configuration confusion | Architecture/config tables and diagrams distinguish both trees and their personal overlays. |
| 10: Missing pytest-timeout | Development requirement added; setup installs runtime and dev dependencies before pytest. |
| 11: Unrunnable module examples | Root examples supply PYTHONPATH and OS-specific venv syntax; ten CLI help commands verified. |
| 12: Incorrect global README identity | Global scope, personal tree and selection contract rewritten. |
| 13: Misstated overlay replacement | Rule/doc additive behavior distinguished from whole-unit command/action/skill replacement and literal script paths. |
| 14: Missing personal commands | Implemented with isolated precedence, lookup and disable regressions. |
| 15: Retired Cuttle MCP claims | Removed from active optional guidance; supported standalone guest MCP setup documented. |
| 16: Maintainer-machine 3D assumptions | Workflow moved to ignored personal scope; reusable client assumptions removed. |
| 17: Pairing check after execution | Admission moved before dispatch; request-level sync/stream regressions added. |
| 18: Retired Discord pairing walkthrough | Replaced with authenticated Web Chat identities and owner-only API prerequisites. |
| 19: mDNS setting/dependency drift | Actual LAN/mdns keys, zeroconf installation and restart documented. External proxy schemes clarified. |
| 20: Android companion transport/token | Trusted HTTPS proxy contract and required token documented; source-level validation only. |
| 21: Missing voice backend | App marked legacy prototype; absent backend/MCP recipes removed, JDK 17 and transport limits documented. |
| 22: Per-push version bumps | Guides/comments use release-only SemVer and git-revision/stale-boot update signals. |
| 23: Hidden destructive self-update | Implementation preserves/refuses local work; both worker guides and release runbook describe the new contract. |
| 24: Router feedback/scoring contradictions | Feedback, optional semantic judging, escalation policy and explicit child-chat execution distinguished. |
| 25: Smaller stale pointers | GLOBAL.ini header, scratch-rule index, utility/Electron links and optional roadmap-dump location corrected. |
| 26: API keys vs CLI authentication | Vendor installation/login separated from optional routing, vision and direct-provider credentials. |

Scope audit gaps 1–8 are covered by scoped skills/ranking, personal commands,
personal-only doc reading, nested actions, explicit script semantics, skills
scaffolding and structured-unit disabling. Rule/doc personal deltas remain
additive; disabling structured units does not retract safety rules.

Optional wrappers were reviewed rather than deleted by name: the Gitea shim,
Muse/Playwright guest launchers, Tailscale/mesh setup helpers, Blender worker recipe
and vendor Govee integration remain compatible optional entry points. No internal
caller does not establish that an external entry point is unused. Unrelated
seeded-project examples were removed from the shared commands skill. The local
planning skill delegates to its existing global runbook owner.

## Validation and limits

- Broad offline gate: **2,708 passed, 59 skipped, one stale documentation assertion
  failed**. That assertion was corrected to verify the single procedure owner;
  the final affected gate passed **63 tests**. The entire broad suite was not
  repeated after that source-assertion/template/doc cleanup.
- Pairing/authorization/coordinator/stream/restart gate: **138 passed**.
- Final pairing/worker preservation gate: **70 passed**.
- Scoped discovery/integration/context/architecture gate: **72 passed**.
- Loader owner's initial focused/neighbor gate: **126 passed**. The scope child's
  expanded run had an action-fixture failure caused by parent-checkout discovery;
  root isolated that fixture from real actions and the final gates pass.
- Preservation hashes, real project skill list/get, guest isolation, simulated
  clean-global discovery: passed. Twelve exact archives are ignored by Git.
- Ten CLI `--help` invocations succeeded. Owned-doc link scan checked 138 targets;
  only a literal teaching placeholder matched as missing. Bash fences/launchers
  passed syntax checks. Touched Python modules compiled. Working/staged whitespace
  checks passed. Five executable mode changes are staged; content changes remain
  pending for review.
- Public tracked global-rule text: **10,187 → 10,288 characters**. A fresh `hello`
  envelope with inventory, history, personal rules and model judging excluded:
  **13,312 → 13,413 characters**. The agent brief separately shrank from 19,764 to
  15,648 characters. Relevant/irrelevant prompt, guest, personal, shadow/off and
  non-severable safety checks passed; size is not a compliance guarantee.
- Windows PowerShell/Electron builds, APK/device/proxy connectivity, clean
  dependency installation, real guest MCP, GPU workflows and optional services
  were not executed. Paid/live tests remain gated and skipped. No production
  restart or worker deployment is included in the verification results.

Official references used to verify external documentation claims:
[Android AGP 8.2 requirements](https://developer.android.com/build/releases/agp-8-2-0-release-notes),
[Tailscale Serve](https://tailscale.com/docs/reference/tailscale-cli/serve), and
[Tailscale Funnel](https://tailscale.com/docs/reference/tailscale-cli/funnel).
Live CLI/proxy behavior is not claimed from those documentation checks.

Child implementation records: CH-000929 (loaders), CH-000930 (documentation),
CH-000931 (workflow migration). Detailed scratch reports/logs live under ignored
`temp/`; this document records the durable disposition.

## Follow-up: restart-card failure during self-edit

The first live restart click exposed a transition failure absent from fresh-process
tests. Flask had cached the old personal-overlay module; a newly loaded action
loader imported its newly added `unit_disabled` symbol. The import failed before
the shell restart recipe reached the restart service. Removing that unnecessary
import fixes the immediate dependency, but editing disk cannot replace a module
already cached in a running process.

The HTTP card path now routes the reserved `flask.restart` control directly to
`api.flask_restart.request_restart`, after existing token/session validation and
an explicit owner-role check. Project recipe resolution and shell launch are
bypassed. The restart service still owns drain/force policy and daemon requests;
no process kill, second daemon, or blanket module reload was added. Standalone
legacy recipe callers retain their prior behavior.

Transition regressions remove the overlay symbol and deliberately make action
resolution/listing/shell execution raise ImportError. HTTP restart cards still
reach the fake restart service once for status/graceful/when-idle/force; non-owners
remain denied. Focused action/forms/restart/architecture validation: **87 passed,
1 skipped**. The immediate loader gate separately passed **25 tests**.

This protects configuration-loader failures on the HTTP restart-card path; it
is not a claim that arbitrary edits to the live Flask/auth/restart implementation
are safe. Self-development validation must include cached-old/new-module
transitions and isolated candidate testing. Existing hosting-process recovery
still requires the native command/owner endpoint once to load these changes.

## Second audit cross-check

Re-read both complete Desktop audits against committed snapshot `5b8840d1` and
current source. The 26 documentation dispositions and eight confirmed scope
loader gaps still map to implemented repairs. Two omissions were corrected:

- The five Linux executable modes had reverted to 100644 in the latest commit.
  Restored staged 100755 modes for startup, Host/Client launchers and the voice/main
  mobile Gradle wrappers. The Git UI commit helper now preserves selected,
  explicitly staged executable-bit changes through its reset/add sequence when
  core.filemode=false. A disposable repository regression proves the final
  committed tree mode, not just the working filesystem permission.
- Two global documentation/skill references still named an unrelated personal
  project. Removed those remaining named examples.

Fresh audit-focused gate: **89 passed** (loaders, integration gating, updater
preservation, ComfyUI configuration, pairing, commands, skills, restart cards and
architecture). Git pending-change suite: **31 passed**, including preservation of
staged executable modes and recovery from partially staged deletions.

Outstanding acceptance evidence remains: clean dependency installation, Windows
PowerShell/Electron builds, Android APK/device and trusted-proxy connectivity,
external Tailscale CLI/proxy behavior, actual guest MCP and GPU/service workflows.
In particular documentation finding 20 has a corrected supported transport
contract, but that contract has not been demonstrated on a physical Android
companion. Do not label these live-validation targets complete. Optional wrapper
recommendations retain supported explicit entry points rather than deleting them
solely because there is no internal caller. No release/push/restart was performed
by this second cross-check.

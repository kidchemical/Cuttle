# Feature-owned context bundles

Status: proposed design; runtime discovery and context gating are not implemented.

Experimental features ship their agent guidance with the feature. Resolved
enablement determines whether that guidance is available to a chat. Conditional
wording in an always-injected rule is insufficient: disabled capabilities must
be absent from the effective context catalog.

## Ownership and layout

Proposed shared layout, extending the existing Cuttle config tree:

```text
.cuttle_global/features/<feature-id>/
  rules/
  docs/
  skills/
  commands/
  actions/
  scripts/
```

Only include directories a feature uses. This is packaged configuration, not
a new plugin executor or a marketplace. Feature code stays in its owned API
package. Register its bundle against the existing feature registry; do not infer
enablement from a directory's existence or create another toggle store.

Use feature-qualified resource identities, such as
`feature/gizmos/docs/usage-meters.md`, to prevent collisions with core/project
files. Script resources are dependencies of approved commands/actions, not
instructions to run code while discovering a bundle. Preserve existing public
command/action identifiers when migrating; reject ambiguous registrations.

Install-local overlays live under `<home>/personal/features/<feature-id>/`
and retain the established markdown-append and unit-replacement semantics.
They cannot enable a disabled feature. Project policy can suppress a shared
bundle's context categories through the corresponding GLOBAL.ini settings;
it cannot bypass the runtime feature gate. Core safety rules remain independent.

The bundle belongs to the feature throughout its lifecycle. Graduation from
experimental to generally available changes availability policy without
duplicating the bundle into unrelated global files. Established Tasks guidance
remains core; usage-meter and shell-dock guidance belongs to `gizmos`.

## Effective catalog

One shared resolver supplies the effective feature resources to existing owners:

- Brain full context: enabled feature rule bodies and resource inventories.
- Brain resume snapshots/deltas: the same resources and availability state.
- Markdown skills: enabled summaries, lookup and body reads.
- Doc ranking: enabled candidates and body reads, independent of whether the
  optional ranking judge is available.
- Project commands: enabled palette entries and invocation lookup.
- Project actions: enabled definitions, resolution and execution validation.
- Capability/runbook hints: enabled feature hints only.

The resolver captures the existing flag resolver's effective state, including
defaults, stored overrides and the process kill switch. Unknown feature ids
are unavailable. Each preparation uses one captured view for text and snapshot;
if it changes during preparation, retry or use the existing full-context fallback.
Discovery must not start services or execute feature code.

An off feature contributes no operational rules, docs, skills, commands or
actions to the effective catalog. A small core feature-management catalog may
still list its name, description and enable/disable control, so an explicit user
request can discover and enable it. That catalog does not expose its workflows.

Feature discovery is distinct from maintainers reading tracked source/docs to
develop or debug a disabled feature. Cuttle project development documentation
can describe the registry and bundle architecture without opting a user into
that capability. Files remain shipped in source and release builds.

## Availability changes and enforcement

On enablement, the next prepared turn adds the feature's rules and catalogs.
On disablement, the next prepared turn explicitly withdraws its prior guidance
and removes its catalogs. Preserve the existing delivery receipt contract:
acknowledge a snapshot only after its context was delivered successfully.

Include feature availability and resource identity/content changes in resume
snapshots and relevant catalog/cache keys. Merely tracking directory basenames
will miss a flag change. Direct skill/doc lookups must use the same effective
catalog rather than falling back to raw packaged paths.

A resumed native agent retains earlier conversation text; Cuttle cannot erase
that memory. Withdrawal notices supersede the earlier feature rules, and actual
feature gates continue to enforce availability. Commands/actions prepared
before disablement must recheck the gate when invoked, including persisted
confirmation cards. Guidance visibility never replaces auth or approval checks.

Flag changes take effect at the next context preparation boundary; immediate
mid-turn behavior remains governed by runtime gates. Do not promise to rewrite
an already-running vendor agent's prompt. Ordinary flag changes should require
no Flask restart; adding loader code follows the existing reload procedure.

## Implementation sequence

1. Add the shared bundle resolver and registry association with validated roots,
   feature-qualified identities and captured availability.
2. Integrate Brain full context, resume deltas and all resource discovery/read
   owners. Reuse existing overlay and project-policy semantics.
3. Split the mixed Gizmos runbook: keep core Tasks in core guidance and move
   usage-meter/shell instructions into the `gizmos` bundle. Remove operational
   pointers for that experiment from unconditional global rules/capability hints.
4. Migrate other experimental operational guidance through the same mechanism;
   keep feature-development documentation scoped to the Cuttle project.

Acceptance covers fresh and resumed chats; flag off/on/off; kill switch;
default-off public installs; overlays; GLOBAL.ini off/shadow; disabled direct
lookups and persisted actions; cache invalidation; preparation/delivery races;
and core Tasks/safety surviving when every experiment is disabled. Check the
effective catalog with optional judging disabled and enabled, and across
multiple independent features. Disabled feature text must not appear anywhere
in the prepared operational envelope or ranker candidates.

Current seams: `api.experimental.flags`, `api.cuttle_brain.context_compiler`,
`context_delta`, `api.markdown_skills`, `api.jev.rank`, `api.project_commands`,
`api.project_actions`, and `api.cuttle_ui_capabilities`.

# Cuttle Architecture Principles

Cuttle should remain easy for both humans and coding agents to understand, modify, test, and extend.

Prefer simple ownership and explicit boundaries over clever abstractions.

## 1. One behavior, one owner

Every meaningful behavior or state transition should have one obvious owner.

Do not duplicate business rules across pages, routes, adapters, helpers, or compatibility paths.

If logic already has an owner, call that owner instead of reimplementing it.

## 2. Composition roots compose; they do not own domains

Large entry files such as the web API, chat page, and shell may coordinate other components.

They should primarily:

- gather inputs,
- call owned domain/service interfaces,
- perform transport, rendering, or orchestration,
- apply returned decisions or effects.

New domain logic should not be added directly to a composition root when a coherent owner exists or can reasonably be created.

Do not extract code merely to reduce line count. Extract when doing so creates clearer ownership.

## 3. Dependencies point toward owners

Higher-level composition may depend on lower-level domains and services.

Owned domains must not reach backward into their composition root.

Avoid:

- reverse imports,
- circular dependencies,
- hidden access to page/module globals,
- process-global import tricks,
- duplicated state caches.

Prefer explicit arguments, narrow interfaces, and dependency injection where useful.

## 4. State has one owner and an explicit lifetime

Before adding mutable state, determine:

- who owns it,
- whether it is request-, turn-, session-, process-, or persistent-lifetime,
- how it is reset or recovered,
- whether it survives restart.

Do not create multiple mutable copies of the same logical state.

**Persistent state lives in the Cuttle home, never the install tree.** The
checkout, `Program Files` folder or AppImage mount is code only: it may be
read-only, is replaced on upgrade, and can be shared by several users. Every
database, settings file, upload, log, cache, `.env`, secret and install-local
overlay resolves through `core.runtime_paths` (`cuttle_home()` and its helpers),
never by joining onto `__file__`, `src/` or the repository root. A new store gets
a `runtime_paths` helper or a `runtime_state_path(owner, name)` call, and a test
redirects it with `CUTTLE_HOME`. A project's own `.cuttle/` (tracked config and
its `personal/` overlay) belongs to that project and is not runtime state.
`src/tests/quality/test_state_outside_install_tree.py` enforces this; layout and
migration: [`cuttle-home.md`](cuttle-home.md).

Pure decision modules should receive snapshots and return decisions or transitions rather than secretly reading unrelated global state.

## 5. Keep decisions separate from effects

When practical, structure behavior as:

`gather state → decide/plan → execute effects`

Decision logic should be easy to test without DOM, network, subprocesses, or unrelated application state.

Transport, rendering, persistence, and process execution should remain at the appropriate boundary.

Do not force this pattern where it makes the code less clear.

## 6. Preserve architectural categories

Cuttle has distinct extension roles:

- **Surface adapters** present Cuttle to users and transport input/output.
- **Agent harness adapters** wrap installed external agent CLIs.
- **Agent operations / service integrations** provide capabilities agents can invoke.

Do not blur these categories.

Surfaces should not implement vendor execution logic.

Agent operations should not become alternate chat/execution surfaces.

Harness adapters should remain vendor-specific wrappers behind the shared execution path.

## 7. Cuttle is BYO-CLI

For external agent tools, Cuttle should:

- discover the installed CLI,
- validate availability/version where appropriate,
- invoke it through its adapter,
- expose supported capabilities,
- provide installation guidance when missing.

Cuttle should not silently install or update third-party agent CLIs.

## 8. Favor small, boring interfaces

Prefer a few explicit values or a narrow data structure over:

- generic callback bags,
- magical fallback behavior,
- implicit global lookup,
- broad utility modules,
- abstractions created only for reuse that does not yet exist.

A new contributor or agent should be able to identify the owner and interface without tracing the entire application.

## 9. Fix the smallest correct boundary

Bug fixes should normally stay within the owning domain.

Do not redesign neighboring systems just because they are messy.

If a bug reveals an architectural problem, document it separately unless fixing the boundary is necessary for the bug itself.

Avoid opportunistic feature additions during refactors.

## 10. Refactors preserve behavior unless explicitly stated otherwise

Before moving risky behavior, characterize it.

Prefer behavioral tests against real public interfaces over source-text assertions.

When behavior intentionally changes:

- state the change,
- test it,
- distinguish it from structural refactoring.

Do not call untested semantic drift “benign.”

## 11. Tests must fail closed around external execution

Tests should never accidentally:

- send paid model prompts,
- invoke a real agent CLI,
- perform network actions,
- install software,
- mutate real user data.

Integration tests should explicitly provide fake executors/providers or fail before reaching external execution.

Real smoke tests should be deliberate and opt-in.

## 12. Optimize for agent navigability

A normal feature or bug fix should fit inside a reasonably small working context.

The ideal path is:

1. find the owner,
2. read its interface and tests,
3. make the change,
4. verify neighboring contracts.

If an agent routinely needs to understand an entire 10k–25k line composition file to safely modify one feature, that boundary should be reconsidered.

Line count is a warning signal, not an architecture metric by itself.

## Before implementing

For a non-trivial change, answer:

1. **Who owns this behavior?**
2. **Who owns the state, what is its lifetime, and does it live in the Cuttle home?**
3. **What is the narrowest interface this change should use?**
4. **Am I duplicating logic that already has an owner?**
5. **Can I test the behavior without exercising unrelated systems?**

If those answers are unclear, inspect the repository map and existing domain interfaces before writing code.
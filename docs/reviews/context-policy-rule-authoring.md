# Context policy + rule-authoring review

Cuttle implementation audit of the always-on context-policy trim and the
rule-authoring runbook. No transcripts, chat identities, local paths, or
secrets below.

## Sample limitation

Findings rest on a convenience sample of locally available logs: 538 records
across two projects — not a representative product population or benchmark.
Treat every observation as a lead for owned fixes, not a product claim.

## What the sample showed

- Sampled earlier fresh payloads split two ways: bare fresh turns ran ~18–20K
  characters with the full always-on rules and no ranked extras, while the
  ~28K cases DID include ranked extras. Those figures describe the logs as
  observed, NOT the trimmed rules below — do not combine the two sets.
- Normal resumed turns are usually bare prompts, so the full-once/resume
  design is retained.
- One resumed chat repeated the same architecture-inventory notice across
  many resume deltas: resume bookkeeping must distinguish prepared vs
  successfully delivered context and never acknowledge truncated/failed
  payloads. The correction records the exact prepared snapshot only after a
  successful, non-meta, non-cancelled adapter result. Failed turns retain pending
  notices; missing snapshots and truncated or unstable deltas get a full briefing.
  Fresh and resumed contexts now apply the same project policy and personal overlays.
- Unrelated runner-ups: a desktop-settings question ranked chat-history docs;
  a license question ranked chat-history; a lag investigation ranked Govee
  alongside Electron debug. The ranked winner may still be wrong — selection
  policy tests are not semantic evaluation. Injection now accepts only the gated
  winner: categorical runner-up probabilities do not establish independent
  relevance. Project docs retain candidate priority within the existing cap;
  summaries use bounded merged titles/intros. Excerpts are marked and respect
  both character budgets. Existing query logs retain bounded selection metadata.
- Mandatory short project pointers stay always-on. Longer material is read on
  demand from those mandatory intent pointers even when ranking is disabled;
  it does not live only in optional excerpts.

## Rule trim results (offline fixtures, ~14–19K envelopes)

`compile_context` with the optional judge disabled, matched synthetic guest
vs Cuttle projects. Rule bodies are summed through the same loader + router
the compiler uses; envelope totals are separate.

| Measurement (characters) | Before | After |
|---|---:|---:|
| Global rule bodies | 12,343 | 10,187 (−17.5%) |
| Cuttle project rule bodies | 2,535 | 3,289 (intent router and version correction) |
| Guest full envelope | 16,290 | 14,153 |
| Cuttle full envelope | 19,103 | 17,720 |
| Global rule bodies with policy off | 1,021 | 1,021 (safety only) |

Figures are historical fixture measurements under default settings with an
empty personal overlay and ranking disabled — not live-chat savings. Counts
are characters of compiled rule bodies vs total envelope, not bytes/tokens
and not billing savings. Semantic relevance was NOT validated: the optional
judge was disabled in these runs.

The manager measured baseline and candidate tracked guidance in the same fixture
path. Absolute path lengths and inventories affect envelope totals; installed
overlays can change both totals. Vendor-native `AGENTS.md` ingestion is a separate
context channel and is not included in these Brain envelope savings. The capability
directory and existing CLI/child-chat pointers remain: capability-only fallback
and agent operations still need them. No new global Cuttle architecture skill or
second context router was added.

Global `00-core.md` kept every hard invariant and intent→doc pointer; how-to
moved to the runbooks that already owned it. New on-demand runbook:
`.cuttle_global/docs/rule-authoring.md` (scope, invariants-vs-procedures, one
owner, narrow triggers, personal overlay, budgets, tests). Only
`00-safety.md` is mechanically non-severable; the rest can fall to explicit
`GLOBAL.ini` policy.

## Reproduce with public commands

From the Cuttle checkout with the project venv, judging disabled:

```bash
CUTTLE_JEV_DISABLED=1 .venv/bin/python -m pytest \
  src/tests/test_context_policy_rules.py \
  src/tests/test_context_compiler.py \
  src/tests/test_global_project_split.py \
  src/tests/test_personal_overlay.py \
  -q -p no:cacheprovider
```

Run delivery and ranking policy gates with explicit fake adapters/judges:

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  src/tests/test_context_policy_audit.py src/tests/test_rank_winner_only.py -q
```

These tests make no vendor calls. The ranking tests intentionally enable fake
selection; a global `CUTTLE_JEV_DISABLED=1` suppresses even their compiler seam.
The rule-scoping suite disables judging within its fixtures. Existing architecture
boundary tests continue to enforce import direction and owned execution seams;
instructions alone cannot prove that an agent follows the owner map.

## Reviewed checkpoint

Manager verification: **2,549 passed, 61 deliberate live/platform/fixture skips**
in the broad offline suite (the existing `src/tests/unit` exclusion retained).
The running isolated shadow app also passed **three Stop/resend/refresh browser
journeys with zero skips**, using production routes and deterministic execution.
The new selection tests use fake judges; no claim of live semantic ranking
accuracy is made. One unrelated stale title-test expectation was corrected to
match the existing production anti-anchoring behavior; production title code
was preserved.

The Python changes require the normal user-selected Flask restart before they
affect the hosted instance. No daemon restart, additional listener, or networking
redesign is part of this checkpoint. Existing import/lifecycle owners and the
full-once/resume model remain in place.

`test_context_policy_rules.py` compiles matched isolated Cuttle and synthetic
guest contexts and asserts: Cuttle-only principles/map/safety pointers appear
only for Cuttle; full development-safety procedures are absent for both hello
and a bug request (the pointer instructs the agent to read them); mandatory
host/cross-project safety is present and survives `GLOBAL.ini` off; and a
generous global rule-body budget holds without freezing exact lengths.

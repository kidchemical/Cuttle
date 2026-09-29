# ERRORS

Command failures, exceptions, and integration issues. Format: `[ERR-YYYYMMDD-XXX] skill_or_command_name`

| Situation | Action |
| --- | --- |
| Command/operation fails | Log with Summary, Error, Context, Suggested Fix |
| API/external tool fails | Log with integration details |
| Recurring | Add **See Also** to link related entries |

---
<!-- Add entries below -->

## Agent Harness — "add agent" (Gemini pilot) rollout

Tally of everything hit while wiring Gemini into the folder-per-agent harness. Source chat: **CH-000148**.
Root process gap: the agent was handed to the user to test *before* a test existed. A contract +
live smoke test would have caught ERR-1 on the first run, then surfaced ERR-2 as an actionable
message instead of a wall of stack traces. See `src/api/agent_harness/ADDING_AN_AGENT.md`.

### [ERR-20260816-001] agent_harness/gemini — resume store crashes on int session id
- **Priority:** High · **Status:** Fixed (regression test added) · **Area:** agent_harness / gemini_cli_session_store
- **Summary:** `/gemini …` returned `'int' object has no attribute 'strip'`.
- **Error:** `AttributeError: 'int' object has no attribute 'strip'`
- **Context:** Kernel passes the raw DB `chat_session_id` (an int) into `load/save/clear_gemini_resume_id`; the store called `.strip()` directly on it. First real `/gemini` turn (CH-000148 msg 2445).
- **Suggested Fix / Done:** Coerce with `str(...)` before `.strip()` in `src/scripts/utilities/gemini_cli_session_store.py`. Regression test `test_gemini_resume_store_accepts_int_session_id` in `src/tests/test_agent_harness.py`.

### [ERR-20260816-002] agent_harness/gemini — Gemini CLI free tier no longer supported (auth)
- **Priority:** High · **Status:** Open (external / account) · **Area:** gemini_cli_tool auth
- **Summary:** After ERR-1 was fixed, `/gemini` failed to authenticate.
- **Error:** `IneligibleTierError: This client is no longer supported for Gemini Code Assist for individuals … reasonCode: UNSUPPORTED_CLIENT` (migrate to https://antigravity.google).
- **Context:** `@google/gemini-cli` free login (`Loaded cached credentials`) is rejected. CH-000148 msg 2449.
- **Suggested Fix:** Provide an AI Studio API key via `GEMINI_API_KEY` / `GOOGLE_API_KEY` env (loaded from `src/.env`), or migrate the CLI login. Code-side: detect this class and return a concise, actionable error (see ERR-3).
- **See Also:** ERR-20260816-003

### [ERR-20260816-003] agent_harness/gemini — raw CLI stderr leaked into chat as the "answer"
- **Priority:** High · **Status:** Fixed · **Area:** gemini_cli_tool output handling
- **Summary:** On failure the chat showed raw CLI noise (WARN pytest_cache scandir, "YOLO mode is enabled", "Loaded cached credentials", MCP `unityMCP` ECONNREFUSED, full node.js stack traces) instead of a clean error.
- **Error:** N/A (presentation bug) — see CH-000148 msg 2449.
- **Context:** `execute_prompt` merged raw stderr into `error`/`output`; the kernel surfaces `result.error` verbatim.
- **Suggested Fix / Done:** Added `summarize_gemini_stderr()` in `gemini_cli_tool.py`: classifies known auth/quota failures into a one-line actionable message and strips known noise banners + MCP-discovery/stack-trace blocks. Regression test in `src/tests/test_agent_harness_smoke.py`.

### [ERR-20260816-004] agent_harness/gemini — noisy MCP discovery + pytest_cache scandir warnings
- **Priority:** Low · **Status:** Mitigated (filtered from output) · **Area:** gemini_cli_tool / env
- **Summary:** Gemini attempts MCP discovery for `unityMCP` (127.0.0.1:8090 → ECONNREFUSED) and warns on `src/.pytest_cache` (EPERM). Cosmetic but pollutes replies.
- **Suggested Fix / Done:** Filtered by `summarize_gemini_stderr()`. Longer term: run Gemini with MCP discovery disabled / a clean cwd for headless turns.

### [ERR-20260816-005] process — new harness agent shipped to user without a test
- **Priority:** High · **Status:** Fixed (process) · **Area:** agent_harness onboarding
- **Summary:** "Add agent" had no embedded test-first gate, so the user became the first tester and hit ERR-1 then ERR-2 live.
- **Suggested Fix / Done:** `src/api/agent_harness/ADDING_AN_AGENT.md` runbook now mandates a contract test + gated live smoke (`CUTTLE_AGENT_SMOKE=1`) before handing an agent to the user. Smoke harness: `src/tests/test_agent_harness_smoke.py`.

### [ERR-20260816-006] agent_harness/opencode — configured provider has insufficient credits
- **Priority:** Medium · **Status:** Mitigated (prefer OpenAI) · **Area:** agent_harness / opencode auth
- **Summary:** The live OpenCode smoke reached Anthropic but the configured account had insufficient credits.
- **Error:** `Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing.`
- **Suggested Fix / Done:** Default `/opencode` model is now `openai/gpt-4o-mini`. Allowlisted action `opencode.sync-auth` copies `OPENAI_API_KEY` from Cuttle env/`.env` into OpenCode `auth.json` without the LLM seeing the key. Billing error text points at that path. Regression coverage in `test_agent_harness_smoke.py`.

### [ERR-20260816-007] agent_harness/opencode — prompts silently truncated at 2,800 characters
- **Priority:** Critical · **Status:** Fixed · **Area:** agent_harness / prompt transport
- **Summary:** The OpenCode adapter sliced long prompts before launch. Because Cuttle prepends its capability contract, normal first turns could lose part or all of the user's request without any error.
- **Cause:** A conservative Windows shell argv threshold was copied into the adapter even though the installed npm package includes a native executable and OpenCode supports stdin.
- **Suggested Fix / Done:** Resolve the packaged `opencode.exe` instead of the npm `.cmd` shim and send the complete UTF-8 prompt over stdin. Added offline byte-for-byte transport coverage and changed the live per-agent smoke to use a long prompt with its required instruction at the end.

### [ERR-20260816-008] cuttle_ui_capabilities — chat-store recipe leaked another chat's transcript
- **Priority:** Critical · **Status:** Fixed (regression test added) · **Area:** cuttle_ui_capabilities / cuttle_brain.context_compiler / muse prompt
- **Summary:** In **CH-000155** the agent answered about **CH-000147** — it read and summarized a different user conversation as if it were the current chat.
- **Cause:** The same day's edit to `cuttle_chat_store_addon()` added a *literal* example id to the prompt block: the mapping line named `CH-000147` and the read recipe shipped `sid=147;` as runnable code. The block never told the agent which chat the turn belonged to, so an agent asked about "this chat" ran the only recipe it had, verbatim.
- **Context:** The block reaches `/muse` (`_with_muse_chat_context`) and every harness agent through the Context Compiler (`include_chat_store_hint`). Any of them could quote a stranger's messages back into chat — a cross-session data leak, not just a wrong answer.
- **Suggested Fix / Done:** `cuttle_chat_store_addon(current_session_id=…)` now scopes the recipe to the turn's chat (`This chat is CH-000155 → 155`, `sid=155`). With no id it emits a non-runnable placeholder plus "never reuse an id from an example". The handle→row mapping is taught generically (`CH-<zero-padded number>`). `_with_muse_chat_context` and `compile_context` thread `chat_session_id` through; `numeric_chat_session_id()` accepts only real handle shapes (`155`, `CH-000155`, `db_session_155`) so an unknown id degrades to "none" rather than a guess. Regression test: `src/tests/test_chat_store_session_scope.py` (fails on the old block with `[147]`).
- **Learning:** A prompt block that hands an agent runnable code must not contain sample identifiers. Agents execute examples.

### [ERR-20260816-009] cursor_cli_session_store — `/cursor` never resumed, so every turn was memory-less
- **Priority:** Critical · **Status:** Fixed (regression test added) · **Area:** cursor_cli_session_store / tool_manager
- **Summary:** The reason the CH-000155 agent went hunting for history at all: no `/cursor` turn in any authenticated web chat had stored a Cursor resume id since 2026-08-16 ~09:40, so each turn launched a brand-new Cursor session with zero memory of its own chat. Live proof: `agent -p … --workspace /path/to/Cuttle --output-format stream-json --model auto --stream-partial-output` on a mid-conversation turn — no `--resume`, and the once-per-session capability briefing re-injected. Asked "yeah let's do it," the agent had no antecedent and reconstructed one from another session's rows (see ERR-008 for how it chose which).
- **Error:** `AttributeError: 'int' object has no attribute 'strip'`, swallowed by `_persist_session`'s bare `except`.
- **Cause:** `save_cursor_resume_id()` guarded with `not (cuttle_session_id or "").strip()` — no `str()`. Auth chats pass the numeric DB id (`155`), so the save raised on every turn. `append_cursor_run_meta()` in the same store already coerced, which is why `recent_runs`/model badges kept working and the map entry looked healthy while `resume_id` was silently absent. Sessions ≤125 had ids because those turns reached the store through a path that passed the string form.
- **Context:** Verified by running the real CLI through `_cursor_agent_oneline_prompt`: string chat id → resume saved; int chat id → run succeeds, `resume_id` stays `None`. Identical to **ERR-20260816-001** in `gemini_cli_session_store` — that fix was not swept across the sibling stores.
- **Suggested Fix / Done:** Coerce with `str()` in `save_cursor_resume_id` (and in `codex_cli_session_store._session_key`, same latent hazard). `_persist_session` now logs the failure instead of swallowing it. Regression test `test_session_store_accepts_numeric_chat_id` in `src/tests/test_cursor_agent_slash_commands.py` covers int/str/`db_session_` id shapes plus the run-meta writer. End-to-end check: two real turns with an int chat id now recall a word set in the first.
- **Learning:** Fix a bug in one session store, grep the siblings the same hour — they are copy-paste relatives. And a resume store that silently degrades to "no memory" is worse than one that errors: the agent invents plausible context instead of reporting a gap.
- **See Also:** ERR-20260816-001, ERR-20260816-008, ERR-20260816-010

### [ERR-20260816-010] test coverage — the resume smoke suite could not see the agent that broke
- **Priority:** Critical · **Status:** Fixed (new contract suite) · **Area:** tests / agent_harness smoke
- **Summary:** ERR-009 ran unnoticed for a full day *with* resume smoke tests on the branch. Asked why the smoke didn't catch it, the honest answer is that it never looked at Cursor.
- **Cause (three gaps, all in the same suite):**
  1. **Wrong population.** `test_agent_harness_smoke.py` parametrizes over `list_agents()` — the harness catalog (gemini, opencode, antigravity). Cursor, Codex, Muse and Claude are the pre-harness dispatch path in `core/tool_manager.py` + `scripts/utilities/*_cli_session_store.py`, so no smoke test covered the agent doing ~every turn of real work.
  2. **Wrong direction.** `test_adapter_contract` exercised `load_resume` / `clear_resume` with an int id but never `save_resume`, and save was the broken half. Load worked fine the whole time — there was simply nothing to load.
  3. **Fake at the seam.** `test_kernel_resume_round_trip…` proves the kernel threads a resume id between turns, but through a `_FakeAdapter`, so no real store and no real argv were involved. Nothing asserted that `--resume` reached the command line.
- **Suggested Fix / Done:** `src/tests/test_agent_resume_contract.py` plus discovery helpers in `src/tests/conftest.py`:
  - `discover_resume_store_modules()` finds every `*session_store*.py` under `src/` (cursor, codex, gemini, muse, opencode, antigravity today) and `pytest_generate_tests` fans the contract over it, so a new agent's store is covered the moment the file exists — no opt-in step to forget.
  - Round trip asserts **save → load → clear** for every chat-id shape production passes (int DB id, digit string, `db_session_…`, opaque token), asserts the map file was actually written, and rejects a store that binds a blank session id.
  - Cursor argv end-to-end with a fake CLI: turn 1 has no `--resume` and persists the CLI session; turn 2 must launch with `--resume <uuid>`; a second chat must not inherit it.
  - Positive control (`test_contract_detects_an_int_hostile_store`) reinstalls the pre-fix guard and asserts the argv check fails, so the suite can't pass vacuously. Verified by dropping in a throwaway int-hostile store: discovery picked it up and failed on the int shape, both raising and silent-no-op variants.
  - Failure visibility: `test_cursor_resume_persist_failure_is_reported` pins the log line, since the bare `except` was what turned a crash into "the agent just forgets".
  - Opt-in live canary `test_live_cursor_resume_two_turn` (`CUTTLE_AGENT_SMOKE=1`) does two real Cursor turns and requires nonce recall.
- **Learning:** A smoke suite is scoped to the population it enumerates. When a migration introduces a new registry, the *old* paths silently leave coverage — the agents that carry the most traffic are the ones most likely to be outside the new catalog. Test the layer the user experiences (argv / CLI invocation), not just the store the code calls, and keep one deliberately-broken control so the assertion can't rot into a no-op.
- **See Also:** ERR-20260816-001, ERR-20260816-009

### [ERR-20260817-011] tool_manager — Cursor resume silently skipped when `status_queue` is None
- **Priority:** Critical · **Status:** Fixed (regression test added) · **Area:** tool_manager / agent_harness smoke
- **Summary:** Live harness smoke `test_live_resume_two_turn[cursor]` timed out on turn 2 with `resume_id: null`. Turn 1 answered "seeded" in ~10s but never wrote a Cursor UUID — because `_cursor_agent_oneline_prompt` set `use_stream = status_queue is not None`, and the smoke (and non-SSE `/api/chat`, Discord sync) pass no queue. Text mode never emits `session_id`, so `--resume` never appeared.
- **Error:** Turn 2: `[FAIL] Cursor Agent timed out after 180s.` Map entry had `recent_runs` but no `resume_id`.
- **Cause:** Coupling UI status streaming to the wire format required for resume. Dedicated `test_live_cursor_resume_two_turn` passed only because it passed a `Queue()`.
- **Suggested Fix / Done:** Always use stream-json; `_emit` already no-ops without a queue. Offline regression `test_cursor_run_saves_resume_even_without_status_queue`. Live Cursor + Muse smokes re-run green after the fix.
- **Learning:** Status UX and resume transport must not share a boolean. A smoke that omits `status_queue` is closer to non-SSE production than one that always injects a queue.
- **See Also:** ERR-20260816-009, ERR-20260816-010

### [ERR-20260818-012] agent_harness — project chip cwd ignored after harness (CH-000164)
- **Priority:** High · **Status:** Fixed (regression tests added) · **Area:** agent_harness / cursor resume
- **Summary:** Chat **CH-000164** had project chip Escape Purgatory (`/path/to/Escape-Purgatory`) but Cursor Agent ran with `--workspace /path/to/Cuttle`. The agent warned "cwd is still Cuttle" and EP rules did not auto-load.
- **Cause:** Two harness regressions stacked:
  1. Every adapter copied `os.getcwd()` as the fallback when resolving cwd (Flask's process cwd is Cuttle).
  2. Cursor `load_cursor_resume_binding` step 3 pinned `--workspace` to *any* cwd that already had a `--resume` UUID for the chat, so a Cuttle resume hijacked later EP turns. Kernel `save_resume` then wrote the same UUID under EP/source while `recent_runs` stayed on Cuttle.
- **Suggested Fix / Done:** Kernel owns cwd via `api.agent_harness.cwd.resolve_harness_cwd` and `constrain_to_project` so adapters cannot jump repos. Resume lookup is same-repo aliases only (`src/` / `source/`). Tests: `test_every_bundled_adapter_honors_project_chip`, `test_kernel_rejects_adapter_cwd_from_another_project`, `test_resume_binding_does_not_follow_other_project`.
- **Learning:** Resume is per (agent, chat, **project**). Preserving a CLI session is not worth running the wrong repo. Put cwd in the kernel so the next agent does not re-copy `getcwd()`.
- **See Also:** ERR-20260816-009

### [ERR-20260928-001] dashboards — stale performance-route test vs owner gate
- **Priority:** Medium · **Status:** Fixed (regression test added) · **Area:** dashboards / test_jev
- **Summary:** `test_jev.py::test_flask_performance_route` failed with `KeyError: 'id'`.
- **Error:** Test mounted `dashboards_bp` standalone with no auth context and asserted payload fields; the applied `@owner_required` gate returns 401 JSON with no `id`.
- **Context:** Workstream-1-adjacent cleanup gated the dashboards blueprint but did not update the test. Recorded separately per CH-000764; does not block settings extraction.
- **Suggested Fix / Done:** Rewrote as `test_flask_performance_route_requires_owner` asserting 401 for anonymous (gate contract). Service payload shape remains covered by `test_dashboards_catalog_performance_is_live`. Deliberately did not loosen the gate.

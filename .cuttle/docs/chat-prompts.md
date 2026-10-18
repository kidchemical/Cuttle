# Chat prompts and final-response processing

Settings → General → **System prompt** supplies shared instructions to chat
agents. Settings → Agents → expand an agent → **system prompt** adds instructions
for that agent. Shared instructions come first; agent-specific preferences take
precedence on conflicts. Blank fields clear the corresponding layer. This ships
directly as a basic settings capability; optional response processing is experimental.

Preferences apply to subsequent normal turns, including existing/resumed chats.
They supplement native CLI system instructions and project context rather than
replacing either. The harness kernel supplies a fresh preference snapshot after
context compilation on every execution; after a clear, an explicit empty snapshot
supersedes preferences earlier in the native conversation. A turn already running
keeps the instructions it received when it started. Background helpers and the
router's classification prompt do not inherit conversational prompt preferences.

## Response post-processing (experimental)

While the effective flag is off (including the process kill switch), the entire
post-processing settings section is hidden. It starts hidden while availability
loads and stays hidden on lookup failure. Changing the flag in Experimental
refreshes it without reloading; disabling preserves its saved preferences.

1. Enable **Response post-processing** in Settings → Experimental.
2. In Settings → Agents → Response post-processing, use the toggle to enable processing, enter
   instructions (for example, “Give me three TL;DR bullets”), and choose append
   or replace. Save these settings.
3. In that same section, add an executor and choose the API or restricted agent
   CLI, model, effort and optional fallback executors. Save post-processing.
   These selections are stored independently; Background helpers never supplies
   a default chain. An empty post-processing chain skips the pass.

Descriptions use the standard info tooltips. Append displays **Original response**
as a collapsed disclosure before the processed response; expanding it renders
the original Markdown. The same layout survives chat reload. Replace displays
only the processed reply.

The pass runs after a successful final reply, before saving, notification and
delivery. Sync and SSE responses, saved history and pending-result recovery all
receive the processed text. It adds model usage and latency, with a 90-second
total deadline across configured fallbacks. API usage uses the existing provider
ledger; CLI execution uses the helper's existing authentication and tool restrictions.

Failures or empty/invalid output retain the original reply. Failed/cancelled
turns, system notices, supervised-owned responses, interactive Cuttle cards,
and responses over 120,000 characters are skipped. Stop or superseding the turn
discards a late pass and prevents persistence/notification. No chat recursion,
native CLI session resume, or project tools are used for the extra pass.

In replacement mode the original text remains in assistant metadata under
`postprocessing.original_response`; metadata also records outcome and helper
usage. The displayed and saved message body is the replacement.

Owners: `api.chat_preferences` (validation/settings),
`api.response_postprocessing` (execution policy), `api.background_helpers`
(executor selection), `api.chat_turn_workflow` (claimed turn lifetime),
`api.settings_routes` (HTTP), and `settings_chat_preferences.js` (editors).
Settings keys: `chat_prompts` and `response_postprocessing` (including its own
`chain`). Legacy background task entries remain stored but never control this
pass. Routes:
`GET/POST /api/settings/chat-prompts` (partial global/agent patches),
`GET/POST /api/settings/response-postprocessing` (enabled/prompt/mode/chain),
`GET /api/settings/response-postprocessing/<kind>/<target>/models` (owner-only,
flag-gated shared executor discovery).
Reads require authentication; writes require owner access.

Tests: `test_chat_preferences.py`, `test_response_postprocessing.py`, kernel
prompt tests in `test_agent_harness.py`, settings authorization tests, and
isolated browser coverage in `e2e/test_settings_agents.py`. Tests use fake
executors and private state; no paid calls are made.

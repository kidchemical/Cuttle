# Direct model API providers

Settings → Agents → Background helpers uses the manifest-driven catalog
at `src/api/model_providers/<provider>/`, distinct from agent CLI adapters.
OpenAI, Anthropic, and user-managed local servers serve text operations. OpenAI,
ElevenLabs, and Google serve speech; OpenAI serves transcription. TypeSafe/OpenRouter
Jev decisions use their own typed operation in the same provider owner.
`api.background_helpers` selects a global execution chain and optional per-task
chains over API providers/local servers or installed agent CLIs. Claude Code and
Codex support restricted background text initially. Add each fallback explicitly;
only listed executors run. Unchecked task overrides inherit the global default.
With no new chain, existing provider/model/effort keys and task-specific defaults
continue working unchanged. API credentials remain under Providers.

Choose a provider to browse its model list. Discovery refreshes expired catalogs
when you open the picker; **Refresh models** forces a new account list and
supplemental models.dev metadata. The list excludes known non-text models and
labels unverified compatibility/access. Missing credentials or unavailable APIs
show suggestions or the last successful list. A successful empty account list
is not replaced with suggestions. **Custom model ID…** allows new IDs not yet
classified. **Provider default** clears a saved model override. Environment model
overrides supply the default when a chain has no explicit model; explicit chain
model IDs take precedence and remain visible in the editor.

The catalog caches cloud lists for one hour, local lists for one minute, and
models.dev metadata for 24 hours. Credentials and endpoint/account changes select
a different account cache. Refresh only discovers metadata; it never generates
content or performs paid capability probes. Catalogs indicate visibility and
known compatibility; they do not promise quota, billing, or a successful request.

Read-only agent CLI (from the repository root):

```bash
PYTHONPATH=src .venv/bin/python -m api.model_providers list
PYTHONPATH=src .venv/bin/python -m api.model_providers models openai --refresh
PYTHONPATH=src .venv/bin/python -m api.model_providers models google --operation speech_generation --refresh
PYTHONPATH=src .venv/bin/python -m api.model_providers voices elevenlabs --refresh
PYTHONPATH=src .venv/bin/python -m api.model_providers inspect anthropic claude-haiku-4-5-20251001
```

`list` and `inspect` never contact providers. `models` performs discovery as
needed. Inspect output includes per-field provenance and any cached native
thinking/effort/token-limit facts. Failed network requests produce sanitized
errors and preserve last-good data.

All direct Cuttle model inference now flows through this owner: background
helpers (titles, commits, prompt enhancement, routing), progress narration,
chat speech narration, vision prepass, server speech recognition, TTS, and Jev
judgments. Restricted CLI background text uses the existing agent adapters,
selected by the shared background owner. CLI subscription usage is reported as
CLI allowance rather than estimated direct API spend. Speech synthesis and vision
remain capability-specific API operations.
`core.local_llm` retains local queue/retry policy over an injected transport.
API key testing uses authenticated metadata reads, never paid completions.

Background helper chains offer model-specific **Reasoning effort** choices plus
**Automatic**, and **Temperature** with a blank **Task default** choice. New chain entries persist their own effort/temperature selections. Legacy API
controls retain per-provider/model effort maps (`completion_efforts`,
`chat_tts.summarize_efforts`). Native effort support takes
precedence over community metadata and fallback declarations. Haiku 4.5's
thinking-token budget is not a named effort level; Haiku 5.5's declared named
levels appear in the picker. Runtime validates an explicit selection against
cached capabilities before calling the provider. Higher effort may cost more.

Voice → Model uses account discovery filtered for speech, with refresh, custom
model ID, and default choices. Voices follow the chosen model: OpenAI's built-in
catalog is filtered for its older TTS models; ElevenLabs and Google discover
account voices with a one-hour cache and last-good/fallback handling. Legacy
Gemini PCM is wrapped as browser-playable WAV; Gemini 3.8 TTS uses Interactions.
Custom or saved IDs survive unavailable catalogs and are labeled unverified.

A model-list **401** is API authentication, independent of a CLI login or paid
CLI subscription. Replace the saved API key under Providers → API Keys, test it,
and refresh models. Account visibility still does not promise inference quota.

Image/video generation and project adapter drop-ins remain future work. Provider
folders and typed operation contracts allow additions without duplicating
credentials and discovery. Adding a provider:
`src/api/model_providers/ADDING_A_PROVIDER.md`.

This is a repair of the established Background helpers workflow, so it ships
without an experimental rollout flag. Python changes require the owner-selected
Flask restart; then refresh the Settings page.


## CLI subscription login and task controls

“Subscription login” means the existing vendor CLI login: `claude auth login`
using Claude.ai, or `codex login` using ChatGPT. It does not create a new Cuttle
account or API credential. Each helper receives a separate child environment
with Cuttle's API keys/provider overrides removed; the host environment and
native login files remain unchanged. Claude's metadata status must report a
Claude.ai login; Codex's account metadata must report ChatGPT before a prompt is
submitted. A disabled API key saved in Cuttle therefore cannot override this
helper login. “CLI saved login” allows whichever login the CLI itself has stored,
which may involve API billing. Helpers still consume the CLI plan's allowance.

Helpers use fresh isolated requests, tools/MCP/hooks/project context disabled,
no resumes, a shared concurrency limit of two CLI helpers, and one total deadline
across fallbacks. Process state is staged temporarily under the Cuttle home and
removed after cleanup. CLI output-token allowances are prompt hints, not API
hard generation caps; timeouts/output limits are enforced. Codex requires an
app-server that confirms an empty environment selection before inference.
Unsupported older CLIs fail without falling back to unrestricted coding execution.
Windows needs native CLI binaries instead of shell shims.

In Agents → Background helpers, choose **Global default** or a task, enable the
chain, choose executor/model, then adjust its supported effort/temperature and
**Save background helpers**. A task chain replaces the global chain. Narration
controls in Voice apply only when no background narration chain is configured;
Voice links to the execution editor whenever that chain is active. TTS audio
provider/model/voice choices remain in Voice and continue using model API adapters.

Temperature ranges and conditions live in provider capability manifests (plus
native/supplemental facts). Anthropic's older temperature-capable models use
0–1; OpenAI/local generally use 0–2. Known reasoning models disable temperature
or restrict it to specified efforts. CLI temperature is unavailable because the
initial connectors do not expose it. Requests validate explicit settings and
omit unsupported parameters. Blank preserves the domain caller's task default.

Router decisions respect the background chain for any enabled router mode;
router-off stays off and the classifier fast path remains unchanged. API providers
without a JSON response parameter can produce JSON text, validated before use.
CLI routing never re-enters chat dispatch.

Read-only background catalog verbs:

```bash
PYTHONPATH=src .venv/bin/python -m api.background_helpers list
PYTHONPATH=src .venv/bin/python -m api.background_helpers models cli claude --refresh
PYTHONPATH=src .venv/bin/python -m api.background_helpers inspect api openai gpt-4o-mini
```

Connector extension contract: `src/api/agent_harness/ADDING_AN_AGENT.md`,
“Optional restricted background text.”

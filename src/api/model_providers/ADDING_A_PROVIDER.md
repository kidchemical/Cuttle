# Model API providers

This package owns direct provider discovery and request translation. CLI agents
remain in `api.agent_harness.agents`; background-helper selection remains in
`api.completion_providers`. There are no imports into `web_chat_api`.

## Folder contract

Add `<id>/__init__.py`, `manifest.yaml`, and `adapter.py`, following the bundled
OpenAI, Anthropic, or local examples. The validated folder ID is the identity;
`adapter.py` exports `build_adapter()`. Bundled adapters load lazily. Project
Python drop-ins are not supported here.

Manifests declare schema version 1, ID, label, priority (fallback order),
credential environment names, model environment override, default model,
operations, `models_dev_provider`, default capabilities, ordered `model_rules`,
and fallback `models`. A model row can contain exact capability overrides.
Rules use case-insensitive shell globs; later matching rules win. Defaults,
rules, and exact records are validated before Python import.

All adapters implement `discovery_identity(manifest)` and `list_models(manifest)`.
Discovery returns normalized `{id, label?, lifecycle?, capabilities?}` records,
not SDK response objects. Account identity includes every credential, endpoint,
organization/project selector that changes access; it is hashed before storage
and never returned publicly. Construct fresh SDK clients so credential changes
reach the next request. Use bounded requests, SDK pagination, no retries, and
no inference probes during discovery.

`text_generation` adds `generate_text(manifest, TextRequest, capabilities)` returning
`TextResult(text, usage)`; `complete_text` is a compatibility facade.
`TextRequest` preserves the helper's prompt, system instruction, output budget,
temperature, timeout, legacy JSON-object requirement, named effort, and typed
image inputs. Prompts, image bytes, and secrets are not catalog metadata. The adapter translates
these to its wire protocol. Protocol details belong inside provider folders.
`speech_generation` adds `synthesize_speech(manifest, SpeechRequest, capabilities)`
returning `SpeechResult(audio, mime, usage)` and `list_voices(manifest, model)`.
The manifest's validated `speech` declaration owns fallback models/voices,
default model, speed range, stability, and `fetch_voices`. Account discovery
filters by operation while sharing a provider's full model snapshot.
`audio_transcription` adds `transcribe(manifest, TranscriptionRequest)`.
`structured_decision` adds `decide(manifest, DecisionRequest)` for Jev's native
question API. Jev retains answer parsing and configuration policy. `check_key`
uses an explicitly supplied credential for a read, without changing global env.
Future image/video generation and embedding execution should add separate typed
request/result contracts and operation-specific methods here. Do not overload text with arbitrary keyword dictionaries or split shared
credentials/discovery into separate modality registries.

## Capability merge and trust

Resolution is deterministic:

1. Provider manifest defaults.
2. Exact provider + model metadata from models.dev.
3. Normalized native provider model capabilities.
4. Ordered manifest rules for protocol exceptions.
5. Exact manifest model overrides.
6. Explicit native operation, thinking-type, and named-effort declarations are
   authoritative; native negative effort support clears supplemental levels.

Each field retains its source. Non-text modalities and retired models exclude
background text generation. The provider's supported operations cap every model.
Provider model lists establish account visibility, not successful inference or
billing/quota health. models.dev never establishes account availability, and
structured-output support does not imply legacy `json_object` support.

OpenAI `/v1/models` lacks endpoint capability metadata. Its manifest classifies
known text families and excludes specialized APIs. Unrecognized families remain
`unknown`, even when listed by the account. Known Codex/pro text models use
Responses; models requiring research/search/audio tools are excluded from this
background contract. A new protocol or family may need a manifest/adapter update.
Custom IDs remain possible; discovery is advice, not a frozen model allowlist.

Anthropic's Messages protocol no longer accepts non-default temperature after
Opus 4.6; manifest rules override potentially outdated community flags.
Anthropic's model capabilities provide thinking types, effort levels,
structured-output support, and token limits when supplied. Absence stays absent;
fallback levels for known models are documented protocol declarations;
native negative support takes precedence. Local Ollama discovery uses installed tags plus
bounded `/api/show` capability reads; other local servers use `/v1/models`.
Cuttle never starts servers, installs models, or loads/unloads them.

Reasoning output budgets include internal reasoning. The OpenAI adapter reserves
at least 4,096 output tokens for reasoning models (clamped to known model limits)
and chooses none/minimal/low only when that effort is declared. Anthropic uses
normal Messages without enabled thinking when possible, and uses adaptive/low
with the same allowance when native capabilities require thinking. These are
maximum budgets, not promises of cost; expensive models remain user choices.

## Cache and ownership

`service` owns account snapshots: one hour for cloud, one minute for local.
`models_dev` owns the shared raw feed: 24 hours. Failed automatic refreshes have
a one-minute retry cooldown; explicit refresh bypasses it. Failed refresh keeps
last-good records. A successful empty list stays empty. Missing credentials use
manifest suggestions with unverified access. No secrets/prompts are persisted.
Snapshots live in `<cuttle_home>/cache/` with atomic writes. Runtime completions
only read cached capabilities and never synchronously fetch catalogs. Pricing
continues owning its derived index and consumes the same raw models.dev feed.

Model discovery is owner-only because it uses server credentials. Voice library
reads preserve the established authenticated TTS route. The general
provider settings read remains authenticated and performs no remote discovery.
The UI preserves saved/custom IDs, labels unknown compatibility/access, filters
unsupported models, and rejects stale async responses after switching providers.

Add offline discovery, capability, request-shape, cache, auth, and picker tests.
Do not use paid prompts as model capability probes. See `tests/providers/` and
`tests/e2e/test_settings_model_providers.py`, `test_settings_voice_providers.py`,
and `tests/providers/test_provider_operations.py`. The latter guards SDK and
inference call ownership across `api`, `core`, and `managers`.

## Reference sources

- [OpenAI model list](https://developers.openai.com/api/reference/resources/models/methods/list)
- [OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)
- [OpenAI Responses](https://developers.openai.com/api/reference/resources/responses/methods/create)
- [Anthropic model list and capabilities](https://platform.claude.com/docs/en/api/models/list)
- [models.dev schema and feed](https://github.com/anomalyco/models.dev/blob/dev/README.md)

- [Anthropic effort](https://platform.claude.com/docs/en/build-with-claude/effort)
- [OpenAI speech](https://developers.openai.com/api/docs/guides/text-to-speech)
- [ElevenLabs model discovery](https://elevenlabs.io/docs/api-reference/models/list)
- [Google legacy speech](https://ai.google.dev/gemini-api/docs/generate-content/speech-generation)
- [Google Interactions speech](https://ai.google.dev/gemini-api/docs/speech-generation)
- [Google voice discovery](https://ai.google.dev/api/voices)

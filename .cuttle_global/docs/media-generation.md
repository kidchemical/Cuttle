# Generate speech and images

`python -m api.media_generate` lets any Cuttle agent CLI generate speech or an
image using the configured model APIs. Run it using the Cuttle checkout's Python
environment and `src` path as shown in [Agent ops](agent-ops-cli.md).

## Cloud generation

Speech uses the OpenAI, ElevenLabs, and Google adapters. Choose a provider and
voice ID. Use `--model` for a specific model, or omit it for the provider's
configured manifest default. Images use the OpenAI GPT Image API. Choose the
model explicitly. One invocation makes one generation request.

```bash
PYTHONPATH=src .venv/bin/python -m api.model_providers voices elevenlabs
PYTHONPATH=src .venv/bin/python -m api.model_providers models openai --operation image_generation
PYTHONPATH=src .venv/bin/python -m api.media_generate speech --provider elevenlabs \
  --voice VOICE_ID --model eleven_multilingual_v2 --input-file narration.txt --name narration
PYTHONPATH=src .venv/bin/python -m api.media_generate image --provider openai \
  --model gpt-image-2.6-flare --input-file prompt.txt --name thumbnail --share
```

The CLI reads UTF-8 text from `--input-file` or from stdin. Speech retains the
provider's format (typically MP3 for OpenAI or ElevenLabs and WAV for Google).
Images are PNG. Files default to `<cuttle_home>/generated-media/`. The version 1
JSON receipt includes `artifact.path`, `artifact.mime`, `artifact.bytes`,
and usage when provided. `--share` also returns `artifact.url` for signed-in
Cuttle clients. Embed image URLs with Markdown and link audio URLs. If image
staging fails, the receipt returns the saved file and `artifact.share_error`.
Retry sharing the saved file with
`python -m api.shared_media stage <path> --json` ([chat media](chat-media.md)).

Keys follow existing provider Settings and environment conventions. A caller
can set a key for just this process using the Cuttle secret vault's `run --env`.
Keep keys out of arguments. Hosted generation sends the supplied text to the
chosen provider and may use API credits. OpenAI's legacy Speech API models are
[scheduled for shutdown](https://developers.openai.com/api/docs/guides/text-to-speech)
on January 6, 2027. Choose Google or ElevenLabs for a new batch speech workflow
that needs to run past that date.

The CLI has no daemon or HTTP generation route. Cuttle owns artifact storage,
sharing, and the existing provider usage ledger. The adapter owns the vendor
request. Removing this CLI leaves existing media files and ledger entries
readable. Local models and their runtimes are managed by the installation.

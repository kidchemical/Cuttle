# Setup Wizard and Doctor

Cuttle provides a **Setup Wizard** (status) and **Doctor** (config/health checks) to guide configuration and troubleshoot issues.

## Wizard

The wizard shows which setup steps are done:

1. **Default pipeline** – A default pipeline is set and its file exists.
2. **API keys** – At least one of `OPENAI_API_KEY`, `API_KEY`, or `ANTHROPIC_API_KEY` is set in the environment.
3. **Channels** – Channel config (e.g. allowFrom) is present for webchat or discord.

- **GET /api/wizard/status** – Returns `{ steps: { default_pipeline, api_keys, channels }, next_step }`. `next_step` is one of `default_pipeline`, `api_keys`, `channels`, or `done`.
- **Wizard page** – Open `/wizard_page.html` in the browser to see status and run Doctor.

## Doctor

Doctor runs config and health checks and returns suggestions.

- **GET /api/doctor** – Returns `{ success, checks: [ { name, status, message } ], suggestions: [ ] }`. `status` is `ok`, `warn`, or `fail`.

**Checks:**

- **pipelines_dir** – `src/pipelines` exists.
- **default_pipeline** – Default pipeline file exists.
- **trigger_webchat** – At least one pipeline has a trigger-webchat node.
- **trigger_discord** – At least one pipeline has a trigger-discord node.
- **data_dir** – `src/data` is writable (auth, pairing store).
- **api_keys** – At least one LLM API key in environment.
- **pipeline_executor** – Pipeline executor module loads.

Use Doctor after setup or when Web Chat/Discord have no handler or pipelines fail.

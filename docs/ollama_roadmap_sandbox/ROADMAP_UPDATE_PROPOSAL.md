# Roadmap documentation update (archive)

**Status:** Historical. Public roadmap is [docs/ROADMAP.md](../../ROADMAP.md). This file records an older Ollama-sandbox before/after.

## Summary

- **Issue:** ROADMAP still called `llm-local` a “dead stub” and listed a Phase 1 row to implement it, while Cuttle already runs Ollama through `nodeType` `llm-local` and Flask **`POST /api/llm-request`** (`web_chat_api.py`).
- **Resolution:** Updated confirmed node types, OpenClaw comparison “Model support”, Phase 1 table row, and aligned the **Current State** LLM row with Ollama. Refreshed approximate LOC for `web_chat_api.py`, `pipeline_trigger_executor.py`, and `bot_mcp.py` so the summary table is not misleading.

## Edits applied (reference)

### (a) Confirmed node types

- **Before:** `llm-local (dead stub)`
- **After:** `llm-local (local Ollama via OpenAI-compatible /api/llm-request)` (wording may be tightened further in the main doc)

### (b) Phase 1 table

- **Before:** Row “Complete dead `llm-local` Ollama stub (~30 lines)” pointing at `~line 4139`
- **After:** Single clear **Done** row naming `llm-local`, Ollama, and `/api/llm-request` without relying on stale line numbers

### (c) OpenClaw comparison

- Cuttle column for **Model support** updated so it does not claim “Ollama stub dead” and notes local Ollama path.

## Review (Cursor)

- **Accuracy:** Matches code paths (`llm_request`, `node_type == 'llm-local'`, Ollama OpenAI-compatible client in `web_chat_api.py`).
- **Honesty:** Comparison row mentions serialization (Ollama one-at-a-time) so readers do not expect OpenClaw-scale local throughput.
- **Maintainability:** Phase 1 row avoids `~~strikethrough~~` inside a dense table cell; LOC uses `~` to reduce churn.

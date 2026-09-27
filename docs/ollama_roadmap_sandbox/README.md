# Ollama roadmap experiment (sandbox)

**Purpose:** Local LLM (Ollama) + MCP should **only** create or overwrite files under this folder (`docs/ollama_roadmap_sandbox/`) when testing pipeline-driven doc updates.

**Canonical roadmap:** [`docs/ROADMAP.md`](../../ROADMAP.md). [ROADMAP_UPDATE_PROPOSAL.md](ROADMAP_UPDATE_PROPOSAL.md) is an archive of an older Ollama-sandbox pass.

Paths in magic-line / MCP fallbacks are anchored to the **Cuttle repo root** (parent of `src/`), not the Flask worker cwd — see `_try_direct_file_tool_fulfillment` in `web_chat_api.py`.

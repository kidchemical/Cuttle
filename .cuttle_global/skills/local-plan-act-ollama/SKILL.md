---
name: local-plan-act-ollama
description: >-
  Local coding in Cuttle: use llama.cpp or a guest CLI. Do not wire Ollama
  as an MCP tool server. Agent ops are python -m api.*.
---

# Local LLM

Cuttle does not host an MCP tool server. For local coding use **llama.cpp** or
a guest CLI (`/hermes`, `/cursor`, …). Cuttle-owned agent ops are
`python -m api.*` (`.cuttle_global/docs/agent-ops-cli.md`).

Ollama remains an optional `/api/llm-request` backend (serialized, one model
at a time). Do not rebuild pipeline-node tool loops around it.

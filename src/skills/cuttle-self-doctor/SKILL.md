---
name: cuttle-self-doctor
description: >-
  Local LLM self-service (list models) and inspecting Cuttle execution via
  query reports. Use for live self-doctoring, debugging weak tool use, or when the
  agent should fix its own trajectory.
---

# Cuttle self-doctor (local tools + trajectory)

## Tool-capable local model

Cuttle's **Local LLM** node (`provider: local`) uses the configured backend (`LOCAL_LLM_BACKEND` in `src/.env` — **llama.cpp** or **Ollama**). Model `"default"` / `"local-default"` resolves via `src/core/local_llm.py` (`resolve_local_model`). With MCP `toolsConfig` present, prefer models that support native `tool_calls` (e.g. Qwen 2.5/3 Coder on llama.cpp with `--jinja`).

Wire **MCP Toolset** to the LLM node's **tools** input so definitions reach the model.

## Manage local models (MCP)

| Tool | Use |
|------|-----|
| **ollama_list_models** | List models on the active local backend (llama.cpp `/v1/models` or Ollama). |
| **ollama_pull_model** | Download a model via **Ollama only** (`model_name`, e.g. `qwen2.5:latest`). Not available when `LOCAL_LLM_BACKEND=llamacpp`. |

These are real MCP tools—call them; do not print fake `ollama run` / bash blocks or invent connection errors.

## Inspect your own run (trajectory)

After a pipeline or chat turn, Cuttle can write **query reports**:

- Path pattern: `src/web/logs/query_report_<QUERY_ID>.html` (eight-character id).
- Use **read_file** (MCP) from the project root to open the HTML. It summarizes LLM calls, tool calls, timings, tokens, and the execution graph.

If the user gives a Query ID, read that file before guessing what went wrong.

## Plan-then-act (local)

On **llm-local**, enable **Reasoning loop**, **Plan-then-tools** (`reasoningLoopWithTools`), and **Reasoning passes** (e.g. 3). See skill **local-plan-act-ollama** (`.cursor/skills/local-plan-act-ollama/SKILL.md`).

## Shared coaching text

The same self-doctoring hints are injected automatically whenever MCP tools are attached (`src/core/mcp_tool_coaching.py`), so agents do not rely only on this skill file at runtime.

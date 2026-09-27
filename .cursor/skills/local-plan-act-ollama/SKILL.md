---
name: local-plan-act-ollama
description: >-
  Historical note: Cuttle Local LLM (Ollama) used to plan-then-act when MCP
  toolsets were wired to pipeline nodes. Cuttle-as-MCP-server is retired.
  Prefer guest harnesses or llama.cpp local; do not rebuild Ollama MCP tools.
---

# Local Ollama: planning, tools, and acting

**Status (2026-09):** Cuttle no longer starts `run_cuttle_mcp.py` or offers MCP
packs to LLM nodes. Pipeline “MCP Toolset → Ollama” is a dead path. For local
coding use llama.cpp / a guest CLI. Agent-facing Cuttle work is
`python -m api.*` (`.cuttle/docs/agent-ops-cli.md`).

The rest of this file is historical (node-editor reasoning loop + old MCP tools).

## What Cuttle does

1. **No MCP tools** — **Reasoning loop** on the **Local LLM (Ollama)** node runs `reasoningRounds − 1` internal passes, then one final pass (text only). Same as before.

2. **MCP Toolset connected** — Enable **Plan-then-tools** (`reasoningLoopWithTools`) on that node. Cuttle runs `reasoningRounds − 1` **planning passes with tools omitted** (pure text planning), then **one** `/api/llm-request` with **full tools** and a prompt that includes the plan. Inside that request, Ollama can loop on tool calls (up to 10 rounds) like OpenAI.

3. **Ollama tool choice** — First and follow-up chat completions use `tool_choice: auto` so local models can emit a final natural-language answer after tool results; `required` often caused flaky or endless tool-only behavior.

## Node editor settings

| Setting | Role |
|--------|------|
| **Reasoning loop** | Master switch for multi-pass behavior. |
| **Plan-then-tools** | Only matters when an MCP Toolset wire feeds the node: plan without tools, then act with tools. |
| **Reasoning passes** | `N`: `N−1` planning/reasoning passes, then 1 final pass. Try **3** for Self Improvement–style tasks. |

Implementation: Ollama path in `src/api/web_chat_api.py` (`/api/llm-request`). The old graph executor is gone.

## Self Improvement

`src/pipelines/Self_Improvement.json` — **Owner** runs are routed to **Claude Code** (`tool-remote-agent`) when `pipeline_name` is `Self_Improvement` and the router has **`localLlmOnly: false`** (see `web_chat_api._execute_router_tool`). Ollama remains a **fallback** only. For **local-only** ad-hoc experiments, you could set `localLlmOnly: true` and use **Ollama** with `reasoningLoopWithTools: true`, `reasoningRounds: 3`—but that path is weaker than Claude Code for real repo edits.

Prefer a **tools-capable** Ollama model in Cuttle settings (**preferred tools Ollama model**); default in code is **`qwen2.5:latest`**—pull via **ollama_pull_model** if needed. Plain **llama3** often narrates tools instead of emitting `tool_calls`. See skill **cuttle-self-doctor** (`src/skills/cuttle-self-doctor/SKILL.md`) for list/pull models and reading **query_report_*.html** trajectories.

## Limitations

- Planning passes are **extra Ollama calls** (latency + queue behind `_ollama_request_lock`).
- Tool quality is still bounded by the local model and MCP tool definitions.
- This is not a full autonomous coding agent like Claude Code; it is **plan → tool loop → answer** inside one pipeline step.

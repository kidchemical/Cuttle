"""
Bundled tools for LLM tool-calling: local CLIs (Claude Code) and API-style
delegates (Ollama) — wired via tool-cli-toolset / tool-api-toolset, not separate graph nodes.

Tool names are prefixed with cuttle_ to avoid collisions with MCP tools.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from typing import Any, Dict, List, Optional

BUNDLED_TOOL_NAMES = frozenset(
    {
        "cuttle_claude_code",
        "cuttle_ollama_ask",
    }
)


def merge_bundled_cli_configs(configs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not configs:
        return None
    out: Dict[str, Any] = {
        "claude": False,
        "claudeModel": "haiku",
        "project": "sandbox",
        "customPath": "",
    }
    for c in configs:
        if c.get("enableClaudeCode"):
            out["claude"] = True
        if c.get("claudeModel"):
            out["claudeModel"] = str(c["claudeModel"]).strip()
        if c.get("project"):
            out["project"] = str(c["project"]).strip()
        if c.get("customPath"):
            out["customPath"] = str(c["customPath"]).strip()
    if not out["claude"]:
        return None
    return out


def merge_bundled_api_configs(configs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not configs:
        return None
    out: Dict[str, Any] = {
        "ollama": False,
        "ollamaModel": "default",
        "temperature": 0.4,
        "maxTokens": 2048,
    }
    for c in configs:
        if c.get("enableOllama"):
            out["ollama"] = True
        if c.get("ollamaModel"):
            out["ollamaModel"] = str(c["ollamaModel"]).strip()
        if c.get("temperature") is not None:
            try:
                out["temperature"] = float(c["temperature"])
            except (TypeError, ValueError):
                pass
        if c.get("maxTokens") is not None:
            try:
                out["maxTokens"] = int(c["maxTokens"])
            except (TypeError, ValueError):
                pass
    if not out["ollama"]:
        return None
    return out


def bundled_tool_specs_openai(
    bundled_cli: Optional[Dict[str, Any]],
    bundled_api: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    specs: List[Dict[str, Any]] = []
    if bundled_cli:
        if bundled_cli.get("claude"):
            specs.append(
                {
                    "type": "function",
                    "function": {
                        "name": "cuttle_claude_code",
                        "description": (
                            "Run Anthropic Claude Code CLI on the configured project directory. "
                            "Use for multi-file edits, repo tasks, and shell-capable coding work. "
                            "Pass a clear natural-language instruction."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "prompt": {
                                    "type": "string",
                                    "description": "Instruction for Claude Code (what to do in the repo).",
                                }
                            },
                            "required": ["prompt"],
                        },
                    },
                }
            )
    if bundled_api and bundled_api.get("ollama"):
        specs.append(
            {
                "type": "function",
                "function": {
                    "name": "cuttle_ollama_ask",
                        "description": (
                            "Ask the local LLM a focused sub-question (one-shot, no tools). "
                            "Uses the configured local backend (llama.cpp or Ollama). "
                            "Use for cheap local reasoning, summarization, or a second opinion without calling cloud APIs."
                        ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "prompt": {
                                "type": "string",
                                "description": "Question or instruction for the local model.",
                            },
                            "model": {
                                "type": "string",
                                "description": "Optional Ollama model id (overrides toolset default when set).",
                            },
                        },
                        "required": ["prompt"],
                    },
                },
            }
        )
    return specs


def _resolve_project_dir(bundled_cli: Dict[str, Any], project_root: str) -> str:
    project = (bundled_cli.get("project") or "sandbox").strip()
    custom = (bundled_cli.get("customPath") or "").strip()
    if project == "custom" and custom:
        return os.path.abspath(os.path.expanduser(custom))
    if project == "pc_bot":
        return project_root
    if project == "current":
        return os.getcwd()
    d = os.path.join(tempfile.gettempdir(), "cuttle_sandbox")
    os.makedirs(d, exist_ok=True)
    return d


def invoke_bundled_tool(
    name: str,
    arguments: Dict[str, Any],
    bundled_cli: Optional[Dict[str, Any]],
    bundled_api: Optional[Dict[str, Any]],
    *,
    project_root: str,
    session_id: Optional[str] = None,
) -> str:
    """Execute one bundled tool; returns text for the tool_result message."""
    prompt = (arguments or {}).get("prompt") or ""
    prompt = str(prompt).strip()
    if name == "cuttle_claude_code" and not prompt:
        return "Error: missing prompt"

    if name == "cuttle_claude_code":
        if not bundled_cli or not bundled_cli.get("claude"):
            return "Error: Claude Code is not enabled on the CLI toolset for this pipeline."
        from scripts.utilities.claude_code_tool import ClaudeCodeTool

        model = bundled_cli.get("claudeModel") or "haiku"
        sid = (session_id or "bundled")[:12]
        tool = ClaudeCodeTool(session_id=sid, model=model)
        cwd = _resolve_project_dir(bundled_cli, project_root)
        result = asyncio.run(tool.execute_claude_command(prompt, cwd))
        if result.get("success"):
            return str(result.get("output") or "")
        return f"Error: {result.get('error', 'claude failed')}\n{result.get('output', '')}"

    if name == "cuttle_ollama_ask":
        if not bundled_api or not bundled_api.get("ollama"):
            return "Error: local LLM delegate is not enabled on the API toolset for this pipeline."
        from openai import OpenAI as _OAI
        from core.local_llm import get_local_base_url, get_local_api_key, resolve_local_model, get_local_label

        ollama_base = get_local_base_url()
        local_label = get_local_label()
        model_arg = (arguments or {}).get("model")
        requested = str(model_arg).strip() if model_arg else (bundled_api.get("ollamaModel") or "default")
        ollama_model = resolve_local_model(requested, with_tools=False)
        temp = float(bundled_api.get("temperature") or 0.4)
        max_tok = int(bundled_api.get("maxTokens") or 2048)
        client = _OAI(base_url=ollama_base, api_key=get_local_api_key())
        try:
            resp = client.chat.completions.create(
                model=ollama_model,
                messages=[
                    {"role": "system", "content": "You are a helpful assistant. Answer concisely."},
                    {"role": "user", "content": prompt},
                ],
                temperature=temp,
                max_tokens=max_tok,
                timeout=120,
            )
            return resp.choices[0].message.content or ""
        except Exception as e:
            return f"Error: local LLM ({local_label}) sub-call failed: {e}"

    return f"Error: unknown bundled tool {name}"

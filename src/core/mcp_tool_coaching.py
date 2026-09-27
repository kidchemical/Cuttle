"""
System-prompt addendum when an LLM has **bundled** OpenAI-style tools.

Name is historical (pipeline MCP toolsets). Cuttle-as-MCP-server is archived
(`docs/archive/deprecated-cuttle-mcp/`). This module is only prompt text.
"""


def mcp_tools_system_prompt_suffix() -> str:
    try:
        from core.local_llm import get_local_backend, get_local_label
        local_backend = get_local_backend()
        local_label = get_local_label()
    except Exception:
        local_backend = "ollama"
        local_label = "Ollama"

    local_models_tool = "ollama_list_models"
    local_pull_tool = "ollama_pull_model"
    bundled_local_tool = "cuttle_ollama_ask"
    if local_backend == "llamacpp":
        local_hint = (
            f"\n- **Local LLM ({local_label})**: The active backend is llama.cpp "
            f"(llama-server). Use **{local_models_tool}** to see the loaded model. "
            f"**{local_pull_tool}** is only for Ollama — do not call it when the backend is llama.cpp."
        )
    else:
        local_hint = (
            f"\n- **Local LLMs ({local_label})**: Call **{local_models_tool}** to see installed models. "
            f"To download one, call **{local_pull_tool}** with **model_name** (e.g. `qwen2.5:latest`); "
            "large pulls can take several minutes."
        )

    return (
        "\n\nYou have access to MCP tools (e.g. read_file, write_file, pipeline_list, "
        "pipeline_run_now, ollama_list_models, ollama_pull_model) and may also have Cuttle **bundled** tools: "
        "**cuttle_claude_code** (coding CLI in the project directory), "
        f"and **{bundled_local_tool}** (a one-shot local LLM sub-call via {local_label}). "
        "You always have **cuttle_feed_preferences** to read or update the user's home-feed topics, sources, and interests "
        '(e.g. "add New York Times", "more UFO YouTube"). '
        "When the user's request can be fulfilled or clarified by calling a tool, call the tool first—do not guess, "
        "refuse, or imitate tools with bash, markdown code fences, or prose commands. "
        "For casual greetings or small talk, reply briefly without invoking tools. "
        "Use the native tool-calling API only. "
        "After each tool result, reason briefly, then answer the user or call another tool if needed."
        "\n\n**Cuttle self-service (live self-doctoring):**"
        + local_hint
        + "\n- **Execution trajectory**: Each pipeline/chat run can be inspected via **read_file** on "
        "`src/web/logs/query_report_<QUERY_ID>.html` (8-character id from the user, Jobs UI, or response metadata). "
        "That report lists LLM calls, tool calls, durations, and errors—use it to debug your own behavior or Cuttle routing."
    )

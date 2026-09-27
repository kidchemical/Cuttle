"""System-prompt addendum when an LLM has bundled OpenAI-style tools (not MCP)."""


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
        "\n\nYou may have Cuttle **bundled** tools: "
        "**cuttle_claude_code** (coding CLI in the project directory), "
        f"and **{bundled_local_tool}** (a one-shot local LLM sub-call via {local_label}). "
        "When the user's request can be fulfilled or clarified by calling a tool, call the tool first—do not guess, "
        "refuse, or imitate tools with bash, markdown code fences, or prose commands. "
        "For casual greetings or small talk, reply briefly without invoking tools. "
        "Use the native tool-calling API only. "
        "After each tool result, reason briefly, then answer the user or call another tool if needed."
        "\n\n**Cuttle self-service:**"
        + local_hint
    )

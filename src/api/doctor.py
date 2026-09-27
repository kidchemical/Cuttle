"""
Doctor: config and health checks for Cuttle.
Runs checks (pipeline files, default pipeline, auth, ports, env) and returns suggestions.
"""

import os
from pathlib import Path
from typing import Dict, List, Any

# Project root (src)
PROJECT_ROOT = Path(__file__).parent.parent


def run_checks() -> Dict[str, Any]:
    """Run all doctor checks. Returns { success, checks: [...], suggestions: [...] }."""
    checks: List[Dict[str, Any]] = []
    suggestions: List[str] = []

    # 1. Visual graphs removed — chat uses slash agents + router
    pipelines_dir = PROJECT_ROOT / "pipelines"
    checks.append({
        "name": "pipelines_dir",
        "status": "ok",
        "message": (
            "Visual pipeline graphs were removed; slash agents + router handle chat"
            + (f" ({pipelines_dir} still on disk)" if pipelines_dir.exists() else "")
        ),
    })
    checks.append({
        "name": "default_pipeline",
        "status": "ok",
        "message": "No default graph required",
    })
    checks.append({
        "name": "trigger_webchat",
        "status": "ok",
        "message": "Web Chat uses slash agents (no graph trigger)",
    })
    checks.append({
        "name": "trigger_discord",
        "status": "ok",
        "message": "Discord DMs use slash agents (legacy URL /api/pipeline-trigger-discord)",
    })
    checks.append({
        "name": "default_pipeline_webchat",
        "status": "ok",
        "message": "No default webchat pipeline required",
    })

    # 4. Auth DB / pairing store writable
    data_dir = PROJECT_ROOT / "data"
    data_ok = data_dir.exists() or True  # will be created on first use
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        test_file = data_dir / ".doctor_write_test"
        test_file.write_text("ok")
        test_file.unlink()
        data_ok = True
    except Exception as e:
        data_ok = False
    checks.append({
        "name": "data_dir",
        "status": "ok" if data_ok else "fail",
        "message": "Data directory writable" if data_ok else f"Data directory not writable: {e}",
    })
    if not data_ok:
        suggestions.append("Ensure src/data exists and is writable (for auth and pairing store).")

    # 5. API keys (env) – optional
    openai_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("API_KEY")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    has_llm_key = bool(openai_key or anthropic_key)
    checks.append({
        "name": "api_keys",
        "status": "ok" if has_llm_key else "warn",
        "message": "At least one LLM API key set (OPENAI_API_KEY or ANTHROPIC_API_KEY)" if has_llm_key else "No OPENAI_API_KEY or ANTHROPIC_API_KEY in environment",
    })
    if not has_llm_key:
        suggestions.append("Set OPENAI_API_KEY or ANTHROPIC_API_KEY in .env or environment for cloud LLMs.")

    # 6. Chat backend (Flask import)
    try:
        from api import web_chat_api  # noqa: F401
        checks.append({"name": "chat_backend", "status": "ok", "message": "Chat API importable"})
    except Exception as e:
        checks.append({"name": "chat_backend", "status": "fail", "message": str(e)})
        suggestions.append("Fix Flask chat API import (check dependencies).")

    success = all(c.get("status") == "ok" for c in checks) or not any(c.get("status") == "fail" for c in checks)
    return {
        "success": success,
        "checks": checks,
        "suggestions": suggestions,
    }

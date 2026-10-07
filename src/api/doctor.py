"""
Doctor: config and health checks for Cuttle.
Runs checks for auth, ports, environment, and chat backend availability.
"""

import os
from typing import Dict, List, Any


def run_checks() -> Dict[str, Any]:
    """Run all doctor checks. Returns { success, checks: [...], suggestions: [...] }."""
    checks: List[Dict[str, Any]] = []
    suggestions: List[str] = []

    # 4. Auth DB / pairing store writable
    from core.runtime_paths import cuttle_home

    data_dir = cuttle_home()
    error = ""
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        test_file = data_dir / ".doctor_write_test"
        test_file.write_text("ok")
        test_file.unlink()
    except Exception as e:
        error = str(e)
    data_ok = not error
    checks.append({
        "name": "data_dir",
        "status": "ok" if data_ok else "fail",
        "message": f"Cuttle home writable ({data_dir})" if data_ok else f"Cuttle home not writable ({data_dir}): {error}",
    })
    if not data_ok:
        suggestions.append(f"Ensure {data_dir} is writable, or set CUTTLE_HOME to a writable folder.")

    # 5. API keys (env) – optional
    openai_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("API_KEY")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    has_llm_key = bool(openai_key or anthropic_key)
    checks.append({
        "name": "api_keys",
        "status": "ok",
        "message": "At least one LLM API key set (OPENAI_API_KEY or ANTHROPIC_API_KEY)" if has_llm_key else "Optional direct API providers unconfigured; agent CLIs use native authentication",
    })

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

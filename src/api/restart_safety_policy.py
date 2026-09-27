"""
Canonical restart-safety policy shared across harness adapters.

Hard enforcement vs advisory injection is declared explicitly so tests can
fail if a harness is silently treated as protected.
"""

from __future__ import annotations

from typing import Any, Dict, List

# Short block injected into agent prompts (Cursor/Codex/Hermes/Claude/Gemini).
RESTART_SAFETY_BRIEF = """\
Flask / daemon restart safety (Cuttle-managed processes):
- NEVER taskkill / Stop-Process the Flask (web_chat_api), cuttle_daemon, or Discord bot processes to "restart" them.
- Use `/restart graceful`, `/restart when-idle`, or `/restart force --yes` (or POST /api/flask/restart).
- The daemon owns stop/start/health. Killing Flask from an agent destroys the chat delivering your reply.
- Unrelated taskkill of non-Cuttle processes is fine.
""".strip()

DENY_MESSAGE = (
    "Direct termination of Cuttle-managed processes is blocked.\n"
    "Use /restart graceful or /restart when-idle."
)

# Explicit coverage map — update when adding harnesses.
HARNESS_COVERAGE: Dict[str, Dict[str, Any]] = {
    "cursor": {
        "hard_enforcement": "beforeShellExecution",
        "hard_path": ".cursor/hooks.json → block_cuttle_managed_kill.py",
        "advisory": [
            ".cursor/rules/restart-cuttle.mdc",
            "cuttle_ui_capabilities / restart_safety_brief",
            "AGENTS.md",
        ],
        "notes": "Project hooks apply when agent --workspace is Cuttle (or a project that inherits these hooks).",
    },
    "codex": {
        "hard_enforcement": None,
        "hard_path": None,
        "advisory": [
            "AGENTS.md",
            "cuttle_ui_capabilities / restart_safety_brief (injected on /codex prompts)",
        ],
        "notes": "Codex CLI has no Cuttle-owned before-shell hook; policy is advisory only.",
    },
    "hermes": {
        "hard_enforcement": None,
        "hard_path": None,
        "advisory": [
            "cuttle_ui_capabilities / restart_safety_brief (injected on /hermes prompts)",
        ],
        "notes": "Hermes oneshot has no Cuttle shell gate; advisory only.",
    },
    "claude": {
        "hard_enforcement": None,
        "hard_path": None,
        "advisory": [
            "AGENTS.md",
            "cuttle_ui_capabilities / restart_safety_brief",
        ],
        "notes": "Claude Code may read AGENTS.md; no Cuttle shell intercept.",
    },
    "deepseek": {
        "hard_enforcement": None,
        "hard_path": None,
        "advisory": [
            "cuttle_ui_capabilities / restart_safety_brief (injected on /deepseek prompts)",
        ],
        "notes": "DeepSeek Harness headless has no Cuttle shell gate; advisory only.",
    },
    "cuttle_shell": {
        "hard_enforcement": None,
        "hard_path": None,
        "advisory": [],
        "notes": "Pipeline tools.shell archived; Cursor CLI still gated by beforeShellExecution.",
    },
}


def harness_coverage_report() -> Dict[str, Any]:
    return {
        "harnesses": dict(HARNESS_COVERAGE),
        "hard_enforced": sorted(
            k for k, v in HARNESS_COVERAGE.items() if v.get("hard_enforcement")
        ),
        "advisory_only": sorted(
            k for k, v in HARNESS_COVERAGE.items() if not v.get("hard_enforcement")
        ),
    }


def assert_harness_not_silently_protected(name: str) -> Dict[str, Any]:
    """Return coverage for ``name``; raise if unknown (do not invent protection)."""
    key = (name or "").strip().lower()
    if key not in HARNESS_COVERAGE:
        raise KeyError(
            f"Unknown harness `{name}` — not listed in restart_safety_policy. "
            "Do not treat it as protected."
        )
    return dict(HARNESS_COVERAGE[key])


def restart_safety_for_capabilities() -> str:
    return RESTART_SAFETY_BRIEF

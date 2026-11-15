"""Reply for a routed turn that found no agent CLI to run.

Cuttle hosts vendor agent CLIs and never installs them. On a new install a
plain message walks the whole chain and every target answers "CLI not found";
that is a setup step, so the reply says what to install instead of listing
failed attempts.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _installed_agents(project_path: Optional[str]) -> List[Dict[str, Any]]:
    try:
        from api.agent_harness.catalog import public_catalog

        return [row for row in public_catalog(project_path) if row.get("available")]
    except Exception:
        return []


def no_agent_installed_reply(
    attempts: List[Dict[str, Any]],
    project_path: Optional[str] = None,
    *,
    installed: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Setup guidance when every attempted target's CLI is missing, else ``""``.

    ``attempts`` are dispatch attempts; a missing CLI carries
    ``cli_missing = {"label", "hint"}`` from the harness kernel. ``installed``
    (catalog rows) is injectable for tests.
    """
    missing = [a.get("cli_missing") for a in attempts or []]
    if not missing or not all(isinstance(m, dict) for m in missing):
        return ""
    lines = [
        "**No agent is installed yet.** Cuttle runs each message through a coding "
        "agent's command-line tool, and none of the ones it tried is on this machine.",
        "",
        "Install one of these and sign in to it:",
        "",
    ]
    seen = set()
    for m in missing:
        label = str(m.get("label") or "").strip() or "Agent"
        if label in seen:
            continue
        seen.add(label)
        hint = " ".join(str(m.get("hint") or "").split())
        lines.append(f"- **{label}**: {hint}" if hint else f"- **{label}**")
    lines += ["", "Then send your message again."]
    rows = _installed_agents(project_path) if installed is None else installed
    ready = [
        f"**{row.get('label') or row.get('id')}** (`{row.get('slash')} your message`)"
        for row in rows
        if row.get("slash")
    ]
    if ready:
        lines += ["", "Already installed here: " + ", ".join(ready) + "."]
    return "\n".join(lines)

"""Chat action-form specs for entering and approving secrets (pure).

Field types rendered by ``chat_secret_fields.js``:

* ``secret`` (``secret: NAME``) — a password input whose value goes straight
  to ``PUT /api/secret-vault/secrets/NAME``; the agent only sees that a value
  was stored.
* ``secret_approval`` (``secrets: [NAME, …]``, ``command``) — Allow for this
  chat / Allow once / Deny; the browser records the grant.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from typing import Any, Dict, List, Optional

TAG = "cuttle_action_form"


def command_text(argv: List[str]) -> str:
    if sys.platform == "win32":
        return subprocess.list2cmdline(argv)
    return shlex.join(argv)


def _field_id(name: str) -> str:
    return "secret_" + re.sub(r"[^A-Za-z0-9_]", "_", name)


def request_card(names: List[str], *, labels: Optional[Dict[str, str]] = None,
                 reason: str = "", session: str = "") -> Dict[str, Any]:
    labels = labels or {}
    title = "Secret needed" if len(names) == 1 else "Secrets needed"
    desc = ("Typed values go straight to your Cuttle vault. The agent only learns "
            "that they were saved.")
    if reason:
        desc = reason.strip() + "\n\n" + desc
    spec: Dict[str, Any] = {
        "title": f"{title}: {', '.join(names)}",
        "description": desc,
        "resume": True,
        "submitLabel": "Save to vault",
        "fields": [{"id": _field_id(n), "type": "secret", "secret": n,
                    "label": labels.get(n) or f"Value for {n}", "required": True}
                   for n in names],
    }
    if session:
        spec["session_id"] = session
    return spec


def approval_card(names: List[str], argv: List[str], *, session: str = "",
                  deliveries: Optional[List[str]] = None) -> Dict[str, Any]:
    cmd = command_text(argv)
    lines = [f"The agent wants to run `{cmd}` using: {', '.join(names)}."]
    if deliveries:
        lines.append("Delivery: " + "; ".join(deliveries) + ".")
    lines.append("Values are injected into that command only and redacted from its output.")
    spec: Dict[str, Any] = {
        "title": "Allow secret use?",
        "description": "\n\n".join(lines),
        "resume": True,
        "submitLabel": "Confirm",
        "fields": [{"id": "secret_approval", "type": "secret_approval", "secrets": list(names),
                    "command": cmd, "label": "Allow", "required": True}],
    }
    if session:
        spec["session_id"] = session
    return spec


def render(spec: Dict[str, Any]) -> str:
    return f"<{TAG}>\n{json.dumps(spec, ensure_ascii=False, indent=2)}\n</{TAG}>"


def answer_text(field_type: str, raw: Any, defn: Dict[str, Any]) -> str:
    """Human answer for a submitted secret field. Never echoes the raw input."""
    if field_type == "secret":
        name = str(defn.get("secret") or "")
        return f"saved to the vault as secret:{name}" if raw == "stored" else "(not provided)"
    names = ", ".join(str(n) for n in (defn.get("secrets") or []))
    return {
        "granted_chat": f"allowed for this chat ({names})",
        "granted_once": f"allowed once ({names})",
        "denied": f"denied ({names})",
    }.get(str(raw), "(no answer)")

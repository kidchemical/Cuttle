"""Antigravity plan quota — headless ``/usage`` + ``/credits`` JSON (no model turn).

``agy -p "/usage" --output-format json`` returns ``command.data.groups[]``: one
group per model family (Gemini, Claude and GPT, Argon…), each with weekly and
5-hour buckets carrying ``remaining_fraction`` and ``reset_time``.
``agy -p "/credits"`` returns ``command.data.remaining_credits``. Both run in
about three seconds, create no conversation and spend no tokens.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.agent_cli_env import agent_cli_env

_WINDOW_LABELS = {"5h": "5-hour", "weekly": "Weekly"}


def _iso_epoch(value: Any) -> Optional[int]:
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
    except (TypeError, ValueError):
        return None


def _run_command(exe: str, command: str, timeout: float) -> Dict[str, Any]:
    try:
        proc = subprocess.run(
            [exe, "-p", command, "--output-format", "json"],
            capture_output=True, text=True, timeout=timeout,
            stdin=subprocess.DEVNULL, env=agent_cli_env(),
            # A neutral cwd: quota is account-wide and must not touch a project.
            cwd=tempfile.gettempdir(), encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "ERROR", "error": f"`agy {command}` failed: {type(exc).__name__}"}
    for line in reversed((proc.stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                body = json.loads(line)
            except ValueError:
                continue
            if isinstance(body, dict):
                return body
    text = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    if "sign in" in text.lower():
        return {"status": "ERROR", "error": "Antigravity is not signed in (run `agy` once interactively)."}
    return {"status": "ERROR", "error": f"`agy {command}` returned no JSON (exit {proc.returncode})."}


def parse_quota_groups(data: Any) -> List[Dict[str, Any]]:
    """``command.data.groups`` → ``[{name, models, buckets: [{id, window, label, remaining_percent, reset_at}]}]``."""
    groups = []
    for raw in (data or {}).get("groups") or []:
        if not isinstance(raw, dict):
            continue
        buckets = []
        for b in raw.get("buckets") or []:
            if not isinstance(b, dict):
                continue
            try:
                remaining = max(0.0, min(100.0, float(b.get("remaining_fraction")) * 100.0))
            except (TypeError, ValueError):
                continue
            window = str(b.get("window") or "").strip().lower()
            buckets.append({
                "id": str(b.get("id") or window or "bucket"),
                "window": window,
                "label": _WINDOW_LABELS.get(window, str(b.get("name") or window or "Limit")),
                "remaining_percent": round(remaining, 2),
                "reset_at": _iso_epoch(b.get("reset_time")),
            })
        if buckets:
            desc = str(raw.get("description") or "")
            models = desc.split(":", 1)[1].strip() if desc.lower().startswith("models within") else ""
            groups.append({"name": str(raw.get("name") or "Models"), "models": models, "buckets": buckets})
    return groups


def fetch_antigravity_quota(*, timeout: float = 45.0) -> Dict[str, Any]:
    """``{success, groups, credits, error, updated_at}``; never raises."""
    from api.agent_harness.agents.antigravity.adapter import antigravity_executable

    exe = antigravity_executable()
    if not exe:
        return {"success": False, "error": "Antigravity CLI (`agy`) not found on PATH."}
    with ThreadPoolExecutor(max_workers=2) as pool:
        usage_f = pool.submit(_run_command, exe, "/usage", timeout)
        credits_f = pool.submit(_run_command, exe, "/credits", timeout)
        usage, credits = usage_f.result(), credits_f.result()
    if str(usage.get("status") or "").upper() != "SUCCESS":
        return {"success": False, "error": str(usage.get("error") or "Antigravity usage unavailable")[:240]}
    command = usage.get("command") if isinstance(usage.get("command"), dict) else {}
    groups = parse_quota_groups(command.get("data"))
    out: Dict[str, Any] = {"success": True, "groups": groups, "updated_at": time.time()}
    cdata = (credits.get("command") or {}).get("data") if isinstance(credits.get("command"), dict) else None
    if isinstance(cdata, dict) and cdata.get("remaining_credits") is not None:
        out["credits"] = {"remaining": cdata.get("remaining_credits"), "upgrade_uri": cdata.get("upgrade_uri") or ""}
    if not groups:
        out["error"] = "Antigravity reported no quota groups."
    return out


def format_antigravity_usage_markdown(data: Dict[str, Any]) -> str:
    from api.agent_usage import cuttle_meters_markdown

    if not data.get("success"):
        return f"❌ **Antigravity usage**\n\n{(data.get('error') or 'Unknown error').strip()}"
    lines = ["**Antigravity — usage**", ""]
    rows: List[Dict[str, Any]] = []
    for group in data.get("groups") or []:
        for b in group["buckets"]:
            row: Dict[str, Any] = {"label": f"{group['name']} · {b['label']}", "pct": b["remaining_percent"]}
            if b.get("reset_at"):
                row.update(tooltip="Resets", tooltip_at=b["reset_at"])
            rows.append(row)
    credits = data.get("credits") or {}
    if credits:
        rows.append({"label": "AI credits", "pct": 0, "disabled": True, "status": str(credits.get("remaining"))})
    block = cuttle_meters_markdown(rows)
    if block:
        lines.append(block)
    for group in data.get("groups") or []:
        if group.get("models"):
            lines.append(f"- **{group['name']}:** {group['models']}")
    lines.append("")
    lines.append(
        "_Bars show remaining quota per model group; models in a group share a "
        "weekly and a 5-hour limit. Source: headless `agy /usage` + `/credits`._"
    )
    if credits.get("upgrade_uri"):
        lines.append(f"Credits: [{credits['upgrade_uri']}]({credits['upgrade_uri']})")
    return "\n".join(lines)


def run_antigravity_usage() -> str:
    return format_antigravity_usage_markdown(fetch_antigravity_quota())

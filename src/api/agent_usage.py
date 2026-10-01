"""
Account / local usage reporters for harness agents (``/usage``).

Cursor already lives in ``api.cursor_agent_commands``. This module covers Codex
(ChatGPT plan windows), OpenCode (``opencode stats``), Hermes
(``hermes insights``), and Muse Code (account + local session rollup — Meta
does not expose subscription % via API).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_USAGE_SLASH_RE = re.compile(r"^(/?)usage\b(.*)$", re.IGNORECASE | re.DOTALL)


def parse_usage_slash(prompt: str) -> Optional[str]:
    """If ``prompt`` is a usage slash/meta command, return args (may be empty)."""
    text = (prompt or "").strip()
    if not text:
        return None
    m = _USAGE_SLASH_RE.match(text)
    if not m:
        return None
    return (m.group(2) or "").strip()


def cuttle_meters_markdown(
    rows: List[Dict[str, Any]], *, variant: str = "usage"
) -> str:
    """Emit a ``<cuttle_meters>`` block matching Cursor ``/usage`` styling."""
    clean: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        label = str(row.get("label") or "").strip()
        if not label:
            continue
        entry: Dict[str, Any] = {"label": label}
        if row.get("disabled"):
            entry["disabled"] = True
            if row.get("status") is not None:
                entry["status"] = str(row.get("status"))
            entry["pct"] = 0
        else:
            try:
                entry["pct"] = round(float(row.get("pct") or 0), 2)
            except (TypeError, ValueError):
                entry["pct"] = 0.0
            if row.get("status") is not None:
                entry["status"] = str(row.get("status"))
        clean.append(entry)
    if not clean:
        return ""
    return (
        "<cuttle_meters>\n"
        + json.dumps({"variant": variant, "rows": clean}, separators=(",", ":"))
        + "\n</cuttle_meters>"
    )


def _fmt_tokens(n: Any) -> str:
    try:
        v = int(float(n or 0))
    except (TypeError, ValueError):
        return "0"
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}M".rstrip("0").rstrip(".")
    if v >= 1000:
        return f"{v / 1000:.1f}k".rstrip("0").rstrip(".")
    return str(v)


def _fmt_usd(amount: Optional[float]) -> str:
    if amount is None:
        return "?"
    return f"${amount:,.2f}"


def _relative_pct(parts: List[Tuple[str, float]], *, top: int = 8) -> List[Dict[str, Any]]:
    total = sum(max(0.0, v) for _l, v in parts)
    if total <= 0:
        return []
    ranked = sorted(parts, key=lambda x: x[1], reverse=True)[:top]
    rows: List[Dict[str, Any]] = []
    for label, val in ranked:
        rows.append(
            {
                "label": label,
                "pct": round((max(0.0, val) / total) * 100.0, 2),
            }
        )
    return rows


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text or "")


def _run_cli(argv: List[str], *, timeout: float = 60.0) -> Tuple[int, str]:
    try:
        r = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
    except FileNotFoundError:
        return 127, ""
    except Exception as exc:
        return 1, str(exc)
    out = (r.stdout or "") + (("\n" + r.stderr) if r.stderr else "")
    return int(r.returncode or 0), out


# ---------------------------------------------------------------------------
# Codex (ChatGPT plan windows via /wham/usage)
# ---------------------------------------------------------------------------


def _codex_auth_paths() -> List[Path]:
    home = Path.home()
    out: List[Path] = []
    codex_home = (os.environ.get("CODEX_HOME") or "").strip()
    if codex_home:
        out.append(Path(codex_home) / "auth.json")
    out.append(home / ".codex" / "auth.json")
    seen: set[str] = set()
    uniq: List[Path] = []
    for p in out:
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    return uniq


def _read_codex_auth() -> Optional[Dict[str, Any]]:
    for path in _codex_auth_paths():
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            continue
        if isinstance(data, dict):
            return data
    return None


def _codex_access_token(auth: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    tokens = auth.get("tokens") if isinstance(auth.get("tokens"), dict) else {}
    tok = (
        str(tokens.get("access_token") or "").strip()
        or str(auth.get("access_token") or "").strip()
    )
    account = (
        str(tokens.get("account_id") or "").strip()
        or str(auth.get("account_id") or "").strip()
    )
    return (tok or None), (account or None)


def fetch_codex_account_usage() -> Dict[str, Any]:
    auth = _read_codex_auth()
    if not auth:
        return {
            "success": False,
            "error": (
                "Could not find Codex login credentials (`~/.codex/auth.json`). "
                "Run `codex login`, then try `/usage` again."
            ),
        }
    token, account_id = _codex_access_token(auth)
    if not token:
        return {
            "success": False,
            "error": (
                "Codex auth.json has no access token. "
                "Run `codex login` and retry `/usage`."
            ),
        }
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "Cuttle-CodexUsage/1.0",
        "Accept": "application/json",
    }
    if account_id:
        headers["ChatGPT-Account-Id"] = account_id
    req = urllib.request.Request(
        "https://chatgpt.com/backend-api/wham/usage",
        headers=headers,
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return {
            "success": False,
            "error": (
                f"Codex usage API returned HTTP {exc.code}. "
                "Re-run `codex login` and retry."
            ),
        }
    except Exception as exc:
        return {"success": False, "error": f"Codex usage request failed: {exc}"}
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return {"success": False, "error": "Codex usage API returned invalid JSON."}
    if not isinstance(data, dict):
        return {"success": False, "error": "Unexpected Codex usage payload."}
    data["success"] = True
    return data


def _window_reset_label(window: Dict[str, Any]) -> str:
    try:
        secs = int(window.get("reset_after_seconds") or 0)
    except (TypeError, ValueError):
        secs = 0
    if secs <= 0:
        reset_at = window.get("reset_at")
        try:
            ts = int(reset_at)
            return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%b %d, %Y %H:%M UTC")
        except Exception:
            return ""
    hours, rem = divmod(secs, 3600)
    mins = rem // 60
    if hours >= 48:
        days = hours // 24
        return f"in {days}d {hours % 24}h"
    if hours:
        return f"in {hours}h {mins}m"
    return f"in {mins}m"


def format_codex_usage_markdown(data: Dict[str, Any]) -> str:
    if not data.get("success"):
        err = (data.get("error") or "Unknown error").strip()
        return f"❌ **Codex usage**\n\n{err}"

    plan = str(data.get("plan_type") or "chatgpt").strip() or "chatgpt"
    email = str(data.get("email") or "").strip()
    rate = data.get("rate_limit") if isinstance(data.get("rate_limit"), dict) else {}
    primary = (
        rate.get("primary_window")
        if isinstance(rate.get("primary_window"), dict)
        else {}
    )
    secondary = (
        rate.get("secondary_window")
        if isinstance(rate.get("secondary_window"), dict)
        else {}
    )
    credits = data.get("credits") if isinstance(data.get("credits"), dict) else {}
    resets = (
        data.get("rate_limit_reset_credits")
        if isinstance(data.get("rate_limit_reset_credits"), dict)
        else {}
    )

    lines = ["**Codex — usage**", ""]
    bits = [plan.title() if len(plan) <= 12 else plan]
    if email:
        bits.append(email)
    lines.append(f"- Plan: {' · '.join(bits)}")
    if rate.get("limit_reached"):
        lines.append("- Status: limit reached")
    elif rate.get("allowed") is False:
        lines.append("- Status: not allowed")

    meter_rows: List[Dict[str, Any]] = []
    if primary:
        try:
            pct = 100.0 - float(primary.get("used_percent") or 0)
        except (TypeError, ValueError):
            pct = 0.0
        label = "5-hour"
        reset = _window_reset_label(primary)
        if reset:
            label = f"5-hour (resets {reset})"
        meter_rows.append({"label": label, "pct": round(pct, 2)})
    if secondary:
        try:
            pct = 100.0 - float(secondary.get("used_percent") or 0)
        except (TypeError, ValueError):
            pct = 0.0
        label = "Weekly"
        reset = _window_reset_label(secondary)
        if reset:
            label = f"Weekly (resets {reset})"
        meter_rows.append({"label": label, "pct": round(pct, 2)})

    has_credits = bool(credits.get("has_credits") or credits.get("unlimited"))
    try:
        bal = float(str(credits.get("balance") or "0").replace(",", ""))
    except (TypeError, ValueError):
        bal = 0.0
    if credits.get("unlimited"):
        meter_rows.append(
            {"label": "Credits", "pct": 0, "disabled": True, "status": "Unlimited"}
        )
    elif has_credits and bal > 0:
        # No hard max published — show balance as a note, meter off.
        meter_rows.append(
            {
                "label": "Credits",
                "pct": 0,
                "disabled": True,
                "status": _fmt_usd(bal),
            }
        )
    else:
        meter_rows.append(
            {"label": "Credits", "pct": 0, "disabled": True, "status": "None"}
        )

    block = cuttle_meters_markdown(meter_rows)
    if block:
        lines.append("")
        lines.append(block)

    try:
        avail = int(resets.get("available_count") or 0)
    except (TypeError, ValueError):
        avail = 0
    if avail:
        lines.append("")
        lines.append(f"Rate-limit resets available: **{avail}**")

    lines.append("")
    lines.append(
        "_5-hour / Weekly bars show remaining capacity from ChatGPT Codex plan "
        "windows (`GET /backend-api/wham/usage`)._"
    )
    lines.append(
        "Dashboard: [chatgpt.com](https://chatgpt.com) → Settings → Usage"
    )
    return "\n".join(lines)


def run_codex_usage() -> str:
    return format_codex_usage_markdown(fetch_codex_account_usage())


# ---------------------------------------------------------------------------
# OpenCode (opencode stats)
# ---------------------------------------------------------------------------


def _opencode_argv() -> Optional[List[str]]:
    try:
        from api.agent_harness.agents.opencode.adapter import opencode_executable

        exe = opencode_executable()
    except Exception:
        exe = None
    if not exe:
        return None
    return [exe]


def fetch_opencode_stats(*, days: int = 30, models: int = 8) -> Dict[str, Any]:
    argv = _opencode_argv()
    if not argv:
        return {
            "success": False,
            "error": "OpenCode CLI not found on PATH. Install `opencode`, then retry.",
        }
    days_i = max(1, min(int(days or 30), 365))
    models_i = max(1, min(int(models or 8), 20))
    code, out = _run_cli(
        argv
        + [
            "stats",
            "--days",
            str(days_i),
            "--models",
            str(models_i),
        ],
        timeout=90.0,
    )
    text = _strip_ansi(out)
    if code != 0 and not text.strip():
        return {
            "success": False,
            "error": f"`opencode stats` failed (exit {code}).",
        }
    parsed = parse_opencode_stats_text(text)
    parsed["success"] = True
    parsed["days"] = days_i
    parsed["raw_ok"] = bool(text.strip())
    if not parsed.get("total_cost") and not parsed.get("tools") and not parsed.get("models"):
        if not text.strip():
            return {
                "success": False,
                "error": "`opencode stats` returned no output.",
            }
        parsed["raw_preview"] = text.strip()[:1200]
    return parsed


def parse_opencode_stats_text(text: str) -> Dict[str, Any]:
    t = _strip_ansi(text or "")

    def _money(pat: str) -> Optional[float]:
        m = re.search(pat, t)
        if not m:
            return None
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            return None

    def _cell(label: str) -> Optional[str]:
        m = re.search(rf"│{re.escape(label)}\s+([^\s│]+)", t)
        return m.group(1).strip() if m else None

    data: Dict[str, Any] = {
        "sessions": None,
        "messages": None,
        "total_cost": _money(r"Total Cost\s+\$([0-9.,]+)"),
        "avg_cost_day": _money(r"Avg Cost/Day\s+\$([0-9.,]+)"),
        "input": _cell("Input"),
        "output": _cell("Output"),
        "cache_read": _cell("Cache Read"),
        "tools": [],
        "models": [],
    }
    sm = re.search(r"│Sessions\s+(\d+)", t)
    if sm:
        data["sessions"] = int(sm.group(1))
    mm = re.search(r"│Messages\s+(\d+)", t)
    if mm:
        data["messages"] = int(mm.group(1))

    for m in re.finditer(
        r"│\s*([a-zA-Z0-9_-]+)\s+█[█\s]*\s+(\d+)\s+\(\s*(\d+(?:\.\d+)?)%\)",
        t,
    ):
        data["tools"].append(
            {
                "name": m.group(1),
                "calls": int(m.group(2)),
                "pct": float(m.group(3)),
            }
        )

    # Model sections: a provider/model id line, later a Cost row in the same box.
    model_id = None
    for raw_line in t.splitlines():
        line = raw_line.strip().strip("│").strip()
        if not line:
            continue
        if re.match(r"^[\w@./+-]+/[\w@./+-]+$", line) or re.match(
            r"^(gpt|o\d|claude|gemini)[\w.+/ -]*$", line, flags=re.I
        ):
            # Likely a model id heading (contains / or known family prefix).
            if "/" in line or line.lower().startswith(("gpt", "claude", "gemini", "o1", "o3", "o4")):
                model_id = line.split()[0]
                continue
        cm = re.search(r"^Cost\s+\$([0-9.]+)", line)
        if cm and model_id:
            try:
                cost = float(cm.group(1))
            except ValueError:
                model_id = None
                continue
            data["models"].append({"id": model_id, "cost": cost})
            model_id = None
    return data


def format_opencode_usage_markdown(data: Dict[str, Any]) -> str:
    if not data.get("success"):
        err = (data.get("error") or "Unknown error").strip()
        return f"❌ **OpenCode usage**\n\n{err}"

    days = int(data.get("days") or 30)
    lines = ["**OpenCode — usage**", "", f"- Window: last **{days}** days (local CLI stats)"]
    if data.get("sessions") is not None:
        lines.append(
            f"- Sessions: {data['sessions']}"
            + (f" · Messages: {data['messages']}" if data.get("messages") is not None else "")
        )
    if data.get("total_cost") is not None:
        avg = data.get("avg_cost_day")
        lines.append(
            f"- Cost: {_fmt_usd(float(data['total_cost']))}"
            + (f" · {_fmt_usd(float(avg))}/day avg" if avg is not None else "")
        )
    tok_bits = []
    for label, key in (("In", "input"), ("Out", "output"), ("Cache", "cache_read")):
        if data.get(key):
            tok_bits.append(f"{label} {data[key]}")
    if tok_bits:
        lines.append(f"- Tokens: {' · '.join(tok_bits)}")

    tools = data.get("tools") if isinstance(data.get("tools"), list) else []
    tool_rows = [
        {"label": str(t.get("name") or "?"), "pct": float(t.get("pct") or 0)}
        for t in tools[:8]
        if isinstance(t, dict)
    ]
    if tool_rows:
        lines.append("")
        lines.append("**Tool distribution**")
        lines.append("")
        lines.append(cuttle_meters_markdown(tool_rows, variant="usage"))

    models = data.get("models") if isinstance(data.get("models"), list) else []
    model_parts: List[Tuple[str, float]] = []
    for m in models:
        if not isinstance(m, dict):
            continue
        mid = str(m.get("id") or "").strip()
        try:
            cost = float(m.get("cost") or 0)
        except (TypeError, ValueError):
            cost = 0.0
        if mid and cost > 0:
            model_parts.append((mid, cost))
    model_rows = _relative_pct(model_parts)
    if model_rows:
        lines.append("")
        lines.append("**By model (share of $)**")
        lines.append("")
        lines.append(cuttle_meters_markdown(model_rows, variant="usage"))

    preview = (data.get("raw_preview") or "").strip()
    if preview and not tool_rows and not model_rows:
        lines.append("")
        lines.append("```")
        lines.append(preview[:1000])
        lines.append("```")

    lines.append("")
    lines.append("_Local OpenCode stats (`opencode stats`) — not a cloud plan quota._")
    return "\n".join(lines)


def run_opencode_usage(*, days: int = 30) -> str:
    return format_opencode_usage_markdown(fetch_opencode_stats(days=days))


# ---------------------------------------------------------------------------
# Hermes (hermes insights)
# ---------------------------------------------------------------------------


def _hermes_argv() -> Optional[List[str]]:
    try:
        from scripts.utilities.hermes_cli_tool import hermes_executable

        exe = hermes_executable()
    except Exception:
        exe = None
    if not exe:
        # Fall back to PATH name.
        return ["hermes"]
    return [exe]


def fetch_hermes_insights(*, days: int = 30) -> Dict[str, Any]:
    argv = _hermes_argv()
    if not argv:
        return {
            "success": False,
            "error": "Hermes CLI not found. Install Hermes, then retry `/usage`.",
        }
    days_i = max(1, min(int(days or 30), 365))
    code, out = _run_cli(argv + ["insights", "--days", str(days_i)], timeout=90.0)
    text = _strip_ansi(out)
    if code != 0 and not text.strip():
        return {
            "success": False,
            "error": f"`hermes insights` failed (exit {code}).",
        }
    parsed = parse_hermes_insights_text(text)
    parsed["success"] = True
    parsed["days"] = days_i
    if not any(
        [
            parsed.get("total"),
            parsed.get("tools"),
            parsed.get("models"),
        ]
    ):
        if not text.strip():
            return {
                "success": False,
                "error": "`hermes insights` returned no output.",
            }
        parsed["raw_preview"] = text.strip()[:1200]
    return parsed


def parse_hermes_insights_text(text: str) -> Dict[str, Any]:
    t = text or ""

    def _num(pat: str) -> Optional[int]:
        m = re.search(pat, t)
        if not m:
            return None
        try:
            return int(m.group(1).replace(",", ""))
        except ValueError:
            return None

    data: Dict[str, Any] = {
        "input": _num(r"Input tokens:\s*([\d,]+)"),
        "output": _num(r"Output tokens:\s*([\d,]+)"),
        "total": _num(r"Total tokens:\s*([\d,]+)"),
        "sessions": _num(r"Sessions:\s*([\d,]+)"),
        "messages": _num(r"Messages:\s*([\d,]+)"),
        "tool_calls": _num(r"Tool calls:\s*([\d,]+)"),
        "tools": [],
        "models": [],
        "period": None,
    }
    pm = re.search(r"Period:\s*([^\n]+)", t)
    if pm:
        data["period"] = pm.group(1).strip()

    for m in re.finditer(
        r"^  ([A-Za-z0-9_./-]+)\s+(\d+)\s+(\d+(?:\.\d+)?)%\s*$", t, re.M
    ):
        data["tools"].append(
            {
                "name": m.group(1),
                "calls": int(m.group(2)),
                "pct": float(m.group(3)),
            }
        )

    in_models = False
    for line in t.splitlines():
        if "Models Used" in line:
            in_models = True
            continue
        if not in_models:
            continue
        if not line.strip():
            break
        if "────" in line or re.match(r"^\s+Model\s+", line):
            continue
        m = re.match(r"^\s+(\S+)\s+(\d+)\s+([\d,]+)\s*$", line)
        if m:
            data["models"].append(
                {
                    "id": m.group(1),
                    "sessions": int(m.group(2)),
                    "tokens": int(m.group(3).replace(",", "")),
                }
            )
    return data


def format_hermes_usage_markdown(data: Dict[str, Any]) -> str:
    if not data.get("success"):
        err = (data.get("error") or "Unknown error").strip()
        return f"❌ **Hermes usage**\n\n{err}"

    days = int(data.get("days") or 30)
    lines = ["**Hermes — usage**", "", f"- Window: last **{days}** days (local insights)"]
    if data.get("period"):
        lines.append(f"- Period: {data['period']}")
    overview = []
    if data.get("sessions") is not None:
        overview.append(f"Sessions {data['sessions']}")
    if data.get("messages") is not None:
        overview.append(f"Messages {data['messages']}")
    if data.get("tool_calls") is not None:
        overview.append(f"Tools {data['tool_calls']}")
    if overview:
        lines.append(f"- {(' · '.join(overview))}")
    tok_bits = []
    if data.get("input") is not None:
        tok_bits.append(f"In {_fmt_tokens(data['input'])}")
    if data.get("output") is not None:
        tok_bits.append(f"Out {_fmt_tokens(data['output'])}")
    if data.get("total") is not None:
        tok_bits.append(f"Total {_fmt_tokens(data['total'])}")
    if tok_bits:
        lines.append(f"- Tokens: {' · '.join(tok_bits)}")

    tools = data.get("tools") if isinstance(data.get("tools"), list) else []
    tool_rows = [
        {"label": str(t.get("name") or "?"), "pct": float(t.get("pct") or 0)}
        for t in tools[:8]
        if isinstance(t, dict)
    ]
    if tool_rows:
        lines.append("")
        lines.append("**Tool distribution**")
        lines.append("")
        lines.append(cuttle_meters_markdown(tool_rows, variant="usage"))

    models = data.get("models") if isinstance(data.get("models"), list) else []
    model_parts = [
        (str(m.get("id") or "?"), float(m.get("tokens") or 0))
        for m in models
        if isinstance(m, dict) and m.get("id")
    ]
    model_rows = _relative_pct(model_parts)
    if model_rows:
        lines.append("")
        lines.append("**By model (share of tokens)**")
        lines.append("")
        lines.append(cuttle_meters_markdown(model_rows, variant="usage"))

    preview = (data.get("raw_preview") or "").strip()
    if preview and not tool_rows and not model_rows:
        lines.append("")
        lines.append("```")
        lines.append(preview[:1000])
        lines.append("```")

    lines.append("")
    lines.append("_Local Hermes insights (`hermes insights`) — not a cloud plan quota._")
    return "\n".join(lines)


def run_hermes_usage(*, days: int = 30) -> str:
    return format_hermes_usage_markdown(fetch_hermes_insights(days=days))


# ---------------------------------------------------------------------------
# Muse Code (account + local MSP rollup; no public plan-% API)
# ---------------------------------------------------------------------------


def _muse_auth_path() -> Path:
    xdg = (os.environ.get("XDG_CONFIG_HOME") or "").strip()
    if xdg:
        return Path(xdg) / "muse" / "auth.json"
    return Path.home() / ".config" / "muse" / "auth.json"


def _read_muse_auth() -> Optional[Dict[str, Any]]:
    path = _muse_auth_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _muse_sessions_root() -> Path:
    xdg = (os.environ.get("XDG_DATA_HOME") or "").strip()
    if xdg:
        return Path(xdg) / "muse" / "sessions"
    return Path.home() / ".local" / "share" / "muse" / "sessions"


def _aggregate_muse_msp_usage(*, days: int = 30) -> Dict[str, Any]:
    """Roll up tokenUsage from recent MSP view snapshots (local only)."""
    root = _muse_sessions_root() / ".msp-view-v1"
    if not root.is_dir():
        return {"sessions": 0, "by_model": {}, "prompt": 0, "completion": 0, "context": 0}

    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=max(1, days))
    by_model: Dict[str, Dict[str, int]] = {}
    sessions = 0
    prompt_total = 0
    completion_total = 0
    context_total = 0

    for view_dir in root.iterdir():
        if not view_dir.is_dir():
            continue
        try:
            from scripts.utilities.muse_cli_session_store import read_muse_msp_context

            ctx = read_muse_msp_context(view_dir.name)
        except Exception:
            ctx = None
        if not isinstance(ctx, dict):
            continue
        # Prefer snapshot mtime for recency filter.
        snaps = list(view_dir.glob("snapshot-*.json"))
        latest_mtime = max((p.stat().st_mtime for p in snaps), default=0)
        if latest_mtime:
            try:
                when = datetime.fromtimestamp(latest_mtime, tz=timezone.utc)
                if when < cutoff:
                    continue
            except Exception:
                pass
        sessions += 1
        mid = str(ctx.get("model") or "unknown").strip() or "unknown"
        bucket = by_model.setdefault(
            mid, {"prompt": 0, "completion": 0, "context": 0, "sessions": 0}
        )
        pt = int(ctx.get("prompt_tokens") or 0)
        ct = int(ctx.get("completion_tokens") or 0)
        xt = int(ctx.get("context_tokens") or 0)
        bucket["prompt"] += pt
        bucket["completion"] += ct
        bucket["context"] += xt
        bucket["sessions"] += 1
        prompt_total += pt
        completion_total += ct
        context_total += xt

    return {
        "sessions": sessions,
        "by_model": by_model,
        "prompt": prompt_total,
        "completion": completion_total,
        "context": context_total,
    }


def fetch_muse_account_usage(*, days: int = 30) -> Dict[str, Any]:
    auth = _read_muse_auth()
    if not auth:
        return {
            "success": False,
            "error": (
                "Could not find Muse login (`~/.config/muse/auth.json`). "
                "Run `muse login`, then try `/usage` again."
            ),
        }
    providers = auth.get("providers") if isinstance(auth.get("providers"), dict) else {}
    meta = providers.get("meta") if isinstance(providers.get("meta"), dict) else {}
    days_i = max(1, min(int(days or 30), 365))
    local = _aggregate_muse_msp_usage(days=days_i)
    return {
        "success": True,
        "email": str(meta.get("user_email") or "").strip(),
        "name": str(meta.get("user_full_name") or "").strip(),
        "mechanism": str(meta.get("mechanism") or "").strip(),
        "obtained_via": str(meta.get("obtained_via") or "").strip(),
        "days": days_i,
        "local": local,
    }


def format_muse_usage_markdown(data: Dict[str, Any]) -> str:
    if not data.get("success"):
        err = (data.get("error") or "Unknown error").strip()
        return f"❌ **Muse Code usage**\n\n{err}"

    days = int(data.get("days") or 30)
    lines = ["**Muse Code — usage**", ""]
    identity = []
    if data.get("name"):
        identity.append(str(data["name"]))
    if data.get("email"):
        identity.append(str(data["email"]))
    if identity:
        lines.append(f"- Account: {' · '.join(identity)}")
    auth_bits = []
    if data.get("obtained_via"):
        auth_bits.append(str(data["obtained_via"]))
    if data.get("mechanism"):
        auth_bits.append(str(data["mechanism"]))
    if auth_bits:
        lines.append(f"- Auth: {' / '.join(auth_bits)}")

    local = data.get("local") if isinstance(data.get("local"), dict) else {}
    sessions = int(local.get("sessions") or 0)
    lines.append(f"- Local MSP sessions (last {days}d): **{sessions}**")
    tok_bits = []
    if local.get("prompt"):
        tok_bits.append(f"In {_fmt_tokens(local['prompt'])}")
    if local.get("completion"):
        tok_bits.append(f"Out {_fmt_tokens(local['completion'])}")
    if local.get("context"):
        tok_bits.append(f"Context {_fmt_tokens(local['context'])}")
    if tok_bits:
        lines.append(f"- Tokens (latest snapshot per session): {' · '.join(tok_bits)}")

    by_model = local.get("by_model") if isinstance(local.get("by_model"), dict) else {}
    model_parts: List[Tuple[str, float]] = []
    for mid, bucket in by_model.items():
        if not isinstance(bucket, dict):
            continue
        weight = float(
            int(bucket.get("context") or 0)
            or (int(bucket.get("prompt") or 0) + int(bucket.get("completion") or 0))
        )
        if weight > 0:
            model_parts.append((str(mid), weight))
    model_rows = _relative_pct(model_parts)
    if model_rows:
        lines.append("")
        lines.append("**By model (local context share)**")
        lines.append("")
        lines.append(cuttle_meters_markdown(model_rows, variant="usage"))
    else:
        lines.append("")
        lines.append(
            "_No recent local MSP usage snapshots found — run a Muse turn, then `/usage` again._"
        )

    lines.append("")
    lines.append(
        "_Meta does not expose Muse subscription quota % via a public API yet. "
        "Meters above are local session rollups, not plan allowance._"
    )
    lines.append(
        "Dashboard: [dev.meta.ai/usage](https://dev.meta.ai/usage)"
    )
    return "\n".join(lines)


def run_muse_usage(*, days: int = 30) -> str:
    return format_muse_usage_markdown(fetch_muse_account_usage(days=days))


def _days_from_args(args: str, default: int = 30) -> int:
    text = (args or "").strip().lower()
    if not text:
        return default
    m = re.search(r"(\d+)\s*d", text)
    if m:
        try:
            return max(1, min(int(m.group(1)), 365))
        except ValueError:
            return default
    if text.isdigit():
        try:
            return max(1, min(int(text), 365))
        except ValueError:
            return default
    return default


def handle_agent_usage_slash(agent_id: str, prompt: str) -> Optional[str]:
    """
    If ``prompt`` is a usage command for ``agent_id``, return markdown reply.
    Otherwise None.
    """
    live = re.fullmatch(r"/?usage-live(?:\s+(.*))?", (prompt or "").strip(), re.I | re.S)
    if live and (agent_id or "").strip().lower() in {"codex", "muse", "hermes", "opencode"}:
        from api.usage_live import live_usage_reply
        return live_usage_reply(agent_id.strip().lower(), live.group(1) or "")
    args = parse_usage_slash(prompt)
    if args is None:
        return None
    aid = (agent_id or "").strip().lower()
    days = _days_from_args(args, 30)
    if aid == "codex":
        return run_codex_usage()
    if aid == "opencode":
        return run_opencode_usage(days=days)
    if aid == "hermes":
        return run_hermes_usage(days=days)
    if aid == "muse":
        return run_muse_usage(days=days)
    return None

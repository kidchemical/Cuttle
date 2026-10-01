"""
Cursor Agent CLI slash commands for Cuttle web chat (/model, /plan, /ask, …).

These map interactive Cursor CLI slash commands onto session prefs + `agent -p`
flags. They only make sense when the message is already routed as `/cursor …`.
"""

from __future__ import annotations

import json
import os
import re
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Commands that Cuttle can honor for `/cursor <cmd> …` (web / Discord bridge).
_CURSOR_AGENT_SLASH_RE = re.compile(
    r"^/(model|plan|ask|agent|clear|new-chat|newchat|new|about|sandbox|usage|compact|summarize|compress)\b(.*)$",
    re.IGNORECASE | re.DOTALL,
)

_NEW_SESSION_ALIASES = frozenset(
    {
        "clear",
        "new",
        "new-chat",
        "newchat",
    }
)

# Brief in-process cache so the slash palette doesn't re-spawn `agent models`
# on every keystroke. Cleared by `/model refresh` / `?refresh=1`.
_MODELS_CACHE: Optional[Tuple[float, List[Dict[str, str]]]] = None
_MODELS_CACHE_TTL_SEC = 60.0


def parse_cursor_agent_slash(prompt: str) -> Optional[Tuple[str, str]]:
    """If prompt is a Cursor Agent slash command, return (command, args). Else None."""
    text = (prompt or "").strip()
    if not text.startswith("/"):
        return None
    m = _CURSOR_AGENT_SLASH_RE.match(text)
    if not m:
        return None
    return m.group(1).lower(), (m.group(2) or "").strip()


def clear_cursor_agent_models_cache() -> None:
    """Drop the in-process `agent models` cache (next list re-runs the CLI)."""
    global _MODELS_CACHE
    _MODELS_CACHE = None


def _fetch_cursor_agent_models() -> List[Dict[str, str]]:
    """Run `agent models` and parse into [{id, label, current}, …]."""
    from scripts.utilities.cursor_cli_tool import _resolve_cursor_agent_argv, _cursor_agent_subprocess_env

    agent_argv = _resolve_cursor_agent_argv()
    if not agent_argv:
        return []
    try:
        r = subprocess.run(
            list(agent_argv) + ["models"],
            capture_output=True,
            text=True,
            timeout=45,
            env=_cursor_agent_subprocess_env(),
        )
    except Exception:
        return []
    out = (r.stdout or "") + ("\n" + r.stderr if r.stderr else "")
    models: List[Dict[str, str]] = []
    for line in out.splitlines():
        s = line.strip()
        if not s or s.lower().startswith("available models"):
            continue
        # "id - Label (current, default)" or "id - Label"
        if " - " not in s:
            continue
        mid, rest = s.split(" - ", 1)
        mid = mid.strip()
        rest = rest.strip()
        if not mid:
            continue
        current = "(current" in rest.lower()
        label = re.sub(r"\s*\([^)]*current[^)]*\)\s*", "", rest, flags=re.I).strip()
        label = re.sub(r"\s*\([^)]*default[^)]*\)\s*", "", label, flags=re.I).strip() or mid
        models.append({"id": mid, "label": label, "current": current})
    return models


def list_cursor_agent_models() -> List[Dict[str, str]]:
    """Return Cursor Agent models, caching successful CLI results briefly.

    Call :func:`clear_cursor_agent_models_cache` (or the models API with
    ``?refresh=1``) to force a fresh ``agent models`` run.
    """
    global _MODELS_CACHE
    now = time.monotonic()
    if _MODELS_CACHE is not None:
        ts, cached = _MODELS_CACHE
        if now - ts < _MODELS_CACHE_TTL_SEC and cached:
            return [dict(m) for m in cached]
    models = _fetch_cursor_agent_models()
    if models:
        _MODELS_CACHE = (now, list(models))
    return [dict(m) for m in models]


def refresh_cursor_agent_models() -> Dict[str, Any]:
    """Force-refresh the Cursor Agent model list via ``agent models``."""
    clear_cursor_agent_models_cache()
    models = list_cursor_agent_models()
    return {
        "models": models,
        "count": len(models),
        "source": "cli_refresh" if models else "cli_empty",
        "error": None if models else "agent models returned nothing",
    }


def _format_models_markdown(
    models: List[Dict[str, str]],
    current_id: Optional[str],
    *,
    recent_block: str = "",
    last_reported: Optional[str] = None,
) -> str:
    if not models:
        body = (
            "**Cursor Agent — models**\n\n"
            "Could not list models (`agent models` failed or returned nothing).\n"
            "Try again, or set one with `/model <id>`."
        )
    else:
        lines = ["**Cursor Agent — models**", ""]
        preferred = (current_id or "auto").strip() or "auto"
        lines.append(f"Session preferred: `{preferred}`")
        if last_reported:
            lines.append(f"Last reported by CLI: `{last_reported}`")
        lines.append("")
        for m in models:
            mid = (m.get("id") or "").strip()
            is_pref = bool(preferred) and mid.lower() == preferred.lower()
            mark = " ← current" if is_pref else ""
            lines.append(f"- `{mid}` — {m.get('label') or mid}{mark}")
        lines.append("")
        lines.append("Set with `/model <id>` (keeps the Cursor Agent badge on this chat).")
        lines.append("Refresh the CLI list with `/model refresh`.")
        lines.append(
            "Auto usually reports as `Auto` only — Cursor does not expose the "
            "underlying routed model to the agent CLI."
        )
        body = "\n".join(lines)
    if recent_block:
        return body.rstrip() + "\n\n" + recent_block.strip() + "\n"
    return body


def _model_status_notice(model_id: str) -> str:
    label = (model_id or "").strip() or "auto"
    try:
        for m in list_cursor_agent_models() or []:
            if str(m.get("id") or "") == label and m.get("label"):
                return f"⚡ Model: {m['label']}"
    except Exception:
        pass
    pretty = "Auto" if label.lower() in ("auto", "default") else label
    return f"⚡ Model: {pretty}"


def _run_agent_about() -> str:
    from scripts.utilities.cursor_cli_tool import _resolve_cursor_agent_argv, _cursor_agent_subprocess_env

    agent_argv = _resolve_cursor_agent_argv()
    if not agent_argv:
        return "❌ Cursor Agent CLI not found."
    try:
        r = subprocess.run(
            list(agent_argv) + ["about"],
            capture_output=True,
            text=True,
            timeout=45,
            env=_cursor_agent_subprocess_env(),
        )
    except Exception as e:
        return f"❌ **Cursor Agent about failed:** {e}"
    text = ((r.stdout or "").strip() or (r.stderr or "").strip())
    if not text:
        return f"❌ `agent about` exited with code {r.returncode}."
    return f"**Cursor Agent — about**\n\n```\n{text[:4000]}\n```"


def _cursor_cli_auth_path() -> Path:
    """Where `agent login` stores credentials (mirrors the CLI's getAuthFilePath)."""
    home = Path.home()
    if os.name == "nt":
        appdata = (os.environ.get("APPDATA") or "").strip()
        base = Path(appdata) if appdata else home / "AppData" / "Roaming"
        return base / "Cursor" / "auth.json"
    if sys.platform == "darwin":
        return home / ".cursor" / "auth.json"
    xdg = (os.environ.get("XDG_CONFIG_HOME") or "").strip()
    return (Path(xdg) if xdg else home / ".config") / "cursor" / "auth.json"


def _read_cursor_cli_access_token() -> Optional[str]:
    """Read the Cursor Agent CLI login access token (never log it)."""
    try:
        data = json.loads(_cursor_cli_auth_path().read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    token = str(data.get("accessToken") or "").strip()
    return token or None


def _cursor_dashboard_post(
    token: str, path: str, body: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    payload = json.dumps(body if body is not None else {}).encode("utf-8")
    req = urllib.request.Request(
        "https://api2.cursor.sh" + path,
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Connect-Protocol-Version": "1",
            "User-Agent": "Cuttle-CursorUsage/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(
        req, context=ssl.create_default_context(), timeout=30
    ) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    data = json.loads(raw) if raw.strip() else {}
    return data if isinstance(data, dict) else {}


def _cursor_dashboard_get(token: str, path: str) -> Dict[str, Any]:
    req = urllib.request.Request(
        "https://api2.cursor.sh" + path,
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "Cuttle-CursorUsage/1.0",
        },
        method="GET",
    )
    with urllib.request.urlopen(
        req, context=ssl.create_default_context(), timeout=30
    ) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    data = json.loads(raw) if raw.strip() else {}
    return data if isinstance(data, dict) else {}


def _ms_to_utc_date(value: Any) -> str:
    try:
        if isinstance(value, str) and value.strip().isdigit():
            ms = int(value.strip())
        elif isinstance(value, (int, float)):
            ms = int(value)
        else:
            return ""
        if ms > 10_000_000_000:  # ms vs seconds
            ms = ms / 1000.0
        return datetime.fromtimestamp(ms, tz=timezone.utc).strftime("%b %d, %Y")
    except Exception:
        return ""


def _cents_to_usd(value: Any) -> Optional[float]:
    try:
        return float(value) / 100.0
    except (TypeError, ValueError):
        return None


def _fmt_usd(amount: Optional[float]) -> str:
    if amount is None:
        return "?"
    return f"${amount:,.2f}"


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


def fetch_cursor_account_usage() -> Dict[str, Any]:
    """
    Fetch current-cycle Cursor plan usage via the Agent CLI login + dashboard API.

    Returns a dict with keys success (bool), error? (str), and usage fields when OK.
    Does not include the access token.
    """
    token = _read_cursor_cli_access_token()
    if not token:
        return {
            "success": False,
            "error": (
                "Could not find a Cursor Agent CLI login on this machine. "
                "Run `agent login`, then try `/usage` again."
            ),
        }
    try:
        period = _cursor_dashboard_post(
            token, "/aiserver.v1.DashboardService/GetCurrentPeriodUsage"
        )
        plan = _cursor_dashboard_post(
            token, "/aiserver.v1.DashboardService/GetPlanInfo"
        )
        hard = _cursor_dashboard_post(
            token, "/aiserver.v1.DashboardService/GetHardLimit"
        )
    except urllib.error.HTTPError as e:
        return {
            "success": False,
            "error": f"Cursor usage API returned HTTP {e.code}. Run `agent login` and retry.",
        }
    except Exception as e:
        return {"success": False, "error": f"Cursor usage request failed: {e}"}

    start = period.get("billingCycleStart")
    end = period.get("billingCycleEnd")
    aggregations: List[Dict[str, Any]] = []
    if start is not None and end is not None:
        try:
            agg = _cursor_dashboard_post(
                token,
                "/aiserver.v1.DashboardService/GetAggregatedUsageEvents",
                {"startDate": str(start), "endDate": str(end)},
            )
            raw_list = agg.get("aggregations") if isinstance(agg, dict) else None
            if isinstance(raw_list, list):
                aggregations = [a for a in raw_list if isinstance(a, dict)]
        except Exception:
            aggregations = []

    stripe: Dict[str, Any] = {}
    try:
        stripe = _cursor_dashboard_get(token, "/auth/full_stripe_profile")
    except Exception:
        stripe = {}

    plan_info = plan.get("planInfo") if isinstance(plan.get("planInfo"), dict) else {}
    plan_usage = (
        period.get("planUsage") if isinstance(period.get("planUsage"), dict) else {}
    )

    return {
        "success": True,
        "period": period,
        "plan_info": plan_info,
        "plan_usage": plan_usage,
        "hard_limit": hard if isinstance(hard, dict) else {},
        "stripe": stripe if isinstance(stripe, dict) else {},
        "aggregations": aggregations,
    }


def format_cursor_usage_markdown(data: Dict[str, Any]) -> str:
    """Render fetch_cursor_account_usage() output as a chat markdown reply.

    Headline percentages show remaining quota, derived from Cursor Agent CLI ``/usage``
    (``totalPercentUsed`` / ``autoPercentUsed`` / ``apiPercentUsed``), not the
    dollar ``totalSpend/limit`` ratio — those can diverge sharply (e.g. 60%
    dollar vs 3% plan quota) because included-plan compute $ is a different
    meter from the quota the CLI shows.
    """
    if not data.get("success"):
        err = (data.get("error") or "Unknown error").strip()
        return f"❌ **Cursor usage**\n\n{err}"

    plan_info = data.get("plan_info") if isinstance(data.get("plan_info"), dict) else {}
    plan_usage = (
        data.get("plan_usage") if isinstance(data.get("plan_usage"), dict) else {}
    )
    period = data.get("period") if isinstance(data.get("period"), dict) else {}
    hard = data.get("hard_limit") if isinstance(data.get("hard_limit"), dict) else {}
    stripe = data.get("stripe") if isinstance(data.get("stripe"), dict) else {}

    plan_name = (plan_info.get("planName") or stripe.get("membershipType") or "Cursor").strip()
    price = (plan_info.get("price") or "").strip()
    status = (stripe.get("subscriptionStatus") or "").strip()
    used = _cents_to_usd(plan_usage.get("totalSpend") or plan_usage.get("includedSpend"))
    limit = _cents_to_usd(plan_usage.get("limit") or plan_info.get("includedAmountCents"))
    remaining = _cents_to_usd(plan_usage.get("remaining"))
    if remaining is None and used is not None and limit is not None:
        remaining = max(0.0, limit - used)

    def _pct_field(*keys: str) -> Optional[float]:
        for k in keys:
            raw = plan_usage.get(k)
            if raw is None:
                continue
            try:
                return float(raw)
            except (TypeError, ValueError):
                continue
        return None

    # CLI-aligned meters (preferred). Fall back to parsing display strings.
    included_pct = _pct_field("totalPercentUsed")
    auto_pct = _pct_field("autoPercentUsed")
    api_pct = _pct_field("apiPercentUsed")

    def _pct_from_msg(msg: str) -> Optional[float]:
        m = re.search(r"(\d+(?:\.\d+)?)\s*%", msg or "")
        if not m:
            return None
        try:
            return float(m.group(1))
        except ValueError:
            return None

    display = (period.get("displayMessage") or "").strip()
    auto_msg = (period.get("autoModelSelectedDisplayMessage") or "").strip()
    named_msg = (period.get("namedModelSelectedDisplayMessage") or "").strip()
    if included_pct is None:
        included_pct = _pct_from_msg(display)
    if auto_pct is None:
        auto_pct = _pct_from_msg(auto_msg)
    if api_pct is None:
        api_pct = _pct_from_msg(named_msg)

    # Dollar spend ratio — secondary; often much higher than totalPercentUsed.
    dollar_pct = None
    try:
        if used is not None and limit and limit > 0:
            dollar_pct = (used / limit) * 100.0
    except Exception:
        dollar_pct = None

    start_s = _ms_to_utc_date(period.get("billingCycleStart"))
    end_s = _ms_to_utc_date(
        period.get("billingCycleEnd") or plan_info.get("billingCycleEnd")
    )
    cycle = (
        f"{start_s} → {end_s}"
        if start_s and end_s
        else (end_s and f"resets {end_s}") or ""
    )
    on_demand_off = bool(hard.get("noUsageBasedAllowed"))

    lines = ["**Cursor — usage**", ""]
    plan_bits = [plan_name]
    if price:
        plan_bits.append(price)
    if status:
        plan_bits.append(status)
    lines.append(f"- Plan: {' · '.join(plan_bits)}")
    if cycle:
        lines.append(f"- Billing cycle: {cycle}")
        if end_s:
            lines.append(f"- Resets: {end_s}")

    # Show remaining quota: Included / Auto / API / On-demand.
    # Primary UI is the meter strip (not duplicate bullet % + analytics chart).
    meter_rows: List[Dict[str, Any]] = []
    if included_pct is not None:
        meter_rows.append(
            {"label": "Included", "pct": round(100.0 - float(included_pct), 2)}
        )
    if auto_pct is not None:
        meter_rows.append(
            {"label": "Auto", "pct": round(100.0 - float(auto_pct), 2)}
        )
    elif auto_msg:
        lines.append(f"- Auto / Composer: {auto_msg}")
    if api_pct is not None:
        meter_rows.append(
            {"label": "API", "pct": round(100.0 - float(api_pct), 2)}
        )
    elif named_msg:
        lines.append(f"- Named / API models: {named_msg}")
    meter_rows.append(
        {
            "label": "On-Demand",
            "pct": 0 if on_demand_off else round(100.0 - float(dollar_pct or 0), 2),
            "disabled": on_demand_off,
            "status": "Off" if on_demand_off else None,
        }
    )

    if meter_rows:
        lines.append("")
        lines.append("<cuttle_meters>")
        lines.append(
            json.dumps(
                {"variant": "usage", "rows": meter_rows},
                separators=(",", ":"),
            )
        )
        lines.append("</cuttle_meters>")
    else:
        lines.append(
            "- On-demand: "
            + ("off (hard limit)" if on_demand_off else "available")
        )

    if used is not None and limit is not None:
        dollar_note = f" ({dollar_pct:.0f}% of $ limit)" if dollar_pct is not None else ""
        lines.append("")
        lines.append(
            f"Compute $: {_fmt_usd(used)} / {_fmt_usd(limit)}"
            + dollar_note
            + (f" · {_fmt_usd(remaining)} left" if remaining is not None else "")
        )

    aggs = data.get("aggregations") if isinstance(data.get("aggregations"), list) else []
    model_rows: List[Tuple[float, str, str]] = []
    for a in aggs:
        if not isinstance(a, dict):
            continue
        intent = (a.get("modelIntent") or "").strip()
        if not intent or intent == "default":
            continue
        cost = _cents_to_usd(a.get("totalCents"))
        if cost is None:
            continue
        out_tok = _fmt_tokens(a.get("outputTokens"))
        model_rows.append((cost, intent, out_tok))
    model_rows.sort(key=lambda r: r[0], reverse=True)
    if model_rows:
        lines.append("")
        lines.append("**By model (this cycle)**")
        lines.append("")
        for cost, intent, out_tok in model_rows[:8]:
            lines.append(f"- `{intent}` — {_fmt_usd(cost)} · {out_tok} out tok")

    lines.append("")
    lines.append(
        "_Included / Auto / API show remaining quota from Cursor Agent CLI `/usage`. "
        "Compute $ is included-plan value"
        + (
            ", not extra charges (on-demand is off)."
            if on_demand_off
            else "."
        )
        + "_"
    )
    lines.append(
        "Dashboard: [cursor.com/dashboard?tab=usage](https://cursor.com/dashboard?tab=usage)"
    )
    return "\n".join(lines)


def _run_cursor_usage() -> str:
    return format_cursor_usage_markdown(fetch_cursor_account_usage())


def handle_cursor_agent_slash(
    prompt: str,
    *,
    cwd: str,
    chat_session_id,
) -> Optional[Dict[str, Any]]:
    """
    Handle Cursor Agent slash commands embedded after `/cursor`.

    Returns:
      - dict with keys action='reply' and message=... for immediate chat replies
      - dict with keys action='run', prompt=..., mode?=... to continue as an agent run
      - None if this is not a Cursor Agent slash command
    """
    parsed = parse_cursor_agent_slash(prompt)
    if not parsed:
        return None
    cmd, args = parsed

    from scripts.utilities.cursor_cli_session_store import (
        clear_cursor_resume_id,
        format_recent_cursor_runs_markdown,
        load_cursor_agent_options,
        update_cursor_agent_options,
    )

    opts = load_cursor_agent_options(cwd, chat_session_id) or {}
    recent_block = format_recent_cursor_runs_markdown(opts)

    if cmd in _NEW_SESSION_ALIASES:
        clear_cursor_resume_id(cwd, chat_session_id)
        return {
            "action": "reply",
            "message": (
                "**Cursor Agent:** Session cleared. "
                "Next message with the Cursor Agent badge starts a fresh conversation."
            ),
        }

    if cmd == "about":
        about = _run_agent_about()
        if recent_block:
            about = about.rstrip() + "\n\n" + recent_block.strip()
        return {"action": "reply", "message": about}

    if cmd == "usage":
        return {"action": "reply", "message": _run_cursor_usage()}

    if cmd in ("compact", "summarize", "compress"):
        try:
            from api.agent_context import compact_agent_context

            result = compact_agent_context(
                chat_session_id=chat_session_id,
                agent_id="cursor",
                cwd=cwd,
            )
        except Exception as exc:
            return {
                "action": "reply",
                "message": f"**Cursor Agent:** Compact failed — {exc}",
            }
        if not result.get("success"):
            err = result.get("error") or "Compact failed"
            return {
                "action": "reply",
                "message": f"**Cursor Agent:** Could not compact — {err}",
            }
        body = (result.get("output") or "").strip()
        status = result.get("status") if isinstance(result.get("status"), dict) else {}
        pct = status.get("percent")
        used = status.get("used_tokens")
        limit = status.get("limit_tokens")
        meter = ""
        if pct is not None and used is not None and limit:
            meter = f"\n\nContext after compact (estimate): **{pct}%** ({used:,} / {limit:,} tokens)."
        return {
            "action": "reply",
            "message": (
                "**Cursor Agent:** Context compacted via `/summarize`."
                + (f"\n\n{body}" if body else "")
                + meter
            ),
        }

    if cmd == "model":
        if not args:
            models = list_cursor_agent_models()
            return {
                "action": "reply",
                "message": _format_models_markdown(
                    models,
                    opts.get("model"),
                    recent_block=recent_block,
                    last_reported=opts.get("last_reported_model"),
                ),
            }
        # `/model <id>` or `/model <id> <prompt to run now>` (same idea as /plan /ask).
        parts = args.split(None, 1)
        model_id = parts[0].strip()
        remainder = parts[1].strip() if len(parts) > 1 else ""
        if model_id.lower() in ("refresh", "reload", "sync") and not remainder:
            refreshed = refresh_cursor_agent_models()
            count = int(refreshed.get("count") or 0)
            err = refreshed.get("error")
            if err and count == 0:
                return {
                    "action": "reply",
                    "message": f"**Cursor Agent:** Model refresh failed — {err}.",
                }
            note = f" ({err})" if err else ""
            return {
                "action": "reply",
                "message": (
                    f"**Cursor Agent:** Refreshed model list from `agent models` — "
                    f"{count} models available in the `/` palette.{note}\n\n"
                    "Filter in chat (with a Cursor badge): type `/model` or a "
                    "name fragment (e.g. `sonnet`, `1m`), then pick a row. "
                    "Or set directly: `/model <id>`."
                ),
                "preferred_model": opts.get("model"),
            }
        update_cursor_agent_options(cwd, chat_session_id, model=model_id)
        if remainder:
            return {
                "action": "run",
                "prompt": remainder,
                "mode": opts.get("mode"),
                "preferred_model": model_id,
            }
        return {
            "action": "reply",
            "ui": "system",
            "notice": _model_status_notice(model_id),
            "message": (
                f"**Cursor Agent:** Model set to `{model_id}` for this chat.\n"
                "Later turns with the Cursor Agent badge will use `--model`.\n"
                "Tip: `/model <id> your prompt` sets the model and runs the prompt in one turn."
            ),
            "preferred_model": model_id,
        }

    if cmd == "sandbox":
        low = (args or "").strip().lower()
        if low in ("", "status"):
            cur = opts.get("sandbox") or "disabled"
            return {
                "action": "reply",
                "message": (
                    f"**Cursor Agent:** Sandbox is `{cur}` for this chat.\n"
                    "Use `/sandbox enabled` or `/sandbox disabled`."
                ),
            }
        if low in ("enabled", "enable", "on", "true", "1"):
            update_cursor_agent_options(cwd, chat_session_id, sandbox="enabled")
            return {
                "action": "reply",
                "message": "**Cursor Agent:** Sandbox set to `enabled` for this chat.",
            }
        if low in ("disabled", "disable", "off", "false", "0"):
            update_cursor_agent_options(cwd, chat_session_id, sandbox="disabled")
            return {
                "action": "reply",
                "message": "**Cursor Agent:** Sandbox set to `disabled` for this chat.",
            }
        return {
            "action": "reply",
            "message": "❌ Usage: `/sandbox enabled|disabled|status`",
        }

    if cmd == "agent":
        update_cursor_agent_options(cwd, chat_session_id, mode=None)
        if args:
            return {"action": "run", "prompt": args, "mode": None}
        return {
            "action": "reply",
            "message": (
                "**Cursor Agent:** Mode reset to default (agent). "
                "Plan/Ask flags will not be passed on the next run."
            ),
        }

    if cmd == "plan":
        update_cursor_agent_options(cwd, chat_session_id, mode="plan")
        if args:
            return {"action": "run", "prompt": args, "mode": "plan"}
        return {
            "action": "reply",
            "message": (
                "**Cursor Agent:** Plan mode on for this chat "
                "(`--mode plan` on subsequent runs).\n"
                "Send a prompt, or `/plan <prompt>` to run immediately. "
                "`/agent` returns to default mode."
            ),
        }

    if cmd == "ask":
        update_cursor_agent_options(cwd, chat_session_id, mode="ask")
        if args:
            return {"action": "run", "prompt": args, "mode": "ask"}
        return {
            "action": "reply",
            "message": (
                "**Cursor Agent:** Ask mode on for this chat "
                "(`--mode ask` on subsequent runs).\n"
                "Send a question, or `/ask <prompt>` to run immediately. "
                "`/agent` returns to default mode."
            ),
        }

    return None

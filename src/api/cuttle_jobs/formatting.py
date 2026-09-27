"""Gitea-facing comment formatting for remote ``@cuttle`` jobs."""

from __future__ import annotations

import re
from typing import Optional, Sequence

_THINK_BLOCK = re.compile(
    r"<(think|thinking|redacted_thinking)\b[^>]*>.*?</\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
_THINK_TRAILING = re.compile(
    r"<(think|thinking|redacted_thinking)\b[^>]*>.*\Z",
    re.IGNORECASE | re.DOTALL,
)
_WEB_CHAT_UI = re.compile(
    r"<cuttle_(?:action_form|confirm|button|form)\b[^>]*>.*?</cuttle_(?:action_form|confirm|button|form)\s*>",
    re.IGNORECASE | re.DOTALL,
)
_MULTI_BLANK = re.compile(r"\n{3,}")

_CHANNEL_CONSTRAINTS = """\
## Channel constraints (Gitea issue thread — NOT Cuttle web chat)

- Your final reply is posted as **one** Gitea markdown comment by the orchestrator.
  Write only that comment body — nothing else.
- Do **not** call `gitea_cli` / `gitea.issue` / post comments / change labels / assign.
  The worker already handled start-of-job updates and will post your reply.
- Do **not** emit `<cuttle_action_form>`, `<cuttle_confirm>`, or any web-chat UI.
  Those only work in Cuttle web chat and look broken on Gitea.
- Do **not** include `<think>` / chain-of-thought in the final reply.
- Ignore project rules that require Discord forms or self-updating Gitea for this turn —
  they do not apply on this remote job channel.
- Keep it compact: short sections, emoji headers OK, no essay, no duplicate file dumps.
"""

# Hidden HTML comment read by custom/public/assets/js/cuttle-issue-actions.js
# (raw-content on the issue page). Safe in markdown; not a web-chat form.
IMPLEMENT_OFFER_MARKER = "<!-- cuttle-offer:implement -->"
_IMPLEMENT_OFFER_PHRASE = re.compile(
    r"would you like me to implement",
    re.IGNORECASE,
)


def channel_constraints() -> str:
    return _CHANNEL_CONSTRAINTS


def with_implement_offer_marker(body: str, *, force: bool = False) -> str:
    """Append the implement-offer marker when the comment invites @cuttle yes."""
    text = (body or "").rstrip()
    if not text:
        return text
    if IMPLEMENT_OFFER_MARKER in text:
        return text
    if force or _IMPLEMENT_OFFER_PHRASE.search(text) or "@cuttle yes" in text.lower():
        return f"{text}\n\n{IMPLEMENT_OFFER_MARKER}\n"
    return text


def sanitize_gitea_markdown(text: str) -> str:
    """Strip thinking blocks and web-chat UI tags before posting to Gitea."""
    raw = (text or "").strip()
    if not raw:
        return ""
    raw = _THINK_BLOCK.sub("", raw)
    raw = _THINK_TRAILING.sub("", raw)
    raw = _WEB_CHAT_UI.sub("", raw)
    raw = _MULTI_BLANK.sub("\n\n", raw).strip()
    return raw


def format_ack_comment(command: str, user: Optional[str], job_id: int) -> str:
    """Stylized start-of-job ack (not an ``@cuttle`` command)."""
    who = f"@{user}" if user else "you"
    cmd = (command or "").lower().strip()
    if cmd == "investigate":
        return f"🔍 Digging in — **investigate** from {who} · job `{job_id}`"
    if cmd == "solve":
        return f"🛠️ On it — **solve** from {who} · job `{job_id}`"
    if cmd == "converse":
        return f"💬 Got it — note from {who} · job `{job_id}`"
    return f"⚡ On it — **{cmd or 'job'}** from {who} · job `{job_id}`"


def format_decline_comment() -> str:
    return (
        "👍 Understood — I won't implement unless you ask later "
        "(`@cuttle yes` / `@cuttle solve`, or keep talking with `@cuttle …`)."
    )


def format_investigate_comment(agent_text: str, *, issue_number: int) -> str:
    body = sanitize_gitea_markdown(agent_text) or "_(empty investigation)_"
    return with_implement_offer_marker(
        f"## 🔍 Investigation · #{int(issue_number)}\n\n"
        f"{body}\n\n"
        "---\n"
        "_Read-only · `@cuttle yes` to implement · `@cuttle …` to keep talking_",
        force=True,
    )


def format_converse_comment(agent_text: str) -> str:
    body = sanitize_gitea_markdown(agent_text) or "_(No reply generated.)_"
    return with_implement_offer_marker(body)


def format_solve_comment(
    agent_text: str,
    *,
    issue_number: int,
    changes: Optional[Sequence[str]] = None,
    pr_url: Optional[str] = None,
    commit_url: Optional[str] = None,
) -> str:
    body = sanitize_gitea_markdown(agent_text)
    if not body:
        body = f"## ✅ Fix ready · #{int(issue_number)}\n\nImplemented a focused fix."

    lines = [body, "", "---"]
    files = list(changes or [])
    if files:
        shown = files[:12]
        lines.append("📁 " + " · ".join(f"`{c}`" for c in shown))
        if len(files) > 12:
            lines.append(f"_…+{len(files) - 12} more_")

    meta: list[str] = ["🧪 Needs playtest", "🏷️ Needs Testing"]
    if pr_url:
        meta.append(f"[PR]({pr_url})")
    if commit_url:
        meta.append(f"[commit]({commit_url})")
    lines.append(" · ".join(meta))
    return "\n".join(lines)

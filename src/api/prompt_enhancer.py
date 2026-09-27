"""Rewrite a rough chat prompt into a clearer instruction for a coding agent.

Cloud order (auto/cloud): OpenAI gpt-4o-mini → Anthropic Haiku → local LLM.
The composer wand calls this; when nothing is reachable the caller keeps the
user's original text (there is no useful heuristic rewrite).
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

MAX_PROMPT_CHARS = 8000
_MAX_OUTPUT_CHARS = 4000
_CONTEXT_CLIP = 600
_MAX_CONTEXT_MESSAGES = 6

_DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
_DEFAULT_ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"

# Leading slash directives (/cursor, /codex, /cmd build, …) are routing, not
# prose: keep them byte-identical so enhancement can't change the harness.
_SLASH_LINE_RE = re.compile(r"^\s*(/[a-z0-9][\w:-]*(?:\s+[^\n]*)?)$", re.IGNORECASE)
_SLASH_LEAD_RE = re.compile(r"^\s*(/[a-z0-9][\w:-]*)(\s+|$)", re.IGNORECASE)

_SYSTEM = (
    "You rewrite a user's rough prompt into a clearer prompt for a coding agent. "
    "Keep the user's intent, scope, and voice — do NOT answer the request, do NOT "
    "add requirements, features, acceptance criteria, or steps the user did not ask for. "
    "Fix grammar, resolve vague wording, and make the ask specific and actionable. "
    "Preserve every file path, @mention, URL, code snippet, and fenced code block verbatim. "
    "Stay roughly the same length (never more than ~2x the original). "
    "Reply with ONLY the rewritten prompt — no preamble, no quotes, no commentary."
)


def split_slash_prefix(text: str) -> Tuple[str, str]:
    """Split leading slash-command routing from the prose body.

    A leading line is routing-only when it is short (`/cursor`, `/model sonnet`);
    a longer line like `/cursor fix the login bug` keeps only the command token
    as prefix and treats the rest as prose.
    """
    raw = text or ""
    lines = raw.split("\n")
    prefix: List[str] = []
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        stripped = line.strip()
        if not stripped:
            if prefix:
                prefix.append(line)
                idx += 1
                continue
            break
        if not _SLASH_LINE_RE.match(line) or len(stripped) > 120:
            break
        if len(stripped.split()) > 3:
            # Command token is routing, the rest of the line is prose.
            m = _SLASH_LEAD_RE.match(line)
            if not m:
                break
            token = line[: m.end()]
            pre = ("\n".join(prefix) + "\n" + token) if prefix else token
            rest = [line[m.end():]] + lines[idx + 1:]
            return pre, "\n".join(rest)
        prefix.append(line)
        idx += 1
    if not prefix:
        return "", raw
    return "\n".join(prefix) + "\n", "\n".join(lines[idx:])


def clean_enhanced(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    text = re.sub(r"</?(?:prompt_to_rewrite|context)>", "", text).strip()
    text = re.sub(
        r"^(?:enhanced|improved|rewritten|refined)\s*prompt\s*:\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    # Models sometimes echo the context header back into the rewrite.
    text = re.sub(r"^Project:[^\n]*\n+", "", text).strip()
    text = re.sub(r"^Recent conversation:(?:\n-[^\n]*)*\n*", "", text).strip()
    # Models like to wrap the whole answer in one fence; unwrap only that case.
    fence = re.match(r"^```[a-zA-Z0-9_-]*\n(.*)\n```$", text, flags=re.DOTALL)
    if fence and "```" not in fence.group(1):
        text = fence.group(1).strip()
    if len(text) > _MAX_OUTPUT_CHARS:
        text = text[:_MAX_OUTPUT_CHARS].rstrip() + "…"
    return text.strip()


def _clip(text: str, limit: int = _CONTEXT_CLIP) -> str:
    t = re.sub(r"\s+", " ", (text or "")).strip()
    return t[:limit].rstrip() + "…" if len(t) > limit else t


def build_enhance_prompt(
    prompt: str,
    *,
    context_messages: Optional[Sequence[Dict[str, Any]]] = None,
    project_name: str = "",
) -> str:
    ctx: List[str] = []
    if (project_name or "").strip():
        ctx.append(f"Project: {project_name.strip()}")
    convo = [m for m in (context_messages or []) if (m or {}).get("content")]
    if convo:
        ctx.append("Recent conversation:")
        for m in convo[-_MAX_CONTEXT_MESSAGES:]:
            role = "User" if (m.get("role") or "").lower() == "user" else "Assistant"
            ctx.append(f"- {role}: {_clip(str(m.get('content') or ''))}")

    parts: List[str] = []
    if ctx:
        parts.append("<context>")
        parts.extend(ctx)
        parts.append("</context>")
        parts.append(
            "The context above is background only. Never copy it, quote it, "
            "or mention it in your reply."
        )
    parts.append("<prompt_to_rewrite>")
    parts.append(prompt)
    parts.append("</prompt_to_rewrite>")
    parts.append(
        "Reply with only the rewritten prompt text — no tags, no labels, no context."
    )
    return "\n".join(parts)


def _via_openai(prompt: str) -> Optional[str]:
    from api.llm_complete import complete

    text = complete(
        user=prompt,
        system=_SYSTEM,
        max_tokens=900,
        temperature=0.4,
        openai_model=os.getenv("PROMPT_ENHANCE_OPENAI_MODEL") or _DEFAULT_OPENAI_MODEL,
        timeout=45,
        providers=("openai",),
    )
    return clean_enhanced(text or "") or None


def _via_anthropic(prompt: str) -> Optional[str]:
    from api.llm_complete import complete

    text = complete(
        user=prompt,
        system=_SYSTEM,
        max_tokens=900,
        temperature=0.4,
        anthropic_model=os.getenv("PROMPT_ENHANCE_MODEL") or _DEFAULT_ANTHROPIC_MODEL,
        providers=("anthropic",),
    )
    return clean_enhanced(text or "") or None


def _via_local(prompt: str) -> Optional[str]:
    from api.llm_complete import complete

    text = complete(
        user=prompt,
        system=_SYSTEM,
        max_tokens=900,
        temperature=0.4,
        timeout=90,
        providers=("local",),
    )
    return clean_enhanced(text or "") or None


def enhance_prompt(
    prompt: str,
    *,
    context_messages: Optional[Sequence[Dict[str, Any]]] = None,
    project_name: str = "",
    inference_mode: str = "auto",
) -> Dict[str, Any]:
    """Return {ok, prompt, source, error}. `prompt` is the full text incl. slash prefix."""
    original = (prompt or "").strip()
    if not original:
        return {"ok": False, "prompt": prompt or "", "source": "", "error": "Empty prompt"}
    if len(original) > MAX_PROMPT_CHARS:
        return {
            "ok": False,
            "prompt": prompt,
            "source": "",
            "error": f"Prompt too long (>{MAX_PROMPT_CHARS} chars)",
        }

    slash_prefix, body = split_slash_prefix(original)
    if not body.strip():
        return {
            "ok": False,
            "prompt": prompt,
            "source": "",
            "error": "Nothing to enhance — the prompt is only a slash command",
        }

    request_text = build_enhance_prompt(
        body.strip(),
        context_messages=context_messages,
        project_name=project_name,
    )
    mode = (inference_mode or "auto").lower()
    if mode == "local":
        providers = (("local", _via_local),)
    else:
        providers = (
            ("openai", _via_openai),
            ("anthropic", _via_anthropic),
            ("local", _via_local),
        )

    last_error = ""
    for name, provider in providers:
        try:
            out = provider(request_text)
        except Exception as e:
            last_error = str(e)
            print(f"[PROMPT-ENHANCE] {name} failed: {e}", flush=True)
            continue
        if not out:
            continue
        if out.strip() == body.strip():
            return {
                "ok": True,
                "prompt": original,
                "source": name,
                "unchanged": True,
                "error": "",
            }
        return {
            "ok": True,
            "prompt": slash_prefix + out if slash_prefix else out,
            "source": name,
            "unchanged": False,
            "error": "",
        }

    return {
        "ok": False,
        "prompt": original,
        "source": "",
        "error": last_error or "No enhancement provider available (set OPENAI_API_KEY)",
    }

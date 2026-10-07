"""
Suggest a git commit message from pending diffs + optional chat prompts.

Cloud order (auto/cloud): configured completion provider first, then remaining
providers in registry order. Models follow completion settings unless overridden.
When none work, fall back to an intent heuristic from chat prompts + diff
signals — never a bare file/directory inventory.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

_SUBJECT_MAX = 72
_PROMPT_CLIP = 400
_MAX_PROMPTS = 12
_INVENTORY_RE = re.compile(
    r"\band\s+\d+\s+more(\s+files?)?\b|"
    r"^(?:Update|Add|Remove)\s+"
    r"[\w./\\-]+"
    r"(?:\s*,\s*[\w./\\-]+){0,3}"
    r"(?:\s*,?\s+and\s+[\w./\\-]+)?$",
    re.IGNORECASE,
)
_META_PROMPT_RE = re.compile(
    r"\b(auto[- ]?nam(?:e|ing)|commit\s*message|suggest(?:ion|ions|ed|ing)?|"
    r"doesn'?t\s+seem|quite\s+bad|not\s+any\s+better|inventory|"
    r"heuristic|haiku|llm\s+suggest)\b",
    re.IGNORECASE,
)
_SLASH_LEAD_RE = re.compile(
    r"^(?:/(?:cursor|claude|hermes|codex|muse|opencode|antigravity|deepseek|cmd|pipeline|project|cd|model|plan|ask|agent|sandbox)\b[^\n]*\s+)+",
    re.IGNORECASE,
)
_POLITE_RE = re.compile(
    r"^(?:hey[, ]+|hi[, ]+|please\s+|can you\s+|could you\s+|could we\s+|"
    r"can we\s+|would you\s+|i want(?: you)? to\s+|i'd like(?: you)? to\s+|"
    r"we need to\s+|let'?s\s+|try to\s+|help(?: me)?\s+)\s*",
    re.IGNORECASE,
)
_WORK_VERB_RE = re.compile(
    r"\b(add|fix|fixes?|improve|improving|implement|update|updates?|"
    r"refactor|rename|remove|delete|support|enable|disable|wire|"
    r"build|create|allow|prevent|stop|make|change|changes)\b",
    re.IGNORECASE,
)

# Default cloud models (override via env).
_DEFAULT_COMMIT_MODEL = "claude-haiku-4-5-20251001"
_DEFAULT_OPENAI_COMMIT_MODEL = "gpt-4o-mini"

_SYSTEM = (
    "You write git commit subject lines for a working tree diff. "
    "Describe the purpose of the change (fix, feature, refactor), not a file inventory. "
    "Never write 'X and N more files' or list directory paths as the subject. "
    "Prefer a specific verb + object (e.g. 'Add include/exclude for pending git commits'). "
    "Reply with ONLY the subject — no quotes, no trailing period, no body."
)


def sanitize_commit_message(raw: str) -> str:
    if not raw:
        return ""
    text = raw.strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    # Prefer first non-empty line; drop common labels.
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        line = re.sub(
            r"^(commit\s*message\s*:|subject\s*:|title\s*:)\s*",
            "",
            line,
            flags=re.IGNORECASE,
        )
        line = line.strip("\"'`“”‘’ ").rstrip(".").strip()
        if line:
            text = line
            break
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > _SUBJECT_MAX:
        cut = text[:_SUBJECT_MAX].rstrip()
        if " " in cut:
            cut = cut.rsplit(" ", 1)[0]
        text = cut.rstrip(".,;:/…") + "…"
    # Collapse accidental double ellipsis from source text.
    text = re.sub(r"…+", "…", text)
    return text


def _usable_subject(raw: str, *, file_count: int = 0) -> str:
    """Sanitize and reject inventory-style subjects."""
    text = sanitize_commit_message(raw)
    if not text:
        return ""
    if _INVENTORY_RE.search(text):
        # Always reject "and N more"; also reject bare area lists when multi-file.
        if file_count >= 2 or re.search(r"\band\s+\d+\s+more", text, re.I):
            return ""
    return text


def _clip_prompt(text: str) -> str:
    t = re.sub(r"\s+", " ", (text or "")).strip()
    if len(t) > _PROMPT_CLIP:
        t = t[:_PROMPT_CLIP].rstrip() + "…"
    return t


def _user_prompts_from_messages(messages: Sequence[Dict[str, Any]]) -> List[str]:
    out: List[str] = []
    for m in messages or []:
        if (m.get("role") or "").lower() != "user":
            continue
        content = _clip_prompt(m.get("content") or "")
        if not content:
            continue
        # Skip pure slash meta that isn't about the work.
        low = content.lower()
        if low in ("/cursor new", "/cursor new session", "new", "new session"):
            continue
        out.append(content)
    if len(out) > _MAX_PROMPTS:
        out = out[-_MAX_PROMPTS:]
    return out


def _tokens(text: str) -> Set[str]:
    return {
        t
        for t in re.findall(r"[a-z0-9]{3,}", (text or "").lower())
        if t not in {
            "the", "and", "for", "with", "that", "this", "from", "into",
            "have", "just", "please", "want", "need", "like", "doesnt",
            "does", "seem", "seems", "also", "then", "than", "when",
            "file", "files", "path", "src", "api", "web", "js", "css",
            "py", "html", "test", "tests",
        }
    }


def _strip_to_intent(prompt: str) -> str:
    text = (prompt or "").strip()
    # Drop invisible capability / tool blobs if they ever leak into history.
    text = re.sub(r"<cuttle_[^>]+>.*?</cuttle_[^>]+>", " ", text, flags=re.DOTALL | re.I)
    text = re.sub(r"<cuttle_[^>]+/>", " ", text, flags=re.I)
    text = _SLASH_LEAD_RE.sub("", text).strip()
    # Prefer the first sentence / clause that looks like work.
    chunks = re.split(r"[.!?]\s+|\n+", text)
    pick = ""
    for ch in chunks:
        ch = ch.strip(" \t-•")
        if not ch:
            continue
        if _WORK_VERB_RE.search(ch) or len(ch) >= 24:
            pick = ch
            break
        if not pick:
            pick = ch
    text = pick or text
    # Soften questions into imperatives.
    text = re.sub(
        r"^(?:why|how|what)\s+(?:is|are|does|do|can|could)\s+",
        "",
        text,
        flags=re.I,
    )
    # Loop polite prefixes a few times.
    for _ in range(4):
        nxt = _POLITE_RE.sub("", text).strip()
        if nxt == text:
            break
        text = nxt
    text = re.sub(r"^(?:to\s+)", "", text, flags=re.I).strip()
    # Chatty filler → tighter subject.
    text = re.sub(r"\bability to\b", "", text, flags=re.I)
    text = re.sub(r"\beasily\b", "", text, flags=re.I)
    text = re.sub(r"\bdisclude\b", "exclude", text, flags=re.I)
    text = re.sub(r"\bmark\s+", "", text, flags=re.I)
    text = re.sub(r"\s*/\s*", "/", text)
    text = re.sub(r"\s+", " ", text).strip()
    # "improving X" / "adding X" → "Improve X" / "Add X"
    text = re.sub(r"^improving\b", "Improve", text, flags=re.I)
    text = re.sub(r"^adding\b", "Add", text, flags=re.I)
    text = re.sub(r"^fixing\b", "Fix", text, flags=re.I)
    text = re.sub(r"^updating\b", "Update", text, flags=re.I)
    text = re.sub(r"^implementing\b", "Implement", text, flags=re.I)
    text = text.strip(" \t\"'`.,;:?!")
    if not text:
        return ""
    # Capitalize first letter only.
    text = text[0].upper() + text[1:]
    return sanitize_commit_message(text)


def _score_prompt(prompt: str, corpus_tokens: Set[str]) -> float:
    intent = _strip_to_intent(prompt)
    if not intent or len(intent) < 8:
        return -1.0
    # Skip meta chatter about the suggester itself / quality complaints.
    if _META_PROMPT_RE.search(prompt) and not re.search(
        r"\b(include|exclude|ignore|gitignore|slash|palette|pending\s+changes)\b",
        prompt,
        re.I,
    ):
        return -1.0
    toks = _tokens(intent)
    if not toks:
        return 0.0
    overlap = len(toks & corpus_tokens)
    work = 2.0 if _WORK_VERB_RE.search(intent) else 0.0
    # Concrete feature words that often appear in this product.
    feature = 0.0
    for kw in (
        "include", "exclude", "ignore", "gitignore", "slash", "palette",
        "pending", "commit", "diff", "checkbox", "restart", "flask",
    ):
        if kw in toks or kw in (prompt or "").lower():
            feature += 1.5
    # Prefer medium-length concrete asks over tiny chatter.
    length = min(len(intent), 72) / 72.0
    return overlap * 3.0 + work + feature + length


def subject_from_user_prompts(
    prompts: Optional[Sequence[str]],
    *,
    files: Optional[Sequence[Dict[str, Any]]] = None,
    diff_excerpt: str = "",
) -> str:
    """Best-effort subject from chat prompts, scored against the pending change corpus."""
    corpus = _tokens(diff_excerpt or "")
    for f in files or []:
        corpus |= _tokens(str(f.get("path") or ""))
    scored: List[Tuple[float, str]] = []
    for p in prompts or []:
        score = _score_prompt(str(p), corpus)
        intent = _strip_to_intent(str(p))
        if score < 0 or not intent:
            continue
        # Reject inventory leftovers after stripping.
        if _INVENTORY_RE.search(intent):
            continue
        scored.append((score, intent))
    if not scored:
        return ""
    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[0][1]
    # Only blend when both look like concrete feature asks (not complaints).
    if (
        len(scored) >= 2
        and scored[1][0] >= max(4.0, scored[0][0] * 0.85)
        and not _META_PROMPT_RE.search(scored[0][1])
        and not _META_PROMPT_RE.search(scored[1][1])
    ):
        a, b = scored[0][1], scored[1][1]
        at, bt = _tokens(a), _tokens(b)
        if at and bt and len(at & bt) / max(1, len(at | bt)) < 0.35:
            a_short = a.split(",")[0].strip()
            b_short = b.split(",")[0].strip()
            b_short = re.sub(
                r"^(Add|Fix|Improve|Update|Implement|Refactor|Remove)\s+",
                "",
                b_short,
                flags=re.I,
            )
            blended = sanitize_commit_message(f"{a_short}; {b_short}")
            if blended and len(blended) <= _SUBJECT_MAX and not _INVENTORY_RE.search(blended):
                return blended
    return top


def subject_from_diff(files: Optional[Sequence[Dict[str, Any]]], diff_excerpt: str) -> str:
    """Lightweight subject from path names + added symbols when prompts are missing."""
    paths = [str(f.get("path") or "").replace("\\", "/") for f in (files or []) if f.get("path")]
    statuses = {str(f.get("status") or "modified") for f in (files or [])}
    if statuses == {"deleted"}:
        verb = "Remove"
    elif statuses <= {"added", "untracked"}:
        verb = "Add"
    else:
        verb = "Update"

    # New/renamed symbols in the diff.
    symbols = re.findall(
        r"^\+\s*(?:def|class|function|const|let|var|async function)\s+([A-Za-z_][\w]*)",
        diff_excerpt or "",
        flags=re.M,
    )
    # Prefer feature-ish names over tiny helpers.
    symbols = [s for s in symbols if len(s) >= 4 and not s.startswith("_")]
    if symbols:
        # Dedupe preserving order
        seen: set = set()
        uniq = []
        for s in symbols:
            if s.lower() in seen:
                continue
            seen.add(s.lower())
            uniq.append(s)
        label = uniq[0]
        # camelCase / snake → words
        label = re.sub(r"([a-z])([A-Z])", r"\1 \2", label)
        label = label.replace("_", " ").strip()
        return sanitize_commit_message(f"{verb} {label}")

    # Filename stem if single file; else shared keyword across paths.
    if len(paths) == 1:
        stem = paths[0].rsplit("/", 1)[-1]
        stem = re.sub(r"\.(py|js|ts|tsx|css|html|md|yaml|yml|json)$", "", stem, flags=re.I)
        stem = stem.replace("_", " ").replace("-", " ")
        return sanitize_commit_message(f"{verb} {stem}")

    return ""


def build_intent_heuristic(
    context: Dict[str, Any],
    *,
    user_prompts: Optional[Sequence[str]] = None,
) -> str:
    """Non-LLM subject: prompts first, then diff symbols, then area fallback from context."""
    files = list(context.get("files") or [])
    diff = str(context.get("diff_excerpt") or "")
    from_prompts = subject_from_user_prompts(user_prompts, files=files, diff_excerpt=diff)
    if from_prompts:
        return from_prompts
    from_diff = subject_from_diff(files, diff)
    if from_diff:
        return from_diff
    base = sanitize_commit_message(str(context.get("heuristic_message") or ""))
    return base or "Update project files"


def build_suggest_prompt(
    *,
    file_summary: str,
    diff_excerpt: str,
    user_prompts: Optional[Sequence[str]] = None,
    recent_subjects: Optional[Sequence[str]] = None,
    area_summary: str = "",
    avoid_messages: Optional[Sequence[str]] = None,
) -> str:
    parts = [
        "Write a concise git commit subject (max ~72 characters) for the DIFF below.",
        "Read the hunks — summarize what the change does, not which files moved.",
        "Bad: 'Update chat_page.js and 11 more files' or 'Update src/api, src/scripts, and src/tests'.",
        "Good: 'Add include/exclude and .gitignore for pending git commits'.",
    ]
    prompts = [p for p in (user_prompts or []) if p]
    if prompts:
        parts.append("\nRecent user prompts that likely caused these changes (use as intent):")
        for p in prompts:
            parts.append(f"- {p}")
    subjects = [
        s for s in (recent_subjects or [])
        if s and not _INVENTORY_RE.search(str(s))
    ]
    if subjects:
        parts.append("\nRecent commit subjects in this repo (match tone/style lightly):")
        for s in subjects[:5]:
            parts.append(f"- {s}")
    avoid = []
    seen = set()
    for raw in avoid_messages or []:
        cleaned = sanitize_commit_message(str(raw or ""))
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        avoid.append(cleaned)
    if avoid:
        parts.append(
            "\nDo NOT reuse any of these subjects — pick clearly different wording:"
        )
        for s in avoid[:12]:
            parts.append(f"- {s}")
    if (area_summary or "").strip():
        parts.append("\nAreas touched: " + area_summary.strip())
    excerpt = (diff_excerpt or "").strip()
    if excerpt:
        parts.append("\nDiff:")
        parts.append(excerpt)
    parts.append("\nFiles (inventory only — do not copy this as the subject):")
    parts.append(file_summary or "(none)")
    return "\n".join(parts)


def _via_openai(prompt: str, *, file_count: int = 0, temperature: float = 0.3) -> Optional[str]:
    from api.llm_complete import complete

    text = complete(
        user=prompt,
        system=_SYSTEM,
        max_tokens=80,
        temperature=float(temperature),
        openai_model=os.getenv("COMMIT_MSG_OPENAI_MODEL") or None,
        timeout=60,
        providers=("openai",),
    )
    return _usable_subject(text or "", file_count=file_count) or None


def _via_local(prompt: str, *, file_count: int = 0, temperature: float = 0.3) -> Optional[str]:
    from api.llm_complete import complete

    text = complete(
        user=prompt,
        system=_SYSTEM,
        max_tokens=120,
        temperature=float(temperature),
        timeout=90,
        providers=("local",),
    )
    return _usable_subject(text or "", file_count=file_count) or None


def _via_anthropic(prompt: str, *, file_count: int = 0, temperature: float = 0.3) -> Optional[str]:
    from api.llm_complete import complete

    text = complete(
        user=prompt,
        system=_SYSTEM,
        max_tokens=80,
        temperature=float(temperature),
        anthropic_model=os.getenv("COMMIT_MSG_MODEL") or None,
        providers=("anthropic",),
    )
    return _usable_subject(text or "", file_count=file_count) or None


def _is_avoided_subject(message: str, avoid_messages: Optional[Sequence[str]]) -> bool:
    key = sanitize_commit_message(message or "").lower()
    if not key:
        return False
    for raw in avoid_messages or []:
        if key == sanitize_commit_message(str(raw or "")).lower():
            return True
    return False


def suggest_commit_message(
    context: Dict[str, Any],
    *,
    user_prompts: Optional[Sequence[str]] = None,
    avoid_messages: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Return {message, source, heuristic_message}."""
    heuristic = build_intent_heuristic(context, user_prompts=user_prompts)
    files = context.get("files") or []
    file_count = int((context.get("totals") or {}).get("files") or len(files) or 0)
    avoid = [str(t).strip() for t in (avoid_messages or []) if str(t or "").strip()]
    regenerating = bool(avoid)
    prompt = build_suggest_prompt(
        file_summary=str(context.get("file_summary") or ""),
        diff_excerpt=str(context.get("diff_excerpt") or ""),
        user_prompts=user_prompts,
        recent_subjects=context.get("recent_subjects") or [],
        area_summary=str(context.get("area_summary") or ""),
        avoid_messages=avoid or None,
    )
    from api.completion_providers import resolve_order
    by_id = {"openai": _via_openai, "anthropic": _via_anthropic, "local": _via_local}
    providers = tuple(by_id[name] for name in resolve_order())

    base_temp = 0.95 if regenerating else 0.3
    attempts = 3 if regenerating else 1
    last_msg = None
    last_source = "heuristic"
    for attempt in range(attempts):
        attempt_temp = min(1.2, base_temp + (0.15 * attempt))
        for provider in providers:
            try:
                msg = provider(prompt, file_count=file_count, temperature=attempt_temp)
                if not msg:
                    continue
                last_msg = msg
                pname = provider.__name__
                if pname.startswith("_via_"):
                    last_source = pname[5:]
                else:
                    last_source = pname.lstrip("_")
                if not _is_avoided_subject(msg, avoid):
                    return {
                        "message": msg,
                        "source": last_source,
                        "heuristic_message": heuristic,
                    }
            except Exception as e:
                print(f"[COMMIT-MSG] {provider.__name__} failed: {e}", flush=True)

    if last_msg and not _is_avoided_subject(last_msg, avoid):
        return {
            "message": last_msg,
            "source": last_source,
            "heuristic_message": heuristic,
        }
    if heuristic and not _is_avoided_subject(heuristic, avoid):
        return {
            "message": heuristic,
            "source": "heuristic",
            "heuristic_message": heuristic,
        }
    return {
        "message": last_msg or heuristic,
        "source": last_source if last_msg else "heuristic",
        "heuristic_message": heuristic,
    }


def load_session_user_prompts(chat_session_id: Optional[int], *, limit: int = 40) -> List[str]:
    if chat_session_id is None:
        return []
    try:
        sid = int(chat_session_id)
    except (TypeError, ValueError):
        return []
    try:
        from api.auth_db import get_auth_db

        msgs = get_auth_db().get_messages(sid, limit=limit)
        return _user_prompts_from_messages(msgs)
    except Exception as e:
        print(f"[COMMIT-MSG] session prompts skipped: {e}", flush=True)
        return []

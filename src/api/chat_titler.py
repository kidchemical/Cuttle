"""
Automatic chat session naming.

Strategy (cheapest first):
  1. Instant fallback — the first user message, truncated, replaces the
     default "Chat Session N" name as soon as the first message lands.
  2. Background LLM title — a tiny completion generates a short descriptive
     title (emoji + 3–6 words). Provider follows the chat's inference mode:
       - local  → the already-loaded local model (llama.cpp Qwen3 / Ollama).
                  One model server, one model — a title request just queues
                  behind normal traffic, so no second model is needed.
       - cloud/auto → configured completion provider first, then the
                  remaining providers in registry order.
  3. Progressive re-titles — as the conversation grows, the title is
     regenerated at increasing user-message counts so it tracks the topic.

Manual renames (name_auto = 0) are never overwritten.

Env overrides:
  CHAT_TITLE_OPENAI_MODEL  Override the configured OpenAI completion model
  CHAT_TITLE_MODEL         Override the configured Anthropic completion model
  CHAT_TITLE_DISABLED=1    turn auto-titling off entirely
"""

import os
import re
import threading

# Re-title when the user-message count reaches one of these; after the last
# threshold, refresh every 15 user messages.
_RETITLE_AT = (1, 2, 5, 12)
_RETITLE_EVERY_AFTER = 15

_FALLBACK_MAX_LEN = 48
_TITLE_MAX_LEN = 60

_DEFAULT_OPENAI_TITLE_MODEL = "gpt-4o-mini"
_DEFAULT_ANTHROPIC_TITLE_MODEL = "claude-haiku-4-5-20251001"

# Leading slash tokens kept in the stored title so the history UI can render
# them as chips (`/cursor ✨ Fix titles`). Includes project cmds (`/build`)
# and `/cmd name` / `/pipeline id`.
_LEADING_SLASH_RE = re.compile(
    r"^/(?:cmd\s+[A-Za-z][\w-]*|pipeline\s+\S+|[A-Za-z][\w-]*)\b\s*",
    re.IGNORECASE,
)
_SKIP_SLASH_NAMES = {"help", "pipelines", "project", "cd"}
_MAX_TITLE_SLASH = 3

_inflight_lock = threading.Lock()
_inflight_sessions = set()


def _strip_slash_lead(text: str) -> str:
    """Drop leading /cursor (etc.) prefixes; keep the actual request."""
    text = (text or "").strip()
    while True:
        nxt = _LEADING_SLASH_RE.sub("", text).strip()
        if nxt == text:
            return text
        text = nxt


def _slash_token_name(matched: str) -> str:
    parts = matched.strip().lstrip("/").split()
    if not parts:
        return ""
    if parts[0].lower() == "cmd" and len(parts) > 1:
        return parts[1].lower()
    return parts[0].lower()


def collect_slash_prefixes(messages, limit: int = _MAX_TITLE_SLASH) -> str:
    """Unique leading slash tokens from user messages, first-seen order."""
    found = []
    seen = set()
    for m in messages or []:
        if (m.get("role") or "").lower() != "user":
            continue
        text = re.sub(r"\s+", " ", (m.get("content") or "")).strip()
        while True:
            mm = _LEADING_SLASH_RE.match(text)
            if not mm:
                break
            token = mm.group(0).strip()
            text = text[mm.end():].lstrip()
            name = _slash_token_name(token)
            if not name or name in _SKIP_SLASH_NAMES:
                continue
            key = token.lower()
            if key in seen:
                continue
            seen.add(key)
            found.append(token)
            if len(found) >= limit:
                return " ".join(found)
    return " ".join(found)


def compose_session_title(descriptive: str, messages) -> str:
    """Prefix an LLM/fallback title with slash tokens for chip rendering."""
    prefixes = collect_slash_prefixes(messages)
    desc = (descriptive or "").strip()
    if prefixes and desc.lower().startswith(prefixes.lower()):
        return desc
    desc = _strip_slash_lead(desc)
    if prefixes and desc:
        return f"{prefixes} {desc}"
    return prefixes or desc


# Greeting-only openers carry no topic — the fallback must skip past them
# to the last substantive user message instead of titling the chat "hello".
_GREETING_ONLY_RE = re.compile(r"^[hi!?.'\-, ]*$", re.IGNORECASE)
_GREETINGS = frozenset({
    "hello", "hi", "hey", "yo", "sup", "hiya", "howdy",
    "hello there", "hey there", "hi there",
    "good morning", "good afternoon", "good evening",
    "greetings", "aloha",
})


def _is_greeting_only(text: str) -> bool:
    t = _strip_slash_lead((text or "").strip()).lower()
    t = re.sub(r"\s+", " ", t).strip("!?.',- ")
    if not t:
        return True
    if t in _GREETINGS:
        return True
    return bool(_GREETING_ONLY_RE.match(t)) and len(t) <= 24


def fallback_from_messages(messages) -> str:
    """Best-effort title without an LLM: last substantive user message.

    Scans user messages newest-first, skipping greeting-only openers
    ("/muse hello" must not title the chat "hello") and pasted terminal
    output (long dumps truncate into gibberish). Falls back to the
    first user message only when nothing substantive exists.
    """
    # Pasted shell output truncates into gibberish — prefer a short,
    # human-typed message when one exists.
    _PASTE_MAX_LEN = 400
    first_user = next(
        (m.get("content") or "" for m in (messages or [])
         if (m.get("role") or "").lower() == "user"),
        "",
    )
    users = [
        m.get("content") or "" for m in (messages or [])
        if (m.get("role") or "").lower() == "user"
    ]
    for allow_paste in (False, True):
        for content in reversed(users):
            if _is_greeting_only(content):
                continue
            if not allow_paste and len(content) > _PASTE_MAX_LEN:
                continue
            fb = fallback_title(_strip_slash_lead(content))
            if fb:
                return fb
    if first_user:
        return fallback_title(first_user)
    return ""


def fallback_title(text: str) -> str:
    """Single-line truncation of a message, used as the instant title.

    Keeps leading slash commands so the history UI can render them as chips.
    """
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if not text:
        return ""
    if len(text) > _FALLBACK_MAX_LEN:
        text = text[:_FALLBACK_MAX_LEN].rstrip() + "…"
    return text


def sanitize_chat_title(raw: str) -> str:
    """LLMs love quotes, labels and trailing periods — strip them, keep emoji."""
    if not raw:
        return ""
    title = raw.strip()
    title = re.sub(r"<think>.*?</think>", "", title, flags=re.DOTALL)
    title = title.strip().splitlines()[0].strip()
    title = re.sub(r"^(title\s*:\s*)", "", title, flags=re.IGNORECASE)
    title = title.strip("\"'`“”‘’ ").rstrip(".").strip()
    title = re.sub(r"\s+", " ", title)
    if len(title) > _TITLE_MAX_LEN:
        cut = title[:_TITLE_MAX_LEN].rstrip()
        if " " in cut:
            cut = cut.rsplit(" ", 1)[0]
        title = cut.rstrip(".,;:/…") + "…"
    return title


def _should_retitle(user_message_count: int) -> bool:
    if user_message_count in _RETITLE_AT:
        return True
    if user_message_count > _RETITLE_AT[-1]:
        return user_message_count % _RETITLE_EVERY_AFTER == 0
    return False


def _clip_message(m) -> str:
    content = re.sub(r"\s+", " ", (m.get("content") or "")).strip()
    if (m.get("role") or "").lower() == "user":
        content = _strip_slash_lead(content)
    content = content[:300]
    return f"{m.get('role', '?')}: {content}"


def _title_key(text: str) -> str:
    """Normalize for duplicate detection (ignore slash chips + emoji + case)."""
    t = _strip_slash_lead((text or "").strip()).lower()
    t = re.sub(r"[\U0001F300-\U0001FAFF\U00002700-\U000027BF]+", "", t)
    t = re.sub(r"[^\w\s]+", " ", t, flags=re.UNICODE)
    return re.sub(r"\s+", " ", t).strip()


def _build_prompt(
    messages,
    current_title: str = "",
    avoid_titles=None,
) -> str:
    """Compact transcript excerpt: first 2 + last 4 messages, truncated.

    ``current_title`` is accepted for API compatibility but deliberately
    NOT echoed into the prompt: echoing a stale/greeting title (e.g. the
    instant-fallback "hello" from the first user message) anchors the LLM
    and it just repeats it. De-dup against prior names is handled by
    ``avoid_titles`` instead.
    """
    if len(messages) <= 6:
        excerpt = [_clip_message(m) for m in messages]
    else:
        excerpt = (
            [_clip_message(m) for m in messages[:2]]
            + ["..."]
            + [_clip_message(m) for m in messages[-4:]]
        )

    avoid = []
    seen = set()
    for raw in avoid_titles or []:
        cleaned = _strip_slash_lead(str(raw or "").strip())
        if not cleaned:
            continue
        key = _title_key(cleaned)
        if not key or key in seen:
            continue
        seen.add(key)
        avoid.append(cleaned)
    avoid_line = ""
    if avoid:
        avoid_line = (
            "Do NOT reuse any of these titles — change the wording AND emoji "
            "so the new title is clearly different:\n"
            + "\n".join(f"- {t}" for t in avoid[:12])
            + "\n"
        )
    return (
        "Write a short chat title (3-6 words) naming the topic of this conversation. "
        "Start with one relevant emoji when it clearly fits "
        "(e.g. 🐛 bug, ✨ feature, 🎨 UI, 🔧 refactor, 🧪 test). "
        "If no emoji fits, skip it. "
        "Never start with a slash command (no /cursor, /muse, etc.). "
        "Avoid vague titles like 'general' or 'chat' — use concrete words from the messages. "
        "Weigh the most recent messages most heavily; ignore greetings "
        "(hello, hi, hey) and restate the actual topic being discussed. "
        "Reply with ONLY the title — no quotes, no trailing period, no explanation.\n"
        + avoid_line
        + "\n"
        + "\n".join(excerpt)
    )


def _title_via_openai(prompt: str, *, temperature: float = 0.3):
    from api.llm_complete import complete

    text = complete(
        user=prompt,
        max_tokens=40,
        temperature=float(temperature),
        openai_model=os.getenv("CHAT_TITLE_OPENAI_MODEL") or None,
        timeout=20,
        providers=("openai",),
    )
    return sanitize_chat_title(text) or None


def _title_via_local(prompt: str, *, temperature: float = 0.2):
    from api.llm_complete import complete

    text = complete(
        user=prompt,
        max_tokens=200,
        temperature=float(temperature),
        timeout=120,
        providers=("local",),
    )
    return sanitize_chat_title(text) or None


def _title_via_anthropic(prompt: str, *, temperature: float = 0.3):
    from api.llm_complete import complete

    text = complete(
        user=prompt,
        max_tokens=40,
        temperature=float(temperature),
        anthropic_model=os.getenv("CHAT_TITLE_MODEL") or None,
        providers=("anthropic",),
    )
    return sanitize_chat_title(text) or None


def _is_avoided_title(title: str, avoid_titles) -> bool:
    key = _title_key(title)
    if not key:
        return False
    for raw in avoid_titles or []:
        if key == _title_key(str(raw or "")):
            return True
    return False


def _generate_title(
    messages,
    inference_mode: str,
    current_title: str = "",
    avoid_titles=None,
    temperature: float = None,
):
    avoid = [str(t).strip() for t in (avoid_titles or []) if str(t or "").strip()]
    regenerating = bool(avoid)
    temp = temperature
    if temp is None:
        temp = 0.95 if regenerating else 0.3
    if (inference_mode or "").lower() == "local":
        providers = (_title_via_local,)
        if not regenerating and temperature is None:
            temp = 0.2
    else:
        from api.completion_providers import resolve_order
        by_id = {"openai": _title_via_openai, "anthropic": _title_via_anthropic,
                 "local": _title_via_local}
        providers = tuple(by_id[name] for name in resolve_order())

    attempts = 3 if regenerating else 1
    last = None
    for attempt in range(attempts):
        prompt = _build_prompt(
            messages,
            current_title=current_title,
            avoid_titles=avoid,
        )
        attempt_temp = min(1.2, float(temp) + (0.15 * attempt))
        for provider in providers:
            try:
                title = provider(prompt, temperature=attempt_temp)
                if not title:
                    continue
                title = _strip_slash_lead(title)
                if not title:
                    continue
                last = title
                if not _is_avoided_title(title, avoid):
                    return title
            except Exception as e:
                print(f"[TITLER] {provider.__name__} failed: {e}", flush=True)
    return last


def _pack_title_result(descriptive: str, messages, source: str) -> dict:
    desc = _strip_slash_lead((descriptive or "").strip())
    prefixes = collect_slash_prefixes(messages)
    composed = compose_session_title(desc, messages) if desc else (prefixes or "")
    return {
        "title": desc[:80],
        "slash_prefixes": prefixes,
        "composed": (composed or "")[:80],
        "source": source,
    }


def suggest_session_title(
    chat_session_id: int,
    inference_mode: str = "auto",
    avoid_titles=None,
) -> dict:
    """Generate a title suggestion without persisting it.

    Returns descriptive ``title`` (no slash chips), plus ``slash_prefixes`` and
    ``composed`` for display/save. ``source`` is llm|fallback|current|empty.
    Never raises.
    """
    try:
        from api.auth_db import get_auth_db
        db = get_auth_db()
        info = db.get_session_naming_info(chat_session_id) or {}
        messages = db.get_messages(chat_session_id) or []
        current = (info.get("session_name") or "").strip()
        current_desc = _strip_slash_lead(current)
        avoid = [str(t).strip() for t in (avoid_titles or []) if str(t or "").strip()]
        if avoid and current_desc and not _is_avoided_title(current_desc, avoid):
            avoid = list(avoid) + [current_desc]

        if messages:
            descriptive = _generate_title(
                messages,
                inference_mode,
                current_title=current_desc,
                avoid_titles=avoid or None,
            )
            if descriptive:
                return _pack_title_result(descriptive, messages, "llm")

            fb = fallback_from_messages(messages)
            fb_desc = _strip_slash_lead(fb)
            if fb_desc and not _is_avoided_title(fb_desc, avoid):
                return _pack_title_result(fb_desc, messages, "fallback")

        if current_desc and not _is_avoided_title(current_desc, avoid):
            return _pack_title_result(current_desc, messages, "current")
        if current_desc:
            # Re-suggest exhausted alternatives — still return descriptive-only.
            return _pack_title_result(current_desc, messages, "current")
        return _pack_title_result("", messages, "empty")
    except Exception as e:
        print(f"[TITLER] suggest_session_title({chat_session_id}) failed: {e}", flush=True)
        return {
            "title": "",
            "slash_prefixes": "",
            "composed": "",
            "source": "empty",
        }


def _run_llm_titling(chat_session_id: int, inference_mode: str):
    try:
        from api.auth_db import get_auth_db
        db = get_auth_db()
        info = db.get_session_naming_info(chat_session_id) or {}
        if not (info.get("name_auto") in (1, None)):
            return
        messages = db.get_messages(chat_session_id)
        if not messages:
            return
        descriptive = _generate_title(
            messages, inference_mode, current_title=info.get("session_name") or ""
        )
        title = compose_session_title(descriptive, messages) if descriptive else None
        if title and db.set_session_name(chat_session_id, title, auto=True):
            print(f'[TITLER] session {chat_session_id} → "{title}"', flush=True)
    except Exception as e:
        print(f"[TITLER] session {chat_session_id} titling failed: {e}", flush=True)
    finally:
        with _inflight_lock:
            _inflight_sessions.discard(chat_session_id)


def schedule_session_autoname(chat_session_id: int, inference_mode: str = "auto"):
    """Call after an assistant reply is stored. Cheap; never raises.

    Applies the instant fallback name (first user message) if the session
    still has a default name, and kicks off background LLM titling when the
    conversation hits a re-title threshold.
    """
    if os.getenv("CHAT_TITLE_DISABLED") == "1":
        return
    try:
        from api.auth_db import get_auth_db
        db = get_auth_db()
        info = db.get_session_naming_info(chat_session_id)
        if not info:
            return
        if not (info.get("name_auto") in (1, None)):
            return  # user renamed it — hands off

        # Instant fallback for sessions still carrying the default name.
        name = info.get("session_name") or ""
        if not name or re.match(r"^Chat Session \d+$", name):
            all_messages = db.get_messages(chat_session_id, limit=None) or []
            fb = fallback_from_messages(all_messages)
            if fb:
                # Keep slash chips so the history UI can render the agent badge.
                prefixes = collect_slash_prefixes(all_messages)
                composed = f"{prefixes} {fb}".strip() if prefixes else fb
                db.set_session_name(chat_session_id, composed, auto=True)

        if not _should_retitle(int(info.get("user_message_count") or 0)):
            return

        with _inflight_lock:
            if chat_session_id in _inflight_sessions:
                return
            _inflight_sessions.add(chat_session_id)

        threading.Thread(
            target=_run_llm_titling,
            args=(chat_session_id, inference_mode),
            daemon=True,
            name=f"chat-titler-{chat_session_id}",
        ).start()
    except Exception as e:
        print(f"[TITLER] schedule failed for session {chat_session_id}: {e}", flush=True)

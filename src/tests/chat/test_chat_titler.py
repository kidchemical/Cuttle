"""Tests for progressive chat session auto-naming."""

from api.chat_titler import (
    _build_prompt,
    _generate_title,
    _should_retitle,
    collect_slash_prefixes,
    compose_session_title,
    fallback_title,
    sanitize_chat_title,
)


def test_sanitize_preserves_leading_emoji():
    assert sanitize_chat_title('Title: "🐛 Fix chat names."') == "🐛 Fix chat names"
    assert sanitize_chat_title("✨ Add action forms") == "✨ Add action forms"
    assert sanitize_chat_title("<think>nope</think>\n🔧 Refactor titler\n") == "🔧 Refactor titler"


def test_sanitize_strips_quotes_and_period():
    assert sanitize_chat_title('"Rename sessions automatically."') == "Rename sessions automatically"


def test_fallback_keeps_slash_prefix_for_chips():
    out = fallback_title('/cursor Can we auto-name chat sessions like commits?')
    assert out.lower().startswith("/cursor")
    assert "auto-name" in out.lower() or "chat sessions" in out.lower()
    stacked = fallback_title('/cursor /plan name chats with emojis')
    assert stacked.lower().startswith("/cursor /plan")


def test_compose_session_title_prefixes_slash_chips():
    messages = [
        {"role": "user", "content": "/cursor /plan auto name chat sessions"},
        {"role": "assistant", "content": "Working on it."},
    ]
    assert collect_slash_prefixes(messages) == "/cursor /plan"
    assert compose_session_title("✨ Auto-name chat sessions", messages) == (
        "/cursor /plan ✨ Auto-name chat sessions"
    )
    already = "/cursor /plan ✨ Auto-name chat sessions"
    assert compose_session_title(already, messages) == already
    assert collect_slash_prefixes([{"role": "user", "content": "/help list commands"}]) == ""


def test_fallback_truncates():
    long = "word " * 40
    out = fallback_title(long)
    assert out.endswith("…")
    assert len(out) <= 49


def test_should_retitle_progressive_thresholds():
    assert _should_retitle(1)
    assert _should_retitle(2)
    assert not _should_retitle(3)
    assert _should_retitle(5)
    assert _should_retitle(12)
    assert _should_retitle(15)
    assert _should_retitle(30)
    assert not _should_retitle(16)


def test_build_prompt_asks_for_emoji_and_omits_current_title():
    messages = [
        {"role": "user", "content": "/cursor auto name chat sessions"},
        {"role": "assistant", "content": "I'll wire OpenAI titling."},
    ]
    prompt = _build_prompt(messages, current_title="Chat Session 4")
    assert "emoji" in prompt.lower()
    # current_title is API-compatible only: never echoed (avoids
    # placeholder/greeting anchoring); dedup lives in avoid_titles.
    assert "Current title" not in prompt
    assert "Chat Session 4" not in prompt
    assert _build_prompt(messages, current_title="💬 general") == prompt
    assert "user: auto name chat sessions" in prompt
    assert "user: /cursor" not in prompt
    assert "Never start with a slash" in prompt


def test_build_prompt_includes_avoid_titles():
    prompt = _build_prompt(
        [{"role": "user", "content": "test"}],
        current_title="💬 general",
        avoid_titles=["/cursor 💬 general", "💬 general"],
    )
    assert "Do NOT reuse" in prompt
    assert "💬 general" in prompt


def test_generate_title_retries_when_avoided(monkeypatch):
    calls = {"n": 0}

    def fake_openai(prompt, temperature=0.3):
        calls["n"] += 1
        if calls["n"] == 1:
            return "💬 general"
        return "🧪 Smoke test chat"

    fake_openai.__name__ = "_title_via_openai"
    monkeypatch.setattr("api.chat_titler._title_via_openai", fake_openai)
    monkeypatch.setattr(
        "api.chat_titler._title_via_anthropic",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "api.chat_titler._title_via_local",
        lambda *_a, **_k: None,
    )
    title = _generate_title(
        [{"role": "user", "content": "test"}],
        "cloud",
        avoid_titles=["💬 general"],
    )
    assert title == "🧪 Smoke test chat"
    assert calls["n"] >= 2


def test_generate_title_prefers_openai(monkeypatch):
    def fake_openai(prompt, temperature=0.3):
        return "✨ Auto-name chat sessions"

    def fake_anthropic(prompt, temperature=0.3):
        return "Should not use Haiku"

    def boom(prompt, temperature=0.3):
        raise RuntimeError("nope")

    fake_openai.__name__ = "_title_via_openai"
    fake_anthropic.__name__ = "_title_via_anthropic"
    boom.__name__ = "_title_via_local"
    monkeypatch.setattr("api.chat_titler._title_via_openai", fake_openai)
    monkeypatch.setattr("api.chat_titler._title_via_anthropic", fake_anthropic)
    monkeypatch.setattr("api.chat_titler._title_via_local", boom)
    title = _generate_title(
        [{"role": "user", "content": "name chats like commits"}],
        "cloud",
    )
    assert title == "✨ Auto-name chat sessions"


def test_generate_title_local_skips_cloud(monkeypatch):
    def fake_local(prompt, temperature=0.2):
        return "💬 Local title"

    def fake_openai(prompt, temperature=0.3):
        return "Should not use OpenAI"

    fake_local.__name__ = "_title_via_local"
    fake_openai.__name__ = "_title_via_openai"
    monkeypatch.setattr("api.chat_titler._title_via_local", fake_local)
    monkeypatch.setattr("api.chat_titler._title_via_openai", fake_openai)
    title = _generate_title(
        [{"role": "user", "content": "hello"}],
        "local",
    )
    assert title == "💬 Local title"


def test_title_honors_completion_provider_preference(monkeypatch):
    import api.chat_titler as titler
    monkeypatch.setattr('api.completion_providers.resolve_order', lambda: ['local', 'openai', 'anthropic'])
    monkeypatch.setattr(titler, '_title_via_local', lambda *a, **k: 'Fix commit staging')
    monkeypatch.setattr(titler, '_title_via_openai', lambda *a, **k: (_ for _ in ()).throw(AssertionError('wrong provider')))
    assert titler._generate_title([{'role': 'user', 'content': 'fix commits'}], 'auto') == 'Fix commit staging'


def test_title_delegates_model_selection_to_completion_owner(monkeypatch):
    import api.chat_titler as titler
    monkeypatch.delenv('CHAT_TITLE_OPENAI_MODEL', raising=False)
    captured = {}
    def complete(**kwargs):
        captured.update(kwargs)
        return 'Fix naming'
    monkeypatch.setattr('api.llm_complete.complete', complete)
    assert titler._title_via_openai('prompt') == 'Fix naming'
    assert captured['openai_model'] is None

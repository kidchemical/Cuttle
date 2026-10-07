"""Tests for commit message suggestion helpers."""

from api.commit_message_suggester import (
    build_intent_heuristic,
    build_suggest_prompt,
    sanitize_commit_message,
    subject_from_user_prompts,
    suggest_commit_message,
)
from scripts.utilities.git_pending_changes import heuristic_commit_message


def test_sanitize_commit_message_strips_noise():
    assert sanitize_commit_message('Commit message: "Fix the bug."') == "Fix the bug"
    assert sanitize_commit_message("<think>nope</think>\nAdd forms\n") == "Add forms"
    long = "word " * 30
    out = sanitize_commit_message(long)
    assert len(out) <= 73
    assert out.endswith("…")


def test_heuristic_commit_message():
    assert heuristic_commit_message([]) == "Update project files"
    assert (
        heuristic_commit_message([{"path": "src/a.py", "status": "modified"}])
        == "Update a.py"
    )
    assert (
        heuristic_commit_message([{"path": "a.py", "status": "added"}])
        == "Add a.py"
    )
    grouped = heuristic_commit_message(
        [
            {"path": "src/web/js/chat/chat_page.js", "status": "modified"},
            {"path": "src/web/css/chat_page.css", "status": "modified"},
            {"path": "src/api/web_chat_api.py", "status": "modified"},
        ]
    )
    assert "and 2 more" not in grouped
    assert "src/web" in grouped
    assert "src/api" in grouped


def test_subject_from_user_prompts_prefers_work_intent():
    files = [
        {"path": "src/web/js/chat/chat_page.js", "status": "modified"},
        {"path": "src/api/commit_message_suggester.py", "status": "modified"},
    ]
    msg = subject_from_user_prompts(
        [
            "thanks",
            "can you add ability to mark pending changes include / exclude / ignore files",
            "fix slash palette reopening after escape",
            "improve commit message quality — too inventory-like",
        ],
        files=files,
        diff_excerpt="+function setPendingFileIncluded\n+def subject_from_user_prompts",
    )
    assert msg
    assert "and 2 more" not in msg.lower()
    assert "src/api" not in msg
    # Should sound like the work, not a directory list.
    low = msg.lower()
    assert any(k in low for k in ("pending", "commit", "slash", "include", "quality", "palette"))


def test_build_intent_heuristic_uses_prompts_over_areas():
    ctx = {
        "files": [
            {"path": "src/api/a.py", "status": "modified"},
            {"path": "src/scripts/b.py", "status": "modified"},
            {"path": "src/tests/c.py", "status": "modified"},
        ],
        "diff_excerpt": "+def ignore_pending_path\n+pendingFileInclusion",
        "heuristic_message": "Update src/api, src/scripts, and src/tests",
    }
    out = build_intent_heuristic(
        ctx,
        user_prompts=[
            "add include/exclude/ignore for pending commit files",
        ],
    )
    assert "src/api" not in out
    assert "include" in out.lower() or "pending" in out.lower() or "ignore" in out.lower()


def test_build_suggest_prompt_includes_prompts_and_diff():
    p = build_suggest_prompt(
        file_summary="modified  src/x.py (+3 -1)",
        diff_excerpt="diff --git a/src/x.py",
        user_prompts=["add auto commit messages", "/cursor tweak ui"],
        recent_subjects=[
            "Fix pending strip",
            "Update web_chat_api.py and 5 more files",
        ],
        area_summary="src/web (2 files)",
    )
    assert "add auto commit messages" in p
    assert "src/x.py" in p
    assert "Fix pending strip" in p
    assert "and 5 more files" not in p  # inventory subjects filtered
    assert p.index("Diff:") < p.index("Files (inventory")
    assert "do not copy this as the subject" in p
    assert "src/web (2 files)" in p


def test_suggest_falls_back_to_intent_heuristic(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    ctx = {
        "files": [{"path": "src/web/js/chat/chat_page.js", "status": "modified"}],
        "file_summary": "modified chat_page.js (+1 -0)",
        "diff_excerpt": "+pendingFileInclusion",
        "heuristic_message": "Update src/api, src/scripts, and src/tests",
        "recent_subjects": [],
        "totals": {"files": 1},
    }

    def boom(*_a, **_k):
        raise RuntimeError("nope")

    monkeypatch.setattr("api.commit_message_suggester._via_openai", boom)
    monkeypatch.setattr("api.commit_message_suggester._via_anthropic", boom)
    monkeypatch.setattr(
        "api.commit_message_suggester._via_local", lambda *_a, **_k: None
    )
    out = suggest_commit_message(
        ctx,
        user_prompts=["add include/exclude for pending git commits"],
    )
    assert out["source"] == "heuristic"
    assert "src/api" not in out["message"]
    assert "include" in out["message"].lower() or "pending" in out["message"].lower()


def test_suggest_prefers_openai(monkeypatch):
    ctx = {
        "files": [{"path": "src/a.py", "status": "modified"}],
        "file_summary": "modified a.py",
        "diff_excerpt": "+def foo",
        "heuristic_message": "Update a.py",
        "recent_subjects": [],
        "totals": {"files": 1},
    }

    def fake_openai(*_a, **_k):
        return "Add pending file include/exclude controls"

    def fake_anthropic(*_a, **_k):
        return "Should not use Haiku"

    # Keep __name__ matching real providers so source tagging stays stable.
    fake_openai.__name__ = "_via_openai"
    fake_anthropic.__name__ = "_via_anthropic"
    monkeypatch.setattr("api.commit_message_suggester._via_openai", fake_openai)
    monkeypatch.setattr("api.commit_message_suggester._via_anthropic", fake_anthropic)
    out = suggest_commit_message(ctx, user_prompts=[])
    assert out["source"] == "openai"
    assert "include" in out["message"].lower()


def test_usable_subject_rejects_inventory():
    from api.commit_message_suggester import _usable_subject

    assert _usable_subject("Add include/exclude for pending commits", file_count=12)
    assert _usable_subject("Update chat_page.js and 11 more files", file_count=12) == ""
    assert _usable_subject("Update src/api, src/scripts, and src/tests", file_count=6) == ""


def test_suggest_avoids_previous_subjects(monkeypatch):
    ctx = {
        "files": [{"path": "src/a.py", "status": "modified"}],
        "file_summary": "modified a.py",
        "diff_excerpt": "+def foo",
        "heuristic_message": "Update a.py",
        "recent_subjects": [],
        "totals": {"files": 1},
    }
    calls = {"n": 0}

    def fake_openai(prompt, file_count=0, temperature=0.3):
        calls["n"] += 1
        assert "Do NOT reuse" in prompt
        if calls["n"] == 1:
            return "Update pending changes naming"
        return "Vary commit message suggestions"

    fake_openai.__name__ = "_via_openai"
    monkeypatch.setattr("api.commit_message_suggester._via_openai", fake_openai)
    monkeypatch.setattr(
        "api.commit_message_suggester._via_anthropic",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "api.commit_message_suggester._via_local",
        lambda *_a, **_k: None,
    )
    out = suggest_commit_message(
        ctx,
        user_prompts=[],
        avoid_messages=["Update pending changes naming"],
    )
    assert out["message"] == "Vary commit message suggestions"
    assert calls["n"] >= 2


def test_commit_suggestion_honors_completion_preference(monkeypatch):
    import api.commit_message_suggester as suggester
    monkeypatch.setattr('api.completion_providers.resolve_order', lambda: ['local', 'openai', 'anthropic'])
    monkeypatch.setattr(suggester, '_via_local', lambda *a, **k: 'Fix selected file staging')
    monkeypatch.setattr(suggester, '_via_openai', lambda *a, **k: (_ for _ in ()).throw(AssertionError('wrong provider')))
    result = suggester.suggest_commit_message({'files': [{'path': 'a.py', 'status': 'modified'}]})
    assert result['message'] == 'Fix selected file staging'


def test_commit_suggestion_delegates_model_selection(monkeypatch):
    import api.commit_message_suggester as suggester
    monkeypatch.delenv('COMMIT_MSG_OPENAI_MODEL', raising=False)
    captured = {}
    def complete(**kwargs):
        captured.update(kwargs)
        return 'Fix selected staging'
    monkeypatch.setattr('api.llm_complete.complete', complete)
    assert suggester._via_openai('prompt') == 'Fix selected staging'
    assert captured['openai_model'] is None

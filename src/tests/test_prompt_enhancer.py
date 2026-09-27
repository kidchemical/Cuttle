"""Unit tests for the composer wand prompt enhancer (no network)."""

import pytest

from api import prompt_enhancer as pe


class TestSplitSlashPrefix:
    def test_no_slash(self):
        assert pe.split_slash_prefix("make the tooltip smaller") == (
            "",
            "make the tooltip smaller",
        )

    def test_inline_command_keeps_only_token(self):
        prefix, body = pe.split_slash_prefix("/cursor fix the login bug now")
        assert prefix == "/cursor "
        assert body == "fix the login bug now"

    def test_short_command_line_is_routing_only(self):
        prefix, body = pe.split_slash_prefix("/cursor\n/model sonnet\nmake it faster")
        assert prefix == "/cursor\n/model sonnet\n"
        assert body == "make it faster"

    def test_bare_command_has_no_body(self):
        prefix, body = pe.split_slash_prefix("/cursor")
        assert prefix.strip() == "/cursor"
        assert body.strip() == ""


class TestCleanEnhanced:
    def test_strips_label_and_quotes_thinking(self):
        assert pe.clean_enhanced("Enhanced prompt: do the thing") == "do the thing"
        assert pe.clean_enhanced("<think>hmm</think>\ndo the thing") == "do the thing"

    def test_strips_wrapper_tags_and_echoed_context(self):
        raw = "<prompt_to_rewrite>\nProject: Cuttle\nAdd a wand button\n</prompt_to_rewrite>"
        assert pe.clean_enhanced(raw) == "Add a wand button"

    def test_unwraps_single_fence_but_keeps_inner_code(self):
        assert pe.clean_enhanced("```\nhello\n```") == "hello"
        nested = "Fix this:\n\n```py\nprint(1)\n```"
        assert pe.clean_enhanced(nested) == nested


class TestBuildEnhancePrompt:
    def test_context_is_delimited(self):
        out = pe.build_enhance_prompt(
            "do it",
            project_name="Cuttle",
            context_messages=[{"role": "user", "content": "earlier ask"}],
        )
        assert "<context>" in out and "</context>" in out
        assert "Project: Cuttle" in out
        assert "<prompt_to_rewrite>\ndo it\n</prompt_to_rewrite>" in out

    def test_no_context_block_when_empty(self):
        out = pe.build_enhance_prompt("do it")
        assert "<context>" not in out


class TestEnhancePrompt:
    def test_empty_prompt_rejected(self):
        result = pe.enhance_prompt("   ")
        assert result["ok"] is False
        assert "Empty" in result["error"]

    def test_too_long_rejected(self):
        result = pe.enhance_prompt("x" * (pe.MAX_PROMPT_CHARS + 1))
        assert result["ok"] is False
        assert "too long" in result["error"].lower()

    def test_slash_only_rejected(self):
        result = pe.enhance_prompt("/cursor")
        assert result["ok"] is False
        assert "slash command" in result["error"]

    def test_slash_prefix_preserved(self, monkeypatch):
        monkeypatch.setattr(pe, "_via_openai", lambda p: "Fix the login redirect loop")
        result = pe.enhance_prompt("/cursor fix login pls")
        assert result["ok"] is True
        assert result["prompt"] == "/cursor Fix the login redirect loop"
        assert result["source"] == "openai"

    def test_falls_through_to_next_provider(self, monkeypatch):
        def boom(_prompt):
            raise RuntimeError("no key")

        monkeypatch.setattr(pe, "_via_openai", boom)
        monkeypatch.setattr(pe, "_via_anthropic", lambda p: "Rewritten ask")
        result = pe.enhance_prompt("rough ask")
        assert result["ok"] is True
        assert result["source"] == "anthropic"

    def test_local_mode_skips_cloud(self, monkeypatch):
        def fail(_prompt):
            raise AssertionError("cloud provider must not run in local mode")

        monkeypatch.setattr(pe, "_via_openai", fail)
        monkeypatch.setattr(pe, "_via_anthropic", fail)
        monkeypatch.setattr(pe, "_via_local", lambda p: "Local rewrite")
        result = pe.enhance_prompt("rough ask", inference_mode="local")
        assert result["prompt"] == "Local rewrite"
        assert result["source"] == "local"

    def test_all_providers_unavailable(self, monkeypatch):
        monkeypatch.setattr(pe, "_via_openai", lambda p: None)
        monkeypatch.setattr(pe, "_via_anthropic", lambda p: None)
        monkeypatch.setattr(pe, "_via_local", lambda p: None)
        result = pe.enhance_prompt("rough ask")
        assert result["ok"] is False
        assert result["prompt"] == "rough ask"

    def test_unchanged_output_flagged(self, monkeypatch):
        monkeypatch.setattr(pe, "_via_openai", lambda p: "already clear")
        result = pe.enhance_prompt("already clear")
        assert result["ok"] is True
        assert result["unchanged"] is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

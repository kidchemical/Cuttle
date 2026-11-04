"""Every agent's reasoning rides in its chat reply as one collapsed <think> block."""
import pytest

from api.agent_harness.reasoning_reply import turn_thinking, with_thinking


def test_with_thinking_merges_into_existing_progress_block_and_skips_duplicates():
    assert with_thinking([], "answer") == "answer"
    assert with_thinking(["why"], "answer") == "<think>\nwhy\n</think>\n\nanswer"
    # Cursor progress narration already leads the reply: one block, reasoning first.
    assert (with_thinking(["why"], "<think>\nChecking files\n</think>\n\nanswer")
            == "<think>\nwhy\n\nChecking files\n</think>\n\nanswer")
    # Already carried (Claude detached follow-ups wrap their own).
    assert with_thinking(["why"], "<think>\nwhy\n</think>\n\nanswer") == "<think>\nwhy\n</think>\n\nanswer"
    assert "&lt;/think>" in with_thinking(["quoting </think>"], "x")


@pytest.mark.parametrize("marker", ["[CANCELLED] stopped", "[FAIL] quota", "❌ broke"])
def test_status_marked_replies_are_untouched(marker):
    """Router/persist/recovery classify replies by their leading marker."""
    assert with_thinking(["why"], marker) == marker


def test_turn_thinking_reads_full_top_level_blocks_only():
    class T:
        execution_data = {"events": [
            {"kind": "thinking", "text": "preview…"},  # status-line preview
            {"kind": "thinking", "text": "real", "agent_text": True},
            {"kind": "thinking", "text": "child", "agent_text": True, "nested": True},
            {"kind": "writing", "text": "w", "agent_text": True},
            {"kind": "thinking", "text": "real", "agent_text": True},
        ]}
    assert turn_thinking(T()) == ["real"]


def _manifest(agent_id):
    from api.agent_harness.types import AgentManifest
    return AgentManifest(
        id=agent_id, label=agent_id, slash=f"/{agent_id}", models=[], resume=False,
        capabilities_inject="never", env_profile="native", activity="heartbeat",
        missing_cli_hint="", notes="", hint="", install_hint="", install_kind="",
        install_package="", install_url_windows="", install_url_posix="",
        executable_names=[], auto_install=False, schema_version=1, source="test",
    )


@pytest.mark.parametrize("agent_id", ["codex", "muse", "opencode"])
@pytest.mark.parametrize("chat", [True, False])
def test_kernel_adds_any_agents_reasoning_to_chat_replies(monkeypatch, tmp_path, agent_id, chat):
    from api.agent_harness import kernel
    from api.agent_harness.activity import TextActivityLog
    from api.agent_harness.types import AgentResult

    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path))

    class _Adapter:
        def available(self): return True
        def resolve_cwd(self, project_path): return project_path or "."
        def load_resume(self, cwd, sid): return None
        def save_resume(self, cwd, sid, cli): pass
        def clear_resume(self, cwd, sid): pass

        async def execute(self, prompt, *, cwd, resume, model, status_queue=None,
                          chat_session_id=None, timeout=600.0):
            log = TextActivityLog(agent_id)
            log.nested = lambda item: item.startswith("child")
            log.save("thinking", "The answer, written as reasoning", "r1")
            log.save("thinking", "sub-agent reasoning", "child-1")
            log.save("writing", "closing line", "w1")
            return AgentResult(success=True, output="closing line", session_id="s")

    monkeypatch.setattr(kernel, "get_agent", lambda _id, project_path=None: (_manifest(agent_id), _Adapter()))
    monkeypatch.setattr(kernel, "_compile_agent_prompt",
                        lambda *a, **kw: (kw.get("prompt") or (a[1] if len(a) > 1 else ""), {}))
    monkeypatch.setattr("api.chat_run_registry.begin_run", lambda sid, qid: __import__("threading").Event())
    out = kernel.run_agent_web_command(agent_id, "go", 9002 if chat else None, project_path=str(tmp_path))
    if chat:
        assert out["response"] == "<think>\nThe answer, written as reasoning\n</think>\n\nclosing line"
    else:
        assert out["response"] == "closing line"


def test_short_reasoning_that_also_appears_in_the_answer_is_kept():
    assert with_thinking(["a"], "answer") == "<think>\na\n</think>\n\nanswer"

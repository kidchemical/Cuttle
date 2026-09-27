"""Tests for Cuttle Brain Context Compiler + hot-swap handoff."""

from __future__ import annotations

from pathlib import Path


def test_compile_includes_rules_and_user_prompt(tmp_path):
    from api.cuttle_brain.context_compiler import compile_context

    rules = tmp_path / ".cuttle" / "rules"
    rules.mkdir(parents=True)
    (rules / "01-test.md").write_text("Never invent parallel config paths.", encoding="utf-8")
    (tmp_path / ".cuttle" / "commands").mkdir(parents=True)
    (tmp_path / ".cuttle" / "commands" / "build.md").write_text("# build\n", encoding="utf-8")

    compiled = compile_context(
        "do the thing",
        project_path=str(tmp_path),
        inject_capabilities=False,
    )
    assert "do the thing" in compiled.prompt
    assert "Never invent parallel config paths." in compiled.prompt
    assert "project_rules" in compiled.layers_used
    assert "profile" in compiled.layers_used
    assert "/build" in compiled.prompt
    assert compiled.prompt.index("Never invent") < compiled.prompt.index("do the thing")


def test_compile_includes_hub_rules_for_sibling_project(tmp_path):
    from api.cuttle_brain.context_compiler import compile_context, load_hub_rules

    hub_rules = load_hub_rules()
    if not hub_rules:
        return  # Cuttle hub not present in this checkout layout

    rules = tmp_path / ".cuttle" / "rules"
    rules.mkdir(parents=True)
    (rules / "01-local.md").write_text("Local project rule.", encoding="utf-8")

    compiled = compile_context(
        "sync discord",
        project_path=str(tmp_path),
        inject_capabilities=False,
    )
    assert "sync discord" in compiled.prompt
    assert "Local project rule." in compiled.prompt
    assert "Cuttle hub rules" in compiled.prompt
    assert "hub_rules" in compiled.layers_used
    assert "project_rules" in compiled.layers_used
    # Hub 00-core should mention the native discord runbook when present.
    hub_text = " ".join(t for _, t in hub_rules).lower()
    if "discord" in hub_text:
        assert "discord.md" in compiled.prompt.lower()


def test_compile_capabilities_once_flag(tmp_path):
    from api.cuttle_brain.context_compiler import compile_context

    with_caps = compile_context("hi", project_path=str(tmp_path), inject_capabilities=True)
    without = compile_context("hi", project_path=str(tmp_path), inject_capabilities=False)
    assert "<cuttle_ui_capabilities>" in with_caps.prompt
    assert "<cuttle_ui_capabilities>" not in without.prompt
    assert "<cuttle_context>" in with_caps.envelope
    # Anti-narration contract is always present; user ask is explicitly demarcated.
    assert "not the user speaking" in with_caps.prompt.lower()
    assert "## User request\nhi" in with_caps.prompt
    close_at = with_caps.prompt.rfind("</cuttle_context>")
    assert close_at > 0
    assert with_caps.prompt.index("## User request", close_at) > close_at
    assert without.schema_version >= 2


def test_envelope_narration_detector():
    from api.cuttle_brain.context_compiler import looks_like_envelope_narration

    # CH-000150-8 shape
    assert looks_like_envelope_narration(
        "Yes, I can read the context and understand the configuration you provided."
    )
    assert looks_like_envelope_narration(
        "I received a context message without a specific request."
    )
    assert not looks_like_envelope_narration("pong")
    assert not looks_like_envelope_narration("Yes — the file is at src/api/foo.py")


def test_inventory_lists_cuttle_files(tmp_path):
    from api.cuttle_brain.context_compiler import project_inventory

    cuttle = tmp_path / ".cuttle"
    (cuttle / "docs").mkdir(parents=True)
    (cuttle / "actions").mkdir(parents=True)
    (cuttle / "docs" / "runbook.md").write_text("x", encoding="utf-8")
    (cuttle / "actions" / "demo.yaml").write_text("name: demo\n", encoding="utf-8")
    inv = project_inventory(str(tmp_path))
    assert "runbook.md" in inv["docs"]
    assert "demo.yaml" in inv["actions"]


def test_handoff_only_when_agent_changes(tmp_path, monkeypatch):
    from api.cuttle_brain import handoff as ho

    monkeypatch.setattr(ho, "_map_file", lambda: tmp_path / "last_agent.json")
    monkeypatch.setattr(ho, "fetch_recent_transcript", lambda *a, **k: [
        {"role": "user", "content": "earlier question"},
        {"role": "assistant", "content": "earlier answer"},
    ])

    assert ho.build_handoff("42", to_agent="opencode") is None
    ho.record_last_agent("42", "antigravity")
    assert ho.get_last_agent(42) == "antigravity"

    delta = ho.build_handoff("42", to_agent="opencode")
    assert delta is not None
    assert delta.from_agent == "antigravity"
    assert delta.to_agent == "opencode"
    assert "earlier question" in delta.text
    assert "Agent handoff" in delta.text

    # Same agent again → no handoff
    assert ho.build_handoff("42", to_agent="antigravity") is None


def test_kernel_same_agent_skips_handoff_but_uses_resume(monkeypatch, tmp_path):
    import asyncio
    from api.agent_harness import kernel
    from api.agent_harness.types import AgentManifest, AgentResult
    from api.cuttle_brain import handoff as ho

    monkeypatch.setattr(ho, "_map_file", lambda: tmp_path / "last_agent.json")
    ho.record_last_agent("9001", "fakeprobe")

    seen = {"resume": None, "prompt": None}

    class _FakeAdapter:
        def available(self):
            return True

        def resolve_cwd(self, project_path: str) -> str:
            return str(tmp_path)

        def load_resume(self, cwd, chat_session_id):
            return "native-sid-1"

        def save_resume(self, cwd, chat_session_id, cli_session_id):
            pass

        def clear_resume(self, cwd, chat_session_id):
            pass

        async def execute(self, prompt, **kwargs):
            seen["resume"] = kwargs.get("resume")
            seen["prompt"] = prompt
            return AgentResult(success=True, output="ok", session_id="native-sid-1")

    manifest = AgentManifest(
        id="fakeprobe",
        label="Fake",
        slash="/fakeprobe",
        resume=True,
        capabilities_inject="once_per_resume",
    )
    monkeypatch.setattr(
        kernel, "get_agent", lambda aid, project_path=None: (manifest, _FakeAdapter())
    )
    monkeypatch.setattr(
        "api.query_tracker.start_query_tracking", lambda *a, **k: "qid"
    )
    monkeypatch.setattr(
        "api.query_tracker.finish_query_tracking", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "api.query_tracker.get_query_tracker", lambda *a, **k: None
    )
    monkeypatch.setattr("api.active_executions.register_execution", lambda *a, **k: None)
    monkeypatch.setattr("api.active_executions.unregister_execution", lambda *a, **k: None)

    out = kernel.run_agent_web_command("fakeprobe", "follow up", 9001, project_path=str(tmp_path))
    assert out.get("response") == "ok"
    assert seen["resume"] == "native-sid-1"
    # Same agent + resume → bare user prompt (no envelope / handoff).
    assert seen["prompt"] == "follow up"


def test_kernel_switch_injects_handoff_and_keeps_target_resume(monkeypatch, tmp_path):
    from api.agent_harness import kernel
    from api.agent_harness.types import AgentManifest, AgentResult
    from api.cuttle_brain import handoff as ho

    monkeypatch.setattr(ho, "_map_file", lambda: tmp_path / "last_agent.json")
    monkeypatch.setattr(
        ho,
        "fetch_recent_transcript",
        lambda *a, **k: [{"role": "user", "content": "from antigravity era"}],
    )
    ho.record_last_agent("9002", "antigravity")

    seen = {"resume": None, "prompt": None}

    class _FakeAdapter:
        def available(self):
            return True

        def resolve_cwd(self, project_path: str) -> str:
            return str(tmp_path)

        def load_resume(self, cwd, chat_session_id):
            return "opencode-prior-sid"

        def save_resume(self, cwd, chat_session_id, cli_session_id):
            pass

        def clear_resume(self, cwd, chat_session_id):
            pass

        async def execute(self, prompt, **kwargs):
            seen["resume"] = kwargs.get("resume")
            seen["prompt"] = prompt
            return AgentResult(success=True, output="switched", session_id="opencode-prior-sid")

    manifest = AgentManifest(
        id="opencode",
        label="OpenCode",
        slash="/opencode",
        resume=True,
        capabilities_inject="once_per_resume",
    )
    monkeypatch.setattr(
        kernel, "get_agent", lambda aid, project_path=None: (manifest, _FakeAdapter())
    )
    monkeypatch.setattr(
        "api.query_tracker.start_query_tracking", lambda *a, **k: "qid"
    )
    monkeypatch.setattr(
        "api.query_tracker.finish_query_tracking", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "api.query_tracker.get_query_tracker", lambda *a, **k: None
    )
    monkeypatch.setattr("api.active_executions.register_execution", lambda *a, **k: None)
    monkeypatch.setattr("api.active_executions.unregister_execution", lambda *a, **k: None)

    out = kernel.run_agent_web_command("opencode", "continue here", 9002, project_path=str(tmp_path))
    assert out.get("response") == "switched"
    assert seen["resume"] == "opencode-prior-sid"
    assert "Agent handoff" in (seen["prompt"] or "")
    assert "from antigravity era" in (seen["prompt"] or "")
    assert "antigravity" in (seen["prompt"] or "")
    # Target already had resume → handoff only, not a full fresh envelope.
    assert "<cuttle_ui_capabilities>" not in (seen["prompt"] or "")
    assert ho.get_last_agent("9002") == "opencode"


def test_cli_inventory(tmp_path):
    from api.cuttle_brain.cli import main

    cuttle = tmp_path / ".cuttle" / "rules"
    cuttle.mkdir(parents=True)
    (cuttle / "a.md").write_text("rule", encoding="utf-8")
    assert main(["inventory", "--project", str(tmp_path)]) == 0


def test_win_cli_prefers_exe(tmp_path):
    from api.agent_harness.win_cli import prefer_native_binary

    shim = tmp_path / "tool.cmd"
    native = tmp_path / "tool.exe"
    shim.write_text("@echo off\n", encoding="utf-8")
    native.write_bytes(b"native")
    assert prefer_native_binary(str(shim), is_windows=True) == str(native)

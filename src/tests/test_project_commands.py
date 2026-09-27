"""Tests for project-local .cuttle/commands discovery and expansion."""

from __future__ import annotations

from pathlib import Path

from api.project_commands import (
    build_agent_prompt,
    find_project_command,
    list_project_commands,
    try_expand_message_project_command,
)


def _write_cmd(root: Path, name: str, body: str, front: str = "") -> None:
    d = root / ".cuttle" / "commands"
    d.mkdir(parents=True, exist_ok=True)
    text = front + body
    (d / f"{name}.md").write_text(text, encoding="utf-8")


def test_list_and_find_project_commands(tmp_path: Path):
    _write_cmd(
        tmp_path,
        "build",
        "# Build\n\nDo the build.\n",
        "---\nname: build\ndescription: Build the game\ntitle: build\n---\n\n",
    )
    cmds = list_project_commands(str(tmp_path))
    assert len(cmds) == 1
    assert cmds[0]["name"] == "build"
    assert cmds[0]["description"] == "Build the game"
    found = find_project_command(str(tmp_path), "build")
    assert found and found["path"].endswith("build.md")


def test_nested_source_commands_dir(tmp_path: Path):
    nested = tmp_path / "source" / ".cuttle" / "commands"
    nested.mkdir(parents=True)
    (nested / "deploy.md").write_text(
        "---\nname: deploy\ndescription: Upload\n---\n\n# Deploy\n",
        encoding="utf-8",
    )
    cmds = list_project_commands(str(tmp_path))
    assert any(c["name"] == "deploy" for c in cmds)


def test_expand_with_cursor_sticky(tmp_path: Path):
    _write_cmd(
        tmp_path,
        "build",
        "Run Unity batchmode.\n",
        "---\nname: build\ndescription: Build\n---\n\n",
    )
    exp = try_expand_message_project_command("/cursor /build shipping", str(tmp_path))
    assert exp and exp["action"] == "prompt"
    assert exp["message"].startswith("/cursor ")
    assert "shipping" in exp["message"]
    assert "Unity batchmode" in exp["message"]


def test_expand_cmd_form(tmp_path: Path):
    _write_cmd(
        tmp_path,
        "deploy",
        "Steampipe upload.\n",
        "---\nname: deploy\ndescription: Deploy\n---\n\n",
    )
    exp = try_expand_message_project_command("/cmd deploy", str(tmp_path))
    assert exp and exp["action"] == "prompt"
    assert "Steampipe" in exp["prompt"]


def test_build_agent_prompt_includes_project_path(tmp_path: Path):
    _write_cmd(
        tmp_path,
        "x",
        "Body here.\n",
        "---\nname: x\ndescription: X\n---\n\n",
    )
    cmd = find_project_command(str(tmp_path), "x")
    prompt = build_agent_prompt(cmd, "extra")
    assert "Body here" in prompt
    assert "extra" in prompt
    assert str(tmp_path.resolve()) in prompt or "Project root" in prompt


def test_expand_shell_plus_watch_ignores_sticky_agent(tmp_path: Path):
    from api.project_commands import format_project_command_shell_reply

    _write_cmd(
        tmp_path,
        "build",
        "Should not become an agent prompt.\n",
        "---\nname: build\nexecute: shell\nrun: echo kicked\nwatch:\n  id: ep-release\n  title: Private build\n---\n\n",
    )
    exp = try_expand_message_project_command("/cursor /build", str(tmp_path))
    assert exp and exp["action"] == "shell"
    cmd = exp["command"]
    assert cmd.get("watch", {}).get("id") == "ep-release"
    reply = format_project_command_shell_reply(
        cmd,
        {
            "success": True,
            "output": "[1%] running — Starting /build…\nKicked /build. Poll /output/ep-release-status.json",
            "exit_code": 0,
        },
    )
    assert "<cuttle_action_form>" in reply
    assert "/output/ep-release-status.json" in reply
    assert "__watch_resume__" in reply
    assert "```" not in reply
    assert "Kicked /build" not in reply
    assert "Poll /output" not in reply


def test_shell_failure_has_no_watch_form(tmp_path: Path):
    from api.project_commands import format_project_command_shell_reply, find_project_command

    _write_cmd(
        tmp_path,
        "build",
        "x\n",
        "---\nname: build\nexecute: shell\nrun: echo no\nwatch:\n  id: ep-release\n---\n\n",
    )
    cmd = find_project_command(str(tmp_path), "build")
    reply = format_project_command_shell_reply(
        cmd, {"success": False, "output": "Unity already running", "exit_code": 3}
    )
    assert "<cuttle_action_form>" not in reply
    assert "failed" in reply.lower()


def test_reattach_shell_reply_has_no_second_watch_form(tmp_path: Path):
    """Second /build while ep-release is live must not mint another card."""
    from api.project_commands import format_project_command_shell_reply, find_project_command

    _write_cmd(
        tmp_path,
        "build",
        "x\n",
        "---\nname: build\nexecute: shell\nrun: echo no\nwatch:\n  id: ep-release\n---\n\n",
    )
    cmd = find_project_command(str(tmp_path), "build")
    reply = format_project_command_shell_reply(
        cmd,
        {
            "success": False,
            "exit_code": 4,
            "error": (
                "A job is already running on this watch card. "
                "Stop it first, then run again for a new build (new form id)."
            ),
            "output": "",
        },
    )
    assert "<cuttle_action_form>" not in reply
    assert "already running" in reply.lower()
    assert "own build and form" in reply.lower()

    legacy = format_project_command_shell_reply(
        cmd,
        {
            "success": True,
            "exit_code": 0,
            "output": "A release job is already running. Reattach the watch form.",
        },
    )
    assert "<cuttle_action_form>" not in legacy
    assert "already running" in legacy.lower()

    # Timeout-after-successful-kick still gets a form (new attempt / new run).
    kicked = format_project_command_shell_reply(
        cmd,
        {
            "success": True,
            "exit_code": 0,
            "output": "Kicked — job already running (reattached).",
            "reattached": True,
        },
    )
    assert "<cuttle_action_form>" in kicked
    assert "**/build** started" in kicked


def test_watch_job_running_short_circuits_without_new_form(tmp_path: Path, monkeypatch):
    """Live watch job → fail fast, no kick, no second form for the same run."""
    from api.project_commands import (
        find_project_command,
        format_project_command_shell_reply,
        run_project_command_shell,
    )

    _write_cmd(
        tmp_path,
        "build",
        "x\n",
        "---\nname: build\nexecute: shell\nrun: echo should-not-run\nwatch:\n  id: ep-release\n---\n\n",
    )
    cmd = find_project_command(str(tmp_path), "build")
    monkeypatch.setattr("api.project_commands._watch_job_running", lambda _id: True)
    result = run_project_command_shell(cmd, session_id="already_running_gate")
    assert result.get("success") is False
    assert result.get("exit_code") == 4
    reply = format_project_command_shell_reply(cmd, result)
    assert "<cuttle_action_form>" not in reply
    assert "already running" in reply.lower()


def test_cancelled_shell_reply_has_no_watch_form(tmp_path: Path):
    from api.project_commands import format_project_command_shell_reply, find_project_command

    _write_cmd(
        tmp_path,
        "build",
        "x\n",
        "---\nname: build\nexecute: shell\nrun: echo no\nwatch:\n  id: ep-release\n---\n\n",
    )
    cmd = find_project_command(str(tmp_path), "build")
    reply = format_project_command_shell_reply(
        cmd, {"success": False, "cancelled": True, "error": "Cancelled"}
    )
    assert "<cuttle_action_form>" not in reply
    assert "cancelled" in reply.lower()


def test_shell_command_stop_kills_process(tmp_path: Path):
    import json
    import sys
    import threading
    import time

    from api import chat_run_registry as reg
    from api.project_commands import find_project_command, run_project_command_shell

    reg._runs.clear()
    reg._session_jobs.clear()
    recipe = f'{sys.executable} -c "import time; time.sleep(40)"'
    _write_cmd(
        tmp_path,
        "hang",
        "x\n",
        (
            "---\nname: hang\nexecute: shell\ntimeout: 40\n"
            f"run: {json.dumps(recipe)}\n---\n\n"
        ),
    )
    cmd = find_project_command(str(tmp_path), "hang")
    sid = "shell_cancel_test"

    def later():
        time.sleep(0.5)
        reg.cancel_session_runs(sid)

    t = threading.Thread(target=later, daemon=True)
    t.start()
    started = time.time()
    result = run_project_command_shell(cmd, session_id=sid)
    t.join(timeout=10)
    assert result.get("cancelled") is True
    assert (time.time() - started) < 20


def test_shell_echo_succeeds(tmp_path: Path):
    from api import chat_run_registry as reg
    from api.project_commands import find_project_command, run_project_command_shell

    reg._runs.clear()
    reg._session_jobs.clear()
    _write_cmd(
        tmp_path,
        "ok",
        "x\n",
        "---\nname: ok\nexecute: shell\nrun: echo kicked\n---\n\n",
    )
    cmd = find_project_command(str(tmp_path), "ok")
    result = run_project_command_shell(cmd, session_id="shell_ok_test")
    assert result.get("success") is True
    assert "kicked" in (result.get("output") or "").lower()


def test_timeout_reattaches_when_watch_job_running(tmp_path: Path, monkeypatch):
    import json
    import sys

    from api import chat_run_registry as reg
    from api.project_commands import find_project_command, run_project_command_shell

    reg._runs.clear()
    reg._session_jobs.clear()
    # Must outlive timeout so the deadline branch (not normal exit) fires.
    # Gate at the top of run_project_command_shell is off (job not "running"
    # yet); mid-run the monkeypatch makes the timeout path reattach.
    recipe = f'{sys.executable} -c "import time\\nwhile True: time.sleep(1)"'
    _write_cmd(
        tmp_path,
        "build",
        "x\n",
        (
            "---\nname: build\nexecute: shell\ntimeout: 1\n"
            "watch:\n  id: ep-release\n"
            f"run: {json.dumps(recipe)}\n---\n\n"
        ),
    )
    cmd = find_project_command(str(tmp_path), "build")
    calls = {"n": 0}

    def _running(_id):
        # First check (preflight gate) → False so the kick starts.
        # Later checks (timeout path) → True so we reattach instead of killing.
        calls["n"] += 1
        return calls["n"] > 1

    monkeypatch.setattr("api.project_commands._watch_job_running", _running)
    result = run_project_command_shell(cmd, session_id="reattach_test")
    assert result.get("success") is True
    assert result.get("cancelled") is not True
    assert result.get("reattached") is True

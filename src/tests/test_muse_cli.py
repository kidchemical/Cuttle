"""Muse Code CLI — JSONL parse, session map, WSL path helpers, and mocked exec."""

from __future__ import annotations

from pathlib import Path
import os
import queue
from unittest.mock import patch

import pytest

from scripts.utilities.muse_cli_tool import (
    DEFAULT_MUSE_MODEL,
    _muse_activity_for_event,
    _parse_muse_jsonl,
    clear_muse_default_model_cache,
    muse_model_label,
    resolve_muse_default_model,
    usage_for_query_report,
    windows_to_wsl_path,
    MuseCliTool,
)
from scripts.utilities import muse_cli_session_store as store


@pytest.fixture(autouse=True)
def _isolate_muse_cli_default(monkeypatch):
    """Don't read the live WSL Muse settings.json during unit tests."""
    clear_muse_default_model_cache()
    from scripts.utilities.muse_cli_tool import clear_muse_catalog_cache

    clear_muse_catalog_cache()
    monkeypatch.setattr(
        "scripts.utilities.muse_cli_tool.read_muse_cli_settings_model",
        lambda: None,
    )
    # Avoid live Meta Model API / env key side effects in unit tests.
    monkeypatch.setattr(
        "scripts.utilities.muse_cli_tool._fetch_meta_muse_spark_models",
        lambda: ([], "no MODEL_API_KEY / META_API_KEY"),
    )
    monkeypatch.setattr(
        "api.agent_harness.agent_defaults.get_starred_model",
        lambda _agent: None,
    )
    monkeypatch.setattr(
        "api.agent_harness.agent_defaults.get_starred_effort",
        lambda _agent: None,
    )
    monkeypatch.delenv("MUSE_MODEL", raising=False)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.delenv("META_API_KEY", raising=False)
    yield
    clear_muse_default_model_cache()
    clear_muse_catalog_cache()


SAMPLE_JSONL = """
{"schema_version":1,"stream":{"kind":"session","id":"3d64e574-a415-4c86-961b-d330058b5491"},"payload_type":"session.run.linked","payload":{}}
{"schema_version":1,"stream":{"kind":"session","id":"3d64e574-a415-4c86-961b-d330058b5491"},"payload_type":"run.terminal.completed","payload":{"terminal":"completed","text":"echo: Hello from Muse.\\n"}}
""".strip()


def test_muse_jsonl_activity_matches_cursor_style():
    labels, count = {}, [0]
    proposed = {
        "payload_type": "task.lifecycle.proposed",
        "payload": {"event": {"task_id": "t1", "task_kind": "tool.workspace.read_file"}},
    }
    assert _muse_activity_for_event(proposed, labels, count) == "tool 1: workspace read file"
    assert _muse_activity_for_event(
        {"payload_type": "run.output.delta", "payload": {"text": "I found the handler"}},
        labels,
        count,
    ) == "writing: …I found the handler"
    assert _muse_activity_for_event(
        {"payload_type": "task.lifecycle.failed", "payload": {"event": {"task_id": "t1"}}},
        labels,
        count,
    ) == "tool failed: workspace read file"


def test_muse_activity_covers_model_tools_and_failures():
    """Event vocabulary captured from a live `muse exec --json` run."""
    labels, count = {}, [0]
    assert _muse_activity_for_event(
        {"payload_type": "run.model.configured", "payload": {"display_label": "muse-spark-1.2"}},
        labels,
        count,
    ) == "Muse Code ready (model: muse-spark-1.2)"
    # Model steps are progress, but must not consume a tool number.
    assert _muse_activity_for_event(
        {
            "payload_type": "task.lifecycle.proposed",
            "payload": {"event": {"task_id": "m1", "task_kind": "model.meta.response"}},
        },
        labels,
        count,
    ) == "Muse Code is thinking…"
    assert _muse_activity_for_event(
        {
            "payload_type": "task.lifecycle.proposed",
            "payload": {"event": {"task_id": "t1", "task_kind": "tool.read_file"}},
        },
        labels,
        count,
    ) == "tool 1: read file"
    assert _muse_activity_for_event(
        {
            "payload_type": "task.lifecycle.output",
            "payload": {"event": {"task_id": "t1", "chunk": "Read text file `AGENTS.md`.\n1|# A"}},
        },
        labels,
        count,
    ) == "tool 1: read file · Read text file `AGENTS.md`."
    # Structured tool payloads have no readable first line.
    assert _muse_activity_for_event(
        {
            "payload_type": "task.lifecycle.output",
            "payload": {"event": {"task_id": "t1", "chunk": '{\n  "stdout": "AGENTS.md"'}},
        },
        labels,
        count,
    ) is None
    assert _muse_activity_for_event(
        {
            "payload_type": "tool.result",
            "payload": {"correlation_facts": {"tool_name": "write_file", "outcome": "error"}},
        },
        labels,
        count,
    ) == "tool failed: write file (error)"


def test_muse_activity_reports_provider_retries_only():
    """`attempt 1/10` fires on every model step; only retries are worth a line."""
    labels, count = {}, [0]
    assert _muse_activity_for_event(
        {
            "payload_type": "task.lifecycle.status",
            "payload": {"event": {"message": "opening meta model stream attempt 1/10"}},
        },
        labels,
        count,
    ) is None
    assert _muse_activity_for_event(
        {
            "payload_type": "task.lifecycle.status",
            "payload": {"event": {"message": "opening meta model stream attempt 3/10"}},
        },
        labels,
        count,
    ) == "Muse Code retrying the model stream (attempt 3/10)…"


def test_muse_activity_ignores_internal_reminders():
    assert _muse_activity_for_event(
        {
            "payload_type": "task.lifecycle.proposed",
            "payload": {"event": {"task_id": "r1", "task_kind": "reminder.agent.plugin:scope"}},
        },
        {},
        [0],
    ) is None


def test_windows_to_wsl_path():
    assert windows_to_wsl_path(r"C:\Projects\Cuttle") == "/mnt/c/Projects/Cuttle"
    assert windows_to_wsl_path("C:/Projects/Cuttle/src") == "/mnt/c/Projects/Cuttle/src"
    assert windows_to_wsl_path("/mnt/e/already") == "/mnt/e/already"


def test_native_resolver_ignores_wsl_forwarding_shims(tmp_path: Path, monkeypatch):
    """A `muse.cmd` that forwards to WSL must NOT be treated as a native binary.

    Native mode hands muse a Windows workspace path; when the shim re-enters WSL
    bash mangles the backslashes (`C:\\Projects\\Cuttle` -> `E:DevCuttle`) and the run
    dies with "workspace root does not exist". Skipping the shim lets resolution
    fall through to the real WSL binary, which converts the path to /mnt/e/...
    """
    import scripts.utilities.muse_cli_tool as muse_mod

    shim_only = tmp_path / "shim_only"
    shim_only.mkdir()
    shim = shim_only / "muse.cmd"
    shim.write_text("@echo off\r\nwsl -e bash -lc \"muse %*\"\r\n", encoding="utf-8")

    paired = tmp_path / "paired"
    paired.mkdir()
    paired_shim = paired / "muse.cmd"
    paired_shim.write_text("@echo off\r\nwsl -e bash -lc \"muse %*\"\r\n", encoding="utf-8")
    real = paired / "muse.exe"
    real.write_bytes(b"MZ")

    monkeypatch.delenv("MUSE_CLI_PATH", raising=False)
    monkeypatch.setattr(muse_mod, "_default_windows_muse_install", lambda: None)

    # Shim with no sibling .exe → not native (fall through to WSL).
    monkeypatch.setattr(muse_mod.shutil, "which", lambda name: str(shim))
    assert muse_mod._which_muse_native() is None

    # PATH shim + sibling .exe → prefer the real binary.
    monkeypatch.setattr(muse_mod.shutil, "which", lambda name: str(paired_shim))
    assert muse_mod._which_muse_native() == str(real)

    monkeypatch.setattr(muse_mod.shutil, "which", lambda name: str(real))
    assert muse_mod._which_muse_native() == str(real)

    # Explicit override to a lone shim is rejected too (same broken path handling).
    monkeypatch.setenv("MUSE_CLI_PATH", str(shim))
    monkeypatch.setattr(muse_mod.shutil, "which", lambda name: None)
    assert muse_mod._which_muse_native() is None


def test_native_resolver_prefers_meta_windows_muse_bin(tmp_path: Path, monkeypatch):
    """Meta's Windows install is muse.cmd + muse-bin-*.exe — not a WSL shim."""
    if os.name != "nt":
        pytest.skip("Windows Muse installer layout")
    import scripts.utilities.muse_cli_tool as muse_mod

    install = tmp_path / "Programs" / "muse"
    install.mkdir(parents=True)
    shim = install / "muse.cmd"
    shim.write_text(
        '@echo off\r\n'
        '"%SystemRoot%\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" '
        '-NoProfile -ExecutionPolicy Bypass -File "%~dp0.muse-launcher.ps1" %*\r\n',
        encoding="utf-8",
    )
    (install / ".muse-launcher.ps1").write_text("# launcher\n", encoding="utf-8")
    bin_exe = install / "muse-bin-1.3.0-R3233.1.exe"
    bin_exe.write_bytes(b"MZ")

    monkeypatch.delenv("MUSE_CLI_PATH", raising=False)
    monkeypatch.setattr(muse_mod, "_default_windows_muse_install", lambda: None)
    monkeypatch.setattr(muse_mod.shutil, "which", lambda name: str(shim))
    assert muse_mod._which_muse_native() == str(bin_exe)

    # Well-known install path wins even when PATH still has a WSL forwarder.
    wsl_shim_dir = tmp_path / "cuttle_scripts"
    wsl_shim_dir.mkdir()
    wsl_shim = wsl_shim_dir / "muse.cmd"
    wsl_shim.write_text("@echo off\r\nwsl -e bash -lc \"muse %*\"\r\n", encoding="utf-8")
    monkeypatch.setattr(muse_mod.shutil, "which", lambda name: str(wsl_shim))
    monkeypatch.setattr(muse_mod, "_default_windows_muse_install", lambda: str(bin_exe))
    assert muse_mod._which_muse_native() == str(bin_exe)


def test_muse_effort_store_roundtrip(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(store, "_repo_root", lambda: tmp_path)
    assert store.load_muse_effort("s1") is None
    store.save_muse_effort("s1", "high")
    assert store.load_muse_effort("s1") == "high"
    store.save_muse_effort("s1", None)
    assert store.load_muse_effort("s1") is None


def test_muse_effort_slash_set_list_reject(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(store, "_repo_root", lambda: tmp_path)
    from api.agent_harness.agents.muse.adapter import build_adapter

    adapter = build_adapter()
    store.save_muse_model("s1", "muse-spark-1.3-contributor")
    result = adapter.handle_meta("/effort high", chat_session_id="s1", model=None)
    assert result is not None
    assert result.success is True
    assert result.meta.get("agent_effort") == "high"
    # Effort replies must carry the model or badges degrade to the agent id.
    assert result.model == "muse-spark-1.3-contributor"
    assert result.meta.get("agent_model") == "muse-spark-1.3-contributor"
    assert store.load_muse_effort("s1") == "high"

    listed = adapter.handle_meta("/effort", chat_session_id="s1", model=None)
    assert listed is not None and listed.success is True
    assert "`high`" in (listed.output or "")

    # A real task starting with "effort" must not be swallowed.
    assert adapter.handle_meta("effort the login flow", chat_session_id="s1", model=None) is None

    unknown = adapter.handle_meta("/effort banana", chat_session_id="s1", model=None)
    assert unknown is not None and unknown.success is True
    assert "banana" in (unknown.output or "")
    # Failed pin must not overwrite the good one.
    assert store.load_muse_effort("s1") == "high"

    reset = adapter.handle_meta("/effort reset", chat_session_id="s1", model=None)
    assert reset is not None and reset.success is True
    assert store.load_muse_effort("s1") is None


def test_messages_payload_carries_muse_pins(tmp_path: Path, monkeypatch):
    """Badges seed from the history payload — first paint is right, no flicker."""
    from api.auth_db import AuthDatabase

    db = AuthDatabase(tmp_path / "cuttle_auth.db")
    owner = db.create_user("owner@local", "Owner", "local", password="x")
    sid = db.create_chat_session(owner, "muse pins")
    plain_sid = db.create_chat_session(owner, "no pins")
    token = db.create_auth_session(owner)

    monkeypatch.setattr(store, "_repo_root", lambda: tmp_path)
    store.save_muse_model(sid, "muse-spark-1.3-contributor")
    store.save_muse_effort(sid, "high")

    monkeypatch.setattr("api.auth_api.get_auth_db", lambda: db)
    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)
    from api import web_chat_api as wca

    client = wca.app.test_client()
    client.set_cookie("session_token", token)

    res = client.get(f"/api/auth/sessions/{sid}/messages")
    assert res.status_code == 200
    body = res.get_json()
    assert body["success"] is True
    assert body["agent_pins"]["muse"]["model"] == "muse-spark-1.3-contributor"
    assert body["agent_pins"]["muse"]["effort"] == "high"
    assert "muse_model" not in body
    assert body["session_name"] == "muse pins"

    res = client.get(f"/api/auth/sessions/{plain_sid}/messages")
    assert res.status_code == 200
    body = res.get_json()
    assert body["agent_pins"]["muse"]["model"] == DEFAULT_MUSE_MODEL
    assert body["agent_pins"]["muse"]["effort"] == ""
    assert body["session_name"] == "no pins"


def test_wsl_resolution_is_flagged_deprecated(monkeypatch):
    """WSL path stays as fallback but must advertise itself as deprecated."""
    import scripts.utilities.muse_cli_tool as muse_mod

    monkeypatch.setattr(muse_mod, "_which_muse_native", lambda: None)
    monkeypatch.setattr(
        muse_mod, "_discover_wsl_muse", lambda: "/home/user/.local/bin/muse"
    )
    res = muse_mod.muse_resolution()
    assert res["mode"] == "wsl"
    assert res["deprecated"] is True


def test_parse_muse_jsonl_extracts_session_and_text():
    parsed = _parse_muse_jsonl(SAMPLE_JSONL)
    assert parsed["session_id"] == "3d64e574-a415-4c86-961b-d330058b5491"
    assert "Hello from Muse" in parsed["output"]
    assert parsed["terminal"] == "completed"
    assert parsed["errors"] == []


def test_parse_muse_jsonl_falls_back_to_output_deltas():
    """Killed turns never see run.terminal.* — deltas must still surface."""
    raw = "\n".join(
        [
            '{"stream":{"kind":"session","id":"3d64e574-a415-4c86-961b-d330058b5491"},'
            '"payload_type":"session.run.linked","payload":{}}',
            '{"payload_type":"run.output.delta","payload":{"text":"Partial "}}',
            '{"payload_type":"run.output.delta","payload":{"text":"reply text"}}',
        ]
    )
    parsed = _parse_muse_jsonl(raw)
    assert parsed["session_id"] == "3d64e574-a415-4c86-961b-d330058b5491"
    assert parsed["output"] == "Partial reply text"
    assert parsed["terminal"] is None


def test_parse_muse_jsonl_terminal_failed():
    raw = (
        '{"stream":{"kind":"session","id":"aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"},'
        '"payload_type":"run.terminal.failed",'
        '"payload":{"terminal":"failed","reason":"auth required","text":""}}\n'
    )
    parsed = _parse_muse_jsonl(raw)
    assert parsed["session_id"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert "auth required" in parsed["errors"][0]
    assert parsed["terminal"] == "failed"


def test_parse_muse_jsonl_nested_task_usage():
    raw = (
        '{"payload_type":"task.lifecycle.completed","payload":{'
        '"event":{"kind":"completed","usage":{'
        '"input_tokens":1200,"output_tokens":340,"cost_micros":1500}}}}\n'
        '{"payload_type":"run.terminal.completed","payload":{'
        '"terminal":"completed","text":"done"}}\n'
    )
    parsed = _parse_muse_jsonl(raw)
    assert parsed["usage"]["input_tokens"] == 1200
    assert parsed["usage"]["output_tokens"] == 340
    assert parsed["usage"]["cost"] == pytest.approx(0.0015)
    assert "done" in parsed["output"]


def test_parse_muse_jsonl_sums_task_usage_deltas():
    raw = (
        '{"payload_type":"task.lifecycle.completed","payload":{'
        '"event":{"usage":{"input_tokens":100,"output_tokens":10}}}}\n'
        '{"payload_type":"task.lifecycle.completed","payload":{'
        '"event":{"usage":{"input_tokens":50,"output_tokens":5}}}}\n'
        '{"payload_type":"run.terminal.completed","payload":{"terminal":"completed","text":"x"}}\n'
    )
    parsed = _parse_muse_jsonl(raw)
    assert parsed["usage"]["input_tokens"] == 150
    assert parsed["usage"]["output_tokens"] == 15


def test_usage_for_query_report():
    u = usage_for_query_report({"input_tokens": 10, "output_tokens": 5}, "muse-spark-1.2")
    assert u["input_tokens"] == 10
    assert u["output_tokens"] == 5
    assert u["total_tokens"] == 15
    assert u["model"] == "muse-spark-1.2"


def test_muse_session_store_roundtrip(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(store, "_repo_root", lambda: tmp_path)
    cwd = str(tmp_path / "proj")
    Path(cwd).mkdir()
    sid = "db_session_42"
    assert store.load_muse_resume_id(cwd, sid) is None
    store.save_muse_resume_id(cwd, sid, "3d64e574-a415-4c86-961b-d330058b5491")
    assert store.load_muse_resume_id(cwd, sid) == "3d64e574-a415-4c86-961b-d330058b5491"
    store.clear_muse_resume_id(cwd, sid)
    assert store.load_muse_resume_id(cwd, sid) is None


def test_muse_session_store_accepts_int_chat_id(tmp_path: Path, monkeypatch):
    """Auth /api/chat passes numeric session_id; store must not call .strip() on int."""
    monkeypatch.setattr(store, "_repo_root", lambda: tmp_path)
    cwd = str(tmp_path / "proj")
    Path(cwd).mkdir()
    uuid = "3d64e574-a415-4c86-961b-d330058b5491"
    store.save_muse_resume_id(cwd, 130, uuid)
    assert store.load_muse_resume_id(cwd, 130) == uuid
    assert store.load_muse_resume_id(cwd, "130") == uuid
    store.clear_muse_resume_id(cwd, 130)
    assert store.load_muse_resume_id(cwd, 130) is None


def test_muse_session_store_rejects_garbage(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(store, "_repo_root", lambda: tmp_path)
    cwd = str(tmp_path / "proj")
    Path(cwd).mkdir()
    store.save_muse_resume_id(cwd, "s1", "not-a-uuid")
    assert store.load_muse_resume_id(cwd, "s1") is None


@pytest.mark.asyncio
async def test_execute_prompt_builds_wsl_argv(tmp_path: Path):
    cwd = tmp_path / "ws"
    cwd.mkdir()
    captured = {}

    class FakeReader:
        def __init__(self, data: bytes):
            self.lines = iter(data.splitlines(keepends=True))
            self.data = data

        async def readline(self):
            return next(self.lines, b"")

        async def read(self, _size=-1):
            data, self.data = self.data, b""
            return data

    class FakeProc:
        returncode = 0
        stdout = FakeReader((
            '{"payload_type":"run.lifecycle.started","payload":{}}\n'
            '{"payload_type":"task.lifecycle.proposed","payload":{"event":{"task_id":"t1","task_kind":"tool.workspace.read_file"}}}\n'
            + SAMPLE_JSONL
        ).encode("utf-8"))
        stderr = FakeReader(b"")

        async def wait(self):
            return 0

        def kill(self):
            pass

    async def fake_exec(*cmd, **kwargs):
        captured["cmd"] = list(cmd)
        captured["kwargs"] = kwargs
        return FakeProc()

    with patch(
        "scripts.utilities.muse_cli_tool._which_muse_native", return_value=None
    ), patch(
        "scripts.utilities.muse_cli_tool._discover_wsl_muse",
        return_value="/home/user/.local/bin/muse",
    ), patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        tool = MuseCliTool(model="muse-spark-1.2")
        statuses = queue.Queue()
        result = await tool.execute_prompt(
            "Say hi",
            cwd=str(cwd),
            resume="3d64e574-a415-4c86-961b-d330058b5491",
            timeout=30.0,
            provider="echo",
            status_queue=statuses,
        )

    assert result["success"] is True
    assert "Hello from Muse" in result["output"]
    assert result["muse_session_id"] == "3d64e574-a415-4c86-961b-d330058b5491"
    activity = [statuses.get_nowait()[1] for _ in range(statuses.qsize())]
    assert activity == ["Resuming Muse Code…", "Muse Code is thinking…", "tool 1: workspace read file"]
    cmd = captured["cmd"]
    assert cmd[:4] == ["wsl", "-e", "bash", "-lc"]
    inner = cmd[4]
    assert "muse" in inner
    assert "exec" in inner
    assert "--json" in inner
    assert "--session-id" in inner
    assert "--provider" in inner
    assert "echo" in inner
    # echo provider must not get --model
    assert "--model" not in inner


@pytest.mark.asyncio
async def test_execute_prompt_timeout_preserves_session_and_partial(
    tmp_path: Path, monkeypatch
):
    """Idle/timeout must keep Muse session id + streamed text (CH-000478-87)."""
    import asyncio

    cwd = tmp_path / "ws"
    cwd.mkdir()
    monkeypatch.setattr(store, "_repo_root", lambda: tmp_path)
    saved: dict = {}

    class HangReader:
        def __init__(self, lines: list):
            self._lines = list(lines)
            self._i = 0

        async def readline(self):
            if self._i < len(self._lines):
                line = self._lines[self._i]
                self._i += 1
                return line
            await asyncio.sleep(3600)
            return b""

        async def read(self, _size=-1):
            await asyncio.sleep(3600)
            return b""

    class FakeProc:
        returncode = None
        stdout = HangReader(
            [
                (
                    b'{"stream":{"kind":"session","id":'
                    b'"3d64e574-a415-4c86-961b-d330058b5491"},'
                    b'"payload_type":"session.run.linked","payload":{}}\n'
                ),
                (
                    b'{"payload_type":"task.lifecycle.proposed","payload":'
                    b'{"event":{"task_id":"t1","task_kind":"tool.workspace.read_file"}}}\n'
                ),
                b'{"payload_type":"run.output.delta","payload":{"text":"Still baking vines"}}\n',
            ]
        )
        stderr = HangReader([])

        async def wait(self):
            await asyncio.sleep(3600)
            return -1

        def kill(self):
            self.returncode = -9

    async def fake_exec(*_cmd, **_kwargs):
        return FakeProc()

    async def fake_kill(proc):
        proc.kill()
        proc.returncode = -9

    real_deadline = __import__(
        "api.agent_harness.timeouts", fromlist=["ActivityDeadline"]
    ).ActivityDeadline

    def _capped(idle, **kw):
        return real_deadline(idle, absolute_timeout=0.8, **kw)

    monkeypatch.setattr("api.agent_harness.timeouts.ActivityDeadline", _capped)
    monkeypatch.setattr(
        "scripts.utilities.agent_process.kill_process_tree", fake_kill
    )

    with patch(
        "scripts.utilities.muse_cli_tool._which_muse_native",
        return_value=str(tmp_path / "muse.exe"),
    ), patch(
        "scripts.utilities.muse_cli_tool._discover_wsl_muse", return_value=None
    ), patch("asyncio.create_subprocess_exec", side_effect=fake_exec), patch(
        "scripts.utilities.muse_cli_session_store.save_muse_resume_id",
        side_effect=lambda c, s, m: saved.update({"cwd": c, "sid": s, "muse": m}),
    ):
        tool = MuseCliTool(model="muse-spark-1.3")
        result = await tool.execute_prompt(
            "bake vines",
            cwd=str(cwd),
            timeout=0.25,
            chat_session_id="478",
        )

    assert result["success"] is False
    assert result.get("timed_out") is True
    assert result["muse_session_id"] == "3d64e574-a415-4c86-961b-d330058b5491"
    assert "Still baking vines" in (result.get("output") or "")
    assert "interrupted" in (result.get("output") or "").lower()
    assert "workspace read file" in (result.get("output") or "")
    assert saved.get("muse") == "3d64e574-a415-4c86-961b-d330058b5491"
    assert saved.get("sid") == "478"


@pytest.mark.asyncio
async def test_execute_prompt_cancel_preserves_partial(tmp_path: Path, monkeypatch):
    import asyncio
    import threading

    cwd = tmp_path / "ws"
    cwd.mkdir()

    class HangReader:
        def __init__(self, lines: list):
            self._lines = list(lines)
            self._i = 0

        async def readline(self):
            if self._i < len(self._lines):
                line = self._lines[self._i]
                self._i += 1
                return line
            await asyncio.sleep(3600)
            return b""

        async def read(self, _size=-1):
            await asyncio.sleep(3600)
            return b""

    class FakeProc:
        returncode = None
        stdout = HangReader(
            [
                (
                    b'{"stream":{"kind":"session","id":'
                    b'"aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"},'
                    b'"payload_type":"session.run.linked","payload":{}}\n'
                ),
                b'{"payload_type":"run.output.delta","payload":{"text":"Halfway done"}}\n',
            ]
        )
        stderr = HangReader([])

        async def wait(self):
            await asyncio.sleep(3600)
            return -1

        def kill(self):
            self.returncode = -9

    async def fake_exec(*_a, **_k):
        return FakeProc()

    async def fake_kill(proc):
        proc.kill()

    cancel = threading.Event()

    async def _set_soon():
        await asyncio.sleep(0.15)
        cancel.set()

    monkeypatch.setattr(
        "scripts.utilities.agent_process.kill_process_tree", fake_kill
    )

    with patch(
        "scripts.utilities.muse_cli_tool._which_muse_native",
        return_value=str(tmp_path / "muse.exe"),
    ), patch(
        "scripts.utilities.muse_cli_tool._discover_wsl_muse", return_value=None
    ), patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        tool = MuseCliTool()
        setter = asyncio.create_task(_set_soon())
        try:
            result = await tool.execute_prompt(
                "work",
                cwd=str(cwd),
                timeout=30.0,
                chat_session_id="99",
                cancel_event=cancel,
            )
        finally:
            setter.cancel()

    assert result["success"] is False
    assert result.get("cancelled") is True
    assert result["muse_session_id"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert "Halfway done" in (result.get("output") or "")


@pytest.mark.asyncio
async def test_e2e_muse_echo_provider():
    """Live WSL/native muse exec --provider echo (no Meta API credits)."""
    from api.agent_router.supervised.test_isolation import allow_external_runners
    from scripts.utilities.muse_cli_tool import muse_available

    if not muse_available():
        pytest.skip("muse CLI not installed")
    tool = MuseCliTool()
    with allow_external_runners("muse echo provider e2e (no model credits)"):
        result = await tool.execute_prompt(
            "Cuttle e2e ping",
            cwd=str(Path(__file__).resolve().parents[2]),
            timeout=120.0,
            provider="echo",
            max_model_steps=2,
        )
    assert result["success"] is True, result.get("error")
    assert "Cuttle e2e ping" in (result.get("output") or "")
    assert result.get("muse_session_id")


def test_inference_mode_includes_muse():
    from api.inference_mode import is_cloud_cli_slash_command, cloud_cli_slash_blocked_message

    assert is_cloud_cli_slash_command("/muse hello") is True
    assert cloud_cli_slash_blocked_message("local") is not None
    assert "/muse" in (cloud_cli_slash_blocked_message("local") or "")


def test_starred_slash_allows_muse():
    from api.starred_slash import normalize_sticky_prefix, sticky_prefix_from_text

    assert normalize_sticky_prefix("/muse") == "/muse "
    assert sticky_prefix_from_text("/muse fix it") == "/muse "


def test_palette_js_lists_muse():
    # Registry lives in chat_slash.js (Phase 3 Slice 2).
    js = Path(__file__).resolve().parents[1] / "web" / "js" / "chat_slash.js"
    text = js.read_text(encoding="utf-8")
    assert "prefix: '/muse '" in text
    assert "Muse Code" in text
    assert "stickySession: true" in text


def test_registry_knows_muse():
    from api.agent_router.registry import KNOWN_AGENTS, normalize_agent_id, validate_execution_target

    assert "muse" in KNOWN_AGENTS
    assert normalize_agent_id("muse-code") == "muse"
    target, err = validate_execution_target("muse", "")
    assert err is None
    assert target is not None
    assert target.agent == "muse"


def test_chat_endpoint_dispatches_muse_in_auto_mode(monkeypatch, owner_session):
    """Regression: the cloud-mode guard must not swallow allowed CLI commands."""
    from api import web_chat_api as w

    monkeypatch.setattr(
        w,
        "_run_pinned_harness_turn",
        lambda agent_id, prompt, chat_session_id, **kwargs: {
            "success": True,
            "response": f"muse handled: {prompt}",
            "type": "muse_command",
            "agent_model": "muse-spark-1.2-contributor",
        },
    )

    with w.app.test_client() as client:
        owner_session.sign_in(client)
        response = client.post(
            "/api/chat",
            json={
                "message": "/muse inspect routing",
                "session_id": "muse-routing-regression",
                "inference_mode": "auto",
                "stream": False,
            },
        )

    assert response.status_code == 200
    body = response.get_json()
    assert body["type"] == "muse_command"
    assert body["response"] == "muse handled: inspect routing"


def test_muse_reply_metadata_includes_model_badge():
    from api.web_chat_api import _assistant_message_metadata

    meta = _assistant_message_metadata({
        "type": "muse_command",
        "agent_model": "muse-spark-1.2",
        "query_id": "q1",
    })

    chip = meta["slash_command"]["chips"][0]
    assert chip["label"] == "Muse Code - Spark 1.2"
    assert chip["meta"] == "/muse · model muse-spark-1.2"


def test_muse_web_success_returns_agent_model(tmp_path: Path, monkeypatch):
    """Successful /muse replies must carry agent_model so the badge shows the model."""
    import scripts.utilities.muse_cli_tool as muse_mod
    from api import web_chat_api as w

    async def fake_execute_prompt(self, *args, **kwargs):
        return {
            "success": True,
            "output": "Hello from Muse.",
            "muse_session_id": "3d64e574-a415-4c86-961b-d330058b5491",
            "usage": {},
        }

    monkeypatch.setattr(muse_mod, "muse_available", lambda: True)
    monkeypatch.setattr(MuseCliTool, "execute_prompt", fake_execute_prompt)
    monkeypatch.setattr(store, "_repo_root", lambda: tmp_path)

    res = w._run_muse_web_command(
        "say hi",
        "muse-badge-session",
        project_path=str(tmp_path),
        status_queue=None,
    )

    assert res["type"] == "muse_command"
    assert res["agent_model"] == DEFAULT_MUSE_MODEL

    meta = w._assistant_message_metadata(res)
    chip = meta["slash_command"]["chips"][0]
    assert chip["label"] == f"Muse Code - {muse_model_label(DEFAULT_MUSE_MODEL)}"


def test_typing_indicator_no_cursor_chip_for_muse():
    """Regression: generating a /muse reply must not add a stray Cursor badge."""
    js = Path(__file__).resolve().parents[1] / "web" / "js" / "chat_page.js"
    text = js.read_text(encoding="utf-8")
    fn = text.split("function pendingSlashForTypingIndicator", 1)[1].split("\n    }\n", 1)[0]
    assert "if (!isCursor) {" in fn
    assert "enrichSlashCommandWithMuseModel" in fn


def _sse_events(payload: bytes):
    """Decode `data: {...}` frames from a streamed /api/chat response."""
    import json

    out = []
    for line in payload.decode("utf-8", errors="replace").splitlines():
        if line.startswith("data: "):
            try:
                out.append(json.loads(line[6:]))
            except json.JSONDecodeError:
                pass
    return out


def test_muse_stream_forwards_agent_model_to_client(monkeypatch, owner_session):
    """The badge is built from the SSE `response` event — it must carry agent_model.

    Unit-testing the Muse adapter alone passed while the live chat still
    showed a bare "Muse Code" badge, because the SSE frame allowlists fields.
    """
    from api import web_chat_api as w

    monkeypatch.setattr(
        w,
        "_run_pinned_harness_turn",
        lambda agent_id, prompt, chat_session_id, **kwargs: {
            "success": True,
            "response": "done",
            "type": "muse_command",
            "agent_model": "muse-spark-1.1",
            "muse_effort": "high",
        },
    )

    with w.app.test_client() as client:
        owner_session.sign_in(client)
        response = client.post(
            "/api/chat",
            json={
                "message": "/muse inspect routing",
                "session_id": "muse-stream-badge",
                "inference_mode": "auto",
                "stream": True,
            },
        )

    events = _sse_events(response.get_data())
    final = [e for e in events if e.get("type") == "response"]
    assert final, f"no response event in stream: {events}"
    assert final[0]["response_type"] == "muse_command"
    assert final[0]["agent_model"] == "muse-spark-1.1"
    assert final[0]["muse_effort"] == "high"


def test_muse_model_store_roundtrip(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(store, "_repo_root", lambda: tmp_path)

    assert store.load_muse_model("sess-1") is None
    assert store.save_muse_model("sess-1", "muse-spark-1.1") == "muse-spark-1.1"
    assert store.load_muse_model("sess-1") == "muse-spark-1.1"
    # Per chat, not global.
    assert store.load_muse_model("sess-2") is None
    # Resume ids live in the same file and must survive a model write.
    store.save_muse_resume_id(str(tmp_path), "sess-1", "3d64e574-a415-4c86-961b-d330058b5491")
    assert store.save_muse_model("sess-1", "") is None
    assert store.load_muse_model("sess-1") is None
    assert store.load_muse_resume_id(str(tmp_path), "sess-1") == (
        "3d64e574-a415-4c86-961b-d330058b5491"
    )


def test_muse_model_command_sets_and_lists(tmp_path: Path, monkeypatch):
    import scripts.utilities.muse_cli_tool as muse_mod
    from api import web_chat_api as w

    monkeypatch.setattr(muse_mod, "muse_available", lambda: True)
    monkeypatch.setattr(store, "_repo_root", lambda: tmp_path)

    listing = w._run_muse_web_command(
        "model", "muse-model-session", project_path=str(tmp_path)
    )
    assert "muse-spark-1.1" in listing["response"]
    assert "✅ current" in listing["response"]

    setter = w._run_muse_web_command(
        "model muse-spark-1.1", "muse-model-session", project_path=str(tmp_path)
    )
    assert setter["agent_model"] == "muse-spark-1.1"
    assert "muse_model" not in setter
    assert store.load_muse_model("muse-model-session") == "muse-spark-1.1"

    reset = w._run_muse_web_command(
        "/model default", "muse-model-session", project_path=str(tmp_path)
    )
    assert reset["agent_model"] == DEFAULT_MUSE_MODEL
    assert store.load_muse_model("muse-model-session") is None


def test_muse_model_command_does_not_hijack_real_prompts(tmp_path: Path, monkeypatch):
    """`/muse model the login flow` is a task, not a model switch."""
    from api import web_chat_api as w

    monkeypatch.setattr(store, "_repo_root", lambda: tmp_path)
    assert w._handle_muse_model_command("model the login flow", "s", "muse-spark-1.2") is None
    assert w._handle_muse_model_command("models are hard", "s", "muse-spark-1.2") is None
    assert store.load_muse_model("s") is None


def test_muse_session_model_is_used_for_the_run(tmp_path: Path, monkeypatch):
    """A palette pick must reach the CLI and the reply badge."""
    import scripts.utilities.muse_cli_tool as muse_mod
    from api import web_chat_api as w

    seen = {}

    async def fake_execute_prompt(self, *args, **kwargs):
        seen["model"] = self.model
        return {"success": True, "output": "ok", "usage": {}}

    monkeypatch.setattr(muse_mod, "muse_available", lambda: True)
    monkeypatch.setattr(MuseCliTool, "execute_prompt", fake_execute_prompt)
    monkeypatch.setattr(store, "_repo_root", lambda: tmp_path)
    store.save_muse_model("muse-pinned", "muse-spark-1.2-contributor")

    res = w._run_muse_web_command("say hi", "muse-pinned", project_path=str(tmp_path))

    assert seen["model"] == "muse-spark-1.2-contributor"
    assert res["agent_model"] == "muse-spark-1.2-contributor"
    chip = w._assistant_message_metadata(res)["slash_command"]["chips"][0]
    assert chip["label"] == "Muse Code - Spark 1.2 (Contributor)"


def test_muse_clear_session_reply(tmp_path: Path, monkeypatch):
    """Regression: this branch read `muse` before it was assigned (NameError)."""
    import scripts.utilities.muse_cli_tool as muse_mod
    from api import web_chat_api as w

    monkeypatch.setattr(muse_mod, "muse_available", lambda: True)
    monkeypatch.setattr(store, "_repo_root", lambda: tmp_path)
    store.save_muse_resume_id(
        str(tmp_path), "muse-clear", "3d64e574-a415-4c86-961b-d330058b5491"
    )

    res = w._run_muse_web_command("new", "muse-clear", project_path=str(tmp_path))

    assert res["type"] == "muse_command"
    assert "Session cleared" in res["response"]
    # A control reply claims no model — agent id only, never a Cuttle default.
    assert res["agent_model"] == "muse"
    assert store.load_muse_resume_id(str(tmp_path), "muse-clear") is None


def test_muse_models_endpoints(tmp_path: Path, monkeypatch, owner_session):
    from api import web_chat_api as w

    monkeypatch.setattr(store, "_repo_root", lambda: tmp_path)

    with w.app.test_client() as client:
        owner_session.sign_in(client)
        listed = client.get("/api/muse/models?session=api-sess").get_json()
        assert listed["success"] is True
        assert listed["preferredModel"] == DEFAULT_MUSE_MODEL
        assert {m["id"] for m in listed["models"]} >= {
            "muse-spark-1.3",
            "muse-spark-1.3-contributor",
            "muse-spark-1.2",
            "muse-spark-1.1",
            "muse-spark-1.2-contributor",
        }

        saved = client.post(
            "/api/muse/model", json={"session": "api-sess", "model": "muse-spark-1.1"}
        ).get_json()
        assert saved["success"] is True
        assert saved["preferredModel"] == "muse-spark-1.1"

        again = client.get("/api/muse/models?session=api-sess").get_json()
        assert again["preferredModel"] == "muse-spark-1.1"
        current = [m for m in again["models"] if m["current"]]
        assert [m["id"] for m in current] == ["muse-spark-1.1"]

        missing = client.post("/api/muse/model", json={"model": "muse-spark-1.1"})
        assert missing.status_code == 400


def test_muse_model_palette_wired_into_chat_page():
    """The `/` palette must offer Muse model picks and apply them without a chip."""
    js = Path(__file__).resolve().parents[1] / "web" / "js" / "chat_page.js"
    text = js.read_text(encoding="utf-8")
    assert "function buildMuseModelPaletteItems" in text
    assert ".concat(museModels)" in text
    assert "cmd.category === 'muse-model'" in text
    assert "persistMuseModelSelection" in text
    assert "'/api/muse/model'" in text
    # Staged `/model refresh` under Muse sticky (no instant museRefresh).
    assert "prefix: '/model refresh'" in text
    assert "category: 'muse-cmd'" in text
    assert "museRefresh: true" not in text
    assert "params.set('refresh', '1')" in text
    # No hardcoded Cuttle model default anywhere in the palette path:
    # the chip resolves session pin → starred → live CLI default
    # from /api/muse/models (CH-000419).
    assert "muse-spark-1.3" not in text
    assert "Spark 1.3 (Contributor)" in text


def test_muse_model_slash_refresh(monkeypatch):
    from api.agent_harness.agents.muse.adapter import _handle_muse_model_slash

    monkeypatch.setattr(
        "scripts.utilities.muse_cli_tool.refresh_muse_catalog",
        lambda: {
            "count": 5,
            "source": "meta_api_refresh",
            "error": None,
            "models": [{"id": "muse-spark-1.3", "label": "Muse Spark 1.3"}],
        },
    )
    result = _handle_muse_model_slash("model refresh", "99", "muse-spark-1.3")
    assert result is not None
    assert result.success is True
    assert "5" in result.output
    assert "palette" in result.output.lower()


def test_list_muse_catalog_models_static_fallback(monkeypatch):
    from scripts.utilities import muse_cli_tool as muse_mod

    muse_mod.clear_muse_catalog_cache()
    monkeypatch.setattr(muse_mod, "_fetch_meta_muse_spark_models", lambda: ([], "no key"))
    out = muse_mod.list_muse_catalog_models(refresh=True)
    assert out["source"] == "static_fallback"
    assert out["count"] >= 5
    assert {m["id"] for m in out["models"]} >= {"muse-spark-1.3", "muse-spark-1.1"}


def test_list_muse_catalog_models_meta_api(monkeypatch):
    from scripts.utilities import muse_cli_tool as muse_mod

    muse_mod.clear_muse_catalog_cache()
    monkeypatch.setattr(
        muse_mod,
        "_fetch_meta_muse_spark_models",
        lambda: (
            [
                {"id": "muse-spark-9.9", "label": "Muse Spark 9.9", "description": "future"},
                {
                    "id": "muse-spark-9.9-contributor",
                    "label": "Muse Spark 9.9 (Contributor)",
                    "description": "future contrib",
                },
            ],
            None,
        ),
    )
    out = muse_mod.list_muse_catalog_models(refresh=True)
    assert out["source"] == "meta_api_refresh"
    assert out["count"] == 2
    assert out["models"][0]["id"] == "muse-spark-9.9"


def test_resolve_muse_default_model_reads_cli_settings(monkeypatch):
    """Unpinned /muse must follow Muse CLI settings.json, not a hardcoded 1.2."""
    import scripts.utilities.muse_cli_tool as muse_mod

    clear_muse_default_model_cache()
    monkeypatch.setattr(
        muse_mod,
        "read_muse_cli_settings_model",
        lambda: "muse-spark-1.3-contributor",
    )
    assert resolve_muse_default_model() == "muse-spark-1.3-contributor"
    assert muse_model_label("muse-spark-1.3-contributor") == "Spark 1.3 (Contributor)"
    assert MuseCliTool().model == "muse-spark-1.3-contributor"

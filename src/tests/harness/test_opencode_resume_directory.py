"""Native resume directory must match the OpenCode event subscription."""
import asyncio
import json
import sys
from pathlib import Path

import pytest

from api.agent_harness.agents.opencode import adapter as oc
from tests.harness.test_agent_harness_timeouts import _Proc


class ListingProcess:
    returncode = 0

    def __init__(self, rows):
        self.rows = rows

    async def communicate(self):
        return json.dumps(self.rows).encode(), b""


def patch_cli(monkeypatch, rows):
    calls = []

    async def spawn(*argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[1:3] == ("session", "list"):
            return ListingProcess(rows)
        return _Proc([b'{"type":"step_start","sessionID":"saved"}\n', b'{"type":"text","sessionID":"saved","part":{"text":"Done"}}\n'])

    monkeypatch.setattr(oc, "opencode_executable", lambda: "opencode")
    monkeypatch.setattr(oc.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(oc, "attach_to_chat_run", lambda *args: None)
    async def no_repository(cwd, chat_session_id):
        return None
    monkeypatch.setattr(oc, "_git_common_directory", no_repository)
    return calls


def test_resume_subscribes_in_saved_subdirectory(monkeypatch, tmp_path):
    native = tmp_path / "previous-working-dir"
    native.mkdir()
    calls = patch_cli(monkeypatch, [
        {"id": "unrelated", "directory": str(tmp_path)},
        {"id": "saved", "directory": str(native)},
    ])
    result = asyncio.run(oc.Adapter().execute(
        "continue", cwd=str(tmp_path), resume="saved", model=None))
    assert result.success and result.output == "Done"
    argv, kwargs = calls[-1]
    assert argv[argv.index("--session") + 1] == "saved"
    assert argv[argv.index("--dir") + 1] == str(native)
    assert kwargs["cwd"] == str(native)
    assert "PWD" not in kwargs["env"]


def test_resume_copies_into_selected_linked_checkout(monkeypatch, tmp_path):
    selected = tmp_path / "worktree"
    original = tmp_path / "primary" / "electron"
    selected.mkdir()
    original.mkdir(parents=True)
    calls = patch_cli(monkeypatch, [{"id": "saved", "directory": str(original)}])

    async def repository(cwd, chat_session_id):
        return tmp_path / "primary" / ".git"

    monkeypatch.setattr(oc, "_git_common_directory", repository)
    async def copy_session(exe, cwd, resume, chat_session_id):
        assert cwd == str(selected) and resume == "saved"
        return "recovered"
    monkeypatch.setattr(oc, "_copy_session_to_directory", copy_session)
    result = asyncio.run(oc.Adapter().execute(
        "continue", cwd=str(selected), resume="saved", model=None))
    assert result.success
    argv, kwargs = calls[-1]
    assert "--fork" not in argv
    assert argv[argv.index("--session") + 1] == "recovered"
    assert argv[argv.index("--dir") + 1] == str(selected)
    assert kwargs["cwd"] == str(selected)


def test_nested_foreign_repository_cannot_resume(monkeypatch, tmp_path):
    native = tmp_path / "other-repo"
    native.mkdir()
    calls = patch_cli(monkeypatch, [{"id": "saved", "directory": str(native)}])

    async def repository(cwd, chat_session_id):
        return Path(cwd) / ".git"

    monkeypatch.setattr(oc, "_git_common_directory", repository)
    result = asyncio.run(oc.Adapter().execute(
        "continue", cwd=str(tmp_path), resume="saved", model=None))
    assert not result.success and "different repository" in result.error
    assert len(calls) == 1


def test_new_session_pins_selected_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("PWD", "/stale/parent/directory")
    calls = patch_cli(monkeypatch, [])
    result = asyncio.run(oc.Adapter().execute(
        "task", cwd=str(tmp_path), resume=None, model=None))
    assert result.success
    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert argv[argv.index("--dir") + 1] == str(tmp_path)
    assert "PWD" not in kwargs["env"]


@pytest.mark.parametrize("case", ["missing", "outside", "deleted", "malformed", "symlink"])
def test_invalid_resume_directory_never_starts_turn(monkeypatch, tmp_path, case):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "other-project"
    outside.mkdir()
    directory = outside
    if case == "deleted":
        directory = project / "deleted"
    if case == "symlink":
        directory = project / "linked"
        directory.symlink_to(outside, target_is_directory=True)
    rows = [{"id": "saved", "directory": str(directory)}]
    if case == "missing":
        rows = []
    if case == "malformed":
        rows = {"id": "saved", "directory": str(project)}
    calls = patch_cli(monkeypatch, rows)
    result = asyncio.run(oc.Adapter().execute(
        "continue", cwd=str(project), resume="saved", model=None))
    assert not result.success and "No turn was started" in result.error
    assert len(calls) == 1


def test_lookup_timeout_reaps_only_owned_process(monkeypatch, tmp_path):
    proc = ListingProcess([])
    proc.returncode = None
    killed = []

    async def spawn(*args, **kwargs):
        return proc

    async def timeout(awaitable, **kwargs):
        awaitable.close()
        raise asyncio.TimeoutError

    async def kill(owned):
        killed.append(owned)

    monkeypatch.setattr(oc.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(oc.asyncio, "wait_for", timeout)
    monkeypatch.setattr(oc, "attach_to_chat_run", lambda *args: None)
    monkeypatch.setattr(oc, "kill_process_tree", kill)
    with pytest.raises(ValueError, match="lookup timed out"):
        asyncio.run(oc._resume_directory("opencode", str(tmp_path), "saved", None))
    assert killed == [proc]


@pytest.mark.parametrize("verified_directory", ["selected", "wrong"])
def test_copy_preserves_transcript_and_verifies_before_saving(monkeypatch, tmp_path, verified_directory):
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path / "home"))
    source = {
        "info": {"id": "ses_original", "directory": "/old", "share": {"url": "old-share"}},
        "messages": [
            {"info": {"id": "msg_user", "sessionID": "ses_original", "role": "user"},
             "parts": [{"id": "prt_user", "messageID": "msg_user", "sessionID": "ses_original", "type": "text", "text": "Keep this work"}]},
            {"info": {"id": "msg_reply", "sessionID": "ses_original", "parentID": "msg_user", "role": "assistant"},
             "parts": []},
        ],
    }
    imported = []
    saved = []
    paths = []

    async def command(exe, cwd, sid, *args):
        if args[0] == "import":
            paths.append(Path(args[1]))
            imported.append(json.loads(paths[-1].read_text()))
            return b"Imported session"
        if args[1] == "ses_original":
            return json.dumps(source).encode()
        verified = dict(imported[-1])
        verified["info"] = dict(verified["info"], directory=str(tmp_path) if verified_directory == "selected" else "/wrong")
        return json.dumps(verified).encode()

    monkeypatch.setattr(oc, "_session_command", command)
    monkeypatch.setattr(oc, "save_opencode_resume_id", lambda *args: saved.append(args))
    if verified_directory == "wrong":
        with pytest.raises(ValueError, match="could not be verified"):
            asyncio.run(oc._copy_session_to_directory("opencode", str(tmp_path), "ses_original", 123))
        assert not saved
    else:
        new_id = asyncio.run(oc._copy_session_to_directory("opencode", str(tmp_path), "ses_original", 123))
        assert saved == [(str(tmp_path), 123, new_id)]
        copied = imported[0]
        assert new_id != "ses_original" and "share" not in copied["info"]
        assert copied["messages"][0]["info"]["sessionID"] == new_id
        assert copied["messages"][1]["info"]["parentID"] == copied["messages"][0]["info"]["id"]
        assert copied["messages"][0]["parts"][0]["messageID"] == copied["messages"][0]["info"]["id"]
        assert copied["messages"][0]["parts"][0]["text"] == "Keep this work"
        assert source["info"]["id"] == "ses_original"
    assert paths and not paths[0].exists()


def test_large_export_stdout_is_complete_even_when_cli_exits_immediately(monkeypatch, tmp_path):
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(oc, "attach_to_chat_run", lambda *args: None)
    output = asyncio.run(oc._session_command(
        sys.executable, str(tmp_path), None, "-c",
        "import os; os.write(1, b'x' * 200000); os._exit(0)"))
    assert output == b"x" * 200000

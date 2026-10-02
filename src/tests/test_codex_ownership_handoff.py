"""Cancellation and cleanup regressions from the Codex architecture handoff."""
import asyncio
import os
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.agent_harness import codex_thread_ownership as ownership
from scripts.utilities import agent_process, codex_app_server_turn, codex_cli_tool
from tests.test_codex_thread_ownership import (
    _FAKE_EXEC_SERVER, _FAKE_TURN_SERVER, _fake_bin, _noop_store,
)


def test_stop_wins_over_available_thread():
    stopped = threading.Event()
    stopped.set()
    tid = "handoff-pre-cancel"
    token = asyncio.run(ownership.await_acquire(
        tid, "turn", timeout=1, cancel_event=stopped,
    ))
    try:
        assert token is None
    finally:
        ownership.release(tid, token)


@pytest.mark.parametrize("transport", ["turn", "exec"])
@pytest.mark.parametrize("resume", [None, "handoff-already-stopped"])
def test_pre_cancel_spawns_nothing(tmp_path, monkeypatch, transport, resume):
    stopped = threading.Event()
    stopped.set()
    monkeypatch.setattr(codex_app_server_turn, "codex_executable", lambda: "/fake/codex")
    monkeypatch.setattr(codex_cli_tool, "codex_executable", lambda: "/fake/codex")
    spawn = AsyncMock(side_effect=AssertionError("stopped turn spawned"))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    if transport == "turn":
        run = codex_app_server_turn.run_codex_turn_app_server(
            "go", cwd=str(tmp_path), resume=resume, model=None,
            reasoning_effort=None, cancel_event=stopped,
        )
    else:
        run = codex_cli_tool.CodexCliTool().execute_prompt(
            "go", cwd=str(tmp_path), resume=resume, cancel_event=stopped,
        )
    assert asyncio.run(run)["cancelled"] is True
    spawn.assert_not_called()
    assert ownership.owner_of(resume) is None


@pytest.mark.skipif(os.name == "nt", reason="fake executables use shebangs")
@pytest.mark.parametrize("transport", ["turn", "exec"])
def test_task_cancel_reaps_child_and_releases_thread(tmp_path, monkeypatch, transport):
    tid = "handoff-task-cancel-" + transport
    monkeypatch.setenv("S2_TID", tid)
    monkeypatch.setenv("S2_MODE", "hold")
    monkeypatch.setenv("S2_EXEC_MODE", "hold")
    module = codex_app_server_turn if transport == "turn" else codex_cli_tool
    executable = _fake_bin(tmp_path, "fake-codex", (
        _FAKE_TURN_SERVER if transport == "turn" else _FAKE_EXEC_SERVER
    ))
    monkeypatch.setattr(module, "codex_executable", lambda: executable)
    _noop_store(monkeypatch)
    children = []
    monkeypatch.setattr(module, "attach_to_chat_run", lambda sid, proc: children.append(proc))

    async def exercise():
        if transport == "turn":
            run = module.run_codex_turn_app_server(
                "go", cwd=str(tmp_path), resume=tid, model=None,
                reasoning_effort=None, timeout=15,
            )
        else:
            run = module.CodexCliTool().execute_prompt(
                "go", cwd=str(tmp_path), resume=tid, timeout=15,
            )
        task = asyncio.create_task(run)
        try:
            for _ in range(200):
                if children:
                    break
                await asyncio.sleep(0.01)
            assert children
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert children[0].returncode is not None
            assert ownership.owner_of(tid) is None
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            for child in children:
                if child.returncode is None:
                    child.kill()
                    await child.wait()
    asyncio.run(exercise())


def test_registry_cleanup_does_not_block_event_loop(monkeypatch):
    from api import chat_run_registry
    ready, ticker = threading.Event(), threading.Event()
    observed = []
    proc = SimpleNamespace(returncode=None)

    def sweep(child):
        ready.set()
        observed.append(ticker.wait(timeout=2))
        child.returncode = 0
        return True

    monkeypatch.setattr(chat_run_registry, "kill_process_tree", sweep)
    async def exercise():
        async def tick():
            while not ready.is_set():
                await asyncio.sleep(0.01)
            ticker.set()
        ticking = asyncio.create_task(tick())
        assert await agent_process.kill_process_tree(proc) is True
        await ticking
    asyncio.run(exercise())
    assert observed == [True]


def test_dead_parent_still_sweeps_descendants(monkeypatch):
    from api import chat_run_registry
    proc = SimpleNamespace(returncode=0)
    calls = []
    monkeypatch.setattr(chat_run_registry, "kill_process_tree", lambda p: calls.append(p))
    assert asyncio.run(agent_process.kill_process_tree(proc)) is True
    assert calls == [proc]

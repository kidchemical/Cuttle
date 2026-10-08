"""Offline Muse completion races, including item/RPC ordering and repeated input."""

import asyncio
import json
import os
import queue
import stat
import sys
import threading
import time

import pytest

from api.agent_harness import steer
from scripts.utilities import muse_serve_turn as runner


_SERVER = r'''
import json, sys
mode = MODE
count = 0
def out(msg):
    print(json.dumps(msg), flush=True)
def echo(text, *, turn="t1", session="s1", steered=True):
    out({"method": "item/completed", "params": {"sessionId": session, "item": {
        "kind": "userMessage", "itemId": "u1", "text": text, "steered": steered, "turnId": turn}}})
def complete():
    out({"method": "item/completed", "params": {"sessionId": "s1", "item": {
        "kind": "agentMessage", "itemId": "a1", "text": "Original reply: potato", "turnId": "t1"}}})
    out({"method": "turn/completed", "params": {"sessionId": "s1", "turnId": "t1", "terminal": "completed"}})
for line in sys.stdin:
    msg = json.loads(line)
    method, mid = msg.get("method"), msg.get("id")
    if method == "initialize":
        out({"id": mid, "result": {}})
    elif method == "session/start":
        out({"id": mid, "result": {"session": {"sessionId": "s1"}}})
    elif method == "turn/start":
        out({"id": mid, "result": {"turnId": "t1"}})
    elif method == "turn/steer":
        count += 1
        text = msg["params"]["input"][0]["text"]
        ack = {"id": mid, "result": {"status": "accepted", "turnId": "t1"}}
        if mode == "eof_before_ack":
            break
        if mode in ("echo_before_ack", "completion_before_ack_with_echo"):
            echo(text)
        if mode.startswith("completion_before_ack"):
            complete()
            out(ack)
            continue
        out(ack)
        if mode == "ack_before_echo":
            echo(text)
        elif mode == "wrong_turn":
            echo(text, turn="stale")
        elif mode == "wrong_session":
            echo(text, session="other")
        elif mode == "not_steered":
            echo(text, steered=False)
        elif mode == "duplicate_echo" and count == 1:
            echo(text)
            echo(text)
            continue
        complete()
        if mode == "echo_after_completion":
            echo(text)
'''


@pytest.fixture(autouse=True)
def clean_registry():
    with steer._lock:
        steer._active.clear()
    yield
    with steer._lock:
        steer._active.clear()


def run_case(tmp_path, monkeypatch, mode):
    binary = tmp_path / "fake-muse"
    binary.write_text(f"#!{sys.executable}\n" + _SERVER.replace("MODE", repr(mode)), encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(runner, "_which_muse_native", lambda: str(binary))
    statuses = queue.Queue()
    replies = []
    requests = 2 if mode == "duplicate_echo" else 1

    def send():
        deadline = time.monotonic() + 5
        while not steer.active_agent(7331) and time.monotonic() < deadline:
            time.sleep(0.01)
        for _ in range(requests):
            replies.append(steer.steer(7331, "/muse potato", timeout=2))

    thread = threading.Thread(target=send, daemon=True)
    thread.start()
    result = asyncio.run(runner.run_muse_turn_serve(
        "hello", cwd=str(tmp_path), resume=None, model=None, reasoning_effort=None,
        chat_session_id="7331", status_queue=statuses, timeout=8,
    ))
    thread.join(4)
    assert not thread.is_alive()
    assert len(replies) == requests
    assert steer.active_agent(7331) is None
    return result, replies, list(statuses.queue)


@pytest.mark.skipif(os.name == "nt", reason="fake executable uses a shebang")
@pytest.mark.parametrize("mode", ["unread", "wrong_turn", "wrong_session", "not_steered", "duplicate_echo", "echo_after_completion"])
def test_accepted_but_unread_followup_is_visible(tmp_path, monkeypatch, mode):
    result, replies, statuses = run_case(tmp_path, monkeypatch, mode)
    assert all(reply["steered"] for reply in replies)
    assert result["success"] is True
    assert result["steered"] == (1 if mode == "duplicate_echo" else 0)
    assert result["undelivered_steers"] == ["potato"]
    assert "Muse Code ended this turn without confirming it read your follow-up" in result["output"]
    assert "- potato" in result["output"]
    assert "Send it again to continue." in result["output"]
    assert any("steer queued: potato" in str(status) for status in statuses)


@pytest.mark.skipif(os.name == "nt", reason="fake executable uses a shebang")
@pytest.mark.parametrize("mode", ["ack_before_echo", "echo_before_ack", "completion_before_ack_with_echo"])
def test_echoed_followup_survives_ack_ordering(tmp_path, monkeypatch, mode):
    result, replies, _ = run_case(tmp_path, monkeypatch, mode)
    assert replies[0]["steered"] is True
    assert result["steered"] == 1
    assert result["undelivered_steers"] == []
    assert result["output"] == "Original reply: potato"


@pytest.mark.skipif(os.name == "nt", reason="fake executable uses a shebang")
@pytest.mark.parametrize("mode", ["completion_before_ack", "eof_before_ack"])
def test_unacknowledged_followup_returns_to_client_queue(tmp_path, monkeypatch, mode):
    result, replies, _ = run_case(tmp_path, monkeypatch, mode)
    assert replies[0]["steered"] is False
    assert replies[0]["reason"] == "rejected"
    assert result["steered"] == 0
    assert result["undelivered_steers"] == []


def test_rejected_send_cannot_consume_an_identical_retry_receipt():
    from api.agent_harness.steer_delivery import SteerDelivery

    delivery = SteerDelivery()
    failed = delivery.begin("potato")
    assert delivery.acknowledge(failed, succeeded=False) is False
    retry = delivery.begin("potato")
    assert delivery.acknowledge(retry, succeeded=True) is True
    assert delivery.receive("potato", "u2") is True
    assert delivery.received_count == 1
    assert delivery.undelivered == []


def test_history_echo_cannot_confirm_a_future_identical_send():
    from api.agent_harness.steer_delivery import SteerDelivery

    delivery = SteerDelivery()
    assert delivery.receive("potato", "history-item") is False
    receipt = delivery.begin("potato")
    delivery.acknowledge(receipt, succeeded=True)
    assert delivery.receive("potato", "history-item") is False
    assert delivery.received_count == 0
    assert delivery.undelivered == ["potato"]


def test_muse_adapter_preserves_unconfirmed_delivery_metadata(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock
    from api.agent_harness.agents.muse.adapter import build_adapter
    from scripts.utilities import muse_cli_session_store as store, muse_cli_tool as cli

    monkeypatch.setattr(steer, "steer_enabled", lambda _agent: True)
    monkeypatch.setattr(cli, "resolve_muse_default_model", lambda: "fake-model")
    monkeypatch.setattr(store, "read_muse_session_usage", lambda *a, **k: {})
    monkeypatch.setattr(store, "read_muse_msp_context", lambda *a, **k: None)
    raw = {"success": True, "output": "Original reply\n\nUnconfirmed: potato", "steered": 0,
           "undelivered_steers": ["potato"], "usage": {"input_tokens": 10, "output_tokens": 2}}
    monkeypatch.setattr(runner, "run_muse_turn_serve", AsyncMock(return_value=raw))
    monkeypatch.setattr(cli.MuseCliTool, "execute_prompt", AsyncMock(side_effect=AssertionError("unexpected exec fallback")))
    result = asyncio.run(build_adapter().execute(
        "hello", cwd=str(tmp_path), resume=None, model="fake-model", chat_session_id="7331",
    ))
    assert result.success is True
    assert result.output == raw["output"]
    assert result.meta["undelivered_steers"] == ["potato"]


def test_send_scheduled_before_completion_is_rejected_on_loop(tmp_path, monkeypatch):
    """The Flask callback was captured while live; its queued send runs after completion."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    proc = SimpleNamespace(returncode=0, stdin=Mock())
    proc.stdin.is_closing.return_value = False
    monkeypatch.setattr(runner, "_which_muse_native", lambda: "fake-muse")
    monkeypatch.setattr(runner.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
    future = None

    async def run(_proc, **kwargs):
        nonlocal future
        emit = lambda msg: kwargs["on_stdout_line"](json.dumps(msg).encode())
        emit({"id": 1, "result": {}})
        emit({"id": 2, "result": {"session": {"sessionId": "s1"}}})
        emit({"id": 3, "result": {"turnId": "t1"}})
        with steer._lock:
            send = steer._active["7331"].send
        future = send("late potato")
        emit({"method": "turn/completed", "params": {"sessionId": "s1", "turnId": "t1", "terminal": "completed"}})
        await asyncio.sleep(0)
        return SimpleNamespace(stderr=b"", cancelled=False, timed_out=False)

    monkeypatch.setattr(runner, "run_interruptible", run)
    result = asyncio.run(runner.run_muse_turn_serve(
        "hello", cwd=str(tmp_path), resume=None, model=None, reasoning_effort=None, chat_session_id="7331",
    ))
    assert future is not None and future.done()
    assert future.result() == (False, "turn is no longer active")
    written = [json.loads(call.args[0]) for call in proc.stdin.write.call_args_list]
    assert not any(msg.get("method") == "turn/steer" for msg in written)
    assert result["steered"] == 0

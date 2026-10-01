"""A stream is a subscriber, not the lifetime of the running turn.

Scripted workers and Events reproduce the client's intentional 10/20-second
SSE detach without a vendor process or timing-dependent network test.
"""
import threading

import pytest

from api import chat_delivery as delivery, chat_live_status as live
from api.chat_coordinator import (
    AgentSelection, PreparedAgentTurn, StreamTurnIO, submit_agent_stream_turn,
)


@pytest.mark.parametrize("lane", ["harness", "router_family", "pipeline"])
def test_detached_stream_keeps_worker_busy_and_publishes_until_done(lane):
    sid = "detach-status-" + lane
    more = threading.Event()
    published = threading.Event()
    finish = threading.Event()
    saved = threading.Event()
    stopped = threading.Event()

    def run(status_queue=None, **kwargs):
        try:
            status_queue.put(("status", "first tool"))
            assert more.wait(3)
            status_queue.put(("status", "second tool"))
            published.set()
            assert finish.wait(3)
            return {"success": True, "response": "reply"}
        finally:
            stopped.set()

    io = StreamTurnIO(
        run_harness=lambda *a, **k: run(**k), run_router=run,
        run_pipeline=run, persist_user=lambda: None,
        make_saver=lambda: lambda result: saved.set(),
        notify_mobile=None, format_shortcut=lambda *a: {},
    )
    events = submit_agent_stream_turn(
        PreparedAgentTurn(message="test", session_id=sid), io=io,
        delivery=delivery, is_router_family=lambda m: False,
        selection=AgentSelection(kind=lane, agent_id="cursor", prompt="test"),
    )
    try:
        assert next(events)[0] == "connecting"
        assert next(events) == ("status", "first tool")
        events.close()  # reader.cancel() / navigation / lost connection
        assert delivery.is_busy(sid), "disconnect freed a running worker's slot"
        more.set()
        assert published.wait(3)
        assert live.get_live_status(sid)["status"] == "second tool"
        finish.set()
        assert saved.wait(3)
        # Wait for the worker's final park/release rather than just its saver.
        for _ in range(300):
            if delivery.peek_result(sid):
                break
            threading.Event().wait(.01)
        assert delivery.peek_result(sid)["response"] == "reply"
        assert not delivery.is_busy(sid)
        assert not live.get_live_status(sid)["active"]
    finally:
        more.set()
        finish.set()
        stopped.wait(3)
        events.close()
        delivery.end(sid)
        delivery.clear_result(sid)
        live.clear_live_status(sid)


def test_abandoned_connecting_marker_releases_unstarted_worker():
    from api.chat_turn_workflow import run_agent_stream_turn
    sid = "detach-before-worker"
    events = run_agent_stream_turn(
        sid, delivery=delivery, persist_user=lambda: None,
        run=lambda q: pytest.fail("worker must not start"),
        make_saver=lambda: None, notify_mobile=None,
    )
    try:
        assert next(events)[0] == "connecting"
        events.close()
        assert not delivery.is_busy(sid)
        assert not live.get_live_status(sid)["active"]
    finally:
        events.close()
        delivery.end(sid)
        live.clear_live_status(sid)


def test_pipeline_transport_close_before_worker_start_releases_claim():
    from api import web_chat_api as wca

    sid = "detach-pipeline-head"
    chunks = wca._generate_chat_stream(
        lambda **kwargs: pytest.fail("worker must not start"), sid,
    )
    try:
        assert '"type": "session"' in next(chunks)
        assert delivery.is_busy(sid)
        chunks.close()
        assert not delivery.is_busy(sid)
        assert not live.get_live_status(sid)["active"]
    finally:
        chunks.close()
        delivery.end(sid)
        live.clear_live_status(sid)


def test_stopped_worker_legacy_emit_cannot_reach_replacement_turn():
    import queue
    from api import chat_status
    from api.chat_turn_workflow import run_agent_stream_turn

    sid = "detach-legacy-resend"
    resume = threading.Event()
    producer_queue = []
    newer_queue = queue.Queue()

    def run(status_queue):
        producer_queue.append(status_queue)
        status_queue.put(("status", "old first tool"))
        assert resume.wait(3)
        # Legacy pipeline status writer looks up by session, rather than
        # accepting the producer's queue argument directly.
        chat_status.emit_status(
            sid, "old late tool", is_cancelled=delivery.is_turn_cancelled,
            publish_live=lambda text: live.set_live_status(sid, text),
        )
        return {"success": True, "response": "old late reply"}

    events = run_agent_stream_turn(
        sid, delivery=delivery, persist_user=lambda: None, run=run,
        make_saver=lambda: lambda result: pytest.fail("stale result saved"),
        notify_mobile=None,
    )
    try:
        assert next(events)[0] == "connecting"
        assert next(events)[0] == "status"
        events.close()
        delivery.cancel_current_turn(sid)
        assert delivery.try_begin(sid)
        newer_turn = delivery.current_turn(sid)
        live.set_live_status(sid, "new work", turn=newer_turn)
        chat_status.register_status_queue(sid, newer_queue)
        resume.set()
        while producer_queue[0].get(timeout=3)[0] != "done":
            pass
        assert live.get_live_status(sid)["status"] == "new work"
        assert newer_queue.empty(), "old producer wrote into the replacement queue"
        assert delivery.current_turn(sid) == newer_turn
        assert delivery.peek_result(sid) is None
    finally:
        resume.set()
        events.close()
        chat_status.unregister_status_queue(sid)
        delivery.end(sid)
        live.clear_live_status(sid)

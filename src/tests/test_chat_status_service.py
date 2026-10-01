"""Status-queue registry + emit behavior (P4-2 baseline).

Pins the current ``api.web_chat_api`` status-queue contract BEFORE the
registry moves to the owned ``api.chat_status`` service: register /
unregister / lookup, emit fanout to queue + live-status, FIFO order,
full-queue drop, cancel suppression (neither queued nor published),
unregister-then-emit, and phase-line formatting. Post-extraction the
same assertions must hold through the service and the compatibility
wrappers; registry identity across both modules is asserted separately.
"""
from __future__ import annotations

import queue as queue_module
import threading
from pathlib import Path

import pytest

import api.web_chat_api as wca
from api import chat_delivery as delivery
from api.chat_status_phases import emit_pipeline_status


def setup_function(_fn=None):
    delivery._cancel_sticky.clear()
    with wca._chat_live_status_lock:
        wca._chat_live_status.clear()
    wca._chat_status_queues.clear()


def _cancel(sid):
    from api.chat_delivery import _session_keys

    for k in _session_keys(sid):
        delivery._cancel_sticky[k] = 1


def test_register_lookup_unregister_roundtrip():
    q = queue_module.Queue()
    assert wca._chat_status_queues.get("reg-s") is None
    wca._chat_status_queues["reg-s"] = q
    assert wca._chat_status_queues.get("reg-s") is q
    wca._chat_status_queues.pop("reg-s", None)
    assert wca._chat_status_queues.get("reg-s") is None


def test_emit_fans_out_to_queue_and_live_status():
    q = queue_module.Queue()
    wca._chat_status_queues["fan-s"] = q
    wca.emit_chat_status("fan-s", "Working…")
    assert q.get_nowait() == ("status", "Working…")
    assert wca.get_chat_live_status("fan-s")["status"] == "Working…"


def test_emit_preserves_fifo_order():
    q = queue_module.Queue()
    wca._chat_status_queues["fifo-s"] = q
    for text in ("one", "two", "three"):
        wca.emit_chat_status("fifo-s", text)
    assert [q.get_nowait() for _ in range(3)] == [
        ("status", "one"), ("status", "two"), ("status", "three"),
    ]


def test_emit_without_queue_publishes_live_only():
    wca.emit_chat_status("noq-s", "Hello")
    assert wca.get_chat_live_status("noq-s")["status"] == "Hello"


def test_emit_drops_on_full_queue_without_raising():
    q = queue_module.Queue(maxsize=1)
    q.put_nowait(("status", "filler"))
    wca._chat_status_queues["full-s"] = q
    wca.emit_chat_status("full-s", "dropped")  # must not raise
    assert q.get_nowait() == ("status", "filler")
    assert q.empty()


def test_emit_while_cancelled_suppresses_both_arms():
    q = queue_module.Queue()
    wca._chat_status_queues["cx-s"] = q
    _cancel("cx-s")
    try:
        wca.emit_chat_status("cx-s", "late worker")
        assert q.empty()
        assert wca.get_chat_live_status("cx-s")["active"] is False
    finally:
        delivery._cancel_sticky.clear()


def test_emit_after_unregister_writes_live_only():
    q = queue_module.Queue()
    wca._chat_status_queues["unreg-s"] = q
    wca._chat_status_queues.pop("unreg-s", None)
    wca.emit_chat_status("unreg-s", "after")
    assert q.empty()
    assert wca.get_chat_live_status("unreg-s")["status"] == "after"


def test_register_overwrite_replaces_subscriber():
    first, second = queue_module.Queue(), queue_module.Queue()
    wca._chat_status_queues["over-s"] = first
    wca._chat_status_queues["over-s"] = second
    wca.emit_chat_status("over-s", "hi")
    assert first.empty()
    assert second.get_nowait() == ("status", "hi")


def test_pipeline_phase_line_format():
    seen = []
    emit_pipeline_status(
        "ph-s", "route", "Routing…", emit_fn=lambda sid, text: seen.append((sid, text))
    )
    assert seen == [("ph-s", "[route] Routing…")]
    emit_pipeline_status("ph-s", "llm", "", emit_fn=lambda sid, text: seen.append((sid, text)))
    assert seen[-1] == ("ph-s", "[llm]")
    before = len(seen)
    emit_pipeline_status("", "llm", "x", emit_fn=lambda sid, text: seen.append((sid, text)))
    emit_pipeline_status("ph-s", "", "x", emit_fn=lambda sid, text: seen.append((sid, text)))
    assert len(seen) == before


def test_pipeline_phase_leaf_never_imports_entry_module():
    import api.chat_status_phases as phases

    src = Path(phases.__file__).read_text(encoding="utf-8")
    assert "web_chat_api" not in src


def test_pipeline_phase_through_guarded_wrapper():
    q = queue_module.Queue()
    wca._chat_status_queues["phw-s"] = q
    emit_pipeline_status("phw-s", "tool", "Running…", emit_fn=wca.emit_chat_status)
    assert q.get_nowait() == ("status", "[tool] Running…")
    assert wca.get_chat_live_status("phw-s")["status"] == "[tool] Running…"


def test_service_owns_registry_without_monolith_import():
    import api.chat_status as svc

    src = Path(svc.__file__).read_text(encoding="utf-8")
    assert "web_chat_api" not in src
    assert "chat_delivery" not in src


def test_registry_identity_across_service_and_wrapper():
    import api.chat_status as svc

    assert wca._chat_status_queues is svc._QUEUES
    q = queue_module.Queue()
    svc.register_status_queue("ident-q", q)
    assert wca._chat_status_queues.get("ident-q") is q
    wca.emit_chat_status("ident-q", "hi")
    assert q.get_nowait() == ("status", "hi")
    svc.unregister_status_queue("ident-q")
    assert wca._chat_status_queues.get("ident-q") is None


def test_module_cache_shares_one_registry_instance():
    import sys

    import api.chat_status as first

    second = sys.modules["api.chat_status"]
    assert first is second
    assert first._QUEUES is second._QUEUES


def test_service_emit_end_to_end_with_fakes():
    import api.chat_status as svc

    published, queued = [], queue_module.Queue()
    svc.register_status_queue("e2e-s", queued)
    try:
        svc.emit_status(
            "e2e-s", "step one",
            is_cancelled=lambda sid: False,
            publish_live=published.append,
        )
        assert published == ["step one"]
        assert queued.get_nowait() == ("status", "step one")
        # Cancelled: neither published nor queued.
        svc.emit_status(
            "e2e-s", "late",
            is_cancelled=lambda sid: True,
            publish_live=published.append,
        )
        assert published == ["step one"]
        assert queued.empty()
        # Raising publisher never breaks the queue arm.
        def _boom(_text):
            raise RuntimeError("down")

        svc.emit_status(
            "e2e-s", "despite", is_cancelled=lambda sid: False, publish_live=_boom
        )
        assert queued.get_nowait() == ("status", "despite")
    finally:
        svc.unregister_status_queue("e2e-s")


def test_service_emit_requires_explicit_policy():
    import inspect

    import api.chat_status as svc

    params = inspect.signature(svc.emit_status).parameters
    assert params["is_cancelled"].default is inspect.Parameter.empty
    assert params["publish_live"].default is inspect.Parameter.empty


def test_concurrent_emit_and_unregister_stays_consistent():
    q = queue_module.Queue()
    wca._chat_status_queues["race-s"] = q
    errors = []

    def worker(n):
        try:
            for i in range(50):
                wca.emit_chat_status("race-s", f"m{n}-{i}")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    wca._chat_status_queues.pop("race-s", None)
    for t in threads:
        t.join(timeout=30)
    assert not errors
    assert wca._chat_status_queues.get("race-s") is None
    drained = 0
    while not q.empty():
        q.get_nowait()
        drained += 1
    assert drained <= 200

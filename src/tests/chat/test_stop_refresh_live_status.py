"""Stop then refresh must not resurrect generating activity (CH-000522).

The in-tab Stop flags (`userStoppedGeneration`) die on reload. After refresh
the client trusts `/api/chat-live-status` `generating`. A cancelled Codex
worker that still calls `emit_chat_status` used to flip that flag back on —
activity appeared, vanished, then appeared again until the worker actually died.
"""

from __future__ import annotations

from pathlib import Path

CHAT_JS = Path(__file__).resolve().parents[2] / "web" / "js" / "chat/chat_page.js"


def setup_function(_fn=None):
    from api import chat_delivery as delivery
    from api import chat_run_registry as reg
    import api.web_chat_api as wca

    delivery._busy.clear()
    delivery._pending.clear()
    delivery._last_turn.clear()
    delivery._cancel_sticky.clear()
    reg._runs.clear()
    with wca._chat_live_status_lock:
        wca._chat_live_status.clear()


def _refresh_poll(session_id):
    """Same flags `/api/chat-live-status` exposes to a refresh."""
    import api.web_chat_api as wca

    generating, _active, _cancelled, live = wca._public_live_generating(session_id)
    return generating, live


def _plant_leftover_live(session_id, status: str) -> None:
    """Bypass set_chat_live_status (which no-ops after Stop) to leave a stale row."""
    import time
    import api.web_chat_api as wca

    keys = wca._live_status_keys(session_id)
    entry = {
        "active": True,
        "status": status,
        "updated_at": time.time(),
        "report_url": None,
        "query_id": None,
    }
    with wca._chat_live_status_lock:
        for k in keys:
            wca._chat_live_status[k] = entry


def test_late_codex_status_after_stop_does_not_look_generating_on_refresh():
    from api import chat_delivery as delivery
    from api.chat_run_registry import cancel_session_runs
    import api.web_chat_api as wca

    sid = "522-stop-refresh"
    assert delivery.try_begin(sid)
    wca.set_chat_live_status(sid, "tool 1: shell …", active=True)
    generating, _ = _refresh_poll(sid)
    assert generating is True

    info = cancel_session_runs(sid)
    assert info.get("cancelled") is True
    generating, _ = _refresh_poll(sid)
    assert generating is False
    assert delivery.is_turn_cancelled(sid) is True

    # Dying Codex still emits status after Stop. Refresh polls this.
    wca.emit_chat_status(sid, "tool 13: shell …")
    generating, live = _refresh_poll(sid)
    assert generating is False, (
        f"late emit_chat_status after Stop resurrected generating={generating} "
        f"live={live} — this is the CH-000522 refresh activity flash"
    )
    assert not live.get("active")


def test_repeated_late_status_pulses_stay_idle_after_stop():
    """User saw activity *multiple times* after refresh — each late pulse."""
    from api.chat_run_registry import cancel_session_runs
    from api import chat_delivery as delivery
    import api.web_chat_api as wca

    sid = "522-stop-pulses"
    assert delivery.try_begin(sid)
    wca.set_chat_live_status(sid, "Connecting...", active=True)
    cancel_session_runs(sid)

    pulses = []
    for i in range(3):
        wca.emit_chat_status(sid, f"tool {10 + i}: shell …")
        generating, live = _refresh_poll(sid)
        pulses.append(generating)
        # Simulate a poll that sees idle (cancel already cleared once; a pulse
        # must not turn it back on).
        if generating:
            wca.clear_chat_live_status(sid)
            generating2, _ = _refresh_poll(sid)
            pulses.append(generating2)

    assert pulses == [False, False, False], (
        f"late status pulses after Stop flickered generating: {pulses}"
    )


def test_live_status_endpoint_idle_when_cancelled_even_if_row_lingers(monkeypatch):
    from flask import Flask
    from api import chat_delivery as delivery
    from api.chat_run_registry import cancel_session_runs
    import api.web_chat_api as wca

    # The route requires a signed-in caller; auth is not under test here.
    monkeypatch.setattr(
        wca, "_require_session_actor",
        lambda session_id: ({"id": 1}, session_id, None),
    )
    sid = "522-endpoint"
    assert delivery.try_begin(sid)
    cancel_session_runs(sid)
    # Bypass emit_chat_status in case that path is gated — leftover row.
    _plant_leftover_live(sid, "still narrating")

    app = Flask(__name__)
    app.add_url_rule(
        "/api/chat-live-status",
        view_func=wca.chat_live_status,
        methods=["GET"],
    )
    client = app.test_client()
    data = client.get(f"/api/chat-live-status?session_id={sid}").get_json()
    assert data["success"] is True
    assert data["generating"] is False, (
        f"cancelled chat still advertised generating={data.get('generating')} "
        f"active={data.get('active')} status={data.get('status')!r} on refresh poll"
    )
    assert data.get("cancelled") is True


def test_stale_done_clears_live_status_so_refresh_stays_idle():
    """pump_status skips clear_chat_live_status when the turn is stale/cancelled."""
    from api import chat_delivery as delivery
    from api.chat_run_registry import cancel_session_runs
    import api.web_chat_api as wca

    sid = "522-stale-done"
    assert delivery.try_begin(sid)
    token = delivery.current_turn(sid)
    wca.set_chat_live_status(sid, "Connecting...", active=True)
    cancel_session_runs(sid)
    assert delivery.is_stale_turn(sid, token) is True
    # Worker that missed the cancel gate left a live-status row, then finished.
    _plant_leftover_live(sid, "tool 13: shell …")
    wca._clear_live_status_on_stream_done(
        sid,
        stale=True,
        cancelled=delivery.is_turn_cancelled(sid),
    )
    generating, live = _refresh_poll(sid)
    assert generating is False, (
        "cancelled worker finishing must clear live-status; otherwise refresh "
        f"keeps showing activity (live={live})"
    )
    assert not live.get("active")


def test_client_refresh_honors_persisted_stop_notice():
    """Reload drops userStoppedGeneration; the DB Stop notice must still win."""
    src = CHAT_JS.read_text(encoding="utf-8")
    start = src.find("function updateRemoteWaitingFromMessages")
    assert start >= 0
    depth = 0
    i = src.find("{", start)
    j = i
    while j < len(src):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    body = src[start : j + 1]
    assert "transcriptEndsWithGenerationStop" in body, (
        "updateRemoteWaitingFromMessages must honor the persisted Stop notice "
        "after refresh (in-memory userStoppedGeneration is gone) — CH-000522."
    )
    assert "liveStatus.cancelled" in body, (
        "hub live-status after refresh passes empty messages; cancelled must "
        "still suppress the activity spinner"
    )
    helper_start = src.find("function transcriptEndsWithGenerationStop")
    assert helper_start >= 0
    helper = src[helper_start : helper_start + 1400]
    assert "generation-stop" in helper


def test_client_stop_notice_only_counts_after_last_turn():
    """An older turn's Stop notice must not hide a newer remote run (CH-000593).

    Phone sent a new prompt after a Stop; the desktop pane found the earlier
    Stop notice anywhere in the DOM and suppressed the working bubble.
    """
    src = CHAT_JS.read_text(encoding="utf-8")
    helper_start = src.find("function transcriptEndsWithGenerationStop")
    assert helper_start >= 0
    helper_end = src.find("\n    function ", helper_start + 10)
    helper = src[helper_start:helper_end]
    assert 'querySelector(\'.message.system[data-kind="generation-stop"]\')' not in helper, (
        "DOM fallback must scan from the tail and stop at the last user/assistant "
        "bubble, not match any Stop notice in the chat"
    )
    assert "break" in helper.split("chatMessages", 1)[1]

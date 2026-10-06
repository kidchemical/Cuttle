"""Phone/UI must not park a turn in a different Cuttle chat.

CH-000196: sending ``CH-000196`` (or an agent CLI session id) used to fail
``int()`` parse, mint a *new* DB row, and SSE-adopt the client into that row.
The in-flight user message + agent activity then showed under the wrong chat.
"""

from __future__ import annotations

from api import chat_delivery as delivery
from api import mobile_companion as mc
from api.cuttle_ui_capabilities import numeric_chat_session_id


def test_parse_auth_db_session_id_accepts_ch_handle():
    from api.web_chat_api import _parse_auth_db_session_id

    assert _parse_auth_db_session_id(196) == 196
    assert _parse_auth_db_session_id("196") == 196
    assert _parse_auth_db_session_id("CH-000196") == 196
    assert _parse_auth_db_session_id("db_session_196") == 196
    assert _parse_auth_db_session_id("native_999") is None
    assert _parse_auth_db_session_id("anon") is None
    assert _parse_auth_db_session_id("") is None
    assert _parse_auth_db_session_id(None) is None


def test_absent_session_id_only_for_welcome_splash():
    from api.web_chat_api import _auth_session_id_is_absent

    assert _auth_session_id_is_absent(None) is True
    assert _auth_session_id_is_absent("") is True
    assert _auth_session_id_is_absent("anon") is True
    assert _auth_session_id_is_absent("CH-000196") is False
    assert _auth_session_id_is_absent("196") is False
    assert _auth_session_id_is_absent("native_999") is False


def test_numeric_and_parse_agree_on_handles():
    from api.web_chat_api import _parse_auth_db_session_id

    for raw in (196, "196", "CH-000196", "db_session_196"):
        assert _parse_auth_db_session_id(raw) == numeric_chat_session_id(raw) == 196


def test_chat_complete_payload_ignores_agent_cli_session_id():
    payload = mc.chat_complete_payload(
        196,
        {"session_id": "native_999", "response": "done", "pipeline": "cursor"},
    )
    assert payload["session_id"] == "196"
    assert payload["response"] == "done"


def test_chat_complete_payload_still_normalizes_ch_handle():
    payload = mc.chat_complete_payload("CH-000185", {"response": "ok"})
    assert payload["session_id"] == "185"


def test_delivery_keys_alias_ch_handle():
    keys = delivery._session_keys("CH-000090")
    assert "90" in keys
    assert "db_session_90" in keys
    delivery._busy.clear()
    delivery._pending.clear()
    delivery._last_turn.clear()
    assert delivery.try_begin("CH-000090")
    assert delivery.is_busy("90")
    assert delivery.is_busy("db_session_90")
    ids = delivery.busy_session_ids()
    assert "90" in ids
    assert "CH-000090" not in ids


def test_pending_result_keeps_requested_session_id():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "api" / "web_chat_api.py").read_text(
        encoding="utf-8"
    )
    # Must pin parked replies to the polled chat, not result.session_id (CLI ids).
    assert "'session_id': sid," in src
    assert "result.get('session_id', sid)" not in src.split("def chat_pending_result", 1)[1].split("\n@app.route", 1)[0]


def test_client_pins_in_flight_turn_to_bound_session():
    """Switching chats mid-turn must not paint the reply into the open transcript."""
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[2] / "web" / "js" / "chat/chat_page.js"
    ).read_text(encoding="utf-8")
    assert "boundSessionId" in src
    assert "canPaintTurnHere" in src
    assert "isLoadingThisSession" in src
    assert "isViewingSession" in src
    # Recovery after stream detach must use the origin session, not currentSessionId.
    assert "const pendingSid = turnSessionId() || requestBody.session_id" in src
    assert "if (!canPaintTurnHere())" in src
    # Hub/status idle recovery must not treat another chat's isLoading as this one.
    assert "isLoadingThisSession()" in src
    assert "never append it to the" in src or "never append it to the open" in src

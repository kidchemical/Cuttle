"""Tests for chat session id alias normalization."""

from api.session_keys import chat_session_keys


def test_chat_session_keys_numeric_and_handles():
    keys = chat_session_keys(201)
    assert "201" in keys
    assert "db_session_201" in keys
    assert "CH-000201" in keys


def test_chat_session_keys_ch_handle():
    keys = chat_session_keys("CH-000201")
    assert "201" in keys
    assert "db_session_201" in keys
    assert "CH-000201" in keys


def test_chat_session_keys_message_ref():
    """UI share refs (CH-000201-9) alias the same session as the bare handle."""
    keys = chat_session_keys("CH-000201-9")
    assert "201" in keys
    assert "db_session_201" in keys
    assert "CH-000201" in keys
    assert "CH-000201-9" in keys


def test_bare_chat_session_id_collapses_aliases():
    from api.session_keys import bare_chat_session_id

    assert bare_chat_session_id("478") == "478"
    assert bare_chat_session_id("db_session_478") == "478"
    assert bare_chat_session_id("CH-000478") == "478"
    assert bare_chat_session_id("CH-000478-9") == "478"
    assert bare_chat_session_id("supervised-worker-1") == "supervised-worker-1"
    assert bare_chat_session_id(None) == ""

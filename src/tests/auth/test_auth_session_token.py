"""Tests for mobile-friendly session token extraction."""

import pytest
from flask import Flask

from api.auth_session import get_request_session_token, wants_session_token_in_body


@pytest.fixture
def app():
    return Flask(__name__)


def test_get_request_session_token_prefers_cookie(app):
    with app.test_request_context(
        "/",
        headers={"Authorization": "Bearer from-header"},
        environ_base={"HTTP_COOKIE": "session_token=from-cookie"},
    ):
        assert get_request_session_token() == "from-cookie"


def test_get_request_session_token_bearer_fallback(app):
    with app.test_request_context("/", headers={"Authorization": "Bearer abc.def"}):
        assert get_request_session_token() == "abc.def"


def test_get_request_session_token_x_header(app):
    with app.test_request_context("/", headers={"X-Cuttle-Session-Token": "hdr-tok"}):
        assert get_request_session_token() == "hdr-tok"


def test_wants_session_token_in_body_for_mobile_ua(app):
    with app.test_request_context("/", headers={"User-Agent": "Mozilla CuttleMobile/0.1"}):
        assert wants_session_token_in_body() is True


def test_wants_session_token_in_body_for_header(app):
    with app.test_request_context("/", headers={"X-Cuttle-Client": "mobile"}):
        assert wants_session_token_in_body() is True


def test_wants_session_token_in_body_desktop_false(app):
    with app.test_request_context("/", headers={"User-Agent": "Mozilla/5.0 Chrome/120"}):
        assert wants_session_token_in_body() is False

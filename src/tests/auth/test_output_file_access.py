"""Who may read files under <home>/output/ and the query logs."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from api import http_authz

OWNER = {"id": 1, "auth_provider": "local"}


def _denied(filename, user, owns_chat=True):
    db = MagicMock()
    db.get_chat_session.return_value = {"id": 5} if owns_chat else None
    app = Flask(__name__)
    with app.test_request_context("/"), patch.object(http_authz, "current_user", return_value=user), \
            patch("api.auth_db.get_auth_db", return_value=db):
        result = http_authz.output_file_denied(filename)
    return None if result is None else result[1]


@pytest.mark.parametrize("name", ["uploads/5/a.png", "shared/a.png", "job-status.json", "avatars/1/x.png"])
def test_anonymous_is_refused_everywhere(name):
    assert _denied(name, None) == 401


@pytest.mark.parametrize("name", ["shared/a.png", "job-status.json", "avatars/1/x.png"])
def test_signed_in_reads_non_chat_files(name):
    assert _denied(name, OWNER) is None


def test_upload_needs_access_to_its_chat():
    assert _denied("uploads/5/a.png", OWNER) is None
    assert _denied("uploads/5/a.png", OWNER, owns_chat=False) is not None


def test_dotted_path_cannot_dodge_the_chat_check():
    assert _denied("shared/../uploads/5/a.png", OWNER, owns_chat=False) is not None


def test_pre_mint_upload_folder_needs_only_a_login():
    assert _denied("uploads/anon/a.png", OWNER, owns_chat=False) is None


@pytest.mark.parametrize("path", ["/output/shared/a.png", "/output/uploads/5/a.png", "/output/x-status.json",
                                 "/logs/", "/logs/query_data_abc.json"])
def test_real_routes_refuse_anonymous_callers(path):
    from api.web_chat_api import app

    assert app.test_client().get(path).status_code == 401


def test_logs_are_owner_only():
    from api import http_authz as authz
    from api.web_chat_api import app

    with patch.object(authz, "current_user", return_value={"id": 2}), \
            patch.object(authz, "is_owner_user", return_value=False):
        assert app.test_client().get("/logs/query_data_abc.json").status_code == 403

"""Styled error pages: page loads get the Cuttle page, every other caller keeps Flask's response.

Owner: api.error_pages (+ src/web/error_page.html, shared with Electron and Android).
"""
import json
import re

import pytest
from flask import Flask, abort, jsonify

from api import error_pages

NAV = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8", "Sec-Fetch-Dest": "document"}


@pytest.fixture
def client():
    app = Flask(__name__)

    @app.get("/boom")
    def boom():
        raise RuntimeError("kaboom")

    @app.get("/api/thing")
    def api_thing():
        abort(404)

    @app.post("/only-post")
    def only_post():
        return jsonify(ok=True)

    @app.get("/slash/")
    def slash():
        return "ok"

    error_pages.install(app)
    return app.test_client()


def _data(resp):
    m = re.search(r'<script id="cuttleErrorData" type="application/json">(.*?)</script>', resp.get_data(as_text=True), re.S)
    return json.loads(m.group(1))


def test_page_load_404_gets_the_styled_page_with_error_data(client):
    resp = client.get("/nope?x=1", headers=NAV)
    assert resp.status_code == 404 and resp.mimetype == "text/html"
    assert resp.headers["Cache-Control"] == "no-store"
    data = _data(resp)
    assert data["kind"] == "http" and data["status"] == 404 and data["name"] == "Not Found"
    assert data["path"] == "/nope?x=1" and data["method"] == "GET" and data["request_id"]
    # A pane iframe is a page load too.
    assert client.get("/nope", headers={**NAV, "Sec-Fetch-Dest": "iframe"}).mimetype == "text/html"


def test_non_page_callers_keep_flask_default_response(client):
    for headers in ({"Accept": "application/json"},
                    {"Accept": "*/*"},
                    {**NAV, "Sec-Fetch-Dest": "image"},
                    {**NAV, "Sec-Fetch-Dest": "empty"}):  # fetch()
        resp = client.get("/nope", headers=headers)
        assert resp.status_code == 404 and b"cuttleErrorData" not in resp.data
    resp = client.get("/api/thing", headers=NAV)  # /api/* never gets the page
    assert resp.status_code == 404 and b"cuttleErrorData" not in resp.data


def test_unhandled_exception_is_a_styled_500_with_request_id(client, capsys):
    resp = client.get("/boom", headers=NAV)
    assert resp.status_code == 500
    data = _data(resp)
    assert data["status"] == 500 and data["request_id"]
    assert data["request_id"] in capsys.readouterr().out  # same id in the Flask log
    assert "kaboom" not in resp.get_data(as_text=True)  # no exception text to the browser
    assert b"cuttleErrorData" not in client.get("/boom", headers={"Accept": "application/json"}).data


def test_headers_kept_and_redirects_untouched(client):
    resp = client.get("/only-post", headers=NAV)
    assert resp.status_code == 405 and "POST" in resp.headers["Allow"] and _data(resp)["status"] == 405
    assert client.get("/slash", headers=NAV).status_code in (301, 308)


def test_injected_values_cannot_close_the_script_tag(client):
    resp = client.get("/</script><script>alert(1)</script>", headers=NAV)
    body = resp.get_data(as_text=True)
    assert "<script>alert(1)" not in body
    assert _data(resp)["path"] == "/</script><script>alert(1)</script>"

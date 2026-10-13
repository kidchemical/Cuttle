"""HTTP surface for the Test Lab App."""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, request, send_from_directory

from api.experimental import is_enabled
from api.http_authz import authenticated_required, owner_required
from api.test_lab import service, store

test_lab_bp = Blueprint("test_lab", __name__, url_prefix="/api/test-lab")
test_lab_pages_bp = Blueprint("test_lab_pages", __name__)


def _disabled():
    if is_enabled("test_lab"):
        return None
    return jsonify(success=False, disabled=True,
                   error="Enable Test Lab in Settings → Experimental."), 403


@test_lab_bp.get("/projects")
@owner_required
def projects_route():
    disabled = _disabled()
    if disabled:
        return disabled
    try:
        return jsonify(success=True, projects=service.list_projects())
    except service.TestLabError as exc:
        return jsonify(success=False, error=str(exc)), 503


@test_lab_bp.get("/projects/<int:project_id>")
@owner_required
def project_route(project_id: int):
    disabled = _disabled()
    if disabled:
        return disabled
    try:
        data = service.discover_suites(project_id)
        data.pop("_root", None)
        data.pop("_suites", None)
        return jsonify(success=True, **data)
    except service.TestLabError as exc:
        message = str(exc)
        return jsonify(success=False, error=message), 404 if "not found" in message.lower() else 409


@test_lab_bp.post("/projects/<int:project_id>/runs")
@owner_required
def start_run_route(project_id: int):
    disabled = _disabled()
    if disabled:
        return disabled
    body = request.get_json(silent=True)
    suite_id = body.get("suite_id") if isinstance(body, dict) else None
    if not isinstance(suite_id, str) or not suite_id or len(suite_id) > 500:
        return jsonify(success=False, error="Choose a test suite."), 400
    try:
        run = service.start_run(project_id, suite_id)
        return jsonify(success=True, run=run), 202
    except RuntimeError as exc:
        return jsonify(success=False, error=str(exc)), 409
    except service.TestLabError as exc:
        message = str(exc)
        return jsonify(success=False, error=message), 404 if "not found" in message.lower() else 400


@test_lab_bp.get("/runs/<run_id>")
@owner_required
def run_route(run_id: str):
    disabled = _disabled()
    if disabled:
        return disabled
    if len(run_id) != 32 or any(char not in "0123456789abcdef" for char in run_id.lower()):
        return jsonify(success=False, error="Test run not found."), 404
    run = store.get_run(run_id)
    if not run:
        return jsonify(success=False, error="Test run not found."), 404
    return jsonify(success=True, run=run)


@test_lab_pages_bp.get("/test_lab_page.html")
@authenticated_required
def test_lab_page():
    disabled = _disabled()
    if disabled:
        return disabled
    return send_from_directory(Path(__file__).resolve().parents[2] / "web", "test_lab_page.html")

"""HTTP surface for agent review (transport only; behavior in ``service``).

Owner-only. While the ``agent_review`` flag is off every route answers
``403 {disabled: true}``. ``root`` is a registered workspace path, resolved
through the IDE allowlist; ``path`` is relative to it.
"""

from __future__ import annotations

import json
import time

from flask import Blueprint, Response, jsonify, request, stream_with_context

from api.agent_review import service
from api.http_authz import owner_required

agent_review_bp = Blueprint("agent_review", __name__, url_prefix="/api/agent-review")


def _disabled():
    if service.enabled():
        return None
    return jsonify(success=False, disabled=True, error="Enable Agent review in Settings → Experimental."), 403


def _root(value):
    from api.ide.service import resolve_workspace
    return resolve_workspace(value)


def _run(fn):
    disabled = _disabled()
    if disabled:
        return disabled
    from api.ide.service import IdeError
    try:
        return jsonify(success=True, **fn())
    except (service.ReviewError, IdeError) as exc:
        return jsonify(success=False, error=str(exc), conflict=isinstance(exc, service.Conflict)), exc.status


def _body() -> dict:
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


def _paths(body: dict):
    if body.get("path"):
        return [str(body["path"])]
    paths = body.get("paths")
    return [str(p) for p in paths] if isinstance(paths, list) else None


def _hunks(body: dict):
    ids = body.get("hunks")
    return [str(h) for h in ids] if isinstance(ids, list) else None


@agent_review_bp.get("/files")
@owner_required
def files_route():
    return _run(lambda: service.list_reviews(_root(request.args.get("root"))))


@agent_review_bp.get("/file")
@owner_required
def file_route():
    return _run(lambda: service.file_review(_root(request.args.get("root")), request.args.get("path") or ""))


@agent_review_bp.post("/keep")
@owner_required
def keep_route():
    body = _body()
    return _run(lambda: service.keep(_root(body.get("root")), _paths(body), _hunks(body)))


@agent_review_bp.post("/undo")
@owner_required
def undo_route():
    body = _body()
    return _run(lambda: service.undo(_root(body.get("root")), _paths(body), _hunks(body), body.get("etag")))


@agent_review_bp.get("/revision")
@owner_required
def revision_route():
    """Cheap change check for clients without the stream (polling fallback)."""
    return _run(lambda: {"revision": service.revision()})


@agent_review_bp.get("/stream")
@owner_required
def stream_route():
    """Wake signal: one ``revision`` event per change; clients refetch."""
    disabled = _disabled()
    if disabled:
        return disabled

    @stream_with_context
    def generate():
        last, idle = None, 0
        while service.enabled():
            rev = service.revision()
            if rev != last:
                last, idle = rev, 0
                yield f"data: {json.dumps({'revision': rev})}\n\n"
            else:
                idle += 1
                if idle % 15 == 0:
                    yield ": keepalive\n\n"
            time.sleep(1)

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

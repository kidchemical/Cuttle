"""HTTP surface for the IDE App (transport only; behavior in ``service``).

Every route is owner-only: the editor reads and writes project files.
While the ``ide`` flag is off they answer ``403 {disabled: true}``.
"""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, request, send_file, send_from_directory

from api.experimental import is_enabled
from api.http_authz import authenticated_required, owner_required
from api.ide import service

ide_bp = Blueprint("ide", __name__, url_prefix="/api/ide")
ide_pages_bp = Blueprint("ide_pages", __name__)


def _disabled():
    if is_enabled("ide"):
        return None
    return jsonify(success=False, disabled=True, error="Enable the IDE in Settings → Experimental."), 403


def _body() -> dict:
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


def _root(source) -> Path:
    return service.resolve_workspace(source.get("root"))


def _error(exc: service.IdeError):
    payload = {"success": False, "error": str(exc)}
    if isinstance(exc, service.Conflict):
        payload["conflict"] = True
        payload["current"] = exc.current
    return jsonify(payload), exc.status


def _run(fn):
    disabled = _disabled()
    if disabled:
        return disabled
    try:
        return jsonify(success=True, **fn())
    except service.IdeError as exc:
        return _error(exc)
    except (TypeError, ValueError) as exc:
        return jsonify(success=False, error=str(exc) or "Invalid request"), 400
    except PermissionError as exc:
        return jsonify(success=False, error=f"Permission denied: {exc.filename or exc}"), 403
    except OSError as exc:
        return jsonify(success=False, error=str(exc)), 500


def _flag(source, key: str) -> bool:
    value = source.get(key)
    return value in (True, 1) or str(value).lower() in ("1", "true", "yes")


@ide_bp.get("/workspaces")
@owner_required
def workspaces_route():
    return _run(lambda: {"workspaces": service.list_workspaces()})


@ide_bp.get("/tree")
@owner_required
def tree_route():
    return _run(lambda: service.list_dir(_root(request.args), request.args.get("dir") or ""))


@ide_bp.get("/files")
@owner_required
def files_route():
    return _run(lambda: service.list_files(_root(request.args)))


@ide_bp.get("/file")
@owner_required
def read_route():
    return _run(lambda: {"file": service.read_file(_root(request.args), request.args.get("file") or "")})


@ide_bp.put("/file")
@owner_required
def write_route():
    body = _body()
    return _run(lambda: {"file": service.write_file(
        _root(body), body.get("file") or "", body.get("content"),
        etag=body.get("etag"), encoding=str(body.get("encoding") or "utf-8"),
        create=_flag(body, "create"), force=_flag(body, "force"))})


@ide_bp.get("/raw")
@owner_required
def raw_route():
    disabled = _disabled()
    if disabled:
        return disabled
    try:
        path, mime = service.raw_image(_root(request.args), request.args.get("file") or "")
    except service.IdeError as exc:
        return _error(exc)
    response = send_file(path, mimetype=mime, conditional=True, max_age=0)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return response


@ide_bp.post("/stat")
@owner_required
def stat_route():
    body = _body()
    files = body.get("files") if isinstance(body.get("files"), list) else []
    return _run(lambda: {"files": service.stat_files(_root(body), [str(f) for f in files])})


@ide_bp.post("/entry")
@owner_required
def create_route():
    body = _body()
    return _run(lambda: {"entry": service.create_entry(_root(body), body.get("path") or "",
                                                       str(body.get("kind") or "file"))})


@ide_bp.post("/rename")
@owner_required
def rename_route():
    body = _body()
    return _run(lambda: service.rename_entry(_root(body), body.get("from") or "", body.get("to") or ""))


@ide_bp.post("/delete")
@owner_required
def delete_route():
    body = _body()
    return _run(lambda: service.delete_entry(_root(body), body.get("path") or ""))


def _search_options(body) -> dict:
    return {"regex": _flag(body, "regex"), "case": _flag(body, "case"), "word": _flag(body, "word"),
            "include": str(body.get("include") or ""), "exclude": str(body.get("exclude") or "")}


@ide_bp.post("/search")
@owner_required
def search_route():
    body = _body()
    return _run(lambda: service.search(_root(body), str(body.get("query") or ""),
                                       max_results=int(body.get("max_results") or 2000),
                                       **_search_options(body)))


@ide_bp.post("/replace")
@owner_required
def replace_route():
    body = _body()
    paths = body.get("paths") if isinstance(body.get("paths"), list) else None
    return _run(lambda: service.replace(_root(body), str(body.get("query") or ""),
                                        str(body.get("replacement") or ""), paths=paths,
                                        **_search_options(body)))


@ide_bp.get("/original")
@owner_required
def original_route():
    return _run(lambda: {"original": service.git_original(
        _root(request.args), request.args.get("file") or "", request.args.get("ref") or "HEAD")})


@ide_bp.post("/complete")
@owner_required
def complete_route():
    body = _body()
    return _run(lambda: service.complete(path=str(body.get("file") or ""),
                                         language=str(body.get("language") or ""),
                                         prefix=str(body.get("prefix") or ""),
                                         suffix=str(body.get("suffix") or "")))


@ide_pages_bp.get("/ide_page.html")
@authenticated_required
def ide_page():
    disabled = _disabled()
    if disabled:
        return disabled
    return send_from_directory(Path(__file__).resolve().parents[2] / "web", "ide_page.html")

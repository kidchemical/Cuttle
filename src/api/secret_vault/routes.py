"""HTTP transport for the secret vault (owner-only, flag-gated).

No route ever returns a secret value. Values arrive only on
``PUT /api/secret-vault/secrets/<name>`` (Secrets App or a chat secret field).
"""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, request, send_from_directory

from api.http_authz import authenticated_required, owner_required
from api.secret_vault import service
from api.secret_vault.core.names import valid_name

secret_vault_bp = Blueprint("secret_vault", __name__, url_prefix="/api/secret-vault")
secret_vault_pages_bp = Blueprint("secret_vault_pages", __name__)

_WEB = Path(__file__).resolve().parents[2] / "web"


def _disabled():
    if service.is_enabled():
        return None
    return jsonify(success=False, disabled=True,
                   error="Enable the Secret vault in Settings → Experimental."), 403


def _body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _fail(exc: Exception, code: int = 400):
    return jsonify(success=False, error=str(exc)), code


@secret_vault_bp.get("/status")
@owner_required
def status_route():
    return _disabled() or jsonify(success=True, **service.status())


@secret_vault_bp.get("/secrets")
@owner_required
def list_route():
    return _disabled() or jsonify(success=True, secrets=service.list_secrets())


@secret_vault_bp.put("/secrets/<name>")
@owner_required
def put_route(name: str):
    disabled = _disabled()
    if disabled:
        return disabled
    if not valid_name(name):
        return _fail(ValueError("Invalid secret name."))
    body = _body()
    value = body.get("value")
    if value is not None and not isinstance(value, str):
        return _fail(ValueError("value must be a string."))
    try:
        row = service.put_secret(
            name, value=value,
            backend=body.get("backend") or None,
            ref=body.get("ref") if isinstance(body.get("ref"), str) else None,
            description=body.get("description") if isinstance(body.get("description"), str) else None,
            project=body.get("project") if isinstance(body.get("project"), str) else None,
            grant_session=str(body.get("session_id") or "") or None)
    except (service.VaultError, ValueError) as exc:
        return _fail(exc)
    return jsonify(success=True, secret=row)


@secret_vault_bp.delete("/secrets/<name>")
@owner_required
def delete_route(name: str):
    disabled = _disabled()
    if disabled:
        return disabled
    keep_value = request.args.get("keep_value") in ("1", "true")
    try:
        service.delete_secret(name, delete_value=not keep_value)
    except service.VaultError as exc:
        return _fail(exc, 404 if "No secret" in str(exc) else 409)
    return jsonify(success=True)


@secret_vault_bp.get("/grants")
@owner_required
def grants_route():
    return _disabled() or jsonify(success=True,
                                  grants=service.list_grants(request.args.get("session_id") or None))


@secret_vault_bp.post("/grants")
@owner_required
def grant_route():
    disabled = _disabled()
    if disabled:
        return disabled
    body = _body()
    names = body.get("secrets")
    if not isinstance(names, list) or not names or not all(valid_name(n) for n in names):
        return _fail(ValueError("secrets must be a list of secret names."))
    try:
        service.grant(str(body.get("session_id") or ""), names,
                      mode=str(body.get("mode") or "chat"), command=str(body.get("command") or ""))
    except service.VaultError as exc:
        return _fail(exc)
    return jsonify(success=True, grants=service.list_grants(body.get("session_id")))


@secret_vault_bp.delete("/grants")
@owner_required
def revoke_route():
    disabled = _disabled()
    if disabled:
        return disabled
    removed = service.revoke(request.args.get("session_id") or None,
                             request.args.get("secret") or None)
    return jsonify(success=True, removed=removed)


@secret_vault_bp.put("/config")
@owner_required
def config_route():
    disabled = _disabled()
    if disabled:
        return disabled
    try:
        return jsonify(success=True, config=service.update_config(_body()))
    except service.VaultError as exc:
        return _fail(exc)


@secret_vault_bp.put("/backends/<backend_id>")
@owner_required
def save_backend_route(backend_id: str):
    disabled = _disabled()
    if disabled:
        return disabled
    body = {**_body(), "id": backend_id}
    try:
        return jsonify(success=True, backend=service.save_backend(body),
                       backends=service.list_backends())
    except (service.VaultError, ValueError) as exc:
        return _fail(exc)


@secret_vault_bp.delete("/backends/<backend_id>")
@owner_required
def delete_backend_route(backend_id: str):
    disabled = _disabled()
    if disabled:
        return disabled
    try:
        service.delete_backend(backend_id)
    except service.VaultError as exc:
        return _fail(exc, 409)
    return jsonify(success=True, backends=service.list_backends())


@secret_vault_pages_bp.get("/secrets_page.html")
@authenticated_required
def secrets_page():
    return _disabled() or send_from_directory(_WEB, "secrets_page.html")

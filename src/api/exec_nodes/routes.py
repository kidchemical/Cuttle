"""HTTP surface for execution nodes, nested under ``/api/workers`` (the mesh).

Two audiences, never mixed: the owner's UI (node list, a chat's node, node
preferences) and a node's worker relaying the turn it claimed. The relay
accepts only the device bearer of the node the turn was issued to.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from api.device_workers.auth import resolve_worker_identity
from api.exec_nodes import service, store
from api.http_authz import require_chat_session_access, require_ui_operator

exec_nodes_bp = Blueprint("exec_nodes", __name__, url_prefix="/exec")

_MAX_EVENTS_PER_POST = 500


def _owner_denied():
    _user, err = require_ui_operator()
    if err:
        return err
    if not service.enabled():
        return jsonify(success=False, disabled=True,
                       error="Enable Execution nodes in Settings → Experimental."), 403
    return None


def _owned_chat(session: str):
    """Owner + their own chat → (session id, None) or (None, error)."""
    denied = _owner_denied()
    if denied:
        return None, denied
    _user, sid, err = require_chat_session_access(session)
    return (None, err) if err else (sid, None)


@exec_nodes_bp.get("/nodes")
def nodes_route():
    denied = _owner_denied()
    if denied:
        return denied
    return jsonify(success=True, nodes=service.list_nodes(str(request.args.get("agent") or "")))


@exec_nodes_bp.put("/nodes/<node_id>")
def node_prefs_route(node_id: str):
    denied = _owner_denied()
    if denied:
        return denied
    body = request.get_json(silent=True) or {}
    try:
        node = service.set_node_prefs(node_id, label=body.get("label"),
                                      default_cwd=body.get("default_cwd"), paths=body.get("paths"))
    except service.ExecNodeError as exc:
        return jsonify(success=False, error=str(exc)), 404
    return jsonify(success=True, node=node)


@exec_nodes_bp.get("/chats/<session>")
def chat_node_route(session: str):
    sid, denied = _owned_chat(session)
    if denied:
        return denied
    return jsonify(success=True, binding=service.binding(sid),
                   nodes=service.list_nodes(str(request.args.get("agent") or "")))


@exec_nodes_bp.put("/chats/<session>")
def set_chat_node_route(session: str):
    sid, denied = _owned_chat(session)
    if denied:
        return denied
    body = request.get_json(silent=True) or {}
    try:
        binding = service.set_binding(sid, body.get("node_id"), str(body.get("cwd") or ""))
    except service.ExecNodeError as exc:
        return jsonify(success=False, error=str(exc)), 400
    return jsonify(success=True, binding=binding)


# -- node relay (device bearer of the node that owns the turn) -------------

def _node_or_error():
    ok, bound, err = resolve_worker_identity(request)
    if not ok or not bound:
        return None, (jsonify(success=False, error=err or "unauthorized"), 401)
    return bound, None


@exec_nodes_bp.get("/turns/<job_id>")
def turn_payload_route(job_id: str):
    node_id, denied = _node_or_error()
    if denied:
        return denied
    payload = store.turn_payload(job_id, node_id)
    if payload is None:
        return jsonify(success=False, error="turn not found for this node"), 410
    return jsonify(success=True, turn=payload)


@exec_nodes_bp.post("/turns/<job_id>/events")
def turn_events_route(job_id: str):
    node_id, denied = _node_or_error()
    if denied:
        return denied
    body = request.get_json(silent=True) or {}
    raw = body.get("events")
    events = [e for e in raw if isinstance(e, str)] if isinstance(raw, list) else []
    result = body.get("result") if isinstance(body.get("result"), dict) else None
    cancel = store.post_events(job_id, node_id, events[:_MAX_EVENTS_PER_POST], result)
    if cancel is None:
        return jsonify(success=False, error="turn is closed"), 410
    return jsonify(success=True, cancel=cancel)

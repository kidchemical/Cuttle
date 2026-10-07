"""Chat widgets — durable composer-adjacent panels (Tasks first).

Legacy markdown/HTTP compatibility. Tasks semantics are owned by api.gizmos;
new agents use python -m api.gizmos tasks and clients use /api/gizmos/tasks.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from api.gizmos.tasks_model import (
    _norm_scope,
    _norm_edit_mode,
    _norm_description,
    _description_from_sources,
    _norm_project_path,
    _new_id,
    _ensure_item_ids,
    normalize_tasks_payload,
    count_tasks,
    tasks_fully_complete,
    resolve_tasks_widget_status,
    _find_item,
    _remove_item,
    apply_tasks_patch,
    tasks_chip,
    format_tasks_digest,
)

_WIDGET_OPEN_RE = re.compile(
    r"<cuttle_widget\b([^>]*)>([\s\S]*?)</cuttle_widget\s*>",
    re.IGNORECASE,
)
_ATTR_RE = re.compile(
    r"""(\w+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""",
    re.IGNORECASE,
)

SUPPORTED_TYPES = frozenset({"tasks"})


def _parse_attrs(attr_blob: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for m in _ATTR_RE.finditer(attr_blob or ""):
        key = (m.group(1) or "").strip().lower()
        val = m.group(2) if m.group(2) is not None else (
            m.group(3) if m.group(3) is not None else (m.group(4) or "")
        )
        if key:
            out[key] = str(val).strip()
    return out



def parse_widget_tags(text: str) -> List[Dict[str, Any]]:
    """Return list of {attrs, body, span} for each cuttle_widget tag."""
    if not text or "<cuttle_widget" not in text.lower():
        return []
    found: List[Dict[str, Any]] = []
    for m in _WIDGET_OPEN_RE.finditer(text):
        attrs = _parse_attrs(m.group(1) or "")
        body = (m.group(2) or "").strip()
        found.append(
            {
                "attrs": attrs,
                "body": body,
                "start": m.start(),
                "end": m.end(),
                "raw": m.group(0),
            }
        )
    return found


def _load_body_json(body: str) -> Dict[str, Any]:
    if not body:
        return {}
    try:
        data = json.loads(body)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def apply_widget_tags_to_store(
    text: str,
    *,
    user_id: int,
    session_id: Optional[int],
    project_path: str = "",
    db: Any = None,
) -> Tuple[str, List[Dict[str, Any]]]:
    """Upsert widgets from tags; return rewritten text + touched widget rows.

    Store operations follow tag DOCUMENT order, so a base tag followed by a
    patch tag in one message settles base-then-patch (a later base tag
    intentionally replaces). ``touched`` likewise follows document order.
    Text splices are applied from the end inward so offsets stay valid; that
    string-edit detail never reorders the store operations above.
    """
    tags = parse_widget_tags(text)
    if not tags:
        return text, []

    if db is None:
        from api.auth_db import get_auth_db

        db = get_auth_db()

    touched: List[Dict[str, Any]] = []
    splices: List[Tuple[int, int, str]] = []
    # Store operations run in document order; text splices are collected here
    # and applied from the end inward below so offsets stay valid.
    new_text = text
    for tag in tags:
        attrs = tag["attrs"]
        wtype = str(attrs.get("type") or "tasks").strip().lower()
        if wtype not in SUPPORTED_TYPES:
            continue
        wid = str(attrs.get("id") or "").strip() or _new_id(wtype)
        scope = _norm_scope(attrs.get("scope"), "session")
        edit_mode = _norm_edit_mode(
            attrs.get("edit") or attrs.get("edit_mode") or attrs.get("managed"),
            "agent",
        )
        title = str(attrs.get("title") or "").strip() or (
            "Tasks" if wtype == "tasks" else wtype.title()
        )
        op = str(attrs.get("op") or "replace").strip().lower()
        body = _load_body_json(tag["body"])
        proj = _norm_project_path(project_path or attrs.get("project_path"))

        existing = db.get_chat_widget(wid, user_id=user_id)
        status_attr = str(attrs.get("status") or "").strip().lower() or None
        if op == "patch" and existing and str(existing.get("type")) == wtype:
            payload = existing.get("payload") or {}
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except Exception:
                    payload = {}
            if wtype == "tasks":
                payload = apply_tasks_patch(payload, body)
            scope = _norm_scope(attrs.get("scope"), existing.get("scope") or scope)
            if attrs.get("edit") or attrs.get("edit_mode") or attrs.get("managed"):
                edit_mode = _norm_edit_mode(
                    attrs.get("edit") or attrs.get("edit_mode") or attrs.get("managed"),
                    existing.get("edit_mode") or "agent",
                )
            else:
                edit_mode = _norm_edit_mode(existing.get("edit_mode"), "agent")
            title = str(attrs.get("title") or existing.get("title") or title).strip()
            description = _description_from_sources(attrs, body, existing=existing)
            status = (
                resolve_tasks_widget_status(
                    payload,
                    requested=status_attr,
                    existing_status=existing.get("status"),
                )
                if wtype == "tasks"
                else (status_attr or "active")
            )
            row = db.upsert_chat_widget(
                widget_id=wid,
                user_id=user_id,
                wtype=wtype,
                title=title,
                scope=scope,
                session_id=int(session_id) if session_id and scope == "session" else (
                    existing.get("session_id")
                ),
                project_path=proj if scope == "project" else (
                    existing.get("project_path") or ""
                ),
                payload=payload,
                status=status,
                edit_mode=edit_mode,
                description=description,
            )
        else:
            if wtype == "tasks":
                payload = normalize_tasks_payload(body)
            else:
                payload = body
            description = _description_from_sources(attrs, body, existing=existing)
            status = (
                resolve_tasks_widget_status(
                    payload,
                    requested=status_attr,
                    existing_status=(existing or {}).get("status"),
                )
                if wtype == "tasks"
                else (status_attr or "active")
            )
            row = db.upsert_chat_widget(
                widget_id=wid,
                user_id=user_id,
                wtype=wtype,
                title=title,
                scope=scope,
                session_id=int(session_id) if session_id and scope == "session" else None,
                project_path=proj if scope == "project" else "",
                payload=payload,
                status=status,
                edit_mode=edit_mode,
                description=description,
            )

        if row:
            touched.append(row)
            chip = tasks_chip(
                title,
                row.get("payload") or payload,
                status=str(row.get("status") or status),
            )
            pin_note = (
                "*(archived — done)*"
                if str(row.get("status") or "") == "archived"
                else "*(pinned above composer)*"
            )
            replacement = f"\n\n> 📌 {chip} {pin_note}\n\n"
            splices.append((tag["start"], tag["end"], replacement))

    for start, end, replacement in reversed(splices):
        new_text = new_text[:start] + replacement + new_text[end:]

    # Collapse excess blank lines from replacements
    new_text = re.sub(r"\n{3,}", "\n\n", new_text).strip()
    return new_text, touched


def rewrite_assistant_text_widgets(
    text: str,
    *,
    session_id: Any,
    project_path: str = "",
    user_id: Optional[int] = None,
) -> str:
    """Entry used from assistant-save hooks. No-op when no tags or no user."""
    if not isinstance(text, str) or "<cuttle_widget" not in text.lower():
        return text
    try:
        sid = session_id
        if isinstance(sid, str):
            if sid.startswith("db_session_"):
                sid = sid[len("db_session_") :]
            sid = int(sid) if str(sid).isdigit() else None
        elif sid is not None:
            sid = int(sid)
    except Exception:
        sid = None

    uid = user_id
    if uid is None and sid is not None:
        try:
            from api.auth_db import get_auth_db

            db = get_auth_db()
            sess = db.get_chat_session_by_id(int(sid))
            if sess:
                uid = int(sess.get("user_id"))
        except Exception:
            uid = None
    if uid is None:
        return text

    try:
        rewritten, _ = apply_widget_tags_to_store(
            text,
            user_id=int(uid),
            session_id=sid,
            project_path=project_path or "",
        )
        return rewritten
    except Exception as e:
        print(f"[WIDGETS] rewrite failed: {e}", flush=True)
        return text


def register_chat_widget_routes(app) -> None:
    """Attach REST endpoints to the Flask app."""
    from flask import jsonify, request

    from api.auth_db import get_auth_db
    from api.auth_session import get_request_session_token

    def _auth_user():
        token = get_request_session_token()
        if not token:
            return None, None

        db = get_auth_db()
        user = db.verify_auth_session(token)
        return user, db

    def _session_id_arg() -> Optional[int]:
        raw = (
            request.args.get("session_id")
            or (request.get_json(silent=True) or {}).get("session_id")
            or ""
        )
        raw = str(raw).strip()
        if raw.startswith("db_session_"):
            raw = raw[len("db_session_") :]
        try:
            return int(raw)
        except Exception:
            return None

    @app.route("/api/widgets", methods=["GET"])
    def api_widgets_list():
        user, db = _auth_user()
        if not user:
            return jsonify({"success": False, "error": "auth required"}), 401
        sid = _session_id_arg()
        client_project_path = _norm_project_path(
            request.args.get("project_path")
            or (request.args.get("project") or "")
        )
        # Session row wins over the query string. The chat UI often still holds
        # the *previous* chip's project_path when loadChatSession first refreshes
        # the strip; trusting that leaked project-scoped widgets onto the new chat.
        project_path = client_project_path
        if sid:
            try:
                sess = db.get_chat_session(int(sid), user["id"])
                if sess is not None:
                    # Even empty session project wins — never keep a mismatched
                    # client path from the chat we just left.
                    project_path = _norm_project_path(sess.get("project_path"))
            except Exception:
                pass
        widgets = db.list_chat_widgets(
            user_id=user["id"],
            session_id=sid,
            project_path=project_path or None,
            status="active",
        )
        rev = 0
        for w in widgets:
            try:
                rev = max(rev, int(w.get("revision") or 0))
            except Exception:
                pass
        return jsonify(
            {
                "success": True,
                "widgets": widgets,
                "widgets_revision": rev,
            }
        )

    @app.route("/api/widgets/<widget_id>", methods=["PUT", "PATCH"])
    def api_widgets_upsert(widget_id: str):
        user, db = _auth_user()
        if not user:
            return jsonify({"success": False, "error": "auth required"}), 401
        data = request.get_json(silent=True) or {}
        wid = (widget_id or data.get("id") or "").strip() or _new_id()
        existing = db.get_chat_widget(wid, user_id=user["id"])
        wtype = str(
            data.get("type") or (existing or {}).get("type") or "tasks"
        ).strip().lower()
        if wtype not in SUPPORTED_TYPES:
            return jsonify({"success": False, "error": f"unsupported type {wtype}"}), 400

        scope = _norm_scope(
            data.get("scope"), (existing or {}).get("scope") or "session"
        )
        title = str(
            data.get("title") or (existing or {}).get("title") or "Tasks"
        ).strip()
        sid = data.get("session_id")
        if sid is None:
            sid = _session_id_arg()
        try:
            sid_i = int(sid) if sid is not None else None
        except Exception:
            sid_i = None
        proj = _norm_project_path(
            data.get("project_path") or (existing or {}).get("project_path")
        )

        status = str(data.get("status") or (existing or {}).get("status") or "active")
        if "edit_mode" in data or "edit" in data:
            edit_mode = _norm_edit_mode(
                data.get("edit_mode") if "edit_mode" in data else data.get("edit"),
                (existing or {}).get("edit_mode") or "agent",
            )
        else:
            edit_mode = _norm_edit_mode((existing or {}).get("edit_mode"), "agent")
        op = str(data.get("op") or "").strip().lower()
        payload_in = data.get("payload") if "payload" in data else data.get("items")
        if isinstance(payload_in, list):
            payload_in = {"items": payload_in}

        if request.method == "PATCH" or op == "patch":
            base = (existing or {}).get("payload") or {"items": []}
            if isinstance(base, str):
                try:
                    base = json.loads(base)
                except Exception:
                    base = {"items": []}
            patch = data.get("patch") if isinstance(data.get("patch"), dict) else data
            payload = apply_tasks_patch(base, patch)
            description = _description_from_sources(
                data, patch, existing=existing
            )
        else:
            payload = normalize_tasks_payload(payload_in or (existing or {}).get("payload"))
            description = _description_from_sources(
                data,
                payload_in if isinstance(payload_in, dict) else None,
                existing=existing,
            )

        if wtype == "tasks":
            status = resolve_tasks_widget_status(
                payload,
                requested=data.get("status"),
                existing_status=(existing or {}).get("status"),
            )

        row = db.upsert_chat_widget(
            widget_id=wid,
            user_id=user["id"],
            wtype=wtype,
            title=title,
            scope=scope,
            session_id=sid_i if scope == "session" else None,
            project_path=proj if scope == "project" else "",
            payload=payload,
            status=status,
            edit_mode=edit_mode,
            description=description,
        )
        return jsonify({"success": True, "widget": row})

    @app.route("/api/widgets/<widget_id>/items/<item_id>", methods=["PATCH"])
    def api_widgets_item_patch(widget_id: str, item_id: str):
        user, db = _auth_user()
        if not user:
            return jsonify({"success": False, "error": "auth required"}), 401
        existing = db.get_chat_widget(widget_id, user_id=user["id"])
        if not existing:
            return jsonify({"success": False, "error": "not found"}), 404
        if _norm_edit_mode(existing.get("edit_mode"), "agent") != "shared":
            return jsonify({
                "success": False,
                "error": "agent_managed",
                "message": "This Tasks list is agent-managed. Switch to Shared to edit checkboxes.",
            }), 403
        data = request.get_json(silent=True) or {}
        payload = existing.get("payload") or {"items": []}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {"items": []}
        patch: Dict[str, Any] = {}
        if "done" in data:
            if data.get("done"):
                patch["set_done"] = [item_id]
            else:
                patch["set_undone"] = [item_id]
        if "text" in data:
            patch["set_text"] = {item_id: data.get("text")}
        payload = apply_tasks_patch(payload, patch)
        status = resolve_tasks_widget_status(
            payload,
            existing_status=existing.get("status"),
        )
        row = db.upsert_chat_widget(
            widget_id=widget_id,
            user_id=user["id"],
            wtype=str(existing.get("type") or "tasks"),
            title=str(existing.get("title") or "Tasks"),
            scope=_norm_scope(existing.get("scope")),
            session_id=existing.get("session_id"),
            project_path=existing.get("project_path") or "",
            payload=payload,
            status=status,
            edit_mode=_norm_edit_mode(existing.get("edit_mode"), "agent"),
            description=_norm_description(existing.get("description")),
        )
        return jsonify({"success": True, "widget": row})

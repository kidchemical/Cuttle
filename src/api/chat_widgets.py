"""Chat widgets — durable composer-adjacent panels (Tasks first).

Agents emit ``<cuttle_widget type="tasks" …>`` in replies. Flask upserts SQLite
rows and rewrites the bubble to a short chip. Clients poll ``/api/widgets``.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

_WIDGET_OPEN_RE = re.compile(
    r"<cuttle_widget\b([^>]*)>([\s\S]*?)</cuttle_widget\s*>",
    re.IGNORECASE,
)
_ATTR_RE = re.compile(
    r"""(\w+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""",
    re.IGNORECASE,
)

SUPPORTED_TYPES = frozenset({"tasks"})


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


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


def _norm_scope(raw: Any, default: str = "session") -> str:
    s = str(raw or default).strip().lower()
    return "project" if s == "project" else "session"


def _norm_edit_mode(raw: Any, default: str = "agent") -> str:
    """agent = agent-only edits (default); shared = user may toggle checkboxes."""
    s = str(raw or default).strip().lower()
    if s in ("shared", "user", "both", "anyone"):
        return "shared"
    return "agent"


def _norm_description(raw: Any, *, max_len: int = 2000) -> str:
    """User/agent blurb for tooltip + context digest. Empty clears."""
    if raw is None:
        return ""
    s = str(raw).strip()
    if not s:
        return ""
    # Collapse runaway whitespace; keep intentional newlines as spaces for tooltip.
    s = re.sub(r"\s+", " ", s)
    if len(s) > max_len:
        s = s[: max_len - 1].rstrip() + "…"
    return s


def _description_from_sources(
    *sources: Any,
    existing: Optional[Dict[str, Any]] = None,
) -> str:
    """Prefer explicit description/summary/set_description; else keep existing."""
    for src in sources:
        if not isinstance(src, dict):
            continue
        if "description" in src:
            return _norm_description(src.get("description"))
        if "summary" in src:
            return _norm_description(src.get("summary"))
        if "set_description" in src:
            return _norm_description(src.get("set_description"))
    if existing:
        return _norm_description(existing.get("description"))
    return ""


def _norm_project_path(path: Optional[str]) -> str:
    p = (path or "").strip().replace("\\", "/")
    while p.endswith("/"):
        p = p[:-1]
    return p


def _new_id(prefix: str = "w") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _ensure_item_ids(items: Any, prefix: str = "t") -> List[Dict[str, Any]]:
    if not isinstance(items, list):
        return []
    out: List[Dict[str, Any]] = []
    for i, raw in enumerate(items):
        if isinstance(raw, str):
            out.append(
                {
                    "id": f"{prefix}{i+1}",
                    "text": raw.strip(),
                    "done": False,
                    "children": [],
                }
            )
            continue
        if not isinstance(raw, dict):
            continue
        iid = str(raw.get("id") or "").strip() or f"{prefix}{i+1}"
        children = _ensure_item_ids(raw.get("children") or [], prefix=f"{iid}.")
        out.append(
            {
                "id": iid,
                "text": str(raw.get("text") or raw.get("content") or "").strip(),
                "done": bool(raw.get("done")),
                "children": children,
            }
        )
    return out


def normalize_tasks_payload(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = {}
    if not isinstance(raw, dict):
        raw = {}
    items = raw.get("items")
    if items is None and isinstance(raw.get("tasks"), list):
        items = raw.get("tasks")
    return {"items": _ensure_item_ids(items or [])}


def count_tasks(items: List[Dict[str, Any]]) -> Tuple[int, int]:
    """Return (done, total) including nested children."""
    done = 0
    total = 0

    def walk(nodes: List[Dict[str, Any]]) -> None:
        nonlocal done, total
        for n in nodes or []:
            total += 1
            if n.get("done"):
                done += 1
            walk(n.get("children") or [])

    walk(items)
    return done, total


def tasks_fully_complete(payload: Any) -> bool:
    """True when the list has ≥1 item and every item (incl. nested) is done."""
    data = normalize_tasks_payload(payload)
    done, total = count_tasks(data.get("items") or [])
    return total > 0 and done >= total


def resolve_tasks_widget_status(
    payload: Any,
    *,
    requested: Optional[str] = None,
    existing_status: Optional[str] = None,
) -> str:
    """Pick persisted status. Auto-archive when every task is done.

    Finished agent-authored (and shared) lists leave the strip so completed
    plans do not linger. Empty lists (0/0) stay active. Explicit ``archived``
    always wins; a later undo / add-item patch reactivates when not complete.
    """
    # Prior archived does not stick once items reopen; incomplete → active.
    _ = existing_status
    req = str(requested or "").strip().lower()
    if req == "archived":
        return "archived"
    if tasks_fully_complete(payload):
        return "archived"
    return "active"


def _find_item(
    items: List[Dict[str, Any]], item_id: str
) -> Optional[Dict[str, Any]]:
    for n in items or []:
        if str(n.get("id")) == item_id:
            return n
        found = _find_item(n.get("children") or [], item_id)
        if found:
            return found
    return None


def _remove_item(items: List[Dict[str, Any]], item_id: str) -> bool:
    for i, n in enumerate(list(items or [])):
        if str(n.get("id")) == item_id:
            items.pop(i)
            return True
        if _remove_item(n.get("children") or [], item_id):
            return True
    return False


def apply_tasks_patch(
    payload: Dict[str, Any], patch: Dict[str, Any]
) -> Dict[str, Any]:
    """Apply incremental patch ops to a tasks payload."""
    data = normalize_tasks_payload(payload)
    items = data["items"]
    patch = patch if isinstance(patch, dict) else {}

    for iid in patch.get("set_done") or []:
        node = _find_item(items, str(iid))
        if node is not None:
            node["done"] = True

    for iid in patch.get("set_undone") or []:
        node = _find_item(items, str(iid))
        if node is not None:
            node["done"] = False

    set_text = patch.get("set_text")
    if isinstance(set_text, dict):
        for iid, text in set_text.items():
            node = _find_item(items, str(iid))
            if node is not None:
                node["text"] = str(text or "").strip()

    for iid in patch.get("remove") or []:
        _remove_item(items, str(iid))

    for entry in patch.get("add") or []:
        if not isinstance(entry, dict):
            continue
        parent = str(entry.get("parent") or "").strip()
        item = entry.get("item") if isinstance(entry.get("item"), dict) else entry
        new_nodes = _ensure_item_ids([item])
        if not new_nodes:
            continue
        new_node = new_nodes[0]
        if not parent:
            items.append(new_node)
            continue
        parent_node = _find_item(items, parent)
        if parent_node is None:
            items.append(new_node)
        else:
            kids = parent_node.setdefault("children", [])
            if not isinstance(kids, list):
                parent_node["children"] = [new_node]
            else:
                kids.append(new_node)

    replace_items = patch.get("items")
    if isinstance(replace_items, list):
        items = _ensure_item_ids(replace_items)

    return {"items": items}


def tasks_chip(
    title: str, payload: Dict[str, Any], *, status: str = "active"
) -> str:
    done, total = count_tasks((payload or {}).get("items") or [])
    label = (title or "Tasks").strip() or "Tasks"
    base = f"**{label}** · {done}/{total}"
    if str(status or "active").strip().lower() == "archived":
        return f"{base} · archived"
    return base


def format_tasks_digest(widgets: List[Dict[str, Any]]) -> str:
    """Compact markdown for Context Compiler injection."""
    lines: List[str] = []
    for w in widgets or []:
        if str(w.get("type") or "") != "tasks":
            continue
        if str(w.get("status") or "active") != "active":
            continue
        title = str(w.get("title") or "Tasks").strip() or "Tasks"
        wid = str(w.get("id") or "")
        scope = str(w.get("scope") or "session")
        desc = _norm_description(w.get("description"))
        payload = w.get("payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {}
        payload = normalize_tasks_payload(payload)
        done, total = count_tasks(payload.get("items") or [])
        lines.append(f"- **{title}** (`{wid}`, scope={scope}) {done}/{total}")
        if desc:
            lines.append(f"  - intent: {desc}")

        def walk(nodes: List[Dict[str, Any]], indent: int = 2) -> None:
            pad = " " * indent
            for n in nodes or []:
                mark = "x" if n.get("done") else " "
                lines.append(f"{pad}- [{mark}] `{n.get('id')}` {n.get('text') or ''}")
                walk(n.get("children") or [], indent + 2)

        walk(payload.get("items") or [])
    if not lines:
        return ""
    return (
        "### Active chat widgets (Tasks)\n"
        "These lists already exist above the composer. "
        "**Patch only** — reuse each `id` with "
        "`<cuttle_widget type=\"tasks\" id=\"…\" op=\"patch\">`. "
        "Do **not** create another Tasks widget for the same concern, "
        "do **not** emit an empty/default Tasks stub, and "
        "do **not** invent pin-chip markdown "
        "(`> 📌 … *(pinned above composer)*`). "
        "Checking off the **last** open item auto-archives the list "
        "(server-side) — no separate archive step needed. "
        "From the shell: `python -m api.widgets_cli list|get|patch`. "
        "Do not only restate plans in markdown.\n"
        "Each widget may include an `intent` / description — honor it when patching.\n"
        + "\n".join(lines)
    )


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

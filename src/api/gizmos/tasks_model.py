"""Pure Tasks gizmo model; shared by CLI, HTTP and legacy compatibility."""
from __future__ import annotations
import json
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

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
        "### Active Tasks gizmos\n"
        "Reuse these ids through `python -m api.gizmos tasks patch <id>`. "
        "Create a list at the start of multi-step planning; update it during work. "
        "Do not create another list for the same concern or emit Tasks markdown tags. "
        "Completing the last item auto-archives the list.\n"
        "Honor each list's intent.\n"
        + "\n".join(lines)
    )

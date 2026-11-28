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


# Who an item belongs to. Colour and HTML are never stored; ``label`` is text.
OWNER_KINDS = ("chat", "agent", "user", "group")
OWNER_ID_MAX = 128
OWNER_LABEL_MAX = 60
OWNER_AGENT_MAX = 40
ORIGIN_TURN_MAX = 160
# Short display label for compact views (the Studio checklist); ``text`` stays the full item.
LABEL_WORDS_MAX = 3
LABEL_CHARS_MAX = 32


def normalize_label(raw: Any, *, strict: bool = False) -> str:
    """Item ``label``: one line, at most 3 words / 32 characters; empty when absent.

    ``strict`` rejects an over-long label (new writes); otherwise it is cut to fit.
    """
    if raw is None:
        return ""
    if not isinstance(raw, str):
        if strict:
            raise ValueError("label must be a string")
        raw = str(raw)
    words = raw.split()
    if strict and (len(words) > LABEL_WORDS_MAX or len(" ".join(words)) > LABEL_CHARS_MAX):
        raise ValueError(f"label must be at most {LABEL_WORDS_MAX} words and {LABEL_CHARS_MAX} characters")
    return " ".join(words[:LABEL_WORDS_MAX])[:LABEL_CHARS_MAX].rstrip()


def normalize_owner(raw: Any, *, strict: bool = False) -> Optional[Dict[str, Any]]:
    """Canonical ``owner`` dict, or None when absent.

    ``strict`` (writes) raises ``ValueError`` on any bad shape. Lenient (reads
    of stored rows) drops a malformed owner instead of failing the whole list.
    Optional keys are omitted when empty.
    """
    if raw is None:
        return None

    def bad(message: str) -> None:
        if strict:
            raise ValueError(message)
        return None

    if not isinstance(raw, dict):
        return bad("owner must be an object")
    kind = str(raw.get("kind") or "").strip().lower()
    if kind not in OWNER_KINDS:
        return bad("owner.kind must be chat, agent, user or group")
    oid = str(raw.get("id") or "").strip()
    label = str(raw.get("label") or "").strip()
    agent = str(raw.get("agent") or "").strip().lower()
    if len(oid) > OWNER_ID_MAX:
        if strict:
            raise ValueError(f"owner.id must be at most {OWNER_ID_MAX} characters")
        oid = oid[:OWNER_ID_MAX]
    if len(label) > OWNER_LABEL_MAX:
        if strict:
            raise ValueError(f"owner.label must be at most {OWNER_LABEL_MAX} characters")
        label = label[:OWNER_LABEL_MAX].rstrip()
    if len(agent) > OWNER_AGENT_MAX:
        if strict:
            raise ValueError(f"owner.agent must be at most {OWNER_AGENT_MAX} characters")
        agent = agent[:OWNER_AGENT_MAX]
    if strict and not label:
        raise ValueError("owner.label is required")
    if kind == "chat" and not oid:
        return bad("a chat owner needs the chat handle as id")
    owner: Dict[str, Any] = {"kind": kind, "label": label}
    if oid:
        owner["id"] = oid
    if agent:
        owner["agent"] = agent
    return owner


def _norm_origin_turn(raw: Any) -> str:
    """Turn id (= query id) that created a Tasks list; empty when none."""
    return str(raw or "").strip()[:ORIGIN_TURN_MAX]


def _ensure_item_ids(items: Any, prefix: str = "t", *, strict: bool = False) -> List[Dict[str, Any]]:
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
        children = _ensure_item_ids(raw.get("children") or [], prefix=f"{iid}.", strict=strict)
        done = bool(raw.get("done"))
        raw_progress = raw.get("in_progress")
        if strict and raw_progress is not None and not isinstance(raw_progress, bool):
            raise ValueError("in_progress must be a boolean")
        in_progress = raw_progress is True
        if in_progress and done:
            if strict:
                raise ValueError(f"task {iid} is done and cannot be in progress")
            in_progress = False  # invariant repair for stored rows
        raw_hidden = raw.get("hidden")
        if strict and raw_hidden is not None and not isinstance(raw_hidden, bool):
            raise ValueError("hidden must be a boolean")
        hidden = raw_hidden is True
        node: Dict[str, Any] = {
            "id": iid,
            "text": str(raw.get("text") or raw.get("content") or "").strip(),
            "done": done,
            "children": children,
        }
        label = normalize_label(raw.get("label"), strict=strict)
        if label:
            node["label"] = label
        owner = normalize_owner(raw.get("owner"), strict=strict)
        if owner is not None:
            node["owner"] = owner
        if in_progress:
            node["in_progress"] = True
        # Opt-in only: checking done never sets or clears hidden.
        if hidden:
            node["hidden"] = True
        out.append(node)
    return out


def normalize_tasks_payload(raw: Any, *, strict: bool = False) -> Dict[str, Any]:
    """Canonical payload. ``strict`` is for new writes (see ``normalize_owner``)."""
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
    out: Dict[str, Any] = {"items": _ensure_item_ids(items or [], strict=strict)}
    origin = _norm_origin_turn(raw.get("origin_turn_id"))
    if origin:
        out["origin_turn_id"] = origin
    return out


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
    """Apply incremental patch ops to a tasks payload.

    Raises ``ValueError`` for a bad op shape or a write that would make an item
    both done and in progress. ``set_done`` clears ``in_progress``.
    """
    data = normalize_tasks_payload(payload)
    items = data["items"]
    origin = data.get("origin_turn_id", "")
    patch = patch if isinstance(patch, dict) else {}

    for iid in patch.get("set_done") or []:
        node = _find_item(items, str(iid))
        if node is not None:
            node["done"] = True
            node.pop("in_progress", None)

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

    set_label = patch.get("set_label")
    if set_label is not None and not isinstance(set_label, dict):
        raise ValueError("set_label must be an object")
    for iid, label in (set_label or {}).items():
        node = _find_item(items, str(iid))
        if node is None:
            continue
        label_n = normalize_label(label, strict=True)
        if label_n:
            node["label"] = label_n
        else:
            node.pop("label", None)

    for iid in patch.get("remove") or []:
        _remove_item(items, str(iid))

    for entry in patch.get("add") or []:
        if not isinstance(entry, dict):
            continue
        parent = str(entry.get("parent") or "").strip()
        item = entry.get("item") if isinstance(entry.get("item"), dict) else entry
        new_nodes = _ensure_item_ids([item], strict=True)
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

    set_owner = patch.get("set_owner")
    if set_owner is not None and not isinstance(set_owner, dict):
        raise ValueError("set_owner must be an object")
    for iid, owner in (set_owner or {}).items():
        node = _find_item(items, str(iid))
        if node is None:
            continue
        owner_n = normalize_owner(owner, strict=True)
        if owner_n is None:
            node.pop("owner", None)
        else:
            node["owner"] = owner_n

    for iid in patch.get("set_in_progress") or []:
        node = _find_item(items, str(iid))
        if node is None:
            continue
        if node.get("done"):
            raise ValueError(f"task {iid} is done and cannot be in progress")
        node["in_progress"] = True

    for iid in patch.get("clear_in_progress") or []:
        node = _find_item(items, str(iid))
        if node is not None:
            node.pop("in_progress", None)

    for iid in patch.get("set_hidden") or []:
        node = _find_item(items, str(iid))
        if node is not None:
            node["hidden"] = True

    for iid in patch.get("clear_hidden") or []:
        node = _find_item(items, str(iid))
        if node is not None:
            node.pop("hidden", None)

    replace_items = patch.get("items")
    if isinstance(replace_items, list):
        items = _ensure_item_ids(replace_items, strict=True)

    out: Dict[str, Any] = {"items": items}
    if origin:
        out["origin_turn_id"] = origin
    return out


def tasks_chip(
    title: str, payload: Dict[str, Any], *, status: str = "active"
) -> str:
    done, total = count_tasks((payload or {}).get("items") or [])
    label = (title or "Tasks").strip() or "Tasks"
    base = f"**{label}** · {done}/{total}"
    if str(status or "active").strip().lower() == "archived":
        return f"{base} · archived"
    return base


# Context digest budget: the envelope is a directory, not a copy of the lists.
# A chat's own (session) list shows its next open items; shared lists show the
# header only. Everything else is one ``tasks get`` away.
_DIGEST_OPEN_ITEMS = 8
_DIGEST_TEXT_CHARS = 160


def _clip(text: Any, limit: int = _DIGEST_TEXT_CHARS) -> str:
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def format_tasks_digest(widgets: List[Dict[str, Any]]) -> str:
    """Compact directory of active lists for Context Compiler injection."""
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
        lines.append(f"- **{title}** (`{wid}`, scope={scope}) {done}/{total} done")
        # Only this chat's own list carries its intent and next items; a
        # project-wide list is a title here and `tasks get <id>` away.
        if scope != "session":
            continue
        if desc:
            lines.append(f"  - intent: {_clip(desc, 240)}")
        open_items: List[Dict[str, Any]] = []

        def walk(nodes: List[Dict[str, Any]]) -> None:
            for n in nodes or []:
                if not n.get("done"):
                    open_items.append(n)
                walk(n.get("children") or [])

        walk(payload.get("items") or [])
        for n in open_items[:_DIGEST_OPEN_ITEMS]:
            lines.append(f"  - [ ] `{n.get('id')}` {_clip(n.get('text'))}")
        if len(open_items) > _DIGEST_OPEN_ITEMS:
            lines.append(f"  - … {len(open_items) - _DIGEST_OPEN_ITEMS} more open")
    if not lines:
        return ""
    return (
        "### Active Tasks gizmos\n"
        "Lists only; items: `python -m api.gizmos tasks get <id>`, update: "
        "`python -m api.gizmos tasks patch <id>`. "
        "Do not create another list for the same concern or emit Tasks markdown tags. "
        "Completing the last item auto-archives the list.\n"
        + "\n".join(lines)
    )
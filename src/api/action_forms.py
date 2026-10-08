"""
Dynamic inline action forms for Cuttle chat.

Agents emit::

    <cuttle_action_form>
    { JSON spec }
    </cuttle_action_form>

Cuttle rewrites that into a self-contained UI card. User choices run allowlisted
project ``.cuttle/actions/*`` or global ``.cuttle_global/actions/*`` recipes (Discord, shell, CLI, …) with **no LLM** and
(by default) **no chat reply** — only toast + lock on the card.
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from api.project_actions import (
    decode_inline_action_payload,
    encode_inline_action_payload,
    execute_inline_action,
    find_project_action_resolved,
    sign_action_form_spec,
    verify_action_form_spec,
)

_FORM_OPEN_RE = re.compile(
    r"<cuttle_action_form\b[^>]*>\s*(\{)",
    re.IGNORECASE,
)
_FORM_CLOSE_RE = re.compile(r"</cuttle_action_form\s*>", re.IGNORECASE)


def _decode_leading_json_object(text: str, start: int) -> Tuple[Optional[Any], int]:
    """Parse one JSON object from ``text[start:]``; return (obj, end_index)."""
    try:
        obj, end = json.JSONDecoder().raw_decode(text, start)
    except json.JSONDecodeError:
        return None, start
    return obj, end

_PENDING_TTL_SEC = 3600
_lock = threading.RLock()
_forms: Dict[str, Dict[str, Any]] = {}  # form_id -> record

_VALID_LOCK = frozenset({"form", "field", "none"})
_VALID_MODE = frozenset({"choice", "multi", "form"})
_WATCH_BUILTIN = frozenset({"__watch_resume__", "__watch_park__", "__watch_cancel__"})
_WATCH_TOASTS = {
    "__watch_resume__": "Waiting until this finishes, then I will continue in this chat.",
    "__watch_park__": "Locked — reply here when it's done.",
    "__watch_cancel__": "Job stopped.",
}

# Agents sometimes invent placeholder action ids ("none", "noop") for Q&A
# picks. Those are not allowlisted recipes — treat them as "no side effect"
# the same as omitting ``action`` entirely (toast Selected: …).
_NOOP_ACTION_SENTINELS = frozenset(
    {
        "none",
        "null",
        "noop",
        "no-op",
        "no_op",
        "n/a",
        "na",
        "false",
        "0",
        "__none__",
        "__noop__",
        "__null__",
        "__agent_reply__",
    }
)


def _coerce_action_name(raw: Any) -> Optional[str]:
    """Normalize an option/submit action; strip invented no-op placeholders."""
    if raw is None:
        return None
    name = str(raw).strip()
    if not name:
        return None
    if name.lower() in _NOOP_ACTION_SENTINELS:
        return None
    return name


def clear_forms_for_tests() -> None:
    with _lock:
        _forms.clear()


def _purge_locked(now: Optional[float] = None) -> None:
    t = now if now is not None else time.time()
    dead = [fid for fid, rec in _forms.items() if float(rec.get("expires_at") or 0) < t]
    for fid in dead:
        _forms.pop(fid, None)


def _safe_json_loads(raw: str) -> Optional[Any]:
    try:
        return json.loads(raw)
    except Exception:
        return None


def normalize_action_form_spec(
    spec: Any,
    *,
    project_path: str = "",
) -> Optional[Dict[str, Any]]:
    """Validate/normalize a form spec. Returns None if unusable."""
    if isinstance(spec, str):
        spec = _safe_json_loads(spec.strip())
    if not isinstance(spec, dict):
        return None

    mode = str(spec.get("mode") or "choice").strip().lower()
    if mode not in _VALID_MODE:
        mode = "choice"
    lock = str(spec.get("lock") or "form").strip().lower()
    if lock not in _VALID_LOCK:
        lock = "form"
    silent = spec.get("silent")
    if silent is None:
        silent = True
    silent = bool(silent)

    reusable = spec.get("reusable")
    if reusable is None:
        # One-shot by default when the whole form locks; reusable when lock=none.
        reusable = lock == "none"
    reusable = bool(reusable)

    title = str(spec.get("title") or "Choose an action").strip() or "Choose an action"
    description = str(spec.get("description") or spec.get("desc") or "").strip()
    content = spec.get("content")
    if content is not None:
        content = str(content)

    options_in = spec.get("options")
    options: List[Dict[str, Any]] = []
    if isinstance(options_in, list):
        for i, opt in enumerate(options_in):
            if not isinstance(opt, dict):
                continue
            oid = str(opt.get("id") or opt.get("value") or f"opt_{i}").strip()
            label = str(opt.get("label") or opt.get("title") or oid).strip() or oid
            action = _coerce_action_name(opt.get("action"))
            params = opt.get("params") if isinstance(opt.get("params"), dict) else {}
            params = dict(params)
            # Form-level content fills params.content when missing.
            if content and "content" not in params and action:
                params["content"] = content
            field_lock = str(opt.get("lock") or "").strip().lower()
            if field_lock not in _VALID_LOCK:
                field_lock = ""
            options.append(
                {
                    "id": oid,
                    "label": label,
                    "action": action,
                    "params": params,
                    "lock": field_lock or None,
                }
            )

    fields_in = spec.get("fields")
    fields: List[Dict[str, Any]] = []
    if isinstance(fields_in, list):
        for i, f in enumerate(fields_in):
            if not isinstance(f, dict):
                continue
            fid = str(f.get("id") or f"field_{i}").strip()
            if not fid:
                continue
            fields.append(
                {
                    "id": fid,
                    "label": str(f.get("label") or fid).strip() or fid,
                    "type": str(f.get("type") or "text").strip().lower(),
                    "required": bool(f.get("required")),
                    "help": str(f.get("help") or "").strip(),
                    "placeholder": str(f.get("placeholder") or "").strip(),
                    "value": f.get("value", f.get("default", "")),
                    "line": str(f.get("line") or f.get("body") or "").strip(),
                    "options": f.get("options") if isinstance(f.get("options"), list) else [],
                    "lock": (
                        str(f.get("lock")).strip().lower()
                        if str(f.get("lock") or "").strip().lower() in _VALID_LOCK
                        else None
                    ),
                }
            )

    submit = spec.get("submit") if isinstance(spec.get("submit"), dict) else {}
    submit_action = _coerce_action_name(
        submit.get("action") or spec.get("action")
    )
    param_map = submit.get("paramMap") or submit.get("paramsFromFields") or spec.get("paramMap")
    if not isinstance(param_map, dict):
        param_map = {}
    param_map = {str(k): str(v) for k, v in param_map.items()}
    submit_params = submit.get("params") if isinstance(submit.get("params"), dict) else {}
    submit_params = dict(submit_params)

    if mode in ("choice", "multi") and not options:
        return None
    if mode == "form" and not fields and not submit_action:
        return None

    # Whether picking an option should post a user message and resume the agent.
    # Opt-in so side-effect forms (flask.restart, watch, etc) stay silent.
    resume = bool(spec.get("resume") or spec.get("notify_agent") or spec.get("notifyAgent"))
    has_side_effect = bool(submit_action) or any(o.get("action") for o in options)
    # Q&A cards (no side effect) get a free-text "custom" answer by default so
    # the user is never locked into the listed options. Opt out per card with
    # "allow_custom": false. Side-effect / watch cards never get one.
    allow_custom_raw = spec.get("allow_custom", spec.get("allowCustom", None))
    if allow_custom_raw is None:
        allow_custom = not has_side_effect and not spec.get("watch")
    else:
        allow_custom = bool(allow_custom_raw)
    default_submit = "Run" if has_side_effect else "Submit"
    out: Dict[str, Any] = {
        "title": title,
        "description": description,
        "mode": mode,
        "lock": lock,
        "silent": silent,
        "resume": resume,
        "allow_custom": allow_custom,
        "reusable": reusable,
        "locked": bool(spec.get("locked")),
        "selected": list(spec.get("selected") or []) if isinstance(spec.get("selected"), list) else [],
        "submitLabel": str(
            spec.get("submitLabel") or spec.get("submit_label") or default_submit
        ).strip()
        or default_submit,
        "cancelLabel": str(spec.get("cancelLabel") or spec.get("cancel_label") or "Cancel").strip()
        or "Cancel",
        "busyLabel": str(spec.get("busyLabel") or "Running…").strip() or "Running…",
        "options": options,
        "fields": fields,
        "content": content,
        "submit": {
            "action": submit_action,
            "paramMap": param_map,
            "params": submit_params,
        },
        "project_path": str(project_path or spec.get("project_path") or "").strip(),
        # Chat that owns this card. Side effects follow the card, not whatever
        # session the client happens to have open when the button is clicked.
        "session_id": str(spec.get("session_id") or "").strip(),
    }
    toast = spec.get("toast")
    if toast is not None:
        out["toast"] = str(toast)
    restart_id = str(spec.get("restartId") or spec.get("restart_id") or "").strip()
    if restart_id:
        out["restartId"] = restart_id
    watch = _normalize_watch(spec.get("watch"))
    if watch:
        out["watch"] = watch
    from api.flask_restart import canonicalize_restart_form
    return canonicalize_restart_form(out)


_WATCH_SNAPSHOT_KEYS = (
    "state",
    "percent",
    "label",
    "version",
    "elapsed",
    "elapsed_sec",
    "started_at",
    "run_id",
    "action",
    "detail",
    "bars",
    "grid",
)


def sanitize_watch_snapshot(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    out: Dict[str, Any] = {}
    for key in _WATCH_SNAPSHOT_KEYS:
        if key not in raw:
            continue
        val = raw[key]
        if key == "percent":
            try:
                out[key] = max(0, min(100, int(val)))
            except (TypeError, ValueError):
                continue
        elif key == "elapsed_sec":
            try:
                out[key] = max(0, int(val))
            except (TypeError, ValueError):
                continue
        elif key == "grid":
            from api.job_watch import sanitize_grid
            grid = sanitize_grid(val)
            if grid:
                out["grid"] = grid
        elif key == "bars":
            try:
                from api.job_watch import sanitize_bars

                bars = sanitize_bars(val)
            except Exception:
                bars = None
            if bars:
                out["bars"] = bars
        else:
            out[key] = str(val) if val is not None else ""
    return out or None


def merge_watch_snapshot_into_spec(
    spec: Dict[str, Any],
    *,
    snapshot: Optional[Dict[str, Any]] = None,
    terminal: bool = False,
    toast: str = "",
    lock: bool = False,
) -> Dict[str, Any]:
    """Freeze a watch card onto one job result so later runs cannot overwrite it."""
    out = dict(spec or {})
    watch = dict(out.get("watch") or {}) if isinstance(out.get("watch"), dict) else {}
    snap = sanitize_watch_snapshot(snapshot)
    if snap:
        watch["snapshot"] = snap
        if snap.get("started_at") and not watch.get("started_at"):
            watch["started_at"] = str(snap.get("started_at") or "")
        if snap.get("action") and not watch.get("action"):
            watch["action"] = str(snap.get("action") or "")
        if snap.get("run_id") and not watch.get("run_id"):
            watch["run_id"] = str(snap.get("run_id") or "")
    watch["terminal"] = bool(terminal)
    if watch:
        out["watch"] = watch
    if toast:
        out["toast"] = str(toast)
    should_lock = bool(lock) or (
        terminal
        and not out.get("reusable")
        and str(out.get("lock") or "form").strip().lower() == "form"
    )
    if should_lock:
        out["locked"] = True
    return out


def _normalize_watch(raw: Any) -> Optional[Dict[str, Any]]:
    """Keep job-progress polling metadata. URLs must be same-origin /output or /api."""
    if not isinstance(raw, dict):
        return None
    url = str(raw.get("url") or "").strip()
    if not (url.startswith("/output/") or url.startswith("/api/")):
        return None
    interval = raw.get("interval_ms")
    try:
        interval_ms = max(1500, int(interval))
    except (TypeError, ValueError):
        interval_ms = 4000
    done_states = raw.get("done_states")
    if not isinstance(done_states, list) or not done_states:
        done_states = ["done"]
    fail_states = raw.get("fail_states")
    if not isinstance(fail_states, list) or not fail_states:
        fail_states = ["failed"]
    out: Dict[str, Any] = {
        "id": str(raw.get("id") or "job").strip() or "job",
        "url": url,
        "interval_ms": interval_ms,
        "done_states": [str(s) for s in done_states],
        "fail_states": [str(s) for s in fail_states],
        "resume_message": str(raw.get("resume_message") or "").strip(),
    }
    started = str(raw.get("started_at") or "").strip()
    if started:
        out["started_at"] = started
    action = str(raw.get("action") or "").strip()
    if action:
        out["action"] = action
    run_id = str(raw.get("run_id") or "").strip()
    if run_id:
        out["run_id"] = run_id
    snap = sanitize_watch_snapshot(raw.get("snapshot"))
    if snap:
        out["snapshot"] = snap
    if raw.get("terminal") in (True, "true", 1, "1"):
        out["terminal"] = True
    return out


def register_action_form(
    *,
    session_id: str,
    project_path: str,
    spec: Dict[str, Any],
    form_id: Optional[str] = None,
) -> str:
    fid = str(form_id or "").strip() or uuid.uuid4().hex
    now = time.time()
    with _lock:
        _purge_locked(now)
        _forms[fid] = {
            "id": fid,
            "session_id": str(session_id),
            "project_path": str(project_path or ""),
            "spec": dict(spec),
            "created_at": now,
            "expires_at": now + _PENDING_TTL_SEC,
        }
    return fid


def get_action_form(form_id: str) -> Optional[Dict[str, Any]]:
    with _lock:
        _purge_locked()
        rec = _forms.get(form_id)
        return dict(rec) if rec else None


def encode_form_fallback(spec: Dict[str, Any]) -> str:
    """Build a server-signed ``inline.…`` token from a spec (API/tests).

    Not written into stored chat HTML — pending tags keep a short ``id`` plus
    the JSON body. After Flask restart the server reloads that body from
    persisted assistant history; the client must not mint unsigned tokens.
    """
    sid = spec.get("session_id") or spec.get("session")
    return encode_inline_action_payload(
        action_name="__action_form__",
        project_path=str(spec.get("project_path") or ""),
        params={"spec": spec},
        session_id=str(sid) if sid else None,
    )


def _strip_pending_fallback_attr(attrs: str) -> str:
    """Drop legacy ``fallback="inline…"`` from pending open-tag attrs."""
    return re.sub(
        r'\s+fallback=(?:"[^"]*"|\'[^\']*\')',
        "",
        attrs or "",
        flags=re.I,
    )


def decode_form_fallback(token: str) -> Optional[Dict[str, Any]]:
    parsed = decode_inline_action_payload(token)
    if not parsed or parsed.get("action") != "__action_form__":
        return None
    params = parsed.get("params") or {}
    spec = params.get("spec")
    return normalize_action_form_spec(
        spec, project_path=str(parsed.get("project_path") or "")
    )


def _assign_restart_form_identity(spec: Dict[str, Any]) -> Optional[str]:
    """Stamp shared flask-restart-* id so cards across chats stay linked until restart."""
    try:
        from api.flask_restart import (
            is_flask_restart_controller_spec,
            shared_restart_form_id,
        )
    except Exception:
        return None
    if not is_flask_restart_controller_spec(spec):
        return None
    fid = shared_restart_form_id()
    spec["restartFormGroup"] = fid
    # Keep agent-emitted ids from fragmenting the link group.
    spec["id"] = fid
    return fid


def is_qa_resume_spec(spec: Dict[str, Any]) -> bool:
    """A question for the agent: resumes on answer and has no side effect."""
    if not isinstance(spec, dict) or not spec.get("resume"):
        return False
    if spec.get("watch") or spec.get("locked"):
        return False
    if (spec.get("submit") or {}).get("action"):
        return False
    if any(o.get("action") for o in spec.get("options") or []):
        return False
    return spec.get("mode") in ("choice", "multi", "form")


def merge_qa_resume_specs(specs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Fold several Q&A resume cards into one ``form`` card with a single Submit.

    Separate resume cards each wake the agent on their own click, so the first
    answer ends the turn and every other card in the reply is orphaned.
    """
    fields: List[Dict[str, Any]] = []
    used: set = set()

    def _unique(fid: str) -> str:
        base = fid or f"q{len(fields) + 1}"
        out, n = base, 2
        while out in used:
            out, n = f"{base}_{n}", n + 1
        used.add(out)
        return out

    for i, spec in enumerate(specs):
        mode = spec.get("mode")
        if mode in ("choice", "multi"):
            question = spec.get("title") or f"Question {i + 1}"
            fields.append(
                {
                    "id": _unique(f"q{i + 1}"),
                    "label": question,
                    "type": "checkboxes" if mode == "multi" else "radio",
                    "required": False,
                    "help": spec.get("description") or "",
                    "placeholder": "",
                    "value": [] if mode == "multi" else "",
                    "line": "",
                    "options": [
                        {"value": o.get("id"), "label": o.get("label")}
                        for o in spec.get("options") or []
                    ],
                    "lock": None,
                }
            )
        else:
            for f in spec.get("fields") or []:
                fields.append({**f, "id": _unique(str(f.get("id") or ""))})

    merged = dict(specs[0])
    merged.update(
        {
            "title": "Questions",
            "description": "",
            "mode": "form",
            "lock": "form",
            "resume": True,
            "allow_custom": all(
                s.get("allow_custom", s.get("allowCustom", True)) for s in specs
            ),
            "reusable": False,
            "submitLabel": "Submit",
            "options": [],
            "fields": fields,
            "content": None,
            "submit": {"action": None, "paramMap": {}, "params": {}},
        }
    )
    merged.pop("id", None)
    return merged


def rewrite_action_forms(
    text: str,
    *,
    session_id: str,
    project_path: str,
) -> Tuple[str, int]:
    if not text or "<cuttle_action_form" not in text.lower():
        return text, 0
    if not session_id:
        return text, 0

    # Chat project chip beats agent cwd (demo was rewritten with Cuttle\src).
    session_path = resolve_session_project_path(session_id)
    effective_path = session_path or (project_path or "")

    found: List[Tuple[int, int, Optional[Dict[str, Any]]]] = []
    for m in _FORM_OPEN_RE.finditer(text):
        brace = m.start(1)
        raw, json_end = _decode_leading_json_object(text, brace)
        if raw is None:
            continue
        close_m = _FORM_CLOSE_RE.search(text, json_end)
        if not close_m:
            continue
        spec = normalize_action_form_spec(raw, project_path=effective_path or "")
        found.append((m.start(), close_m.end(), spec))

    qa_idx = [i for i, (_, _, s) in enumerate(found) if s and is_qa_resume_spec(s)]
    drop: set = set()
    if len(qa_idx) > 1:
        merged = merge_qa_resume_specs([found[i][2] for i in qa_idx])  # type: ignore[misc]
        first = qa_idx[0]
        found[first] = (found[first][0], found[first][1], merged)
        drop = set(qa_idx[1:])

    count = 0
    out_parts: List[str] = []
    last = 0

    for i, (start, end, spec) in enumerate(found):
        out_parts.append(text[last:start])
        if i in drop:
            last = end
            continue
        if not spec:
            out_parts.append(text[start:end])
            last = end
            continue
        if effective_path:
            spec["project_path"] = effective_path
        spec["session_id"] = str(session_id)
        shared_id = _assign_restart_form_identity(spec)
        form_id = register_action_form(
            session_id=session_id,
            project_path=spec.get("project_path") or effective_path or "",
            spec=spec,
            form_id=shared_id,
        )
        spec["id"] = form_id
        spec["sig"] = sign_action_form_spec(spec)
        count += 1
        # Restart-safe without a huge fallback= attribute: body JSON is the
        # durable copy persisted in chat history (not a client-trusted spec).
        payload = json.dumps(spec, ensure_ascii=False)
        out_parts.append(
            f'<cuttle_action_form_pending id="{form_id}">\n'
            f"{payload}\n"
            f"</cuttle_action_form_pending>"
        )
        last = end

    if count == 0:
        return text, 0
    out_parts.append(text[last:])
    return "".join(out_parts), count


_PENDING_OPEN_RE = re.compile(
    r"<cuttle_action_form_pending\b([^>]*)>\s*(\{)",
    re.IGNORECASE,
)

_PENDING_BLOCK_RE = re.compile(
    r"<cuttle_action_form_pending\b([^>]*)>([\s\S]*?)</cuttle_action_form_pending>",
    re.IGNORECASE,
)


def _pending_block_id(attrs: Optional[str]) -> str:
    """Card id from a pending open-tag attribute string ("" when absent)."""
    m = re.search(r'\bid=(["\'])([^"\']+)\1', attrs or "", re.I)
    return m.group(2) if m else ""


def _namespaced_form_result_meta(
    meta: Dict[str, Any],
    form_id: str,
    entry: Dict[str, Any],
) -> Dict[str, Any]:
    """Store one card's lock result without clobbering sibling cards.

    One assistant message can hold several cards (e.g. a Q&A card plus a
    restart controller). ``action_form_result`` used to be a single slot,
    so consuming one card rewrote — and lock-reads leaked into — the other.
    Per-card entries live under ``forms``; top-level keys stay as the
    last-writer compat copy.
    """
    prev = meta.get("action_form_result")
    prev = dict(prev) if isinstance(prev, dict) else {}
    forms = prev.get("forms")
    forms = dict(forms) if isinstance(forms, dict) else {}
    forms[str(form_id)] = dict(entry)
    prev.update(entry)
    prev["forms"] = forms
    meta["action_form_result"] = prev
    return meta


def load_action_form_spec_from_history(
    session_id: Optional[str],
    form_id: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Canonical spec for ``form_id`` from this chat's persisted assistant HTML.

    Matches the pending tag ``id`` attribute, not a coincidental substring in
    another card's JSON. Client ``data-spec`` is never executed.
    """
    from api.cuttle_ui_capabilities import numeric_chat_session_id

    nid = numeric_chat_session_id(session_id)
    fid = str(form_id or "").strip()
    if not nid or not fid:
        return None
    try:
        from api.auth_db import get_auth_db

        msgs = get_auth_db().find_messages_containing(
            int(nid), fid, role="assistant", limit=20
        )
    except Exception:
        return None
    for msg in msgs or []:
        content = str(msg.get("content") or "")
        for m in _PENDING_OPEN_RE.finditer(content):
            attrs = m.group(1) or ""
            id_m = re.search(r'\bid=(["\'])([^"\']+)\1', attrs, re.I)
            if not id_m or id_m.group(2) != fid:
                continue
            obj, _ = _decode_leading_json_object(content, m.start(2))
            if not isinstance(obj, dict):
                continue
            if not verify_action_form_spec(obj):
                continue
            signed_sid = numeric_chat_session_id(obj.get("session_id"))
            if signed_sid != nid:
                continue
            spec = normalize_action_form_spec(
                obj, project_path=str(obj.get("project_path") or "")
            )
            if not spec:
                continue
            # Shared id flask-restart-gN must never execute a git.push (or other)
            # spec that merely appeared in a message containing that string.
            if _is_flask_restart_form_id(fid):
                try:
                    from api.flask_restart import is_flask_restart_controller_spec
                except Exception:
                    is_flask_restart_controller_spec = lambda _s: False  # noqa: E731
                if not is_flask_restart_controller_spec(spec):
                    continue
            spec["id"] = fid
            spec["session_id"] = f"db_session_{nid}"
            return spec
    return None


def _run_one(
    project_path: str,
    action_name: Optional[str],
    params: Dict[str, Any],
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    if not action_name:
        return {"success": True, "response": "Cancelled.", "skipped": True}
    # Reuse inline executor shape via a synthetic token.
    token = encode_inline_action_payload(
        action_name=action_name,
        project_path=project_path,
        params=params or {},
        session_id=session_id,
    )
    return execute_inline_action(token, session_id=session_id)


def _field_checked(value: Any) -> bool:
    if value is True or value is False:
        return bool(value)
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _compose_checkbox_content(
    spec: Dict[str, Any],
    fields: Dict[str, Any],
) -> Tuple[Optional[str], List[str]]:
    """Join checked checkbox/toggle field ``line`` bodies into one Discord body.

    Returns ``(content, missing)``:

    * ``content`` is None when the form has no post-line fields (leave existing
      content), ``""`` when such fields exist but none are checked.
    * ``missing`` lists checked fields with no ``line``. A label is a UI summary,
      never post text — fabricating a body from it posted the card's own bullets
      to Discord instead of the real update.
    """
    defs = [f for f in (spec.get("fields") or []) if isinstance(f, dict)]
    skip_ids = {"title", "channel", "content", "body"}

    def _is_post_line_field(f: Dict[str, Any]) -> bool:
        fid = str(f.get("id") or "")
        if fid in skip_ids:
            return False
        t = str(f.get("type") or "").lower()
        if t in ("radio", "select", "textarea", "number", "range"):
            return False
        if t in ("checkbox", "toggle"):
            return True
        # Missing type normalizes to "text"; still post if a Discord line is set.
        return bool(str(f.get("line") or "").strip())

    boxes = [f for f in defs if _is_post_line_field(f)]
    if not boxes:
        return None, []

    title = str(fields.get("title") or "").strip()
    if not title:
        for f in defs:
            if str(f.get("id") or "") == "title":
                title = str(f.get("value") or "").strip()
                break
    if not title:
        raw = str(spec.get("content") or "").strip()
        if raw:
            title = raw.split("\n", 1)[0].strip()

    lines: List[str] = []
    missing: List[str] = []
    for f in boxes:
        fid = str(f.get("id") or "")
        if fid == "title":
            continue
        if not _field_checked(fields.get(fid, f.get("value"))):
            continue
        line = str(f.get("line") or "").strip()
        if not line:
            missing.append(str(f.get("label") or "").strip() or fid)
            continue
        lines.append(line)

    if not lines:
        return "", missing
    if title:
        return title + "\n\n" + "\n".join(lines), missing
    return "\n".join(lines), missing


def qa_form_answers(spec: Dict[str, Any], fields: Dict[str, Any]) -> List[Tuple[str, str]]:
    """(question label, human answer) pairs for a Q&A form submission."""

    def _opt_labels(defn: Dict[str, Any]) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for o in defn.get("options") or []:
            if isinstance(o, dict):
                val = str(o.get("value", o.get("id", "")))
                out[val] = str(o.get("label") or o.get("name") or val)
            else:
                out[str(o)] = str(o)
        return out

    answers: List[Tuple[str, str]] = []
    for defn in spec.get("fields") or []:
        if not isinstance(defn, dict):
            continue
        fid = str(defn.get("id") or "")
        if not fid:
            continue
        question = str(defn.get("label") or fid)
        ftype = str(defn.get("type") or "text").lower()
        raw = fields.get(fid)
        labels = _opt_labels(defn)
        if ftype == "checkboxes":
            picked = raw if isinstance(raw, list) else ([raw] if raw else [])
            answer = ", ".join(labels.get(str(v), str(v)) for v in picked) or "(none)"
        elif ftype in ("checkbox", "toggle"):
            answer = "yes" if raw in (True, "true", 1, "1") else "no"
        elif ftype in ("radio", "select"):
            answer = labels.get(str(raw), str(raw)) if raw not in (None, "") else "(no answer)"
        else:
            answer = str(raw or "").strip() or "(blank)"
        answers.append((question, answer))
    return answers


def _spec_allows_custom(spec: Dict[str, Any]) -> bool:
    """Whether a spec accepts a free-text custom answer (Q&A only, opt-out)."""
    if not isinstance(spec, dict):
        return False
    if spec.get("allow_custom") is False or spec.get("allowCustom") is False:
        return False
    if spec.get("watch"):
        return False
    if isinstance(spec.get("submit"), dict) and spec["submit"].get("action"):
        return False
    for o in spec.get("options") or []:
        if isinstance(o, dict) and o.get("action"):
            return False
    return True


def build_runs_from_submission(
    spec: Dict[str, Any],
    selection: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Turn a user selection into a list of {action, params, option_id?}.

    selection shapes:
      choice: { "option": "fu" } or { "options": ["fu"] }
      multi:  { "options": ["a", "b"] }
      form:   { "fields": { "channel": "...", "body": "..." } }
      cancel: { "cancel": true }
      custom (any Q&A mode): { "custom_text": "my own words" }
    """
    if not isinstance(selection, dict):
        return []
    custom_text = str(
        selection.get("custom_text", selection.get("customText", ""))
    ).strip()
    if custom_text and _spec_allows_custom(spec):
        custom_text = custom_text[:2000]
        project_path = str(spec.get("project_path") or "")
        fields = (
            dict(selection.get("fields"))
            if isinstance(selection.get("fields"), dict)
            else {}
        )
        return [
            {
                "action": None,
                "params": {},
                "option_id": "custom",
                "label": "Custom",
                "project_path": project_path,
                "fields": fields,
                "custom_text": custom_text,
            }
        ]
    if selection.get("cancel"):
        # Older clients marked every no-action Q&A option as cancel. If the
        # click still carried a real option id, honor that pick instead of
        # collapsing to "Cancelled."
        opted = selection.get("options")
        if opted is None and selection.get("option") is not None:
            opted = [selection.get("option")]
        if not isinstance(opted, list):
            opted = []
        by_id = {
            str(o["id"]): o
            for o in (spec.get("options") or [])
            if isinstance(o, dict) and o.get("id") is not None
        }
        key = str(opted[0]) if opted else ""
        opt = by_id.get(key) if key else None
        if opt and str(opt.get("id") or "").lower() != "cancel" and not opt.get("cancel"):
            selection = {
                **selection,
                "cancel": False,
                "option": key,
                "options": [key],
            }
        else:
            return [{"action": None, "params": {}, "option_id": "cancel"}]

    mode = spec.get("mode") or "choice"
    project_path = str(spec.get("project_path") or "")
    runs: List[Dict[str, Any]] = []

    if mode in ("choice", "multi"):
        opted = selection.get("options")
        if opted is None and selection.get("option") is not None:
            opted = [selection.get("option")]
        if not isinstance(opted, list):
            opted = []
        by_id = {o["id"]: o for o in (spec.get("options") or []) if isinstance(o, dict)}
        for oid in opted:
            key = str(oid)
            opt = by_id.get(key)
            if not opt:
                continue
            params = dict(opt.get("params") or {})
            # Allow selection to override/supplement content
            if selection.get("content") and "content" not in params:
                params["content"] = selection["content"]
            runs.append(
                {
                    "action": opt.get("action"),
                    "params": params,
                    "option_id": key,
                    "label": opt.get("label"),
                    "project_path": project_path,
                }
            )
        return runs

    # form mode
    fields = selection.get("fields") if isinstance(selection.get("fields"), dict) else {}
    submit = spec.get("submit") or {}
    action = submit.get("action")
    params = dict(submit.get("params") or {})
    param_map = submit.get("paramMap") or {}
    for dest, src in param_map.items():
        if src in fields:
            params[dest] = fields[src]
    # Also copy common aliases
    if "content" not in params and fields.get("content") is not None:
        params["content"] = fields.get("content")
    if "content" not in params and fields.get("body") is not None:
        params["content"] = fields.get("body")
    if not action:
        # Q&A form: answers go back to the agent, nothing is posted, so
        # checkbox fields need no `line` bodies.
        return [
            {
                "action": None,
                "params": {},
                "option_id": "submit",
                "label": spec.get("submitLabel") or "Submit",
                "project_path": project_path,
                "fields": fields,
            }
        ]
    composed, missing_lines = _compose_checkbox_content(spec, fields)
    # A client that composed from real `line` bodies says so. Anything else
    # (old cached client, hand-rolled caller) can't be trusted to have real post
    # text for those fields, so refuse rather than post label summaries.
    trusted_client_body = (
        str(selection.get("contentSource") or "").strip().lower() == "lines"
        and bool(str(params.get("content") or "").strip())
    )
    if missing_lines and not trusted_client_body:
        return [
            {
                "action": None,
                "params": {},
                "option_id": "blocked",
                "blocked": (
                    "No post text for: "
                    + ", ".join(missing_lines)
                    + ". Ask the agent to resend the form with a `line` body for each item."
                ),
                "project_path": project_path,
            }
        ]
    if composed is not None:
        if not composed.strip():
            # Client may have already assembled fields.content (older Flask /
            # truncated spec). Keep that instead of aborting the post.
            existing = str(params.get("content") or "").strip()
            if not existing:
                return []
        else:
            params["content"] = composed
    elif spec.get("content") and "content" not in params:
        params["content"] = spec["content"]
    runs.append(
        {
            "action": action,
            "params": params,
            "option_id": "submit",
            "label": spec.get("submitLabel") or "Run",
            "project_path": project_path,
            "fields": fields,
        }
    )
    return runs


def resolve_session_project_path(session_id: Optional[str]) -> str:
    """Prefer the chat session's saved project_path (chip), not agent cwd."""
    if not session_id:
        return ""
    s = str(session_id).strip()
    if s.startswith("db_session_"):
        s = s[len("db_session_") :]
    if not s.isdigit():
        return ""
    try:
        from api.auth_db import get_auth_db

        proj = get_auth_db().get_session_project(int(s)) or {}
        path = str(proj.get("project_path") or "").strip()
        return path
    except Exception:
        return ""


def apply_project_path_to_spec(spec: Dict[str, Any], project_path: str) -> Dict[str, Any]:
    if not spec or not project_path:
        return spec
    out = dict(spec)
    out["project_path"] = project_path
    return out


def mark_action_form_consumed_in_history(
    *,
    session_id: Optional[str],
    form_id: Optional[str],
    selected: List[str],
    toast: str = "",
    spec_patch: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Persist one-shot lock: patch the assistant message HTML/JSON so refresh
    keeps the card locked.
    """
    if not session_id or not form_id:
        return False
    s = str(session_id).strip()
    if s.startswith("db_session_"):
        s = s[len("db_session_") :]
    if not s.isdigit():
        return False

    def _esc_attr(val: str) -> str:
        return (
            str(val)
            .replace("&", "&amp;")
            .replace('"', "&quot;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    try:
        from api.auth_db import get_auth_db

        db = get_auth_db()
        msgs = db.find_messages_containing(
            int(s), form_id, role="assistant", limit=5
        )
        if not msgs:
            return False
        # One bubble can hold several cards: patch the block whose tag id
        # matches this form, never blindly the first block (CH-000989: a
        # restart ack locked and painted the Q&A card sharing the bubble).
        target = None
        for msg in msgs:
            content = str(msg.get("content") or "")
            for m in _PENDING_BLOCK_RE.finditer(content):
                if _pending_block_id(m.group(1)) == str(form_id):
                    target = (msg, content, m)
                    break
            if target:
                break
        if not target:
            return False
        msg, content, match = target
        selected_csv = _esc_attr(",".join(str(x) for x in (selected or [])))

        attrs = match.group(1) or ""
        body = match.group(2) or ""
        attrs = _strip_pending_fallback_attr(attrs)
        attrs = re.sub(r'\s+locked="[^"]*"', "", attrs, flags=re.I)
        attrs = re.sub(r'\s+selected="[^"]*"', "", attrs, flags=re.I)
        attrs = attrs.rstrip() + f' locked="1" selected="{selected_csv}"'
        raw = body.strip()
        parsed = _safe_json_loads(raw)
        if isinstance(parsed, dict):
            parsed["locked"] = True
            parsed["selected"] = list(selected or [])
            parsed["reusable"] = False
            # Collapsed summary text after reload ("Posted to #feature-updates").
            if toast:
                parsed["toast"] = str(toast)
            # e.g. restartId — lets a reloaded card resume live status.
            for key, value in (spec_patch or {}).items():
                parsed[str(key)] = value
            body = "\n" + json.dumps(parsed, ensure_ascii=False) + "\n"
        patched = f"<cuttle_action_form_pending{attrs}>{body}</cuttle_action_form_pending>"
        new_content = content[: match.start()] + patched + content[match.end() :]
        meta = msg.get("metadata") if isinstance(msg.get("metadata"), dict) else {}
        meta = dict(meta or {})
        meta = _namespaced_form_result_meta(
            meta,
            str(form_id),
            {
                "form_id": form_id,
                "selected": list(selected or []),
                "toast": toast,
                "locked": True,
                **{str(k): v for k, v in (spec_patch or {}).items()},
            },
        )
        return db.update_message_content(int(msg["id"]), new_content, metadata=meta)
    except Exception as e:
        print(f"[CHAT] mark action form consumed failed: {e}", flush=True)
        return False


def patch_action_form_watch_in_history(
    *,
    session_id: Optional[str],
    form_id: Optional[str],
    snapshot: Optional[Dict[str, Any]] = None,
    terminal: bool = False,
    toast: str = "",
    lock: bool = False,
) -> bool:
    """Freeze watch progress on the stored card (does not unlock or relock)."""
    if not session_id or not form_id:
        return False
    s = str(session_id).strip()
    if s.startswith("db_session_"):
        s = s[len("db_session_") :]
    if not s.isdigit():
        return False
    snap = sanitize_watch_snapshot(snapshot)
    if not snap and not terminal and not toast:
        return False
    try:
        from api.auth_db import get_auth_db

        db = get_auth_db()
        msgs = db.find_messages_containing(
            int(s), str(form_id), role="assistant", limit=5
        )
        if not msgs:
            return False
        # Same shared-bubble rule as the consume lock: freeze only the
        # block whose tag id matches this form.
        target = None
        for msg in msgs:
            content = str(msg.get("content") or "")
            for m in _PENDING_BLOCK_RE.finditer(content):
                if _pending_block_id(m.group(1)) == str(form_id):
                    target = (msg, content, m)
                    break
            if target:
                break
        if not target:
            return False
        msg, content, match = target

        attrs = match.group(1) or ""
        body = match.group(2) or ""
        attrs = _strip_pending_fallback_attr(attrs)
        parsed = _safe_json_loads(body.strip())
        if isinstance(parsed, dict):
            parsed = merge_watch_snapshot_into_spec(
                parsed, snapshot=snap, terminal=terminal, toast=toast, lock=lock
            )
            if parsed.get("locked"):
                attrs = re.sub(r'\s+locked="[^"]*"', "", attrs, flags=re.I)
                attrs = attrs.rstrip() + ' locked="1"'
            body = "\n" + json.dumps(parsed, ensure_ascii=False) + "\n"
        patched = f"<cuttle_action_form_pending{attrs}>{body}</cuttle_action_form_pending>"
        new_content = content[: match.start()] + patched + content[match.end() :]
        meta = msg.get("metadata") if isinstance(msg.get("metadata"), dict) else {}
        meta = dict(meta or {})
        entry: Dict[str, Any] = {"form_id": str(form_id), "watch_terminal": bool(terminal)}
        if snap:
            entry["watch_snapshot"] = snap
        if toast:
            entry["toast"] = str(toast)
        if lock:
            entry["locked"] = True
        meta = _namespaced_form_result_meta(meta, str(form_id), entry)
        return db.update_message_content(int(msg["id"]), new_content, metadata=meta)
    except Exception as e:
        print(f"[CHAT] action form watch snapshot persist failed: {e}", flush=True)
        return False


def _is_flask_restart_form_id(form_id: Optional[str]) -> bool:
    return bool(re.match(r"^flask-restart-g\d+$", str(form_id or "").strip(), re.I))


def _is_soft_dismiss_toast(toast: Any) -> bool:
    return bool(re.match(r"^(ignored|cancell?ed)\b", str(toast or "").strip(), re.I))


def _is_restart_in_progress_toast(toast: Any) -> bool:
    """when-idle / restarting card is locked but Force/Status must still run."""
    t = str(toast or "").strip().lower()
    return bool(
        t.startswith("**flask restart acknowledged**")
        or t.startswith("flask restart acknowledged")
        or t.startswith("waiting for")
        or t.startswith("restarting flask")
        or t.startswith("postponed")
        or t.startswith("force restart")
    )


def read_action_form_lock_from_history(
    session_id: Optional[str],
    form_id: Optional[str],
) -> Optional[Dict[str, Any]]:
    """If this one-shot form was already used, return the persisted lock."""
    if not session_id or not form_id:
        return None
    s = str(session_id).strip()
    if s.startswith("db_session_"):
        s = s[len("db_session_") :]
    if not s.isdigit():
        return None
    fid = str(form_id).strip()
    is_restart_ctrl = _is_flask_restart_form_id(fid)
    try:
        from api.auth_db import get_auth_db

        msgs = get_auth_db().find_messages_containing(
            int(s), fid, role="assistant", limit=5
        )
    except Exception:
        return None
    for msg in msgs or []:
        meta = msg.get("metadata") if isinstance(msg.get("metadata"), dict) else {}
        res = meta.get("action_form_result") if isinstance(meta, dict) else None
        if isinstance(res, dict) and res.get("locked"):
            forms = res.get("forms")
            if isinstance(forms, dict):
                # Namespaced writer: only this card's own entry counts.
                # A sibling card's lock in the same bubble must not leak
                # across (CH-000989: restart ack bricked the Q&A card).
                entry = forms.get(fid)
                if isinstance(entry, dict) and entry.get("locked"):
                    toast = str(entry.get("toast") or "Already used — this form is locked.")
                    # Soft follow-up dismiss must not permanently kill shared restart cards.
                    if is_restart_ctrl and _is_soft_dismiss_toast(toast):
                        continue
                    return {
                        "selected": list(entry.get("selected") or []),
                        "toast": toast,
                    }
            elif res.get("form_id") in (None, fid):
                toast = str(res.get("toast") or "Already used — this form is locked.")
                # Soft follow-up dismiss must not permanently kill shared restart cards.
                if is_restart_ctrl and _is_soft_dismiss_toast(toast):
                    continue
                return {
                    "selected": list(res.get("selected") or []),
                    "toast": toast,
                }
        # Content fallback: the locked tag must be THIS card's tag, not a
        # sibling card sharing the bubble.
        content = str(msg.get("content") or "")
        for m in _PENDING_BLOCK_RE.finditer(content):
            if _pending_block_id(m.group(1)) != fid:
                continue
            if not re.search(r'\blocked\s*=\s*["\']1["\']', m.group(1) or "", re.I):
                break
            toast = "Already used — this form is locked."
            try:
                parsed = _safe_json_loads((m.group(2) or "").strip())
                if isinstance(parsed, dict) and parsed.get("toast"):
                    toast = str(parsed.get("toast"))
                selected = list(parsed.get("selected") or []) if isinstance(parsed, dict) else []
            except Exception:
                selected = []
            if is_restart_ctrl and _is_soft_dismiss_toast(toast):
                break
            return {
                "selected": selected,
                "toast": toast,
            }
    return None


def _execute_watch_builtin(
    spec: Dict[str, Any],
    run: Dict[str, Any],
    *,
    session_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Continue / I'll reply / Stop — no project action YAML; persist lock only."""
    action = str(run.get("action") or "")
    if action not in _WATCH_BUILTIN:
        return None
    toast = _WATCH_TOASTS[action]
    selected = [str(run.get("option_id") or "")]
    ok = True
    if action == "__watch_cancel__":
        job_id = str(((spec.get("watch") or {}) if isinstance(spec.get("watch"), dict) else {}).get("id") or "").strip()
        if not job_id:
            ok = False
            toast = "Stop failed."
        else:
            try:
                from api.job_watch import cancel_job

                info = cancel_job(job_id) or {}
                ok = bool(info.get("ok"))
                if not ok:
                    toast = str(info.get("error") or "Stop failed.")
            except Exception as e:
                ok = False
                toast = f"Stop failed: {e}"
            if session_id and job_id:
                try:
                    from api.chat_run_registry import clear_session_jobs

                    clear_session_jobs(session_id, job_id)
                except Exception:
                    pass
    return {
        "success": ok,
        "toast": toast,
        "selected": selected,
        "results": [
            {
                "success": ok,
                "action": action,
                "option_id": run.get("option_id"),
                "label": run.get("label"),
            }
        ],
        "actions": [action],
        "response": "",
    }


def execute_action_form_submission(
    *,
    form_token: str,
    selection: Dict[str, Any],
    session_id: Optional[str] = None,
    project_path_override: Optional[str] = None,
    form_id_hint: Optional[str] = None,
    owner_user_id: Optional[int] = None,
    spec_override: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Run a form submission. ``form_token`` is a pending form id or signed
    ``inline.…`` token. After Flask restart the in-memory id is gone; recover
    the canonical spec from persisted assistant history for this chat.
    ``spec_override`` is a locator only (form id) — never executed.
    """
    token = (form_token or "").strip()
    # Allow "pendingId inline.…" — prefer HMAC-valid inline, else the pending id.
    parts = token.split()
    if len(parts) >= 2:
        inline = next((p for p in parts if p.lower().startswith("inline.")), None)
        pending = next(
            (p for p in parts if not p.lower().startswith("inline.")),
            parts[0],
        )
        token = inline if (inline and decode_form_fallback(inline)) else pending
    spec: Optional[Dict[str, Any]] = None
    form_id = None
    owner_session: str = ""
    recovered_from_history = False

    if token.lower().startswith("inline."):
        spec = decode_form_fallback(token)
        if spec:
            form_id = str(spec.get("id") or "").strip() or None
    elif token:
        rec = get_action_form(token)
        if rec:
            owner_session = str(rec.get("session_id") or "")
            spec = normalize_action_form_spec(
                rec.get("spec"),
                project_path=str(rec.get("project_path") or ""),
            )
            form_id = str(rec.get("id") or token)
            # In-memory slot flask-restart-gN is global. Refuse a non-restart
            # spec (e.g. git.push Status) so a later card cannot steal the click.
            if spec and _is_flask_restart_form_id(form_id):
                try:
                    from api.flask_restart import is_flask_restart_controller_spec
                except Exception:
                    is_flask_restart_controller_spec = lambda _s: False  # noqa: E731
                if not is_flask_restart_controller_spec(spec):
                    spec = None
                    owner_session = ""
                    form_id = str(form_id_hint or token or "").strip() or form_id

    locator = (
        (str(form_id_hint).strip() if form_id_hint else "")
        or form_id
        or (
            str(spec_override.get("id") or "").strip()
            if isinstance(spec_override, dict)
            else ""
        )
        or (token if token and not token.lower().startswith("inline.") else "")
    )
    if spec is None and locator:
        spec = load_action_form_spec_from_history(session_id, locator)
        if spec:
            form_id = locator
            owner_session = str(spec.get("session_id") or "")
            recovered_from_history = True

    if not spec:
        return {
            "success": False,
            "silent": True,
            "toast": "That form expired — ask the agent to show it again.",
            "type": "action_form",
            "form_id": form_id,
        }

    # The card's own chat wins over the caller's "current" session: a stale
    # client global (pane switch, deep link, auth refresh) used to send the
    # restart ack — and the user — into an unrelated chat.
    owner_session = owner_session or str(spec.get("session_id") or "")
    session_id = owner_session or session_id
    if owner_user_id is not None and session_id:
        from api.auth_db import get_auth_db
        from api.cuttle_ui_capabilities import numeric_chat_session_id

        nid = numeric_chat_session_id(session_id)
        if not nid or not get_auth_db().get_chat_session(nid, int(owner_user_id)):
            return {
                "success": False,
                "silent": True,
                "toast": "Session not found or access denied",
                "type": "action_form",
                "form_id": form_id,
            }
    form_id = form_id or (str(form_id_hint).strip() if form_id_hint else "") or None

    # Chat chip, then the canonical spec path. Never trust a client
    # project_path override once we have a server-side spec (memory/HMAC/history).
    session_path = resolve_session_project_path(session_id)
    canonical_path = session_path or str(spec.get("project_path") or "").strip()
    if not canonical_path and not (owner_session or recovered_from_history):
        canonical_path = (project_path_override or "").strip()
    if canonical_path:
        spec = apply_project_path_to_spec(spec, canonical_path)
        spec = normalize_action_form_spec(spec, project_path=canonical_path) or spec

    consumed = None
    if not spec.get("reusable"):
        consumed = read_action_form_lock_from_history(session_id, form_id)
    # Soft follow-up "Ignored" locks on shared restart controllers are not real uses.
    try:
        from api.flask_restart import is_flask_restart_controller_spec as _is_restart_spec
    except Exception:
        _is_restart_spec = lambda _s: False  # noqa: E731
    restart_ctrl = _is_flask_restart_form_id(form_id) or bool(_is_restart_spec(spec))
    soft_locked = bool(spec.get("locked")) and restart_ctrl and _is_soft_dismiss_toast(
        spec.get("toast")
    )
    wait_toast = (consumed or {}).get("toast") if isinstance(consumed, dict) else None
    wait_toast = wait_toast or spec.get("toast")
    wait_followup = restart_ctrl and _is_restart_in_progress_toast(wait_toast)
    effectively_locked = bool(spec.get("locked")) and not soft_locked and not wait_followup
    # Already consumed one-shot form (inline token still has the original spec).
    # Shared restart controllers stay clickable while waiting_for_idle so Force
    # can supersede (CH-000545-10 already_locked).
    if (effectively_locked or (consumed and not wait_followup)) and not spec.get("reusable"):
        return {
            "success": False,
            "silent": True,
            "toast": (consumed or {}).get("toast")
            or spec.get("toast")
            or "Already used — this form is locked.",
            "type": "action_form",
            "form_id": form_id,
            "lock": spec.get("lock") or "form",
            "selected": list(
                (consumed or {}).get("selected")
                or spec.get("selected")
                or []
            ),
            "already_locked": True,
        }

    silent = bool(spec.get("silent", True))
    lock = spec.get("lock") or "form"
    reusable = bool(spec.get("reusable"))
    runs = build_runs_from_submission(spec, selection or {})
    if not runs:
        return {
            "success": False,
            "silent": silent,
            "toast": "Nothing selected.",
            "type": "action_form",
            "form_id": form_id,
            "lock": lock,
            "reusable": reusable,
        }

    # Malformed form (checked items with no post body) — never post a guess.
    blocked = next((str(r.get("blocked")) for r in runs if r.get("blocked")), "")
    if blocked:
        return {
            "success": False,
            "silent": silent,
            "toast": blocked,
            "type": "action_form",
            "form_id": form_id,
            "lock": lock,
            "reusable": reusable,
            "blocked": True,
        }

    if len(runs) == 1:
        watch_out = _execute_watch_builtin(spec, runs[0], session_id=session_id)
        if watch_out is not None:
            watch_out.update(
                {
                    "silent": silent,
                    "lock": lock,
                    "reusable": reusable,
                    "form_id": form_id,
                    "type": "action_form",
                    "session_id": session_id or None,
                    "project_path": str(spec.get("project_path") or ""),
                }
            )
            return watch_out

    # Pure cancel / informational choice (action=null). Preserve the
    # selected option id so a Q&A form ("chill", "focus", …) doesn't
    # collapse to "cancel" and show "Cancelled." — only an explicit
    # cancel option id (normalized in build_runs_from_submission) should
    # do that. Real side-effect cards (flask.restart, discord.post, …)
    # have an action and never hit this branch, so they are unaffected.
    if runs and not any(r.get("action") for r in runs):
        answer_text = ""
        first_id = str(runs[0].get("option_id") or "cancel")
        if len(runs) == 1 and first_id == "custom":
            custom = str(runs[0].get("custom_text") or "").strip()[:2000]
            selected = ["custom"]
            label = custom
            toast = "Sent custom reply."
            answer_text = custom
            extra = runs[0].get("fields")
            if isinstance(extra, dict) and extra:
                answers = qa_form_answers(spec, extra)
                real = [(q, a) for q, a in answers if a not in ("(no answer)", "(none)", "(blank)")]
                if real:
                    answer_text += "\n[form-answers]\n" + "\n".join(f"- {q}: {a}" for q, a in real)
        elif len(runs) == 1 and first_id.lower() == "cancel":
            toast = "Cancelled."
            selected = ["cancel"]
            label = first_id
        elif len(runs) == 1 and first_id == "submit" and isinstance(runs[0].get("fields"), dict):
            answers = qa_form_answers(spec, runs[0]["fields"])
            label = "; ".join(f"{q}: {a}" for q, a in answers) or "Submitted"
            toast = f"Submitted — {label}" if answers else "Submitted."
            selected = ["submit"]
            answer_text = "[form-answers]\n" + "\n".join(f"- {q}: {a}" for q, a in answers)
        else:
            labels = [str(r.get("label") or r.get("option_id") or "") for r in runs]
            selected = [str(r.get("option_id") or "") for r in runs]
            label = ", ".join(labels)
            toast = f"Selected: {label}"
            if len(runs) > 1:
                answer_text = "[form-selection] " + ", ".join(
                    f"{lab} ({oid})" if lab != oid else oid
                    for lab, oid in zip(labels, selected)
                )
        # A cancel is a dismissal, not an answer — never wake the agent for it.
        should_resume = bool(spec.get("resume")) and selected != ["cancel"]
        out = {
            "success": True,
            "silent": silent,
            "toast": toast[:300],
            "type": "action_form",
            "form_id": form_id,
            "lock": lock,
            "reusable": reusable,
            "selected": selected,
            "selected_label": label,
            "resume": should_resume,
            "response": "" if silent else toast,
            "session_id": session_id or None,
        }
        if answer_text:
            out["answer_text"] = answer_text
        return out

    results = []
    restart_result = None
    all_ok = True
    toasts = []
    project_path = str(spec.get("project_path") or "")
    for run in runs:
        action = run.get("action")
        if not action:
            continue
        run_project = str(run.get("project_path") or project_path or "")
        run_params = dict(run.get("params") or {})
        channel_hint = str(run_params.get("channel") or run_params.get("channel_id") or "").strip() or None
        if action == "flask.restart" and owner_user_id is not None:
            # Recovery controls must not depend on mutable project recipe
            # discovery. HTTP ingress supplied the authenticated account id;
            # require the same owner role as /api/flask/restart before effects.
            from api.auth_db import get_auth_db
            from api.http_authz import is_owner_user
            from api.flask_restart import request_restart

            user = get_auth_db().get_user_by_id(int(owner_user_id))
            if not is_owner_user(user):
                res = {"success": False, "response": "Owner privileges required."}
            else:
                restart = request_restart(
                    mode=str(run_params.get("mode") or "graceful").strip().lower(),
                    session_id=session_id,
                    user_source="flask.restart_action",
                    force_confirm=str(run_params.get("mode") or "").strip().lower() == "force",
                    chat_notify=False,
                )
                res = {**restart, "response": restart.get("response") or restart.get("error") or restart.get("state") or "Restart requested."}
                if restart.get('success') and restart.get('restart_id') and run_params.get('mode') != 'status':
                    restart_result = {key: restart.get(key) for key in ('restart_id', 'state', 'mode', 'active_work')}
                    restart_result['mode'] = run_params.get('mode') or 'graceful'
        else:
            action_obj, resolved_path = find_project_action_resolved(
                run_project, str(action), channel=channel_hint
            )
            if not action_obj:
                all_ok = False
                results.append(
                    {
                        "success": False,
                        "action": action,
                        "error": (
                            f"Unknown action `{action}` for project path "
                            f"`{run_project or '(empty)'}`. "
                            "Set the chat project chip to the project that owns "
                            "`.cuttle/actions/` (or add it to global `.cuttle_global/actions/`)."
                        ),
                    }
                )
                toasts.append(f"Unknown action `{action}`")
                continue
            res = _run_one(
                resolved_path or run_project,
                str(action),
                run_params,
                session_id=session_id,
            )
        ok = bool(res.get("success"))
        all_ok = all_ok and ok
        results.append(
            {
                "success": ok,
                "action": action,
                "params": dict(run.get("params") or {}),
                "option_id": run.get("option_id"),
                "label": run.get("label"),
                "response": res.get("response"),
                "url": res.get("url"),
                "error": None if ok else (res.get("response") or "failed"),
            }
        )
        if ok:
            label = run.get("label") or action
            toasts.append(str(res.get("response") or f"Ran {label}"))
        else:
            toasts.append(str(res.get("response") or f"Failed: {action}"))

    selected = [str(r.get("option_id") or "") for r in runs if r.get("option_id")]
    toast = " · ".join(t for t in toasts if t) or ("Done." if all_ok else "Failed.")
    if all_ok and len(results) == 1 and results[0].get("url"):
        toast = results[0].get("response") or toast

    out = {
        "success": all_ok,
        "silent": silent,
        "toast": toast,
        "type": "action_form",
        "form_id": form_id,
        "lock": lock,
        "reusable": reusable,
        "selected": selected,
        "results": results,
        "response": "" if silent else toast,
        "project_path": project_path,
        "session_id": session_id or None,
        "actions": [str(r.get("action") or "") for r in runs if r.get("action")],
    }
    if restart_result:
        out['flask_restart'] = restart_result
    return out


def parse_action_form_chat_message(message: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    """
    Parse ``[action-form:run] <token> <json>`` or ``[action-form:cancel] <token>``.
    Returns (kind, payload) or None.
    """
    stripped = (message or "").strip()
    m = re.match(
        r"^\[action-form:(run|cancel)\]\s+(\S+)(?:\s+([\s\S]+))?$",
        stripped,
        flags=re.I,
    )
    if not m:
        return None
    kind = m.group(1).lower()
    token = m.group(2).strip()
    rest = (m.group(3) or "").strip()
    selection: Dict[str, Any] = {"cancel": True} if kind == "cancel" else {}
    if rest:
        parsed = _safe_json_loads(rest)
        if isinstance(parsed, dict):
            selection = parsed
            if kind == "cancel":
                selection["cancel"] = True
    return kind, {"token": token, "selection": selection}


def handle_action_form_chat_message(
    message: str,
    *,
    session_id: Optional[str],
) -> Optional[Dict[str, Any]]:
    parsed = parse_action_form_chat_message(message)
    if not parsed:
        return None
    _kind, payload = parsed
    return execute_action_form_submission(
        form_token=str(payload.get("token") or ""),
        selection=dict(payload.get("selection") or {}),
        session_id=session_id,
    )

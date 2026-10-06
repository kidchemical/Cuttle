"""Tests for chat widgets (Tasks) store + tag rewrite."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from api.chat_widgets import (
    apply_tasks_patch,
    apply_widget_tags_to_store,
    format_tasks_digest,
    normalize_tasks_payload,
    parse_widget_tags,
    resolve_tasks_widget_status,
    tasks_fully_complete,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
CHAT_JS = REPO_ROOT / "src" / "web" / "js" / "chat/chat_page.js"
WIDGETS_JS = REPO_ROOT / "src" / "web" / "js" / "chat/chat_widgets.js"

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)


def _extract_function(src: str, name: str) -> str:
    """Slice one top-level ``function name(...) {...}`` by brace matching."""
    start = None
    for prefix in (f"    async function {name}(", f"    function {name}("):
        idx = src.find(prefix)
        if idx >= 0:
            start = idx
            break
    if start is None:
        raise AssertionError(f"function {name} not found")
    brace = src.index("{", start)
    depth = 0
    for i in range(brace, len(src)):
        ch = src[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return src[start : i + 1]
    raise AssertionError(f"unbalanced braces in {name}")


def test_parse_widget_tags():
    text = (
        'Preface\n'
        '<cuttle_widget type="tasks" id="demo" scope="session" title="Demo">\n'
        '{"items":[{"id":"1","text":"A","done":false}]}\n'
        '</cuttle_widget>\n'
        'Tail'
    )
    tags = parse_widget_tags(text)
    assert len(tags) == 1
    assert tags[0]["attrs"]["id"] == "demo"
    assert tags[0]["attrs"]["type"] == "tasks"


def test_apply_tasks_patch_set_done_and_add():
    base = normalize_tasks_payload(
        {"items": [{"id": "1", "text": "A", "done": False, "children": []}]}
    )
    out = apply_tasks_patch(
        base,
        {
            "set_done": ["1"],
            "add": [{"parent": "1", "item": {"id": "1a", "text": "child", "done": False}}],
        },
    )
    assert out["items"][0]["done"] is True
    assert out["items"][0]["children"][0]["id"] == "1a"


def test_tasks_fully_complete_and_resolve_status():
    incomplete = {"items": [{"id": "1", "text": "A", "done": True}, {"id": "2", "text": "B", "done": False}]}
    complete = {"items": [{"id": "1", "text": "A", "done": True}, {"id": "2", "text": "B", "done": True}]}
    nested = {
        "items": [
            {
                "id": "1",
                "text": "A",
                "done": True,
                "children": [{"id": "1a", "text": "a", "done": False}],
            }
        ]
    }
    assert tasks_fully_complete(incomplete) is False
    assert tasks_fully_complete(complete) is True
    assert tasks_fully_complete(nested) is False
    assert tasks_fully_complete({"items": []}) is False
    assert resolve_tasks_widget_status(complete) == "archived"
    assert resolve_tasks_widget_status(incomplete) == "active"
    assert resolve_tasks_widget_status(incomplete, requested="archived") == "archived"
    # Incomplete payload reactivates even if prior row was archived.
    assert resolve_tasks_widget_status(
        incomplete, existing_status="archived"
    ) == "active"


def test_apply_widget_tags_upserts(tmp_path, monkeypatch):
    from api import auth_db as auth_db_mod

    db_path = tmp_path / "widgets_test.db"
    monkeypatch.setattr(auth_db_mod, "DB_PATH", db_path)
    auth_db_mod._db_instance = None
    db = auth_db_mod.AuthDatabase(db_path)
    auth_db_mod._db_instance = db
    uid = db.create_user("widgets-test@example.com", "Widgets", "local", password="x")
    assert uid
    sess_id = db.create_chat_session(uid, "Widget chat")
    assert sess_id

    text = (
        '<cuttle_widget type="tasks" id="w1" scope="session" title="Tasks" edit="agent">\n'
        + json.dumps(
            {
                "items": [
                    {"id": "1", "text": "Backend", "done": False},
                    {"id": "2", "text": "Frontend", "done": False},
                ]
            }
        )
        + "\n</cuttle_widget>"
    )
    rewritten, touched = apply_widget_tags_to_store(
        text, user_id=uid, session_id=sess_id, project_path="", db=db
    )
    assert "pinned above composer" in rewritten
    assert "*(pinned above composer)*" in rewritten
    assert "_(pinned above composer)_" not in rewritten
    assert "<cuttle_widget" not in rewritten.lower()
    assert len(touched) == 1
    row = db.get_chat_widget("w1", user_id=uid)
    assert row is not None
    assert row["title"] == "Tasks"
    assert (row.get("edit_mode") or "agent") == "agent"
    assert len(row["payload"]["items"]) == 2

    patch_text = (
        '<cuttle_widget type="tasks" id="w1" op="patch">\n'
        '{"set_done":["1"]}\n'
        "</cuttle_widget>"
    )
    _, touched2 = apply_widget_tags_to_store(
        patch_text, user_id=uid, session_id=sess_id, project_path="", db=db
    )
    assert touched2
    row2 = db.get_chat_widget("w1", user_id=uid)
    assert row2["payload"]["items"][0]["done"] is True
    assert row2["revision"] >= 2

    digest = format_tasks_digest([row2])
    assert "Tasks" in digest
    assert "`1`" in digest
    assert "Patch only" in digest
    assert "Do **not** create another Tasks" in digest
    assert "pinned above composer" in digest
    assert "auto-archives" in digest

    # Last open item → auto-archive; strip chip notes archived.
    finish = (
        '<cuttle_widget type="tasks" id="w1" op="patch">\n'
        '{"set_done":["2"]}\n'
        "</cuttle_widget>"
    )
    rewritten_fin, _ = apply_widget_tags_to_store(
        finish, user_id=uid, session_id=sess_id, project_path="", db=db
    )
    row3 = db.get_chat_widget("w1", user_id=uid)
    assert row3["status"] == "archived"
    assert row3["payload"]["items"][1]["done"] is True
    assert "archived" in rewritten_fin
    assert format_tasks_digest([row3]) == ""

    # Undo one item → reactivate.
    reopen = (
        '<cuttle_widget type="tasks" id="w1" op="patch">\n'
        '{"set_undone":["2"]}\n'
        "</cuttle_widget>"
    )
    apply_widget_tags_to_store(
        reopen, user_id=uid, session_id=sess_id, project_path="", db=db
    )
    row4 = db.get_chat_widget("w1", user_id=uid)
    assert row4["status"] == "active"
    assert row4["payload"]["items"][1]["done"] is False
    assert "w1" in format_tasks_digest([row4])


def test_widget_description_attr_body_patch_and_digest(tmp_path, monkeypatch):
    from api import auth_db as auth_db_mod

    db_path = tmp_path / "widgets_desc.db"
    monkeypatch.setattr(auth_db_mod, "DB_PATH", db_path)
    auth_db_mod._db_instance = None
    db = auth_db_mod.AuthDatabase(db_path)
    auth_db_mod._db_instance = db
    uid = db.create_user("widgets-desc@example.com", "WidgetsDesc", "local", password="x")
    sess_id = db.create_chat_session(uid, "Desc chat")

    text = (
        '<cuttle_widget type="tasks" id="cuttle-md" title="MD polish" '
        'description="Track markdown renderer gaps in chat.">\n'
        '{"items":[{"id":"1","text":"Local path links","done":false}]}\n'
        "</cuttle_widget>"
    )
    _, touched = apply_widget_tags_to_store(
        text, user_id=uid, session_id=sess_id, project_path="", db=db
    )
    assert touched
    row = db.get_chat_widget("cuttle-md", user_id=uid)
    assert row["description"] == "Track markdown renderer gaps in chat."

    # Body description wins on replace when attr omitted; patch preserves when omitted.
    body_replace = (
        '<cuttle_widget type="tasks" id="cuttle-md" title="MD polish">\n'
        + json.dumps(
            {
                "description": "Prefer body JSON for longer intent blurbs.",
                "items": [{"id": "1", "text": "Local path links", "done": False}],
            }
        )
        + "\n</cuttle_widget>"
    )
    apply_widget_tags_to_store(
        body_replace, user_id=uid, session_id=sess_id, project_path="", db=db
    )
    row = db.get_chat_widget("cuttle-md", user_id=uid)
    assert row["description"] == "Prefer body JSON for longer intent blurbs."

    patch_only_done = (
        '<cuttle_widget type="tasks" id="cuttle-md" op="patch">\n'
        '{"set_done":["1"]}\n'
        "</cuttle_widget>"
    )
    rewritten_done, _ = apply_widget_tags_to_store(
        patch_only_done, user_id=uid, session_id=sess_id, project_path="", db=db
    )
    row = db.get_chat_widget("cuttle-md", user_id=uid)
    assert row["description"] == "Prefer body JSON for longer intent blurbs."
    assert row["payload"]["items"][0]["done"] is True
    assert row["status"] == "archived"
    assert "archived" in rewritten_done
    # Fully complete → out of active digest.
    assert format_tasks_digest([row]) == ""

    # Description patch on an archived-complete list keeps it archived.
    patch_desc = (
        '<cuttle_widget type="tasks" id="cuttle-md" op="patch">\n'
        '{"description":"Now focused on strikethrough."}\n'
        "</cuttle_widget>"
    )
    apply_widget_tags_to_store(
        patch_desc, user_id=uid, session_id=sess_id, project_path="", db=db
    )
    row = db.get_chat_widget("cuttle-md", user_id=uid)
    assert row["description"] == "Now focused on strikethrough."
    assert row["status"] == "archived"


def test_api_widgets_list_requires_auth_not_500():
    """Regression: missing get_request_session_token import used to 500 the strip."""
    from flask import Flask

    from api.chat_widgets import register_chat_widget_routes

    app = Flask(__name__)
    register_chat_widget_routes(app)
    client = app.test_client()
    r = client.get("/api/widgets?session_id=1")
    assert r.status_code == 401
    data = r.get_json()
    assert data and data.get("success") is False


def test_api_widgets_list_returns_session_widget(tmp_path, monkeypatch):
    from flask import Flask

    from api import auth_db as auth_db_mod
    from api.chat_widgets import register_chat_widget_routes

    db_path = tmp_path / "widgets_api.db"
    monkeypatch.setattr(auth_db_mod, "DB_PATH", db_path)
    auth_db_mod._db_instance = None
    db = auth_db_mod.AuthDatabase(db_path)
    auth_db_mod._db_instance = db
    uid = db.create_user("widgets-api@example.com", "WidgetsAPI", "local", password="x")
    sess_id = db.create_chat_session(uid, "Widget API chat")
    db.upsert_chat_widget(
        widget_id="api-demo",
        user_id=uid,
        wtype="tasks",
        title="To-do",
        scope="session",
        session_id=sess_id,
        project_path="",
        payload={"items": [{"id": "1", "text": "Ship it", "done": False, "children": []}]},
        status="active",
        edit_mode="shared",
    )
    token = db.create_auth_session(uid)

    app = Flask(__name__)
    register_chat_widget_routes(app)
    client = app.test_client()
    r = client.get(
        f"/api/widgets?session_id={sess_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    data = r.get_json()
    assert data["success"] is True
    assert any(w.get("id") == "api-demo" for w in data["widgets"])
    assert data["widgets_revision"] >= 1


def test_widgets_do_not_leak_across_sessions_or_projects(tmp_path, monkeypatch):
    """Blender-chat todos must not appear on a different project's new chat (API)."""
    from flask import Flask

    from api import auth_db as auth_db_mod
    from api.chat_widgets import register_chat_widget_routes

    db_path = tmp_path / "widgets_isolation.db"
    monkeypatch.setattr(auth_db_mod, "DB_PATH", db_path)
    auth_db_mod._db_instance = None
    db = auth_db_mod.AuthDatabase(db_path)
    auth_db_mod._db_instance = db
    uid = db.create_user("widgets-iso@example.com", "WidgetsIso", "local", password="x")

    blender = r"E:\Game Dev\Blender-Project"
    cuttle = r"C:\Projects\Cuttle"
    blender_sid = db.create_chat_session(uid, "Blender chat")
    db.set_session_project(
        blender_sid, uid, project_id=1, project_name="Blender", project_path=blender
    )
    new_sid = db.create_chat_session(uid, "Fresh chat")
    db.set_session_project(
        new_sid, uid, project_id=2, project_name="Cuttle", project_path=cuttle
    )

    db.upsert_chat_widget(
        widget_id="blender-session-todo",
        user_id=uid,
        wtype="tasks",
        title="Blender session To-do",
        scope="session",
        session_id=blender_sid,
        project_path="",
        payload={
            "items": [{"id": "1", "text": "Rig the mesh", "done": False, "children": []}]
        },
        status="active",
        edit_mode="agent",
    )
    db.upsert_chat_widget(
        widget_id="blender-project-todo",
        user_id=uid,
        wtype="tasks",
        title="Blender project To-do",
        scope="project",
        session_id=None,
        project_path=blender,
        payload={
            "items": [{"id": "1", "text": "Export GLB", "done": False, "children": []}]
        },
        status="active",
        edit_mode="agent",
    )

    token = db.create_auth_session(uid)
    app = Flask(__name__)
    register_chat_widget_routes(app)
    client = app.test_client()

    # New chat with a different project must not see either blender widget.
    r = client.get(
        f"/api/widgets?session_id={new_sid}&project_path={cuttle}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    data = r.get_json()
    ids = {w.get("id") for w in data["widgets"]}
    assert "blender-session-todo" not in ids
    assert "blender-project-todo" not in ids


def test_widgets_ignore_stale_client_project_path(tmp_path, monkeypatch):
    """Stale project_path from a prior chat chip must not leak project widgets.

    Reproduces: open blender chat (project-scoped To-do) → new chat on another
    project → client still passes blender ``project_path`` on ``/api/widgets``.
    List must follow the session row's project, not the mismatched query string.
    """
    from flask import Flask

    from api import auth_db as auth_db_mod
    from api.chat_widgets import register_chat_widget_routes

    db_path = tmp_path / "widgets_stale_proj.db"
    monkeypatch.setattr(auth_db_mod, "DB_PATH", db_path)
    auth_db_mod._db_instance = None
    db = auth_db_mod.AuthDatabase(db_path)
    auth_db_mod._db_instance = db
    uid = db.create_user("widgets-stale@example.com", "WidgetsStale", "local", password="x")

    blender = r"E:\Game Dev\Blender-Project"
    cuttle = r"C:\Projects\Cuttle"
    new_sid = db.create_chat_session(uid, "Fresh chat")
    db.set_session_project(
        new_sid, uid, project_id=2, project_name="Cuttle", project_path=cuttle
    )
    db.upsert_chat_widget(
        widget_id="blender-project-todo",
        user_id=uid,
        wtype="tasks",
        title="Blender project To-do",
        scope="project",
        session_id=None,
        project_path=blender,
        payload={
            "items": [{"id": "1", "text": "Export GLB", "done": False, "children": []}]
        },
        status="active",
        edit_mode="agent",
    )

    token = db.create_auth_session(uid)
    app = Flask(__name__)
    register_chat_widget_routes(app)
    client = app.test_client()

    r = client.get(
        f"/api/widgets?session_id={new_sid}&project_path={blender}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    ids = {w.get("id") for w in r.get_json()["widgets"]}
    assert "blender-project-todo" not in ids, (
        "stale project_path query must not leak another project's widgets "
        "onto a chat whose session row is a different project"
    )


@node_only
def test_create_new_chat_refreshes_widget_strip():
    """Regression: blender To-do stayed visible after New Chat + first send.

    createNewChat nulls currentSessionId / currentProject but historically did
    not call refreshChatWidgets(), so the strip kept rendering the prior chat's
    tasks until something else happened to refresh.
    """
    src = CHAT_JS.read_text(encoding="utf-8")
    body = _extract_function(src, "createNewChat")
    assert "refreshChatWidgets()" in body, (
        "createNewChat must refresh/clear the widgets strip so a prior chat's "
        "To-do does not stick on a new chat"
    )


@node_only
def test_widget_strip_clears_when_session_cleared(tmp_path):
    """Controller contract: refresh() with no session must empty the strip."""
    widgets_path = str(WIDGETS_JS).replace("\\", "/")
    driver = tmp_path / "widget_strip_clear.js"
    driver.write_text(
        """
const fs = require('fs');
const src = fs.readFileSync(%s, 'utf8');
// chat_widgets.js attaches to window (or `this`); mirror window onto global.
global.window = global;
eval(src);

let sessionId = 42;
const strip = {
    hidden: true,
    innerHTML: '',
    __cuttleWidgetsBound: false,
    addEventListener() {},
};
const root = {
    getElementById(id) {
        return id === 'chatWidgetsStrip' ? strip : null;
    },
};
const blenderPayload = {
    success: true,
    widgets_revision: 3,
    widgets: [{
        id: 'blender-todo',
        type: 'tasks',
        status: 'active',
        title: 'Blender To-do',
        edit_mode: 'agent',
        scope: 'session',
        payload: { items: [{ id: '1', text: 'Rig', done: false, children: [] }] },
    }],
};
const fetchFn = (url) => {
    const empty = !/session_id=42\\b/.test(String(url));
    const data = empty
        ? { success: true, widgets: [], widgets_revision: 0 }
        : blenderPayload;
    return Promise.resolve({ json: async () => data });
};

const ctrl = global.CuttleChatWidgets.create({
    root,
    getSessionId: () => sessionId,
    getProjectPath: () => 'E:/Game Dev/Blender-Project',
    fetchFn,
});

ctrl.refresh().then(() => {
    if (strip.hidden || !strip.innerHTML.includes('Blender To-do')) {
        console.error('expected blender widget after first refresh');
        process.exit(2);
    }
    // New chat: session cleared. refresh must hide the prior To-do.
    sessionId = null;
    return ctrl.refresh();
}).then(() => {
    if (!strip.hidden || strip.innerHTML) {
        console.error('strip still showing prior chat widgets: ' + strip.innerHTML);
        process.exit(3);
    }
    console.log('widget-strip-clear-ok');
}).catch((err) => {
    console.error(err);
    process.exit(1);
});
"""
        % json.dumps(widgets_path),
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["node", str(driver)], capture_output=True, text=True, encoding="utf-8", timeout=30
    )
    assert proc.returncode == 0, proc.stderr + "\n---\n" + proc.stdout
    assert "widget-strip-clear-ok" in proc.stdout


@node_only
def test_widget_strip_ignores_stale_inflight_and_empty_revision(tmp_path):
    """Switching chats must not keep the prior To-do from a late fetch.

    Also: live-status widgets_revision=0 must refresh when local rev is non-zero
    (empty target chat).
    """
    widgets_path = str(WIDGETS_JS).replace("\\", "/")
    driver = tmp_path / "widget_strip_stale.js"
    driver.write_text(
        """
const fs = require('fs');
const src = fs.readFileSync(%s, 'utf8');
global.window = global;
eval(src);

let sessionId = 42;
let projectPath = 'E:/Game Dev/Blender-Project';
const strip = {
    hidden: true,
    innerHTML: '',
    __cuttleWidgetsBound: false,
    addEventListener() {},
};
const root = {
    getElementById(id) {
        return id === 'chatWidgetsStrip' ? strip : null;
    },
};
const blenderPayload = {
    success: true,
    widgets_revision: 3,
    widgets: [{
        id: 'blender-todo',
        type: 'tasks',
        status: 'active',
        title: 'Blender To-do',
        edit_mode: 'agent',
        scope: 'session',
        payload: { items: [{ id: '1', text: 'Rig', done: false, children: [] }] },
    }],
};
const emptyPayload = { success: true, widgets: [], widgets_revision: 0 };
let resolveSlow;
const slow = new Promise((resolve) => { resolveSlow = resolve; });
let phase = 'race';
const fetchFn = (url) => {
    const u = String(url);
    const for42 = /session_id=42\\b/.test(u);
    if (phase === 'race' && for42) {
        return slow.then(() => ({ json: async () => blenderPayload }));
    }
    if (for42) {
        return Promise.resolve({ json: async () => blenderPayload });
    }
    return Promise.resolve({ json: async () => emptyPayload });
};

const ctrl = global.CuttleChatWidgets.create({
    root,
    getSessionId: () => sessionId,
    getProjectPath: () => projectPath,
    fetchFn,
});

function waitFor(pred, label, ms) {
    const start = Date.now();
    return new Promise((resolve, reject) => {
        const tick = () => {
            if (pred()) return resolve();
            if (Date.now() - start > (ms || 500)) {
                return reject(new Error(label + ': ' + strip.innerHTML));
            }
            setTimeout(tick, 5);
        };
        tick();
    });
}

ctrl.refresh();
sessionId = 99;
projectPath = 'C:/Projects/Cuttle';
ctrl.refresh();
resolveSlow();
waitFor(
    () => strip.hidden && !strip.innerHTML.includes('Blender To-do'),
    'stale inflight kept prior widget'
).then(() => {
    phase = 'after';
    sessionId = 42;
    projectPath = 'E:/Game Dev/Blender-Project';
    ctrl.refresh();
    return waitFor(
        () => !strip.hidden && strip.innerHTML.includes('Blender To-do'),
        'expected blender widget before empty-rev test'
    );
}).then(() => {
    sessionId = 99;
    projectPath = 'C:/Projects/Cuttle';
    ctrl.onRevision(0);
    return waitFor(
        () => strip.hidden && !strip.innerHTML.includes('Blender To-do'),
        'onRevision(0) did not clear strip'
    );
}).then(() => {
    console.log('widget-strip-stale-ok');
}).catch((err) => {
    console.error(err && err.message ? err.message : err);
    process.exit(1);
});
"""
        % json.dumps(widgets_path),
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["node", str(driver)], capture_output=True, text=True, encoding="utf-8", timeout=30
    )
    assert proc.returncode == 0, proc.stderr + "\n---\n" + proc.stdout
    assert "widget-strip-stale-ok" in proc.stdout


def test_widget_scope_and_edit_visibility_across_chats(tmp_path, monkeypatch):
    """Create Tasks widgets and assert session/project + agent/shared visibility.

    - session + Agent: only that chat
    - session + Shared: only that chat (edit_mode shared)
    - project + Agent: other chats of same project, not other projects
    """
    from flask import Flask
    from urllib.parse import quote

    from api import auth_db as auth_db_mod
    from api.chat_widgets import apply_widget_tags_to_store, register_chat_widget_routes

    db_path = tmp_path / "widgets_visibility.db"
    monkeypatch.setattr(auth_db_mod, "DB_PATH", db_path)
    auth_db_mod._db_instance = None
    db = auth_db_mod.AuthDatabase(db_path)
    auth_db_mod._db_instance = db
    uid = db.create_user("widgets-vis@example.com", "WidgetsVis", "local", password="x")

    cuttle = r"C:\Projects\Cuttle"
    blender = r"E:\Game Dev\Blender-Project"
    chat_a = db.create_chat_session(uid, "Cuttle A")
    chat_b = db.create_chat_session(uid, "Cuttle B")
    chat_other = db.create_chat_session(uid, "Blender chat")
    db.set_session_project(
        chat_a, uid, project_id=1, project_name="Cuttle", project_path=cuttle
    )
    db.set_session_project(
        chat_b, uid, project_id=1, project_name="Cuttle", project_path=cuttle
    )
    db.set_session_project(
        chat_other, uid, project_id=2, project_name="Blender", project_path=blender
    )

    tags = (
        '<cuttle_widget type="tasks" id="sess-agent" scope="session" title="Session Agent" '
        'edit="agent">\n'
        '{"items":[{"id":"1","text":"Only chat A","done":false}]}\n'
        "</cuttle_widget>\n"
        '<cuttle_widget type="tasks" id="sess-shared" scope="session" title="Session Shared" '
        'edit="shared">\n'
        '{"items":[{"id":"1","text":"User can check","done":false}]}\n'
        "</cuttle_widget>\n"
        '<cuttle_widget type="tasks" id="proj-agent" scope="project" title="Project Agent" '
        'edit="agent">\n'
        '{"items":[{"id":"1","text":"All Cuttle chats","done":false}]}\n'
        "</cuttle_widget>\n"
    )
    rewritten, touched = apply_widget_tags_to_store(
        tags, user_id=uid, session_id=chat_a, project_path=cuttle, db=db
    )
    assert len(touched) == 3
    assert "pinned above composer" in rewritten
    assert "<cuttle_widget" not in rewritten.lower()

    token = db.create_auth_session(uid)
    app = Flask(__name__)
    register_chat_widget_routes(app)
    client = app.test_client()
    headers = {"Authorization": f"Bearer {token}"}

    def _ids(session_id: int, project_path: str) -> dict:
        r = client.get(
            f"/api/widgets?session_id={session_id}&project_path={quote(project_path)}",
            headers=headers,
        )
        assert r.status_code == 200
        data = r.get_json()
        assert data["success"] is True
        by_id = {w["id"]: w for w in data["widgets"]}
        return by_id

    in_a = _ids(chat_a, cuttle)
    assert set(in_a) == {"sess-agent", "sess-shared", "proj-agent"}
    assert in_a["sess-agent"]["scope"] == "session"
    assert in_a["sess-agent"]["edit_mode"] == "agent"
    assert in_a["sess-shared"]["edit_mode"] == "shared"
    assert in_a["proj-agent"]["scope"] == "project"
    assert in_a["proj-agent"]["edit_mode"] == "agent"

    in_b = _ids(chat_b, cuttle)
    assert "sess-agent" not in in_b
    assert "sess-shared" not in in_b
    assert "proj-agent" in in_b
    assert in_b["proj-agent"]["edit_mode"] == "agent"

    in_other = _ids(chat_other, blender)
    assert "sess-agent" not in in_other
    assert "sess-shared" not in in_other
    assert "proj-agent" not in in_other


def test_live_status_batch_includes_widgets_revision(tmp_path, monkeypatch):
    """Electron shell batches live-status — must carry widgets_revision or the
    strip never refreshes until a hard reload.
    """
    from flask import Flask

    from api import auth_db as auth_db_mod

    db_path = tmp_path / "widgets_batch_rev.db"
    monkeypatch.setattr(auth_db_mod, "DB_PATH", db_path)
    auth_db_mod._db_instance = None
    db = auth_db_mod.AuthDatabase(db_path)
    auth_db_mod._db_instance = db
    uid = db.create_user("widgets-batch@example.com", "WidgetsBatch", "local", password="x")
    sid = db.create_chat_session(uid, "Batch rev chat")
    db.set_session_project(
        sid, uid, project_id=1, project_name="Cuttle", project_path=r"C:\Projects\Cuttle"
    )
    db.upsert_chat_widget(
        widget_id="batch-todo",
        user_id=uid,
        wtype="tasks",
        title="Batch To-do",
        scope="session",
        session_id=sid,
        project_path="",
        payload={"items": [{"id": "1", "text": "Appear live", "done": False, "children": []}]},
        status="active",
        edit_mode="agent",
    )
    token = db.create_auth_session(uid)

    # Import after DB monkeypatch so routes use the temp db.
    import api.web_chat_api as wca

    app = Flask(__name__)
    app.add_url_rule(
        "/api/chat-live-status-batch",
        view_func=wca.chat_live_status_batch,
        methods=["GET"],
    )
    client = app.test_client()
    r = client.get(
        f"/api/chat-live-status-batch?session_ids={sid}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.get_data(as_text=True)
    data = r.get_json()
    assert data["success"] is True
    statuses = data.get("statuses") or {}
    entry = statuses.get(str(sid)) or statuses.get(f"db_session_{sid}")
    assert entry is not None, f"missing status for {sid}: {statuses.keys()}"
    assert "widgets_revision" in entry, (
        "batch live-status must include widgets_revision so Electron/app-shell "
        "can refresh the Tasks strip without a page reload"
    )
    assert int(entry["widgets_revision"]) >= 1


def test_assistant_reply_path_refreshes_widget_strip():
    """Server rewrites tags to pin chips before the client paints — so
    formatMessage never upserts. processMessage must refresh the strip after
    painting an assistant reply (Electron otherwise needs a hard refresh).
    """
    src = CHAT_JS.read_text(encoding="utf-8")
    # Narrow to the success+response paint block inside processMessage.
    idx = src.find("} else if (data.success && data.response) {")
    assert idx >= 0
    # Look ahead for the notify + end of that branch's local-generation cleanup.
    chunk = src[idx : idx + 12000]
    assert "refreshChatWidgets()" in chunk, (
        "after painting a successful assistant reply, processMessage must call "
        "refreshChatWidgets() — tags are already rewritten server-side so the "
        "client ingest path never runs"
    )


def test_hub_live_status_notes_widgets_revision():
    """App-shell postMessage cuttle-live-status must forward widgets_revision."""
    src = CHAT_JS.read_text(encoding="utf-8")
    body = _extract_function(src, "applyHubLiveStatusPayload")
    assert "noteWidgetsRevision" in body, (
        "applyHubLiveStatusPayload must call noteWidgetsRevision so Electron "
        "batch polls refresh the Tasks strip when revision bumps"
    )


@node_only
def test_widget_strip_renders_scope_and_edit_labels(tmp_path):
    """Strip labels: This chat / Project, Agent / Shared, checkboxes gated."""
    widgets_path = str(WIDGETS_JS).replace("\\", "/")
    driver = tmp_path / "widget_strip_labels.js"
    driver.write_text(
        """
const fs = require('fs');
const src = fs.readFileSync(%s, 'utf8');
global.window = global;
eval(src);

const strip = {
    hidden: true,
    innerHTML: '',
    __cuttleWidgetsBound: false,
    addEventListener() {},
};
const root = {
    getElementById(id) {
        return id === 'chatWidgetsStrip' ? strip : null;
    },
};
const payload = {
    success: true,
    widgets_revision: 5,
    widgets: [
        {
            id: 'sess-agent',
            type: 'tasks',
            status: 'active',
            title: 'Session Agent',
            edit_mode: 'agent',
            scope: 'session',
            payload: { items: [{ id: '1', text: 'A', done: false, children: [] }] },
        },
        {
            id: 'proj-shared',
            type: 'tasks',
            status: 'active',
            title: 'Project Shared',
            edit_mode: 'shared',
            scope: 'project',
            payload: { items: [{ id: '1', text: 'B', done: false, children: [] }] },
        },
    ],
};
const fetchFn = () => Promise.resolve({ json: async () => payload });
const ctrl = global.CuttleChatWidgets.create({
    root,
    getSessionId: () => 7,
    getProjectPath: () => 'C:/Projects/Cuttle',
    fetchFn,
});
ctrl.refresh().then(() => {
    const html = strip.innerHTML;
    if (strip.hidden) {
        console.error('strip hidden');
        process.exit(2);
    }
    if (!html.includes('Session Agent') || !html.includes('Project Shared')) {
        console.error('missing titles: ' + html);
        process.exit(3);
    }
    if (!html.includes('>This chat<') || !html.includes('>Project<')) {
        console.error('missing scope labels: ' + html);
        process.exit(4);
    }
    if (!html.includes('>Agent<') || !html.includes('>Shared<')) {
        console.error('missing edit labels: ' + html);
        process.exit(5);
    }
    if (!html.includes('is-agent-managed')) {
        console.error('agent card missing is-agent-managed');
        process.exit(6);
    }
    if (!/data-widget-id="sess-agent"[\\s\\S]*disabled/.test(html)) {
        console.error('agent checkbox should be disabled');
        process.exit(7);
    }
    // Shared card should not disable its checkbox.
    const sharedBlock = html.split('data-widget-id="proj-shared"')[1] || '';
    const sharedInput = (sharedBlock.match(/<input type="checkbox"[^>]*>/) || [''])[0];
    if (!sharedInput || /\\bdisabled\\b/.test(sharedInput)) {
        console.error('shared checkbox should be enabled: ' + sharedInput);
        process.exit(8);
    }
    console.log('widget-strip-labels-ok');
}).catch((err) => {
    console.error(err);
    process.exit(1);
});
"""
        % json.dumps(widgets_path),
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["node", str(driver)], capture_output=True, text=True, encoding="utf-8", timeout=30
    )
    assert proc.returncode == 0, proc.stderr + "\n---\n" + proc.stdout
    assert "widget-strip-labels-ok" in proc.stdout


def _order_db(tmp_path):
    # Explicit temporary AuthDatabase only: the module singleton and DB_PATH
    # are never touched, so all prior global state is preserved.
    from api import auth_db as auth_db_mod

    db = auth_db_mod.AuthDatabase(tmp_path / "widget_order.db")
    uid = db.create_user("order@example.com", "Order", "local", password="x")
    sess_id = db.create_chat_session(uid, "Order chat")
    return db, uid, sess_id


def _tag(wid, body, op=""):
    op_attr = f' op="{op}"' if op else ""
    return (
        f'<cuttle_widget id="{wid}" type="tasks" title="T-{wid}"'
        f' scope="session"{op_attr}>'
        f"{json.dumps(body)}</cuttle_widget>"
    )


def test_tag_order_base_then_set_done_patch(tmp_path):
    """Single message [base, patch(set_done)]: patch wins (D3 fix)."""
    db, uid, sess_id = _order_db(tmp_path)
    text = "n1 %s mid %s end" % (
        _tag("w1", {"items": [{"id": "1", "text": "a", "done": False}]}),
        _tag("w1", {"set_done": ["1"]}, op="patch"),
    )
    rewritten, touched = apply_widget_tags_to_store(
        text, user_id=uid, session_id=sess_id, project_path="", db=db)
    assert "<cuttle_widget" not in rewritten.lower()
    assert [t["id"] for t in touched] == ["w1", "w1"]
    row = db.get_chat_widget("w1", user_id=uid)
    assert row["payload"]["items"] == [
        {"id": "1", "text": "a", "done": True, "children": []}]


def test_tag_order_base_then_add_patch(tmp_path):
    """Single message [base, add-patch]: added item present exactly once."""
    db, uid, sess_id = _order_db(tmp_path)
    text = "%s %s" % (
        _tag("w1", {"items": [{"id": "1", "text": "a", "done": False}]}),
        _tag("w1", {"add": [{"id": "2", "text": "b"}]}, op="patch"),
    )
    rewritten, _ = apply_widget_tags_to_store(
        text, user_id=uid, session_id=sess_id, project_path="", db=db)
    assert "<cuttle_widget" not in rewritten.lower()
    items = db.get_chat_widget("w1", user_id=uid)["payload"]["items"]
    assert [(i["id"], i["done"]) for i in items] == [("1", False), ("2", False)]


def test_tag_order_patch_then_base_replaces(tmp_path):
    """Single message [patch, base]: the later base intentionally replaces."""
    db, uid, sess_id = _order_db(tmp_path)
    text = "%s %s" % (
        _tag("w1", {"set_done": ["1"]}, op="patch"),
        _tag("w1", {"items": [{"id": "1", "text": "a", "done": False}]}),
    )
    _, touched = apply_widget_tags_to_store(
        text, user_id=uid, session_id=sess_id, project_path="", db=db)
    assert [t["id"] for t in touched] == ["w1", "w1"]
    row = db.get_chat_widget("w1", user_id=uid)
    assert row["payload"]["items"] == [
        {"id": "1", "text": "a", "done": False, "children": []}]


def test_tag_order_interleaved_ids_and_successive_patches(tmp_path):
    """Interleaved w1/w2 plus two successive w1 patches settle in order."""
    db, uid, sess_id = _order_db(tmp_path)
    text = " ".join([
        _tag("w1", {"items": [{"id": "1", "text": "a", "done": False}]}),
        _tag("w2", {"items": [{"id": "9", "text": "z", "done": False}]}),
        _tag("w1", {"set_done": ["1"]}, op="patch"),
        _tag("w1", {"set_text": {"1": "A!"}}, op="patch"),
    ])
    rewritten, touched = apply_widget_tags_to_store(
        text, user_id=uid, session_id=sess_id, project_path="", db=db)
    assert "<cuttle_widget" not in rewritten.lower()
    assert [t["id"] for t in touched] == ["w1", "w2", "w1", "w1"]
    assert db.get_chat_widget("w1", user_id=uid)["payload"]["items"] == [
        {"id": "1", "text": "A!", "done": True, "children": []}]
    assert db.get_chat_widget("w2", user_id=uid)["payload"]["items"] == [
        {"id": "9", "text": "z", "done": False, "children": []}]


def test_tag_order_chips_keep_place_with_noise(tmp_path):
    """Unrelated text and an UNSUPPORTED widget type stay put; supported
    chips land in order around them."""
    db, uid, sess_id = _order_db(tmp_path)
    skipped = (
        '<cuttle_widget id="wx" type="frobnicate" title="Skip me">'
        '{"nonsense": true}</cuttle_widget>'
    )
    text = "hello %s %s world %s !" % (
        _tag("w1", {"items": [{"id": "1", "text": "a", "done": False}]}),
        skipped,
        _tag("w1", {"set_done": ["1"]}, op="patch"),
    )
    rewritten, touched = apply_widget_tags_to_store(
        text, user_id=uid, session_id=sess_id, project_path="", db=db)
    assert rewritten.startswith("hello")
    assert skipped in rewritten  # unsupported type: exact text survives
    assert db.get_chat_widget("wx", user_id=uid) is None
    assert [t["id"] for t in touched] == ["w1", "w1"]
    assert rewritten.count("pinned above composer") == 1  # base chip
    assert "archived — done" in rewritten  # patched chip: all done → archived
    base_chip = rewritten.index("pinned above composer")
    patch_chip = rewritten.index("archived — done")
    assert (rewritten.index("hello") < base_chip < rewritten.index(skipped)
            < rewritten.index("world") < patch_chip < rewritten.rindex("!"))
    assert db.get_chat_widget("w1", user_id=uid)["payload"]["items"][0]["done"] is True


def test_tag_order_leaves_global_db_state_untouched(tmp_path):
    """Order rewrites use the explicit DB only; singleton/DB_PATH unchanged."""
    from api import auth_db as auth_db_mod

    before_instance = auth_db_mod._db_instance
    before_path = auth_db_mod.DB_PATH
    db, uid, sess_id = _order_db(tmp_path)
    apply_widget_tags_to_store(
        _tag("w1", {"items": [{"id": "1", "text": "a", "done": False}]}),
        user_id=uid, session_id=sess_id, project_path="", db=db)
    assert auth_db_mod._db_instance is before_instance
    assert auth_db_mod.DB_PATH == before_path


def test_tag_order_separate_messages_unchanged(tmp_path):
    """Two per-message rewrites: base then patch still applies cleanly."""
    db, uid, sess_id = _order_db(tmp_path)
    apply_widget_tags_to_store(
        _tag("w1", {"items": [{"id": "1", "text": "a", "done": False}]}),
        user_id=uid, session_id=sess_id, project_path="", db=db)
    _, touched = apply_widget_tags_to_store(
        _tag("w1", {"set_done": ["1"]}, op="patch"),
        user_id=uid, session_id=sess_id, project_path="", db=db)
    assert len(touched) == 1
    assert db.get_chat_widget("w1", user_id=uid)["payload"]["items"][0]["done"] is True

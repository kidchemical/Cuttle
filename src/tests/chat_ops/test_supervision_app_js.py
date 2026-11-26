"""Supervision App: pure model, dialog prefill from the app defaults, and shell registration."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[2] / "web"

HARNESS = r"""
const assert = require('assert');
const S = require(process.env.WEB + '/js/chat/chat_supervision_model.js');
const A = require(process.env.WEB + '/js/supervision/supervision_model.js');

// The display toggle is one of the dialog's System care switches, off by default.
assert.deepEqual(S.SYSTEM_CARE.map((c) => c.id), ['processes', 'memory', 'display']);
assert.deepEqual(S.systemFields(null), { processes: true, memory: false, display: false });
assert.deepEqual(S.systemFields({ processes: true }), { processes: true, memory: false, display: false });

// A new dialog starts from the app defaults; a playbook and a stored schedule win.
const app = { every: 1800, agent: 'codex', involve_user: 'never', nudge: 'template',
              system: { processes: false, memory: true, display: true } };
let d = S.dialogDefaults({ handle: 'CH-000007', defaults: app });
assert.deepEqual([d.every, d.agent, d.involveUser, d.nudge], [1800, 'codex', 'never', 'template']);
assert.deepEqual(d.system, { processes: false, memory: true, display: true });
d = S.dialogDefaults({ handle: 'CH-000007', defaults: { every: 777 } });
assert.equal(d.every, 900);  // an interval the dialog cannot show
d = S.dialogDefaults({ handle: 'CH-000007', defaults: app, playbook: { id: 'p', every: 600, involve_user: 'blocked' } });
assert.deepEqual([d.every, d.agent, d.involveUser, d.nudge], [600, 'codex', 'blocked', 'template']);
d = S.dialogDefaults({ handle: 'CH-000007', defaults: app },
    { id: 'op', every: 300, agent: 'cursor', system: { processes: true }, involve_user: 'questions' });
assert.deepEqual([d.every, d.agent, d.involveUser], [300, 'cursor', 'questions']);
assert.deepEqual(d.system, { processes: true, memory: false, display: false });
assert.deepEqual(S.dialogDefaults({ handle: 'CH-000007' }).system, { processes: true, memory: false, display: false });

// Defaults form.
const builtin = { every: 900, involve_user: 'blocked', nudge: 'agent',
                  system: { processes: true, memory: false, display: false }, paused_alert_hours: 6 };
assert.deepEqual(A.fields(null, builtin), builtin);
assert.equal(A.fields({ every: 42 }, builtin).every, 900);
assert(A.same(builtin, A.fields(builtin)));
assert(!A.same(builtin, { ...builtin, system: { ...builtin.system, display: true } }));
let out = A.body({ ...builtin, paused_alert_hours: '2' });
assert(out.ok); assert.equal(out.body.paused_alert_hours, 2); assert.equal(out.body.system.display, false);
assert(!A.body({ ...builtin, paused_alert_hours: '' }).ok);
assert(!A.body({ ...builtin, paused_alert_hours: '-1' }).ok);
assert(!A.body({ ...builtin, every: 42 }).ok);

// Live list rows.
const now = 1000;
let r = A.row({ id: 'a', state: 'pending', target: 'CH-000002', target_id: 2, target_name: 'Game <opt>',
    supervisor: 'CH-000001', supervisor_id: 1, supervisor_name: 'Supervisor', agent: 'claude', every: 900,
    next_check: 900, system: { processes: true, display: true },
    last_seen: { state: 'busy', display_off: 'locked' }, violations: ['x'] }, now);
assert.equal(r.title, 'Game <opt>'); assert.equal(r.by, 'Supervisor');
assert.equal(r.status, 'Chat working · checking…'); assert.equal(r.every, 'every 15m');
assert.deepEqual(r.care, ['processes', 'display']);
assert.deepEqual(r.alerts, ['1 rule violation', 'display is off']);
r = A.row({ id: 'b', state: 'paused', target: 'CH-000003', supervisor: 'CH-000001', every: 600, system: {} }, now);
assert(r.paused); assert(r.status.startsWith('Paused')); assert.equal(r.title, 'CH-000003');
r = A.row({ id: 'c', state: 'pending', target: 'CH-000004', supervisor: 'CH-000001', every: 600, next_check: 5000 }, now);
assert(r.status.startsWith('First check pending'));

// Display note: only when something wants the display.
const want = [{ paused: false, care: ['display'] }];
assert.equal(A.displayNote({ held: false, error: '' }, want), null);
assert.equal(A.displayNote({ held: true }, want).kind, 'ok');
assert.equal(A.displayNote({ held: false, error: 'no session' }, want).kind, 'bad');
assert.equal(A.displayNote({ held: false, error: 'no session' }, [{ paused: true, care: ['display'] }]), null);
assert.equal(A.esc('<a href="x">'), '&lt;a href=&quot;x&quot;&gt;');
console.log(JSON.stringify({ ok: true }));
"""

MIGRATE = r"""
const fs = require('fs');
const src = fs.readFileSync(process.env.RAIL, 'utf8');
const body = src.split('function migrateUILayout(saved) {')[1].split('\n        function ')[0];
const pre = src.split('const CANONICAL_RAIL_ITEM_ORDER')[1].split('let enabledFlags')[0];
const fn = new Function('return (function(){ const CANONICAL_RAIL_ITEM_ORDER' + pre +
  '; return function migrateUILayout(saved) {' + body + '; })()')();
const v15 = fn({ layout_version: 15, rail_items: ['nav-chat', 'nav-media', 'nav-apps'], rail_hidden: ['nav-tasks'] });
const v14 = fn({ layout_version: 14, rail_items: ['nav-chat', 'nav-media', 'nav-apps'], rail_hidden: [] });
const v16 = fn({ layout_version: 16, rail_items: ['nav-chat', 'nav-supervision', 'nav-apps'], rail_hidden: [] });
console.log(JSON.stringify({ v15, v14, v16 }));
"""


def _node(script, **env):
    proc = subprocess.run(["node", "-e", script], env={**os.environ, **env},
                          capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_model_defaults_form_rows_and_dialog_prefill():
    assert _node(HARNESS, WEB=str(WEB)) == {"ok": True}


def test_supervision_app_is_registered_stashed_behind_its_flag():
    html = (WEB / "app_shell.html").read_text(encoding="utf-8")
    rail = (WEB / "js" / "shell" / "shell_rail.js").read_text(encoding="utf-8")
    shell = (WEB / "js" / "shell" / "app_shell.js").read_text(encoding="utf-8")
    assert 'id="nav-supervision"' in html and 'data-page="/supervision_page.html"' in html
    assert "'nav-supervision'" in rail.split("const CANONICAL_RAIL_ITEM_ORDER")[1].split("];")[0]
    assert "'nav-supervision'" in rail.split("const DEFAULT_RAIL_HIDDEN =")[1].split(";")[0]
    assert "'nav-supervision': 'chat_supervision'" in rail.split("const EXPERIMENTAL_RAIL_APPS =")[1].split("};")[0]
    assert "'/supervision_page.html': 'Supervision'" in shell
    page = (WEB / "supervision_page.html").read_text(encoding="utf-8")
    assert page.index("page_controls.css") < page.index("orchestrator_page.css") < page.index("supervision_page.css")
    assert page.index("chat_supervision_model.js") < page.index("supervision_model.js") < page.index("supervision_page.js")
    assert 'data-page="supervision"' in page
    skin = (WEB / "css" / "orchestrator_page.css").read_text(encoding="utf-8")
    assert 'body[data-page="supervision"]' in skin.split("{")[0]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_v16_migration_stashes_supervision_and_keeps_v15_pins():
    out = _node(MIGRATE, RAIL=str(WEB / "js" / "shell" / "shell_rail.js"))
    assert out["v15"]["changed"] and out["v15"]["layout"]["layout_version"] == 16
    assert out["v15"]["layout"]["rail_items"] == ["nav-chat", "nav-media", "nav-apps"]  # a v15 Media pin survives
    assert sorted(out["v15"]["layout"]["rail_hidden"]) == ["nav-supervision", "nav-tasks"]
    assert "nav-media" in out["v14"]["layout"]["rail_hidden"]  # the v15 step still applies to v14
    assert out["v16"]["changed"] is False and "nav-supervision" in out["v16"]["layout"]["rail_items"]


def test_page_and_routes_are_served():
    from flask import Flask

    from api.chat_ops import routes

    app = Flask(__name__)
    app.register_blueprint(routes.chat_ops_bp)
    app.register_blueprint(routes.chat_ops_pages_bp)
    rules = {r.rule: sorted(r.methods - {"HEAD", "OPTIONS"}) for r in app.url_map.iter_rules()}
    assert rules["/supervision_page.html"] == ["GET"]
    assert rules["/api/chat-ops/supervisions"] == ["GET"]
    assert sorted(m for r in app.url_map.iter_rules() if r.rule == "/api/chat-ops/defaults"
                  for m in r.methods - {"HEAD", "OPTIONS"}) == ["GET", "PUT"]

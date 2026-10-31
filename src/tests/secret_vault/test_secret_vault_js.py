"""Secret-vault client code under node.

* chat_secret_fields.js — password/approval markup never carries
  ``data-field-id`` (so the generic collector cannot read a value), names are
  escaped, ``apply`` PUTs values / POSTs grants and returns tokens only.
* chat_action_forms.js — renders secret fields through the module, and a
  safe notice (never a text input) when the module is missing.
* secrets_model.js — Apps page pure helpers.
* static wiring — script tags, stashed rail entry gated by the flag.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
WEB = REPO / "src" / "web"
FIELDS = WEB / "js" / "chat" / "chat_secret_fields.js"
FORMS = WEB / "js" / "chat" / "chat_action_forms.js"
MODEL = WEB / "js" / "secret_vault" / "secrets_model.js"

node_only = pytest.mark.skipif(shutil.which("node") is None, reason="node not available")

HARNESS = r"""
const esc = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const out = {};
const spec = { mode: 'form', title: 'T', resume: true, fields: [
    { id: 's1', type: 'secret', secret: 'db<x>', label: 'DB', required: true },
    { id: 'a1', type: 'secret_approval', secrets: ['db', 'api'], command: 'psql "x"', label: 'Allow' },
]};
const renderCard = (A) => A.renderActionFormCardHtml({ spec, formId: 'f', fallback: '',
    lockedAttr: '', selectedAttr: '', contentPreviewHtml: '', esc });

// Without the secret module: never a text input for a secret.
const bare = require(process.env.FORMS);
const bareHtml = renderCard(bare);
out.bareSafe = !/type="text"[^>]*data-field-id="s1"/.test(bareHtml) && bareHtml.includes('unavailable');

const F = require(process.env.FIELDS);
globalThis.CuttleChatSecretFields = F;
const html = renderCard(bare);
out.password = /type="password"[^>]*data-secret-field="s1"/.test(html);
out.noFieldId = !/data-field-id="s1"/.test(html) && !/data-field-id="a1"/.test(html);
out.escaped = html.includes('db&lt;x&gt;') && !html.includes('db<x>');
out.approvalRadios = (html.match(/name="af_a1"/g) || []).length;
out.command = html.includes('psql &quot;x&quot;');

// apply() with a fake card
function fakeInput(attrs, value) {
    return { value, getAttribute: (k) => attrs[k] ?? null };
}
function fakeGroup(attrs, checked) {
    return { getAttribute: (k) => attrs[k] ?? null,
        querySelector: () => (checked ? { value: checked } : null) };
}
function card(inputs, groups) {
    return {
        querySelector: () => (inputs.length || groups.length ? {} : null),
        querySelectorAll: (sel) => (sel.startsWith('input') ? inputs : groups),
    };
}
const calls = [];
const request = async (url, opts) => {
    calls.push({ url, body: JSON.parse(opts.options.body), method: opts.options.method });
    return { ok: true, status: 200, json: async () => ({ success: true }) };
};
(async () => {
    const pw = fakeInput({ 'data-secret-field': 's1', 'data-secret-name': 'db', 'data-secret-required': '1' }, 'SeCrEt!');
    const grp = fakeGroup({ 'data-secret-approval': 'a1', 'data-secret-names': 'db,api', 'data-secret-command': 'psql' }, 'granted_once');
    const res = await F.apply(card([pw], [grp]), { request, sessionId: '42' });
    out.applyFields = res.fields;
    out.cleared = pw.value === '';
    out.calls = calls;
    const empty = await F.apply(card([fakeInput({ 'data-secret-field': 's', 'data-secret-name': 'x', 'data-secret-required': '1' }, '')], []), { request });
    out.emptyError = empty.error;
    const none = await F.apply(card([], [fakeGroup({ 'data-secret-approval': 'a' }, null)]), { request });
    out.noChoice = none.error;
    const failing = async () => ({ ok: false, status: 403, json: async () => ({ success: false, error: 'Enable it' }) });
    const fail = await F.apply(card([fakeInput({ 'data-secret-field': 's', 'data-secret-name': 'x' }, 'v')], []), { request: failing });
    out.failError = fail.error;

    const M = require(process.env.MODEL);
    out.argv = M.parseArgv('pass\n show \n\n{ref}\n');
    out.session = M.sessionLabel('42');
    out.payload = M.backendPayload({ preset: '', label: 'L', get: 'a\n{ref}', set: '', delete: '', first_line: true });
    out.keyringPayload = M.backendPayload({ preset: 'keyring', keyring_backend: 'x.Y' });
    out.rowEscaped = M.secretRowHtml({ name: 'n', description: '<b>', backend: 'mem', stored: true }, []).includes('&lt;b&gt;');
    out.validName = [M.validName('ok-1'), M.validName('../x')];
    console.log(JSON.stringify(out));
})();
"""


@node_only
def test_secret_fields_and_model_under_node():
    env = {**os.environ, "FORMS": str(FORMS), "FIELDS": str(FIELDS), "MODEL": str(MODEL)}
    proc = subprocess.run(["node", "-e", HARNESS], capture_output=True, text=True, env=env, timeout=30)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["bareSafe"]
    assert out["password"] and out["noFieldId"] and out["escaped"] and out["command"]
    assert out["approvalRadios"] == 3
    assert out["applyFields"] == {"s1": "stored", "a1": "granted_once"}
    assert out["cleared"]
    assert out["calls"] == [
        {"url": "/api/secret-vault/secrets/db", "method": "PUT",
         "body": {"value": "SeCrEt!", "session_id": "42"}},
        {"url": "/api/secret-vault/grants", "method": "POST",
         "body": {"session_id": "42", "secrets": ["db", "api"], "mode": "once", "command": "psql"}},
    ]
    assert out["emptyError"] == "Enter a value for x."
    assert out["noChoice"] == "Choose Allow or Deny."
    assert out["failError"] == "Enable it"
    assert out["argv"] == ["pass", "show", "{ref}"]
    assert out["session"] == "CH-000042"
    assert out["payload"] == {"kind": "command", "label": "L",
                              "options": {"get": ["a", "{ref}"], "set": [], "delete": [], "first_line": True}}
    assert out["keyringPayload"]["options"] == {"keyring_backend": "x.Y"}
    assert out["rowEscaped"] and out["validName"] == [True, False]


def test_static_wiring():
    chat = (WEB / "chat_page.html").read_text(encoding="utf-8")
    assert chat.index("chat_secret_fields.js") < chat.index("chat_action_forms.js")
    shell = (WEB / "app_shell.html").read_text(encoding="utf-8")
    assert 'data-page="/secrets_page.html"' in shell and 'id="nav-secrets"' in shell
    rail = (WEB / "js" / "shell" / "shell_rail.js").read_text(encoding="utf-8")
    assert "'nav-secrets': 'secret_vault'" in rail
    assert "'nav-secrets'" in rail.split("DEFAULT_RAIL_HIDDEN = [")[1].split("]")[0]
    page = (WEB / "secrets_page.html").read_text(encoding="utf-8")
    assert "secrets_model.js" in page and "secrets_page.js" in page
    assert 'name="value" type="password"' in page
    cards = (WEB / "js" / "chat" / "chat_action_cards.js").read_text(encoding="utf-8")
    assert "CuttleChatSecretFields" in cards and "secretFields.apply(" in cards

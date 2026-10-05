"""Card HTML/render planning in src/web/js/chat_action_forms.js (plan C1).

Behavioral characterization of `renderActionFormCardHtml` under node:
modes, malformed specs, escaping, locked/selected states, restart
healing/pending sync, watch bars, session targeting, and nested-preview
passthrough. The page passes `contentPreviewHtml` (nested formatMessage)
and `esc` explicitly; DOM/fetch/timers/action execution stay outside.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MOD_JS = REPO_ROOT / "src" / "web" / "js" / "chat_action_forms.js"

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)

HARNESS = """
const A = require(process.env.MOD_JS);
const esc = (s) => String(s ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const render = (spec, extra) => A.renderActionFormCardHtml(Object.assign(
    { spec, formId: '', fallback: '', lockedAttr: '', selectedAttr: '',
      contentPreviewHtml: '', esc }, extra || {}));
const out = {};
const count = (html, re) => (html.match(new RegExp(re, 'g')) || []).length;

// choice Q&A card
out.choice = (() => {
    const html = render({ mode: 'choice', title: 'Pick',
        options: [{ id: 'a', label: 'A' }, { id: 'cancel', label: 'No' }] });
    return { buttons: count(html, '<button'), primary: count(html, 'cuttle-button--primary'),
        cancelFlag: (html.match(/data-action-form-cancel="\\d"/g) || [])
            .map((m) => /(\d)/.exec(m)[1]).join(','),
        locked: /data-locked="(\\d)"/.exec(html)[1] };
})();
// side-effect choice keeps cancel unstyled
out.cancelStyle = (() => {
    const html = render({ mode: 'choice', title: 'Run?',
        options: [{ id: 'go', label: 'Go', action: 'git.push' }, { id: 'cancel' }] });
    const go = /<button[^>]*data-action-form-option="go"[^>]*>/.exec(html)[0];
    return { goPrimary: go.includes('cuttle-button--primary'), goDisabled: go.includes('disabled') };
})();
// multi selected + locked
out.multi = (() => {
    const html = render({ mode: 'multi', title: 'M',
        options: [{ id: 'x', action: 'a.b' }], selected: ['x'] },
        { lockedAttr: 'true' });
    return { checked: count(html, ' checked'), selClass: count(html, 'is-selected'),
        disabled: count(html, ' disabled'), statusHidden: html.includes('cuttle-action-form-status" hidden') };
})();
// form fields
out.form = (() => {
    const html = render({ mode: 'form', title: 'F', fields: [
        { id: 't', type: 'text', required: true, value: 'v' },
        { id: 'ta', type: 'textarea' },
        { id: 's', type: 'select', options: [{ value: '1', label: 'One' }], value: '1' },
        { id: 'r', type: 'radio', options: [{ value: 'a', label: 'A' }], value: 'a' },
        { id: 'c', type: 'checkboxes', options: [{ value: 'k', label: 'K' }], value: ['k'] },
        { id: 'ch', type: 'checkbox', value: true, line: 'Line' }] });
    return { inputs: count(html, '<input'), textareas: count(html, '<textarea'),
        selects: count(html, '<select'), required: count(html, 'required'),
        submit: html.includes('data-action-form-submit="1"') };
})();
// malformed inputs render without throwing (string options stay a
// preserved TypeError, covered separately)
out.malformed = (() => {
    const r = [render(null, {}), render({}, {}), render({ mode: 'wizard' }, {})];
    return { sums: r.map((h) => count(h, 'cuttle-action-form-fields') + count(h, 'cuttle-action-form-options')).join(','),
        emptyTitle: r[1].includes('Choose an action') };
})();
// string options reach the unguarded predicate (preserved TypeError)
out.stringOptionsThrows = (() => {
    try { render({ mode: 'choice', options: 'nope' }, {}); return 'no-throw'; }
    catch (e) { return e.constructor.name; }
})();
// escaping
out.escape = (() => {
    const html = render({ mode: 'choice', title: '<script>alert(1)</script>',
        description: 'a&b"c', options: [{ id: '<x>', label: 'L&"' }] },
        { formId: '<f>' });
    return { noRawScript: !html.includes('<script>'), hasLt: html.includes('&lt;script&gt;'),
        hasAmp: html.includes('a&amp;b&quot;c'), noRawAttr: !html.includes('data-form-id="<f>"') };
})();
// restart heal: soft-dismiss toast unlocks the card for status sync
out.heal = (() => {
    const spec = { mode: 'choice', title: 'Restart Flask', toast: 'Ignored by user',
        options: [{ id: 'status', action: 'flask.restart' }] };
    const html = render(spec, { formId: 'flask-restart-g2' });
    return { locked: /data-locked="(\\d)"/.exec(html)[1],
        pendingSync: /data-restart-pending-sync="(\\d)"/.exec(html)[1],
        hasLockedClass: html.includes('cuttle-action-form--locked') };
})();
// pending restart sync: unlocked restart card waits for status
out.pending = (() => {
    const spec = { mode: 'choice', title: 'Restart Flask',
        options: [{ id: 'status', action: 'flask.restart' }] };
    const html = render(spec, { formId: 'flask-restart-g2' });
    return { locked: /data-locked="(\\d)"/.exec(html)[1],
        pendingSync: /data-restart-pending-sync="(\\d)"/.exec(html)[1],
        disabled: count(html, ' disabled'), summary: /Checking restart status/.test(html) };
})();
// locked restart progress
out.progress = (() => {
    const html = render({ mode: 'choice', title: 'Restart Flask', locked: true,
        restartId: 'r1', pending: true, toast: 'Stopping',
        options: [{ id: 'status', action: 'flask.restart' }] },
        { formId: 'flask-restart-g2' });
    return { bar18: html.includes('width:18%'), labelStopping: html.includes('Stopping') };
})();
// watch bars: primary + worker
out.watch = (() => {
    const html = render({ mode: 'choice', title: 'Bake',
        watch: { id: 'job', url: '/output/x.json',
            snapshot: { state: 'running', percent: 42, label: 'Half',
                bars: [{ id: 'all', percent: 42 }, { label: 'w1', percent: 10 }] } },
        options: [{ id: 'a', action: '__watch_resume__' }] },
        { formId: 'w1' });
    return { items: count(html, 'progress-bar-item '), primary: count(html, 'progress-bar-item--primary'),
        worker: count(html, 'progress-bar-item--worker'), label: html.includes('Half') };
})();
// failed watch
out.watchFail = (() => {
    const html = render({ mode: 'choice', title: 'Bake',
        watch: { id: 'job', url: '/output/x.json',
            snapshot: { state: 'failed', percent: 100, label: 'Boom' } },
        options: [{ id: 'a', action: '__watch_cancel__' }] });
    return { failed: html.includes('is-failed'),
        failIcon: html.includes('cuttle-action-form-summary-icon" aria-hidden="true">✕') };
})();
// session targeting attributes
out.session = (() => {
    const html = render({ mode: 'choice', title: 'S', session_id: '7',
        restartId: 'rid', restartFormGroup: 'g', options: [{ id: 'a' }] },
        { formId: 'f16' });
    return { sid: /data-session-id="([^"]*)"/.exec(html)[1],
        group: /data-restart-form-group="([^"]*)"/.exec(html)[1],
        rid: /data-restart-id="([^"]*)"/.exec(html)[1],
        formId: /data-form-id="([^"]*)"/.exec(html)[1] };
})();
// nested preview passes through verbatim
out.preview = (() => {
    const html = render({ mode: 'choice', title: 'P', options: [{ id: 'a' }] },
        { contentPreviewHtml: '<div class="cuttle-action-form-preview"><p>fmt</p></div>' });
    return { passthrough: html.includes('<div class="cuttle-action-form-preview"><p>fmt</p></div>'),
        absent: render({ mode: 'choice', title: 'P', options: [{ id: 'a' }] }, {})
            .includes('cuttle-action-form-preview') };
})();
// owner boundary: no page globals inside the renderer (comments stripped)
out.boundary = (() => {
    const src = (A.renderActionFormCardHtml.toString()
        + A.renderWatchBarsHtml.toString())
        .replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*/g, '');
    const banned = ['document', 'window', 'fetch(', 'formatMessage', 'currentSessionId'];
    return { banned: banned.filter((t) => src.includes(t)) };
})();
process.stdout.write(JSON.stringify(out));
"""


def _run_harness():
    env = {"MOD_JS": str(MOD_JS), "PATH": "/usr/bin:/bin"}
    proc = subprocess.run(
        ["node", "-e", HARNESS],
        capture_output=True, text=True, timeout=120, env=env,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout)


@node_only
def test_choice_card_structure():
    out = _run_harness()["choice"]
    assert out["buttons"] == 3  # 2 options + summary toggle
    assert out["primary"] == 1  # non-cancel option only (cancel is unstyled)
    assert out["cancelFlag"] == "0,1"
    assert out["locked"] == "0"


@node_only
def test_cancel_option_unstyled_but_enabled():
    out = _run_harness()["cancelStyle"]
    assert out["goPrimary"] is True
    assert out["goDisabled"] is False


@node_only
def test_multi_selected_locked():
    out = _run_harness()["multi"]
    assert out["checked"] == 1
    assert out["selClass"] == 1
    assert out["disabled"] >= 1
    assert out["statusHidden"] is False


@node_only
def test_form_field_types():
    out = _run_harness()["form"]
    assert out["inputs"] == 4  # text + radio + checkboxes + checkbox
    assert out["textareas"] == 1
    assert out["selects"] == 1
    assert out["required"] >= 1
    assert out["submit"] is True


@node_only
def test_malformed_specs_render():
    out = _run_harness()["malformed"]
    assert out["emptyTitle"] is True
    assert out["sums"] == "1,1,1"


@node_only
def test_string_options_throw_preserved():
    assert _run_harness()["stringOptionsThrows"] == "TypeError"


@node_only
def test_escaping():
    out = _run_harness()["escape"]
    assert out["noRawScript"] is True
    assert out["hasLt"] is True
    assert out["hasAmp"] is True
    assert out["noRawAttr"] is True


@node_only
def test_restart_heal_unlocks():
    out = _run_harness()["heal"]
    assert out["locked"] == "0"
    assert out["pendingSync"] == "1"
    assert out["hasLockedClass"] is False


@node_only
def test_restart_pending_sync():
    out = _run_harness()["pending"]
    assert out["locked"] == "0"
    assert out["pendingSync"] == "1"
    assert out["disabled"] >= 1
    assert out["summary"] is True


@node_only
def test_restart_progress_bar():
    out = _run_harness()["progress"]
    assert out["bar18"] is True
    assert out["labelStopping"] is True


@node_only
def test_watch_bars_kinds():
    out = _run_harness()["watch"]
    assert out["items"] == 2
    assert out["primary"] == 1
    assert out["worker"] == 1
    assert out["label"] is True


@node_only
def test_failed_watch_marks():
    out = _run_harness()["watchFail"]
    assert out["failed"] is True
    assert out["failIcon"] is True


@node_only
def test_session_targeting_attrs():
    out = _run_harness()["session"]
    assert out["sid"] == "7"
    assert out["group"] == "g"
    assert out["rid"] == "rid"
    assert out["formId"] == "f16"


@node_only
def test_preview_passthrough():
    out = _run_harness()["preview"]
    assert out["passthrough"] is True
    assert out["absent"] is False


@node_only
def test_owner_boundary_no_page_globals():
    assert _run_harness()["boundary"] == {"banned": []}


@node_only
def test_card_module_parses():
    proc = subprocess.run(
        ["node", "--check", str(MOD_JS)],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr[-1000:]


@node_only
def test_watch_frame_grid_escape_bound_and_snapshot():
    code = HARNESS.split("const out = {};")[0] + r"""
const grid = {total: 240, inventory:'verified', workers:['tower', '<img src=x>'], cells:[
    {frame:1, state:'completed',worker:'tower'},
    {frame:2, state:'rendering',worker:'<img src=x>', gap_fill:true},
    {frame:3, state:'missing'}]};
const html = render({mode:'choice', watch:{id:'b',url:'/output/b.json',
    snapshot:{state:'done', grid}}, options:[]});
const bounded = A.renderWatchGridHtml({cells:Array.from({length:3000}, (_,i)=>({frame:i,state:'pending'}))}, esc);
console.log(JSON.stringify({html, count:(bounded.match(/class="watch-frame /g)||[]).length,
    colour:A.watchWorkerColour('tower'), same:A.watchWorkerColour('tower')}));
"""
    result = subprocess.run(['node', '-e', code], env={**__import__('os').environ, 'MOD_JS': str(MOD_JS)},
                            capture_output=True, text=True, check=True)
    out = json.loads(result.stdout)
    assert 'is-completed' in out['html'] and 'is-rendering is-gap-fill' in out['html']
    assert 'Frame 3 · missing' in out['html']
    assert '<img src=x>' not in out['html'] and '&lt;img src=x&gt;' in out['html']
    assert out['count'] == 2048
    assert out['colour'] == out['same']

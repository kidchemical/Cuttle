"""Rail hold-to-edit mode always has a visible exit.

Taps on page content land inside the content iframe and never reach the
shell document, so the document-level tap-outside handler cannot exit edit
mode on most of the screen. The shell therefore shows a transparent scrim
over each pane's content plus a Done button while editing. This pins that
contract: scrim visibility follows the mode, tapping the scrim or Done
leaves the mode, and taps on Done don't double-exit via the scrim.
"""
import shutil
from pathlib import Path

import pytest

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)

REPO = Path(__file__).resolve().parents[3]
SHELL_SRC = REPO / "src/web/js/shell/app_shell.js"
SHELL_CSS = REPO / "src/web/css/app_shell.css"


def _extract_edit_mode():
    source = SHELL_SRC.read_text(encoding="utf-8")
    start = source.index("function setRailEditing(on)")
    end = source.index("function ensureRailEditChrome", start)
    return source[start:end]


STUB = """
let railEditing = false;
let hidePickerCalls = 0;
function hideRailAddPicker() { hidePickerCalls++; }
const allEls = [];
function makeEl(className) {
    const el = {
        hidden: false,
        textContent: '',
        type: '',
        children: [],
        handlers: {},
        _classes: new Set(),
        _className: '',
        get className() { return el._className; },
        set className(v) {
            el._className = v || '';
            el._classes = new Set(el._className.split(' ').filter(Boolean));
        },
        classList: {
            toggle(c, force) {
                if (force === undefined) {
                    if (el._classes.has(c)) el._classes.delete(c);
                    else el._classes.add(c);
                } else if (force) el._classes.add(c);
                else el._classes.delete(c);
            },
            contains(c) { return el._classes.has(c); },
        },
        setAttribute() {},
        appendChild(c) { el.children.push(c); return c; },
        addEventListener(t, f) { (el.handlers[t] = el.handlers[t] || []).push(f); },
        querySelector(sel) {
            if (sel === '.rail-edit-scrim') {
                return el.children.find((c) => c._classes.has('rail-edit-scrim')) || null;
            }
            return null;
        },
    };
    el.className = className || '';
    allEls.push(el);
    return el;
}
const rails = [makeEl('icon-rail'), makeEl('icon-rail')];
const document = {
    createElement: () => makeEl(''),
    querySelectorAll: (sel) => {
        if (sel === '.icon-rail') return rails;
        if (sel === '.rail-edit-scrim') return allEls.filter((e) => e._classes.has('rail-edit-scrim'));
        return [];
    },
    getElementById: () => null,
};
const mainEl = makeEl('shell-main');
const col = { querySelector: (sel) => (sel === '.shell-main' ? mainEl : null) };
"""


@node_only
def test_edit_scrim_toggles_with_mode_and_exits_on_tap():
    import subprocess

    script = "const assert = require('node:assert/strict');\n"
    script += STUB + "\n" + _extract_edit_mode() + "\n"
    script += """
ensureRailEditScrim(col);
ensureRailEditScrim(col);
const scrims = () => allEls.filter((e) => e._classes.has('rail-edit-scrim'));
assert.equal(scrims().length, 1, 'one scrim per pane content');
const scrim = scrims()[0];
assert.equal(scrim.hidden, true, 'scrim hidden while not editing');
const done = scrim.children.find((c) => c._classes.has('rail-edit-done'));
assert.ok(done, 'Done button lives on the scrim');
assert.equal(done.textContent, 'Done');

setRailEditing(true);
assert.ok(rails.every((r) => r.classList.contains('rail-editing')));
assert.equal(scrim.hidden, false, 'scrim covers content while editing');

// Tap on Done must not exit through the scrim handler (target is the button).
scrim.handlers['pointerdown'][0]({ target: done });
assert.ok(rails[0].classList.contains('rail-editing'), 'Done tap is not a scrim tap');
// Done click exits.
let stopped = false;
done.handlers['click'][0]({ stopPropagation() { stopped = true; } });
assert.ok(stopped);
assert.ok(rails.every((r) => !r.classList.contains('rail-editing')));
assert.equal(scrim.hidden, true);
assert.equal(hidePickerCalls, 1);

// Scrim tap exits.
setRailEditing(true);
assert.equal(scrim.hidden, false);
scrim.handlers['pointerdown'][0]({ target: scrim });
assert.ok(rails.every((r) => !r.classList.contains('rail-editing')));
assert.equal(scrim.hidden, true);
assert.equal(hidePickerCalls, 2);
"""
    subprocess.run(["node", "-e", script], check=True)


def test_edit_mode_jiggle_css_present():
    css = SHELL_CSS.read_text(encoding="utf-8")
    assert "@keyframes rail-jiggle" in css
    assert ".rail-edit-scrim" in css
    assert ".rail-edit-done" in css
    # Every rail icon jiggles (including the held one); minus badges stay
    # scoped to removable entries by the rail-editing badge rule.
    assert ".icon-rail.rail-editing .rail-items .rail-item {" in css
    assert ".rail-item-remove" in css
    assert "prefers-reduced-motion" in css

"""Studio display helper (node): pref + flag gate, stale pref, kill switch, sync.

Runs ``src/web/js/shared/chat_display.js`` under node with fake window,
document and localStorage. Nothing here touches a real browser or the server.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
MODEL = REPO / "src" / "web" / "js" / "shared" / "chat_display.js"

HARNESS = r"""
const M = require(process.env.MODEL);

function fakeStore(init, opts) {
  const m = new Map(Object.entries(init || {}));
  return {
    getItem(k) { if (opts && opts.throws) throw new Error('blocked'); return m.has(k) ? m.get(k) : null; },
    setItem(k, v) { m.set(k, String(v)); },
    _m: m,
  };
}
function fakeWin(origin) {
  const handlers = {};
  return {
    location: { origin },
    addEventListener(t, f) { (handlers[t] = handlers[t] || []).push(f); },
    fire(t, ev) { (handlers[t] || []).forEach((f) => f(ev)); },
  };
}
function fakeDoc() {
  const classes = new Set();
  return {
    classes,
    documentElement: { classList: {
      toggle(c, on) { if (on) classes.add(c); else classes.delete(c); return !!on; },
      contains(c) { return classes.has(c); },
    } },
  };
}
const tick = () => new Promise((r) => setTimeout(r, 0));
const FLAG_ON = { success: true, flags: [{ id: 'chat_studio_display', enabled: true }] };
const FLAG_OFF = { success: true, flags: [{ id: 'chat_studio_display', enabled: false }] };

function make(store, opts) {
  const o = opts || {};
  const win = fakeWin('https://host.test');
  const doc = fakeDoc();
  const st = fakeStore(store, o);
  const calls = [];
  const fetchFn = o.payload === undefined ? undefined : async (url) => {
    calls.push(url);
    if (o.payload instanceof Error) throw o.payload;
    return { json: async () => o.payload };
  };
  const d = M.create({ window: win, document: doc, storage: st, fetch: fetchFn });
  return { d, win, doc, st, calls };
}

async function main() {
  const out = {};

  // Gate: effective Studio needs the pref AND the cached flag.
  let t = make({});
  out.classic = { mode: t.d.mode(), cls: t.doc.classes.has('cuttle-studio') };
  t = make({ cuttleChatDisplay: 'studio', cuttleChatStudioFlag: '1' });
  out.studio = { mode: t.d.mode(), isStudio: t.d.isStudio(), cls: t.doc.classes.has('cuttle-studio') };
  t = make({ cuttleChatDisplay: 'studio' });
  out.stalePrefNoFlag = { mode: t.d.mode(), cls: t.doc.classes.has('cuttle-studio'), pref: t.d.state().pref };
  t = make({ cuttleChatDisplay: 'studio', cuttleChatStudioFlag: '0' });
  out.stalePrefFlagOff = { mode: t.d.mode(), cls: t.doc.classes.has('cuttle-studio') };
  t = make({ cuttleChatStudioFlag: '1' });
  out.flagOnlyNoPref = { mode: t.d.mode(), flag: t.d.state().flag };
  t = make({ cuttleChatDisplay: 'nonsense', cuttleChatStudioFlag: '1' });
  out.junkPref = { mode: t.d.mode() };
  t = make({ cuttleChatDisplay: 'studio', cuttleChatStudioFlag: '1' }, { throws: true });
  out.blockedStorage = { mode: t.d.mode(), cls: t.doc.classes.has('cuttle-studio') };

  // syncFlag: fetches, writes the cache, drops the class on off / kill switch.
  t = make({ cuttleChatDisplay: 'studio', cuttleChatStudioFlag: '1' }, { payload: FLAG_OFF });
  const offResult = await t.d.syncFlag();
  out.syncOff = { result: offResult, mode: t.d.mode(), cls: t.doc.classes.has('cuttle-studio'),
    cache: t.st._m.get('cuttleChatStudioFlag'), pref: t.d.state().pref, url: t.calls[0] };

  t = make({ cuttleChatDisplay: 'studio' }, { payload: FLAG_ON });
  const beforeOn = t.d.mode();
  const onResult = await t.d.syncFlag();
  out.syncOnFromAbsent = { before: beforeOn, result: onResult, mode: t.d.mode(),
    cls: t.doc.classes.has('cuttle-studio'), cache: t.st._m.get('cuttleChatStudioFlag') };

  t = make({ cuttleChatDisplay: 'studio', cuttleChatStudioFlag: '1' },
    { payload: { success: true, kill_switch: true, flags: [{ id: 'chat_studio_display', enabled: true }] } });
  out.killSwitch = { result: await t.d.syncFlag(), mode: t.d.mode() };

  t = make({ cuttleChatDisplay: 'studio', cuttleChatStudioFlag: '1' }, { payload: { success: false, error: 'x' } });
  out.unusablePayload = { result: await t.d.syncFlag(), mode: t.d.mode() };

  t = make({ cuttleChatDisplay: 'studio', cuttleChatStudioFlag: '1' }, { payload: new Error('offline') });
  out.fetchFailed = { result: await t.d.syncFlag(), mode: t.d.mode() };

  t = make({});
  out.noFetch = { result: await t.d.syncFlag(), mode: t.d.mode() };

  // setMode persists, applies here, and rejects unknown values.
  t = make({ cuttleChatStudioFlag: '1' });
  const log = [];
  const off = t.d.onChange((s) => log.push(s.mode));
  t.d.setMode('studio');
  const afterStudio = { mode: t.d.mode(), cls: t.doc.classes.has('cuttle-studio'), stored: t.st._m.get('cuttleChatDisplay') };
  t.d.setMode('studio');
  t.d.setMode('bogus');
  t.d.setMode('classic');
  out.setMode = { afterStudio, log };
  off();
  t.d.setMode('studio');
  out.unsubscribed = log.length;

  // Other pane: storage event re-applies; unrelated keys do nothing.
  t = make({ cuttleChatStudioFlag: '1' });
  const seen = [];
  t.d.onChange((s) => seen.push(s.studio));
  t.st._m.set('cuttleChatDisplay', 'studio');
  t.win.fire('storage', { key: 'someOtherKey' });
  const unrelated = t.doc.classes.has('cuttle-studio');
  t.win.fire('storage', { key: 'cuttleChatDisplay' });
  out.storage = { unrelated, afterEvent: t.doc.classes.has('cuttle-studio'), seen: seen.slice() };

  // Flag cache changed in another pane: class follows.
  t.st._m.set('cuttleChatStudioFlag', '0');
  t.win.fire('storage', { key: 'cuttleChatStudioFlag' });
  out.storageFlagOff = { cls: t.doc.classes.has('cuttle-studio'), seen: seen.slice() };

  // Messages: a cross-origin relay is ignored; a same-origin appearance relay
  // re-applies from storage; a flags-changed message refetches the flag.
  t = make({ cuttleChatDisplay: 'studio', cuttleChatStudioFlag: '1' }, { payload: FLAG_ON });
  t.st._m.set('cuttleChatStudioFlag', '0');
  t.win.fire('message', { origin: 'https://evil.test', data: { type: 'cuttle-ui-anim-change' } });
  const crossOrigin = t.doc.classes.has('cuttle-studio');
  t.win.fire('message', { origin: 'https://host.test', data: { type: 'cuttle-ui-anim-change' } });
  out.message = { crossOriginApplied: crossOrigin, appearanceApplied: t.doc.classes.has('cuttle-studio') };
  t.st._m.set('cuttleChatStudioFlag', '1');
  t.win.fire('message', { origin: 'https://host.test', data: { type: 'cuttle-experimental-flags-changed' } });
  await tick();
  out.message.flagsChangedFetched = t.calls.length;
  out.message.flagsChangedCache = t.st._m.get('cuttleChatStudioFlag');
  out.message.flagsChangedClass = t.doc.classes.has('cuttle-studio');

  // Exports for the owner modules and the boot gate.
  out.exports = { key: M.MODE_KEY, flagKey: M.FLAG_KEY, cls: M.CLASS, flagId: M.FLAG_ID,
    effective: [M.effectiveMode('studio', '1'), M.effectiveMode('studio', '0'), M.effectiveMode(null, '1')],
    payload: [M.flagFromPayload(FLAG_ON), M.flagFromPayload(null), M.flagFromPayload({ success: true, flags: [] })] };
  return out;
}

main().then((o) => console.log(JSON.stringify(o))).catch((e) => { console.error(e); process.exit(1); });
"""


@pytest.fixture(scope="module")
def result():
    if not shutil.which("node"):
        pytest.skip("node not available")
    proc = subprocess.run(
        ["node", "-e", HARNESS],
        env={**os.environ, "MODEL": str(MODEL)},
        capture_output=True, text=True, timeout=60,
        encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_default_is_classic_with_no_class(result):
    assert result["classic"] == {"mode": "classic", "cls": False}


def test_studio_needs_pref_and_cached_flag(result):
    assert result["studio"] == {"mode": "studio", "isStudio": True, "cls": True}


def test_stale_pref_with_flag_off_stays_classic(result):
    assert result["stalePrefNoFlag"] == {"mode": "classic", "cls": False, "pref": "studio"}
    assert result["stalePrefFlagOff"] == {"mode": "classic", "cls": False}


def test_flag_without_pref_and_junk_pref_are_classic(result):
    assert result["flagOnlyNoPref"]["mode"] == "classic"
    assert result["flagOnlyNoPref"]["flag"] is True
    assert result["junkPref"]["mode"] == "classic"


def test_blocked_storage_degrades_to_classic(result):
    assert result["blockedStorage"] == {"mode": "classic", "cls": False}


def test_flag_off_drops_class_and_keeps_pref(result):
    off = result["syncOff"]
    assert off["result"] is False
    assert off["mode"] == "classic"
    assert off["cls"] is False
    assert off["cache"] == "0"
    assert off["pref"] == "studio"  # kept, so re-enabling restores Studio
    assert off["url"] == "/api/experimental/flags"


def test_flag_on_from_absent_cache_enables_studio(result):
    # Pref was already 'studio'; the first flag fetch that says on turns the look on.
    on = result["syncOnFromAbsent"]
    assert on == {"before": "classic", "result": True, "mode": "studio", "cls": True, "cache": "1"}


def test_kill_switch_forces_off(result):
    assert result["killSwitch"] == {"result": False, "mode": "classic"}


def test_unusable_payload_and_fetch_failure_keep_cache(result):
    assert result["unusablePayload"] == {"result": True, "mode": "studio"}
    assert result["fetchFailed"] == {"result": True, "mode": "studio"}


def test_sync_without_fetch_is_a_noop(result):
    assert result["noFetch"] == {"result": False, "mode": "classic"}


def test_set_mode_persists_applies_and_notifies_once_per_change(result):
    assert result["setMode"]["afterStudio"] == {"mode": "studio", "cls": True, "stored": "studio"}
    # studio (change), studio again (no-op), bogus (ignored), classic (change)
    assert result["setMode"]["log"] == ["studio", "classic"]


def test_unsubscribe_stops_callbacks(result):
    assert result["unsubscribed"] == 2


def test_storage_event_from_other_pane(result):
    assert result["storage"]["unrelated"] is False
    assert result["storage"]["afterEvent"] is True
    assert result["storage"]["seen"] == [True]
    assert result["storageFlagOff"]["cls"] is False
    assert result["storageFlagOff"]["seen"] == [True, False]


def test_message_relay_and_flag_refetch(result):
    msg = result["message"]
    assert msg["crossOriginApplied"] is True  # class was on from creation; the foreign relay changed nothing
    assert msg["appearanceApplied"] is False  # flag went off in storage; the same-origin relay re-applied it
    assert msg["flagsChangedFetched"] == 1  # the flags-changed message triggers one fetch
    assert msg["flagsChangedCache"] == "1"
    assert msg["flagsChangedClass"] is True


def test_cross_origin_message_is_ignored():
    # Dedicated check: a cross-origin relay must not change the class.
    if not shutil.which("node"):
        pytest.skip("node not available")
    script = HARNESS.split("async function main()")[0] + r"""
const t = make({ cuttleChatDisplay: 'studio', cuttleChatStudioFlag: '1' });
t.st._m.set('cuttleChatStudioFlag', '0');
t.win.fire('message', { origin: 'https://evil.test', data: { type: 'cuttle-ui-anim-change' } });
console.log(JSON.stringify({ cls: t.doc.classes.has('cuttle-studio') }));
"""
    proc = subprocess.run(["node", "-e", script], env={**os.environ, "MODEL": str(MODEL)},
                          capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == {"cls": True}


def test_reload_on_flip_reloads_only_when_the_effective_mode_changes():
    """The chat page opts in: a mode flip after load reloads it; a pref/flag change that
    keeps the mode does not."""
    if not shutil.which("node"):
        pytest.skip("node not available")
    script = HARNESS.split("async function main()")[0] + r"""
const out = {};
let t = make({ cuttleChatDisplay: 'studio', cuttleChatStudioFlag: '1' });
let reloads = 0;
t.win.location.reload = () => { reloads += 1; };
t.d.reloadOnFlip();
t.d.setMode('studio');
out.sameMode = reloads;
t.st._m.set('cuttleChatStudioFlag', '0');           // flag turned off elsewhere
t.win.fire('storage', { key: 'cuttleChatStudioFlag' });
out.flagOff = reloads;
t = make({});                                        // Classic page, pref saved with the flag off
reloads = 0;
t.win.location.reload = () => { reloads += 1; };
t.d.reloadOnFlip();
t.d.setMode('studio');                               // still Classic: the flag is off
out.prefOnlyStaysClassic = reloads;
const off = t.d.reloadOnFlip();
off();
console.log(JSON.stringify(out));
"""
    proc = subprocess.run(["node", "-e", script], env={**os.environ, "MODEL": str(MODEL)},
                          capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == {"sameMode": 0, "flagOff": 1, "prefOnlyStaysClassic": 0}


def test_pure_exports(result):
    exp = result["exports"]
    assert exp["key"] == "cuttleChatDisplay"
    assert exp["flagKey"] == "cuttleChatStudioFlag"
    assert exp["cls"] == "cuttle-studio"
    assert exp["flagId"] == "chat_studio_display"
    assert exp["effective"] == ["studio", "classic", "classic"]
    assert exp["payload"] == [True, None, False]

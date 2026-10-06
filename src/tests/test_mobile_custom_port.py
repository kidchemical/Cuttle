"""Custom-port round-trip and validation for the mobile shell + companion.

Offline only: real functions are extracted from ``apps/mobile/src/main.js``
and evaluated in Node with fake Preferences/DOM/fetch globals (the module's
Capacitor imports and top-level ``init()`` cannot run under Node, so a direct
import is not possible — extraction keeps the test honest about what it runs).
Handler coverage fires the real ``bindForm`` listeners through fake click /
submit events and asserts zero Preferences writes, zero fetch calls, and zero
navigation for invalid ports.

The Android companion (Kotlin) has no JVM/Android harness in this environment
(no ``kotlinc``; nothing installed to get one), so its checks are
source-contract assertions on the real validator, not execution.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MAIN_JS = REPO / "apps" / "mobile" / "src" / "main.js"
COMPANION_MAIN = REPO / "apps" / "android_companion" / "app" / "src" / "main" / (
    "java/com/cuttle/companion/ui/MainActivity.kt"
)
COMPANION_BASE_URL = REPO / "apps" / "android_companion" / "app" / "src" / "main" / (
    "java/com/cuttle/companion/core/BaseUrl.kt"
)
COMPANION_API = REPO / "apps" / "android_companion" / "app" / "src" / "main" / (
    "java/com/cuttle/companion/sse/CuttleApi.kt"
)

PURE_FUNCTIONS = [
    "cleanHost",
    "buildBaseUrl",
    "cuttleEntryUrl",
    "recentHostKey",
    "upsertRecentHost",
    "parsePortNumber",
    "isValidPort",
    "saveConfig",
    "loadConfig",
    "loadRecentHosts",
    "saveRecentHosts",
    "rememberHost",
    "readForm",
    "syncSchemeUi",
    "populateForm",
    "showStatus",
    "clearStatus",
    "formatRecentMeta",
    "nativeShell",
    "renderRecentHosts",
    "testConnection",
    "applyNativeNotifications",
    "openInNativeShell",
    "bindForm",
]

PURE_CONSTS = ["PREFS", "DEFAULT_PORT_HTTP", "DEFAULT_PORT_HTTPS", "MAX_RECENT_HOSTS", "$"]


def _extract_decl(source: str, name: str) -> str:
    pattern = re.compile(
        r"(?:export\s+)?(?:async\s+)?(?:function\s+" + re.escape(name) + r"\s*\(|const\s+"
        + re.escape(name) + r"\s*=)"
    )
    match = pattern.search(source)
    assert match, f"declaration {name} not found in main.js"
    start = match.start()
    brace = source.index("{", match.end() - 1)
    depth = 0
    i = brace
    while True:
        char = source[i]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    return source[start : i + 1]


def _extract_const(source: str, name: str) -> str:
    pattern = re.compile(r"(?:export\s+)?const\s+" + re.escape(name) + r"\s*=")
    match = pattern.search(source)
    assert match, f"const {name} not found in main.js"
    semi = source.index(";", match.end())
    return source[match.start() : semi + 1]


def _node_eval(driver: str) -> str:
    node = shutil.which("node")
    assert node, "node is required for the offline main.js checks"
    source = MAIN_JS.read_text(encoding="utf-8")
    parts = [_extract_const(source, name) for name in PURE_CONSTS]
    parts += [_extract_decl(source, name) for name in PURE_FUNCTIONS]
    script = "\n".join(parts) + "\n" + driver
    proc = subprocess.run(
        [node, "--input-type=module", "--eval", script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, f"node driver failed:\n{proc.stderr}\n{proc.stdout}"
    return proc.stdout


FAKES = """
const __store = {};
const Preferences = {
  set: async ({ key, value }) => { __store[key] = String(value); },
  get: async ({ key }) => ({ value: (__store[key] !== undefined ? __store[key] : null) }),
};
const __fetchCalls = [];
globalThis.fetch = async (url, opts) => {
  __fetchCalls.push(String(url));
  return { ok: true, json: async () => ({ ok: true, message: 'Cuttle OK' }) };
};
function __mkNode() {
  return {
    className: '', textContent: '', innerHTML: '', title: '', value: '',
    classList: { toggle() {} }, children: [], _h: {},
    setAttribute() {}, appendChild(c) { this.children.push(c); },
    addEventListener(t, fn) { (this._h[t] = this._h[t] || []).push(fn); },
    querySelector() { return { textContent: '' }; },
  };
}
function __mkEl(init = {}) {
  const el = __mkNode();
  Object.assign(el, { checked: false, disabled: false, hidden: true }, init);
  return el;
}
const __els = {
  '#host': __mkEl(),
  '#port-preset': __mkEl({ value: '8000' }),
  '#port-custom': __mkEl(),
  '#use-https': __mkEl(),
  '#scheme-hint': __mkEl(),
  '#custom-port-wrap': __mkEl(),
  '#notify-enabled': __mkEl({ checked: true }),
  '#mobile-token': __mkEl(),
  '#status': __mkEl(),
  '#status-text': __mkEl(),
  '#recent-hosts': __mkEl(),
  '#recent-hosts-list': __mkEl(),
  '#setup-form': __mkEl(),
  '#btn-test': __mkEl(),
  '#btn-update': __mkEl(),
  '#btn-connect': __mkEl(),
};
const document = {
  querySelector: (sel) => (__els[sel] !== undefined ? __els[sel] : null),
  createElement: () => __mkNode(),
  addEventListener() {},
  removeEventListener() {},
};
globalThis.window = { location: { href: '' }, cuttleMobile: undefined };
function __setForm({ host, preset, custom, https }) {
  __els['#host'].value = host;
  __els['#port-preset'].value = preset;
  __els['#port-custom'].value = custom;
  __els['#use-https'].checked = https;
}
async function __fire(sel, type, ev = {}) {
  const el = __els[sel];
  for (const fn of (el._h[type] || [])) await fn(ev);
}
function __fakeSubmitEvent() { return { preventDefault() {} }; }
function assert(cond, msg) {
  if (!cond) throw new Error('ASSERT: ' + msg);
  console.log('ok - ' + msg);
}
"""


def test_js_port_vectors_reject_malformed_and_keep_supported():
    out = _node_eval(
        FAKES
        + """
const cases = [
  ['8000', 8000, true], ['8888', 8888, true], ['8443', 8443, true],
  ['80', 80, true], ['443', 443, true], ['8001', 8001, true],
  ['8890', 8890, true], ['1', 1, true], ['65535', 65535, true],
  [' 8443 ', 8443, true],
  ['0', 0, false], ['65536', 65536, false], ['99999', 99999, false],
  ['abc', NaN, false], ['', NaN, false], ['80.5', NaN, false],
  ['-1', NaN, false], ['0x50', NaN, false],
];
for (const [raw, want, valid] of cases) {
  const got = parsePortNumber(raw);
  if (Number.isNaN(want)) assert(Number.isNaN(got), `parse(${JSON.stringify(raw)}) is NaN`);
  else assert(got === want, `parse(${JSON.stringify(raw)}) === ${want}`);
  assert(isValidPort(got) === valid, `valid(${JSON.stringify(raw)}) === ${valid}`);
}
assert(isValidPort(parsePortNumber(8080)) === true, 'numeric 8080 valid');
assert(isValidPort(parsePortNumber(80.5)) === false, 'numeric 80.5 rejected, not clamped');
console.log('VECTORS DONE');
"""
    )
    assert "VECTORS DONE" in out


def test_js_custom_port_roundtrip_through_fake_prefs_and_form():
    out = _node_eval(
        FAKES
        + """
// Custom port round-trip: form -> baseUrl -> fake prefs -> load -> form.
__setForm({ host: '192.168.1.20', preset: 'custom', custom: '8890', https: false });
let form = readForm();
assert(form.host === '192.168.1.20', 'host cleaned');
assert(form.port === 8890 && isValidPort(form.port), 'custom 8890 accepted, not clamped');
assert(form.useHttps === false, 'explicit http choice preserved on custom port');
const base = buildBaseUrl(form.host, form.port, form.useHttps);
assert(base === 'http://192.168.1.20:8890', 'base URL keeps custom port, got ' + base);
await saveConfig(form.host, form.port, form.useHttps, base, true, 'tok');
__setForm({ host: '', preset: '8000', custom: '', https: false });
const loaded = await loadConfig();
assert(loaded.port === 8890, 'stored custom port survives load');
populateForm(loaded);
assert(__els['#port-preset'].value === 'custom', 'form repopulates custom preset');
assert(__els['#port-custom'].value === '8890', 'form repopulates custom value');
const again = readForm();
assert(again.port === 8890, 're-read keeps 8890');
assert(cuttleEntryUrl(base) === 'http://192.168.1.20:8890/app_shell.html', 'entry URL keeps port');
// Stored-base contract: the exact base string the native layer reads.
// The native Java endpoint calls (CuttleApi ping/poll/reply) concatenate
// paths onto this stored string; that concatenation is source-inspected in
// CuttleApi.java, not executed here — no JVM/device run is claimed.
const storedBase = __store['cuttle_base_url'].replace(/\\/+$/, '');
assert(storedBase + '/api/lan-ping' === 'http://192.168.1.20:8890/api/lan-ping', 'native ping keeps port');
assert(storedBase + '/api/mobile/poll' === 'http://192.168.1.20:8890/api/mobile/poll', 'native poll keeps port');
// Defaults preserved.
__setForm({ host: 'pc', preset: '8000', custom: '', https: false });
let d = readForm();
assert(d.port === 8000 && d.useHttps === false, '8000 default http');
__setForm({ host: 'pc', preset: '8888', custom: '', https: false });
d = readForm();
assert(d.port === 8888 && d.useHttps === true, '8888 forces https');
// Malformed custom entries are invalid (callers block, never connect).
for (const bad of ['abc', '0', '65536', '99999', '']) {
  __setForm({ host: 'pc', preset: 'custom', custom: bad, https: false });
  const f = readForm();
  assert(!isValidPort(f.port), `custom ${JSON.stringify(bad)} invalid, never defaulted`);
}
// Corrupt stored config is surfaced, not swapped: out-of-range stays visible,
// non-numeric blanks the slot, and the saved base URL is returned untouched.
__store['cuttle_port'] = '99999';
__store['cuttle_base_url'] = 'http://192.168.1.20:99999';
let corrupt = await loadConfig();
assert(corrupt.port === 99999, 'stored 99999 preserved, not replaced');
assert(corrupt.baseUrl === 'http://192.168.1.20:99999', 'stored base URL untouched');
populateForm(corrupt);
assert(__els['#port-preset'].value === 'custom', 'corrupt port shows custom slot');
assert(__els['#port-custom'].value === '99999', 'out-of-range value stays visible for correction');
assert(!isValidPort(readForm().port), 'corrupt stored port still blocks actions');
__store['cuttle_port'] = 'not-a-port';
corrupt = await loadConfig();
assert(Number.isNaN(corrupt.port), 'non-numeric stored port preserved as NaN, not 8000');
populateForm(corrupt);
assert(__els['#port-custom'].value === '', 'non-numeric stored port blanks the slot');
assert(!isValidPort(readForm().port), 'blanked slot still blocks actions');
console.log('ROUNDTRIP DONE');
"""
    )
    assert "ROUNDTRIP DONE" in out


def test_js_handlers_block_invalid_ports_with_zero_side_effects():
    out = _node_eval(
        FAKES
        + """
bindForm();
// Invalid custom port: Test button performs no fetch and writes nothing.
__setForm({ host: '192.168.1.20', preset: 'custom', custom: '99999', https: false });
await __fire('#btn-test', 'click');
assert(__fetchCalls.length === 0, 'invalid port: Test makes zero fetch calls');
assert(!('cuttle_base_url' in __store), 'invalid port: Test writes no prefs');
// Invalid custom port: submit saves nothing and navigates nowhere.
await __fire('#setup-form', 'submit', __fakeSubmitEvent());
assert(!('cuttle_base_url' in __store), 'invalid port: submit saves nothing');
assert(window.location.href === '', 'invalid port: submit navigates nowhere');
// Invalid custom port: update check writes nothing.
await __fire('#btn-update', 'click');
assert(!('cuttle_base_url' in __store), 'invalid port: update writes nothing');
assert(__fetchCalls.length === 0, 'invalid port: update makes zero fetch calls');
// Empty custom: treated as missing input, not as 8000 — submit is blocked.
__setForm({ host: '192.168.1.20', preset: 'custom', custom: '', https: false });
await __fire('#setup-form', 'submit', __fakeSubmitEvent());
assert(!('cuttle_base_url' in __store), 'empty custom: submit saves nothing');
// Valid custom port: submit saves the exact config and opens it natively.
__setForm({ host: '192.168.1.20', preset: 'custom', custom: '8890', https: false });
await __fire('#setup-form', 'submit', __fakeSubmitEvent());
assert(__store['cuttle_base_url'] === 'http://192.168.1.20:8890', 'valid submit stores exact base URL');
assert(__store['cuttle_port'] === '8890', 'valid submit stores exact port, not a substitute');
assert(window.location.href === 'http://192.168.1.20:8890/app_shell.html', 'valid submit opens exact entry URL');
assert(__fetchCalls.length === 1 && __fetchCalls[0] === 'http://192.168.1.20:8890/api/lan-ping',
  'valid submit probed the entered custom port');
console.log('HANDLERS DONE');
"""
    )
    assert "HANDLERS DONE" in out


def test_companion_full_url_validation_contract():
    """Source-contract assertions on the real validator's home.

    The validator lives in pure-JVM ``core/BaseUrl.kt`` (no Android imports)
    so it compiles without the SDK; ``MainActivity`` calls it before any
    save/start. The bounds must be explicit in source because java.net.URI
    does not bound explicit numeric ports itself.
    """
    src = COMPANION_BASE_URL.read_text(encoding="utf-8")
    assert "fun validateBaseUrl" in src, "companion validator present"
    assert "java.net.URI" in src, "strict parse, no hand-rolled port guess"
    assert "android" not in src, "validator has no Android dependency"
    flat = re.sub(r"\s+", "", src)
    assert "uri.port" in flat, "validator reads the parsed port"
    assert "port!=-1" in flat, "absent port (-1) passes through to the platform default"
    assert "port<1" in flat and "port>65535" in flat, "explicit 1..65535 bounds in source"
    assert "65535" in src, "upper bound literal present"
    main = COMPANION_MAIN.read_text(encoding="utf-8")
    assert "validateBaseUrl" in main, "MainActivity uses the shared validator"
    assert "return@setOnClickListener" in main, "invalid URL blocks save/start"
    # Stored verbatim: trimmed only, no scheme/port rewrite.
    assert "Prefs.setBaseUrl(this, rawBase.trim())" in main
    # No TLS weakening alongside validation.
    for banned in ("usesCleartextTraffic", "HostnameVerifier", "ALLOW_ALL", "proceed()"):
        assert banned not in main, f"no TLS weakening ({banned})"
    api = COMPANION_API.read_text(encoding="utf-8")
    assert "trim().trimEnd('/')" in api, "native HTTP still uses stored base verbatim"


def _jvm_toolchain():
    import os

    kotlinc = shutil.which("kotlinc")
    java = shutil.which("java")
    home_kotlinc = Path.home() / ".local" / "kotlinc" / "kotlinc" / "bin" / "kotlinc"
    home_java = Path.home() / ".local" / "jdk" / "bin" / "java"
    if kotlinc is None and home_kotlinc.exists():
        kotlinc = str(home_kotlinc)
    if java is None and home_java.exists():
        java = str(home_java)
    env_java_home = os.environ.get("JAVA_HOME")
    return kotlinc, java, env_java_home


def test_companion_validator_jvm_execution(tmp_path):
    """Compile the REAL BaseUrl.kt and run accept/reject vectors on the JVM.

    Skips when no Kotlin/JDK toolchain is available (it is user-local, not
    vendored). This is execution of the shipped validator, not a mirror.
    """
    import pytest

    kotlinc, java, java_home = _jvm_toolchain()
    if kotlinc is None or java is None:
        pytest.skip("no kotlinc/JDK toolchain available")
    harness = tmp_path / "Vectors.kt"
    harness.write_text(
        """import com.cuttle.companion.core.validateBaseUrl
fun main() {
  val accept = listOf(
    "https://cuttle.example.net", "https://cuttle.local:8080",
    "https://192.168.1.10:8443", "https://192.168.1.10:8890",
    "https://192.168.1.10:443", "https://192.168.1.10:1",
    "https://192.168.1.10:65535", "http://192.168.1.10:8000",
    "http://192.168.1.10:8001", "http://192.168.1.10:80")
  val reject = listOf(
    "", "   ", "192.168.1.10:8000", "ftp://host:21", "https://",
    "http://host:0", "http://host:99999", "http://host:65536",
    "http://host:abc", "http:///path-only")
  var failures = 0
  for (url in accept) {
    val r = validateBaseUrl(url)
    println("ACCEPT [${if (r == null) "ok" else "FAIL"}] $url")
    if (r != null) failures++
  }
  for (url in reject) {
    val r = validateBaseUrl(url)
    println("REJECT [${if (r != null) "ok" else "FAIL"}] '$url'")
    if (r == null) failures++
  }
  if (failures > 0) throw RuntimeException("$failures vector(s) failed")
  println("JVM VECTORS DONE")
}
""",
        encoding="utf-8",
    )
    jar = tmp_path / "v.jar"
    env = None
    if java_home:
        import os

        env = {**os.environ, "JAVA_HOME": java_home}
    compile_proc = subprocess.run(
        [kotlinc, str(COMPANION_BASE_URL), str(harness), "-include-runtime", "-d", str(jar)],
        capture_output=True,
        text=True,
        timeout=240,
        env=env,
    )
    assert compile_proc.returncode == 0, f"kotlinc failed:\n{compile_proc.stderr}"
    run_proc = subprocess.run(
        [java, "-jar", str(jar)], capture_output=True, text=True, timeout=120, env=env
    )
    assert run_proc.returncode == 0, f"JVM vectors failed:\n{run_proc.stdout}\n{run_proc.stderr}"
    assert "JVM VECTORS DONE" in run_proc.stdout
